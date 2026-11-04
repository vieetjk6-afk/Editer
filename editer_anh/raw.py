"""Đọc nhanh một file RAW: ảnh xem trước nhỏ + nhiệt độ màu "As Shot" + EXIF.

Không giải mã toàn bộ RAW (chậm): dùng ảnh JPEG xem trước nhúng sẵn trong file
(Nikon/Sony/Canon đều có), giải mã ở 1/8 kích thước. Chỉ khi file không có ảnh
xem trước mới phải giải mã RAW ở nửa kích thước.
"""
from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from . import color

RAW_EXT = {".nef", ".nrw", ".arw", ".srf", ".sr2", ".cr2", ".cr3", ".crw", ".dng",
           ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr", ".iiq"}
JPG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

PREVIEW_SIDE = 384


@dataclass
class Photo:
    path: str
    model: str | None = None
    time: float | None = None          # giây kể từ 1970 (giờ chụp)
    iso: float | None = None
    shutter: float | None = None        # giây
    fnumber: float | None = None
    focal: float | None = None
    flash: float | None = None          # 1 = có nháy flash
    as_shot_temp: float | None = None
    as_shot_tint: float | None = None
    preview: np.ndarray | None = field(default=None, repr=False)  # uint8 HxWx3 sRGB
    error: str | None = None


def list_raws(folder: str | Path, recursive: bool = True) -> list[Path]:
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in RAW_EXT)


def _to_small(img: Image.Image, side: int) -> np.ndarray:
    img = img.convert("RGB")
    img.thumbnail((side, side), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def load_image(path: str | Path, side: int = PREVIEW_SIDE) -> np.ndarray:
    """JPG/PNG/TIFF -> mảng uint8 nhỏ."""
    with Image.open(path) as im:
        if im.format == "JPEG":
            im.draft("RGB", (side * 2, side * 2))
        return _to_small(im, side)


def _as_shot(raw) -> tuple[float | None, float | None]:
    """Hệ số cân bằng trắng của máy + ma trận màu -> (K, Tint) như Camera Raw."""
    try:
        wb = np.array(raw.camera_whitebalance[:3], dtype=np.float64)
        if np.any(wb <= 0):
            return None, None
        neutral = 1.0 / wb
        neutral /= neutral.max()
        cam_xyz = np.array(raw.rgb_xyz_matrix[:3, :3], dtype=np.float64)
        if np.any(cam_xyz):
            xyz = np.linalg.solve(cam_xyz, neutral)          # bảng màu Adobe cho máy này
        else:
            rgb_cam = np.array(raw.color_matrix[:3, :3], dtype=np.float64)  # DNG: camera -> sRGB
            if not np.any(rgb_cam):
                return None, None
            xyz = color.linear_to_xyz(rgb_cam @ neutral)
        if np.any(xyz <= 0):
            return None, None
        t, tint = color.xy_to_temp_tint(*color.xyz_to_xy(xyz))
        if not (1500 < t < 50000):
            return None, None
        return t, tint
    except Exception:
        return None, None


def _ratio(v) -> float | None:
    try:
        v = v.values[0] if hasattr(v, "values") else v
        if hasattr(v, "num"):
            return v.num / v.den if v.den else None
        return float(v)
    except Exception:
        return None


def _exif(path: Path, ph: Photo) -> None:
    try:
        import exifread
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        return
    if not tags:
        return
    g = tags.get
    if g("Image Model"):
        ph.model = str(g("Image Model")).strip()
    ph.iso = _ratio(g("EXIF ISOSpeedRatings")) or ph.iso
    ph.shutter = _ratio(g("EXIF ExposureTime")) or ph.shutter
    ph.fnumber = _ratio(g("EXIF FNumber")) or ph.fnumber
    ph.focal = _ratio(g("EXIF FocalLength")) or ph.focal
    fl = _ratio(g("EXIF Flash"))
    if fl is not None:
        ph.flash = float(int(fl) & 1)
    dt = g("EXIF DateTimeOriginal") or g("Image DateTime")
    if dt:
        try:
            ph.time = datetime.strptime(str(dt)[:19], "%Y:%m:%d %H:%M:%S").timestamp()
        except ValueError:
            pass


def load(path: str | Path, side: int = PREVIEW_SIDE) -> Photo:
    path = Path(path)
    ph = Photo(path=str(path))
    try:
        import rawpy
    except ImportError as e:  # pragma: no cover
        ph.error = f"thiếu thư viện rawpy: {e}"
        return ph
    try:
        with rawpy.imread(str(path)) as raw:
            ph.as_shot_temp, ph.as_shot_tint = _as_shot(raw)
            try:
                th = raw.extract_thumb()
                if th.format == rawpy.ThumbFormat.JPEG:
                    with Image.open(io.BytesIO(th.data)) as im:
                        im.draft("RGB", (side * 2, side * 2))
                        ph.preview = _to_small(im, side)
                        if not ph.model:
                            try:
                                ex = im.getexif()
                                ph.model = ex.get(0x0110) or None
                            except Exception:
                                pass
                else:
                    ph.preview = _to_small(Image.fromarray(th.data), side)
            except Exception:
                ph.preview = None
            if ph.preview is None or min(ph.preview.shape[:2]) < 64:
                rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                                      output_bps=8, user_flip=0)
                ph.preview = _to_small(Image.fromarray(rgb), side)
    except Exception as e:
        ph.error = f"không đọc được RAW: {e}"
        return ph
    _exif(path, ph)
    if ph.time is None:
        ph.time = os.path.getmtime(path)
    return ph


def features(ph: Photo) -> dict:
    """Các con số mô tả một tấm ảnh — đầu vào cho mô hình học phong cách."""
    f: dict[str, float] = {}
    f["mired"] = color.mired(ph.as_shot_temp) if ph.as_shot_temp else math.nan
    f["tint"] = ph.as_shot_tint if ph.as_shot_tint is not None else math.nan
    f["log_iso"] = math.log2(ph.iso) if ph.iso else math.nan
    f["log_shutter"] = math.log2(ph.shutter) if ph.shutter else math.nan
    f["fnumber"] = ph.fnumber or math.nan
    f["focal"] = ph.focal or math.nan
    f["flash"] = ph.flash if ph.flash is not None else math.nan
    f["hour"] = datetime.fromtimestamp(ph.time).hour + datetime.fromtimestamp(ph.time).minute / 60 if ph.time else math.nan
    f.update(image_stats(ph.preview))
    return f


def image_stats(rgb_u8: np.ndarray | None) -> dict:
    keys = ["y_p5", "y_p25", "y_p50", "y_p75", "y_p95", "y_p99", "clip_hi", "clip_lo",
            "n_a", "n_b", "gw_a", "gw_b", "hi_a", "hi_b", "chroma"]
    if rgb_u8 is None:
        return {k: math.nan for k in keys}
    rgb = rgb_u8.reshape(-1, 3).astype(np.float64) / 255.0
    lin = color.srgb_to_linear(rgb)
    y = lin @ np.array([0.2126, 0.7152, 0.0722])
    ly = np.log2(y + 1e-4)
    p = np.percentile(ly, [5, 25, 50, 75, 95, 99])
    mx = rgb.max(axis=1)
    lab = color.xyz_to_lab(color.linear_to_xyz(lin))
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    ch = np.hypot(a, b)
    na, nb = neutral_ab(lab)
    hi = L >= np.percentile(L, 97)
    return {
        "y_p5": p[0], "y_p25": p[1], "y_p50": p[2], "y_p75": p[3], "y_p95": p[4], "y_p99": p[5],
        "clip_hi": float(np.mean(mx > 0.98)), "clip_lo": float(np.mean(mx < 0.02)),
        "n_a": na, "n_b": nb, "gw_a": float(a.mean()), "gw_b": float(b.mean()),
        "hi_a": float(a[hi].mean()), "hi_b": float(b[hi].mean()), "chroma": float(ch.mean()),
    }


_D65_UV = (0.19783, 0.46832)


def neutral_xy(lin: np.ndarray) -> tuple[float, float]:
    """Sắc độ (xy) của các điểm gần xám nhất -> ảnh đang ám màu gì.

    Đo bằng sắc độ (u'v') nên không phụ thuộc sáng/tối: làm tối ảnh không làm
    nó "bớt ấm". Không đủ điểm thì lấy trung bình cả ảnh (gray-world)."""
    xyz = color.linear_to_xyz(lin.reshape(-1, 3))
    Y = xyz[:, 1]
    s = xyz.sum(axis=1) + 1e-9
    den = xyz[:, 0] + 15 * xyz[:, 1] + 3 * xyz[:, 2] + 1e-9
    u, v = 4 * xyz[:, 0] / den, 9 * xyz[:, 1] / den
    d = np.hypot(u - _D65_UV[0], v - _D65_UV[1])
    # Ngưỡng sáng TƯƠNG ĐỐI theo chính ảnh, để ảnh tối hơn vẫn chọn đúng các điểm
    # đó; bỏ vùng tối (dễ bị nhấc đen làm lệch) và vùng cháy.
    lo, hi = np.percentile(Y, [25, 98])
    mid = (Y > max(lo, 0.01)) & (Y < min(hi, 0.9)) & (s > 1e-6)
    if mid.sum() < 50:
        m = np.ones(len(Y), bool)
    else:
        thr = max(0.004, float(np.percentile(d[mid], 30)))
        m = mid & (d <= thr)
        if m.sum() < 50:
            m = mid
    mean = xyz[m].mean(axis=0)
    return color.xyz_to_xy(mean)


def neutral_ab(lab: np.ndarray) -> tuple[float, float]:
    """Màu trung bình của các điểm gần xám ở vùng trung tính -> độ ám màu của ảnh.
    Không đủ điểm xám thì dùng trung bình cả ảnh (gray-world)."""
    lab = lab.reshape(-1, 3)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    mid = (L > 20) & (L < 92)
    ch = np.hypot(a, b)
    if mid.sum() < 50:
        return float(a.mean()), float(b.mean())
    thr = max(6.0, float(np.percentile(ch[mid], 30)))
    m = mid & (ch <= thr)
    if m.sum() < 50:
        m = mid
    return float(np.median(a[m])), float(np.median(b[m]))
