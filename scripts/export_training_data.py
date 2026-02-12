"""
导出训练数据 - 从 miner_raw_snapshots 和 fault_labels 导出为 CSV
用于同步到 Ubuntu 训练主机进行模型训练
"""
import sys
import csv
import os
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.models import SessionLocal, MinerRawSnapshot, FaultLabel, Miner


def export_to_csv(
    output_dir: str = "data/training_exports",
    days: int = 30,
    fault_type_filter: str = None,
) -> str:
    """
    导出训练数据为 CSV
    output_dir: 输出目录
    days: 导出最近 N 天的数据
    fault_type_filter: 可选，只导出指定故障类型
    """
    db = SessionLocal()
    
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir) / f"miner_training_data_{timestamp}.csv"
    
    since = datetime.now() - timedelta(days=days)
    
    query = (
        db.query(MinerRawSnapshot)
        .filter(MinerRawSnapshot.timestamp >= since)
        .order_by(MinerRawSnapshot.timestamp.desc())
    )
    
    if fault_type_filter:
        query = query.filter(MinerRawSnapshot.fault_type == fault_type_filter)
    
    snapshots = query.all()
    
    # 获取人工标注
    snapshot_ids = [s.id for s in snapshots]
    labels = {
        l.snapshot_id: l
        for l in db.query(FaultLabel)
        .filter(FaultLabel.snapshot_id.in_(snapshot_ids))
        .all()
    }
    
    columns = [
        "id", "miner_id", "timestamp", "miner_model", "hashrate", "power_usage",
        "fan_speed", "temperature", "hw_errors", "uptime", "pool_url", "pool_status",
        "pool_rejected", "status", "fault_type", "labeled_fault_type", "fault_cause",
        "solution",
    ]
    
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        
        for s in snapshots:
            label = labels.get(s.id)
            row = {
                "id": s.id,
                "miner_id": s.miner_id,
                "timestamp": s.timestamp.isoformat() if s.timestamp else "",
                "miner_model": s.miner_model or "",
                "hashrate": s.hashrate or 0,
                "power_usage": s.power_usage or 0,
                "fan_speed": s.fan_speed or 0,
                "temperature": s.temperature or 0,
                "hw_errors": s.hw_errors or 0,
                "uptime": s.uptime or 0,
                "pool_url": (s.pool_url or "")[:100],
                "pool_status": s.pool_status or "",
                "pool_rejected": s.pool_rejected or 0,
                "status": s.status or "",
                "fault_type": s.fault_type or "",
                "labeled_fault_type": label.fault_type if label else "",
                "fault_cause": label.fault_cause if label else "",
                "solution": label.solution if label else "",
            }
            writer.writerow(row)
    
    db.close()
    
    print(f"导出完成: {output_path}")
    print(f"共 {len(snapshots)} 条记录")
    return str(output_path)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="导出最近 N 天数据")
    parser.add_argument("--output", default="data/training_exports", help="输出目录")
    parser.add_argument("--fault-type", default=None, help="只导出指定故障类型")
    
    args = parser.parse_args()
    
    export_to_csv(
        output_dir=args.output,
        days=args.days,
        fault_type_filter=args.fault_type,
    )
