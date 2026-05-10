"""
从矿机导出 txt 中分离「原始日志正文」与「文末人工诊断」。

支持两种方式（按优先级）：
1) 显式标记块（推荐新写入的日志使用，便于 100% 可靠解析）：

===== MINER_DIAGNOSIS_BEGIN v1 =====
主因: （一句话，可选）
方案:
- 步骤一
- 步骤二
（自由正文也可）
===== MINER_DIAGNOSIS_END v1 =====

2) 启发式：在部分 Braiins 导出中，大段 JSON 结束后空行，接着以「矿机（」或「IP地址为」开头的长文人工分析
   （分别类似 logs_10_102_0_190、logs_10_102_0_137 样例）。若误切分，请改用标记块。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

MARK_BEGIN = "===== MINER_DIAGNOSIS_BEGIN v1 ====="
MARK_END = "===== MINER_DIAGNOSIS_END v1 ====="
AUTO_BATCH_TAG = "【AUTO_DIAGNOSIS_BATCH v1】"


@dataclass
class SplitResult:
    body: str
    annotation: str
    method: str  # "markers" | "heuristic_json_then_footer" | "none"


def split_log_body_and_annotation(text: str) -> SplitResult:
    text = text if isinstance(text, str) else ""
    if not text.strip():
        return SplitResult(body=text, annotation="", method="none")

    if MARK_BEGIN in text and MARK_END in text:
        pre, _, rest = text.partition(MARK_BEGIN)
        mid, _, post = rest.partition(MARK_END)
        ann = (mid or "").strip()
        body = (pre + post).strip()
        return SplitResult(body=body, annotation=ann, method="markers")

    # Braiins 导出常见：最后一段 JSON 以 "id": 1 与单独一行 } 结束，后接空行再为人工分析（含「我对这台…」「IP地址为…」等）
    pat_tail_json = re.compile(
        r'("\s*id"\s*:\s*1\s*\r?\n\}\s*)(\r?\n\s*\r?\n)(.+)$',
        re.I | re.S,
    )
    tail_matches = list(pat_tail_json.finditer(text))
    if tail_matches:
        m = tail_matches[-1]
        ann = (m.group(3) or "").strip()
        if len(ann) >= 40:
            body = text[: m.start(3)].rstrip()
            return SplitResult(body=body, annotation=ann, method="heuristic_after_id1_json")

    # 启发式：单独一行的 } 后接空行，再以「矿机（」「IP地址为」或批量 AUTO 标记开头
    patterns = [
        re.compile(r"(?ms)^\}\s*\n\s*\n(矿机（.+)$"),
        re.compile(r"(?ms)^\}\s*\n\s*\n(IP地址为.+)$"),
        re.compile(r"(?ms)^\}\s*\n\s*\n(" + re.escape(AUTO_BATCH_TAG) + r".+)$"),
    ]
    last_m = None
    for pat in patterns:
        it = list(pat.finditer(text))
        if it:
            m = it[-1]
            if last_m is None or m.start(1) > last_m.start(1):
                last_m = m
    if last_m:
        body = text[: last_m.start(1)].rstrip()
        ann = last_m.group(1).strip()
        if len(ann) >= 40:
            return SplitResult(body=body, annotation=ann, method="heuristic_json_then_footer")

    return SplitResult(body=text.strip(), annotation="", method="none")


def extract_structured_fields(annotation: str) -> Tuple[str, str, str]:
    """
    从标记块或正文中尽量抽出：主因一行、方案段落、全文。
    若使用 MARKER 块且含「主因:」「方案:」前缀则解析；否则主因取首行、方案取「行动建议：」之后。
    """
    ann = (annotation or "").strip()
    if not ann:
        return "", "", ""

    primary = ""
    solutions = ""

    if "主因:" in ann or "主因：" in ann:
        for line in ann.splitlines():
            s = line.strip()
            if s.startswith("主因:") or s.startswith("主因："):
                primary = s.split(":", 1)[-1].split("：", 1)[-1].strip()
                break

    if not primary:
        first = ann.splitlines()[0].strip() if ann.splitlines() else ""
        if first.startswith(("矿机（", "IP地址为", "我对这台")):
            primary = first[:500]

    # 按优先级匹配「方案/建议」段落（越具体的标题靠前，避免误匹配正文里的小标题）
    for sep in (
        "问题原因排查与解决方案",
        "故障原因推测与解决方案",
        "解决方案建议",
        "行动建议：",
        "行动建议:",
        "总结与建议",
        "【总结与建议】",
    ):
        if sep in ann:
            solutions = ann.split(sep, 1)[-1].strip()
            break

    if not solutions and ("方案:" in ann or "方案：" in ann):
        _, _, tail = re.split(r"方案\s*[:：]", ann, maxsplit=1)
        solutions = tail.strip()

    return primary, solutions, ann


def extract_action_solution_paragraph(annotation: str) -> str:
    """
    在 solutions 段或全文内尽量截取「解决步骤 / 操作」正文，供脚本合并 SOP 时解析。
    若不存在明确小标题，则退回整段 solutions（或全文）。
    """
    _, solutions, full = extract_structured_fields(annotation)
    base = (solutions or "").strip()
    if len(base) < 25:
        base = (full or "").strip()
    if not base:
        return ""
    markers = (
        "解决步骤：",
        "解决步骤:",
        "处理步骤：",
        "处理步骤:",
        "操作建议：",
        "操作建议:",
        "维修步骤：",
        "维修步骤:",
        # 优先于泛化的「按以下步骤」，避免只切到「成功率很高」等过渡句
        "首选方案：",
        "首选方案:",
        "按以下步骤操作，",
        "按以下步骤操作",
        "按以下步骤",
    )
    for mk in markers:
        if mk in base:
            tail = base.split(mk, 1)[-1].strip()
            if len(tail) >= 12:
                return tail
    return base
