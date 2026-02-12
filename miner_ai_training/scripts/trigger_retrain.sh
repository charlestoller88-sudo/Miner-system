#!/bin/bash
# 在 Ubuntu 训练主机上执行 - 完整重训练流程
set -e
cd "$(dirname "$0")/.."

echo "=== 1. 查找最新数据 ==="
LATEST=$(ls -t data/raw/*.csv data/processed/*.csv 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "未找到数据文件，请先将 data/training_exports/*.csv 复制到 data/raw/"
    exit 1
fi
echo "使用数据: $LATEST"

echo "=== 2. 预处理 ==="
python -c "
from src.preprocess import load_raw_data, clean_data, infer_labels, feature_engineering
import pandas as pd
from pathlib import Path
df = load_raw_data(\"$LATEST\")
df = clean_data(df)
df = infer_labels(df)
df = feature_engineering(df, 141.0)
Path('data/processed').mkdir(exist_ok=True)
out = 'data/processed/prepared.csv'
df.to_csv(out, index=False)
print('预处理完成:', out)
"

echo "=== 3. 训练 ==="
python train.py --data data/processed/prepared.csv --output models --model sklearn

echo "=== 4. 导出 ONNX ==="
python export_onnx.py --input models/fault_classifier.joblib --output models/fault_classifier.onnx --n-features 9

echo "=== 5. 完成 ==="
echo "模型已保存到 models/"
echo "请将 models/fault_classifier.onnx 复制到微型 PC 的 Miner_system/data/models/ 目录"
