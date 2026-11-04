"""Tạo mô hình AI GIẢ (cùng đầu vào/đầu ra như CLIP thật) để test khi không có mạng.

Ảnh -> trung bình từng ô 32x32 -> nhân ma trận ngẫu nhiên -> vector 512.
Ảnh giống nhau vẫn ra vector gần nhau, đủ để test cả luồng.
"""
import json
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editer_anh.ai import LABELS, LABELS_NAME, MODEL_NAME, SEG_LABELS  # noqa: E402

ADE = ["wall", "building", "sky", "floor", "tree", "ceiling", "road", "bed ", "windowpane", "grass",
       "cabinet", "sidewalk", "person"]
PEOPLE = ["Background", "Hat", "Hair", "Sunglasses", "Upper-clothes", "Skirt", "Pants", "Dress", "Belt",
          "Left-shoe", "Right-shoe", "Face", "Left-leg", "Right-leg", "Left-arm", "Right-arm", "Bag", "Scarf"]


def _seg_model(path: Path, weights: np.ndarray, bias: np.ndarray) -> None:
    """Ảnh 512 -> trung bình ô 4x4 -> phân loại tuyến tính theo màu -> logits C x 128 x 128."""
    C = len(bias)
    Wt = numpy_helper.from_array(weights.reshape(C, 3, 1, 1).astype(np.float32), "W")
    Bt = numpy_helper.from_array(bias.astype(np.float32), "B")
    nodes = [helper.make_node("AveragePool", ["pixel_values"], ["p"], kernel_shape=[4, 4], strides=[4, 4]),
             helper.make_node("Conv", ["p", "W", "B"], ["logits"])]
    g = helper.make_graph(nodes, "dummyseg",
                          [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["N", 3, 512, 512])],
                          [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N", C, 128, 128])],
                          [Wt, Bt])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, path)


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
    # Phân vùng giả theo màu (đầu vào đã chuẩn hoá ImageNet): xanh dương -> trời,
    # xanh lá -> cây, cam/hồng -> người (+ mặt), sáng -> váy, còn lại -> tường.
    w = np.zeros((len(ADE), 3))
    b = np.zeros(len(ADE))
    w[ADE.index("sky")] = (-2, 0, 3)
    w[ADE.index("tree")] = (-2, 3, -2)
    w[ADE.index("person")] = (2.5, 0, -1.5)
    b[ADE.index("wall")] = 0.5
    _seg_model(out / "seg-canh.onnx", w, b)
    w = np.zeros((len(PEOPLE), 3))
    b = np.zeros(len(PEOPLE))
    w[PEOPLE.index("Face")] = (2.5, 0, -1.5)
    w[PEOPLE.index("Dress")] = (1, 1, 1)
    b[PEOPLE.index("Dress")] = -2.5
    b[PEOPLE.index("Background")] = 0.3
    _seg_model(out / "seg-nguoi.onnx", w, b)
    (out / SEG_LABELS).write_text(json.dumps({"canh": ADE, "nguoi": PEOPLE}), encoding="utf-8")


if __name__ == "__main__":
    make(sys.argv[1])
