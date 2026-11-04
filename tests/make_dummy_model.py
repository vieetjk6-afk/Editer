"""Tạo mô hình AI GIẢ (cùng đầu vào/đầu ra như CLIP thật) để test khi không có mạng.

Ảnh -> trung bình từng ô 32x32 -> nhân ma trận ngẫu nhiên -> vector 512.
Ảnh giống nhau vẫn ra vector gần nhau, đủ để test cả luồng.
"""
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editer_anh.ai import LABELS, LABELS_NAME, MODEL_NAME  # noqa: E402


def make(out: str) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    W = numpy_helper.from_array(rng.normal(0, 1, (147, 512)).astype(np.float32), "W")
    shape = numpy_helper.from_array(np.array([-1, 147], np.int64), "shape")
    nodes = [
        helper.make_node("AveragePool", ["pixel_values"], ["p"], kernel_shape=[32, 32], strides=[32, 32]),
        helper.make_node("Reshape", ["p", "shape"], ["f"]),
        helper.make_node("MatMul", ["f", "W"], ["image_embeds"]),
    ]
    g = helper.make_graph(nodes, "dummy",
                          [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["N", 3, 224, 224])],
                          [helper.make_tensor_value_info("image_embeds", TensorProto.FLOAT, ["N", 512])],
                          [W, shape])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, out / MODEL_NAME)
    np.savez(out / LABELS_NAME, keys=np.array([k for k, _, _ in LABELS]),
             emb=rng.normal(0, 1, (len(LABELS), 512)).astype(np.float32))


if __name__ == "__main__":
    make(sys.argv[1])
