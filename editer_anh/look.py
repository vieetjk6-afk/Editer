"""CHÉP MÀU TỪ ẢNH JPG MẪU (bản 2): khớp theo VÙNG + kiểm tra bằng Camera Raw thật.

"Đúng màu" được định nghĩa bằng những thứ người xem nhìn vào:
  - DA người          : màu da (L*a*b*) giống da trong ảnh mẫu
  - TRẮNG / TRUNG TÍNH: váy cưới, áo trắng, tường... có cùng độ ấm/lạnh như ảnh mẫu
  - TRỜI, CÂY         : cùng sắc xanh như ảnh mẫu
  - TÔNG SÁNG TỐI     : phân bố sáng/tối (độ tương phản, đen nhấc, trắng nén)
  - ÁM VÙNG TỐI/SÁNG  : bóng ngả xanh, vùng sáng ngả cam... (color grading)
  - CÁC MÀU KHÁC      : 8 dải màu (đỏ, cam, vàng, xanh lá...)

Các vùng do AI phân vùng tìm ra (ai.Segmenter). Không có AI thì vẫn chạy được,
chỉ dùng tông sáng tối + trung tính + 8 dải màu.

Cách tìm thông số: MÔ PHỎNG tác dụng các thanh trượt Camera Raw (Nhiệt độ màu,
Tint, Exposure, Tone Curve, HSL, Color Grading, Saturation, Vibrance) rồi tối ưu
cho các số đo ở trên khớp ảnh mẫu. Mô phỏng chỉ là gần đúng, nên khi có
Photoshop, tool cho Camera Raw THẬT dựng ảnh, đo lại, sửa tiếp (vòng lặp) — sai số
cuối cùng là sai số đo trên ảnh Camera Raw dựng ra, không phải trên mô phỏng.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from . import color

# ------------------------------------------------------------------ vùng ảnh
OTHER, SKIN, HAIR, CLOTHES, SKY, PLANT, WATER, PERSON = range(8)
REGION_VI = {SKIN: "da", HAIR: "tóc", CLOTHES: "quần áo", SKY: "trời", PLANT: "cây",
             WATER: "nước", PERSON: "người"}

CURVE_X = np.array([0.0, 48.0, 96.0, 160.0, 208.0, 255.0])
BANDS = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]
CENTERS = np.array([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0])
_NEXT = (np.roll(CENTERS, -1) - CENTERS) % 360      # khoảng tới dải kế tiếp
_PREV = (CENTERS - np.roll(CENTERS, 1)) % 360       # khoảng tới dải trước
TONE_Q = [5, 25, 50, 75, 95]


@dataclass(frozen=True)
class Consts:
    """Ước lượng tác dụng của thanh trượt Camera Raw (dùng cho mô phỏng).
    Sai lệch so với Camera Raw thật được vòng kiểm tra tự học và bù (refine)."""
    hue_frac: float = 0.5     # Hue +100: dịch ~ nửa khoảng tới dải màu kề
    lum_k: float = 35.0       # Luminance ±100: ±35 L* ở màu bão hoà hoàn toàn
    grade_k: float = 35.0     # Color Grading Sat 100: ~35 đơn vị a*b* ở vùng đó
    sat_gain: float = 1.0     # độ mạnh của Saturation (HSL) so với ước lượng
    wb_gain: float = 1.0      # độ mạnh của Nhiệt độ màu/Tint so với ước lượng


DEFAULT = Consts()


# ------------------------------------------------------------------ thông số
@dataclass
class Look:
    """Phần "look" dùng chung cả lô."""
    curve: np.ndarray = field(default_factory=lambda: CURVE_X.copy())   # 6 điểm y (0..255)
    hue: np.ndarray = field(default_factory=lambda: np.zeros(8))
    sat: np.ndarray = field(default_factory=lambda: np.zeros(8))
    lum: np.ndarray = field(default_factory=lambda: np.zeros(8))
    grade: np.ndarray = field(default_factory=lambda: np.zeros(6))      # tối(a,b) giữa(a,b) sáng(a,b)
    saturation: float = 0.0
    vibrance: float = 0.0

    def copy(self) -> "Look":
        return Look(self.curve.copy(), self.hue.copy(), self.sat.copy(), self.lum.copy(),
                    self.grade.copy(), float(self.saturation), float(self.vibrance))


@dataclass
class Shot:
    """Phần riêng từng ảnh. mired = 1e6 / Nhiệt độ màu (None = không biết As Shot)."""
    mired: float | None
    tint: float | None
    ev: float = 0.0


N_SHARED = 6 + 24 + 6 + 2


# Curve được tối ưu dưới dạng [điểm đen, độ dốc 5 đoạn]: độ dốc bị khoá trong khoảng
# SLOPE_LO..SLOPE_HI nên curve KHÔNG THỂ bệt (mất chi tiết) hay gắt, dù ảnh vào thế nào.
SLOPE_LO, SLOPE_HI = 0.5, 1.8
_DX = np.diff(CURVE_X)


def _curve_to_vec(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, float)
    return np.concatenate([[y[0]], np.clip(np.diff(y) / _DX, SLOPE_LO, SLOPE_HI)])


def _vec_to_curve(v: np.ndarray) -> np.ndarray:
    return np.minimum(np.concatenate([[v[0]], v[0] + np.cumsum(v[1:] * _DX)]), 255.0)


def _shared_vec(lk: Look) -> np.ndarray:
    return np.concatenate([_curve_to_vec(lk.curve), lk.hue, lk.sat, lk.lum, lk.grade,
                           [lk.saturation, lk.vibrance]])


def _shared_from(v: np.ndarray) -> Look:
    return Look(_vec_to_curve(v[0:6]), v[6:14].copy(), v[14:22].copy(), v[22:30].copy(), v[30:36].copy(),
                float(v[36]), float(v[37]))


# ------------------------------------------------------------------ curve
def _pchip(xk: np.ndarray, yk: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Nội suy đơn điệu (không vọt lố) qua các điểm curve."""
    h = np.diff(xk)
    d = np.diff(yk) / h
    m = np.zeros_like(yk)
    w1, w2 = 2 * h[1:] + h[:-1], h[1:] + 2 * h[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        inner = (w1 + w2) / (w1 / d[:-1] + w2 / d[1:])
    m[1:-1] = np.where(d[:-1] * d[1:] > 0, inner, 0.0)
    m[0], m[-1] = d[0], d[-1]
    i = np.clip(np.searchsorted(xk, x, side="right") - 1, 0, len(xk) - 2)
    t = (x - xk[i]) / h[i]
    h00, h10 = (1 + 2 * t) * (1 - t) ** 2, t * (1 - t) ** 2
    h01, h11 = t * t * (3 - 2 * t), t * t * (t - 1)
    return h00 * yk[i] + h10 * h[i] * m[i] + h01 * yk[i + 1] + h11 * h[i] * m[i + 1]


_LX = np.linspace(0.0, 1.0, 1024)
_CACHE: dict = {}


def curve_lut(y: np.ndarray) -> np.ndarray:
    ly = np.clip(_pchip(CURVE_X / 255.0, np.asarray(y, float) / 255.0, _LX), 0, 1)
    return np.maximum.accumulate(ly) + _LX * 1e-6


def _inv_lut(y: np.ndarray) -> np.ndarray:
    """Bảng tra ngược của curve (để gỡ curve đang có), có cache."""
    key = np.asarray(y, float).tobytes()
    lut = _CACHE.get(key)
    if lut is None:
        if len(_CACHE) > 64:
            _CACHE.clear()
        lut = _CACHE[key] = np.interp(_LX, curve_lut(y), _LX)
    return lut


def _lut(x: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Tra bảng đều (nhanh hơn np.interp nhiều lần)."""
    n = len(lut) - 1
    f = np.clip(x, 0.0, 1.0) * n
    i = np.minimum(f.astype(np.int32), n - 1)
    t = f - i
    return lut[i] * (1.0 - t) + lut[i + 1] * t


def fix_curve(y: np.ndarray) -> np.ndarray:
    """Curve luôn đi lên, nằm trong 0..255."""
    y = np.clip(np.asarray(y, float), 0, 255)
    for i in range(1, len(y)):
        y[i] = max(y[i], y[i - 1] + 1)
    return np.clip(y, 0, 255)


# ------------------------------------------------------------------ đổi hệ màu nhanh (bảng tra)
_GX = np.linspace(0.0, 1.0, 8193)
_TO_LIN = color.srgb_to_linear(_GX)
_TO_SRGB = color.linear_to_srgb(_GX)


def _to_lin(x: np.ndarray) -> np.ndarray:
    return _lut(x, _TO_LIN)


def _to_srgb(lin: np.ndarray) -> np.ndarray:
    return _lut(lin, _TO_SRGB)


def _lab(x: np.ndarray) -> np.ndarray:
    return color.xyz_to_lab(color.linear_to_xyz(_to_lin(x)))


def _from_lab(lab: np.ndarray) -> np.ndarray:
    return _to_srgb(color.xyz_to_linear(color.lab_to_xyz(lab)))


# ------------------------------------------------------------------ mô phỏng
def band_weights(h: np.ndarray) -> np.ndarray:
    n = len(CENTERS)
    ext = np.append(CENTERS, 360.0)
    idx = np.clip(np.searchsorted(ext, h, side="right") - 1, 0, n - 1)
    t = (h - ext[idx]) / (ext[idx + 1] - ext[idx])
    w = np.zeros((len(h), n))
    r = np.arange(len(h))
    w[r, idx] = 1 - t
    w[r, (idx + 1) % n] += t
    return w


def _bradford(src_xy, dst_xy) -> np.ndarray:
    M = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])

    def xyz(xy):
        x, y = xy
        return np.array([x / y, 1.0, (1 - x - y) / y])
    s, d = M @ xyz(src_xy), M @ xyz(dst_xy)
    return np.linalg.inv(M) @ np.diag(d / s) @ M


def simulate(px: np.ndarray, cur_look: Look, cur_shot: Shot, look: Look, shot: Shot,
             k: Consts = DEFAULT, cache: dict | None = None) -> np.ndarray:
    """Ảnh (sRGB 0..1, N x 3) đang được dựng với (cur_look, cur_shot) -> ước lượng ảnh
    khi đổi sang (look, shot). `cache` (dict riêng cho từng ảnh) giúp dùng lại các bước
    không đổi khi chỉ thử thay một nhóm thanh trượt."""
    k1 = (cur_look.curve.tobytes(), look.curve.tobytes(), cur_shot.mired, cur_shot.tint, cur_shot.ev,
          shot.mired, shot.tint, shot.ev, k.wb_gain)
    x = cache.get(k1) if cache is not None else None
    if x is None:
        x = _stage_tone(px, cur_look, cur_shot, look, shot, k)
        if cache is not None:
            if len(cache) > 24:
                cache.clear()
            cache[k1] = x
    k2 = k1 + (look.hue.tobytes(), look.sat.tobytes(), look.lum.tobytes(), cur_look.hue.tobytes(),
               cur_look.sat.tobytes(), cur_look.lum.tobytes(), k.hue_frac, k.lum_k, k.sat_gain)
    y = cache.get(k2) if cache is not None else None
    if y is None:
        y = _apply_hsl(x, cur_look, look, k)
        if cache is not None:
            cache[k2] = y
    return _apply_grade(y, cur_look, look, k)


def _stage_tone(px, cur_look, cur_shot, look, shot, k):
    x = _lut(px, _inv_lut(cur_look.curve))                      # gỡ curve đang có
    lin = _to_lin(x)
    if (shot.mired is not None and cur_shot.mired is not None
            and (abs(shot.mired - cur_shot.mired) > 1e-9 or abs((shot.tint or 0) - (cur_shot.tint or 0)) > 1e-9)):
        # Camera Raw "giả định" nguồn sáng mới -> bù màu theo tỉ lệ đèn cũ / đèn mới
        m_new = cur_shot.mired + k.wb_gain * (shot.mired - cur_shot.mired)
        t_new = (cur_shot.tint or 0.0) + k.wb_gain * ((shot.tint or 0.0) - (cur_shot.tint or 0.0))
        src = color.temp_tint_to_xy(1e6 / max(m_new, 15.0), t_new)
        dst = color.temp_tint_to_xy(1e6 / cur_shot.mired, cur_shot.tint or 0.0)
        M = color._M_XYZ_RGB @ _bradford(src, dst) @ color._M_RGB_XYZ
        lin = lin @ M.T
    dev = shot.ev - cur_shot.ev
    if dev:
        lin = lin * (2.0 ** dev)
    return _lut(_to_srgb(lin), curve_lut(look.curve))           # curve mới


def _apply_hsl(x: np.ndarray, cur: Look, new: Look, k: Consts) -> np.ndarray:
    dH = new.hue - cur.hue
    ratio = (1 + new.sat / 100) / np.maximum(1 + cur.sat / 100, 0.01)
    dL = new.lum - cur.lum
    if not (np.any(dH) or np.any(ratio != 1) or np.any(dL)):
        return x
    hsv = color.rgb_to_hsv(x)
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    w = band_weights(h)
    span = np.where(dH >= 0, _NEXT, _PREV) * k.hue_frac
    h2 = (h + w @ (dH / 100 * span)) % 360
    s2 = np.clip(s * (w @ ratio) ** k.sat_gain, 0, 1)
    rgb = color.hsv_to_rgb(np.stack([h2, s2, v], axis=-1))
    if np.any(dL):
        lab = _lab(rgb)
        lab[:, 0] += (w @ (dL / 100 * k.lum_k)) * s
        rgb = _from_lab(lab)
    return np.clip(rgb, 0, 1)


def _apply_grade(x: np.ndarray, cur: Look, new: Look, k: Consts) -> np.ndarray:
    dG = new.grade - cur.grade
    sr = (1 + new.saturation / 100) / max(1 + cur.saturation / 100, 0.01)
    vr = (new.vibrance - cur.vibrance) / 100
    if not (np.any(dG) or sr != 1 or vr):
        return x
    lab = _lab(x)
    t = np.clip(lab[:, 0] / 100, 0, 1)
    ws, wm, wh = (1 - t) ** 2, 2 * t * (1 - t), t * t
    lab[:, 1] += ws * dG[0] + wm * dG[2] + wh * dG[4]
    lab[:, 2] += ws * dG[1] + wm * dG[3] + wh * dG[5]
    C = np.hypot(lab[:, 1], lab[:, 2])
    f = sr * (1 + vr * (1 - np.clip(C / 60, 0, 1)))
    lab[:, 1:] *= np.maximum(f, 0)[:, None]
    return _from_lab(lab)


# ------------------------------------------------------------------ lấy mẫu + đo
@dataclass
class Sample:
    px: np.ndarray     # N x 3 sRGB 0..1
    reg: np.ndarray    # N mã vùng
    glob: np.ndarray   # chỉ số các điểm lấy đều cả ảnh (cho tông sáng tối)


def make_sample(img_u8: np.ndarray, regmap: np.ndarray | None, n_glob: int = 3000,
                n_reg: int = 600, seed: int = 0) -> tuple[Sample, np.ndarray]:
    """Lấy mẫu điểm ảnh: đều cả ảnh + bổ sung cho các vùng nhỏ (da, trời...).
    Trả thêm vị trí các điểm để lấy lại đúng chỗ đó trên ảnh dựng lần sau."""
    flat = img_u8.reshape(-1, 3)
    reg = regmap.reshape(-1).astype(np.uint8) if regmap is not None else np.zeros(len(flat), np.uint8)
    rng = np.random.default_rng(seed)
    g = rng.choice(len(flat), min(n_glob, len(flat)), replace=False)
    parts = [g]
    for code in (SKIN, SKY, PLANT, CLOTHES):
        where = np.flatnonzero(reg == code)
        if len(where):
            parts.append(rng.choice(where, min(n_reg, len(where)), replace=False))
    idx = np.concatenate(parts)
    s = Sample(flat[idx].astype(np.float64) / 255.0, reg[idx], np.arange(len(g)))
    return s, idx


def sample_at(img_u8: np.ndarray, idx: np.ndarray, base: Sample) -> Sample:
    return Sample(img_u8.reshape(-1, 3)[idx].astype(np.float64) / 255.0, base.reg, base.glob)


@dataclass
class Sel:
    """Những điểm ảnh dùng để đo — chọn MỘT lần trên ảnh gốc, giữ cố định khi chỉnh,
    để số đo thay đổi liên tục theo thanh trượt."""
    glob: np.ndarray
    neutral: np.ndarray | None = None
    skin: np.ndarray | None = None
    sky: np.ndarray | None = None
    plant: np.ndarray | None = None
    shadow: np.ndarray | None = None
    high: np.ndarray | None = None
    bands: list = field(default_factory=lambda: [None] * 8)
    band_share: np.ndarray = field(default_factory=lambda: np.zeros(8))
    frac: dict = field(default_factory=dict)

    def found(self) -> list[str]:
        names = {"skin": "da", "neutral": "trắng/trung tính", "sky": "trời", "plant": "cây"}
        return [vi for k, vi in names.items() if getattr(self, k) is not None]


def _ok(idx: np.ndarray, n: int = 50) -> np.ndarray | None:
    return idx if len(idx) >= n else None


def select(s: Sample) -> Sel:
    lab = color.srgb_to_lab(s.px)
    L, ab = lab[:, 0], lab[:, 1:]
    C = np.hypot(ab[:, 0], ab[:, 1])
    reg = s.reg
    g = np.zeros(len(L), bool)
    g[s.glob] = True
    sel = Sel(glob=s.glob)
    n_glob = max(len(s.glob), 1)
    for code, name in ((SKIN, "skin"), (SKY, "sky"), (PLANT, "plant"), (CLOTHES, "clothes"), (PERSON, "person")):
        sel.frac[name] = float(np.mean(reg[s.glob] == code)) if len(s.glob) else 0.0
    valid = (L > 3) & (L < 99)
    # Ám màu chung của ảnh (để tìm điểm "trắng/xám" kể cả khi ảnh đang ám vàng)
    cand = valid & np.isin(reg, [OTHER, CLOTHES, PERSON]) & (L > 30) & (L < 97)
    cast = np.zeros(2)
    if cand.sum() >= 50:
        low = cand & (C <= np.percentile(C[cand], 50))
        cast = np.median(ab[low], axis=0)
        Cc = np.hypot(*(ab - cast).T)
        thr = max(4.0, float(np.percentile(Cc[cand], 20)))
        sel.neutral = _ok(np.flatnonzero(cand & (Cc <= thr)))
    else:
        Cc = C
    skin = np.flatnonzero((reg == SKIN) & (L > 25) & (L < 95))
    if len(skin) >= 60:
        d = np.hypot(*(ab[skin] - np.median(ab[skin], axis=0)).T)
        sel.skin = skin[d <= np.percentile(d, 80)]
    sel.sky = _ok(np.flatnonzero((reg == SKY) & (L > 35) & (L < 99)), 60)
    sel.plant = _ok(np.flatnonzero((reg == PLANT) & valid), 60)
    gv = np.flatnonzero(g & valid & ~np.isin(reg, [SKIN, HAIR]))
    if len(gv) >= 200:
        sel.shadow = _ok(gv[L[gv] <= np.percentile(L[gv], 12)])
    gh = np.flatnonzero(g & valid & (reg != SKY) & (L < 98.5))
    if len(gh) >= 200:
        sel.high = _ok(gh[L[gh] >= np.percentile(L[gh], 88)])
    # 8 dải màu cho các vật thể khác (hoa, đồ trang trí, quần áo màu...)
    other = np.flatnonzero(g & valid & np.isin(reg, [OTHER, CLOTHES, PERSON, WATER]) & (Cc > 12) & (L > 15) & (L < 95))
    if len(other):
        hue = color.rgb_to_hsv(s.px[other])[:, 0]
        band = np.argmax(band_weights(hue), axis=1)
        for b in range(8):
            idx = other[band == b]
            share = len(idx) / n_glob
            if share >= 0.015 and len(idx) >= 60:
                sel.bands[b], sel.band_share[b] = idx, share
    return sel


def measure(px: np.ndarray, sel: Sel) -> dict:
    lab = _lab(px)
    m = {"tone": np.percentile(lab[sel.glob, 0], TONE_Q)}
    if sel.neutral is not None:
        m["neutral"] = lab[sel.neutral, 1:].mean(axis=0)
    for name in ("skin", "sky", "plant"):
        idx = getattr(sel, name)
        if idx is not None:
            m[name] = lab[idx].mean(axis=0)
    for name in ("shadow", "high"):
        idx = getattr(sel, name)
        if idx is not None:
            m[name] = lab[idx, 1:].mean(axis=0)
    for b, idx in enumerate(sel.bands):
        if idx is not None:
            m[f"band{b}"] = lab[idx].mean(axis=0)
    return m


_W = {"tone": np.full(5, 0.7), "neutral": np.array([3.0, 3.0]), "skin": np.array([1.5, 3.0, 3.0]),
      "sky": np.array([1.2, 1.5, 1.5]), "plant": np.array([1.0, 1.5, 1.5]),
      "shadow": np.array([1.0, 1.0]), "high": np.array([1.0, 1.0])}


def diff(m: dict, ref: dict, sel: Sel, ref_sel: Sel) -> np.ndarray:
    parts = []
    for key in sorted(set(m) & set(ref)):
        if key.startswith("band"):
            b = int(key[4:])
            w = 0.8 * min(1.0, math.sqrt(min(sel.band_share[b], ref_sel.band_share[b]) / 0.05)) * np.array([0.5, 1, 1])
        else:
            w = _W[key]
        parts.append(w * (m[key] - ref[key]))
    return np.concatenate(parts) if parts else np.zeros(0)


def report(stats: list[dict], ref: dict) -> dict:
    """Số đo dễ hiểu: ΔE2000 từng vùng, lệch màu trắng, lệch tông sáng tối."""
    out = {}
    for key, name in (("skin", "da"), ("sky", "trời"), ("plant", "cây")):
        v = [float(color.delta_e2000(m[key], ref[key])) for m in stats if key in m and key in ref]
        if v:
            out[name] = float(np.mean(v))
    v = [float(np.hypot(*(m["neutral"] - ref["neutral"]))) for m in stats if "neutral" in m and "neutral" in ref]
    if v:
        out["trắng"] = float(np.mean(v))
    out["sáng tối"] = float(np.mean([np.mean(np.abs(m["tone"] - ref["tone"])) for m in stats]))
    return out


def fmt_report(r: dict) -> str:
    parts = []
    for k in ("da", "trắng", "trời", "cây"):
        if k in r:
            parts.append(f"{k} ΔE {r[k]:.1f}")
    parts.append(f"sáng tối ΔL {r['sáng tối']:.1f}")
    return ", ".join(parts)


# ------------------------------------------------------------------ tối ưu
@dataclass
class Rep:
    """Một ảnh đại diện đang được tối ưu."""
    px: np.ndarray
    sel: Sel
    cur_look: Look
    cur_shot: Shot


def _layout(shots: list[Shot]) -> list[tuple[int, int, bool]]:
    """Vị trí biến riêng của từng ảnh trong vector: (bắt đầu, số biến, có WB)."""
    out, pos = [], N_SHARED
    for s in shots:
        wb = s.mired is not None and s.tint is not None
        n = 3 if wb else 1
        out.append((pos, n, wb))
        pos += n
    return out


def _pack(lk: Look, shots: list[Shot]) -> np.ndarray:
    v = [_shared_vec(lk)]
    for s in shots:
        v.append([s.mired, s.tint, s.ev] if (s.mired is not None and s.tint is not None) else [s.ev])
    return np.concatenate(v).astype(float)


def _unpack(x: np.ndarray, shots: list[Shot]) -> tuple[Look, list[Shot]]:
    lk = _shared_from(x[:N_SHARED])
    out = []
    for (p, n, wb), s in zip(_layout(shots), shots):
        out.append(Shot(float(x[p]), float(x[p + 1]), float(x[p + 2])) if wb else Shot(s.mired, s.tint, float(x[p])))
    return lk, out


def _bounds(shots: list[Shot]):
    lo = [0] + [SLOPE_LO] * 5 + [-100] * 24 + [-40] * 6 + [-100, -100]
    hi = [50] + [SLOPE_HI] * 5 + [100] * 24 + [40] * 6 + [100, 100]
    sc = [10] + [0.2] * 5 + [30] * 24 + [8] * 6 + [20, 20]
    for _, n, wb in _layout(shots):
        if wb:
            lo += [20, -150, -4]
            hi += [500, 150, 4]
            sc += [30, 10, 0.5]
        else:
            lo += [-4]
            hi += [4]
            sc += [0.5]
    return np.array(lo, float), np.array(hi, float), np.array(sc, float)


REG = 1.0   # độ "ngại" thay đổi lớn (cao hơn = chỉnh nhẹ tay hơn)


def _regular(lk: Look, shots: list[Shot], priors: list[Shot], reg: float = REG) -> np.ndarray:
    """Phạt thay đổi lớn: mỗi mục ~ 1 đơn vị khi thay đổi bằng 'thang' của nó, ngang 1 ΔE
    khớp màu. Không có phạt này, bộ giải sẽ dùng các giá trị cực đoan bù trừ nhau (khớp
    hoàn hảo trên mô phỏng nhưng hỏng trên Camera Raw thật)."""
    r = [(lk.curve - CURVE_X) / 25 * 0.7, lk.hue / 15, lk.sat / 25, lk.lum / 20,
         lk.grade / 8, [lk.saturation / 20, lk.vibrance / 20],
         np.diff(lk.curve - CURVE_X, 2) / 20 * 0.7]               # curve mượt, không gợn sóng
    r = [np.atleast_1d(np.asarray(a, float)) * reg for a in r]
    for s, p in zip(shots, priors):
        if s.mired is not None and p.mired is not None:
            r.append(np.array([(s.mired - p.mired) / 80 * 0.3, ((s.tint or 0) - (p.tint or 0)) / 40 * 0.3]))
        r.append(np.array([(s.ev - p.ev) / 2.0 * 0.3]))
    return np.concatenate(r)


def _with_offset(m: dict, off: dict | None) -> dict:
    """Số đo mô phỏng + phần chênh 'Camera Raw thật - mô phỏng' (chỉ giữ số đo có cả hai)."""
    if off is None:
        return m
    return {key: m[key] + off[key] for key in off if key in m}


def solve(reps: list[Rep], ref: dict, ref_sel: Sel, look0: Look, shots0: list[Shot],
          priors: list[Shot], k: Consts = DEFAULT, fix_look: bool = False,
          max_nfev: int = 50, offsets: list[dict] | None = None, reg: float = REG,
          prox: tuple | None = None, gmap: tuple | None = None) -> tuple[Look, list[Shot]]:
    """Tìm thông số để số đo (mô phỏng [+ phần bù]) khớp ảnh mẫu.
    offsets: phần chênh "Camera Raw thật - mô phỏng" của từng ảnh (từ vòng kiểm tra).
    prox = (x_ref, mu): "vùng tin cậy" — phạt đi xa x_ref (mu càng lớn bước càng ngắn).
    gmap = (x_goc, g): Camera Raw mạnh/yếu hơn mô phỏng g lần theo từng nhóm thanh trượt:
    giá trị thật x tương đương giá trị mô phỏng x_goc + g * (x - x_goc)."""
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix

    caches = [dict() for _ in reps]
    x0 = _pack(look0, shots0)
    lo, hi, sc = _bounds(shots0)
    if fix_look:   # chỉ chỉnh WB/exposure: khoá phần look bằng khoảng rất hẹp
        lo[:N_SHARED] = x0[:N_SHARED] - 1e-6
        hi[:N_SHARED] = x0[:N_SHARED] + 1e-6
    x0 = np.clip(x0, lo, hi)
    lay = _layout(shots0)

    def fun(x):
        lk_real, sh_real = _unpack(x, shots0)
        xm = x if gmap is None else gmap[0] + gmap[1] * (x - gmap[0])
        lk, shots = _unpack(xm, shots0)
        parts = []
        for i, (r, s) in enumerate(zip(reps, shots)):
            y = simulate(r.px, r.cur_look, r.cur_shot, lk, s, k, caches[i])
            m = _with_offset(measure(y, r.sel), offsets[i] if offsets else None)
            parts.append(diff(m, ref, r.sel, ref_sel))
        out = [np.concatenate(parts), _regular(lk_real, sh_real, priors, reg)]
        if prox is not None:
            out.append(prox[1] * (x - prox[0]) / sc)
        return np.concatenate(out)

    # Ma trận thưa: biến riêng của ảnh i chỉ ảnh hưởng số đo của ảnh i
    sizes = []
    lk0, sh0 = _unpack(x0, shots0)
    for i, (r, s) in enumerate(zip(reps, sh0)):
        m = _with_offset(measure(r.px, r.sel), offsets[i] if offsets else None)
        sizes.append(len(diff(m, ref, r.sel, ref_sel)))
    n_reg = len(_regular(lk0, sh0, priors, reg)) + (len(x0) if prox is not None else 0)
    S = lil_matrix((sum(sizes) + n_reg, len(x0)), dtype=int)
    row = 0
    for (p, n, _), sz in zip(lay, sizes):
        S[row:row + sz, :N_SHARED] = 1
        S[row:row + sz, p:p + n] = 1
        row += sz
    S[row:, :] = 1
    res = least_squares(fun, x0, jac_sparsity=S, bounds=(lo, hi), x_scale=sc, diff_step=1e-3,
                        max_nfev=max_nfev, method="trf")
    lk, shots = _unpack(res.x, shots0)
    lk.curve = fix_curve(lk.curve)
    return lk, shots


# ------------------------------------------------------------------ đổi sang thanh trượt
def _hue_units() -> np.ndarray:
    h = np.arange(360.0)
    rgb = color.hsv_to_rgb(np.stack([h, np.ones(360), np.ones(360)], axis=-1))
    ab = color.srgb_to_lab(rgb)[:, 1:]
    return ab / np.linalg.norm(ab, axis=1, keepdims=True)


_HUE_U = _hue_units()


def grade_to_hs(v, k: Consts = DEFAULT) -> tuple[int, int]:
    v = np.asarray(v, float)
    mag = float(np.hypot(*v))
    if mag < 1e-6:
        return 0, 0
    return int(np.argmax(_HUE_U @ (v / mag))), int(round(min(100.0, mag / k.grade_k * 100)))


def hs_to_grade(h: float, sat: float, k: Consts = DEFAULT) -> np.ndarray:
    return _HUE_U[int(round(h)) % 360] * (sat / 100 * k.grade_k)


def xmp_values(lk: Look, shot: Shot, k: Consts = DEFAULT) -> tuple[dict, list[tuple[int, int]]]:
    v: dict = {}
    if shot.mired is not None and shot.tint is not None:
        v["WhiteBalance"] = "Custom"
        v["Temperature"] = float(np.clip(1e6 / shot.mired, 2000, 50000))
        v["Tint"] = float(np.clip(shot.tint, -150, 150))
    else:
        v["WhiteBalance"] = "As Shot"
    v["Exposure2012"] = float(np.clip(shot.ev, -5, 5))
    for i, b in enumerate(BANDS):
        v[f"HueAdjustment{b}"] = float(lk.hue[i])
        v[f"SaturationAdjustment{b}"] = float(lk.sat[i])
        v[f"LuminanceAdjustment{b}"] = float(lk.lum[i])
    # Camera Raw lưu bánh xe Color Grading vùng tối/sáng bằng tên cũ "SplitToning..."
    sh, ss = grade_to_hs(lk.grade[0:2], k)
    mh, ms = grade_to_hs(lk.grade[2:4], k)
    hh, hs = grade_to_hs(lk.grade[4:6], k)
    v.update({"SplitToningShadowHue": sh, "SplitToningShadowSaturation": ss,
              "ColorGradeMidtoneHue": mh, "ColorGradeMidtoneSat": ms,
              "SplitToningHighlightHue": hh, "SplitToningHighlightSaturation": hs,
              "SplitToningBalance": 0, "ColorGradeBlending": 50,
              "Saturation": float(lk.saturation), "Vibrance": float(lk.vibrance)})
    curve = [(int(x), int(round(y))) for x, y in zip(CURVE_X, fix_curve(lk.curve))]
    return v, curve


def from_crs(crs: dict, curve: list[tuple[int, int]] | None, k: Consts = DEFAULT) -> tuple[Look, Shot]:
    """Ngược của xmp_values (để kiểm tra / để mô phỏng Camera Raw trong test)."""
    def num(key, d=0.0):
        try:
            return float(crs.get(key, d))
        except (TypeError, ValueError):
            return d
    lk = Look()
    lk.hue = np.array([num(f"HueAdjustment{b}") for b in BANDS])
    lk.sat = np.array([num(f"SaturationAdjustment{b}") for b in BANDS])
    lk.lum = np.array([num(f"LuminanceAdjustment{b}") for b in BANDS])
    lk.grade = np.concatenate([
        hs_to_grade(num("SplitToningShadowHue"), num("SplitToningShadowSaturation"), k),
        hs_to_grade(num("ColorGradeMidtoneHue"), num("ColorGradeMidtoneSat"), k),
        hs_to_grade(num("SplitToningHighlightHue"), num("SplitToningHighlightSaturation"), k)])
    lk.saturation, lk.vibrance = num("Saturation"), num("Vibrance")
    if curve:
        pts = np.array(curve, float)
        lk.curve = np.interp(CURVE_X, pts[:, 0], pts[:, 1])
    temp = num("Temperature", 0.0)
    shot = Shot(1e6 / temp if temp > 0 else None, num("Tint") if temp > 0 else None, num("Exposure2012"))
    return lk, shot


# ------------------------------------------------------------------ chuyển sang cả lô
def appearance(preview_u8: np.ndarray) -> tuple[float, float]:
    """Điểm trắng/xám trong ảnh xem trước trông như ánh sáng bao nhiêu (mired, tint)."""
    from .raw import neutral_xy
    lin = color.srgb_to_linear(preview_u8.reshape(-1, 3).astype(np.float64) / 255.0)
    t, tint = color.xy_to_temp_tint(*neutral_xy(lin))
    return 1e6 / t, tint


def brightness(preview_u8: np.ndarray, regmap: np.ndarray | None = None) -> float:
    """Độ sáng 'mốc' của ảnh: da người nếu có (thợ chụp đo sáng theo da), không thì trung vị."""
    lin = color.srgb_to_linear(preview_u8.reshape(-1, 3).astype(np.float64) / 255.0)
    y = lin @ np.array([0.2126, 0.7152, 0.0722])
    if regmap is not None:
        skin = regmap.reshape(-1) == SKIN
        if skin.mean() > 0.005:
            return float(np.median(y[skin])) + 1e-4
    return float(np.median(y)) + 1e-4


def transfer(rep_shot: Shot, app_r, app_j, cam_r: Shot, cam_j: Shot, y_r: float, y_j: float) -> Shot:
    """Chuyển WB/exposure đã khớp của ảnh đại diện r sang ảnh j cùng cảnh.

    Giữ cho điểm trắng của j trông giống điểm trắng của r sau khi dựng:
      mired_j = mired_r + (vẻ_j - vẻ_r) + (asShot_j - asShot_r)
    (cùng ánh sáng, chỉ khác cân bằng trắng tự động của máy => ra cùng mired)."""
    ev = rep_shot.ev + float(np.clip(math.log2(y_r / y_j), -2.5, 2.5))
    if rep_shot.mired is None or cam_j.mired is None or cam_r.mired is None:
        return Shot(cam_j.mired, cam_j.tint, ev)
    m = rep_shot.mired + (app_j[0] - app_r[0]) + (cam_j.mired - cam_r.mired)
    t = rep_shot.tint + (app_j[1] - app_r[1]) + ((cam_j.tint or 0) - (cam_r.tint or 0))
    return Shot(float(np.clip(m, 20, 500)), float(np.clip(t, -150, 150)), ev)


def pick_reps(times: list[float], scenes: list[int], embs: np.ndarray | None, clip_hi: list[float],
              max_reps: int, force: list[int] = ()) -> tuple[list[int], list[int]]:
    """Mỗi cảnh lớn chọn 1 ảnh đại diện (gần 'trung bình' cảnh nhất, không cháy sáng).
    Cảnh còn lại dùng ảnh đại diện giống nó nhất. -> (reps, rep_of_mỗi_ảnh)"""
    groups: dict[int, list[int]] = {}
    for i, s in enumerate(scenes):
        groups.setdefault(s, []).append(i)
    order = sorted(groups, key=lambda s: -len(groups[s]))
    reps = list(dict.fromkeys(force))
    for s in order:
        if len(reps) >= max_reps:
            break
        idx = groups[s]
        if any(r in idx for r in reps):
            continue
        if embs is not None:
            c = embs[idx].mean(axis=0)
            score = -(embs[idx] @ c) + np.array([clip_hi[i] for i in idx]) * 2
        else:
            tm = np.median([times[i] for i in idx])
            score = np.array([abs(times[i] - tm) for i in idx], float) + np.array([clip_hi[i] for i in idx]) * 1e6
        reps.append(idx[int(np.argmin(score))])
    rep_of = []
    rep_scene = {scenes[r]: r for r in reps}
    for i in range(len(scenes)):
        if scenes[i] in rep_scene:
            rep_of.append(rep_scene[scenes[i]])
        elif embs is not None:
            rep_of.append(reps[int(np.argmax([embs[i] @ embs[r] for r in reps]))])
        else:
            rep_of.append(reps[int(np.argmin([abs(times[i] - times[r]) for r in reps]))])
    return reps, rep_of


def _wkey(key: str) -> np.ndarray:
    return 0.8 * np.array([0.5, 1, 1]) if key.startswith("band") else _W[key]


def _predict(reps: list[Rep], lk: Look, shots: list[Shot], k: Consts, caches=None) -> list[dict]:
    return [measure(simulate(r.px, r.cur_look, r.cur_shot, lk, s, k, caches[i] if caches else None), r.sel)
            for i, (r, s) in enumerate(zip(reps, shots))]


def _restrict(m: dict, keys: set) -> dict:
    return {key: m[key] for key in keys if key in m}


GROUPS = ["curve", "hue", "sat", "lum", "grade", "satvib", "mired", "tint", "ev"]


def _group_index(shots: list[Shot]) -> np.ndarray:
    """Nhóm (chỉ số trong GROUPS) của từng phần tử trong vector thông số."""
    g = [0] * 6 + [1] * 8 + [2] * 8 + [3] * 8 + [4] * 6 + [5] * 2
    for _, n, wb in _layout(shots):
        g += [6, 7, 8] if wb else [8]
    return np.array(g)


def _ident(shots: list[Shot], priors: list[Shot]) -> np.ndarray:
    """Điểm 'không chỉnh gì': curve thẳng, HSL/grading 0, WB = As Shot, exposure 0."""
    return _pack(Look(), [Shot(p.mired, p.tint, 0.0) if s.mired is not None else Shot(s.mired, s.tint, 0.0)
                          for s, p in zip(shots, priors)])


def learn_gains(reps: list[Rep], keys, ref, ref_sel, ident, gidx, tmpl, tried, k: Consts) -> np.ndarray:
    """Học độ mạnh thật của từng nhóm thanh trượt từ các lần dựng: thay đổi số đo mô phỏng
    (theo thông số đã quy đổi) phải giống thay đổi số đo THẬT."""
    from scipy.optimize import least_squares
    caches = [dict() for _ in reps]

    def rmod(xm):
        lk, sh = _unpack(xm, tmpl)
        pred = _predict(reps, lk, sh, k, caches)
        return np.concatenate([diff(_restrict(p, kk), ref, r.sel, ref_sel) for p, kk, r in zip(pred, keys, reps)])

    def res(g):
        G = g[gidx]
        out = []
        for (xa, ra), (xb, rb) in tried:
            pa = rmod(ident + G * (xa - ident))
            pb = rmod(ident + G * (xb - ident))
            out.append((pb - pa) - (rb - ra))
        out.append((np.log(g)) * 1.5)       # ít bằng chứng thì giữ gần 1
        return np.concatenate(out)
    r = least_squares(res, np.ones(len(GROUPS)), bounds=(0.3, 3.0), diff_step=1e-3, max_nfev=20)
    return r.x


def refine(reps: list[Rep], render, segment, ref: dict, ref_sel: Sel, look0: Look, shots0: list[Shot],
           priors: list[Shot], iters: int = 3, log=print, k: Consts = DEFAULT, tol: float = 0.8,
           reg_schedule=(0.6, 0.3, 0.15)):
    """VÒNG KIỂM TRA: Camera Raw (render) dựng ảnh -> đo -> sửa -> dựng lại.

    reps: các ảnh đại diện ở dạng ảnh xem trước (gốc của mô phỏng).
    reg_schedule: mức phạt thay đổi lớn ở từng lượt — nới dần, vì giờ đã có số đo THẬT
    canh chừng (mô phỏng sai thì lượt đó bị bỏ), nên được phép chỉnh mạnh hơn để khớp đúng.
    Mỗi lượt:
      1. Đo ảnh Camera Raw dựng THẬT. Lượt nào tệ hơn lượt tốt nhất thì bỏ và siết "vùng
         tin cậy" (bước sau ngắn hơn); lượt tốt thì nới ra.
      2. Học ĐỘ MẠNH THẬT của từng nhóm thanh trượt (Camera Raw mạnh/yếu hơn mô phỏng bao
         nhiêu lần) từ các cặp lần dựng.
      3. Giải lại trên mô phỏng đã quy đổi độ mạnh + phần bù "thật - mô phỏng" tại điểm tốt nhất.
    Khi hội tụ, ảnh THẬT khớp ảnh mẫu kể cả khi mô phỏng sai về độ mạnh từng thanh trượt.
    render(i, look, shot) -> ảnh uint8; segment(ảnh) -> bản đồ vùng (hoặc None).
    Trả về (look, shots, điểm lệch, báo cáo) tốt nhất + lịch sử."""
    n = len(reps)
    tmpl = [replace(s) for s in shots0]
    x_try = _pack(look0, shots0)
    bases, history, keys = [], [], None
    best, mu = None, 0.6
    gidx, gains, seen = _group_index(tmpl), np.ones(len(GROUPS)), []

    for it in range(iters + 1):
        lk_t, sh_t = _unpack(x_try, tmpl)
        lk_t.curve = fix_curve(lk_t.curve)
        x_try = _pack(lk_t, sh_t)
        imgs = [render(i, lk_t, sh_t[i]) for i in range(n)]
        act = []
        for i, img in enumerate(imgs):
            if len(bases) <= i:
                s, idx = make_sample(img, segment(img) if segment else None, seed=i)
                bases.append((idx, s, select(s)))
            idx, s0, sel = bases[i]
            act.append(measure(sample_at(img, idx, s0).px, sel))
        sels = [b[2] for b in bases]
        score = float(np.sqrt(np.mean(np.concatenate([diff(m, ref, sel, ref_sel) for m, sel in zip(act, sels)]) ** 2)))
        rp = report(act, ref)
        history.append((score, rp))
        if keys is None:   # số đo có cả trên ảnh thật lẫn mô phỏng
            pred0 = _predict(reps, lk_t, sh_t, k)
            keys = [set(a) & set(p) for a, p in zip(act, pred0)]
        r_act = np.concatenate([diff(_restrict(a, kk), ref, r.sel, ref_sel) for a, kk, r in zip(act, keys, reps)])
        seen.append((x_try.copy(), r_act))
        if best is None or score < best["score"]:
            best = {"x": x_try.copy(), "act": act, "r_act": r_act, "score": score, "report": rp, "imgs": imgs}
            mu = mu if it == 0 else max(0.05, mu * 0.5)     # tốt -> nới vùng tin cậy
            mark = ""
        else:
            mu *= 4.0                                       # tệ -> siết lại, bước ngắn hơn
            mark = "  (tệ hơn -> bỏ, thử bước ngắn hơn)"
        log(f"    lượt {it + 1}: {fmt_report(rp)}{mark}")
        if it == iters or best["score"] < tol:
            break
        ident = _ident(tmpl, priors)
        if len(seen) >= 2:   # học độ mạnh thật của từng nhóm thanh trượt
            pairs = [((xa, ra), (xb, rb)) for (xa, ra), (xb, rb) in zip(seen[:-1], seen[1:])]
            pairs += [((best["x"], best["r_act"]), s_) for s_ in seen if s_[0] is not best["x"]][-2:]
            gains = learn_gains(reps, keys, ref, ref_sel, ident, gidx, tmpl, pairs, k)
            log("      độ mạnh Camera Raw / mô phỏng: " + ", ".join(
                f"{n} {g:.2f}" for n, g in zip(GROUPS, gains) if abs(g - 1) > 0.15))
        G = gains[gidx]
        xm_best = ident + G * (best["x"] - ident)
        lk_m, sh_m = _unpack(xm_best, tmpl)
        pred_b = _predict(reps, lk_m, sh_m, k)
        offs = [{key: a[key] - p[key] for key in kk} for a, p, kk in zip(best["act"], pred_b, keys)]
        reg = reg_schedule[min(it, len(reg_schedule) - 1)]
        lk_b, sh_b = _unpack(best["x"], tmpl)
        lk_p, sh_p = solve(reps, ref, ref_sel, lk_b, sh_b, priors, k, offsets=offs, reg=reg,
                           prox=(best["x"], mu), gmap=(ident, G))
        x_try = _pack(lk_p, sh_p)
    lk_b, sh_b = _unpack(best["x"], tmpl)
    return (lk_b, sh_b, best["score"], best["report"], best["imgs"]), history


# ------------------------------------------------------------------ cả quy trình
@dataclass
class Reference:
    sample: Sample
    sel: Sel
    stats: dict
    emb: np.ndarray | None = None


def load_ref_image(path, max_side: int = 1024) -> np.ndarray:
    """Đọc ảnh mẫu, xoay đúng chiều, ĐỔI ĐÚNG HỒ SƠ MÀU (Display P3, AdobeRGB...) về sRGB."""
    import io
    from PIL import Image, ImageCms, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        if im.mode not in ("RGB", "CMYK"):
            im = im.convert("RGB")
        if icc:
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:
                pass
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        return np.asarray(im)


def load_reference(paths, segment=None, embedder=None) -> Reference:
    """Một hoặc vài ảnh mẫu -> gộp thành một bộ số đo."""
    pxs, regs, globs, n0, imgs = [], [], [], 0, []
    for i, p in enumerate(paths):
        img = load_ref_image(p)
        imgs.append(img)
        s, _ = make_sample(img, segment(img) if segment else None, seed=100 + i)
        pxs.append(s.px)
        regs.append(s.reg)
        globs.append(s.glob + n0)
        n0 += len(s.px)
    s = Sample(np.concatenate(pxs), np.concatenate(regs), np.concatenate(globs))
    sel = select(s)
    emb = None
    if embedder is not None:
        e = embedder.embed(imgs).mean(axis=0)
        emb = e / np.linalg.norm(e)
    return Reference(s, sel, measure(s.px, sel), emb)


def copy_color(photos, scenes: list[int], embs, ref: Reference, segment=None, log=print,
               render_factory=None, max_reps: int = 6, iters: int = 5, seg_all: bool = False):
    """Chép màu ảnh mẫu cho cả lô.
    photos: các raw.Photo (có preview, as_shot_temp/tint, time).
    render_factory(reps) -> render(i, look, shot) (dựng bằng Camera Raw thật), hoặc None.
    -> (look dùng chung, shot từng ảnh, thông tin)"""
    n = len(photos)
    times = [p.time or 0.0 for p in photos]
    clip_hi = [float(np.mean(p.preview.reshape(-1, 3).max(axis=1) > 250)) for p in photos]
    force, info = [], {}
    if embs is not None and ref.emb is not None:
        sims = embs @ ref.emb
        j = int(np.argmax(sims))
        if sims[j] > 0.93:
            force = [j]
            log(f"Ảnh mẫu có vẻ là bản đã chỉnh của {Path(photos[j].path).name} -> dùng ảnh này để khớp.")
    reps, rep_of = pick_reps(times, scenes, embs, clip_hi, max_reps, force)
    regmaps = {}
    for i in (range(n) if seg_all else reps):
        regmaps[i] = segment(photos[i].preview) if segment else None
    cams = [Shot(1e6 / p.as_shot_temp if p.as_shot_temp else None,
                 p.as_shot_tint if p.as_shot_temp else None, 0.0) for p in photos]
    rep_objs = []
    for j, i in enumerate(reps):
        s, _ = make_sample(photos[i].preview, regmaps.get(i), seed=j)
        rep_objs.append(Rep(s.px, select(s), Look(), cams[i]))
        found = rep_objs[-1].sel.found()
        log(f"  ảnh đại diện {j + 1}: {Path(photos[i].path).name}"
            + (f" (thấy: {', '.join(found)})" if found else ""))
    priors = [replace(cams[i]) for i in reps]
    log("Đang tìm thông số (mô phỏng)...")
    lk, shots = solve(rep_objs, ref.stats, ref.sel, Look(), [replace(c) for c in priors], priors)
    pred = [measure(simulate(r.px, r.cur_look, r.cur_shot, lk, s), r.sel) for r, s in zip(rep_objs, shots)]
    info["mo_phong"] = report(pred, ref.stats)
    log(f"  trên mô phỏng: {fmt_report(info['mo_phong'])}")
    if render_factory is not None:
        log(f"Kiểm tra bằng Camera Raw thật ({len(reps)} ảnh x tối đa {iters + 1} lượt):")
        best, hist = refine(rep_objs, render_factory([photos[i] for i in reps]), segment,
                            ref.stats, ref.sel, lk, shots, priors, iters=iters, log=log)
        lk, shots = best[0], best[1]
        info["camera_raw"], info["lich_su"] = best[3], hist
        info["anh_dai_dien"] = [{"ten": Path(photos[i].path).name, "goc": photos[i].preview, "ket_qua": img,
                                 "nguon": "camera_raw"} for i, img in zip(reps, best[4])]
    else:   # xem trước bằng mô phỏng (gần đúng)
        info["anh_dai_dien"] = []
        for j, i in enumerate(reps):
            pv = photos[i].preview
            y = simulate(pv.reshape(-1, 3) / 255.0, Look(), cams[i], lk, shots[j])
            info["anh_dai_dien"].append({"ten": Path(photos[i].path).name, "goc": pv, "nguon": "mo_phong",
                                         "ket_qua": (np.clip(y, 0, 1).reshape(pv.shape) * 255 + 0.5).astype(np.uint8)})
    rep_shot = {i: shots[j] for j, i in enumerate(reps)}
    apps = {i: appearance(photos[i].preview) for i in range(n)}
    ys = {i: brightness(photos[i].preview, regmaps.get(i)) for i in range(n)}
    out = []
    for i in range(n):
        r = rep_of[i]
        out.append(rep_shot[r] if i == r else transfer(rep_shot[r], apps[r], apps[i], cams[r], cams[i], ys[r], ys[i]))
    # Ổn định cân bằng trắng trong cùng cảnh (đo điểm trắng từng ảnh có nhiễu)
    for sc in set(scenes):
        idx = [i for i in range(n) if scenes[i] == sc and out[i].mired is not None and i not in rep_shot]
        if len(idx) >= 3:
            mm = float(np.median([out[i].mired for i in idx]))
            tm = float(np.median([out[i].tint for i in idx]))
            for i in idx:
                out[i] = Shot(0.5 * out[i].mired + 0.5 * mm, 0.5 * out[i].tint + 0.5 * tm, out[i].ev)
    info["reps"], info["rep_of"] = reps, rep_of
    return lk, out, info
