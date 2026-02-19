"""
模型训练入口 - 故障分类
"""
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix

# 故障类型（根因具体化，与 preprocess.FAULT_TYPES 一致）
FAULT_TYPES = [
    "normal", "fan_fault", "asic_not_detected", "power_issue", "cable_connection",
    "pool_issue", "board_damage", "high_temperature", "hw_errors", "offline",
    "zero_hashrate", "low_hashrate", "other",
]


def load_data(csv_path: str):
    """加载预处理后的数据"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    
    feature_cols = [
        "hashrate", "power_usage", "fan_speed", "temperature",
        "hw_errors", "uptime", "pool_rejected", "hashrate_ratio", "model_encoded"
    ]
    available = [c for c in feature_cols if c in df.columns]
    
    X = df[available].fillna(0).values.astype(np.float32)
    
    label_col = "labeled_fault_type" if "labeled_fault_type" in df.columns else ("label" if "label" in df.columns else "fault_type")
    if label_col not in df.columns:
        raise ValueError("数据中缺少标签列 (labeled_fault_type / label / fault_type)")
    
    from sklearn.preprocessing import LabelEncoder
    def to_known_label(v):
        v = (v or "").strip() or "other"
        return v if v in FAULT_TYPES else "other"
    le = LabelEncoder()
    le.fit(FAULT_TYPES)
    y = le.transform(df[label_col].fillna("other").astype(str).apply(to_known_label))
    
    return X, y, le, available


def train_sklearn(X, y, available, le):
    """使用 sklearn 模型训练（轻量，易于导出）"""
    from sklearn.ensemble import RandomForestClassifier
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    clf = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)
    clf.fit(X_train, y_train)
    
    y_pred = clf.predict(X_test)
    class_names = list(le.classes_)
    print(classification_report(y_test, y_pred, target_names=class_names, zero_division=0))
    print("混淆矩阵:")
    print(confusion_matrix(y_test, y_pred))
    
    return clf, X_train, available


def train_mlp(X, y, available):
    """使用 PyTorch MLP 训练"""
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("未安装 PyTorch，使用 RandomForest")
        return train_sklearn(X, y, available)
    
    from sklearn.preprocessing import StandardScaler
    from torch.utils.data import TensorDataset, DataLoader
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    
    X_t = torch.FloatTensor(X_train_s)
    y_t = torch.LongTensor(y_train)
    
    dataset = TensorDataset(X_t, y_t)
    loader = DataLoader(dataset, batch_size=32, shuffle=True)
    
    n_features = X.shape[1]
    n_classes = len(FAULT_TYPES)
    
    class MLP(nn.Module):
        def __init__(self):
            super().__init__()
            self.fc = nn.Sequential(
                nn.Linear(n_features, 64),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(64, 32),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(32, n_classes),
            )
        
        def forward(self, x):
            return self.fc(x)
    
    model = MLP()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    crit = nn.CrossEntropyLoss()
    
    for epoch in range(50):
        for bx, by in loader:
            opt.zero_grad()
            loss = crit(model(bx), by)
            loss.backward()
            opt.step()
    
    model.eval()
    with torch.no_grad():
        y_pred = model(torch.FloatTensor(scaler.transform(X_test))).argmax(1).numpy()
    
    print(classification_report(y_test, y_pred, target_names=FAULT_TYPES[:n_classes]))
    return model, scaler, available


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/processed/miner_training_data.csv", help="预处理后的 CSV")
    parser.add_argument("--output", default="models", help="模型输出目录")
    parser.add_argument("--model", choices=["sklearn", "mlp"], default="sklearn")
    
    args = parser.parse_args()
    
    data_path = Path(args.data)
    if not data_path.exists():
        # 尝试从 raw 找
        raw_files = list(Path("data/raw").glob("*.csv")) + list(Path("data/processed").glob("*.csv"))
        if not raw_files:
            print("未找到数据文件，请先将 data/training_exports/*.csv 复制到 data/raw/")
            return
        data_path = raw_files[0]
        print(f"使用数据: {data_path}")
    
    X, y, le, feature_cols = load_data(str(data_path))
    print(f"样本数: {len(X)}, 特征: {feature_cols}")
    
    if args.model == "sklearn":
        clf, X_train, _ = train_sklearn(X, y, feature_cols, le)
        
        # 保存
        import joblib
        Path(args.output).mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": clf, "label_encoder": le, "feature_cols": feature_cols},
                    Path(args.output) / "fault_classifier.joblib")
        print(f"模型已保存: {args.output}/fault_classifier.joblib")
    else:
        model, scaler, _ = train_mlp(X, y, feature_cols)
        Path(args.output).mkdir(parents=True, exist_ok=True)
        torch.save({"model": model.state_dict(), "scaler": scaler, "feature_cols": feature_cols},
                   Path(args.output) / "fault_classifier.pt")
        print(f"模型已保存: {args.output}/fault_classifier.pt")


if __name__ == "__main__":
    main()
