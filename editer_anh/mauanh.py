"""CHÉP MÀU KIỂU EVOTO: đổi màu TRỰC TIẾP trên điểm ảnh theo ảnh mẫu, riêng từng vùng, rồi xuất ảnh thành phẩm.

Khác chép màu bằng thanh trượt Lightroom: một thanh "Đỏ" của Lightroom đổi cả da lẫn áo đỏ (lỗi thật 0.13.0: da
cam, áo chói). Ở đây mỗi màu được ánh xạ riêng theo cả độ sáng, độ đậm lẫn tông bằng một bảng màu 3D (3D LUT)
trơn — da (sáng, nhạt) và áo dài đỏ (tối, đậm) là hai điểm khác nhau trong bảng nên không kéo nhau — và da được
chỉnh thêm qua lớp mặt nạ da làm mềm.

Một ảnh:
1. Phân vùng (AI của tool): da / mặt, tóc, áo, trời, cây, còn lại.
2. "Gu" chung của ảnh mẫu (G): cân bằng trắng theo đồ trắng, đường cong sáng tối (điểm đen, điểm trắng, độ sáng
   da), độ rực, ám màu vùng tối / vùng sáng.
3. Vùng có ở CẢ HAI ảnh (da, cây, trời cùng kiểu) khớp cả phân bố màu theo ảnh mẫu (biến đổi MKL).
   Áo: giữ tông màu thật của áo; cùng tông với áo mẫu thì chỉ theo một phần độ sáng / độ đậm.
4. Gom thành MỘT bảng màu 3D trơn (17x17x17); màu ảnh mẫu không cho biết (hoa tím, đồ trang trí...) theo "gu".
5. Áp lên ảnh đầy đủ độ phân giải (Pillow Color3DLUT) + lớp da qua mặt nạ mềm; giữ EXIF / ICC.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import color
from .look import CLOTHES, FACE, HAIR, OTHER, PERSON, PLANT, SKIN, SKY, _band_of, sky_mismatch

ANALYSIS = 768          # cạnh dài ảnh dùng để phân tích (nhanh; bảng màu áp lên ảnh đầy đủ)
N_LUT = 17              # số nút mỗi chiều của bảng màu 3D
SKIN_SET = (FACE, SKIN)
NEUTRAL_SET = (OTHER, CLOTHES, PERSON)


# ------------------------------------------------------------------ đọc / ghi ảnh
def load_rgb(path) -> tuple:
    """Ảnh -> (ảnh RGB sRGB đã xoay đứng, thông tin để ghi lại: exif, icc)."""
    from PIL import Image, ImageCms, ImageOps
    im = Image.open(path)
    im.load()
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)               # điểm ảnh xoay đúng chiều, EXIF hướng = 1
    exif = im.info.get("exif") or (im.getexif().tobytes() if im.getexif() else b"")
    if im.mode != "RGB":
        im = im.convert("RGB")
    if icc:
        try:   # ảnh không phải sRGB (Adobe RGB, P3...) -> đổi về sRGB để bảng màu đúng
            import io
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            name = (ImageCms.getProfileDescription(src) or "").lower()
            if "srgb" not in name:
                im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
                icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        except Exception:
            pass
    return im, {"exif": exif, "icc": icc}


def small(im, side: int = ANALYSIS) -> np.ndarray:
    from PIL import Image
    s = im.copy()
    s.thumbnail((side, side), Image.LANCZOS)
    return np.asarray(s.convert("RGB"))


def save(im, path, meta: dict, quality: int = 95) -> None:
    kw = {"quality": int(quality), "subsampling": 0, "optimize": True}
    if meta.get("exif"):
        kw["exif"] = meta["exif"]
    if meta.get("icc"):
        kw["icc_profile"] = meta["icc"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp.jpg")
    im.save(tmp, "JPEG", **kw)
    tmp.replace(path)


# ------------------------------------------------------------------ thống kê màu một ảnh
@dataclass
class Mau:
    """Thống kê màu của một ảnh (đã phân vùng), trong Lab."""
    reg: dict = field(default_factory=dict)        # tên vùng -> (trung bình 3, hiệp phương sai 3x3, số điểm)
    Lq: np.ndarray | None = None                   # độ sáng L ở các phân vị Q
    neutral: np.ndarray | None = None              # ám màu (a, b) của đồ trắng/xám, quy về L=70
    white_L: float | None = None                   # đồ trắng sáng cỡ nào
    shadow: np.ndarray | None = None               # ám màu (a, b) vùng tối gần như không màu
    high: np.ndarray | None = None                 # ám màu (a, b) vùng sáng gần như không màu
    chroma: float | None = None                    # độ đậm (C) giữa của các màu không phải da / trắng
    emb: np.ndarray | None = None                  # "dấu vân tay" nội dung (CLIP), chọn ảnh mẫu giống cảnh
    ten: str = ""

    def found(self) -> list[str]:
        names = {"skin": "da", "neutral": "trắng", "sky": "trời", "plant": "cây", "clothes": "áo"}
        return [names[k] for k in names if k in self.reg]


Q = np.array([0.5, 5.0, 25.0, 50.0, 75.0, 95.0, 99.5])


def _masks(lab: np.ndarray, reg: np.ndarray | None, loose: bool = False) -> dict:
    """loose (ảnh CỦA BẠN, chưa chỉnh): đồ trắng chụp dưới đèn vàng / bóng râm bị ám đậm (váy trắng thành vàng
    cam) -> không qua ngưỡng "gần như không màu". Khi đó lấy nhóm điểm sáng, nhạt màu, cùng một kiểu ám làm "trắng"."""
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    C = np.hypot(a, b)
    hue = np.degrees(np.arctan2(b, a)) % 360.0
    valid = (L > 3) & (L < 99)
    if reg is None:
        reg = np.full(len(L), OTHER, np.uint8)
    cn = C * (86.0 / (L + 16.0))                      # độ đậm quy về L=70 (so trắng sáng với xám tối)
    m = {
        # da: bỏ điểm gần như không màu trong vùng mặt (lòng trắng mắt, răng, chỗ loá) — đó là "trắng"
        "skin": np.isin(reg, SKIN_SET) & valid & (L > 20) & (C > 4) & (C < 55) & (hue > 10) & (hue < 90)
        & (C * (86.0 / (L + 16.0)) >= 6),
        "neutral": np.isin(reg, NEUTRAL_SET) & valid & (L > 45) & (cn < 8),
        "sky": (reg == SKY) & valid & (L > 35),
        "plant": (reg == PLANT) & valid & (C > 6),
        "clothes": (reg == CLOTHES) & valid & (C > 15),
    }
    need = max(150, int(0.002 * len(L)))
    if loose and m["neutral"].sum() < need:
        cand = np.flatnonzero(np.isin(reg, NEUTRAL_SET) & valid & (L > 55) & (cn < 24) & ~m["skin"])
        if len(cand) >= 2 * need:   # đồ trắng là thứ SÁNG nhất trong nhóm nhạt màu (váy, rèm > tường be)
            cand = cand[L[cand] >= np.percentile(L[cand], 60)]
        if len(cand) >= need:
            abn = lab[cand, 1:] * (86.0 / (L[cand, None] + 16.0))
            med = np.median(abn, axis=0)
            near = cand[np.hypot(*(abn - med).T) < 6.0]
            if len(near) >= need:
                m["neutral"] = np.zeros(len(L), bool)
                m["neutral"][near] = True
    m["colorful"] = valid & (C > 10) & ~m["skin"] & ~np.isin(reg, SKIN_SET)
    m["gray"] = valid & (C < 12)
    return m


def _stat(x: np.ndarray):
    mu = x.mean(axis=0)
    cov = np.cov(x.T) + np.eye(3) * 0.5 if len(x) > 3 else np.eye(3)
    return mu, cov, len(x)


def measure(rgb_u8: np.ndarray, reg: np.ndarray | None, ten: str = "", masks: dict | None = None,
            loose: bool = False) -> Mau:
    """masks: dùng đúng các vùng này (đo ảnh SAU khi đổi màu trên cùng những điểm đã đo ảnh trước — không thì
    điểm "trắng" / "da" được chọn lại theo màu mới và số đo trước / sau không so được)."""
    lab = color.srgb_to_lab(rgb_u8.reshape(-1, 3) / 255.0)
    regf = reg.reshape(-1) if reg is not None else None
    mk = masks if masks is not None else _masks(lab, regf, loose)
    n = len(lab)
    out = Mau(ten=ten)
    need = max(150, int(0.002 * n))
    for name in ("skin", "neutral", "sky", "plant", "clothes"):
        # da: ảnh mẫu chụp toàn thân thì mặt chỉ ~0,3% ảnh — vẫn đủ để đo màu da
        if mk[name].sum() >= (max(100, int(0.0004 * n)) if name == "skin" else need):
            out.reg[name] = _stat(lab[mk[name]])
    out.Lq = np.percentile(lab[:, 0], Q)
    if "neutral" in out.reg:
        nl = lab[mk["neutral"]]
        out.neutral = (nl[:, 1:] * (86.0 / (nl[:, :1] + 16.0))).mean(axis=0)
        out.white_L = float(np.percentile(nl[:, 0], 75))
    g = lab[mk["gray"]]
    if len(g) >= need:
        sh, hi = g[(g[:, 0] > 8) & (g[:, 0] < 40)], g[(g[:, 0] > 72) & (g[:, 0] < 97)]
        out.shadow = sh[:, 1:].mean(axis=0) if len(sh) >= need // 2 else None
        out.high = hi[:, 1:].mean(axis=0) if len(hi) >= need // 2 else None
    col = lab[mk["colorful"]]
    if len(col) >= need:
        out.chroma = float(np.median(np.hypot(col[:, 1], col[:, 2])))
    return out


def sane(m: Mau) -> Mau:
    """Mục tiêu lấy từ ảnh mẫu, chặn trong khoảng hợp lý (như look.sane_ref): trắng cho ấm / lạnh theo gu nhưng
    hầu như không hồng / xanh lá; da tông 35-70°, độ đậm <= 32 (ánh phông đỏ / đèn vàng không nhuộm da theo)."""
    if m.neutral is not None:
        a, b = m.neutral
        m.neutral = np.array([np.clip(a, -2.5, 2.5), np.clip(b, -6.0, 10.0)])
    if "skin" in m.reg:
        mu, cov, n = m.reg["skin"]
        L, a, b = mu
        c, h = np.hypot(a, b), np.degrees(np.arctan2(b, a))
        c, h = min(c, 32.0), float(np.clip(h, 35.0, 70.0))
        m.reg["skin"] = (np.array([L, c * np.cos(np.radians(h)), c * np.sin(np.radians(h))]), cov, n)
    return m


def merge(ms: list[Mau]) -> Mau:
    """Nhiều ảnh mẫu không phân biệt cảnh -> gộp thống kê (theo số điểm)."""
    if len(ms) == 1:
        return ms[0]
    out = Mau(ten=" + ".join(m.ten for m in ms))
    for name in ("skin", "neutral", "sky", "plant", "clothes"):
        parts = [m.reg[name] for m in ms if name in m.reg]
        if parts:
            n = sum(p[2] for p in parts)
            mu = sum(p[0] * p[2] for p in parts) / n
            cov = sum((p[1] + np.outer(p[0] - mu, p[0] - mu)) * p[2] for p in parts) / n
            out.reg[name] = (mu, cov, n)
    out.Lq = np.mean([m.Lq for m in ms], axis=0)

    def avg(attr):
        v = [getattr(m, attr) for m in ms if getattr(m, attr) is not None]
        return np.mean(v, axis=0) if v else None
    out.neutral, out.white_L, out.shadow, out.high, out.chroma = (avg("neutral"), avg("white_L"), avg("shadow"),
                                                                    avg("high"), avg("chroma"))
    return out


# ------------------------------------------------------------------ "gu" chung (G): hàm màu -> màu
def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


@dataclass
class Gu:
    gain: np.ndarray = field(default_factory=lambda: np.ones(3))   # cân bằng trắng (nhân kênh tuyến tính)
    tone_x: np.ndarray = field(default_factory=lambda: np.array([0.0, 100.0]))
    tone_y: np.ndarray = field(default_factory=lambda: np.array([0.0, 100.0]))
    sat: float = 1.0                                               # độ rực (kiểu Vibrance: màu đã đậm ít đổi)
    shadow: np.ndarray = field(default_factory=lambda: np.zeros(2))
    high: np.ndarray = field(default_factory=lambda: np.zeros(2))

    def tone(self, L: np.ndarray) -> np.ndarray:
        from scipy.interpolate import PchipInterpolator
        return PchipInterpolator(self.tone_x, self.tone_y, extrapolate=True)(np.clip(L, 0, 100))

    def __call__(self, rgb01: np.ndarray) -> np.ndarray:
        """sRGB (N x 3, 0..1) -> Lab sau khi áp "gu"."""
        lin = color.srgb_to_linear(np.clip(rgb01, 0, 1)) * self.gain
        lab = color.xyz_to_lab(color.linear_to_xyz(lin))
        L = lab[:, 0]
        L2 = self.tone(L)
        k = 1.0 + 0.5 * ((L2 + 16.0) / (L + 16.0) - 1.0)            # sáng lên thì màu đậm theo một nửa
        ab = lab[:, 1:] * k[:, None]
        C = np.hypot(ab[:, 0], ab[:, 1])
        # độ rực chung KHÔNG đụng màu da (như Vibrance của Lightroom): hoa, áo rực làm ảnh giảm độ rực thì da không
        # bị xám theo (lỗi thật 0.14.0: da nhợt, xám)
        h = np.degrees(np.arctan2(ab[:, 1], ab[:, 0]))
        skin_like = np.exp(-((h - 50.0) / 28.0) ** 2) * np.clip((45.0 - C) / 15.0, 0.0, 1.0)
        ab *= (1.0 + (self.sat - 1.0) * (1.0 - 0.85 * skin_like) / (1.0 + (C / 45.0) ** 2))[:, None]
        ab += self.shadow[None, :] * _smoothstep(45, 15, L2)[:, None]
        ab += self.high[None, :] * _smoothstep(65, 92, L2)[:, None]
        return np.column_stack([L2, ab])


def _monotone(xs, ys, lo=0.35, hi=2.8):
    o = np.argsort(xs)
    xs, ys = np.asarray(xs, float)[o], np.asarray(ys, float)[o]
    keep = np.concatenate([[True], np.diff(xs) > 2.0])            # bỏ điểm quá sát nhau
    xs, ys = xs[keep], ys[keep]
    for i in range(1, len(xs)):
        dx = xs[i] - xs[i - 1]
        ys[i] = np.clip(ys[i], ys[i - 1] + lo * dx, ys[i - 1] + hi * dx)
    return xs, ys


def make_gu(t: Mau, r: Mau, log=None) -> Gu:
    g = Gu()
    # 1. cân bằng trắng: ám màu đồ trắng của ảnh -> ám màu đồ trắng của ảnh mẫu (nhân kênh tuyến tính)
    if t.neutral is not None and r.neutral is not None:
        lt = color.xyz_to_linear(color.lab_to_xyz(np.array([[70.0, *t.neutral]])))[0]
        lr = color.xyz_to_linear(color.lab_to_xyz(np.array([[70.0, *r.neutral]])))[0]
        gain = lr / np.maximum(lt, 1e-4)
        gain /= float(np.dot([0.2126, 0.7152, 0.0722], gain))
        g.gain = np.clip(gain, 0.75, 1.33)
    # 2. sáng tối: điểm đen, điểm trắng (chất của "gu"), độ sáng da (ảnh cưới: mặt sáng như mẫu)
    xs, ys = [], []
    tq, rq = t.Lq, r.Lq
    for i, w in ((0, 0.8), (6, 0.6)):
        xs.append(tq[i])
        ys.append(tq[i] + w * (rq[i] - tq[i]))
    if "skin" in t.reg and "skin" in r.reg:
        Lt, Lr = t.reg["skin"][0][0], r.reg["skin"][0][0]
        xs.append(Lt)
        ys.append(Lt + 0.9 * np.clip(Lr - Lt, -25, 25))
    else:   # không có da: độ sáng giữa của ảnh mẫu phụ thuộc NỘI DUNG (rạp trắng ngoài nắng vs nhẫn trên bàn gỗ)
        xs.append(tq[3])
        ys.append(tq[3] + 0.3 * np.clip(rq[3] - tq[3], -12, 12))
    if t.white_L and r.white_L and r.white_L > 72:
        xs.append(t.white_L)
        ys.append(t.white_L + 0.4 * (r.white_L - t.white_L))
    # vùng sáng giữ độ dốc >= 0,55: điểm sáng không bị dồn sát trắng (lỗi thật 0.14.0: trán, mũi, gò má trắng bệch,
    # mất màu da vì đường cong dồn 83-100 vào 95-100)
    for i in range(len(xs)):
        if i != 0:
            ys[i] = min(ys[i], 100.0 - 0.55 * (100.0 - xs[i]))
    y0 = float(np.clip(ys[0] - xs[0], 0.0, 15.0))                 # điểm đen nâng (kiểu "matte") nếu mẫu có
    y1 = float(np.clip(ys[1] + (100.0 - xs[1]), 85.0, 100.0))
    gx, gy = _monotone([0.0] + xs + [100.0], [y0] + ys + [y1])
    g.tone_x, g.tone_y = gx, gy
    # 3. độ rực: so độ đậm giữa các màu (không phải da / trắng), chỉ đi một nửa, chặn 0.75-1.35
    if t.chroma and r.chroma:
        k = float(np.clip(r.chroma / t.chroma, 0.75, 1.35))
        g.sat = 1.0 + 0.5 * (k - 1.0)
    # 4. ám màu vùng tối / vùng sáng (color grading) theo điểm gần như không màu, chặn +-4
    if t.shadow is not None and r.shadow is not None:
        g.shadow = 0.7 * np.clip(r.shadow - t.shadow, -4, 4)
    if t.high is not None and r.high is not None:
        g.high = 0.7 * np.clip(r.high - t.high, -4, 4)
    if log:
        log(f"    gu chung: cân bằng trắng x{np.round(g.gain, 3).tolist()}, sáng tối "
            + ", ".join(f"{a:.0f}->{b:.0f}" for a, b in zip(gx, gy)) + f", độ rực x{g.sat:.2f}")
    return g


# ------------------------------------------------------------------ khớp phân bố màu một vùng (MKL)
def _sqrtm(c):
    d, u = np.linalg.eigh((c + c.T) / 2)
    return (u * np.sqrt(np.maximum(d, 1e-6))) @ u.T, (u / np.sqrt(np.maximum(d, 1e-6))) @ u.T


def mkl(mu_s, cov_s, mu_t, cov_t, lo=0.6, hi=1.6):
    """Biến đổi tuyến tính đưa phân bố (mu_s, cov_s) về (mu_t, cov_t) (Pitié & Kokaram 2007), chặn độ co giãn."""
    s_half, s_ihalf = _sqrtm(cov_s)
    m_half, _ = _sqrtm(s_half @ cov_t @ s_half)
    T = s_ihalf @ m_half @ s_ihalf
    d, u = np.linalg.eigh((T + T.T) / 2)
    T = (u * np.clip(d, lo, hi)) @ u.T
    return lambda x: mu_t + (x - mu_s) @ T.T


# ------------------------------------------------------------------ bảng màu 3D
def _grid(n: int = N_LUT) -> np.ndarray:
    """Màu ở các nút bảng (n^3 x 3), thứ tự Pillow Color3DLUT: R đổi nhanh nhất, rồi G, rồi B."""
    v = np.linspace(0.0, 1.0, n)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    return np.column_stack([r.ravel(), g.ravel(), b.ravel()])


def _weights(x: np.ndarray, n: int = N_LUT):
    """Trọng số nội suy tam tuyến tính của mỗi màu x (N x 3) lên 8 nút quanh nó -> ma trận thưa N x n^3."""
    from scipy import sparse
    p = np.clip(x, 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i0
    rows, cols, vals = [], [], []
    idx = np.arange(len(x))
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = ((f[:, 0] if dr else 1 - f[:, 0]) * (f[:, 1] if dg else 1 - f[:, 1])
                     * (f[:, 2] if db else 1 - f[:, 2]))
                node = ((i0[:, 2] + db) * n + (i0[:, 1] + dg)) * n + (i0[:, 0] + dr)
                rows.append(idx)
                cols.append(node)
                vals.append(w)
    return sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                             shape=(len(x), n ** 3))


def _laplacian(n: int = N_LUT):
    from scipy import sparse
    idx = np.arange(n ** 3).reshape(n, n, n)
    a, b = [], []
    for ax in range(3):
        sl0 = [slice(None)] * 3
        sl1 = [slice(None)] * 3
        sl0[ax], sl1[ax] = slice(0, n - 1), slice(1, n)
        a.append(idx[tuple(sl0)].ravel())
        b.append(idx[tuple(sl1)].ravel())
    a, b = np.concatenate(a), np.concatenate(b)
    m = len(a)
    D = sparse.csr_matrix((np.concatenate([np.ones(m), -np.ones(m)]),
                           (np.concatenate([np.arange(m), np.arange(m)]), np.concatenate([a, b]))),
                          shape=(m, n ** 3))
    return (D.T @ D).tocsr()


_LAP = {}


def fit_lut(x: np.ndarray, y: np.ndarray, w: np.ndarray, prior: np.ndarray, n: int = N_LUT,
            smooth: float = 0.6, ridge: float = 0.02) -> np.ndarray:
    """Bảng màu 3D (n^3 x 3, sRGB) khớp x -> y (có trọng số w), trơn, chỗ không có dữ liệu theo `prior`."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    if n not in _LAP:
        _LAP[n] = _laplacian(n)
    W = _weights(x, n)
    Om = sparse.diags(w / max(float(w.mean()), 1e-9))
    scale = len(x) / n ** 3
    A = (W.T @ Om @ W + smooth * scale * _LAP[n] + ridge * scale * sparse.eye(n ** 3)).tocsc()
    rhs = W.T @ (Om @ (y - W @ prior))
    D = np.column_stack([spsolve(A, rhs[:, c]) for c in range(3)])
    return np.clip(prior + D, 0.0, 1.0)


def lut_apply_np(table: np.ndarray, x: np.ndarray, n: int = N_LUT) -> np.ndarray:
    return _weights(x, n) @ table


def to_pil_lut(table: np.ndarray, n: int = N_LUT):
    from PIL import ImageFilter
    return ImageFilter.Color3DLUT(n, table.astype(np.float64).ravel().tolist(), channels=3)


# ------------------------------------------------------------------ một ảnh
@dataclass
class KetQua:
    table: np.ndarray                      # bảng màu chính (n^3 x 3)
    skin_table: np.ndarray | None          # bảng cho vùng da (qua mặt nạ), hoặc None
    skin_mask: np.ndarray | None           # mặt nạ da (ảnh phân tích, 0..1)
    report: dict
    notes: list
    bao_cao: str = ""                       # báo cáo dạng chữ (khi đọc kế hoạch từ file)


def _de(a, b) -> float:
    return float(color.delta_e2000(np.asarray(a, float), np.asarray(b, float)))


def plan(rgb_u8: np.ndarray, reg: np.ndarray | None, ref: Mau, strength: float = 1.0, log=None,
         masks: dict | None = None) -> KetQua:
    """Tính bảng màu cho một ảnh (ảnh phân tích nhỏ + bản đồ vùng) theo thống kê ảnh mẫu `ref`."""
    notes = []
    px = rgb_u8.reshape(-1, 3) / 255.0
    regf = reg.reshape(-1) if reg is not None else np.full(len(px), OTHER, np.uint8)
    lab0 = color.srgb_to_lab(px)
    mk = masks if masks is not None else _masks(lab0, regf, loose=True)
    t = measure(rgb_u8, reg, masks=mk)
    gu = make_gu(t, ref, log)
    base = gu(px)                                          # Lab sau "gu" chung
    want = base.copy()
    wt = np.full(len(px), 0.5)
    # vùng có ở cả hai ảnh: khớp phân bố màu (MKL) trên màu đã qua "gu"
    # (tên, độ mạnh, trọng số, giới hạn co giãn độ tương phản trong vùng: da giữ khối sáng tối của mặt)
    # da trọng số vừa phải trong bảng chung (đồ trắng hồng, quạt giấy... cùng màu da không bị kéo theo); phần da còn
    # lệch do lớp da riêng qua mặt nạ lo
    for name, s, w, lim in (("skin", 1.0, 1.5, (0.8, 1.25)), ("plant", 0.75, 1.5, (0.7, 1.4)),
                            ("sky", 0.75, 1.0, (0.7, 1.4))):
        m = mk[name]
        if name not in t.reg or name not in ref.reg or m.sum() < 60:
            continue
        if name == "sky" and sky_mismatch(t.reg["sky"][0], ref.reg["sky"][0]):
            notes.append("trời khác kiểu trời ảnh mẫu (mây trắng / xanh / hoàng hôn) -> giữ trời")
            continue
        mu, cov, _ = _stat(base[m])
        f = mkl(mu, cov, ref.reg[name][0], ref.reg[name][1], *lim)
        want[m] = base[m] + s * (f(base[m]) - base[m])
        wt[m] = w
    m = mk["skin"]
    if m.sum() >= 60 and "skin" not in ref.reg:
        # ảnh mẫu không thấy da (người quá nhỏ, ảnh cảnh...): giữ tông + độ đậm màu da của ảnh, chỉ theo sáng tối
        kk = (base[m, :1] + 16.0) / (lab0[m, :1] + 16.0)
        want[m, 1:] = lab0[m, 1:] * (1.0 + 0.5 * (kk - 1.0))
        wt[m] = 2.0
        notes.append("ảnh mẫu không thấy da -> giữ màu da của ảnh, chỉ theo sáng tối")
    # trắng: ám màu trắng đúng như mẫu (gu đã cân, chỉnh nốt phần còn lệch)
    m = mk["neutral"]
    if m.sum() >= 60 and ref.neutral is not None:
        nb = base[m]
        cur = (nb[:, 1:] * (86.0 / (nb[:, :1] + 16.0))).mean(axis=0)
        want[m, 1:] += (np.clip(ref.neutral - cur, -4, 4))[None, :] * ((nb[:, :1] + 16.0) / 86.0)
        wt[m] = 3.0
    # áo có màu: GIỮ tông màu thật của áo; cùng tông với áo mẫu thì theo một nửa độ sáng / độ đậm
    m = mk["clothes"]
    if m.sum() >= 60:
        wt[m] = 1.5
        if "clothes" in t.reg and "clothes" in ref.reg and _band_of(t.reg["clothes"][0]) == _band_of(
                ref.reg["clothes"][0]):
            cb = base[m]
            mu = cb.mean(axis=0)
            Lr, ar, br = ref.reg["clothes"][0]
            dL = 0.5 * float(np.clip(Lr - mu[0], -15, 15))
            kc = 1.0 + 0.5 * (float(np.clip(np.hypot(ar, br) / max(np.hypot(mu[1], mu[2]), 1.0), 0.8, 1.3)) - 1.0)
            want[m, 0] += dL
            want[m, 1:] *= kc
            notes.append(f"áo cùng tông áo mẫu: sáng {dL:+.0f}, đậm x{kc:.2f} (giữ tông màu áo)")
    def build(w_all):
        """Bảng màu chung + lớp da + số đo (cùng những điểm đã đo ảnh gốc)."""
        rng = np.random.default_rng(0)
        pick = np.flatnonzero(w_all > 0.5)
        rest = np.flatnonzero(w_all <= 0.5)
        if len(rest) > 30000:
            rest = rng.choice(rest, 30000, replace=False)
        if len(pick) > 40000:
            pick = rng.choice(pick, 40000, replace=False)
        sel = np.concatenate([pick, rest])
        grid = _grid()
        prior = np.clip(color.lab_to_srgb(gu(grid)), 0, 1)
        y = np.clip(color.lab_to_srgb(want[sel]), 0, 1)
        table = fit_lut(px[sel], y, w_all[sel], prior)
        if strength < 1.0:
            table = grid + strength * (table - grid)
        # da: phần còn lệch sau bảng chung -> lớp riêng qua mặt nạ da làm mềm
        skin_table, skin_mask, note = None, None, None
        m = mk["skin"]
        if m.sum() >= 60 and "skin" in ref.reg:
            got = color.srgb_to_lab(np.clip(lut_apply_np(table, px[m]), 0, 1)).mean(axis=0)
            goal = base[m].mean(axis=0) + strength * (want[m].mean(axis=0) - base[m].mean(axis=0)) \
                if strength < 1.0 else want[m].mean(axis=0)
            d = np.clip(goal - got, [-10, -10, -10], [10, 10, 10])
            if np.abs(d).max() > 0.8:
                lab_t = color.srgb_to_lab(table) + d[None, :]
                skin_table = np.clip(color.lab_to_srgb(lab_t), 0, 1)
                skin_mask = np.isin(regf, SKIN_SET).reshape(rgb_u8.shape[:2]).astype(np.float32)
                note = f"da: chỉnh thêm qua mặt nạ (L {d[0]:+.1f}, a {d[1]:+.1f}, b {d[2]:+.1f})"
        out = np.clip(lut_apply_np(table, px), 0, 1)
        if skin_table is not None:
            sm = skin_mask.reshape(-1)[:, None]
            out = out * (1 - sm) + np.clip(lut_apply_np(skin_table, px), 0, 1) * sm
        rp = report(t, measure((out.reshape(rgb_u8.shape) * 255 + 0.5).astype(np.uint8), reg, masks=mk), ref)
        return table, skin_table, skin_mask, rp, note

    def worse(rp):
        """Vùng nào (trắng, da) bị tệ hơn ẢNH GỐC (quá 1 ΔE hoặc 10%)."""
        return [k for k in ("trắng", "da") if k in rp and rp[k][0] is not None
                and rp[k][1] > rp[k][0] + max(1.0, 0.1 * rp[k][0])]

    table, skin_table, skin_mask, rp, note = build(wt)
    bad = worse(rp)
    if bad:   # màu khác trong ảnh cùng màu với đồ trắng / da kéo lệch -> ưu tiên vùng bị tệ đi, làm lại
        wt2 = wt.copy()
        for k, name in (("trắng", "neutral"), ("da", "skin")):
            if k in bad:
                wt2[mk[name]] *= 4.0
        cand = build(wt2)
        if len(worse(cand[3])) < len(bad) or sum(cand[3][k][1] for k in bad) < sum(rp[k][1] for k in bad):
            table, skin_table, skin_mask, rp, note = cand
            notes.append("ưu tiên giữ " + "/".join(bad) + " không tệ hơn ảnh gốc")
    if note:
        notes.append(note)
    return KetQua(table, skin_table, skin_mask, rp, notes)


def report(before: Mau, after: Mau, ref: Mau) -> dict:
    """ΔE so với ảnh mẫu trước / sau (da, trắng, cây, trời) + áo lệch màu gốc bao nhiêu."""
    rp = {}
    for name, vi in (("skin", "da"), ("plant", "cây"), ("sky", "trời")):
        if name in after.reg and name in ref.reg:
            rp[vi] = (_de(before.reg[name][0], ref.reg[name][0]) if name in before.reg else None,
                      _de(after.reg[name][0], ref.reg[name][0]))
    if before.neutral is not None and after.neutral is not None and ref.neutral is not None:
        rp["trắng"] = (float(np.hypot(*(before.neutral - ref.neutral))), float(np.hypot(*(after.neutral - ref.neutral))))
    if "clothes" in before.reg and "clothes" in after.reg:
        a, b = before.reg["clothes"][0], after.reg["clothes"][0]
        dh = abs((np.degrees(np.arctan2(a[2], a[1]) - np.arctan2(b[2], b[1])) + 180) % 360 - 180)
        rp["áo đổi tông"] = (0.0, float(dh))
    return rp


def fmt(rp: dict) -> str:
    parts = []
    for k, (a, b) in rp.items():
        if k == "áo đổi tông":
            parts.append(f"áo đổi tông {b:.0f}°")
        elif a is None:
            parts.append(f"{k} ΔE {b:.1f}")
        else:
            parts.append(f"{k} ΔE {a:.1f} -> {b:.1f}")
    return ", ".join(parts)


def render(im, k: KetQua):
    """Áp kết quả lên ảnh đầy đủ độ phân giải (PIL RGB)."""
    from PIL import Image, ImageFilter
    out = im.filter(to_pil_lut(k.table))
    if k.skin_table is not None and k.skin_mask is not None:
        m = Image.fromarray((np.clip(k.skin_mask, 0, 1) * 255).astype(np.uint8))
        m = m.resize(im.size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(max(2.0, 0.006 * max(im.size))))
        out = Image.composite(im.filter(to_pil_lut(k.skin_table)), out, m)
    return out


# ------------------------------------------------------------------ cả lô
PEOPLE = (PERSON, FACE, SKIN, HAIR, CLOTHES)


def refine_people(a: np.ndarray, reg: np.ndarray, segment) -> np.ndarray:
    """Người nhỏ trong ảnh (ảnh toàn thân, cô dâu chú rể đứng xa): mô hình AI nhìn cả ảnh ở 512 điểm nên dễ sót mặt /
    da. Phân vùng lại trên khung quanh người (phóng to) rồi dán mặt / da / tóc / áo vào."""
    from PIL import Image
    person = np.isin(reg, PEOPLE)
    if person.sum() < 200:
        return reg
    ys, xs = np.nonzero(person)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    h, w = reg.shape
    if (y1 - y0) * (x1 - x0) > 0.45 * h * w:
        return reg                                   # người đã đủ lớn
    pad = int(0.08 * max(y1 - y0, x1 - x0)) + 4
    y0, y1, x0, x1 = max(0, y0 - pad), min(h, y1 + pad), max(0, x0 - pad), min(w, x1 + pad)
    crop = Image.fromarray(np.ascontiguousarray(a[y0:y1, x0:x1]))
    scale = ANALYSIS / max(crop.size)
    if scale > 1.0:
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
    sub = segment(np.asarray(crop))
    sub = np.asarray(Image.fromarray(sub.astype(np.uint8)).resize((x1 - x0, y1 - y0), Image.NEAREST))
    parts = np.isin(sub, (FACE, SKIN, HAIR, CLOTHES))
    out = reg.copy()
    win = out[y0:y1, x0:x1]
    win[parts] = sub[parts]
    return out


def segmenter(segment):
    """Hàm phân vùng dùng cho chép màu: phân vùng cả ảnh + phân vùng lại quanh người nhỏ."""
    if segment is None:
        return None
    return lambda a: refine_people(a, segment(a), segment)


def load_refs(paths, segment=None, embedder=None, log=print) -> list[Mau]:
    refs = []
    for p in paths:
        im, _ = load_rgb(p)
        a = small(im)
        m = sane(measure(a, segment(a) if segment else None, Path(str(p)).name))
        if embedder is not None:
            try:
                m.emb = embedder.embed([a])[0]
            except Exception:
                m.emb = None
        refs.append(m)
        log(f"Ảnh mẫu {m.ten}: thấy {', '.join(m.found()) or 'không có vùng đặc biệt'}")
    return refs


def pick_ref(refs: list[Mau], emb) -> Mau:
    """Ảnh mẫu giống cảnh nhất (trắng, trời, cây, sáng tối theo cảnh). Màu DA là "gu" chung của cả bộ ảnh mẫu: ảnh
    mẫu được chọn không thấy da (người nhỏ, ảnh cảnh) thì lấy da gộp từ các ảnh mẫu khác."""
    if len(refs) == 1:
        return refs[0]
    if emb is not None and all(r.emb is not None for r in refs):
        sims = [float(np.dot(emb, r.emb)) for r in refs]
        best = refs[int(np.argmax(sims))]
    else:
        return merge(refs)
    if "skin" not in best.reg:
        others = [r for r in refs if "skin" in r.reg]
        if others:
            from copy import copy
            best = copy(best)
            best.reg = dict(best.reg)
            best.reg["skin"] = merge(others).reg["skin"] if len(others) > 1 else others[0].reg["skin"]
    return best


def copy_one(src, dst, refs: list[Mau], segment=None, embedder=None, strength: float = 1.0, quality: int = 95,
             log=print) -> dict:
    t0 = time.time()
    im, meta = load_rgb(src)
    a = small(im)
    reg = segment(a) if segment else None
    emb = None
    if embedder is not None and len(refs) > 1:
        try:
            emb = embedder.embed([a])[0]
        except Exception:
            emb = None
    ref = pick_ref(refs, emb)
    k = plan(a, reg, ref, strength)
    save(render(im, k), dst, meta, quality)
    info = {"ra": str(dst), "mau": ref.ten, "bao_cao": fmt(k.report), "ghi_chu": k.notes,
            "giay": round(time.time() - t0, 2)}
    log(f"  {Path(str(src)).name} <- {ref.ten}: {fmt(k.report)}" + (f" ({'; '.join(k.notes)})" if k.notes else ""))
    return info


def out_name(folder: Path, src: Path, taken=()) -> Path:
    """Tên ảnh ra không ghi đè ảnh đã có (cả ảnh vừa đặt tên trong lô `taken`): <tên>-editer.jpg, -editer-2.jpg..."""
    stem = src.stem
    p = folder / f"{stem}-editer.jpg"
    i = 2
    while p.exists() or str(p).lower() in taken:
        p = folder / f"{stem}-editer-{i}.jpg"
        i += 1
    return p


# ------------------------------------------------------------------ cả lô: một "gu", mỗi cảnh một bộ màu
@dataclass
class Anh:
    """Một ảnh trong lô, ở bước phân tích."""
    i: int
    small: np.ndarray                    # ảnh phân tích (uint8, cạnh dài ~ANALYSIS)
    reg: np.ndarray | None               # bản đồ vùng
    time: float | None = None            # giờ chụp (giây)
    emb: np.ndarray | None = None        # "dấu vân tay" nội dung (CLIP)
    ten: str = ""


def capture_time(path) -> float | None:
    """Giờ chụp trong EXIF (DateTimeOriginal) — Lightroom giữ EXIF khi xuất."""
    from datetime import datetime

    from PIL import Image
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            v = ex.get_ifd(0x8769).get(0x9003) or ex.get(0x0132)
        if v:
            return datetime.strptime(str(v).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    return None


def group(photos: list[Anh]) -> list[int]:
    """Chia cảnh như các mục khác của tool (giờ chụp cách > 4 phút, nội dung đổi hẳn). Không có giờ chụp: theo
    thứ tự ảnh + nội dung (CLIP); không có AI: một cảnh."""
    from types import SimpleNamespace

    from . import style
    if not photos:
        return []
    embs = np.stack([p.emb for p in photos]) if all(p.emb is not None for p in photos) else None
    if any(p.time for p in photos):
        return style.group_scenes([SimpleNamespace(time=p.time or 0.0) for p in photos], 4.0, embs)
    if embs is None:
        return [0] * len(photos)
    return style.group_scenes([SimpleNamespace(time=120.0 * k) for k in range(len(photos))], 1e9, embs)


def _Y(L):
    """Lab L -> độ sáng tuyến tính Y (0..1)."""
    f = (np.asarray(L, float) + 16.0) / 116.0
    return np.where(f > 6 / 29, f ** 3, 3 * (6 / 29) ** 2 * (f - 4 / 29))


def gain_srgb(rgb01: np.ndarray, g: float) -> np.ndarray:
    """Phơi sáng (nhân kênh tuyến tính); tăng sáng thì vùng sáng uốn mềm như Lightroom, không cháy gắt."""
    if abs(g - 1.0) < 1e-6:
        return np.clip(rgb01, 0, 1)
    lin = color.srgb_to_linear(np.clip(rgb01, 0, 1)) * g
    if g > 1.0:
        k = 0.8
        over = lin > k
        lin[over] = k + (1 - k) * (1 - np.exp(-(lin[over] - k) / (1 - k)))
    return color.linear_to_srgb(np.clip(lin, 0, 1))


def skin_L(a: np.ndarray, reg: np.ndarray | None) -> float | None:
    lab = color.srgb_to_lab(a.reshape(-1, 3) / 255.0)
    m = _masks(lab, reg.reshape(-1) if reg is not None else None, loose=True)["skin"]
    return float(np.median(lab[m, 0])) if m.sum() >= 60 else None


def plan_scene(photos: list[Anh], ref: Mau, strength: float = 1.0, log=None, looks: list | None = None) -> dict:
    """MỘT bộ màu cho cả cảnh (gom điểm ảnh của mọi ảnh trong cảnh), từng ảnh chỉ cân lại độ sáng theo da cho đều
    -> các ảnh cùng cảnh ra cùng một màu (lỗi thật 0.14.2: mỗi ảnh một màu vì mỗi ảnh tự khớp riêng).
    looks: nếu có, nhận thêm bộ màu chung của cảnh (chưa cân sáng từng ảnh) — để làm hồ sơ màu Camera Raw."""
    from PIL import Image
    Ls = [skin_L(p.small, p.reg) for p in photos]
    have = [v for v in Ls if v is not None]
    target = float(np.median(have)) if have else None
    gains = []
    for v in Ls:
        if target is None or v is None:
            gains.append(1.0)
        else:
            ev = 0.9 * float(np.log2(_Y(target) / max(float(_Y(v)), 1e-4)))
            gains.append(float(2.0 ** np.clip(ev, -1.0, 1.0)))
    rng = np.random.default_rng(1)
    per = max(3000, 150000 // max(1, len(photos)))
    px, rg, mks = [], [], []
    for p, g in zip(photos, gains):
        flat = p.small.reshape(-1, 3)
        r = p.reg.reshape(-1) if p.reg is not None else np.full(len(flat), OTHER, np.uint8)
        x = gain_srgb(flat / 255.0, g)
        # vùng (da, đồ trắng...) tìm TRÊN TỪNG ẢNH rồi mới gộp: ảnh sáng hơn có váy trắng gần cháy (ám nhạt hơn) vẫn
        # được nhận là "trắng" cùng với váy trắng của các ảnh khác
        mk = _masks(color.srgb_to_lab(x), r, loose=True)
        idx = rng.choice(len(flat), min(per, len(flat)), replace=False)
        px.append((np.clip(x[idx], 0, 1) * 255 + 0.5).astype(np.uint8))
        rg.append(r[idx])
        mks.append({k: v[idx] for k, v in mk.items()})
    pooled_mk = {k: np.concatenate([m[k] for m in mks]) for k in mks[0]}
    kq = plan(np.concatenate(px)[None, :, :], np.concatenate(rg)[None, :], ref, strength, log, masks=pooled_mk)
    if looks is not None:
        looks.append(kq)
    grid = _grid()
    out = {}
    for p, g in zip(photos, gains):
        gin = gain_srgb(grid, g)
        table = np.clip(lut_apply_np(kq.table, gin), 0, 1)
        st = np.clip(lut_apply_np(kq.skin_table, gin), 0, 1) if kq.skin_table is not None else None
        sm = np.isin(p.reg, SKIN_SET).astype(np.float32) if (st is not None and p.reg is not None) else None
        k = KetQua(table, st, sm, {}, list(kq.notes))
        lab0 = color.srgb_to_lab(p.small.reshape(-1, 3) / 255.0)
        mk = _masks(lab0, p.reg.reshape(-1) if p.reg is not None else None, loose=True)
        after = np.asarray(render(Image.fromarray(p.small), k))
        k.report = report(measure(p.small, p.reg, masks=mk), measure(after, p.reg, masks=mk), ref)
        ev = float(np.log2(g))
        if abs(ev) > 0.05:
            k.notes.append(f"sáng {ev:+.2f} EV cho đều với cả cảnh")
        k.bao_cao = fmt(k.report)
        out[p.i] = k
    return out


def _ai(use_ai: bool, log):
    seg, emb = None, None
    if use_ai:
        from . import ai
        seg = ai.Segmenter.get()
        emb = ai.Embedder.get()
        log("AI phân vùng (da, áo, trời, cây): " + (f"BẬT ({seg.device})" if seg else "TẮT (không có mô hình)"))
    return (segmenter(seg.segment) if seg else None), emb


def load_photos(entries, segment=None, embedder=None, log=print, progress=None, cancel=None) -> list[Anh]:
    """entries: (i, đường dẫn ảnh, giờ chụp | None). Ảnh lớn thì thu nhỏ để phân tích."""
    out = []
    for n, (i, path, t) in enumerate(entries):
        if cancel and cancel():
            break
        try:
            im, _ = load_rgb(path)
            a = small(im)
            reg = segment(a) if segment else None
            e = None
            if embedder is not None:
                try:
                    e = embedder.embed([a])[0]
                except Exception:
                    e = None
            out.append(Anh(i, a, reg, t or capture_time(path), e, Path(str(path)).name))
        except Exception as ex:   # ảnh lỗi: báo, không làm hỏng cả lô
            log(f"  {Path(str(path)).name}: LỖI đọc ảnh ({ex})")
        if progress:
            progress(n + 1, len(entries))
    return out


def analyze(photos: list[Anh], refs: list[Mau], strength: float = 1.0, log=print, looks: list | None = None) -> dict:
    """Cả lô theo MỘT "gu" (gộp các ảnh mẫu bạn chọn), mỗi cảnh một bộ màu -> {i: KetQua}.
    looks: nếu có, nhận bộ màu của từng cảnh {"canh", "anh", "table"} (+ "canh" 0 = một bộ chung cả lô khi có nhiều
    cảnh) để xuất hồ sơ màu / preset cho Camera Raw, Lightroom."""
    if not photos:
        return {}
    ref = merge(refs) if len(refs) > 1 else refs[0]
    if len(refs) > 1:
        log(f"Gộp {len(refs)} ảnh mẫu thành một kiểu màu cho cả lô (các ảnh cùng một màu, không mỗi ảnh một kiểu).")
    scenes = group(photos)
    n_sc = len(set(scenes))
    log(f"Chia được {n_sc} cảnh (giờ chụp + nội dung) — mỗi cảnh một bộ màu, từng ảnh chỉ cân lại độ sáng cho đều.")
    out = {}
    for sc in sorted(set(scenes)):
        ps = [p for p, s in zip(photos, scenes) if s == sc]
        got = [] if looks is not None else None
        res = plan_scene(ps, ref, strength, looks=got)
        for p in ps:
            k = res[p.i]
            log(f"  cảnh {sc + 1}: {p.ten}: {k.bao_cao}" + (f" ({'; '.join(k.notes)})" if k.notes else ""))
        out.update(res)
        if got:
            looks.append({"canh": sc + 1, "anh": [p.ten for p in ps], "table": got[0].table})
    if looks is not None and n_sc > 1:      # thêm một bộ chung cho cả lô: preset dùng lại cho buổi chụp khác
        got = []
        plan_scene(photos, ref, strength, looks=got)
        looks.insert(0, {"canh": 0, "anh": [p.ten for p in photos], "table": got[0].table})
    return out


def save_plan(path, plans: dict) -> None:
    arrs, meta = {}, {}
    for i, k in plans.items():
        arrs[f"t{i}"] = np.asarray(k.table, np.float32)
        if k.skin_table is not None:
            arrs[f"s{i}"] = np.asarray(k.skin_table, np.float32)
        if k.skin_mask is not None:
            arrs[f"m{i}"] = (np.clip(k.skin_mask, 0, 1) * 255).astype(np.uint8)
        meta[str(i)] = {"bao_cao": k.bao_cao or fmt(k.report), "ghi_chu": list(k.notes)}
    arrs["meta"] = np.frombuffer(json.dumps(meta, ensure_ascii=False).encode("utf-8"), dtype=np.uint8)
    p = Path(path)
    tmp = p.with_name(p.stem + ".tmp.npz")
    np.savez_compressed(tmp, **arrs)
    tmp.replace(p)


def load_plan(path) -> dict:
    out = {}
    with np.load(path) as z:
        meta = json.loads(bytes(z["meta"]).decode("utf-8"))
        for key, m in meta.items():
            i = int(key)
            out[i] = KetQua(np.asarray(z[f"t{i}"], np.float64),
                            np.asarray(z[f"s{i}"], np.float64) if f"s{i}" in z else None,
                            (z[f"m{i}"].astype(np.float32) / 255.0) if f"m{i}" in z else None,
                            {}, list(m.get("ghi_chu") or []), m.get("bao_cao") or "")
    return out


def apply(items, plans: dict, quality: int = 95, log=print, progress=None, cancel=None) -> list[dict]:
    """items: (i, ảnh vào đầy đủ, ảnh ra). Áp đúng bộ màu đã tính ở bước phân tích."""
    out = []
    for n, (i, src, dst) in enumerate(items):
        if cancel and cancel():
            log("Đã dừng theo yêu cầu.")
            break
        try:
            k = plans.get(i)
            if k is None:
                raise RuntimeError("chưa có kết quả phân tích cho ảnh này")
            im, meta = load_rgb(src)
            save(render(im, k), dst, meta, quality)
            out.append({"ra": str(dst), "bao_cao": k.bao_cao, "ghi_chu": k.notes})
        except Exception as e:  # một ảnh lỗi không làm hỏng cả lô
            out.append({"loi": f"{type(e).__name__}: {e}"})
            log(f"  {Path(str(src)).name}: LỖI {e}")
        if progress:
            progress(n + 1, len(items))
    return out


def run_batch(srcs, dsts, ref_paths, strength=1.0, quality=95, use_ai=True, log=print, progress=None,
              cancel=None) -> list[dict]:
    """Chép màu cho cả lô: phân tích cả lô (một "gu", mỗi cảnh một bộ màu) rồi áp srcs[i] -> dsts[i].
    progress(k, n) sau mỗi ảnh được áp; cancel() -> True thì dừng."""
    segment, emb = _ai(use_ai, log)
    refs = load_refs(ref_paths, segment, emb if len(ref_paths) > 1 else None, log)
    photos = load_photos([(i, s, None) for i, s in enumerate(srcs)], segment, emb, log, cancel=cancel)
    plans = analyze(photos, refs, strength, log)
    return apply([(i, s, d) for i, (s, d) in enumerate(zip(srcs, dsts))], plans, quality, log, progress, cancel)


def write_json(path, data) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
