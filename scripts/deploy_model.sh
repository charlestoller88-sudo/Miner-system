#!/bin/bash
# 将训练好的模型从 Ubuntu 复制到微型 PC
# 用法: ./deploy_model.sh user@minipc:/path/to/Miner_system
# 或手动复制: scp miner_ai_training/models/fault_classifier.onnx user@minipc:Miner_system/data/models/

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_SRC="${SCRIPT_DIR}/../miner_ai_training/models/fault_classifier.onnx"
DEST="${1:-.}"

if [ ! -f "$MODEL_SRC" ]; then
    echo "模型文件不存在: $MODEL_SRC"
    echo "请先运行 miner_ai_training/scripts/trigger_retrain.sh"
    exit 1
fi

# 如果 DEST 包含 : 则用 scp
if [[ "$DEST" == *:* ]]; then
    echo "复制到 $DEST/data/models/"
    ssh "${DEST%%:*}" "mkdir -p ${DEST#*:}/data/models"
    scp "$MODEL_SRC" "${DEST}/data/models/fault_classifier.onnx"
else
    echo "复制到 $DEST/data/models/"
    mkdir -p "$DEST/data/models"
    cp "$MODEL_SRC" "$DEST/data/models/fault_classifier.onnx"
fi

echo "部署完成"
