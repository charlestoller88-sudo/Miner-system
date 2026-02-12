# 矿机故障 AI 训练项目

在 Ubuntu 训练主机上使用，从微型 PC 导出的数据进行模型训练。

## 目录结构

```
miner_ai_training/
├── data/
│   ├── raw/           # 从微型 PC 复制的原始导出 CSV
│   ├── processed/     # 预处理后的数据
│   └── labeled/       # 标注完成的数据
├── src/
│   ├── preprocess.py  # 数据清洗、特征工程
│   ├── label_tool.py  # 自动标注工具
│   └── feature_extract.py
├── train.py           # 训练入口
├── export_onnx.py     # 导出 ONNX
└── requirements.txt
```

## 使用流程

### 1. 复制数据

从微型 PC 的 `data/training_exports/*.csv` 复制到本项目的 `data/raw/`。

### 2. 预处理

```bash
python -c "
from src.preprocess import run_preprocess
run_preprocess('data/raw/miner_training_data_xxx.csv', 'data/processed')
"
```

### 3. 训练

```bash
pip install -r requirements.txt
python train.py --data data/processed/miner_training_data_xxx.csv --output models
```

### 4. 导出 ONNX

```bash
python export_onnx.py --input models/fault_classifier.joblib --output models/fault_classifier.onnx
```

### 5. 部署

将 `models/fault_classifier.onnx` 复制到微型 PC 的 `Miner_system/data/models/` 目录。

## 数据要求

- 最少每个故障类型 50+ 条
- 推荐总计 1000+ 条
- 人工标注越多，模型越准
