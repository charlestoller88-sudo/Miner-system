from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import config

Base = declarative_base()
engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Miner(Base):
    """矿机基本信息表"""
    __tablename__ = "miners"
    
    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String(15), unique=True, index=True)
    serial_number = Column(String(50), unique=True)
    model = Column(String(50))
    location = Column(String(100))
    status = Column(String(20), default="unknown")  # online, offline, error
    last_seen = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    notes = Column(Text, nullable=True)
    
    # 定义关系
    stats = relationship("MinerStat", back_populates="miner", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="miner", cascade="all, delete-orphan")
    logs = relationship("MinerLog", back_populates="miner", cascade="all, delete-orphan")

class MinerStat(Base):
    """矿机状态历史表"""
    __tablename__ = "miner_stats"
    
    id = Column(Integer, primary_key=True, index=True)
    miner_id = Column(Integer, ForeignKey("miners.id"), index=True)  # 添加外键
    timestamp = Column(DateTime, default=datetime.now, index=True)
    hashrate = Column(Float)  # 算力 (TH/s)
    power_usage = Column(Float)  # 功耗 (W)
    temperature = Column(Float)  # 温度 (°C)
    fan_speed = Column(Integer)  # 风扇转速 (RPM)
    uptime = Column(Integer)  # 运行时间 (分钟)
    pool = Column(String(100))  # 矿池信息
    hw_errors = Column(Integer)  # 硬件错误数
    
    # 定义关系
    miner = relationship("Miner", back_populates="stats")

class Alert(Base):
    """告警表"""
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, index=True)
    miner_id = Column(Integer, ForeignKey("miners.id"), index=True)  # 添加外键
    alert_type = Column(String(50))  # low_hashrate, high_temp, offline, etc.
    severity = Column(String(20))  # critical, warning, info
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(50), nullable=True)
    
    # 定义关系
    miner = relationship("Miner", back_populates="alerts")

class MinerLog(Base):
    """矿机日志表"""
    __tablename__ = "miner_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    miner_id = Column(Integer, ForeignKey("miners.id"), index=True)  # 添加外键
    timestamp = Column(DateTime, default=datetime.now)
    log_type = Column(String(50))  # system, error, warning, info
    content = Column(Text)
    analyzed = Column(Boolean, default=False)
    analysis_result = Column(Text, nullable=True)
    
    # 定义关系
    miner = relationship("Miner", back_populates="logs")


class MinerRawSnapshot(Base):
    """矿机完整快照表 - 用于 AI 训练"""
    __tablename__ = "miner_raw_snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    miner_id = Column(Integer, ForeignKey("miners.id"), index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    
    # 数值特征
    miner_model = Column(String(50), nullable=True)
    hashrate = Column(Float, nullable=True)  # TH/s
    power_usage = Column(Float, nullable=True)  # W
    fan_speed = Column(Integer, nullable=True)  # RPM
    temperature = Column(Float, nullable=True)  # °C
    hw_errors = Column(Integer, nullable=True)
    uptime = Column(Integer, nullable=True)  # 分钟
    
    # 矿池信息
    pool_url = Column(String(256), nullable=True)
    pool_status = Column(String(20), nullable=True)
    pool_rejected = Column(Integer, nullable=True)
    
    # 状态与故障类型
    status = Column(String(20), default="unknown")  # online, offline, error
    fault_type = Column(String(30), nullable=True)  # normal, zero_hashrate, low_hashrate, high_temperature, hw_errors, pool_issue, other
    
    # 原始 JSON（可选存储，用于深度分析）
    raw_summary_json = Column(Text, nullable=True)
    raw_stats_json = Column(Text, nullable=True)
    raw_devs_json = Column(Text, nullable=True)
    raw_pools_json = Column(Text, nullable=True)


class FaultLabel(Base):
    """人工标注的故障类型 - 供监督学习"""
    __tablename__ = "fault_labels"
    
    id = Column(Integer, primary_key=True, index=True)
    miner_id = Column(Integer, ForeignKey("miners.id"), index=True)
    snapshot_id = Column(Integer, ForeignKey("miner_raw_snapshots.id"), index=True)
    fault_type = Column(String(30), nullable=False)  # normal, zero_hashrate, low_hashrate, high_temperature, hw_errors, pool_issue, other
    fault_cause = Column(Text, nullable=True)  # 可能原因
    solution = Column(Text, nullable=True)  # 处理方案
    labeled_at = Column(DateTime, default=datetime.now)
    user_comment = Column(Text, nullable=True)


class AIDiagnosisFeedback(Base):
    """AI 诊断反馈 - 用于持续学习"""
    __tablename__ = "ai_diagnosis_feedback"
    
    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("miner_raw_snapshots.id"), index=True)
    miner_id = Column(Integer, ForeignKey("miners.id"), index=True)
    ai_fault_type = Column(String(30), nullable=True)
    ai_confidence = Column(Float, nullable=True)
    user_correct = Column(Boolean, nullable=True)  # True=正确, False=错误
    user_actual_fault_type = Column(String(30), nullable=True)  # 用户修正的实际故障类型
    feedback_at = Column(DateTime, default=datetime.now)

# 创建所有表
def init_db():
    Base.metadata.create_all(bind=engine)
    print("数据库初始化完成")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()