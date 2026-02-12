import asyncio
import aiohttp
from datetime import datetime
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from miners.api_client_jsonrpc import AntminerAPIJsonRPC  # 使用 JSON-RPC 版本
from database.models import Miner, MinerStat, Alert, MinerLog
from utils.ip_scanner import discover_miners as ip_discover_miners
import config

class MinerScanner:
    """矿机扫描器"""
    
    def __init__(self, db: Session):
        self.db = db
        self.online_miners = []
        self.offline_miners = []
        self.error_miners = []
    
    async def scan_single_miner(self, miner: Miner):
        """扫描单个矿机"""
        if not miner.ip_address:
            return None
        
        try:
            print(f"[SCAN] 开始扫描矿机: {miner.ip_address} (ID: {miner.id})")
            api = AntminerAPIJsonRPC(miner.ip_address, port=4028, timeout=10)
            data = await api.get_full_summary()
            
            print(f"[SCAN] 矿机 {miner.ip_address} get_full_summary 返回: {data}")
            
            if data:
                # 更新矿机状态
                miner.status = 'online'
                miner.last_seen = datetime.now()
                
                # 保存状态记录
                stat = MinerStat(
                    miner_id=miner.id,
                    hashrate=data.get('hashrate', 0),
                    power_usage=data.get('power_usage', 0),
                    temperature=data.get('temperature', 0),
                    fan_speed=data.get('fan_speed', 0),
                    pool=data.get('pool', ''),
                    hw_errors=data.get('hw_errors', 0),
                    uptime=data.get('uptime', 0)
                )
                self.db.add(stat)
                
                # 检查告警条件
                await self.check_alerts(miner, data)
                
                self.online_miners.append(miner.id)
                return data
            else:
                miner.status = 'offline'
                self.offline_miners.append(miner.id)
                
        except Exception as e:
            miner.status = 'error'
            self.error_miners.append(miner.id)
            print(f"扫描矿机 {miner.ip_address} 出错: {str(e)}")
        
        return None
    
    async def check_alerts(self, miner: Miner, data: Dict):
        """检查告警条件"""
        alerts = []
        
        # 低算力告警
        if data.get('hashrate', 0) < config.THRESHOLDS['low_hashrate']:
            alerts.append({
                'type': 'low_hashrate',
                'severity': 'warning',
                'message': f"算力过低: {data.get('hashrate', 0)} TH/s"
            })
        
        # 高温告警
        if data.get('temperature', 0) > config.THRESHOLDS['high_temperature']:
            alerts.append({
                'type': 'high_temperature',
                'severity': 'critical',
                'message': f"温度过高: {data.get('temperature', 0)}°C"
            })
        
        # 风扇转速告警
        if data.get('fan_speed', 0) < config.THRESHOLDS['low_fan_speed']:
            alerts.append({
                'type': 'low_fan_speed',
                'severity': 'warning',
                'message': f"风扇转速过低: {data.get('fan_speed', 0)} RPM"
            })
        
        # 硬件错误告警
        if data.get('hw_errors', 0) > 100:
            alerts.append({
                'type': 'hw_errors',
                'severity': 'warning',
                'message': f"硬件错误过多: {data.get('hw_errors', 0)}"
            })
        
        # 保存告警
        for alert_data in alerts:
            alert = Alert(
                miner_id=miner.id,
                alert_type=alert_data['type'],
                severity=alert_data['severity'],
                message=alert_data['message']
            )
            self.db.add(alert)
    
    async def scan_all_miners(self):
        """扫描所有矿机"""
        # 获取所有有IP的矿机
        miners = self.db.query(Miner).filter(Miner.ip_address.isnot(None)).all()
        
        # 分批扫描
        batch_size = config.SCAN_CONFIG['batch_size']
        for i in range(0, len(miners), batch_size):
            batch = miners[i:i+batch_size]
            tasks = [self.scan_single_miner(miner) for miner in batch]
            await asyncio.gather(*tasks)
            
            # 分批提交到数据库
            try:
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                print(f"数据库提交失败: {str(e)}")
        
        return {
            'total': len(miners),
            'online': len(self.online_miners),
            'offline': len(self.offline_miners),
            'error': len(self.error_miners)
        }
    
    async def discover_miners(
        self,
        ip_range: str,
        pool_filter: Optional[str] = None
    ) -> Dict:
        """
        根据IP范围扫描发现网络中的矿机，并按矿池过滤
        ip_range: 支持 CIDR(10.102.0.0/24)、范围(10.102.0.1-100)、逗号分隔多个
        pool_filter: 矿池URL关键词，如 "pool.btc.com" 或 "btc.com"
        返回: {success, discovered, imported, skipped, total_ips, count, error}
        """
        result = await ip_discover_miners(
            ip_range=ip_range,
            pool_filter=pool_filter
        )
        if not result.get('success'):
            return result
        
        discovered = result.get('discovered', [])
        
        # 每次扫描前清除所有现有矿机（含关联的 stats/alerts/logs 会级联删除）
        self.db.query(Miner).delete()
        
        imported = 0
        for item in discovered:
            ip = item.get('ip', '')
            if not ip:
                continue
            
            # 使用 IP 作为唯一标识，矿工账号作为显示名称
            # serial_number 必须唯一，使用 IP
            serial = f"MINER-{ip.replace('.', '-')}"
            
            # 从矿工账号提取显示名称（用于 model 或 notes）
            miner_name = item.get('miner_name', '')
            model = item.get('model', 'Antminer')
            
            # 如果有矿工账号，将其保存在 notes 或 location 字段便于识别
            notes = f"矿工账号: {miner_name}" if miner_name else None
            
            miner = Miner(
                ip_address=ip,
                serial_number=serial,
                model=model,
                location=miner_name if miner_name else None,  # 使用 location 字段存储矿工账号
                status='online',
                last_seen=datetime.now(),
                notes=notes
            )
            self.db.add(miner)
            imported += 1
        
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            result['success'] = False
            result['error'] = str(e)
            return result
        
        result['imported'] = imported
        result['skipped'] = 0  # 每次扫描会清空列表，无跳过
        return result