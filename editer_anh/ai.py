"""AI "HIỂU ẢNH" CHẠY TRÊN MÁY — không cần mạng, không cần đăng nhập.

Dùng phần nhìn của mô hình CLIP (bản rút gọn ~90MB, định dạng ONNX). Mỗi ảnh
xem trước được biến thành một "dấu vân tay nội dung" 512 số: ảnh giống nhau
về nội dung (cùng nhà thờ, cùng tiệc đèn vàng, cùng chân dung ngược sáng...)
có dấu vân tay gần nhau.

Dùng vào ba việc:
  1. Thêm vào đầu vào của mô hình học phong cách -> học được quy luật kiểu
     "cảnh tiệc đèn vàng thì bạn để ấm hơn cảnh làm lễ".
  2. Tìm các ảnh bạn TỪNG chỉnh giống tấm đang xét nhất (hàng xóm gần nhất).
  3. Gắn nhãn cảnh (trong nhà, ngoài trời, tiệc, chân dung...) và chia cảnh.

Nhãn cảnh: điểm giống giữa ảnh và các câu mô tả (tính sẵn lúc đóng gói, lưu
trong labels.npz), nên lúc chạy chỉ cần phần nhìn của mô hình.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

MODEL_NAME = "clip-vision.onnx"
LABELS_NAME = "clip-labels.npz"
SIZE = 224
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

# (mã, tên tiếng Việt, câu mô tả cho CLIP)
LABELS = [
    ("trong_nha", "trong nhà", "a photo taken indoors"),
    ("ngoai_troi", "ngoài trời", "a photo taken outdoors"),
    ("ban_dem", "ban đêm", "a photo taken outdoors at night"),
    ("hoang_hon", "hoàng hôn", "a photo at sunset with golden light"),
    ("den_vang", "đèn vàng", "a photo lit by warm yellow tungsten light"),
    ("nang_gat", "nắng gắt", "a photo in harsh bright midday sunlight"),
    ("bong_ram", "bóng râm", "a photo taken in the shade on a cloudy day"),
    ("nguoc_sang", "ngược sáng", "a backlit photo with the sun behind the people"),
    ("le_cuoi", "làm lễ", "a wedding ceremony"),
    ("tiec", "tiệc", "a wedding reception party in a restaurant"),
    ("nha_tho", "nhà thờ", "a photo inside a church"),
    ("bai_bien", "biển", "a photo at the beach by the sea"),
    ("cay_xanh", "cây xanh", "a photo in a park with green trees and grass"),
    ("chan_dung", "chân dung", "a close-up portrait of a person"),
    ("nhom_nguoi", "ảnh nhóm", "a group photo of many people"),
    ("vay_trang", "váy cưới trắng", "a bride in a white wedding dress"),
    ("chi_tiet", "chi tiết", "a detail photo of rings, flowers or decorations"),
    ("san_khau", "sân khấu", "a stage with colorful lights at an event"),
]


def model_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("EDITER_MODELS"):
        dirs.append(Path(os.environ["EDITER_MODELS"]))
    if getattr(sys, "frozen", False):                 # bản .exe
        dirs.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "models")
        dirs.append(Path(sys.executable).parent / "models")
    dirs.append(Path(__file__).resolve().parents[1] / "models")   # chạy từ mã nguồn
    dirs.append(Path.home() / "Editer" / "models")
    return dirs


class Embedder:
    _inst: "Embedder | None" = None

    def __init__(self, folder: Path):
        import onnxruntime as ort
        providers = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider")
                     if p in ort.get_available_providers()]
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(str(folder / MODEL_NAME), opts, providers=providers)
        self.device = self.sess.get_providers()[0].replace("ExecutionProvider", "")
        self.input = self.sess.get_inputs()[0].name
        outs = [o.name for o in self.sess.get_outputs()]
        self.output = "image_embeds" if "image_embeds" in outs else outs[0]
        lab = np.load(folder / LABELS_NAME)
        self.label_keys = [str(k) for k in lab["keys"]]
        self.label_emb = lab["emb"].astype(np.float32)
        self.label_emb /= np.linalg.norm(self.label_emb, axis=1, keepdims=True)

    @classmethod
    def get(cls) -> "Embedder | None":
        """Trả về bộ AI nếu có mô hình + thư viện, không có thì None (tool vẫn chạy)."""
        if cls._inst is None:
            if os.environ.get("EDITER_KHONG_AI"):
                return None
            for d in model_dirs():
                if (d / MODEL_NAME).exists() and (d / LABELS_NAME).exists():
                    try:
                        cls._inst = Embedder(d)
                    except Exception as e:  # thiếu onnxruntime, file hỏng...
                        print(f"(Không bật được AI: {e})", flush=True)
                        return None
                    break
        return cls._inst

    @staticmethod
    def _prep(rgb_u8: np.ndarray) -> np.ndarray:
        im = Image.fromarray(rgb_u8)
        w, h = im.size
        s = SIZE / min(w, h)
        im = im.resize((max(SIZE, round(w * s)), max(SIZE, round(h * s))), Image.BICUBIC)
        w, h = im.size
        l, t = (w - SIZE) // 2, (h - SIZE) // 2
        a = np.asarray(im.crop((l, t, l + SIZE, t + SIZE)), dtype=np.float32) / 255.0
        return ((a - _MEAN) / _STD).transpose(2, 0, 1)

    def embed(self, images: list[np.ndarray], batch: int = 16) -> np.ndarray:
        out = []
        for i in range(0, len(images), batch):
            x = np.stack([self._prep(im) for im in images[i:i + batch]]).astype(np.float32)
            e = self.sess.run([self.output], {self.input: x})[0]
            out.append(e.reshape(len(x), -1))
        e = np.concatenate(out, axis=0).astype(np.float32)
        return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9)

    def label_scores(self, emb: np.ndarray) -> np.ndarray:
        """Độ giống với từng nhãn cảnh (cosine), N x số nhãn."""
        return emb @ self.label_emb.T

    def top_labels(self, emb: np.ndarray, k: int = 2) -> list[str]:
        names = {key: vi for key, vi, _ in LABELS}
        sc = self.label_scores(emb[None, :])[0] * 100
        p = np.exp(sc - sc.max())
        p /= p.sum()
        idx = np.argsort(-p)[:k]
        return [names.get(self.label_keys[i], self.label_keys[i]) for i in idx]
