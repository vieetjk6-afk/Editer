"""Test cân bằng trắng gốc (As Shot) khi LibRaw chưa biết máy (máy mới như Nikon Z5 II).

- Ước lượng từ hồ sơ màu Adobe (.dcp, 2 nguồn sáng hiệu chuẩn) phải ra đúng như phép tính
  của Camera Raw (DNG SDK).
- Máy không có .dcp: dùng ma trận dự phòng.
- raw.load: LibRaw không tính được -> tự dùng cách trên.
"""
import os
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from editer_anh import color, raw, wb  # noqa: E402
from make_dng import make_dng  # noqa: E402

CM_A = np.array([[0.95, -0.30, -0.06], [-0.52, 1.30, 0.24], [-0.15, 0.25, 0.80]])     # ma trận ở đèn A
CM_D65 = np.array([[0.99, -0.33, -0.08], [-0.53, 1.33, 0.23], [-0.12, 0.21, 0.76]])   # ma trận ở D65


def write_dcp(path: Path, model: str, cm1, cm2, il1=17, il2=21, name="Adobe Standard") -> None:
    """File .dcp tối thiểu (kiểu TIFF 'IIRC'), đủ các thẻ tool cần."""
    entries, extra = [], b""
    base = 8 + 2 + 12 * 6 + 4

    def add(tag, typ, cnt, payload: bytes):
        nonlocal extra
        if len(payload) <= 4:
            entries.append(struct.pack("<HHI", tag, typ, cnt) + payload.ljust(4, b"\0"))
        else:
            entries.append(struct.pack("<HHII", tag, typ, cnt, base + len(extra)))
            extra += payload + (b"\0" if len(payload) % 2 else b"")

    def srat(m):
        return b"".join(struct.pack("<ii", int(round(v * 10000)), 10000) for v in np.asarray(m).reshape(-1))

    add(50708, 2, len(model) + 1, model.encode() + b"\0")
    add(50721, 10, 9, srat(cm1))
    add(50722, 10, 9, srat(cm2))
    add(50778, 3, 1, struct.pack("<H", il1))
    add(50779, 3, 1, struct.pack("<H", il2))
    add(50936, 2, len(name) + 1, name.encode() + b"\0")
    path.write_bytes(b"IIRC" + struct.pack("<I", 8) + struct.pack("<H", len(entries)) + b"".join(entries)
                     + struct.pack("<I", 0) + extra)


def neutral_at(temp, tint, cm):
    x, y = color.temp_tint_to_xy(temp, tint)
    return cm @ np.array([x / y, 1.0, (1 - x - y) / y])


def test_dcp_dual_illuminant(tmp: Path):
    d = tmp / "profiles" / "Adobe Standard"
    d.mkdir(parents=True)
    write_dcp(d / "Nikon Z5_2 Adobe Standard.dcp", "Nikon Z5_2", CM_A, CM_D65)
    write_dcp(d / "Nikon Z 6 Adobe Standard.dcp", "Nikon Z 6", CM_D65, CM_D65)
    os.environ["EDITER_DCP_DIRS"] = str(tmp / "profiles")
    wb.INDEX = tmp / "dcp-index.json"
    prof = wb.find_dcp("NIKON CORPORATION", "NIKON Z5_2")
    assert prof and prof["model"] == "Nikon Z5_2" and prof["t1"] == 2850 and prof["t2"] == 6500, prof
    for temp, tint in ((3200, 4), (4800, -6), (6500, 10)):
        # máy chụp dưới đèn temp: ma trận đúng = nội suy giữa CM_A và CM_D65 theo 1/nhiệt độ (DNG SDK)
        g = 1.0 if temp <= 2850 else 0.0 if temp >= 6500 else (1 / temp - 1 / 6500) / (1 / 2850 - 1 / 6500)
        n = neutral_at(temp, tint, g * CM_A + (1 - g) * CM_D65)
        t, ti, src = wb.estimate("NIKON CORPORATION", "NIKON Z5_2", 1 / n)
        assert abs(t - temp) < 5 and abs(ti - tint) < 0.5 and "Adobe" in src, (temp, tint, t, ti, src)
    assert wb.INDEX.exists(), "phải nhớ kết quả tìm .dcp"
    print("[cân bằng trắng] hồ sơ màu Adobe 2 nguồn sáng: OK")


def test_fallback_without_dcp(tmp: Path):
    os.environ["EDITER_DCP_DIRS"] = str(tmp / "khong-co")
    wb.INDEX = tmp / "dcp-index-2.json"
    cm = (np.array(wb.FALLBACK["ILCE7M5"][1]) / 10000).reshape(3, 3)
    t, ti, src = wb.estimate("SONY", "ILCE-7M5", 1 / neutral_at(5600, 3, cm))
    assert abs(t - 5600) < 5 and abs(ti - 3) < 0.5 and "dự phòng" in src, (t, ti, src)
    r = wb.estimate("SONY", "ILCE-7M9", [2.3, 1.0, 1.7])            # máy chưa ai biết: theo hãng
    assert r and "cùng hãng" in r[2], r
    assert wb.estimate("Hãng lạ", "Máy lạ", [2.0, 1.0, 1.5]) is None
    print("[cân bằng trắng] ma trận dự phòng (máy mới): OK")


def test_raw_load_fallback(tmp: Path):
    """LibRaw không tính được As Shot -> raw.load tự dùng hồ sơ màu Adobe của máy."""
    p = tmp / "IMG_0001.dng"
    make_dng(p, 4300, 6, seed=2)
    ok = raw.load(p)
    assert ok.wb_src == "libraw" and ok.wb_mult is not None, (ok.wb_src, ok.wb_mult)
    # Hồ sơ màu cho đúng máy giả lập (cùng ma trận màu của file DNG) -> phải ra cùng kết quả
    cm = np.linalg.inv(color._M_RGB_XYZ)
    cm = cm / (cm @ np.array([0.95047, 1.0, 1.08883])).max()
    d = tmp / "p2"
    d.mkdir()
    write_dcp(d / "Editer TestCam Adobe Standard.dcp", "Editer TestCam", cm, cm)
    os.environ["EDITER_DCP_DIRS"] = str(d)
    wb.INDEX = tmp / "dcp-index-3.json"
    orig = raw._as_shot
    raw._as_shot = lambda r: (None, None)          # giả lập: LibRaw không có ma trận cho máy này
    try:
        ph = raw.load(p)
    finally:
        raw._as_shot = orig
    assert ph.as_shot_temp and "Adobe" in (ph.wb_src or ""), (ph.as_shot_temp, ph.wb_src)
    assert abs(ph.as_shot_temp - ok.as_shot_temp) < 30 and abs(ph.as_shot_tint - ok.as_shot_tint) < 1.5, \
        (ph.as_shot_temp, ok.as_shot_temp, ph.as_shot_tint, ok.as_shot_tint)
    assert any("hồ sơ màu Adobe" in s for s in raw.wb_summary([ph]))
    print(f"[cân bằng trắng] raw.load tự dùng hồ sơ màu: {ph.as_shot_temp:.0f}K (LibRaw: {ok.as_shot_temp:.0f}K): OK")


def test_calibration_memory(tmp: Path):
    """Nhật ký thật (Nikon Z5 II): tool tính As Shot 5260K tint -29, Camera Raw báo 5450K tint +21.
    Đo một lần bằng Camera Raw -> nhớ -> lần sau (kể cả không mở Photoshop) khớp Camera Raw khi chép màu;
    máy có trong LibRaw thì không đụng tới."""
    wb.CALIB = tmp / "wb-calib.json"
    assert wb.calibrated("NIKON Z5_2", 5260, -29, "hồ sơ màu Adobe") == (5260, -29)
    wb.save_calib("NIKON Z5_2", 1e6 / 5450 - 1e6 / 5260, 21 - (-29))
    t, ti = wb.calibrated("NIKON Z5_2", 5260, -29, "hồ sơ màu Adobe")
    assert abs(t - 5450) < 2 and abs(ti - 21) < 0.1, (t, ti)
    assert wb.calibrated("NIKON Z5_2", 5260, -29, "libraw") == (5260, -29)
    wb.save_calib("NIKON Z5_2", 0.0, -1.0)                      # lần đo sau: cộng dồn phần còn lệch
    assert abs(wb.calib_of("NIKON Z5_2")[1] - 49.0) < 1e-6
    print("[cân bằng trắng] nhớ độ lệch so với Camera Raw: OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as dd:
        test_dcp_dual_illuminant(Path(dd))
        test_fallback_without_dcp(Path(dd))
        test_raw_load_fallback(Path(dd))
        test_calibration_memory(Path(dd))
    print("WB OK")
