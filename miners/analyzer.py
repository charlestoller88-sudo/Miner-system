import re
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database.models import Miner, MinerStat, Alert

class MinerAnalyzer:
    """矿机数据分析器"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def analyze_low_hashrate(self, miner_id: int, hours: int = 24):
        """分析低算力原因"""
        # 获取最近24小时的数据
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        stats = self.db.query(MinerStat).filter(
            MinerStat.miner_id == miner_id,
            MinerStat.timestamp >= cutoff_time
        ).order_by(MinerStat.timestamp.desc()).all()
        
        if not stats:
            return {"error": "无历史数据"}
        
        # 分析算力变化
        hash_rates = [s.hashrate for s in stats]
        avg_hashrate = sum(hash_rates) / len(hash_rates)
        min_hashrate = min(hash_rates)
        
        # 分析温度
        temperatures = [s.temperature for s in stats]
        avg_temp = sum(temperatures) / len(temperatures)
        max_temp = max(temperatures)
        
        # 分析风扇
        fan_speeds = [s.fan_speed for s in stats]
        avg_fan = sum(fan_speeds) / len(fan_speeds) if fan_speeds else 0
        
        # 分析硬件错误
        hw_errors = [s.hw_errors for s in stats]
        total_errors = sum(hw_errors)
        
        # 生成分析报告
        analysis = {
            "miner_id": miner_id,
            "analysis_time": datetime.now(),
            "period_hours": hours,
            "avg_hashrate": round(avg_hashrate, 2),
            "min_hashrate": round(min_hashrate, 2),
            "avg_temperature": round(avg_temp, 1),
            "max_temperature": round(max_temp, 1),
            "avg_fan_speed": round(avg_fan, 0),
            "total_hw_errors": total_errors,
        }
        
        # 判断可能的原因
        possible_causes = []
        
        if max_temp > 80:
            possible_causes.append("温度过高导致降频")
        
        if avg_fan < 3000 and max_temp > 70:
            possible_causes.append("风扇转速不足")
        
        if total_errors > 1000:
            possible_causes.append("硬件错误过多")
        
        if min_hashrate == 0:
            possible_causes.append("矿机可能重启或掉线")
        
        if len(possible_causes) == 0:
            possible_causes.append("网络连接问题或矿池问题")
        
        analysis["possible_causes"] = possible_causes
        analysis["suggestions"] = self.generate_suggestions(possible_causes)
        
        return analysis
    
    def generate_suggestions(self, causes: list) -> list:
        """根据原因生成建议"""
        suggestions = []
        cause_map = {
            "温度过高导致降频": [
                "清理矿机散热片灰尘",
                "改善机柜通风",
                "检查风扇是否正常工作",
                "降低环境温度"
            ],
            "风扇转速不足": [
                "清洁风扇叶片",
                "检查风扇电源连接",
                "考虑更换风扇",
                "降低环境温度以减少风扇负载"
            ],
            "硬件错误过多": [
                "重启矿机",
                "检查ASIC芯片状态",
                "降低超频频率",
                "联系供应商检查硬件"
            ],
            "矿机可能重启或掉线": [
                "检查电源连接",
                "检查网络连接",
                "查看矿机日志",
                "检查固件版本"
            ],
            "网络连接问题或矿池问题": [
                "ping测试矿池连接",
                "更换备用矿池",
                "检查路由器/交换机",
                "重启网络设备"
            ]
        }
        
        for cause in causes:
            if cause in cause_map:
                suggestions.extend(cause_map[cause])
        
        return list(set(suggestions))  # 去重
    
    def get_miner_performance_trend(self, miner_id: int, days: int = 7):
        """获取矿机性能趋势"""
        cutoff_time = datetime.now() - timedelta(days=days)
        
        # 按小时分组统计
        stats = self.db.query(
            MinerStat.timestamp,
            MinerStat.hashrate,
            MinerStat.temperature,
            MinerStat.power_usage
        ).filter(
            MinerStat.miner_id == miner_id,
            MinerStat.timestamp >= cutoff_time
        ).order_by(MinerStat.timestamp).all()
        
        return [
            {
                'timestamp': s.timestamp.isoformat(),
                'hashrate': s.hashrate,
                'temperature': s.temperature,
                'power_usage': s.power_usage
            }
            for s in stats
        ]