"""
数据预处理 - 清洗、标注、特征工程
"""
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.preprocessing import StandardScaler, LabelEncoder

# 故障类型（根因具体化，与 config.FAULT_TYPES_ROOT_CAUSE 及前端标注选项一致）
FAULT_TYPES = [
    "normal", "fan_fault", "asic_not_detected", "power_issue", "cable_connection",
    "pool_issue", "board_damage", "high_temperature", "hw_errors", "offline",
    "zero_hashrate", "low_hashrate", "other",
]


def load_raw_data(csv_path: str) -> pd.DataFrame:
    """加载原始 CSV 数据"""
    return pd.read_csv(csv_path, encoding="utf-8-sig")


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """数据清洗"""
    # 去除全空行
    df = df.dropna(how="all")
    
    # 数值列填充
    numeric_cols = ["hashrate", "power_usage", "fan_speed", "temperature", "hw_errors", "uptime", "pool_rejected"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    
    # 去除关键特征缺失过多的行
    required = ["hashrate", "temperature"]
    for col in required:
        if col in df.columns and df[col].isna().sum() / len(df) > 0.5:
            df = df.dropna(subset=[col])
    
    return df


def infer_labels(df: pd.DataFrame) -> pd.DataFrame:
    """无人工标注时，用规则自动推断标签"""
    if "labeled_fault_type" not in df.columns:
        df["label"] = "unknown"
        return df
    
    # 优先使用人工标注
    has_label = df["labeled_fault_type"].notna() & (df["labeled_fault_type"] != "")
    df["label"] = df["labeled_fault_type"].where(has_label)
    
    # 无标注的用 fault_type 或规则推断
    no_label = df["label"].isna()
    df.loc[no_label, "label"] = df.loc[no_label, "fault_type"]
    
    # 仍未确定的用规则
    still_unknown = df["label"].isna() | (df["label"] == "")
    
    def rule_infer(row):
        if row.get("hashrate", 0) <= 0:
            return "zero_hashrate"
        if row.get("temperature", 0) > 75:
            return "high_temperature"
        if row.get("hw_errors", 0) > 100:
            return "hw_errors"
        if row.get("hashrate", 0) < 50:
            return "low_hashrate"
        if row.get("status") == "offline":
            return "offline"
        return "normal"
    
    df.loc[still_unknown, "label"] = df.loc[still_unknown].apply(rule_infer, axis=1)
    
    return df


def feature_engineering(df: pd.DataFrame, theoretical_hashrate: float = 141.0) -> pd.DataFrame:
    """特征工程"""
    # 算力达成率
    if "hashrate" in df.columns:
        df["hashrate_ratio"] = df["hashrate"] / theoretical_hashrate
        df["hashrate_ratio"] = df["hashrate_ratio"].clip(0, 1.5)
    
    # 矿机型号编码
    if "miner_model" in df.columns:
        le = LabelEncoder()
        df["model_encoded"] = le.fit_transform(df["miner_model"].astype(str))
    
    return df


def prepare_features(df: pd.DataFrame) -> tuple:
    """准备训练特征和标签"""
    feature_cols = [
        "hashrate", "power_usage", "fan_speed", "temperature",
        "hw_errors", "uptime", "pool_rejected", "hashrate_ratio", "model_encoded"
    ]
    
    available = [c for c in feature_cols if c in df.columns]
    X = df[available].fillna(0)
    
    if "label" not in df.columns:
        infer_labels(df)
    
    # 标注中可能出现的历史类型（如旧版 zero_hashrate）均在 FAULT_TYPES 中；未知类型归为 other
    def to_known_label(v):
        v = (v or "").strip() or "other"
        return v if v in FAULT_TYPES else "other"
    
    le = LabelEncoder()
    le.fit(FAULT_TYPES)
    y = le.transform(df["label"].fillna("other").astype(str).apply(to_known_label))
    
    return X.values, y, le, available


def run_preprocess(
    input_path: str,
    output_dir: str = "data/processed",
    theoretical_hashrate: float = 141.0,
) -> str:
    """完整预处理流程"""
    df = load_raw_data(input_path)
    df = clean_data(df)
    df = infer_labels(df)
    df = feature_engineering(df, theoretical_hashrate)
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(output_dir) / Path(input_path).name
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    
    print(f"预处理完成: {out_path}, 共 {len(df)} 条")
    return str(out_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="输入 CSV 路径")
    parser.add_argument("-o", "--output-dir", default="data/processed")
    parser.add_argument("--theoretical-hashrate", type=float, default=141.0)
    args = parser.parse_args()
    run_preprocess(args.input, args.output_dir, args.theoretical_hashrate)
