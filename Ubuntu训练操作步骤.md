# Ubuntu 24.04 矿机故障模型训练 - 详细操作步骤

本文档针对已在 Windows/微型 PC 上导出的训练数据 `miner_training_data_20260218_230309.csv`，在 **Ubuntu 24.04** 上从零完成环境准备、训练与模型导出。

---

## 一、把训练数据和代码放到 Ubuntu 上

任选一种方式，让 Ubuntu 上既有 **训练数据 CSV**，又有 **miner_ai_training 项目**。

### 方式 A：U 盘拷贝（推荐）

1. 在 Windows/微型 PC 上：
   - 把 `Miner_system/data/training_exports/miner_training_data_20260218_230309.csv` 拷到 U 盘；
   - 把整个 `Miner_system` 文件夹（或至少 `miner_ai_training` 文件夹）拷到 U 盘。
2. U 盘插到 Ubuntu 主机，挂载后（例如在 `/media/你的用户名/XXX`）：
   - 把 `miner_training_data_20260218_230309.csv` 拷贝到 Ubuntu 上你准备放项目的目录（见下面「项目目录」）；
   - 把 `miner_ai_training` 文件夹也拷贝到同一父目录（若拷的是整个 Miner_system，则已有 `miner_ai_training`）。

### 方式 B：网络共享 / scp

若 Windows/微型 PC 与 Ubuntu 能互通：

```bash
# 在 Ubuntu 上执行（按你的实际 IP 和路径改）
scp 用户名@微型PC的IP:/path/to/Miner_system/data/training_exports/miner_training_data_20260218_230309.csv ./
scp -r 用户名@微型PC的IP:/path/to/Miner_system/miner_ai_training ./
```

---

## 二、在 Ubuntu 上准备项目目录

在 Ubuntu 上打开终端，假设把项目放在家目录下：

```bash
# 创建目录
mkdir -p ~/miner_ai_training/data/raw
mkdir -p ~/miner_ai_training/data/processed
mkdir -p ~/miner_ai_training/models
```

若你是直接把 Windows 上的 **整个 Miner_system** 拷到 Ubuntu，则项目路径可能类似：

- `/home/你的用户名/Miner_system/miner_ai_training/`

此时 `data/raw`、`data/processed`、`models` 一般已在 `miner_ai_training` 下或需自己建（见上两行）。

**把 CSV 放进 raw：**

```bash
# 若 CSV 在 U 盘（挂载点为 /media/你的用户名/XXX）
cp /media/你的用户名/XXX/miner_training_data_20260218_230309.csv ~/miner_ai_training/data/raw/

# 若 CSV 已在当前目录
cp miner_training_data_20260218_230309.csv ~/miner_ai_training/data/raw/

# 若你的 miner_ai_training 在别处（例如 /home/user/Miner_system/miner_ai_training）
cp /path/to/miner_training_data_20260218_230309.csv /home/user/Miner_system/miner_ai_training/data/raw/
```

确认一下：

```bash
ls ~/miner_ai_training/data/raw/
# 应能看到 miner_training_data_20260218_230309.csv
```

---

## 三、安装 Python 与依赖（Ubuntu 24.04）

Ubuntu 24.04 一般自带 Python 3.12，建议在项目目录下用虚拟环境，避免影响系统。

```bash
cd ~/miner_ai_training
# 若你的 miner_ai_training 在 Miner_system 下，则：
# cd ~/Miner_system/miner_ai_training

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 升级 pip
pip install --upgrade pip

# 安装依赖
pip install -r requirements.txt
```

依赖包括：`pandas`、`numpy`、`scikit-learn`、`joblib`、`skl2onnx`、`onnx`。若某一步报错，把终端完整报错贴出来再排查。

---

## 四、一键完成：预处理 + 训练 + 导出 ONNX

在 **miner_ai_training** 目录下、且已 **激活 venv** 的情况下执行：

```bash
cd ~/miner_ai_training
source venv/bin/activate

chmod +x scripts/trigger_retrain.sh
./scripts/trigger_retrain.sh
```

脚本会自动：

1. 在 `data/raw/` 或 `data/processed/` 中找**最新的 CSV**（会包含你刚放的 `miner_training_data_20260218_230309.csv`）；
2. 做预处理（清洗、标签推断、特征工程，理论算力 141 TH/s）；
3. 用 RandomForest 训练分类模型；
4. 把模型导出为 `models/fault_classifier.onnx`。

若成功，最后会提示“模型已保存到 models/”，并提醒把 `fault_classifier.onnx` 拷回微型 PC。

---

## 五、分步执行（可选）

若你想逐步执行或某一步失败需要重跑，可以按下面来。

### 5.1 预处理

```bash
cd ~/miner_ai_training
source venv/bin/activate

python -m src.preprocess data/raw/miner_training_data_20260218_230309.csv -o data/processed --theoretical-hashrate 141.0
```

会生成：`data/processed/miner_training_data_20260218_230309.csv`（带 `label`、`hashrate_ratio`、`model_encoded` 等）。

### 5.2 训练

```bash
python train.py --data data/processed/miner_training_data_20260218_230309.csv --output models --model sklearn
```

会在 `models/` 下生成 `fault_classifier.joblib`。

### 5.3 导出 ONNX

```bash
python export_onnx.py --input models/fault_classifier.joblib --output models/fault_classifier.onnx --n-features 9
```

得到 `models/fault_classifier.onnx`，用于在微型 PC 上做 AI 诊断推理。

---

## 六、把训练好的模型部署回微型 PC

在 Ubuntu 上得到 `models/fault_classifier.onnx` 后，需放到微型 PC 的 Miner_system 里。

### 方式 1：U 盘

```bash
cp ~/miner_ai_training/models/fault_classifier.onnx /media/你的用户名/U盘名称/
```

然后在 Windows/微型 PC 上，把 U 盘里的 `fault_classifier.onnx` 复制到：

- `Miner_system/data/models/`

（若没有 `data/models` 目录，先建一个。）

### 方式 2：scp（Ubuntu 传到微型 PC）

```bash
scp ~/miner_ai_training/models/fault_classifier.onnx 用户名@微型PC的IP:/path/to/Miner_system/data/models/
```

### 方式 3：使用项目自带的部署脚本（在 Miner_system 根目录）

若 Ubuntu 上也有完整 Miner_system 且能 SSH 到微型 PC：

```bash
cd ~/Miner_system
chmod +x scripts/deploy_model.sh
./scripts/deploy_model.sh 用户名@微型PC的IP:/home/user/Miner_system
```

（把路径改成微型 PC 上真实的 Miner_system 路径。）

---

## 七、简要检查清单

| 步骤 | 说明 |
|------|------|
| 1 | CSV 已在 `miner_ai_training/data/raw/`，且名为 `miner_training_data_20260218_230309.csv` |
| 2 | `cd miner_ai_training` 且 `source venv/bin/activate` |
| 3 | `pip install -r requirements.txt` 无报错 |
| 4 | `./scripts/trigger_retrain.sh` 运行结束无报错 |
| 5 | 存在 `models/fault_classifier.onnx` |
| 6 | 将 `fault_classifier.onnx` 拷贝到微型 PC 的 `Miner_system/data/models/` |

---

## 八、常见报错

- **“未找到数据文件”**：确认 `data/raw/miner_training_data_20260218_230309.csv` 存在，且路径、文件名正确。
- **“数据中缺少标签列”**：先跑一遍预处理（第四步或 5.1），用生成的 `data/processed/*.csv` 再训练。
- **“No module named 'skl2onnx'”**：在 venv 里执行 `pip install skl2onnx onnx`。
- **“n_features” 相关错误**：当前特征数为 9，导出时保持 `--n-features 9` 即可。

按上述顺序在 Ubuntu 24.04 上执行即可完成从「只有 CSV」到「得到 fault_classifier.onnx 并部署回微型 PC」的全流程。
