"""
在矿机导出 txt 文末追加「分析报告」体例的诊断与方案（与 logs_10_102_0_137 结构对齐）。

内容来源：内置 rule_diagnose + 日志证据摘录 + 主因 SOP 方案拆分，属自动草案；
已含人工报告（文末含「以下是详细的分析报告：」）或已打过本脚本标记的会跳过。

用法（项目根目录）:
  python scripts/append_diagnosis_footer_batch.py --dir "data/logs/2026年3月/2026-03-18" --dry-run
  python scripts/append_diagnosis_footer_batch.py --dir "data/logs/2026年3月/2026-03-18"
  python scripts/append_diagnosis_footer_batch.py --dir "..." --force   # 去掉文末 AUTO 块后重写
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


AUTO_FOOTER_TAG = "【AUTO_DIAGNOSIS_BATCH v1】"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ip_from_filename(name: str) -> str:
    m = re.match(r"logs_(\d+)_(\d+)_(\d+)_(\d+)_", name, re.I)
    if not m:
        return ""
    a, b, c, d = m.groups()
    return f"{a}.{b}.{c}.{d}"


def _extract_model(text: str) -> str:
    m = re.search(r"型号:\s*([^\r\n]+)", text)
    if m:
        return m.group(1).strip()
    return "未知型号"


def _split_solutions(s: str) -> list[str]:
    if not s:
        return []
    tmp = str(s).replace("；", ";").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in tmp.split("\n"):
        for p in line.split(";"):
            t = p.strip(" -\t")
            if t:
                out.append(t)
    dedup: list[str] = []
    seen = set()
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup[:14]


def _opening_line(ip: str, model: str, ths: float | None, primary: str) -> str:
    if ths is None:
        ths_desc = "未能从日志中解析到稳定的总算力（TH/s），请重点查看是否缺少 SUMMARY/DEVS 等片段。"
    elif ths <= 0.05:
        ths_desc = "当前识别总算力接近 0 TH/s，矿机处于无算力或已停摆的高风险状态。"
    elif ths < 50.0:
        ths_desc = f"当前识别总算力约 {ths:.2f} TH/s，低于常用低算力阈值（50 TH/s），需重点排查。"
    else:
        ths_desc = f"当前识别总算力约 {ths:.2f} TH/s，整体仍有算力输出，但可能仍存在局部异常。"
    return (
        f"IP地址为 {ip} 的矿机（{model}）。{ths_desc}"
        f"规则引擎对当前日志的主因判定为：{primary}。"
        "以下为按统一模板整理的「分析报告」草案，请在执行维护前结合现场复核。"
    )


def build_footer_block(
    *,
    ip: str,
    model: str,
    ths: float | None,
    rb: dict,
    evidence_lines: list[str],
) -> str:
    primary = (rb.get("primary_cause") or "未识别明确故障关键词").strip()
    conf = (rb.get("confidence") or "中").strip()
    sec = (rb.get("secondary_causes") or "").strip() or "（无）"
    sols = _split_solutions(rb.get("solutions") or "")

    lines: list[str] = [
        AUTO_FOOTER_TAG,
        "",
        _opening_line(ip, model, ths, primary),
        "",
        "以下是详细的分析报告：",
        "",
        "1. 当前状态与算力概况",
    ]
    if ths is not None:
        lines.append(f"- 识别总算力（来自日志解析）：约 {ths:.3f} TH/s")
    else:
        lines.append("- 识别总算力：日志中未解析到可靠数值")
    lines.append(f"- 规则引擎置信度：{conf}")
    lines.append(f"- 主因：{primary}")
    lines.append("")

    lines.append("2. 发现的故障与日志依据")
    lines.append(f"主因归类：{primary}")
    if sec and sec != "（无）":
        lines.append(f"次要/伴生提示：{sec}")
    lines.append("")
    lines.append("关键日志摘录（节选，用于对照）：")
    if evidence_lines:
        for ev in evidence_lines[:10]:
            lines.append(f"- {ev}")
    else:
        lines.append("- （当前未抽取到高优先级摘录行，请直接检索原文中的 ERROR / Tuner / Stratum 等关键词。）")
    lines.append("")

    lines.append("3. 问题原因排查与解决方案")
    lines.append("建议按优先级逐项排查与验证：")
    lines.append("")
    if sols:
        for i, s in enumerate(sols, 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("1. 建议导出更长时间窗口的日志，或到矿机详情页刷新日志后重试自动诊断。")
        lines.append("2. 若仍无明确结论，请按供电、排线、算力板、矿池网络四项做人工复核。")
    lines.append("")
    lines.append(
        f"修复后验证：重新上电或恢复挖矿后，观察 SUMMARY/DEVS 算力与矿池 Accepted 是否恢复正常；"
        f"若同类报错仍高频出现，请升级/回退固件或联系板卡级维修。"
    )
    lines.append("")
    lines.append(
        f"—— 本段由矿机管理平台于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 根据内置规则自动追加；"
        "与人工撰写的「以下是详细的分析报告」体例一致，便于后续训练与检索。"
    )
    # 与人工范例一致：正文最后一行多为「}」，后接空行再进入分析段落
    return "\n\n" + "\n".join(lines)


def _has_manual_footer(text: str) -> bool:
    tail = text[-15000:] if len(text) > 15000 else text
    if "以下是详细的分析报告：" in tail and AUTO_FOOTER_TAG not in tail:
        return True
    return False


def _has_auto_footer(text: str) -> bool:
    return AUTO_FOOTER_TAG in text


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))

    from services.local_ai_miner_diagnoser import (
        collect_evidence,
        extract_ip,
        extract_total_hashrate_ths,
        rule_diagnose,
    )
    from utils.log_footer_split import split_log_body_and_annotation

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        type=str,
        required=True,
        help=r'日志目录，例如 data/logs/2026年3月/2026-03-18',
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印将处理的文件数，不写盘")
    ap.add_argument(
        "--force",
        action="store_true",
        help="若已存在本脚本追加的 AUTO 块，则先移除该块再重新追加",
    )
    args = ap.parse_args()

    log_dir = (root / args.dir).resolve()
    if not log_dir.is_dir():
        print(f"错误: 目录不存在: {log_dir}", file=sys.stderr)
        return 1

    files = sorted(log_dir.glob("*.txt"))
    if not files:
        print("未找到 .txt 文件")
        return 1

    n_skip_manual = 0
    n_skip_auto = 0
    n_write = 0
    to_write: list[tuple[Path, str]] = []

    for path in files:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        if _has_manual_footer(raw):
            n_skip_manual += 1
            continue
        if _has_auto_footer(raw) and not args.force:
            n_skip_auto += 1
            continue

        body = raw
        if _has_auto_footer(raw) and args.force:
            sp = split_log_body_and_annotation(raw)
            if sp.method != "none" and AUTO_FOOTER_TAG in (sp.annotation or ""):
                body = sp.body
            else:
                idx = raw.find(AUTO_FOOTER_TAG)
                if idx != -1:
                    body = raw[:idx].rstrip()

        ip = extract_ip(body) or _ip_from_filename(path.name)
        if not ip:
            ip = "未知IP"
        model = _extract_model(body)
        ths = extract_total_hashrate_ths(body)
        rb = rule_diagnose(body, parsed_hashrate_ths=ths)
        ev_raw = collect_evidence(body, limit=12)
        if isinstance(ev_raw, str):
            evidence_lines = [x.strip() for x in ev_raw.splitlines() if x.strip()]
        else:
            evidence_lines = [str(x).strip() for x in (ev_raw or []) if str(x).strip()]

        footer = build_footer_block(
            ip=ip,
            model=model,
            ths=ths,
            rb=rb,
            evidence_lines=evidence_lines,
        )
        new_text = body.rstrip() + footer
        to_write.append((path, new_text))

    print(
        f"目录: {log_dir}\n"
        f"总文件: {len(files)}\n"
        f"将追加 AUTO 报告: {len(to_write)}\n"
        f"跳过（已有人工「以下是详细的分析报告」）: {n_skip_manual}\n"
        f"跳过（已有 AUTO 块，可加 --force 重写）: {n_skip_auto}"
    )

    if args.dry_run:
        print("（dry-run 未写入）")
        return 0

    for path, new_text in to_write:
        path.write_text(new_text, encoding="utf-8", newline="\n")
        n_write += 1

    print(f"已写入 {n_write} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
