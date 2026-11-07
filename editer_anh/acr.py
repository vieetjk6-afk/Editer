"""Hồ sơ màu (Creative Profile) + preset cho Camera Raw / Lightroom từ màu đã chép kiểu Evoto.

Camera Raw và Lightroom không có chỗ cắm bảng màu .cube. Cách chuẩn là "hồ sơ màu sáng tạo": một file .xmp
(crs:PresetType="Look") chứa bảng màu RGB 3D, chọn trong Profile Browser, có thanh Amount 0-200%. Camera Raw áp bảng
này SAU các thanh sáng tối, đường cong, HSL, Saturation và TRƯỚC Color Grading, trong hệ màu ghi trong bảng (ở đây
sRGB, đường cong sRGB) — tức đúng thứ ảnh Lightroom xuất ra để tool phân tích. Hồ sơ sáng tạo thay chỗ hồ sơ đang
dùng (Adobe Color...), nên tool chép luôn phần "nền" của hồ sơ đó (bảng màu, đường cong của Adobe Color) vào hồ sơ mới
để ảnh vào bảng màu giống hệt lúc phân tích.

Định dạng bảng (big table của DNG SDK; RAWmakase — MIT — đã đo trên Camera Raw 18.7, mini-film đọc được):
- u32 loại = 1, u32 phiên bản = 1, u32 số chiều = 3, u32 số nút n; n^3 mẫu x 3 kênh u16 (R ngoài cùng, rồi G, B trong
  cùng) lưu HIỆU so với bảng đồng nhất (mod 65536); u32 hệ màu (0 sRGB), u32 đường cong (1 sRGB), u32 gam màu
  (0 cắt), 2 số f64 giới hạn Amount (0..2);
- trong XMP: 4 byte độ dài + dữ liệu zlib, viết bằng base85 kiểu Adobe (bảng chữ riêng, nhóm 4 byte little-endian,
  chữ số nhỏ trước; nhóm cuối k byte -> k+1 chữ);
- tên bảng = MD5 các byte bảng (chữ hoa): crs:RGBTable="<tên>", crs:Table_<tên>="<chuỗi>".
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"
_DIGIT = {c: i for i, c in enumerate(ALPHABET)}
SRGB, ADOBE_RGB, PROPHOTO, DISPLAY_P3, REC2020 = range(5)          # hệ màu của bảng
LINEAR, GAMMA_SRGB, GAMMA_1_8, GAMMA_2_2, GAMMA_REC2020 = range(5)  # đường cong (mã hoá) của bảng
CLIP, EXTEND = 0, 1
DIVISIONS = 17        # số nút = bảng của bộ máy: Camera Raw (nội suy tứ diện) lệch TB ΔE <= 0,14, file ~30 KB;
                      # bảng > 32 nút thì Lightroom / Camera Raw bản cũ (DNG SDK cũ) có thể bỏ qua cả hồ sơ
GROUP = "Editer"


# ------------------------------------------------------------------ mã hoá
def b85encode(packed: bytes) -> str:
    """Base85 kiểu Adobe: mỗi nhóm 4 byte là một số little-endian, viết 5 chữ, chữ số nhỏ trước; nhóm cuối k byte
    -> k+1 chữ."""
    pad = (-len(packed)) % 4
    v = np.frombuffer(packed + b"\0" * pad, dtype="<u4").astype(np.int64)
    digits = np.empty((len(v), 5), dtype=np.int64)
    for k in range(5):
        digits[:, k] = v % 85
        v //= 85
    chars = np.frombuffer(ALPHABET.encode("ascii"), dtype=np.uint8)[digits.reshape(-1)]
    return chars[:len(chars) - pad].tobytes().decode("ascii")


def b85decode(text: str) -> bytes:
    vals = np.array([_DIGIT[c] for c in text if c in _DIGIT], dtype=np.int64)
    full = len(vals) // 5 * 5
    words = (vals[:full].reshape(-1, 5) * (85 ** np.arange(5, dtype=np.int64))).sum(1)
    out = (words & 0xFFFFFFFF).astype("<u4").tobytes()
    rest = vals[full:]
    if len(rest) > 1:
        x = int(sum(int(d) * 85 ** k for k, d in enumerate(rest)))
        out += struct.pack("<I", x & 0xFFFFFFFF)[:len(rest) - 1]
    return out


def encode(data: bytes) -> str:
    """Bytes bảng -> chuỗi Table_ (4 byte độ dài + zlib, base85 kiểu Adobe)."""
    return b85encode(struct.pack("<I", len(data)) + zlib.compress(data, 9))


def decode(text: str) -> bytes:
    """Chuỗi Table_ -> bytes bảng (kiểm tra độ dài ghi ở đầu)."""
    out = b85decode(text)
    if len(out) < 5:
        raise ValueError("bảng quá ngắn")
    size = struct.unpack("<I", out[:4])[0]
    data = zlib.decompress(out[4:])
    if len(data) != size:
        raise ValueError(f"độ dài bảng không khớp ({len(data)} != {size})")
    return data


def fingerprint(data: bytes) -> str:
    return hashlib.md5(data).hexdigest().upper()


def _nop(n: int) -> np.ndarray:
    """Bảng đồng nhất ở các nút: round(i * 65535 / (n - 1)) (số nút lẻ như bảng của tool: mọi cách làm tròn như nhau)."""
    return (np.arange(n, dtype=np.int64) * 0xFFFF + (n - 1) // 2) // (n - 1)


def _identity(n: int) -> np.ndarray:
    g = _nop(n)
    return np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1)


def table_bytes(lut: np.ndarray, primaries: int = SRGB, gamma: int = GAMMA_SRGB, gamut: int = CLIP,
                bounds=(0.0, 2.0)) -> bytes:
    """lut: (n, n, n, 3), chỉ số [r, g, b] -> màu ra 0..1 trong hệ màu / đường cong của bảng."""
    n = lut.shape[0]
    if lut.shape != (n, n, n, 3) or not 2 <= n <= 64:
        raise ValueError(f"bảng màu phải là n x n x n x 3 với 2 <= n <= 64 (nhận {lut.shape})")
    q = np.rint(np.clip(np.nan_to_num(lut, nan=0.0), 0.0, 1.0) * 65535).astype(np.int64)
    delta = ((q - _identity(n)) & 0xFFFF).astype("<u2")
    return (struct.pack("<4I", 1, 1, 3, n) + delta.tobytes()
            + struct.pack("<3I2d", primaries, gamma, gamut, float(bounds[0]), float(bounds[1])))


def parse_table(data: bytes) -> dict:
    kind, ver, dims, n = struct.unpack_from("<4I", data, 0)
    if kind != 1 or dims != 3 or n < 2:
        raise ValueError(f"không phải bảng RGB 3D (loại {kind}, {dims} chiều, {n} nút)")
    cnt = n ** 3 * 3
    raw = np.frombuffer(data, dtype="<u2", count=cnt, offset=16).astype(np.int64).reshape(n, n, n, 3)
    vals = (raw + _identity(n)) & 0xFFFF
    off = 16 + cnt * 2
    pri, gam, gmt = struct.unpack_from("<3I", data, off)
    lo, hi = struct.unpack_from("<2d", data, off + 12)
    return {"lut": vals / 65535.0, "primaries": pri, "gamma": gam, "gamut": gmt, "bounds": (lo, hi),
            "version": ver}


# ------------------------------------------------------------------ bảng của bộ máy chép màu <-> bảng Adobe
def from_flat(table: np.ndarray) -> np.ndarray:
    """Bảng n^3 x 3 thứ tự Pillow (R đổi nhanh nhất) -> [r, g, b, 3]."""
    t = np.asarray(table, dtype=np.float64)
    n = round(len(t) ** (1 / 3))
    if n ** 3 != len(t):
        raise ValueError("bảng màu không phải n^3 x 3")
    return t.reshape(n, n, n, 3).transpose(2, 1, 0, 3)


def trilinear(lut: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Nội suy tam tuyến tính (như Pillow / bộ máy chép màu). x: (N, 3) 0..1."""
    n = lut.shape[0]
    p = np.clip(x, 0.0, 1.0) * (n - 1)
    i = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i
    out = np.zeros((len(x), 3))
    for dr in (0, 1):
        wr = f[:, 0] if dr else 1 - f[:, 0]
        for dg in (0, 1):
            wg = f[:, 1] if dg else 1 - f[:, 1]
            for db in (0, 1):
                wb = f[:, 2] if db else 1 - f[:, 2]
                out += (wr * wg * wb)[:, None] * lut[i[:, 0] + dr, i[:, 1] + dg, i[:, 2] + db]
    return out


def tetrahedral(lut: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Nội suy tứ diện — cách Camera Raw (DNG SDK) đọc bảng."""
    n = lut.shape[0]
    p = np.clip(x, 0.0, 1.0) * (n - 1)
    i = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    r0, g0, b0 = i[:, 0], i[:, 1], i[:, 2]

    def c(dr, dg, db):
        return lut[r0 + dr, g0 + dg, b0 + db]

    c000, c111 = c(0, 0, 0), c(1, 1, 1)
    out = np.empty((len(x), 3))
    cases = [
        ((fr >= fg) & (fg >= fb), lambda: (1 - fr) * c000 + (fr - fg) * c(1, 0, 0) + (fg - fb) * c(1, 1, 0) + fb * c111),
        ((fr >= fb) & (fb > fg), lambda: (1 - fr) * c000 + (fr - fb) * c(1, 0, 0) + (fb - fg) * c(1, 0, 1) + fg * c111),
        ((fb > fr) & (fr >= fg), lambda: (1 - fb) * c000 + (fb - fr) * c(0, 0, 1) + (fr - fg) * c(1, 0, 1) + fg * c111),
        ((fg > fr) & (fr >= fb), lambda: (1 - fg) * c000 + (fg - fr) * c(0, 1, 0) + (fr - fb) * c(1, 1, 0) + fb * c111),
        ((fg >= fb) & (fb > fr), lambda: (1 - fg) * c000 + (fg - fb) * c(0, 1, 0) + (fb - fr) * c(0, 1, 1) + fr * c111),
        ((fb > fg) & (fg > fr), lambda: (1 - fb) * c000 + (fb - fg) * c(0, 0, 1) + (fg - fr) * c(0, 1, 1) + fr * c111),
    ]
    done = np.zeros(len(x), dtype=bool)
    for m, fn in cases:
        m = m[:, 0] & ~done
        if m.any():
            out[m] = fn()[m]
            done |= m
    return out


def resample(lut: np.ndarray, m: int) -> np.ndarray:
    """Đổi số nút (nội suy tam tuyến tính như lúc tool áp bảng)."""
    if lut.shape[0] == m:
        return lut
    v = np.linspace(0.0, 1.0, m)
    pts = np.stack(np.meshgrid(v, v, v, indexing="ij"), axis=-1).reshape(-1, 3)
    return trilinear(lut, pts).reshape(m, m, m, 3)


# ------------------------------------------------------------------ "nền" của hồ sơ đang dùng (Adobe Color...)
@dataclass
class Base:
    name: str
    params: dict = field(default_factory=dict)     # thuộc tính crs (chuỗi): CameraProfile, LookTable, ...
    curves: dict = field(default_factory=dict)     # {"ToneCurvePV2012": ["0, 0", ...]}
    tables: dict = field(default_factory=dict)     # {tên bảng: chuỗi Table_}
    raw_only: bool = True
    source: str = ""
    notes: list = field(default_factory=list)


_META = ("PresetType", "Cluster", "UUID", "CameraModelRestriction", "Copyright", "ContactInfo", "HasSettings")
_RESERVED = set(_META) | {"Name", "Group", "Description", "Amount", "SortName", "ShortName"}
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_KEYS = ("LookTable", "RGBTable")


def _li(seq: str) -> list:
    return [html.unescape(x.strip()) for x in re.findall(r"<rdf:li[^>]*>([^<]*)</rdf:li>", seq)]


def read_look(path) -> Base | None:
    """Đọc một hồ sơ màu .xmp (crs:PresetType="Look") -> Base."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    attrs = {k: html.unescape(v) for k, v in re.findall(r'crs:(\w+)="([^"]*)"', text)}
    attrs.update({k: html.unescape(v) for k, v in re.findall(r"<crs:(\w+)>([^<]+)</crs:\1>", text)})
    if attrs.get("PresetType") != "Look":
        return None
    m = re.search(r"<crs:Name>.*?<rdf:li[^>]*>([^<]*)</rdf:li>", text, re.S)
    name = html.unescape(m.group(1).strip()) if m else Path(path).stem
    curves = {k: _li(v) for k, v in re.findall(r"<crs:(ToneCurve\w*)>\s*<rdf:Seq>(.*?)</rdf:Seq>", text, re.S)}
    tables = {k[6:]: v for k, v in attrs.items() if k.startswith("Table_")}
    params = {k: v for k, v in attrs.items() if k not in _META and not k.startswith(("Supports", "Table_"))}
    return Base(name, params, curves, tables, attrs.get("SupportsOutputReferred", "False") != "True", str(path))


def adobe_roots() -> list:
    """Các thư mục Adobe để hồ sơ màu .xmp (Camera Raw / Lightroom), có thì mới dùng."""
    env = os.environ.get("EDITER_ADOBE_PROFILES")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    out = []
    for var, sub in (("PROGRAMDATA", "Adobe/CameraRaw/Settings"), ("APPDATA", "Adobe/CameraRaw/Settings")):
        if os.environ.get(var):
            out.append(Path(os.environ[var]) / sub)
    for var in ("PROGRAMFILES", "PROGRAMW6432"):
        base = os.environ.get(var)
        if base and (Path(base) / "Adobe").is_dir():
            try:
                out += [p for p in (Path(base) / "Adobe").iterdir() if "Lightroom" in p.name or "Camera Raw" in p.name]
            except OSError:
                pass
    home = Path.home()
    out += [Path("/Library/Application Support/Adobe/CameraRaw/Settings"),
            home / "Library/Application Support/Adobe/CameraRaw/Settings"]
    return [p for p in dict.fromkeys(out) if p.is_dir()]


def find_look(name: str, budget: float = 8.0) -> Base | None:
    """Tìm file hồ sơ màu tên `name` (vd "Adobe Color") trong các thư mục Adobe trên máy."""
    t0, want = time.time(), f"{name}.xmp".lower()
    for root in adobe_roots():
        for d, _, files in os.walk(root):
            if time.time() - t0 > budget:
                return None
            for f in files:
                if f.lower() == want:
                    b = read_look(Path(d) / f)
                    if b and b.name == name:
                        return b
    return None


def _num(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int) or (isinstance(v, float) and float(v).is_integer()):
        return str(int(v))
    return f"{v:g}"


def base_from_lr(look) -> Base | None:
    """Hồ sơ đang dùng theo Lightroom (photo:getDevelopSettings().Look) -> Base; tìm dữ liệu bảng trên máy."""
    if isinstance(look, str):
        look = {"Name": look}
    if not isinstance(look, dict) or not look.get("Name"):
        return None
    name = str(look["Name"])
    found = find_look(name)
    params = look.get("Parameters")
    if not isinstance(params, dict) or not params:
        if found:
            found.notes.append(f"nền: hồ sơ {name} ({found.source})")
        return found
    p, curves = {}, {}
    for k, v in params.items():
        if isinstance(v, (list, tuple)) and k.startswith("ToneCurve"):
            vals = [float(x) for x in v]
            curves[k] = [f"{_num(vals[i])}, {_num(vals[i + 1])}" for i in range(0, len(vals) - 1, 2)]
        elif isinstance(v, (bool, int, float)):
            p[k] = _num(v)
        elif isinstance(v, str):
            p[k] = v
    tables = {k[6:]: v for k, v in look.items() if isinstance(k, str) and k.startswith("Table_") and isinstance(v, str)}
    tables.update({k[6:]: v for k, v in params.items() if isinstance(k, str) and k.startswith("Table_")
                   and isinstance(v, str)})
    b = Base(name, {k: v for k, v in p.items() if not k.startswith("Table_")}, curves, tables,
             str(look.get("SupportsOutputReferred", False)).lower() != "true", "Lightroom")
    for key in _TABLE_KEYS:
        fp = b.params.get(key)
        if fp and fp not in b.tables and found and fp in found.tables:
            b.tables[fp] = found.tables[fp]
    missing = [b.params[k] for k in _TABLE_KEYS if b.params.get(k) and b.params[k] not in b.tables]
    if missing and found:   # bảng trong ảnh không còn trên máy (Adobe đã cập nhật hồ sơ) -> dùng bản trên máy
        found.notes.append(f"nền: hồ sơ {name} trên máy ({found.source}); bản trong ảnh thiếu dữ liệu bảng")
        return found
    if missing:
        b.notes.append(f"nền: hồ sơ {name} theo Lightroom, không thấy dữ liệu bảng {', '.join(missing)} trên máy "
                       "(Camera Raw tự tìm theo tên bảng)")
    else:
        b.notes.append(f"nền: hồ sơ {name} theo Lightroom")
    return b


# ------------------------------------------------------------------ ghi XMP
def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _alt(tag: str, text: str, ind: str) -> str:
    return (f'{ind}<crs:{tag}>\n{ind} <rdf:Alt>\n{ind}  <rdf:li xml:lang="x-default">{_esc(text)}</rdf:li>\n'
            f"{ind} </rdf:Alt>\n{ind}</crs:{tag}>\n")


def _seq(tag: str, items: list, ind: str) -> str:
    return (f"{ind}<crs:{tag}>\n{ind} <rdf:Seq>\n" + "".join(f"{ind}  <rdf:li>{_esc(x)}</rdf:li>\n" for x in items)
            + f"{ind} </rdf:Seq>\n{ind}</crs:{tag}>\n")


_HEAD = ('<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Editer">\n'
         ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
         '  <rdf:Description rdf:about=""\n    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"')
_TAIL = "  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n"


@dataclass
class Look:
    name: str
    uuid: str
    rgb_id: str
    rgb: str                      # chuỗi Table_ của bảng màu
    base: Base | None = None
    group: str = GROUP
    description: str = ""

    def parameters(self) -> tuple:
        """(thuộc tính, đường cong, bảng) bên trong hồ sơ: nền + bảng màu của Editer."""
        p = {k: v for k, v in (self.base.params if self.base else {}).items()
             if _NAME.match(k) and k not in _RESERVED and not k.startswith(("Supports", "Table_"))}
        p.pop("RGBTable", None)          # một hồ sơ chỉ có một bảng RGB: bảng của Editer thay bảng của nền
        version = p.pop("Version", "14.0")
        pv = p.pop("ProcessVersion", "11.0")
        gray = p.pop("ConvertToGrayscale", "False")
        attrs = [("Version", version), ("ProcessVersion", pv), ("ConvertToGrayscale", gray)]
        attrs += sorted(p.items()) + [("RGBTable", self.rgb_id)]
        tables = {}
        if self.base:
            tables.update({k: v for k, v in self.base.tables.items() if k in p.values()})
        tables[self.rgb_id] = self.rgb
        return attrs, (dict(self.base.curves) if self.base else {}), tables

    @property
    def out_referred(self) -> bool:
        return not (self.base and self.base.raw_only)

    def profile_xmp(self) -> str:
        attrs, curves, tables = self.parameters()
        top = [("PresetType", "Look"), ("Cluster", ""), ("UUID", self.uuid), ("SupportsAmount", "True"),
               ("SupportsColor", "True"), ("SupportsMonochrome", "False"), ("SupportsHighDynamicRange", "True"),
               ("SupportsNormalDynamicRange", "True"), ("SupportsSceneReferred", "True"),
               ("SupportsOutputReferred", "True" if self.out_referred else "False"), ("CameraModelRestriction", ""),
               ("Copyright", ""), ("ContactInfo", "")] + attrs
        x = _HEAD + "".join(f'\n   crs:{k}="{_esc(v)}"' for k, v in top)
        x += "".join(f'\n   crs:Table_{k}="{v}"' for k, v in tables.items()) + ">\n"
        x += _alt("Name", self.name, "   ") + _alt("Group", self.group, "   ")
        if self.description:
            x += _alt("Description", self.description, "   ")
        x += "".join(_seq(k, v, "   ") for k, v in curves.items())
        return x + _TAIL

    def preset_xmp(self, name: str | None = None, uuid: str | None = None) -> str:
        """Preset (bảng Presets) chọn hồ sơ màu này ở 100%; chứa đủ hồ sơ nên chưa cài hồ sơ vẫn dùng được."""
        attrs, curves, tables = self.parameters()
        uuid = uuid or fingerprint(("preset:" + self.uuid).encode())
        top = [("PresetType", "Normal"), ("Cluster", ""), ("UUID", uuid), ("SupportsAmount", "False"),
               ("SupportsColor", "True"), ("SupportsMonochrome", "True"), ("SupportsHighDynamicRange", "True"),
               ("SupportsNormalDynamicRange", "True"), ("SupportsSceneReferred", "True"),
               ("SupportsOutputReferred", "True" if self.out_referred else "False"), ("CameraModelRestriction", ""),
               ("Copyright", ""), ("ContactInfo", ""), ("Version", dict(attrs)["Version"])]
        cam = dict(attrs).get("CameraProfile")
        if cam:
            top.append(("CameraProfile", cam))
        top.append(("HasSettings", "True"))
        x = _HEAD + "".join(f'\n   crs:{k}="{_esc(v)}"' for k, v in top)
        x += "".join(f'\n   crs:Table_{k}="{v}"' for k, v in tables.items()) + ">\n"
        x += _alt("Name", name or self.name, "   ") + _alt("Group", self.group, "   ")
        x += ("   <crs:Look>\n    <rdf:Description"
              f'\n     crs:Name="{_esc(self.name)}"\n     crs:Amount="1"\n     crs:UUID="{self.uuid}"'
              '\n     crs:SupportsAmount="true"\n     crs:SupportsMonochrome="false"'
              f'\n     crs:SupportsOutputReferred="{"true" if self.out_referred else "false"}">\n')
        x += _alt("Group", self.group, "     ")
        x += "     <crs:Parameters>\n      <rdf:Description"
        x += "".join(f'\n       crs:{k}="{_esc(v)}"' for k, v in attrs) + ">\n"
        x += "".join(_seq(k, v, "       ") for k, v in curves.items())
        x += "      </rdf:Description>\n     </crs:Parameters>\n    </rdf:Description>\n   </crs:Look>\n"
        return x + _TAIL


def make_look(table_flat: np.ndarray, name: str, base: Base | None = None, divisions: int = DIVISIONS,
              description: str = "") -> tuple:
    """Bảng màu của bộ máy chép màu (n^3 x 3, sRGB) -> (Look, bảng đã ghi [r,g,b,3]) — đọc lại để chắc chắn đúng."""
    lut = resample(from_flat(table_flat), divisions)
    data = table_bytes(lut)
    rgb_id = fingerprint(data)
    enc = encode(data)
    back = parse_table(decode(enc))
    if float(np.abs(back["lut"] - np.clip(lut, 0, 1)).max()) > 1.0 / 65535 + 1e-9:
        raise RuntimeError("ghi bảng màu bị sai (đọc lại không khớp)")
    uuid = fingerprint(name.encode("utf-8") + data)
    return Look(name, uuid, rgb_id, enc, base, GROUP, description), back["lut"]


def settings_dir() -> Path:
    """Thư mục preset / hồ sơ màu dùng chung của Camera Raw và Lightroom Classic."""
    env = os.environ.get("EDITER_ACR_SETTINGS")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Adobe" / "CameraRaw" / "Settings"
    return Path.home() / "Library" / "Application Support" / "Adobe" / "CameraRaw" / "Settings"


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def file_name(name: str) -> str:
    s = _BAD.sub("-", name).strip(" .")
    return (s or "Editer")[:120]


def install(looks: list, copy_to=None, log=print) -> dict:
    """Ghi hồ sơ màu + preset vào thư mục Camera Raw / Lightroom (nhóm "Editer") và một bản sao để mang đi."""
    dest = settings_dir() / GROUP
    written, copies = [], []
    for lk in looks:
        for folder, bucket in ((dest, written), (Path(copy_to) if copy_to else None, copies)):
            if folder is None:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            stem = file_name(lk.name)
            for suffix, text in ((".xmp", lk.profile_xmp()), (" (preset).xmp", lk.preset_xmp())):
                p = folder / f"{stem}{suffix}"
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(p)
                bucket.append(str(p))
    log(f"Đã ghi {len(looks)} hồ sơ màu + preset (nhóm \"{GROUP}\") vào {dest}"
        + (f"; bản sao ở {copy_to}" if copy_to else ""))
    return {"thu_muc": str(dest), "files": written, "ban_sao": copies}


def default_name() -> str:
    return time.strftime("Editer %d-%m %Hh%M")


def export(scene_looks: list, name: str | None = None, base: Base | None = None, copy_to=None, samples=(),
           log=print) -> dict:
    """Bộ màu của các cảnh (mauanh.analyze(looks=...)) -> hồ sơ màu + preset trong nhóm "Editer".
    Một cảnh: một hồ sơ tên `name`; nhiều cảnh: "<tên> – cả lô" (dùng lại cho buổi khác) + "<tên> – cảnh N"."""
    name = (name or "").strip() or default_name()
    mau = ", ".join(Path(str(s)).stem[:40] for s in samples) or "ảnh mẫu"
    many = len([x for x in scene_looks if x.get("canh")]) > 1
    looks = []
    for x in scene_looks:
        if not many:
            ten = name
        elif x.get("canh"):
            ten = f"{name} – cảnh {x['canh']}"
        else:
            ten = f"{name} – cả lô"
        anh = list(x.get("anh") or [])
        desc = (f"Editer: chép màu kiểu Evoto theo {mau}. "
                + (f"Cảnh {x['canh']}: " if many and x.get("canh") else "Cả lô: ")
                + ", ".join(anh[:6]) + (f" và {len(anh) - 6} ảnh khác" if len(anh) > 6 else ""))
        lk, _ = make_look(x["table"], ten, base, description=desc)
        looks.append(lk)
    no_base = ("không có hồ sơ nền: dùng đúng cho ảnh JPG / TIFF; trên ảnh RAW hồ sơ đặt trên nền Adobe Standard "
               "(nhạt hơn Adobe Color một chút)")
    if base and base.params.get("RGBTable"):
        base.notes.append(f"hồ sơ nền {base.name} có bảng màu RGB riêng — bảng của Editer thay bảng đó, màu có thể "
                          "khác ảnh JPEG một chút (đổi ảnh về Adobe Color rồi chạy lại để khớp hẳn)")
    for n in (base.notes if base else [no_base]):
        log(f"  {n}")
    info = install(looks, copy_to, log)
    info["ten"] = [lk.name for lk in looks]
    info["nen"] = base.name if base else ""
    info["ghi_chu"] = list(base.notes) if base else [no_base]
    return info
