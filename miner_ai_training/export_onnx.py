"""
导出 ONNX 模型 - 用于微型 PC 轻量推理
"""
import sys
from pathlib import Path

# sklearn RandomForest 导出 ONNX 需要 skl2onnx
try:
    import skl2onnx
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType
except ImportError:
    print("请安装: pip install skl2onnx onnx")
    sys.exit(1)

import joblib


def export_joblib_to_onnx(joblib_path: str, output_path: str, n_features: int = 9):
    """将 joblib 保存的 sklearn 模型导出为 ONNX"""
    data = joblib.load(joblib_path)
    model = data["model"]
    
    initial_type = [("float_input", FloatTensorType([None, n_features]))]
    onnx_model = convert_sklearn(model, initial_types=initial_type)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(onnx_model.SerializeToString())
    
    print(f"ONNX 模型已导出: {output_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="models/fault_classifier.joblib")
    parser.add_argument("--output", default="models/fault_classifier.onnx")
    parser.add_argument("--n-features", type=int, default=9)
    args = parser.parse_args()
    
    export_joblib_to_onnx(args.input, args.output, args.n_features)
