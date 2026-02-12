"""
AI 诊断服务 - 矿机故障诊断
模型未部署时使用规则引擎，部署后加载 ONNX 模型进行推理
"""
from typing import Dict, Any, Optional
from pathlib import Path

# 故障类型到建议的映射
FAULT_SUGGESTIONS = {
    "zero_hashrate": [
        "检查算力板连接是否松动",
        "检查矿池连接状态",
        "尝试重启矿机",
        "查看原始日志中的芯片错误信息",
    ],
    "low_hashrate": [
        "检查散热是否正常，温度是否过高",
        "检查芯片是否有损坏或降频",
        "尝试清理灰尘改善散热",
        "对比同型号矿机算力",
    ],
    "high_temperature": [
        "清理灰尘和散热片",
        "检查风扇是否正常运转",
        "改善机柜通风环境",
        "考虑降低算力板频率",
    ],
    "hw_errors": [
        "检查算力板供电是否稳定",
        "尝试重启矿机",
        "如持续增长考虑更换算力板",
        "检查电源线连接",
    ],
    "pool_issue": [
        "检查网络连接",
        "确认矿池地址和端口正确",
        "尝试切换备用矿池",
        "联系矿池客服确认服务状态",
    ],
    "offline": [
        "检查网络连接和交换机",
        "检查矿机电源",
        "尝试 ping 矿机 IP",
        "检查矿机是否开机",
    ],
    "normal": ["矿机运行正常"],
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


def diagnose_miner(snapshot, db) -> Dict[str, Any]:
    """
    诊断矿机故障
    优先使用 AI 模型，模型不存在时使用规则引擎
    """
    model_path = Path("data/models/fault_classifier.onnx")
    
    if model_path.exists():
        try:
            return _onnx_diagnose(snapshot, str(model_path))
        except Exception as e:
            print(f"[AI_DIAGNOSER] 模型推理失败: {e}，回退到规则引擎")
    
    return _rule_based_diagnose(snapshot)


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
    fault_classes = [
        "normal", "zero_hashrate", "low_hashrate",
        "high_temperature", "hw_errors", "pool_issue", "offline"
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
