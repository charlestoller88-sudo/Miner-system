"""
标注工具 - 半自动标注，人工复核
"""
import pandas as pd
from pathlib import Path

FAULT_TYPES = [
    "normal", "zero_hashrate", "low_hashrate",
    "high_temperature", "hw_errors", "pool_issue", "offline", "other"
]


def auto_label(row) -> str:
    """规则自动标注"""
    hashrate = row.get("hashrate", 0) or 0
    temp = row.get("temperature", 0) or 0
    hw = row.get("hw_errors", 0) or 0
    status = str(row.get("status", ""))
    
    if status == "offline":
        return "offline"
    if hashrate <= 0:
        return "zero_hashrate"
    if temp > 75:
        return "high_temperature"
    if hw > 100:
        return "hw_errors"
    if hashrate < 50:
        return "low_hashrate"
    
    pool_rej = row.get("pool_rejected", 0) or 0
    pool_acc = row.get("pool_accepted", 1) or 1
    if pool_acc + pool_rej > 0 and pool_rej / (pool_acc + pool_rej) > 0.1:
        return "pool_issue"
    
    return "normal"


def run_auto_label(csv_path: str, output_path: str = None) -> pd.DataFrame:
    """对无标注记录执行自动标注"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    
    has_label = "labeled_fault_type" in df.columns
    if has_label:
        needs_label = df["labeled_fault_type"].isna() | (df["labeled_fault_type"] == "")
    else:
        df["labeled_fault_type"] = ""
        needs_label = pd.Series([True] * len(df))
    
    df.loc[needs_label, "labeled_fault_type"] = df.loc[needs_label].apply(auto_label, axis=1)
    
    if output_path:
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"已保存: {output_path}")
    
    return df


def stats_report(df: pd.DataFrame):
    """输出标注统计"""
    col = "labeled_fault_type" if "labeled_fault_type" in df.columns else "fault_type"
    if col not in df.columns:
        print("无标注列")
        return
    
    counts = df[col].value_counts()
    print("\n故障类型分布:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
