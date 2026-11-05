"""Test phần "não" của giao diện (gui/api.py) — không cần mở cửa sổ."""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from make_dng import make_dng  # noqa: E402
from editer_anh.gui.api import Api  # noqa: E402


def wait(api: Api, limit: float = 600) -> dict:
    t, lines = time.time(), []
    while True:
        st = api.trang_thai()
        lines += st["dong"]
        if not st["chay"]:
            st["tat_ca"] = lines
            return st
        assert time.time() - t < limit, "chạy quá lâu"
        time.sleep(0.2)


def main(tmp: Path) -> None:
    os.environ["APPDATA"] = str(tmp / "appdata")
    job = tmp / "job"
    job.mkdir()
    for i in range(4):
        make_dng(job / f"IMG_{i:04d}.dng", 3600 + 300 * i, 3, seed=40 + i, exposure=0.8)
    from PIL import Image
    import skimage.data as d
    Image.fromarray(d.coffee()).save(tmp / "mau.jpg", quality=92)

    api = Api()
    info = api.info()
    assert info["phien_ban"] and "phong_cach" in info
    f = api.xem_thu_muc(str(job))
    assert f["ok"] and f["so_raw"] == 4 and f["so_xmp"] == 0 and len(f["anh"]) == 4, f
    assert api.xem_thu_muc(str(job / "IMG_0000.dng"))["path"] == str(job), "thả file RAW -> lấy thư mục chứa nó"
    im = api.xem_anh([str(tmp / "mau.jpg"), str(job / "IMG_0000.dng")])
    assert len(im) == 1 and im[0]["anh"].startswith("data:image/jpeg;base64,")
    k = api.phan_loai([str(job), str(tmp / "mau.jpg"), str(job / "IMG_0001.dng")])
    assert k == {"thu_muc": [str(job)], "anh": [str(tmp / "mau.jpg")], "raw": [str(job / "IMG_0001.dng")]}, k
    assert api.xem_raw(str(job / "IMG_0002.dng"))["ok"]

    r = api.bat_dau("chep-mau", {"thu_muc": str(job), "anh_mau": [str(tmp / "mau.jpg")], "photoshop": False,
                                 "do_manh": 0.9, "preset": "thử giao diện"})
    assert r["ok"], r
    assert not api.bat_dau("chep-mau", {})["ok"], "không được chạy 2 việc cùng lúc"
    st = wait(api)
    assert st["loi"] is None, st["loi"]
    res = st["ket_qua"]
    assert res["ghi"] == 4 and res["csv"] and res["chep_mau"]["mo_phong"], res
    a0 = res["chep_mau"]["anh_dai_dien"][0]
    assert a0["goc"].startswith("data:image") and a0["ket_qua"].startswith("data:image"), "ảnh trước/sau phải là ảnh nhỏ"
    assert any("đã đọc" in line for line in st["tat_ca"]), "phải nhận được dòng tiến độ"
    assert len(list(job.glob("*.xmp"))) == 4

    # Đồng bộ từ ảnh đã chỉnh: bạn mở IMG_0001 trong Camera Raw, cân lại, bấm Done (Camera Raw ghi lại .xmp)
    from editer_anh import ps
    side = job / "IMG_0001.xmp"
    text = re.sub(r'x:xmptk="Editer[^"]*"', 'x:xmptk="Adobe XMP Core 9.1"', side.read_text(encoding="utf-8"))
    side.write_text(text.replace("<rdf:Description ", '<rdf:Description crs:RawFileName="IMG_0001.dng" ', 1),
                    encoding="utf-8")
    f = api.xem_thu_muc(str(job))
    assert f["so_ban_chinh"] == 1 and f["so_xmp"] == 4, {k: v for k, v in f.items() if k != "anh"}
    opened, real_open = [], ps.open_files
    ps.open_files = lambda files, limit=60: (opened.extend(files), len(files))[1]
    try:
        r = api.mo_moi_canh(str(job))
    finally:
        ps.open_files = real_open
    assert r["ok"] and [Path(p).name for p in opened] == ["IMG_0001.dng"] and r.get("ghi_chu"), (r, opened)
    assert api.bat_dau("dong-bo", {"thu_muc": str(job)})["ok"]
    st = wait(api)
    assert st["loi"] is None, st["loi"]
    rd = st["ket_qua"]
    assert rd["so_mau"] == 1 and rd["ghi"] + rd["de"] == 3 and all(a["mau"] == "IMG_0001.dng" for a in rd["anh"]), rd

    # Lightroom Classic: cài plugin vào thư mục Modules (APPDATA giả)
    assert api.info()["lightroom"]["da_cai"] is False
    r = api.lr_cai()
    assert r["ok"] and r["da_cai"], r
    assert (tmp / "appdata" / "Adobe" / "Lightroom" / "Modules" / "Editer.lrplugin" / "Info.lua").is_file()
    assert api.info()["lightroom"]["phien_ban"]

    # việc lỗi (không có Photoshop trên máy test) -> báo lỗi rõ ràng, không làm sập
    api.bat_dau("kiem-tra-ps", {"raw": str(job / "IMG_0000.dng")})
    st = wait(api, 120)
    if sys.platform != "win32":
        assert st["loi"] and "Photoshop" in st["loi"], st
    print("[giao diện] api: OK —", res["ghi"], "ảnh, ΔE mô phỏng:", res["chep_mau"]["mo_phong"])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as dd:
        main(Path(dd))
    print("GUI API OK")
