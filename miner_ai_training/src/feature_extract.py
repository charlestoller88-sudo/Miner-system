"""
特征提取 - 从原始数据构建模型输入特征
"""
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler, LabelEncoder

DEFAULT_FEATURE_COLS = [
    "hashrate", "power_usage", "fan_speed", "temperature",
    "hw_errors", "uptime", "pool_rejected", "hashrate_ratio", "model_encoded"
]


def extract_features(df: pd.DataFrame, feature_cols: list = None) -> np.ndarray:
    """提取特征矩阵"""
    cols = feature_cols or DEFAULT_FEATURE_COLS
    available = [c for c in cols if c in df.columns]
    X = df[available].fillna(0).values.astype(np.float32)
    return X


def get_feature_names() -> list:
    """返回默认特征名（用于 ONNX 部署时保持一致）"""
    return DEFAULT_FEATURE_COLS.copy()
