"""
根据「文末人工故障分析 + 解决方案」丰富 fault_patterns_learned.json：

- 按每份日志正文走 rule_diagnose 得到主因（与线上一致的主因名称）；
- 从文末分析中抽取编号步骤、以「-」开头的要点等，合并进 sop_overrides[主因]；
- 少量高质量样本写入 diagnostic_few_shot，供 Ollama 叙事参考（MINER_AI_NARRATIVE=1 时）。

不会删除或改写你已有的 extra_rules 示例，只合并 sop_overrides 与 few_shot。

用法（项目根目录）:
  python scripts/enrich_learned_rules_from_annotations.py --log-dir "data/logs/2026年3月/2026-03-18" --dry-run
  python scripts/enrich_learned_rules_from_annotations.py --log-dir "data/logs/2026年3月/2026-03-18" --backup
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import DefaultDict, Dict, List, Set


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


SKIP_LINE_SUBSTR = (
    "矿机管理平台",
    "自动生成",
    "AUTO_DIAGNOSIS",
    "【AUTO_DIAGNOSIS",
    "本段由矿机管理平台",
)


def _strip_auto_noise(annotation: str) -> str:
    lines = []
    for ln in annotation.splitlines():
        if any(s in ln for s in SKIP_LINE_SUBSTR):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


# 从「现象/分析」小节误抽成 SOP 的常见行前缀（在回退到全文解析时使用）
_SKIP_SYMPTOM_LINE_PREFIXES = (
    "当前状态",
    "发现的故障",
    "实时算力",
    "平均算力",
    "拒绝率",
    "矿池连接",
    "硬件错误",
    "故障现象",
    "故障结果",
    "关键错误日志",
    "算力板0",
    "算力板1",
    "算力板2",
    "chain[",
    "从最终的运行数据",
    "核心故障",
    "其他观察",
    "存在的故障",
    "哈希板芯片检测失败",
    "电源通信校验失败",
    "网络连接问题",
    "严重过热",
    "日志中缺失",
    "矿机处于无效",
    "系统陷入",
    "系统频繁",
    "温度与风扇",
    "其他哈希板",
    "仅剩一块",
    "日志明确指出",
    "同样，哈希板",
    "这表明问题",
    "该型号矿机",
    "由于一块哈希板",
    "算力不足",
    "网络连接正常",
    "网络连接不稳定",
    "严重的网络",
    "严重的间歇性",
    "算力与性能数据",
)


def _line_looks_symptom_summary(s: str) -> bool:
    t = s.strip()
    return any(t.startswith(p) for p in _SKIP_SYMPTOM_LINE_PREFIXES)


# 编号步骤里仍可能是「分析/结论」小标题，非可执行 SOP
_SKIP_NUMBERED_ANALYSIS_PREFIXES = (
    "这种",
    "日志中",
    "该错误",
    "这表明",
    "可以看出",
    "由于",
    "从最终",
    "从日志",
    "性能统计数据",
    "哈希板初始化成功",
    "算力板",
    "关键错误",
)


def extract_human_solution_steps(annotation: str) -> List[str]:
    """从人工分析中抽取短句步骤（优先「方案/解决步骤」段内的编号行、要点）。"""
    from utils.log_footer_split import extract_action_solution_paragraph

    ann = _strip_auto_noise(annotation)
    if len(ann) < 40:
        return []

    action_text = extract_action_solution_paragraph(ann)
    # 能切出独立「操作」段则只解析该段；否则回退全文并对典型「现象小结」行过滤
    text = action_text if len(action_text) >= 20 else ann
    skip_symptoms = len(action_text) < 20

    out: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or len(s) > 280:
            continue
        if s.startswith("以下是") or s.startswith("详细故障") or s.startswith("日志证据"):
            continue
        if re.match(r"^问题分析[:：]", s):
            continue
        if skip_symptoms and _line_looks_symptom_summary(s):
            continue
        m = re.match(r"^\d+[\.\)、]\s*(.+)", s)
        if m:
            piece = m.group(1).strip()
            if 6 <= len(piece) <= 240 and not piece.startswith("http"):
                if skip_symptoms and _line_looks_symptom_summary(piece):
                    continue
                if any(piece.startswith(p) for p in _SKIP_NUMBERED_ANALYSIS_PREFIXES):
                    continue
                out.append(piece)
            continue
        if s.startswith(("-", "•", "·")):
            piece = s.lstrip("-•· \t")
            if 6 <= len(piece) <= 240:
                if skip_symptoms and _line_looks_symptom_summary(piece):
                    continue
                out.append(piece)
            continue
        # 已切到「操作」段时：常见「单独一行标题 + 冒号」步骤标题（避免把全文当要点）
        if not skip_symptoms:
            if re.match(r"^(首选方案|备用方案|次选方案)\s*[:：]", s):
                piece = re.sub(r"^(首选方案|备用方案|次选方案)\s*[:：]\s*", "", s).strip()
                if 4 <= len(piece) <= 200:
                    out.append(piece)
                continue
            if re.match(r"^[^：\n]{2,52}[:：]\s*$", s):
                head = re.split(r"[:：]", s, maxsplit=1)[0].strip()
                if not head or "，" in head or len(s) > 36:
                    continue
                if any(
                    head.startswith(p)
                    for p in (
                        "注意",
                        "总结",
                        "风险提示",
                        "进一步",
                        "修复后",
                        "可能原因",
                        "总体",
                        "详细",
                        "这是",
                        "针对",
                        "日志",
                        "该故障",
                        "此类",
                        "如果",
                        "建议",
                        "这种",
                        "该错误",
                        "通常",
                        "性能",
                        "存在",
                    )
                ):
                    continue
                if head.startswith("哈希板") and any(
                    k in head for k in ("成功", "良好", "失败", "正常", "无响应")
                ):
                    continue
                out.append(s.strip())
                continue
            # 短祈使句（单独成行、句号结尾），常见于「断电矿机。」这类一步一句
            if (
                10 <= len(s) <= 200
                and s.endswith(("。", "！", "!"))
                and re.match(r"^(断电|重启|检查|找到|小心|重新|观察|评估|送修|打开|务必|完成|等待|从另)", s)
            ):
                out.append(s)

    # 去重保序
    seen: Set[str] = set()
    dedup: List[str] = []
    for x in out:
        key = x.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(x)
    return dedup[:18]


def _conclusion_head(annotation: str, max_len: int = 420) -> str:
    ann = _strip_auto_noise(annotation).replace("\r\n", "\n")
    for para in re.split(r"\n\s*\n", ann):
        p = para.strip()
        if len(p) < 20:
            continue
        if p.startswith("以下是"):
            continue
        return p[:max_len]
    return ann[:max_len]


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))

    from services.local_ai_miner_diagnoser import collect_evidence, rule_diagnose, extract_total_hashrate_ths
    from utils.log_footer_split import split_log_body_and_annotation

    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=str, required=True)
    ap.add_argument(
        "--learned-path",
        type=str,
        default="fault_patterns_learned.json",
        help="相对项目根的 learned JSON 路径",
    )
    ap.add_argument("--max-few-shot", type=int, default=14, help="最多追加几条 diagnostic_few_shot")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", action="store_true", help="写入前复制 learned 为 .bak 时间戳")
    args = ap.parse_args()

    log_dir = (root / args.log_dir).resolve()
    learned_path = (root / args.learned_path).resolve()
    if not log_dir.is_dir():
        print(f"错误: 目录不存在 {log_dir}", file=sys.stderr)
        return 1
    if not learned_path.is_file():
        print(f"错误: 未找到 {learned_path}", file=sys.stderr)
        return 1

    raw_doc = json.loads(learned_path.read_text(encoding="utf-8"))
    sop_overrides: Dict[str, List[str]] = {
        k: list(v) for k, v in (raw_doc.get("sop_overrides") or {}).items() if isinstance(v, list)
    }
    few_shot: List[dict] = [x for x in (raw_doc.get("diagnostic_few_shot") or []) if isinstance(x, dict)]
    existing_titles = {str(x.get("title", "")) for x in few_shot}

    by_primary: DefaultDict[str, List[str]] = defaultdict(list)
    few_candidates: List[dict] = []

    n_files = 0
    n_used = 0
    for path in sorted(log_dir.glob("*.txt")):
        if not path.is_file():
            continue
        n_files += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        sp = split_log_body_and_annotation(text)
        if sp.method == "none" or len(sp.annotation.strip()) < 60:
            continue
        body = sp.body.strip()
        ann = sp.annotation.strip()
        rb = rule_diagnose(body, parsed_hashrate_ths=extract_total_hashrate_ths(body))
        primary = (rb.get("primary_cause") or "").strip()
        if not primary:
            continue
        steps = extract_human_solution_steps(ann)
        if steps:
            by_primary[primary].extend(steps)
            n_used += 1

        title = path.stem
        if title not in existing_titles and len(few_candidates) < args.max_few_shot * 2:
            ev = collect_evidence(body, limit=10)
            excerpt = ev if isinstance(ev, str) else "\n".join(str(x) for x in (ev or []))
            excerpt = (excerpt or "")[:1400]
            few_candidates.append(
                {
                    "title": title,
                    "your_conclusion": _conclusion_head(ann),
                    "log_excerpt": excerpt,
                    "rule_primary": primary,
                }
            )

    merged_count = 0
    for primary, items in by_primary.items():
        if not items:
            continue
        cur = list(sop_overrides.get(primary, []))
        seen = {x.strip() for x in cur if isinstance(x, str)}
        for it in items:
            s = it.strip()
            if not s or s in seen or len(s) < 8:
                continue
            seen.add(s)
            cur.append(s)
            merged_count += 1
        sop_overrides[primary] = cur[:28]

    added_fs = 0
    for c in few_candidates:
        if len(few_shot) >= args.max_few_shot:
            break
        if c["title"] in existing_titles:
            continue
        few_shot.append(
            {
                "title": c["title"],
                "your_conclusion": c["your_conclusion"],
                "log_excerpt": c["log_excerpt"],
                "note": f"规则主因快照: {c['rule_primary']}",
            }
        )
        existing_titles.add(c["title"])
        added_fs += 1

    raw_doc["sop_overrides"] = sop_overrides
    raw_doc["diagnostic_few_shot"] = few_shot

    print(
        f"扫描 {n_files} 个 txt，有效带标注 {n_used} 份；"
        f"合并新增 SOP 条目约 {merged_count} 条（去重后按主因写入）；"
        f"追加 few_shot {added_fs} 条（上限 {args.max_few_shot}）。"
    )
    print(f"涉及主因数: {len(by_primary)}")

    if args.dry_run:
        for p, xs in sorted(by_primary.items(), key=lambda x: -len(x[1]))[:12]:
            print(f"  [{p}] +{len(xs)} 条原始抽取")
        return 0

    if args.backup:
        bak = learned_path.with_suffix(
            learned_path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(learned_path, bak)
        print(f"已备份: {bak}")

    learned_path.write_text(
        json.dumps(raw_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写入: {learned_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
