"""
加载 train_diagnosis_sklearn.py 导出的模型，对单份日志正文预测粗粒度类别。

用法（项目根目录）:
  python scripts/predict_diagnosis_sklearn.py --model data/ml/models/diagnosis_sklearn.joblib --file "data/logs/.../xxx.txt"
  python scripts/predict_diagnosis_sklearn.py --model ... --file x.txt --top 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=str, required=True, help="diagnosis_sklearn.joblib 路径")
    ap.add_argument("--file", type=str, required=True, help="矿机 txt 路径")
    ap.add_argument("--top", type=int, default=3, help="输出概率最高的前 K 类")
    args = ap.parse_args()

    try:
        import joblib
    except ImportError:
        print("请安装: pip install joblib", file=sys.stderr)
        return 1

    model_path = (root / args.model).resolve() if not Path(args.model).is_absolute() else Path(args.model)
    fp = (root / args.file).resolve() if not Path(args.file).is_absolute() else Path(args.file)
    if not model_path.is_file():
        print(f"模型不存在: {model_path}", file=sys.stderr)
        return 1
    if not fp.is_file():
        print(f"文件不存在: {fp}", file=sys.stderr)
        return 1

    blob = joblib.load(model_path)
    pipe = blob["pipeline"]
    meta = blob.get("meta", {})

    raw = fp.read_text(encoding="utf-8", errors="ignore")
    from utils.log_footer_split import split_log_body_and_annotation

    sp = split_log_body_and_annotation(raw)
    body = sp.body.strip() if sp.method != "none" else raw.strip()
    max_c = meta.get("max_body_chars")
    if max_c and len(body) > max_c:
        body = body[: int(max_c)]

    proba = None
    if hasattr(pipe.named_steps["clf"], "predict_proba"):
        proba = pipe.predict_proba([body])[0]
        classes = pipe.named_steps["clf"].classes_
        topk = min(args.top, len(classes))
        idx = proba.argsort()[::-1][:topk]
        print(f"文件: {fp}")
        print(f"模型标签策略: {meta.get('label_strategy', '?')}")
        print("Top 预测:")
        for i in idx:
            print(f"  {classes[i]}: {proba[i]:.4f}")
    else:
        print(pipe.predict([body])[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
