"""
从 data/logs 递归扫描 .txt 矿机日志，生成供人工标注的 CSV 模板。

用法（在项目根目录执行）:
  python scripts/build_label_template.py
  python scripts/build_label_template.py --root data/logs --output data/ml/labels_template.csv
  python scripts/build_label_template.py --with-rules --limit 500
  python scripts/build_label_template.py --with-rules --sample 300 --seed 42
  python scripts/build_label_template.py --since 2026-05-01

说明:
  - 默认不写规则列；加 --with-rules 会调用与线上一致的 rule_diagnose 预填主因/置信度等，便于你对照修改 label。
  - 请在 Excel/WPS 中编辑 label、agree_rule、notes 后用于训练或整理 fault_patterns_learned.json。

若你把人工结论写在每个 txt 文末，请用 scripts/extract_training_from_logs.py 自动切分正文与标注并导出训练集。
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _infer_log_date(path: Path, log_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(log_root.resolve())
    except ValueError:
        return ""
    for part in rel.parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            return part
    return ""


def _since_ok(log_date: str, since: str | None) -> bool:
    if not since or not log_date:
        return True
    return log_date >= since


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="生成矿机日志人工标注 CSV 模板")
    parser.add_argument(
        "--root",
        type=str,
        default="data/logs",
        help="日志根目录（相对项目根，递归扫描 *.txt）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/ml/labels_template.csv",
        help="输出 CSV 路径（相对项目根）",
    )
    parser.add_argument(
        "--with-rules",
        action="store_true",
        help="对每条日志调用 rule_diagnose，预填 rule_* 列（较慢）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理文件数，0 表示不限制",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="在筛选结果中随机抽样 N 条，0 表示不抽样",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="与 --sample 配合的随机种子",
    )
    parser.add_argument(
        "--since",
        type=str,
        default="",
        help="只包含路径中日期 >= 该值的文件，格式 YYYY-MM-DD",
    )
    args = parser.parse_args()

    log_root = (root / args.root).resolve()
    if not log_root.is_dir():
        print(f"错误: 目录不存在: {log_root}", file=sys.stderr)
        return 1

    out_path = (root / args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    paths = sorted(log_root.rglob("*.txt"))
    since = args.since.strip() or None

    filtered: list[Path] = []
    for p in paths:
        if not p.is_file():
            continue
        d = _infer_log_date(p, log_root)
        if not _since_ok(d, since):
            continue
        filtered.append(p)

    if args.sample and len(filtered) > args.sample:
        rng = random.Random(args.seed)
        filtered = rng.sample(filtered, args.sample)

    if args.limit and len(filtered) > args.limit:
        filtered = filtered[: args.limit]

    if not filtered:
        print("未找到任何 .txt 文件（请检查 --root / --since / 抽样条件）")
        return 1

    diag = None
    if args.with_rules:
        from services.local_ai_miner_diagnoser import (
            classify_hashrate_status,
            extract_ip,
            extract_nameplate_ths,
            extract_total_hashrate_ths,
            rule_diagnose,
        )

        diag = {
            "classify_hashrate_status": classify_hashrate_status,
            "extract_ip": extract_ip,
            "extract_nameplate_ths": extract_nameplate_ths,
            "extract_total_hashrate_ths": extract_total_hashrate_ths,
            "rule_diagnose": rule_diagnose,
        }

    fieldnames = [
        "file_path",
        "log_date",
        "filename",
        "file_size_bytes",
        "ip_from_log",
        "ths_15m",
        "nameplate_ths",
        "status",
        "rule_primary",
        "rule_confidence",
        "rule_secondary",
        "rule_alternate",
        "rule_solutions",
        "label",
        "agree_rule",
        "notes",
    ]

    n = len(filtered)
    print(f"将写入 {n} 行 -> {out_path}")

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for i, path in enumerate(filtered, 1):
            rel = path.relative_to(root).as_posix()
            log_date = _infer_log_date(path, log_root)
            size_b = path.stat().st_size

            row: dict[str, str] = {
                "file_path": rel,
                "log_date": log_date,
                "filename": path.name,
                "file_size_bytes": str(size_b),
                "ip_from_log": "",
                "ths_15m": "",
                "nameplate_ths": "",
                "status": "",
                "rule_primary": "",
                "rule_confidence": "",
                "rule_secondary": "",
                "rule_alternate": "",
                "rule_solutions": "",
                "label": "",
                "agree_rule": "",
                "notes": "",
            }

            if diag is not None:
                text = path.read_text(encoding="utf-8", errors="ignore")
                ip = diag["extract_ip"](text)
                ths = diag["extract_total_hashrate_ths"](text)
                npv = diag["extract_nameplate_ths"](text)
                row["ip_from_log"] = ip or ""
                row["ths_15m"] = f"{ths:.6f}" if ths is not None else ""
                row["nameplate_ths"] = f"{npv:.3f}" if npv is not None else ""
                if ths is not None:
                    row["status"] = diag["classify_hashrate_status"](ths, nameplate_ths=npv)
                rb = diag["rule_diagnose"](text, parsed_hashrate_ths=ths)
                row["rule_primary"] = rb.get("primary_cause", "")
                row["rule_confidence"] = rb.get("confidence", "")
                row["rule_secondary"] = rb.get("secondary_causes", "")
                row["rule_alternate"] = rb.get("alternate_causes", "")
                row["rule_solutions"] = rb.get("solutions", "")

            w.writerow(row)

            if i % 200 == 0 or i == n:
                print(f"  已处理 {i}/{n}")

    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
