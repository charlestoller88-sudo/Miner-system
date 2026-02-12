"""
数据采集服务 - 定时采集矿机完整数据用于 AI 训练
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import delete

from database.models import Miner, MinerStat, MinerRawSnapshot, MinerLog, FaultLabel, AIDiagnosisFeedback
from miners.api_client_jsonrpc import AntminerAPIJsonRPC
from miners.log_fetcher import MinerLogFetcher
import config


def _infer_fault_type(
    hashrate: float,
    temperature: float,
    hw_errors: int,
    pool_rejected: int,
    pool_accepted: int,
    status: str,
    theoretical_hashrate: float = 141.0,
) -> str:
    """根据阈值推断故障类型"""
    if status == "offline":
        return "offline"
    
    if hashrate is None or hashrate <= 0:
        return "zero_hashrate"
    
    if temperature and temperature > config.THRESHOLDS.get("high_temperature", 75):
        return "high_temperature"
    
    if hw_errors and hw_errors > config.THRESHOLDS.get("hw_errors_high", 100):
        return "hw_errors"
    
    if pool_accepted and pool_rejected and pool_accepted > 0:
        reject_rate = pool_rejected / (pool_accepted + pool_rejected)
        if reject_rate > 0.1:
            return "pool_issue"
    
    if theoretical_hashrate and hashrate < config.THRESHOLDS.get("low_hashrate", 50):
        return "low_hashrate"
    
    if theoretical_hashrate and (hashrate / theoretical_hashrate) < 0.7:
        return "low_hashrate"
    
    return "normal"


class DataCollector:
    """矿机数据采集器"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def _get_theoretical_hashrate(self, model: str) -> float:
        """获取矿机理论算力"""
        return config.MODEL_HASHRATE.get(model, config.MODEL_HASHRATE.get("Antminer", 100.0))
    
    async def collect_miner_snapshot(self, miner: Miner) -> Optional[MinerRawSnapshot]:
        """采集单台矿机快照"""
        if not miner.ip_address:
            return None
        
        try:
            api = AntminerAPIJsonRPC(miner.ip_address, port=4028, timeout=10)
            summary_data = await api.get_full_summary()
            
            if not summary_data:
                # 离线矿机，仍然记录快照
                snapshot = MinerRawSnapshot(
                    miner_id=miner.id,
                    timestamp=datetime.now(),
                    miner_model=miner.model,
                    hashrate=0,
                    power_usage=0,
                    fan_speed=0,
                    temperature=0,
                    hw_errors=0,
                    uptime=0,
                    pool_url="",
                    pool_status="offline",
                    pool_rejected=0,
                    status="offline",
                    fault_type="offline",
                )
                self.db.add(snapshot)
                return snapshot
            
            hashrate = summary_data.get("hashrate") or 0
            power_usage = summary_data.get("power_usage") or 0
            temperature = summary_data.get("temperature") or 0
            fan_speed = summary_data.get("fan_speed") or 0
            hw_errors = summary_data.get("hw_errors") or 0
            uptime = summary_data.get("uptime") or 0
            pool = summary_data.get("pool", "")
            
            theoretical = self._get_theoretical_hashrate(miner.model or "Antminer")
            fault_type = _infer_fault_type(
                hashrate=hashrate,
                temperature=temperature,
                hw_errors=hw_errors,
                pool_rejected=0,
                pool_accepted=1,
                status="online",
                theoretical_hashrate=theoretical,
            )
            
            raw_summary = None
            raw_stats = None
            raw_devs = None
            raw_pools = None
            pool_rejected = 0
            pool_status = "Alive"
            
            store_raw = config.DATA_COLLECTION.get("store_raw_json", True)
            fetch_full = config.DATA_COLLECTION.get("fetch_full_logs_on_fault", True)
            
            if store_raw or (fetch_full and fault_type != "normal"):
                log_fetcher = MinerLogFetcher(self.db)
                detailed = await log_fetcher.fetch_detailed_logs(miner)
                
                if detailed.get("pools"):
                    pool_info = detailed["pools"][0]
                    pool_rejected = pool_info.get("rejected", 0) or 0
                    pool_accepted = pool_info.get("accepted", 1) or 1
                    pool_status = pool_info.get("status", "Unknown")
                    if pool_accepted + pool_rejected > 0:
                        reject_rate = pool_rejected / (pool_accepted + pool_rejected)
                        if reject_rate > 0.1:
                            fault_type = "pool_issue"
                
                if store_raw and detailed.get("raw_logs"):
                    for rl in detailed["raw_logs"]:
                        cat = rl.get("category", "")
                        content = rl.get("content", "{}")
                        if cat == "SUMMARY":
                            raw_summary = content
                        elif cat == "STATS":
                            raw_stats = content
                        elif cat == "DEVS":
                            raw_devs = content
                        elif cat == "POOLS":
                            raw_pools = content
            
            snapshot = MinerRawSnapshot(
                miner_id=miner.id,
                timestamp=datetime.now(),
                miner_model=miner.model,
                hashrate=hashrate,
                power_usage=power_usage,
                fan_speed=fan_speed,
                temperature=temperature,
                hw_errors=hw_errors,
                uptime=uptime,
                pool_url=pool,
                pool_status=pool_status,
                pool_rejected=pool_rejected,
                status="online",
                fault_type=fault_type,
                raw_summary_json=raw_summary if store_raw else None,
                raw_stats_json=raw_stats if store_raw else None,
                raw_devs_json=raw_devs if store_raw else None,
                raw_pools_json=raw_pools if store_raw else None,
            )
            self.db.add(snapshot)
            return snapshot
            
        except Exception as e:
            print(f"[DATA_COLLECTOR] 采集矿机 {miner.ip_address} 失败: {e}")
            return None
    
    def cleanup_old_snapshots(self) -> int:
        """删除超过保留期限的快照（同时删除关联的 FaultLabel、AIDiagnosisFeedback）"""
        retention_days = config.DATA_COLLECTION.get("snapshot_retention_days", 30)
        cutoff = datetime.now() - timedelta(days=retention_days)
        old_ids = [r[0] for r in self.db.query(MinerRawSnapshot.id).filter(MinerRawSnapshot.timestamp < cutoff).all()]
        if not old_ids:
            return 0
        self.db.execute(delete(FaultLabel).where(FaultLabel.snapshot_id.in_(old_ids)))
        self.db.execute(delete(AIDiagnosisFeedback).where(AIDiagnosisFeedback.snapshot_id.in_(old_ids)))
        stmt = delete(MinerRawSnapshot).where(MinerRawSnapshot.id.in_(old_ids))
        result = self.db.execute(stmt)
        try:
            self.db.commit()
            return len(old_ids)
        except Exception:
            self.db.rollback()
            return 0

    async def run_collection_cycle(self) -> Dict[str, int]:
        """执行一次完整采集周期"""
        deleted = self.cleanup_old_snapshots()
        if deleted:
            print(f"[DATA_COLLECTOR] 清理 {deleted} 条过期快照")
        miners = self.db.query(Miner).filter(Miner.ip_address.isnot(None)).all()
        success = 0
        failed = 0
        
        batch_size = config.SCAN_CONFIG.get("batch_size", 20)
        for i in range(0, len(miners), batch_size):
            batch = miners[i : i + batch_size]
            tasks = [self.collect_miner_snapshot(m) for m in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for r in results:
                if isinstance(r, MinerRawSnapshot):
                    success += 1
                elif isinstance(r, Exception):
                    failed += 1
                    print(f"[DATA_COLLECTOR] 采集异常: {r}")
            
            try:
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                print(f"[DATA_COLLECTOR] 提交失败: {e}")
        
        return {"success": success, "failed": failed, "total": len(miners)}
