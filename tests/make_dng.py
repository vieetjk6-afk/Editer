"""Tạo file DNG giả lập (không cần máy ảnh) để test luồng đọc RAW.

Camera giả có không gian màu = sRGB tuyến tính, chụp dưới nguồn sáng (temp, tint)
cho trước. Đọc lại phải ra As Shot gần đúng (temp, tint) đó.
"""
import struct
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from editer_anh import color  # noqa: E402


def make_dng(path, temp=3200, tint=5, w=256, h=192, seed=0, exposure=1.0):
    rng = np.random.default_rng(seed)
    xyz_rgb = color._M_RGB_XYZ  # camera RGB (= sRGB tuyến tính) -> XYZ
    ill_xy = color.temp_tint_to_xy(temp, tint)
    ill_xyz = np.array([ill_xy[0] / ill_xy[1], 1.0, (1 - ill_xy[0] - ill_xy[1]) / ill_xy[1]])
    neutral = np.linalg.solve(xyz_rgb, ill_xyz)
    neutral /= neutral.max()
    # cảnh: các mảng màu ngẫu nhiên + một mảng xám lớn
    refl = np.empty((h, w, 3))
    for _ in range(40):
        y0, x0 = rng.integers(0, h - 20), rng.integers(0, w - 20)
        refl[y0:y0 + rng.integers(20, 80), x0:x0 + rng.integers(20, 80)] = rng.uniform(0.05, 0.9, 3)
    refl[: h // 3, : w // 3] = 0.18
    cam = np.clip(refl * neutral * exposure, 0, 1)
    cfa = np.empty((h, w))
    cfa[0::2, 0::2] = cam[0::2, 0::2, 0]
    cfa[0::2, 1::2] = cam[0::2, 1::2, 1]
    cfa[1::2, 0::2] = cam[1::2, 0::2, 1]
    cfa[1::2, 1::2] = cam[1::2, 1::2, 2]
    data = (cfa * 65535).astype("<u2").tobytes()
    cm = np.linalg.inv(xyz_rgb)  # XYZ -> camera (ColorMatrix)
    cm = cm / (cm @ np.array([0.95047, 1.0, 1.08883])).max()

    entries = []  # (tag, type, count, bytes)
    def add(tag, typ, vals):
        fmt = {1: "B", 2: "s", 3: "H", 4: "I", 5: "II", 10: "ii"}[typ]
        if typ == 2:
            b = vals.encode() + b"\0"; cnt = len(b)
        elif typ in (5, 10):
            b = b"".join(struct.pack("<" + fmt, *v) for v in vals); cnt = len(vals)
        else:
            b = struct.pack("<%d%s" % (len(vals), fmt), *vals); cnt = len(vals)
        entries.append((tag, typ, cnt, b))
    rat = lambda x: (int(round(x * 10000)), 10000)
    add(254, 4, [0]); add(256, 4, [w]); add(257, 4, [h]); add(258, 3, [16]); add(259, 3, [1])
    add(262, 3, [32803]); add(271, 2, "Editer"); add(272, 2, "TestCam"); add(273, 4, [0])
    add(274, 3, [1]); add(277, 3, [1]); add(278, 4, [h]); add(279, 4, [len(data)]); add(284, 3, [1])
    add(33421, 3, [2, 2]); add(33422, 1, [0, 1, 1, 2])
    add(50706, 1, [1, 4, 0, 0]); add(50708, 2, "Editer TestCam")
    add(50714, 4, [0]); add(50717, 4, [65535])
    add(50721, 10, [rat(v) for v in cm.flatten()])
    add(50728, 5, [rat(v) for v in neutral]); add(50778, 3, [21])
    entries.sort()
    n = len(entries)
    ifd_off = 8
    extra_off = ifd_off + 2 + n * 12 + 4
    extra = b""
    ifd = struct.pack("<H", n)
    strip_entry_idx = None
    for i, (tag, typ, cnt, b) in enumerate(entries):
        if len(b) <= 4:
            ifd += struct.pack("<HHI", tag, typ, cnt) + b.ljust(4, b"\0")
        else:
            ifd += struct.pack("<HHII", tag, typ, cnt, extra_off + len(extra))
            extra += b + (b"\0" if len(b) % 2 else b"")
        if tag == 273:
            strip_entry_idx = i
    ifd += struct.pack("<I", 0)
    data_off = extra_off + len(extra)
    ifd = bytearray(ifd)
    pos = 2 + strip_entry_idx * 12 + 8
    ifd[pos:pos + 4] = struct.pack("<I", data_off)
    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", ifd_off) + bytes(ifd) + extra + data)


if __name__ == "__main__":
    make_dng(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
