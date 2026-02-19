# AI 矿机故障诊断系统 - 操作指南

本文档分别说明微型 PC 端和 Ubuntu 训练主机端需要执行的操作步骤。

---

## 一、微型 PC 端操作

> 说明：微型 PC 运行 Miner_system 矿机管理系统，负责数据采集、导出和 AI 推理。

### 1. 日常运行（无需手动）

系统启动后会自动执行：

- **定时数据采集**：每 5 分钟采集一次矿机快照（写入 `miner_raw_snapshots`）
- **快照清理**：每次采集前删除超过 30 天的历史快照

### 2. 手动触发采集（可选）

在 Web 管理界面点击「立即采集」，或调用 API：

```bash
POST /api/collect-now
```

### 3. 人工标注故障（可选但推荐）

在矿机详情页对异常矿机进行标注：

1. 进入矿机详情页
2. 点击「标注故障」
3. 选择快照、故障类型、原因、处理方案
4. 提交

标注数据会作为训练标签，提高模型效果。

**故障类型请选「根本原因」**：标注时尽量选择具体根因（如「风扇故障」「算力板/ASIC 未检测」「供电异常」等），而不是只选「0算力/低算力」。这样模型才能学习到根因，诊断结果才会具体化。未区分出根因时再选「0算力」或「低算力」。

**待标记清单规则**：仅显示**故障快照**（算力 &lt;50 TH/s 或无算力）；同一矿机**每 5 分钟**只显示一条，避免重复；**按日期倒序**（当天在最上）。只排除**已标注过的快照**，不按日整日排除，因此当天新产生的故障快照仍会出现在清单中；采集时 5 分钟内不重复写入故障快照。

### 4. 导出训练数据（定期执行，如每周/每月）

在 Miner_system 项目根目录执行：

```bash
# 导出最近 30 天数据（默认）
python scripts/export_training_data.py

# 指定天数
python scripts/export_training_data.py --days 14

# 指定输出目录
python scripts/export_training_data.py --output data/training_exports

# 只导出某类故障
python scripts/export_training_data.py --fault-type low_hashrate
```

**导出文件位置**：`data/training_exports/miner_training_data_YYYYMMDD_HHMMSS.csv`

### 5. 将数据传到 Ubuntu（U 盘 / 网络）

任选一种方式：

- **U 盘**：把 `data/training_exports/*.csv` 拷贝到 U 盘，再插到 Ubuntu 主机
- **网络共享**：通过 SMB / NFS 把该目录共享给 Ubuntu
- **scp**（如已配置 SSH）：
  ```bash
  scp data/training_exports/*.csv user@ubuntu-server:/path/to/miner_ai_training/data/raw/
  ```

### 6. 接收新模型（训练完成后）

训练完成后，把 Ubuntu 上的 `models/fault_classifier.onnx` 拷贝回微型 PC：

- **U 盘**：从 Ubuntu 拷贝到 U 盘，再拷贝到微型 PC 的 `Miner_system/data/models/`
- **scp**（Ubuntu 端执行，需有 SSH 到微型 PC 的权限）：
  ```bash
  scp models/fault_classifier.onnx user@minipc:/path/to/Miner_system/data/models/
  ```

### 7. 使用 AI 诊断

- 在矿机详情页点击「AI诊断」，查看故障类型、置信度和建议
- 诊断会结合**运行状态、硬件性能与运行日志**：除模型结果外，会展示「运行日志提示」（从该矿机最近日志中提取的关键词方向）和「历史标注参考」（您曾填写的同类型故障原因与方案）
- 用**根因类型**重新训练并部署新 ONNX 后，诊断输出的故障类型才会变为具体根因（如风扇故障、ASIC 未检测等）；否则仍为旧 8 类（如 zero_hashrate）
- 如有误判，可反馈正确/错误，用于后续训练

### 8. 原始日志（矿机后台运行日志）

在矿机详情页进入「原始日志」可查看与矿机 Web 后台一致的运行日志，便于 AI 分析故障。系统会依次尝试以下接口：

| 固件类型 | 接口 | 说明 |
|---------|------|------|
| 衍生固件 (fix-freq / Luxor / BOSminer) | JSON-RPC `logs` 命令 (4028) | 与后台显示的运行日志一致 |
| Luxor LUXminer | `http://IP:8080/log/live` | 无需认证 |
| 原厂固件 (Release Fri Feb 7...) | `get_kernel_log.cgi` (80) | 需 config.py 中 root/root |
| Braiins OS | `/api/v1/miner/errors` | 仅错误记录，无完整日志 |

若仍无法获取，请确认 `config.py` 中 `MINER_CREDENTIALS` 与矿机实际账号密码一致。

---

## 二、Ubuntu 训练主机端操作

> 说明：Ubuntu 主机负责数据预处理、模型训练和 ONNX 导出。

### 1. 准备环境

```bash
cd miner_ai_training
pip install -r requirements.txt
```

所需依赖：`pandas`, `scikit-learn`, `joblib`, `skl2onnx`, `onnx` 等。

### 2. 导入训练数据

把微型 PC 传来的 CSV 放入 `data/raw/`：

```bash
# 若用 U 盘，假设挂载在 /media/usb
cp /media/usb/miner_training_data_*.csv miner_ai_training/data/raw/

# 或从网络共享拷贝
cp /mnt/share/miner_training_data_*.csv miner_ai_training/data/raw/
```

### 3. 执行完整重训练（一键）

在 `miner_ai_training` 目录下执行：

```bash
cd miner_ai_training
chmod +x scripts/trigger_retrain.sh
./scripts/trigger_retrain.sh
```

脚本会自动完成：

1. 查找 `data/raw/` 或 `data/processed/` 中最新的 CSV
2. 预处理（清洗、标注、特征工程）
3. 训练 RandomForest 分类模型
4. 导出 ONNX 模型到 `models/fault_classifier.onnx`

### 4. 分步执行（可选）

如需单独控制每一步：

```bash
# 1. 预处理
python -m src.preprocess data/raw/miner_training_data_20250101_120000.csv -o data/processed --theoretical-hashrate 141.0

# 2. 训练
python train.py --data data/processed/miner_training_data_20250101_120000.csv --output models --model sklearn

# 3. 导出 ONNX
python export_onnx.py --input models/fault_classifier.joblib --output models/fault_classifier.onnx --n-features 9
```

### 5. 部署模型到微型 PC

在 **Miner_system 项目根目录**（与 miner_ai_training 同级）执行：

```bash
# 方式 A：远程部署（需 SSH 到微型 PC）
chmod +x scripts/deploy_model.sh
./scripts/deploy_model.sh user@minipc-ip:/home/user/Miner_system

# 方式 B：本地部署（同一台机器或已挂载）
./scripts/deploy_model.sh /path/to/Miner_system

# 方式 C：手动拷贝
cp miner_ai_training/models/fault_classifier.onnx /media/usb/
# 然后在微型 PC 上将 U 盘中的文件复制到 Miner_system/data/models/
```

---

## 三、完整流程示意

```
微型 PC                           Ubuntu 训练主机
    │                                    │
    │  1. 运行采集（自动/手动）              │
    │  2. 人工标注（可选）                  │
    │  3. 导出 CSV                        │
    │  ────────────→  U盘/网络  ─────────→  4. 拷贝到 data/raw/
    │                                      │  5. 运行 trigger_retrain.sh
    │                                      │  6. 生成 fault_classifier.onnx
    │  ←───────────  U盘/scp  ←──────────  7. 部署到微型 PC
    │  8. 将 .onnx 放入 data/models/       │
    │  9. 使用 AI 诊断                     │
```

---

## 四、数据量建议

| 阶段         | 建议数据量                          |
|--------------|-------------------------------------|
| 最少可用     | 每类故障至少 50–100 条标注样本      |
| 推荐         | 正常 500+，每类故障 100+，总计 1000+ |
| 采集周期建议 | 连续采集 2–4 周再开始训练           |

---

## 五、常见问题

**Q：为什么诊断结果还是「0算力/低算力」而不是具体根因？**  
A：当前部署的 ONNX 若是用旧 8 类训练的，输出只会是那 8 类。请在前端标注时尽量选择**根本原因**（风扇故障、算力板未检测等），导出数据后在 Ubuntu 用新版本训练脚本重新训练并部署新模型，诊断结果就会变为根因分类。同时可参考诊断页的「运行日志提示」和「历史标注参考」辅助排查。

**Q：没有 GPU，训练会很慢吗？**  
A：使用 RandomForest（sklearn），CPU 即可，一般几分钟内完成。

**Q：微型 PC 未装 onnxruntime 会怎样？**  
A：AI 诊断会 fallback 到规则引擎，仍可工作，但无模型推理。安装：`pip install onnxruntime`。

**Q：trigger_retrain.sh 报「未找到数据文件」？**  
A：确认已将 CSV 放到 `miner_ai_training/data/raw/` 目录。

**Q：deploy_model.sh 的路径怎么写？**  
A：`user@minipc` 为 Ubuntu 登录微型 PC 的用户名和主机名/IP，`/path/to/Miner_system` 为 Miner_system 在微型 PC 上的实际路径。

**Q：故障标记清单的规则是什么？晚上查不到新故障怎么办？**  
A：当前逻辑如下：  
- **只展示故障快照**：算力低于 50 TH/s 或无算力的矿机快照才会进入待标记清单（与 config.THRESHOLDS 一致）。  
- **只排除已标注过的快照**：不按日整日排除，因此早上标过部分后，**当天后续新产生的故障快照仍会出现在清单中**，晚上可继续查看并标记。  
- **5 分钟内不重复**：同一矿机在同一 5 分钟时间窗内只保留一条代表快照；采集时 5 分钟内不重复写入故障快照。  
- **按日期排序**：清单按日期倒序，**当天排在最上方**，依次为昨天、前天等。

**Q：AI 诊断显示「零算力」和几条通用建议，但实际是风扇故障导致无法启动，为什么和预期差很多？**  
A：主要有三方面原因：  
1. **模型按「症状」分类，不按「根因」**：当前训练的故障类型是 normal、zero_hashrate、low_hashrate、高温、硬件错误、矿池、离线等，都是**表现症状**。风扇故障导致无法启动时，矿机也是 0 算力，特征和「矿池断连 0 算力」「算力板故障 0 算力」很像，模型没有「风扇故障」这一类，只能判成 zero_hashrate。  
2. **建议是固定文案**：诊断结果里的「建议」来自代码里按故障类型写死的列表，不是模型生成的。所以 zero_hashrate 一直显示那几条（现已增加「检查风扇是否故障」等）。  
3. **标注决定模型能学什么**：如果标注时只选了「零算力」而没有区分为「风扇导致」「矿池导致」等，模型无法学到根因。  
**改进方向**：  
- 标注时尽量区分根因：同是 0 算力，若是风扇问题可标为「其他」并在原因里写「风扇故障」；后续若增加「风扇故障」类型，可专门标这类再训练。  
- 系统已为零算力建议中增加风扇相关提示；若需「风扇故障」单独类型与建议，需在训练链路中新增该类别并重新训练。
