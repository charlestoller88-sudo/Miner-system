"""
从 data/logs 下 txt 提取「正文 + 文末人工诊断」，导出训练/微调数据集。

你的方案：在日志最底部追加故障分析与解决方案 —— 可行。训练时必须只用「去掉文末分析后的正文」
作为模型输入，文末内容作为监督信号，避免标签泄漏。

解析优先级（见 utils/log_footer_split.py）：
1) 显式标记块 MINER_DIAGNOSIS_BEGIN/END（推荐今后统一用，解析最稳）
2) 启发式：大 JSON 结束后以「矿机（」开头的长文（与你现有 10.102.0.190 样例一致）

用法（在项目根目录）:
  python scripts/extract_training_from_logs.py --output data/ml/train_from_footer.csv
  python scripts/extract_training_from_logs.py --format jsonl --output data/ml/train_from_footer.jsonl
  python scripts/extract_training_from_logs.py --only-with-annotation --with-rules-columns
  python scripts/extract_training_from_logs.py --since 2026-05-01 --limit 500
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))

    from utils.log_footer_split import (
        extract_structured_fields,
        split_log_body_and_annotation,
    )

    parser = argparse.ArgumentParser(description="从日志文末人工分析导出训练数据")
    parser.add_argument("--root", type=str, default="data/logs", help="日志根目录（递归 *.txt）")
    parser.add_argument(
        "--output",
        type=str,
        default="data/ml/train_from_footer.csv",
        help="输出路径（.csv 或 .jsonl）",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl"),
        default="csv",
        help="输出格式；若 --output 以 .jsonl 结尾则自动为 jsonl",
    )
    parser.add_argument(
        "--only-with-annotation",
        action="store_true",
        help="仅导出成功切分出文末标注的文件",
    )
    parser.add_argument(
        "--min-annotation-chars",
        type=int,
        default=40,
        help="文末标注最短字符数，低于则视为无标注",
    )
    parser.add_argument(
        "--max-body-chars",
        type=int,
        default=0,
        help="正文最大长度截断，0 表示不截断（大模型微调时可再截）",
    )
    parser.add_argument(
        "--with-rules-columns",
        action="store_true",
        help="额外写入 rule_primary 等（调用 rule_diagnose，较慢）",
    )
    parser.add_argument("--since", type=str, default="", help="路径中日期 >= YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0, help="最多处理文件数，0 不限制")
    args = parser.parse_args()

    log_root = (root / args.root).resolve()
    if not log_root.is_dir():
        print(f"错误: 目录不存在: {log_root}", file=sys.stderr)
        return 1

    out_path = (root / args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = args.format
    if out_path.suffix.lower() == ".jsonl":
        fmt = "jsonl"

    since = args.since.strip() or None
    diag = None
    if args.with_rules_columns:
        from services.local_ai_miner_diagnoser import rule_diagnose, extract_total_hashrate_ths

        diag = {"rule_diagnose": rule_diagnose, "extract_total_hashrate_ths": extract_total_hashrate_ths}

    def _log_date(p: Path) -> str:
        import re as _re

        try:
            rel = p.resolve().relative_to(log_root.resolve())
        except ValueError:
            return ""
        for part in rel.parts:
            if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
                return part
        return ""

    paths = sorted(log_root.rglob("*.txt"))
    rows: list[dict] = []

    for path in paths:
        if not path.is_file():
            continue
        ld = _log_date(path)
        if since and (not ld or ld < since):
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        sp = split_log_body_and_annotation(text)
        ann = sp.annotation.strip()
        if args.only_with_annotation and (
            sp.method == "none" or len(ann) < args.min_annotation_chars
        ):
            continue

        body = sp.body
        if args.max_body_chars and len(body) > args.max_body_chars:
            body = body[: args.max_body_chars]

        primary, solutions, ann_full = extract_structured_fields(ann if ann else sp.annotation)

        rec: dict = {
            "file_path": path.relative_to(root).as_posix(),
            "log_date": ld,
            "split_method": sp.method,
            "body": body,
            "annotation_full": ann_full,
            "label_primary": primary,
            "label_solutions": solutions,
        }

        if diag is not None:
            ths = diag["extract_total_hashrate_ths"](text)
            rb = diag["rule_diagnose"](text, parsed_hashrate_ths=ths)
            rec["rule_primary"] = rb.get("primary_cause", "")
            rec["rule_confidence"] = rb.get("confidence", "")
            rec["ths_15m"] = f"{ths:.6f}" if ths is not None else ""

        rows.append(rec)

        if args.limit and len(rows) >= args.limit:
            break

    if not rows:
        print("没有可导出的行（检查 --only-with-annotation / 路径 / --since）")
        return 1

    fieldnames = list(rows[0].keys())

    if fmt == "jsonl":
        with out_path.open("w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    print(f"已导出 {len(rows)} 条 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
