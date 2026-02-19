import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent

# 数据库配置
DATABASE_URL = f"sqlite:///{BASE_DIR}/data/miner_data.db"

# 矿机默认凭据
MINER_CREDENTIALS = {
    'username': 'root',
    'password': 'root'
}

# 扫描配置
SCAN_CONFIG = {
    'timeout': 15,  # 秒（增加到15秒，因为需要顺序调用多个CGI接口）
    'batch_size': 20,  # 并发数量
    'scan_interval': 300,  # 扫描间隔（秒）
}

# IP范围扫描配置（用于发现新矿机）
IP_SCAN_CONFIG = {
    'port': 4028,        # Antminer JSON-RPC 端口
    'timeout': 5,        # 单IP探测超时（秒）- 增加到5秒
    'concurrency': 20,   # 并发扫描数量 - 降低到20避免网络拥堵
    'retry_times': 2,    # 失败重试次数
    'retry_delay': 0.5,  # 重试延迟（秒）
}

# IP范围（根据您的Excel自动生成）
IP_RANGES = [
    '10.102.0.0/24',  # 10.102.0.*
    '10.102.1.0/24',  # 10.102.1.*
]

# 矿机型号映射（根据设备编号判断）
MODEL_MAPPING = {
    'NCATX': 'Antminer S19 XP',
    # 可以添加其他型号的映射
}

# 矿机型号理论算力配置（TH/s）
MODEL_HASHRATE = {
    # S19 系列
    'Antminer S19 XP': 141.0,
    'Antminer S19 Pro': 110.0,
    'Antminer S19j Pro': 104.0,
    'Antminer S19': 95.0,
    'Antminer S19j': 90.0,
    
    # S17 系列
    'Antminer S17 Pro': 53.0,
    'Antminer S17': 56.0,
    'Antminer S17+': 73.0,
    
    # T19 系列
    'Antminer T19': 84.0,
    
    # T17 系列
    'Antminer T17': 40.0,
    'Antminer T17+': 58.0,
    
    # 默认值（如果型号不在列表中）
    'Antminer': 100.0,  # 默认假设 100 TH/s
}

# 阈值配置
THRESHOLDS = {
    'low_hashrate': 50,  # TH/s低于此值为低算力
    'high_temperature': 75,  # 温度高于此值告警
    'low_fan_speed': 3000,  # 风扇转速低于此值告警
    'hw_errors_high': 100,  # 硬件错误超过此值告警
}

# AI 训练数据采集配置
DATA_COLLECTION = {
    'interval_seconds': 300,      # 采集间隔（秒）
    'store_raw_json': True,       # 是否存储原始 JSON
    'snapshot_retention_days': 30,  # 快照保留天数
    'fetch_full_logs_on_fault': True,  # 故障时是否拉取完整 raw_logs
}

# 故障类型（根因具体化，供标注与 AI 诊断一致）
# 标注时请尽量选择「根本原因」而非仅填「0算力/低算力」，便于模型学习
FAULT_TYPES_ROOT_CAUSE = [
    "normal",              # 正常
    "fan_fault",           # 风扇故障
    "asic_not_detected",   # 算力板/ASIC 未检测
    "power_issue",         # 供电异常
    "cable_connection",    # 排线/连接问题
    "pool_issue",          # 矿池问题
    "board_damage",        # 算力板损坏
    "high_temperature",    # 高温
    "hw_errors",           # 硬件错误
    "offline",             # 离线
    "zero_hashrate",       # 0算力（未区分根因时选此项）
    "low_hashrate",       # 低算力（未区分根因时选此项）
    "other",               # 其他
]