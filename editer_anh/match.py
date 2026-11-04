"""CHỈNH THEO MÀU CỦA MỘT FILE JPG MẪU.

Đầu vào: một (hoặc vài) JPG có màu bạn muốn + thư mục RAW.
Đầu ra: thông số Camera Raw (thanh trượt thật, chỉnh tiếp được) cho từng ảnh.

Chia làm hai phần:
  1. RIÊNG TỪNG ẢNH — cân bằng trắng (Temperature/Tint) và Exposure: đưa điểm
     trung tính và độ sáng của mỗi ảnh về giống ảnh mẫu, nên cả lô ra đều màu
     dù ảnh chụp dưới ánh sáng khác nhau.
  2. CHUNG CẢ LÔ — "look": Tone Curve, HSL 8 màu, Color Grading vùng tối/sáng.
     Tính trên toàn bộ lô (gộp điểm ảnh của mọi tấm) so với ảnh mẫu, nên không
     bị một tấm lạ (toàn váy trắng, toàn cây xanh) kéo lệch.

Đây là phép xấp xỉ: tool không có bộ dựng ảnh của Camera Raw, nên ước lượng
trên ảnh xem trước nhúng trong RAW (gần giống profile Adobe Color). Kết quả là
điểm xuất phát rất gần — bạn vẫn mở ra chỉnh tiếp được từng thanh trượt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import color

HSL_BANDS = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]
_CENTERS = np.array([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0])
CURVE_X = [0, 40, 80, 128, 176, 216, 255]
_LUMA = np.array([0.2126, 0.7152, 0.0722])


@dataclass
class Stats:
    rgb: np.ndarray        # N x 3, sRGB 0..1 (đã lấy mẫu)
    lab: np.ndarray        # N x 3
    neutral: tuple[float, float]   # xy của điểm trung tính
    y_med: float           # độ sáng tuyến tính trung vị

    @staticmethod
    def of(rgb_u8: np.ndarray, max_px: int = 60000, seed: int = 0) -> "Stats":
        from .raw import neutral_xy
        px = rgb_u8.reshape(-1, 3)
        if len(px) > max_px:
            px = px[np.random.default_rng(seed).choice(len(px), max_px, replace=False)]
        rgb = px.astype(np.float64) / 255.0
        lin = color.srgb_to_linear(rgb)
        lab = color.xyz_to_lab(color.linear_to_xyz(lin))
        y = lin @ _LUMA
        return Stats(rgb, lab, neutral_xy(lin), float(np.median(y)))


# ------------------------------------------------------------- 1. TỪNG ẢNH
def wb_exposure(photo: Stats, ref: Stats, strength: float = 0.85) -> tuple[float, float, float]:
    """-> (Δmired, ΔTint, ΔEV) cộng vào As Shot của ảnh.

    Ảnh trông lạnh hơn mẫu -> giảm mired (tăng K) -> ảnh ấm lên; ảnh ngả xanh lá
    hơn mẫu -> tăng Tint (Camera Raw: Tint + = bù hồng)."""
    tp, tintp = color.xy_to_temp_tint(*photo.neutral)
    tr, tintr = color.xy_to_temp_tint(*ref.neutral)
    d_mired = (color.mired(tp) - color.mired(tr)) * strength
    d_tint = (tintp - tintr) * strength
    d_ev = 0.0
    if photo.y_med > 1e-4 and ref.y_med > 1e-4:
        d_ev = math.log2(ref.y_med / photo.y_med) * 0.7
    # Nén mềm: lệch ít thì theo gần đủ, lệch nhiều (ảnh mẫu toàn màu cam, không có
    # điểm trắng/xám) thì không cho đẩy quá tay. 60 mired ~ 5500K -> 8000K / 4100K.
    d_mired = 60.0 * math.tanh(d_mired / 60.0)
    d_tint = 30.0 * math.tanh(d_tint / 30.0)
    return float(d_mired), float(d_tint), float(np.clip(d_ev, -1.5, 1.5))


def _bradford(src_xy, dst_xy) -> np.ndarray:
    M = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    def xyz(xy):
        x, y = xy
        return np.array([x / y, 1.0, (1 - x - y) / y])
    s, d = M @ xyz(src_xy), M @ xyz(dst_xy)
    return np.linalg.inv(M) @ np.diag(d / s) @ M


def simulate(photo: Stats, d_mired: float, d_tint: float, d_ev: float) -> np.ndarray:
    """Mô phỏng (gần đúng) ảnh sau khi đổi WB + Exposure -> sRGB 0..1."""
    tp, tintp = color.xy_to_temp_tint(*photo.neutral)
    src = photo.neutral
    new_m = max(color.mired(tp) - d_mired, 20.0)
    dst = color.temp_tint_to_xy(1e6 / new_m, tintp - d_tint)
    lin = color.srgb_to_linear(photo.rgb)
    xyz = color.linear_to_xyz(lin) @ _bradford(src, dst).T
    lin2 = color.xyz_to_linear(xyz) * (2.0 ** d_ev)
    return np.clip(color.linear_to_srgb(lin2), 0, 1)


# ------------------------------------------------------------- 2. CẢ LÔ
def tone_curve(src_rgb: np.ndarray, ref_rgb: np.ndarray, strength: float = 0.8) -> list[tuple[int, int]]:
    """Ghép phân bố độ sáng (histogram matching) -> 7 điểm Tone Curve 0..255."""
    ls = (src_rgb @ _LUMA) * 255
    lr = (ref_rgb @ _LUMA) * 255
    qs = np.linspace(0.5, 99.5, 100)
    ps, pr = np.percentile(ls, qs), np.percentile(lr, qs)
    ps = np.maximum.accumulate(ps + np.arange(len(ps)) * 1e-6)
    pts = []
    for x in CURVE_X:
        if x < ps[0]:        # tối hơn mọi điểm của lô: nối tiếp độ dốc 1 (=> nhấc đen kiểu film)
            y = pr[0] - (ps[0] - x)
        elif x > ps[-1]:     # sáng hơn mọi điểm của lô: tương tự
            y = pr[-1] + (x - ps[-1])
        else:
            y = float(np.interp(x, ps, pr))
        if x == 0:
            y = float(np.clip(y, 0, 60))
        if x == 255:
            y = float(np.clip(y, 200, 255))
        y = x + strength * (y - x)
        pts.append([x, y])
    for i in range(1, len(pts)):              # luôn đi lên, không dốc quá (tránh gắt/bệt)
        dx = pts[i][0] - pts[i - 1][0]
        pts[i][1] = min(max(pts[i][1], pts[i - 1][1] + 0.4 * dx), pts[i - 1][1] + 1.8 * dx)
    return [(int(x), int(round(min(255, max(0, y))))) for x, y in pts]


def apply_curve(rgb: np.ndarray, curve: list[tuple[int, int]]) -> np.ndarray:
    xs = np.array([p[0] for p in curve], dtype=float) / 255
    ys = np.array([p[1] for p in curve], dtype=float) / 255
    return np.interp(rgb, xs, ys)


def _band_weights(h: np.ndarray) -> np.ndarray:
    """Mỗi điểm ảnh thuộc 2 dải màu kề nhau theo tỉ lệ (giống Camera Raw)."""
    n = len(_CENTERS)
    w = np.zeros((len(h), n))
    ext = np.append(_CENTERS, 360.0)
    idx = np.clip(np.searchsorted(ext, h, side="right") - 1, 0, n - 1)
    lo, hi = ext[idx], ext[idx + 1]
    t = (h - lo) / (hi - lo)
    w[np.arange(len(h)), idx] = 1 - t
    w[np.arange(len(h)), (idx + 1) % n] += t
    return w


def _band_stats(rgb: np.ndarray) -> list[dict | None]:
    hsv = color.rgb_to_hsv(rgb)
    lab = color.srgb_to_lab(rgb)
    ok = (hsv[:, 1] > 0.12) & (hsv[:, 2] > 0.12) & (hsv[:, 2] < 0.97)
    if ok.sum() < 200:
        return [None] * len(_CENTERS)
    h, s, L = hsv[ok, 0], hsv[ok, 1], lab[ok, 0]
    W = _band_weights(h) * s[:, None]
    total = len(rgb)
    out = []
    for i, c in enumerate(_CENTERS):
        w = W[:, i]
        ws = w.sum()
        if ws / total < 0.01:
            out.append(None)
            continue
        off = (h - c + 180) % 360 - 180
        out.append({
            "hue": float(np.sum(w * off) / ws),
            "sat": float(np.sum(w * s) / ws),
            "lum": float(np.sum(w * L) / ws),
            "share": float(ws / total),
        })
    return out


def hsl(src_rgb: np.ndarray, ref_rgb: np.ndarray, strength: float = 0.7) -> dict:
    bs, br = _band_stats(src_rgb), _band_stats(ref_rgb)
    out = {}
    for i, name in enumerate(HSL_BANDS):
        a, b = bs[i], br[i]
        if a is None or b is None:
            out.update({f"HueAdjustment{name}": 0, f"SaturationAdjustment{name}": 0,
                        f"LuminanceAdjustment{name}": 0})
            continue
        dh = b["hue"] - a["hue"]
        span = (_CENTERS[(i + 1) % 8] - _CENTERS[i]) % 360 if dh > 0 else (_CENTERS[i] - _CENTERS[i - 1]) % 360
        hue = np.clip(dh / span * 100 * strength, -50, 50)
        sat = np.clip((b["sat"] / max(a["sat"], 1e-3) - 1) * 100 * strength, -70, 50)
        lum = np.clip((b["lum"] - a["lum"]) * 2.0 * strength, -40, 40)
        out.update({f"HueAdjustment{name}": hue, f"SaturationAdjustment{name}": sat,
                    f"LuminanceAdjustment{name}": lum})
    return out


def color_grade(src_rgb: np.ndarray, ref_rgb: np.ndarray) -> dict:
    """Ám màu riêng ở vùng tối / vùng sáng (vd bóng ngả xanh, sáng ngả cam)."""
    def casts(rgb):
        lab = color.srgb_to_lab(rgb)
        L = lab[:, 0]
        mid = (L >= 35) & (L <= 70)
        base = lab[mid, 1:].mean(axis=0) if mid.sum() > 50 else np.zeros(2)
        res = {}
        for name, m in (("Shadow", L < 30), ("Highlight", L > 75)):
            res[name] = lab[m, 1:].mean(axis=0) - base if m.sum() > 50 else None
        return res
    cs, cr = casts(src_rgb), casts(ref_rgb)
    out = {}
    for name in ("Shadow", "Highlight"):
        if cs[name] is None or cr[name] is None:
            out[f"ColorGrade{name}Hue"], out[f"ColorGrade{name}Sat"] = 0, 0
            continue
        d = cr[name] - cs[name]
        mag = float(np.hypot(*d))
        if mag < 1.5:
            out[f"ColorGrade{name}Hue"], out[f"ColorGrade{name}Sat"] = 0, 0
            continue
        rgb = color.lab_to_srgb(np.array([60.0, d[0] * 3, d[1] * 3]))
        hue = float(color.rgb_to_hsv(rgb[None, :])[0, 0])
        out[f"ColorGrade{name}Hue"] = round(hue) % 360
        out[f"ColorGrade{name}Sat"] = float(np.clip(mag * 2.5, 0, 40))
    return out


def batch_look(sim_pixels: list[np.ndarray], ref: Stats) -> tuple[list[tuple[int, int]], dict]:
    """Gộp điểm ảnh (đã chỉnh WB/Exposure mô phỏng) của cả lô -> curve + HSL + grading."""
    pool = np.concatenate(sim_pixels, axis=0)
    if len(pool) > 300000:
        pool = pool[np.random.default_rng(1).choice(len(pool), 300000, replace=False)]
    curve = tone_curve(pool, ref.rgb)
    pool_c = apply_curve(pool, curve)
    params = {}
    params.update(hsl(pool_c, ref.rgb))
    params.update(color_grade(pool_c, ref.rgb))
    return curve, params


def merge_refs(refs: list[np.ndarray]) -> Stats:
    """Nhiều ảnh mẫu -> gộp thành một bộ thống kê."""
    arrs = [r.reshape(-1, 3) for r in refs]
    return Stats.of(np.concatenate(arrs, axis=0)[None, :, :] if len(arrs) > 1 else refs[0])
