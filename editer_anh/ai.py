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

import json
import os
import sys
import time
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


# Hai nhóm nhãn: bối cảnh/ánh sáng và nội dung — xếp hạng riêng từng nhóm
SCENE_KEYS = {"trong_nha", "ngoai_troi", "ban_dem", "hoang_hon", "den_vang", "nang_gat", "bong_ram",
              "nguoc_sang", "nha_tho", "bai_bien", "cay_xanh"}
CONTENT_KEYS = {"le_cuoi", "tiec", "chan_dung", "nhom_nguoi", "vay_trang", "chi_tiet", "san_khau"}


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


# ------------------------------------------------------------------ chạy mô hình (CPU / card đồ hoạ)
GPU_TRYING = Path.home() / "Editer" / "gpu-dang-thu.txt"   # có file này = đang thử card
GPU_OFF = Path.home() / "Editer" / "gpu-tat.txt"           # có file này = không dùng card nữa
_gpu_notice = [False]


def _gpu_providers():
    """Card đồ hoạ: DirectML (mọi card trên Windows: NVIDIA/AMD/Intel) hoặc CUDA.

    An toàn: trước khi thử card, tool đặt file "đang thử"; thử xong thì xoá. Nếu lần chạy
    trước bị SẬP giữa chừng (driver/thư viện lỗi làm cửa sổ tắt ngay), file còn đó ->
    lần này tự chuyển sang CPU và nhớ luôn (file gpu-tat.txt), không sập lại nữa."""
    if os.environ.get("EDITER_CPU"):
        return None
    if GPU_TRYING.exists():
        try:
            GPU_TRYING.replace(GPU_OFF)
        except OSError:
            pass
    if GPU_OFF.exists():
        if not _gpu_notice[0]:
            _gpu_notice[0] = True
            print("(Lần trước chương trình bị dừng đột ngột lúc thử chạy AI trên card đồ hoạ, nên giờ AI "
                  f"chạy bằng CPU — kết quả như nhau, chỉ chậm hơn. Muốn thử lại card: xoá file {GPU_OFF})",
                  flush=True)
        return None
    import onnxruntime as ort
    avail = ort.get_available_providers()
    if "DmlExecutionProvider" in avail:
        return [("DmlExecutionProvider", {"device_id": int(os.environ.get("EDITER_GPU_ID", "0"))}),
                "CPUExecutionProvider"]
    if "CUDAExecutionProvider" in avail:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return None


class _Model:
    """Một mô hình ONNX. Có card đồ hoạ thì thử cả card lẫn CPU, ĐO TỐC ĐỘ thật,
    rồi chọn cái nhanh hơn (card yếu / máy ảo đôi khi còn chậm hơn CPU).
    Kết quả như nhau, chỉ khác tốc độ."""

    def __init__(self, folder: Path, name: str, shape: tuple):
        import onnxruntime as ort
        int8, fp16 = folder / f"{name}.onnx", folder / f"{name}.fp16.onnx"
        cands = []
        gpu = _gpu_providers()
        if gpu:
            cands.append(("card đồ hoạ", fp16 if fp16.exists() else int8, gpu))
        cands.append(("CPU", int8 if int8.exists() else fp16, ["CPUExecutionProvider"]))
        best, errors = None, []
        from . import nhatky
        for label, path, provs in cands:
            if not path.exists():
                continue
            gpu_try = label != "CPU"
            nhatky.note(f"khởi động mô hình {name} trên {label} ({path.name})")
            try:
                if gpu_try:
                    GPU_TRYING.parent.mkdir(parents=True, exist_ok=True)
                    GPU_TRYING.write_text(f"{name}\n", encoding="utf-8")
                opts = ort.SessionOptions()
                opts.log_severity_level = 3
                if gpu_try:   # DirectML cần 2 tuỳ chọn này
                    opts.enable_mem_pattern = False
                    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess = ort.InferenceSession(str(path), opts, providers=provs)
                if gpu_try and sess.get_providers()[0] == "CPUExecutionProvider":
                    # onnxruntime không dùng được card (vd máy không có card thật) và tự lùi về CPU
                    raise RuntimeError("không dùng được card đồ hoạ, đã tự chuyển sang CPU")
                sec = self._bench(sess, shape)
            except Exception as e:
                errors.append(f"{label}: {e}")
                nhatky.note(f"  {label} lỗi: {e}")
                continue
            finally:
                if gpu_try:
                    try:
                        GPU_TRYING.unlink()
                    except OSError:
                        pass
            nhatky.note(f"  {label}: {sec * 1000:.0f} ms/lần")
            if best is None or sec < best[2]:
                best = (label, sess, sec)
        if best is None:
            raise RuntimeError("; ".join(errors) or f"không thấy {name}.onnx")
        self.device, self.sess, self.sec = best
        inp = self.sess.get_inputs()[0]
        self.input = inp.name
        self.half = "float16" in inp.type
        self.outputs = [o.name for o in self.sess.get_outputs()]

    @staticmethod
    def _bench(sess, shape) -> float:
        inp = sess.get_inputs()[0]
        x = np.zeros(shape, np.float16 if "float16" in inp.type else np.float32)
        sess.run(None, {inp.name: x})            # lần đầu: khởi động
        t = time.perf_counter()
        sess.run(None, {inp.name: x})
        return time.perf_counter() - t

    def run(self, x: np.ndarray, output: str | None = None) -> np.ndarray:
        x = x.astype(np.float16 if self.half else np.float32)
        out = self.sess.run([output or self.outputs[0]], {self.input: x})[0]
        return out.astype(np.float32)


class Embedder:
    _inst: "Embedder | None" = None

    def __init__(self, folder: Path):
        self.model = _Model(folder, MODEL_NAME[:-5], (1, 3, SIZE, SIZE))
        self.device = self.model.device
        self.output = "image_embeds" if "image_embeds" in self.model.outputs else self.model.outputs[0]
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
            x = np.stack([self._prep(im) for im in images[i:i + batch]])
            e = self.model.run(x, self.output)
            out.append(e.reshape(len(x), -1))
        e = np.concatenate(out, axis=0).astype(np.float32)
        return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9)

    def label_scores(self, emb: np.ndarray) -> np.ndarray:
        """Độ giống với từng nhãn cảnh (cosine), N x số nhãn."""
        return emb @ self.label_emb.T

    def top_labels(self, emb: np.ndarray, k: int = 1) -> list[str]:
        """Nhãn tốt nhất trong từng nhóm: [bối cảnh/ánh sáng..., nội dung...]."""
        names = {key: vi for key, vi, _ in LABELS}
        sc = self.label_scores(emb[None, :])[0]
        out = []
        for group in (SCENE_KEYS, CONTENT_KEYS):
            idx = [i for i, key in enumerate(self.label_keys) if key in group]
            idx.sort(key=lambda i: -sc[i])
            out += [names.get(self.label_keys[i], self.label_keys[i]) for i in idx[:k]]
        return out


# ------------------------------------------------------------------ phân vùng ảnh
SEG_SCENE = "seg-canh"     # SegFormer-B0 ADE20K: trời, cây, nước, người... (150 loại)
SEG_PEOPLE = "seg-nguoi"   # SegFormer-B2 người: mặt, tay, chân, tóc, váy, áo... (18 loại)
SEG_LABELS = "seg-nhan.json"
SEG_SIZE = 512
_IN_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_IN_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _norm_name(n: str) -> str:
    return n.strip().lower().split(",")[0].strip()


class Segmenter:
    """Tìm vùng trong ảnh: da, tóc, quần áo (váy cưới), trời, cây, nước, người.
    Trả về bản đồ mã vùng (look.SKIN, look.SKY...) cùng kích thước ảnh."""
    _inst: "Segmenter | None" = None
    _tried = False

    def __init__(self, folder: Path):
        from . import look as L
        names = json.loads((folder / SEG_LABELS).read_text(encoding="utf-8"))
        shape = (1, 3, SEG_SIZE, SEG_SIZE)
        self.scene = _Model(folder, SEG_SCENE, shape) if (folder / f"{SEG_SCENE}.onnx").exists() else None
        self.people = None
        if (folder / f"{SEG_PEOPLE}.onnx").exists() or (folder / f"{SEG_PEOPLE}.fp16.onnx").exists():
            self.people = _Model(folder, SEG_PEOPLE, shape)
        if self.scene is None and self.people is None:
            raise RuntimeError("không có mô hình phân vùng")
        scene_map = {"sky": L.SKY, "tree": L.PLANT, "grass": L.PLANT, "plant": L.PLANT, "palm": L.PLANT,
                     "field": L.PLANT, "water": L.WATER, "sea": L.WATER, "river": L.WATER, "lake": L.WATER,
                     "swimming pool": L.WATER, "waterfall": L.WATER, "person": L.PERSON}
        self.scene_codes = np.array([scene_map.get(_norm_name(n), L.OTHER) for n in names.get("canh", [])], np.uint8)
        skin = {"face", "left-leg", "right-leg", "left-arm", "right-arm"}
        people_codes = []
        for n in names.get("nguoi", []):
            n = _norm_name(n)
            people_codes.append(L.OTHER if n in ("background", "sunglasses") else
                                L.SKIN if n in skin else L.HAIR if n == "hair" else L.CLOTHES)
        self.people_codes = np.array(people_codes, np.uint8)
        self.codes = L
        self.sec = sum(m.sec for m in (self.scene, self.people) if m)
        self.device = " + ".join(sorted({m.device for m in (self.scene, self.people) if m}))

    @classmethod
    def get(cls) -> "Segmenter | None":
        if cls._inst is None and not cls._tried:
            cls._tried = True
            if os.environ.get("EDITER_KHONG_AI"):
                return None
            for d in model_dirs():
                if (d / SEG_LABELS).exists():
                    try:
                        cls._inst = Segmenter(d)
                    except Exception as e:
                        print(f"(Không bật được AI phân vùng: {e})", flush=True)
                    break
        return cls._inst

    @property
    def fast(self) -> bool:
        """Đủ nhanh để phân vùng MỌI ảnh trong job (thường là khi chạy bằng card đồ hoạ)."""
        return self.sec < 0.15

    @staticmethod
    def _prep(rgb_u8: np.ndarray) -> np.ndarray:
        im = Image.fromarray(rgb_u8).resize((SEG_SIZE, SEG_SIZE), Image.BILINEAR)
        a = np.asarray(im, np.float32) / 255.0
        return ((a - _IN_MEAN) / _IN_STD).transpose(2, 0, 1)[None]

    def _labels(self, model: _Model, x: np.ndarray, h: int, w: int) -> np.ndarray:
        logits = model.run(x)[0]                       # C x 128 x 128
        lab = np.argmax(logits, axis=0).astype(np.uint8)
        return np.asarray(Image.fromarray(lab).resize((w, h), Image.NEAREST))

    def segment(self, rgb_u8: np.ndarray) -> np.ndarray:
        L = self.codes
        h, w = rgb_u8.shape[:2]
        x = self._prep(rgb_u8)
        out = np.full((h, w), L.OTHER, np.uint8)
        person = None
        if self.scene is not None:
            sc = self.scene_codes[np.minimum(self._labels(self.scene, x, h, w), len(self.scene_codes) - 1)]
            out[:] = sc
            person = sc == L.PERSON
        if self.people is not None:
            pc = self.people_codes[np.minimum(self._labels(self.people, x, h, w), len(self.people_codes) - 1)]
            m = pc != L.OTHER
            if person is not None:   # chỉ tin bộ phận người ở chỗ mô hình cảnh cũng thấy có người
                m &= person
            out[m] = pc[m]
        return out
