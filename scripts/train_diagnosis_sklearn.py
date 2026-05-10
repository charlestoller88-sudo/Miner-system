"""
使用带「文末人工/半自动分析报告」的矿机 txt 训练轻量级诊断分类模型（scikit-learn）。

样本量较小（如 86 条）时，默认使用「粗粒度标签」——结合标注全文 + 正文前段的关键词分桶，
便于泛化；也可改用 headline 模式（首行摘要 LabelEncoder，稀有类合并）。

用法（项目根目录）:
  python scripts/train_diagnosis_sklearn.py --log-dir "data/logs/2026年3月/2026-03-18"
  python scripts/train_diagnosis_sklearn.py --log-dir "..." --label-strategy headline --min-per-class 2
  python scripts/predict_diagnosis_sklearn.py --model data/ml/models/diagnosis_sklearn.joblib --file path/to/log.txt

依赖: pip install -r requirements.txt（含 scikit-learn）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


AUTO_TAG = "【AUTO_DIAGNOSIS_BATCH v1】"


def _strip_auto_marker_lines(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        if AUTO_TAG in ln:
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _coarse_fault_label(annotation: str, body_head: str) -> str:
    """
    粗粒度标签：顺序匹配，命中即返回。覆盖板卡/矿池/电源/风扇/温度/休眠/未知。
    在 86 条量级下比「每篇首句唯一」更利于学习。
    """
    s = (_strip_auto_marker_lines(annotation) + "\n" + body_head)[:80000]
    low = s.lower()

    if re.search(
        r"curtailment|curtail|休眠|sleep|限电|ispowersupplyon.*false",
        low,
        re.I,
    ):
        return "policy_or_power_sleep"

    if re.search(
        r"err:i3|no\s*chips|nochipsdetected|disabled\s*hashboard|"
        r"asic\s+enumeration|initialization\s+of\s+hashboard|"
        r"err:e4|eeprom|hashchip|doesn\x27t\s+match\s+chip|"
        r"number\s+of\s+responses|discovered\s+\d+\s+chips|expected\s+110",
        low,
        re.I,
    ):
        return "board_eeprom_chip"

    if re.search(
        r"pll|get\s+pll|降频|frequency|mhz.*435|chain\[",
        low,
        re.I,
    ):
        return "board_pll_freq_derate"

    if re.search(
        r"fan\s+lost|error_fan|tachometer|less\s+than\s+required\s+number\s+of\s+fans",
        low,
        re.I,
    ):
        return "fan"

    if re.search(
        r"overtemp|temp\s+diff|error_temp|tsensor|pic\s+temp|temperature\s+sensor",
        low,
        re.I,
    ):
        return "temperature"

    if re.search(
        r"stratum|failed\s+to\s+resolve|dns|pool\s+inactivity|socket\s+error|"
        r"no\s+stratum|connection\s+closed",
        low,
        re.I,
    ):
        return "network_or_pool"

    if re.search(
        r"psu|checksum|undervolt|overcurrent|failed\s+to\s+detect\s+psu",
        low,
        re.I,
    ):
        return "psu_or_rail"

    if re.search(r"正常运行|有算力|alive|accepted", low, re.I) and not re.search(
        r"err:|disabled\s+hashboard|no\s+chips",
        low,
        re.I,
    ):
        return "healthy_or_minor"

    return "other_or_mixed"


def _headline_label(annotation: str) -> str:
    ann = _strip_auto_marker_lines(annotation)
    for line in ann.splitlines():
        s = line.strip()
        if not s or s.startswith("以下是"):
            continue
        if s.startswith("1.") or s.startswith("2.") or s.startswith("3."):
            continue
        return s[:256]
    return "EMPTY_HEADLINE"


def load_samples(log_dir: Path, max_body_chars: int) -> Tuple[List[str], List[str], List[str]]:
    sys.path.insert(0, str(_project_root()))
    from utils.log_footer_split import split_log_body_and_annotation

    xs: List[str] = []
    ys: List[str] = []
    paths: List[str] = []
    for p in sorted(log_dir.glob("*.txt")):
        if not p.is_file():
            continue
        raw = p.read_text(encoding="utf-8", errors="ignore")
        sp = split_log_body_and_annotation(raw)
        if sp.method == "none" or len(sp.annotation.strip()) < 60:
            continue
        body = sp.body.strip()
        if max_body_chars and len(body) > max_body_chars:
            body = body[:max_body_chars]
        xs.append(body)
        ys.append(sp.annotation.strip())
        paths.append(str(p.relative_to(_project_root())))
    return xs, ys, paths


def main() -> int:
    root = _project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--log-dir",
        type=str,
        required=True,
        help=r'含已标注 txt 的目录，如 data/logs/2026年3月/2026-03-18',
    )
    ap.add_argument(
        "--output",
        type=str,
        default="data/ml/models/diagnosis_sklearn.joblib",
        help="输出模型路径（joblib）",
    )
    ap.add_argument(
        "--metrics",
        type=str,
        default="data/ml/models/diagnosis_sklearn_metrics.json",
        help="训练指标 JSON",
    )
    ap.add_argument(
        "--label-strategy",
        choices=("coarse", "headline"),
        default="coarse",
        help="coarse=关键词粗分桶（小样本更稳）；headline=首条有效摘要行",
    )
    ap.add_argument("--min-per-class", type=int, default=2, help="headline 模式下样本数少于此的类合并为 __OTHER__")
    ap.add_argument("--max-body-chars", type=int, default=200000, help="正文最大长度，控制内存")
    ap.add_argument("--test-size", type=float, default=0.25, help="留出法测试集比例")
    ap.add_argument("--random-state", type=int, default=42)
    args = ap.parse_args()

    log_dir = (root / args.log_dir).resolve()
    if not log_dir.is_dir():
        print(f"错误: 目录不存在: {log_dir}", file=sys.stderr)
        return 1

    xs, annos, rel_paths = load_samples(log_dir, args.max_body_chars)
    if len(xs) < 8:
        print(f"有效样本过少（{len(xs)}），需带文末分析且启发式/标记能切分出 annotation", file=sys.stderr)
        return 1

    if args.label_strategy == "coarse":
        y = [_coarse_fault_label(a, b[:12000]) for a, b in zip(annos, xs)]
    else:
        raw_heads = [_headline_label(a) for a in annos]
        from collections import Counter

        cnt = Counter(raw_heads)
        y = [h if cnt[h] >= args.min_per_class else "__OTHER__" for h in raw_heads]

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import classification_report, accuracy_score
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
        import joblib
    except ImportError:
        print("请先安装: pip install scikit-learn joblib", file=sys.stderr)
        return 1

    strat = None
    if len(set(y)) > 1:
        try:
            from collections import Counter

            if min(Counter(y).values()) >= 2:
                strat = y
        except Exception:
            strat = None

    X_train, X_test, y_train, y_test, p_train, p_test = train_test_split(
        xs,
        y,
        rel_paths,
        test_size=args.test_size,
        random_state=args.random_state,
        stratify=strat,
    )

    pipe = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char",
                    ngram_range=(2, 5),
                    max_features=12000,
                    min_df=1,
                    sublinear_tf=True,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=500,
                    class_weight="balanced",
                    solver="lbfgs",
                ),
            ),
        ]
    )
    pipe.fit(X_train, y_train)
    pred = pipe.predict(X_test)
    acc = float(accuracy_score(y_test, pred))
    report = classification_report(y_test, pred, zero_division=0)

    out_model = (root / args.output).resolve()
    out_model.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "label_strategy": args.label_strategy,
        "min_per_class": args.min_per_class,
        "max_body_chars": args.max_body_chars,
        "n_samples": len(xs),
        "classes": sorted(set(y)),
        "train_paths_head": p_train[:5],
    }
    joblib.dump({"pipeline": pipe, "meta": meta}, out_model)

    out_metrics = (root / args.metrics).resolve()
    out_metrics.write_text(
        json.dumps(
            {
                "accuracy_holdout": acc,
                "n_train": len(X_train),
                "n_test": len(X_test),
                "label_strategy": args.label_strategy,
                "classification_report": report,
                "classes": sorted(set(y)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"样本数: {len(xs)}  留出测试: {len(X_test)}  准确率(测试集): {acc:.3f}")
    print(f"模型已保存: {out_model}")
    print(f"指标已保存: {out_metrics}")
    print("\n分类报告:\n" + report)
    print(
        "\n说明: 86 条属小样本，当前为「正文→粗类」基线模型，用于辅助归类；"
        "上线宜与规则引擎并用，并持续补充标注后重训。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
