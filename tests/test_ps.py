"""Test cầu nối Photoshop bằng một "Photoshop giả" (máy test không có Photoshop).

Photoshop giả nhận đúng đoạn ExtendScript tool gửi, đọc file .xmp tool ghi cạnh bản sao
ảo của RAW, "dựng" ảnh bằng Camera Raw giả (khác mô phỏng của tool), lưu PNG. Như vậy
test được trọn đường đi: hard link -> ghi .xmp -> dựng -> đọc PNG -> dọn dẹp -> vòng kiểm tra.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from editer_anh import cli, look, raw, xmp  # noqa: E402
from editer_anh import ps as psmod  # noqa: E402
from editer_anh.look import Look, Shot  # noqa: E402
from make_dng import make_dng  # noqa: E402
from test_look import FAKE, render, scene  # noqa: E402


class FakePhotoshopApp:
    """Camera Raw giả: chỉ áp thông số toàn ảnh, KHÔNG áp mask AI (như khi Camera Raw dựng tự động
    mà không chạy được AI) -> tool phải tự phát hiện và chỉ chỉnh toàn ảnh."""

    def __init__(self):
        self.calls, self.seen_xmp, self.seen_local = 0, [], []

    def DoJavaScript(self, code):
        if code.strip() == "app.version":
            return "27.0 (giả)"
        src = json.loads(re.search(r"app\.open\(new File\((\".*?\")\)\)", code).group(1))
        dst = json.loads(re.search(r"d\.saveAs\(new File\((\".*?\")\)", code).group(1))
        size = int(re.search(r"var w = .*?, s = (\d+) /", code).group(1))
        side = Path(src).with_suffix(".xmp")
        assert side.exists(), "tool phải ghi .xmp cạnh bản sao RAW trước khi nhờ Photoshop dựng"
        crs = xmp.read_settings(side)["crs"]
        self.seen_xmp.append(crs)
        self.seen_local.append(xmp.read_local(side))
        lk, sh = look.from_crs(crs, xmp.read_curve(side))
        ph = raw.load(src)
        cam = Shot(1e6 / ph.as_shot_temp, ph.as_shot_tint, 0.0)
        if sh.mired is None:
            sh = Shot(cam.mired, cam.tint, sh.ev)
        base = render(ph.preview, Look(curve=np.array([0.0, 40, 92, 166, 216, 255]), sat=np.full(8, 12.0)),
                      cam, look.DEFAULT, base_shot=cam)
        out = render(base, lk, Shot(sh.mired, sh.tint, sh.ev + 0.15), FAKE, base_shot=cam)
        im = Image.fromarray(out)
        im.thumbnail((size, size))
        im.save(dst)
        self.calls += 1
        return "ok"


def test_render_raw_cleans_up(tmp: Path):
    p = tmp / "IMG_0001.dng"
    make_dng(p, 4000, 5, seed=1)
    app = FakePhotoshopApp()
    ps_ = psmod.Photoshop(app)
    assert ps_.version().startswith("27")
    vals, crv = look.xmp_values(Look(sat=np.full(8, 20.0)), Shot(1e6 / 4100, 2.0, 0.3))
    img = ps_.render_raw(p, xmp.build_xmp(None, vals, crv), psmod.workdir_for(tmp), 512)
    assert img.ndim == 3 and img.shape[2] == 3 and max(img.shape[:2]) <= 512
    assert app.seen_xmp[-1]["Temperature"] == "4100" and app.seen_xmp[-1]["SaturationAdjustmentRed"] == "+20"
    left = list(psmod.workdir_for(tmp).iterdir())
    assert not left, f"còn sót file tạm: {left}"
    assert not p.with_suffix(".xmp").exists(), "không được đụng vào .xmp của ảnh gốc"
    print("[photoshop] dựng + dọn dẹp: OK")


def test_cli_with_fake_photoshop(tmp: Path):
    folder = tmp / "job"
    folder.mkdir()
    for i in range(5):
        p = folder / f"IMG_{i:04d}.dng"
        make_dng(p, 3800 + 100 * i, 4, seed=10 + i, exposure=0.7)
        os.utime(p, (1_700_000_000 + 20 * i,) * 2)
    # ảnh mẫu: một cảnh khác được "Camera Raw giả" chỉnh màu ấm + nhấc đen
    ref_img, _ = scene(31)
    ref_lk = Look(curve=np.array([20.0, 60, 100, 158, 200, 240]), grade=np.array([-4, -6, 0, 0, 6, 10.0]),
                  sat=np.array([0, 10, 0, -40, 0, -20, 0, 0.0]))
    Image.fromarray(render(ref_img, ref_lk, Shot(1e6 / 5500, 0.0, 0.1), FAKE)).save(tmp / "mau.jpg", quality=95)
    app = FakePhotoshopApp()
    orig = psmod.check
    psmod.check = lambda: (psmod.Photoshop(app), "Photoshop 27.0 (giả)")
    logs = []
    orig_log = cli._log
    cli._log = lambda m: (logs.append(m), orig_log(m))
    try:
        cli.main(["chinh", str(folder), "--mau-jpg", str(tmp / "mau.jpg"), "--photoshop", "--so-luot", "2",
                  "--so-anh-dai-dien", "1", "--co-anh", "256", "--luu-preset", "thử nghiệm"])
    finally:
        psmod.check = orig
        cli._log = orig_log
    out = sorted(folder.glob("*.xmp"))
    assert len(out) == 5, out
    assert app.calls >= 2, "Photoshop phải được nhờ dựng ít nhất 2 lượt"
    assert not psmod.workdir_for(folder).exists(), "phải xoá thư mục tạm .editer-tam"
    assert any("KẾT QUẢ ĐO TRÊN CAMERA RAW" in m for m in logs)
    assert any(app.seen_local), "phải thử mask AI (ghi chỉnh cục bộ vào .xmp dựng thử)"
    mlog = [m for m in logs if m.startswith("Chỉnh riêng từng phần bằng mask AI")]
    assert mlog and "nền không" in mlog[0], logs
    locs = [xmp.read_local(o) for o in out]
    assert not any("Editer - Nền" in lc for lc in locs), "nền: Camera Raw không áp mask -> không ghi"
    if any("Editer - Da" in lc for lc in locs):   # da: chỉ khi đã báo rõ là "theo mô phỏng"
        assert "theo mô phỏng" in mlog[0], mlog
    crs = xmp.read_settings(out[0])["crs"]
    assert "SplitToningHighlightHue" in crs and crs["ToneCurveName2012"] == "Custom"
    preset = xmp.preset_dir() / "Editer - thử nghiệm.xmp"
    assert preset.exists(), preset
    preset.unlink()
    print(f"[photoshop] chép màu qua 'Photoshop giả': {app.calls} lần dựng, ghi {len(out)} .xmp: OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        os.environ["APPDATA"] = str(Path(d) / "appdata")   # preset ghi vào thư mục tạm
        test_render_raw_cleans_up(Path(d))
        test_cli_with_fake_photoshop(Path(d))
    print("PS OK")
