"""CÂN BẰNG TRẮNG GỐC (As Shot) khi thư viện đọc RAW chưa biết máy ảnh.

LibRaw cần "ma trận màu" của từng đời máy để đổi hệ số cân bằng trắng của máy (ghi trong file
RAW) ra Nhiệt độ màu / Tint như Camera Raw. Máy mới (Nikon Z5 II, Sony A7 V...) chưa có trong
LibRaw -> trước đây tool không đọc được As Shot và KHÔNG chỉnh được nhiệt độ màu.

Thứ tự tìm ma trận:
  1. Hồ sơ màu .dcp của chính Camera Raw trên máy (ProgramData / AppData\\Adobe\\CameraRaw\\
     CameraProfiles): ColorMatrix1/2 + nguồn sáng hiệu chuẩn — tính y như Camera Raw (DNG SDK).
  2. Bảng dự phòng (ma trận Adobe lấy từ LibRaw bản mới / máy cùng cảm biến) — gần đúng.
Khi có Photoshop, tool còn đọc As Shot thật từ Camera Raw để hiệu chỉnh (look.copy_color).
"""
from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

import numpy as np

from . import color

# Ma trận Adobe ColorMatrix (D65) x 10000 — từ bảng màu LibRaw (bản mới nhất), cho máy chưa có
# trong LibRaw đóng gói. Z5_2 chưa có ở đâu: dùng Z 6_2 (cùng cảm biến 24,5MP BSI) — gần đúng.
FALLBACK = {
    "NIKONZ52": ("Nikon Z 6_2 (cùng cảm biến)", [9943, -3269, -839, -5323, 13269, 2259, -1198, 2083, 7557]),
    "NIKONZ63": ("Nikon Z6_3", [11206, -4286, -941, -4879, 12847, 2251, -745, 1654, 7374]),
    "NIKONZF": ("Nikon Z f", [11607, -4491, -977, -4522, 12460, 2304, -458, 1519, 7616]),
    "NIKONZ8": ("Nikon Z 8", [11423, -4564, -1123, -4816, 12895, 2119, -210, 1061, 7282]),
    "ILCE7M5": ("Sony ILCE-7M5", [9089, -3577, -787, -3563, 11326, 2557, -114, 928, 5904]),
    "ILCE1M2": ("Sony ILCE-1M2", [10058, -4074, -932, -4777, 12731, 2274, -544, 1282, 6102]),
    "ILCE7M4": ("Sony ILCE-7M4", [7460, -2365, -588, -5687, 13442, 2474, -624, 1156, 6584]),
    "ILCE7CM2": ("Sony ILCE-7CM2", [7460, -2365, -588, -5687, 13442, 2474, -624, 1156, 6584]),
    "ILCE7RM5": ("Sony ILCE-7RM5", [8200, -2976, -719, -4296, 12053, 2532, -429, 1282, 5774]),
    "ILCE9M3": ("Sony ILCE-9M3", [9811, -3908, -752, -3704, 11577, 2417, -73, 950, 5980]),
    "CANONEOSR50V": ("Canon EOS R50 V", [9089, -1810, -1209, -4294, 12116, 2454, -498, 1862, 4923]),
}
# Máy mới hoàn toàn của một hãng: dùng ma trận một đời máy gần đây của hãng (gần đúng hơn là bỏ trống)
BRAND = {"NIKON": "NIKONZ52", "SONY": "ILCE7M4", "ILCE": "ILCE7M4", "CANON": "CANONEOSR50V"}

# Nhiệt độ của nguồn sáng hiệu chuẩn (mã EXIF LightSource) — như DNG SDK
_ILLUM_K = {1: 5500.0, 2: 4150.0, 3: 2850.0, 4: 5500.0, 9: 5500.0, 10: 6500.0, 11: 7500.0,
            12: 6400.0, 13: 5050.0, 14: 4150.0, 15: 3525.0, 16: 2925.0, 17: 2850.0, 18: 5500.0,
            19: 6500.0, 20: 5500.0, 21: 6500.0, 22: 7500.0, 23: 5000.0, 24: 3200.0}

INDEX = Path.home() / "Editer" / "dcp-index.json"
CALIB = Path.home() / "Editer" / "wb-calib.json"     # độ lệch As Shot "tool tính" so với Camera Raw, theo đời máy


def _load_calib() -> dict:
    try:
        return json.loads(CALIB.read_text(encoding="utf-8"))
    except Exception:
        return {}


def calib_of(model: str | None) -> tuple[float, float] | None:
    c = _load_calib().get(norm(model))
    return (float(c["mired"]), float(c["tint"])) if c else None


def save_calib(model: str | None, d_mired: float, d_tint: float) -> None:
    """Ghi nhớ độ lệch (cộng dồn vào số đã nhớ): lần sau — kể cả khi không mở Photoshop, khi học / chỉnh
    theo phong cách — As Shot của đời máy này khớp Camera Raw."""
    if not norm(model):
        return
    data = _load_calib()
    old = data.get(norm(model), {"mired": 0.0, "tint": 0.0, "n": 0})
    data[norm(model)] = {"mired": old["mired"] + d_mired, "tint": old["tint"] + d_tint, "n": old.get("n", 0) + 1,
                         "may": model}
    try:
        CALIB.parent.mkdir(parents=True, exist_ok=True)
        CALIB.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def model_keys(make: str | None, model: str | None) -> list[str]:
    """Các cách viết tên máy để so: 'NIKON Z5_2' -> NIKONZ52; Sony 'ILCE-7M4' -> ILCE7M4, SONYILCE7M4."""
    m = norm(model)
    keys = [m] if m else []
    first = norm((make or "").split()[0] if make else "")
    if first and m and not m.startswith(first):
        keys.append(first + m)
    return keys


# ------------------------------------------------------------------ tính As Shot
def neutral_to_temp_tint(neutral, cm1, cm2=None, t1: float | None = None, t2: float | None = None):
    """Hệ số "trung tính" của máy (1 / hệ số cân bằng trắng) -> (Nhiệt độ, Tint) như Camera Raw.
    cm1/cm2: ma trận XYZ -> camera ở 2 nguồn sáng hiệu chuẩn (nội suy theo 1/nhiệt độ, DNG SDK)."""
    neutral = np.asarray(neutral, float)
    a = np.asarray(cm1, float).reshape(3, 3)
    b = np.asarray(cm2, float).reshape(3, 3) if cm2 is not None else None
    if b is not None and t1 and t2 and t1 > t2:
        a, b, t1, t2 = b, a, t2, t1
    xy = (0.3457, 0.3585)                                   # bắt đầu từ D50
    for _ in range(30):
        if b is None or not (t1 and t2):
            m = a
        else:
            t, _tint = color.xy_to_temp_tint(*xy)
            if t <= t1:
                g = 1.0
            elif t >= t2:
                g = 0.0
            else:
                g = (1 / t - 1 / t2) / (1 / t1 - 1 / t2)
            m = g * a + (1 - g) * b
        xyz = np.linalg.solve(m, neutral)
        if np.any(xyz <= 0):
            return None
        nxt = color.xyz_to_xy(xyz)
        if abs(nxt[0] - xy[0]) + abs(nxt[1] - xy[1]) < 1e-7:
            xy = nxt
            break
        xy = nxt
    t, tint = color.xy_to_temp_tint(*xy)
    return (float(t), float(tint)) if 1500 < t < 50000 else None


# ------------------------------------------------------------------ hồ sơ màu Adobe (.dcp)
def _dcp_dirs() -> list[Path]:
    if os.environ.get("EDITER_DCP_DIRS"):
        return [Path(p) for p in os.environ["EDITER_DCP_DIRS"].split(os.pathsep) if p]
    out = []
    for env in ("ProgramData", "APPDATA"):
        if os.environ.get(env):
            out.append(Path(os.environ[env]) / "Adobe" / "CameraRaw" / "CameraProfiles")
    out.append(Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles"))
    return [d for d in out if d.is_dir()]


def read_dcp(path: str | Path) -> dict | None:
    """Đọc ColorMatrix1/2, nguồn sáng hiệu chuẩn và tên máy trong file .dcp (định dạng kiểu TIFF)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        return None
    e = "<" if data[:2] == b"II" else ">"
    try:
        off = struct.unpack(e + "I", data[4:8])[0]
        n = struct.unpack(e + "H", data[off:off + 2])[0]
        tags = {}
        size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8, 11: 4, 12: 8}
        for i in range(n):
            p = off + 2 + 12 * i
            tag, typ, cnt = struct.unpack(e + "HHI", data[p:p + 8])
            nbytes = size.get(typ, 1) * cnt
            vp = p + 8 if nbytes <= 4 else struct.unpack(e + "I", data[p + 8:p + 12])[0]
            raw_v = data[vp:vp + nbytes]
            if typ == 2:
                tags[tag] = raw_v.split(b"\0")[0].decode("latin-1", "replace")
            elif typ == 3:
                tags[tag] = list(struct.unpack(e + "H" * cnt, raw_v))
            elif typ == 4:
                tags[tag] = list(struct.unpack(e + "I" * cnt, raw_v))
            elif typ in (5, 10):
                vals = struct.unpack(e + ("I" if typ == 5 else "i") * (2 * cnt), raw_v)
                tags[tag] = [vals[2 * k] / vals[2 * k + 1] if vals[2 * k + 1] else 0.0 for k in range(cnt)]
    except (struct.error, IndexError):
        return None
    if 50721 not in tags or len(tags[50721]) != 9:
        return None
    return {"model": tags.get(50708, ""), "name": tags.get(50936, ""),
            "cm1": tags[50721], "cm2": tags.get(50722) if len(tags.get(50722, [])) == 9 else None,
            "t1": _ILLUM_K.get((tags.get(50778) or [0])[0]), "t2": _ILLUM_K.get((tags.get(50779) or [0])[0])}


def _load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_dcp(make: str | None, model: str | None) -> dict | None:
    """Hồ sơ màu Adobe của đúng đời máy (ưu tiên 'Adobe Standard'). Nhớ kết quả vào dcp-index.json."""
    keys = model_keys(make, model)
    if not keys:
        return None
    idx = _load_index()
    if keys[0] in idx:
        return idx[keys[0]]
    best = None
    for d in _dcp_dirs():
        try:
            files = list(d.rglob("*.dcp"))
        except OSError:
            continue
        for f in files:
            if not any(k in norm(f.stem) for k in keys):        # lọc nhanh theo tên file
                continue
            prof = read_dcp(f)
            if not prof:
                continue
            u = norm(prof["model"])
            if not (u in keys or any(u.endswith(k) for k in keys)):
                continue
            prof["file"] = str(f)
            if best is None or "ADOBESTANDARD" in norm(f.stem + prof["name"]):
                best = prof
        if best:
            break
    if best:   # chỉ nhớ khi tìm thấy (cài Camera Raw mới sau này thì lần sau vẫn tìm lại)
        idx[keys[0]] = best
        try:
            INDEX.parent.mkdir(parents=True, exist_ok=True)
            INDEX.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return best


def estimate(make: str | None, model: str | None, mult) -> tuple[float, float, str] | None:
    """Hệ số cân bằng trắng của máy (R, G, B) -> (Nhiệt độ, Tint, nguồn) khi LibRaw không tính được."""
    if mult is None:
        return None
    mult = np.asarray(mult, float)[:3]
    if np.any(mult <= 0):
        return None
    neutral = 1.0 / mult
    neutral /= neutral.max()
    return _estimate(make, model, neutral)


def calibrated(model: str | None, temp: float | None, tint: float | None, src: str | None):
    """As Shot đã bù theo độ lệch Camera Raw đã nhớ (chỉ cho máy không có trong LibRaw).
    Dùng khi CHÉP MÀU (cần số tuyệt đối khớp Camera Raw). Học / chỉnh theo phong cách thì không cần:
    ở đó cân bằng trắng tính tương đối so với As Shot của chính tool nên độ lệch tự triệt tiêu."""
    if temp is None or src in (None, "libraw"):
        return temp, tint
    c = calib_of(model)
    if not c or 1e6 / temp + c[0] <= 20:
        return temp, tint
    return 1e6 / (1e6 / temp + c[0]), (tint or 0.0) + c[1]


def _estimate(make, model, neutral):
    prof = find_dcp(make, model)
    if prof:
        r = neutral_to_temp_tint(neutral, prof["cm1"], prof["cm2"], prof["t1"], prof["t2"])
        if r:
            return r[0], r[1], f"hồ sơ màu Adobe ({Path(prof['file']).name})"
    keys = model_keys(make, model)
    hit = next((FALLBACK[k] for k in keys if k in FALLBACK), None)
    if hit is None:
        brand = next((BRAND[b] for b in BRAND if any(k.startswith(b) for k in keys)), None)
        hit = FALLBACK.get(brand) if brand else None
        if hit:
            hit = (f"{hit[0]} — máy cùng hãng, gần đúng", hit[1])
    if hit:
        r = neutral_to_temp_tint(neutral, np.array(hit[1]) / 10000.0)
        if r:
            return r[0], r[1], f"ma trận dự phòng {hit[0]}"
    return None
