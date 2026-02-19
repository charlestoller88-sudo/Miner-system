"""
AI 诊断服务 - 矿机故障诊断（面向 0 算力/低算力矿机）
结合运行状态、硬件性能与运行日志进行分析，模型学习「根本原因」而非仅症状。
"""
from typing import Dict, Any, Optional, List
from pathlib import Path

# 与 config.FAULT_TYPES_ROOT_CAUSE 及训练时 FAULT_TYPES 顺序一致（模型输出索引对应此列表）
FAULT_CLASSES_ORDERED = [
    "normal", "fan_fault", "asic_not_detected", "power_issue", "cable_connection",
    "pool_issue", "board_damage", "high_temperature", "hw_errors", "offline",
    "zero_hashrate", "low_hashrate", "other",
]
try:
    import config
    if getattr(config, "FAULT_TYPES_ROOT_CAUSE", None):
        FAULT_CLASSES_ORDERED = config.FAULT_TYPES_ROOT_CAUSE
except Exception:
    pass

# 故障类型（根因）到建议的映射；与 FAULT_TYPES_ROOT_CAUSE 一致
FAULT_SUGGESTIONS = {
    "normal": ["矿机运行正常"],
    "fan_fault": [
        "检查风扇连接与供电",
        "更换故障风扇后重启矿机",
        "查看原始日志确认风扇报错",
    ],
    "asic_not_detected": [
        "物理重拔插算力板排线、交叉测试",
        "检查供电电压与波纹",
        "单独测试该算力板",
        "固件升级/重置或硬件替换",
    ],
    "power_issue": [
        "检查电源与供电电压、波纹",
        "检查电源线连接与接触",
        "尝试更换电源或供电回路",
    ],
    "cable_connection": [
        "检查算力板/主板排线是否松动、氧化",
        "重插并固定连接线",
        "交叉测试排除单板问题",
    ],
    "pool_issue": [
        "检查网络与矿池连接",
        "确认矿池地址和端口正确",
        "尝试切换备用矿池",
    ],
    "board_damage": [
        "单独测试算力板确认损坏",
        "考虑更换算力板或送修",
    ],
    "high_temperature": [
        "清理灰尘和散热片",
        "检查风扇是否正常运转",
        "改善机柜通风环境",
    ],
    "hw_errors": [
        "检查算力板供电是否稳定",
        "尝试重启矿机",
        "如持续增长考虑更换算力板",
        "检查电源线连接",
    ],
    "offline": [
        "检查网络连接和交换机",
        "检查矿机电源",
        "尝试 ping 矿机 IP",
        "检查矿机是否开机",
    ],
    "zero_hashrate": [
        "结合下方「运行日志提示」与「历史标注参考」排查根因",
        "检查风扇、算力板检测、供电、排线、矿池等",
        "查看原始日志中的报错信息",
    ],
    "low_hashrate": [
        "结合下方「运行日志提示」与「历史标注参考」排查根因",
        "检查散热、芯片、供电、部分算力板异常等",
    ],
    "other": ["请结合运行日志与历史标注参考排查"],
}


def _rule_based_diagnose(snapshot) -> Dict[str, Any]:
    """基于规则的诊断（无模型时使用）"""
    fault_type = snapshot.fault_type or "unknown"
    return {
        "fault_type": fault_type,
        "confidence": 0.85,
        "suggested_actions": FAULT_SUGGESTIONS.get(fault_type, ["请手动检查矿机状态"]),
        "source": "rule_engine",
    }


# 运行日志关键词 → 提示方向（用于 log_hint）：(关键词或关键词元组, 故障键, 显示标签)
LOG_KEYWORD_GROUPS = [
    (("风扇", "fan"), "fan_fault", "风扇"),
    (("asic", "chain", "芯片", "未检测", "detect"), "asic_not_detected", "算力板/ASIC"),
    (("供电", "power", "voltage", "电源"), "power_issue", "供电"),
    (("排线", "连接", "cable", "connect"), "cable_connection", "排线/连接"),
    (("矿池", "pool", "reject", "stratum"), "pool_issue", "矿池"),
    (("温度", "temp", "overheat"), "high_temperature", "高温"),
    (("error", "fail", "错误", "失败"), "hw_errors", "硬件/错误"),
]


def _get_log_hint(db, miner_id: Optional[int], max_logs: int = 50) -> Optional[str]:


def _get_log_hint(db, miner_id: Optional[int], max_logs: int = 50) -> Optional[str]:
    """
    从 MinerLog 取该矿机最近日志，按关键词生成「运行日志提示」供诊断参考。
    """
    if db is None or miner_id is None:
        return None
    try:
        from database.models import MinerLog
        rows = (
            db.query(MinerLog)
            .filter(MinerLog.miner_id == miner_id)
            .order_by(MinerLog.timestamp.desc())
            .limit(max_logs)
            .all()
        )
        text = " ".join((r.content or "").lower() for r in rows).strip()
        if not text:
            return None
        hints = []
        for kws, _fault_key, label in LOG_KEYWORD_GROUPS:
            for kw in kws:
                if kw.lower() in text:
                    hints.append(label)
                    break
        if not hints:
            return None
        return "运行日志中涉及：%s。可结合上述故障类型与历史标注参考排查。" % ("、".join(hints))
    except Exception as e:
        print(f"[AI_DIAGNOSER] 获取运行日志提示失败: {e}")
        return None


def _get_labeled_reference(db, fault_type: str, limit: int = 3) -> List[Dict[str, str]]:
    """
    从数据库取回同故障类型下、您曾标注过的「可能原因」与「处理方案」作为参考。
    这样 AI 只给出粗分类（如 zero_hashrate）时，仍能展示您过去填写的详细原因与方案。
    """
    if db is None:
        return []
    try:
        from database.models import FaultLabel
        from sqlalchemy import or_, and_
        rows = (
            db.query(FaultLabel)
            .filter(
                FaultLabel.fault_type == fault_type,
                or_(
                    and_(FaultLabel.fault_cause.isnot(None), FaultLabel.fault_cause != ""),
                    and_(FaultLabel.solution.isnot(None), FaultLabel.solution != ""),
                ),
            )
            .order_by(FaultLabel.labeled_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for r in rows:
            if (r.fault_cause or "").strip() or (r.solution or "").strip():
                out.append({
                    "fault_cause": (r.fault_cause or "").strip(),
                    "solution": (r.solution or "").strip(),
                })
        return out
    except Exception as e:
        print(f"[AI_DIAGNOSER] 获取历史标注参考失败: {e}")
        return []


def diagnose_miner(snapshot, db) -> Dict[str, Any]:
    """
    诊断矿机故障
    优先使用 AI 模型，模型不存在时使用规则引擎。
    结果中会附加「历史标注参考」：同故障类型下您曾填写的故障原因与处理方案。
    """
    model_path = Path("data/models/fault_classifier.onnx")
    
    if model_path.exists():
        try:
            result = _onnx_diagnose(snapshot, str(model_path))
        except Exception as e:
            print(f"[AI_DIAGNOSER] 模型推理失败: {e}，回退到规则引擎")
            result = _rule_based_diagnose(snapshot)
    else:
        result = _rule_based_diagnose(snapshot)
    
    # 附加历史标注参考：同故障类型下您曾填写的原因与方案（最多 3 条）
    result["labeled_reference"] = _get_labeled_reference(db, result.get("fault_type") or "other", limit=3)
    # 运行日志关键词提示（结合运行状态、硬件、日志共同分析）
    result["log_hint"] = _get_log_hint(db, getattr(snapshot, "miner_id", None))
    return result


def _onnx_diagnose(snapshot, model_path: str) -> Dict[str, Any]:
    """使用 ONNX 模型进行诊断"""
    import numpy as np
    
    try:
        import onnxruntime as ort
    except ImportError:
        return _rule_based_diagnose(snapshot)
    
    theoretical = 141.0  # S19 XP
    hashrate = snapshot.hashrate or 0
    hashrate_ratio = min(1.5, hashrate / theoretical) if theoretical else 0
    
    # 构建特征向量（需与训练时一致: hashrate, power_usage, fan_speed, temperature, hw_errors, uptime, pool_rejected, hashrate_ratio, model_encoded）
    model_enc = 1 if "S19 XP" in (snapshot.miner_model or "") else 0
    
    features = [
        hashrate,
        snapshot.power_usage or 0,
        snapshot.fan_speed or 0,
        snapshot.temperature or 0,
        snapshot.hw_errors or 0,
        snapshot.uptime or 0,
        snapshot.pool_rejected or 0,
        hashrate_ratio,
        model_enc,
    ]
    
    x = np.array([features], dtype=np.float32)
    
    session = ort.InferenceSession(model_path)
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: x})
    
    probs = outputs[0][0]
    num_classes = len(probs)
    # 新模型（根因多分类）与旧模型（8 类）兼容
    if num_classes == len(FAULT_CLASSES_ORDERED):
        fault_classes = FAULT_CLASSES_ORDERED
    else:
        fault_classes = [
            "normal", "zero_hashrate", "low_hashrate",
            "high_temperature", "hw_errors", "pool_issue", "offline", "other",
        ]
    idx = int(np.argmax(probs))
    fault_type = fault_classes[idx] if idx < len(fault_classes) else "other"
    confidence = float(probs[idx])
    
    return {
        "fault_type": fault_type,
        "confidence": confidence,
        "suggested_actions": FAULT_SUGGESTIONS.get(fault_type, ["请手动检查"]),
        "source": "ai_model",
    }
