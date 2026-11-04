"""Toán màu dùng chung: sRGB <-> tuyến tính <-> XYZ <-> Lab, và
Nhiệt độ màu/Tint theo đúng cách Camera Raw tính (thuật toán Robertson trong
Adobe DNG SDK, dng_temperature.cpp, kTintScale = -3000).

Quy ước Tint của Camera Raw: Tint dương = nguồn sáng ngả XANH LÁ (nằm trên
đường Planck), nên bù lại ảnh sẽ ngả hồng. Ví dụ D65 ~ 6500K, Tint ~ +10.
"""
from __future__ import annotations

import math

import numpy as np

# (mired, u, v, slope) — bảng Robertson trong DNG SDK
_TEMP_TABLE = [
    (0, 0.18006, 0.26352, -0.24341),
    (10, 0.18066, 0.26589, -0.25479),
    (20, 0.18133, 0.26846, -0.26876),
    (30, 0.18208, 0.27119, -0.28539),
    (40, 0.18293, 0.27407, -0.30470),
    (50, 0.18388, 0.27709, -0.32675),
    (60, 0.18494, 0.28021, -0.35156),
    (70, 0.18611, 0.28342, -0.37915),
    (80, 0.18740, 0.28668, -0.40955),
    (90, 0.18880, 0.28997, -0.44278),
    (100, 0.19032, 0.29326, -0.47888),
    (125, 0.19462, 0.30141, -0.58204),
    (150, 0.19962, 0.30921, -0.70471),
    (175, 0.20525, 0.31647, -0.84901),
    (200, 0.21142, 0.32312, -1.0182),
    (225, 0.21807, 0.32909, -1.2168),
    (250, 0.22511, 0.33439, -1.4512),
    (275, 0.23247, 0.33904, -1.7298),
    (300, 0.24010, 0.34308, -2.0637),
    (325, 0.24792, 0.34655, -2.4681),
    (350, 0.25591, 0.34951, -2.9641),
    (375, 0.26400, 0.35200, -3.5814),
    (400, 0.27218, 0.35407, -4.3633),
    (425, 0.28039, 0.35577, -5.3762),
    (450, 0.28863, 0.35714, -6.7262),
    (475, 0.29685, 0.35823, -8.5955),
    (500, 0.30505, 0.35907, -11.324),
    (525, 0.31320, 0.35968, -15.628),
    (550, 0.32129, 0.36011, -23.325),
    (575, 0.32931, 0.36038, -40.770),
    (600, 0.33724, 0.36051, -116.45),
]
_TINT_SCALE = -3000.0

D65_XY = (0.3127, 0.3290)


def xy_to_temp_tint(x: float, y: float) -> tuple[float, float]:
    """Toạ độ màu xy -> (Nhiệt độ K, Tint) như Camera Raw."""
    den = 1.5 - x + 6.0 * y
    u, v = 2.0 * x / den, 3.0 * y / den
    last_dt = last_du = last_dv = 0.0
    for i in range(1, 31):
        r1, u1, v1, t1 = _TEMP_TABLE[i]
        du = 1.0 / math.sqrt(1.0 + t1 * t1)
        dv = t1 * du
        uu, vv = u - u1, v - v1
        dt = -uu * dv + vv * du
        if dt <= 0 or i == 30:
            if dt > 0:
                dt = 0.0
            dt = -dt
            f = 0.0 if i == 1 else dt / (last_dt + dt)
            r0, u0, v0, _ = _TEMP_TABLE[i - 1]
            temp = 1.0e6 / (r0 * f + r1 * (1.0 - f)) if (r0 * f + r1 * (1.0 - f)) > 0 else 50000.0
            uu = u - (u0 * f + u1 * (1.0 - f))
            vv = v - (v0 * f + v1 * (1.0 - f))
            du = du * (1.0 - f) + last_du * f
            dv = dv * (1.0 - f) + last_dv * f
            ln = math.hypot(du, dv)
            du, dv = du / ln, dv / ln
            tint = (uu * du + vv * dv) * _TINT_SCALE
            return temp, tint
        last_dt, last_du, last_dv = dt, du, dv
    raise AssertionError("không tới được")


def temp_tint_to_xy(temp: float, tint: float) -> tuple[float, float]:
    """(Nhiệt độ K, Tint) -> xy. Ngược của xy_to_temp_tint."""
    r = 1.0e6 / temp
    offset = tint * (1.0 / _TINT_SCALE)
    for i in range(30):
        r0, u0, v0, t0 = _TEMP_TABLE[i]
        r1, u1, v1, t1 = _TEMP_TABLE[i + 1]
        if r < r1 or i == 29:
            f = (r1 - r) / (r1 - r0)
            u = u0 * f + u1 * (1.0 - f)
            v = v0 * f + v1 * (1.0 - f)
            a1, b1 = 1.0, t0
            n = math.hypot(a1, b1); a1, b1 = a1 / n, b1 / n
            a2, b2 = 1.0, t1
            n = math.hypot(a2, b2); a2, b2 = a2 / n, b2 / n
            a3, b3 = a1 * f + a2 * (1.0 - f), b1 * f + b2 * (1.0 - f)
            n = math.hypot(a3, b3); a3, b3 = a3 / n, b3 / n
            u += a3 * offset
            v += b3 * offset
            d = u - 4.0 * v + 2.0
            return 1.5 * u / d, v / d
    raise AssertionError("không tới được")


def mired(temp: float) -> float:
    return 1.0e6 / temp


# --------------------------------------------------------------------- sRGB
_M_RGB_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_M_XYZ_RGB = np.linalg.inv(_M_RGB_XYZ)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.asarray(c, dtype=np.float64), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def linear_to_xyz(rgb: np.ndarray) -> np.ndarray:
    return rgb @ _M_RGB_XYZ.T


def xyz_to_linear(xyz: np.ndarray) -> np.ndarray:
    return xyz @ _M_XYZ_RGB.T


def _f(t):
    d = 6 / 29
    return np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)


def _finv(t):
    d = 6 / 29
    return np.where(t > d, t ** 3, 3 * d * d * (t - 4 / 29))


def xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    f = _f(xyz / _WHITE_D65)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_xyz(lab: np.ndarray) -> np.ndarray:
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    return np.stack([_finv(fx), _finv(fy), _finv(fz)], axis=-1) * _WHITE_D65


def srgb_to_lab(rgb01: np.ndarray) -> np.ndarray:
    return xyz_to_lab(linear_to_xyz(srgb_to_linear(rgb01)))


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    return np.clip(linear_to_srgb(xyz_to_linear(lab_to_xyz(lab))), 0, 1)


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    s = float(np.sum(xyz))
    return float(xyz[0] / s), float(xyz[1] / s)


def lab_to_temp_tint(lab: np.ndarray) -> tuple[float, float]:
    """Màu (Lab, trắng D65) của một điểm 'trung tính' -> nó trông như ánh sáng
    bao nhiêu K / Tint. Dùng để so độ ấm/lạnh giữa hai ảnh."""
    return xy_to_temp_tint(*xyz_to_xy(lab_to_xyz(np.asarray(lab, dtype=np.float64))))


def rgb_to_hsv(rgb01: np.ndarray) -> np.ndarray:
    """sRGB 0..1 -> H (độ 0..360), S, V (0..1)."""
    r, g, b = rgb01[..., 0], rgb01[..., 1], rgb01[..., 2]
    mx = np.max(rgb01, axis=-1)
    mn = np.min(rgb01, axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-9
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = ((g[rm] - b[rm]) / d[rm]) % 6
    h[gm] = (b[gm] - r[gm]) / d[gm] + 2
    h[bm] = (r[bm] - g[bm]) / d[bm] + 4
    h = h * 60.0
    s = np.where(mx > 1e-9, d / np.maximum(mx, 1e-9), 0.0)
    return np.stack([h, s, mx], axis=-1)
