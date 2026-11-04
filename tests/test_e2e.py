"""Thử toàn bộ: tạo job giả (DNG + XMP theo một quy luật biết trước),
học, rồi chỉnh lô mới và kiểm tra có tìm ra quy luật đó không."""
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
from make_dng import make_dng  # noqa: E402
from editer_anh import color, xmp  # noqa: E402

WORK = Path(sys.argv[1])
TEMPLATE_XMP = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "tests/fixtures/look-mau.xmp")
shutil.rmtree(WORK, ignore_errors=True)
(WORK / "job").mkdir(parents=True)
(WORK / "moi").mkdir()
rng = np.random.default_rng(42)
tpl = Path(TEMPLATE_XMP).read_text(encoding="utf-8")


def rule(temp, ev):
    """'Thói quen' của thợ: ấm hơn As Shot 15 mired, ảnh tối thì bù sáng."""
    return 1e6 / (1e6 / temp - 15), -0.8 * np.log2(ev)


def make(folder, i, t0):
    temp = rng.uniform(2800, 7500)
    tint = rng.uniform(-10, 15)
    ev = 2 ** rng.uniform(-1.2, 0.3)
    p = folder / f"IMG_{i:04d}.dng"
    make_dng(p, temp, tint, seed=i, exposure=ev)
    ts = t0 + i * 20
    os.utime(p, (ts, ts))
    return p, temp, tint, ev


for i in range(120):
    p, temp, tint, ev = make(WORK / "job", i, 1_700_000_000)
    T, E = rule(temp, ev)
    s = re.sub(r'crs:Temperature="[^"]*"', f'crs:Temperature="{T:.0f}"', tpl)
    s = re.sub(r'crs:Tint="[^"]*"', f'crs:Tint="{tint + 3:+.0f}"', s)
    s = re.sub(r'crs:Exposure2012="[^"]*"', f'crs:Exposure2012="{E:+.2f}"', s)
    p.with_suffix(".xmp").write_text(s, encoding="utf-8")

truth = {}
for i in range(1000, 1030):
    p, temp, tint, ev = make(WORK / "moi", i, 1_700_100_000 + (0 if i < 1015 else 3600))
    truth[p.name] = (rule(temp, ev), tint + 3)

run = lambda *a: subprocess.run([sys.executable, "-m", "editer_anh", *a], cwd=ROOT, check=True)
run("hoc", str(WORK / "job"), "-o", str(WORK / "pc.editer"))
run("chinh", str(WORK / "moi"), "--phong-cach", str(WORK / "pc.editer"))

errT, errE = [], []
for f in sorted((WORK / "moi").glob("*.xmp")):
    s = xmp.read_settings(f)["crs"]
    (T, E), tint = truth[f.with_suffix(".dng").name]
    errT.append(abs(color.mired(float(s["Temperature"])) - color.mired(T)))
    errE.append(abs(float(s["Exposure2012"]) - E))
    assert s["CameraProfile"] == "DVLOP III", "phải giữ look"
    assert "Crop" not in "".join(k for k in s if k.startswith("Crop"))
print(f"Lệch nhiệt độ TB: {np.mean(errT):.1f} mired, lệch exposure TB: {np.mean(errE):.2f}")
assert len(errT) == 30
assert np.mean(errT) < 8, "phải học được quy luật nhiệt độ"
assert np.mean(errE) < 0.25, "phải học được quy luật exposure"

# Lần 2 không ghi đè
run("chinh", str(WORK / "moi"), "--phong-cach", str(WORK / "pc.editer"))
assert not list((WORK / "moi").glob("*.bak"))

# Chỉnh theo JPG mẫu
import skimage.data as d
from PIL import Image
Image.fromarray(d.coffee()).save(WORK / "mau.jpg", quality=92)
run("chinh", str(WORK / "moi"), "--mau-jpg", str(WORK / "mau.jpg"), "--phong-cach", str(WORK / "pc.editer"), "--ghi-de")
assert len(list((WORK / "moi").glob("*.xmp.bak"))) == 30
s = xmp.read_settings(sorted((WORK / "moi").glob("*.xmp"))[0])["crs"]
assert s["CameraProfile"] == "Adobe Color" and s["ToneCurveName2012"] == "Custom"
print("E2E OK")
