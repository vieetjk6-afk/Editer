"""Hồ sơ màu + preset cho Camera Raw / Lightroom (editer_anh.acr): định dạng bảng, XMP, nền Adobe Color, xuất file."""
import os
import struct
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from editer_anh import acr, color, mauanh as M  # noqa: E402

CRS = "{http://ns.adobe.com/camera-raw-settings/1.0/}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"

# Bảng đồng nhất 17^3 đúng như RAWmakase (MIT) ghi cho một .cube đồng nhất — hồ sơ đó Lightroom / Camera Raw đọc được
# (RAWmakase đo trên Camera Raw 18.7). Dữ liệu tổng hợp, không có dữ liệu của Adobe.
IDENTITY17_ID = "D5536083C5B64084F45CB6FF124AA7E6"
IDENTITY17 = ("r7400xHbN:9'hoaYQjtYSsU^{u.nBP+0)NW'{ncWp4100000000000000000000000000000000'27tR[DpRtikx9")


def _identity(n):
    v = np.linspace(0, 1, n)
    return np.stack(np.meshgrid(v, v, v, indexing="ij"), axis=-1)


def test_format() -> None:
    # base85 kiểu Adobe: nhóm 4 byte little-endian, chữ số nhỏ trước, nhóm cuối k byte -> k+1 chữ
    assert acr.b85decode("00000") == b"\0\0\0\0" and acr.b85decode("10000") == b"\1\0\0\0"
    assert list(acr.b85decode("120")) == [171, 0] and acr.b85encode(bytes([171, 0])) == "120"
    for k in range(1, 10):
        d = bytes(range(7, 7 + k)) * 5
        assert acr.decode(acr.encode(d)) == d
    assert all(c not in acr.ALPHABET for c in '&<>"'), "chuỗi bảng nằm trong thuộc tính XML"
    # bố cục bảng: đầu (loại 1, phiên bản 1, 3 chiều, n), mẫu u16 là hiệu so với bảng đồng nhất, đuôi sRGB
    data = acr.table_bytes(_identity(2))
    assert data == struct.pack("<4I", 1, 1, 3, 2) + b"\0" * 48 + struct.pack("<3I2d", 0, 1, 0, 0.0, 2.0)
    # bảng đồng nhất 17^3: trùng từng byte với hồ sơ RAWmakase đã kiểm trên Camera Raw
    data = acr.table_bytes(_identity(17))
    assert acr.fingerprint(data) == IDENTITY17_ID and acr.decode(IDENTITY17) == data
    # thứ tự nút: R ngoài cùng, B trong cùng; bảng màu của bộ máy (thứ tự Pillow) đổi đúng
    rng = np.random.default_rng(3)
    lut = np.clip(_identity(17) + rng.normal(0, 0.05, (17, 17, 17, 3)), 0, 1)
    back = acr.parse_table(acr.decode(acr.encode(acr.table_bytes(lut))))
    assert np.abs(back["lut"] - lut).max() <= 0.5 / 65535 + 1e-12
    assert (back["primaries"], back["gamma"], back["gamut"], back["bounds"]) == (acr.SRGB, acr.GAMMA_SRGB, acr.CLIP,
                                                                              (0.0, 2.0))
    flat = M._grid(5) ** 1.3                                      # bảng thứ tự Pillow: R đổi nhanh nhất
    t = acr.from_flat(flat)
    v = np.linspace(0, 1, 5)
    assert np.allclose(t[4, 1, 2], [v[4] ** 1.3, v[1] ** 1.3, v[2] ** 1.3])
    # nội suy tứ diện (Camera Raw) đúng ở nút và trên bảng tuyến tính
    x = rng.random((500, 3))
    lin = _identity(9) * 0.8 + 0.1
    assert np.allclose(acr.tetrahedral(lin, x), x * 0.8 + 0.1) and np.allclose(acr.trilinear(lin, x), x * 0.8 + 0.1)
    print("[acr] định dạng bảng màu (base85 Adobe, zlib, MD5, thứ tự nút) khớp hồ sơ Camera Raw đọc được: OK")


def _fake_adobe_color(folder: Path) -> str:
    """Hồ sơ "Adobe Color" giả trong thư mục Adobe: có bảng LookTable + đường cong như bản thật."""
    blob = b"bang HSV gia" * 64
    fp = acr.fingerprint(blob)
    d = folder / "Settings" / "Adobe" / "Profiles" / "Adobe Raw"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Adobe Color.xmp").write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:PresetType="Look"'
        ' crs:Cluster="" crs:UUID="B952" crs:SupportsAmount="False" crs:SupportsMonochrome="False"'
        ' crs:SupportsOutputReferred="False" crs:Copyright="Adobe" crs:Version="14.0" crs:ProcessVersion="11.0"'
        f' crs:ConvertToGrayscale="False" crs:CameraProfile="Adobe Standard" crs:LookTable="{fp}"'
        f' crs:Table_{fp}="{acr.encode(blob)}"><crs:Name><rdf:Alt><rdf:li xml:lang="x-default">Adobe Color</rdf:li>'
        '</rdf:Alt></crs:Name><crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>22, 16</rdf:li>'
        '<rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012></rdf:Description></rdf:RDF></x:xmpmeta>',
        encoding="utf-8")
    return fp


def _scene_looks():
    """Hai cảnh khác nhau (cách nhau 30 phút) -> bộ màu từng cảnh + một bộ chung (mauanh.analyze(looks=...))."""
    from test_look import scene
    from test_mauanh import ref_of
    _, _, ref = ref_of(71, skin_lab=(66, 16, 15))
    a, ra = scene(72, light_k=4800)
    b, rb = scene(73, light_k=3400)
    photos = [M.Anh(0, a, ra, 1000.0, None, "anh0"), M.Anh(1, a[:, ::-1].copy(), ra[:, ::-1].copy(), 1020.0, None,
                                                              "anh1"),
              M.Anh(2, b, rb, 1000.0 + 1800, None, "anh2")]
    looks = []
    plans = M.analyze(photos, [ref], 0.9, log=lambda m: None, looks=looks)
    return photos, plans, looks


def test_export(tmp: Path) -> None:
    os.environ["EDITER_ACR_SETTINGS"] = str(tmp / "acr")
    os.environ["EDITER_ADOBE_PROFILES"] = str(tmp / "adobe")
    fp = _fake_adobe_color(tmp / "adobe")
    photos, plans, looks = _scene_looks()
    assert [x["canh"] for x in looks] == [0, 1, 2] and looks[0]["anh"] == [p.ten for p in photos], looks
    # nền theo Lightroom (Look của ảnh có Parameters): đường cong dạng mảng số -> chuỗi "x, y"; dữ liệu bảng LookTable
    # lấy từ hồ sơ trên máy theo tên bảng
    base = acr.base_from_lr({"Name": "Adobe Color", "Amount": 1, "Parameters": {
        "CameraProfile": "Adobe Standard", "LookTable": fp, "ToneCurvePV2012": [0, 0, 22, 16, 40, 35, 255, 255],
        "Version": "14.0", "ProcessVersion": "11.0", "ConvertToGrayscale": False}})
    assert base and base.curves["ToneCurvePV2012"] == ["0, 0", "22, 16", "40, 35", "255, 255"] and fp in base.tables
    # chỉ có tên (Lightroom không đưa Parameters): tìm hồ sơ trên máy; không có trên máy: không nền
    only = acr.base_from_lr({"Name": "Adobe Color"})
    assert only and only.params["LookTable"] == fp and only.curves["ToneCurvePV2012"][1] == "22, 16"
    assert acr.base_from_lr({"Name": "Không có"}) is None and acr.base_from_lr(None) is None
    info = acr.export(looks, "Cưới Hà", base, tmp / "ban-sao", ["/x/mau-1.jpg"], log=lambda m: None)
    assert info["ten"] == ["Cưới Hà – cả lô", "Cưới Hà – cảnh 1", "Cưới Hà – cảnh 2"], info["ten"]
    names = sorted(Path(f).name for f in info["files"])
    assert names == sorted([f"{n}.xmp" for n in info["ten"]] + [f"{n} (preset).xmp" for n in info["ten"]]), names
    assert all(Path(f).parent == tmp / "acr" / "Editer" for f in info["files"]) and len(info["ban_sao"]) == 6
    for f, x in zip(info["files"][0::2], looks):
        root = ET.parse(f).getroot()                                      # XML hợp lệ
        d = root.find(f"{RDF}RDF/{RDF}Description")
        assert d.get(f"{CRS}PresetType") == "Look" and d.get(f"{CRS}SupportsAmount") == "True"
        assert d.get(f"{CRS}SupportsOutputReferred") == "False", "nền Adobe Color: hồ sơ cho ảnh RAW"
        assert d.find(f"{CRS}Name/{RDF}Alt/{RDF}li").text == Path(f).stem
        assert d.find(f"{CRS}Group/{RDF}Alt/{RDF}li").text == "Editer"
        lk = acr.read_look(f)
        assert lk.params["CameraProfile"] == "Adobe Standard" and lk.params["LookTable"] == fp and fp in lk.tables
        assert lk.curves["ToneCurvePV2012"] == ["0, 0", "22, 16", "40, 35", "255, 255"]
        rid = lk.params["RGBTable"]
        data = acr.decode(lk.tables[rid])
        assert acr.fingerprint(data) == rid
        tab = acr.parse_table(data)["lut"]
        # Camera Raw (nội suy tứ diện) đọc bảng 17 nút ra gần như đúng bảng của bộ máy chép màu (tam tuyến tính):
        # đo được TB 0,03-0,14, p99 <= 1,0 trên ảnh tổng hợp màu rực (ảnh thật: TB 0,05, p99 0,4-0,7)
        for p in photos:
            px = p.small.reshape(-1, 3) / 255.0
            de = color.delta_e2000(color.srgb_to_lab(acr.trilinear(acr.from_flat(x["table"]), px)),
                                   color.srgb_to_lab(acr.tetrahedral(tab, px)))
            assert de.mean() < 0.2 and np.percentile(de, 99) < 1.5, (f, de.mean(), np.percentile(de, 99))
    # bộ màu của cảnh = bảng của ảnh có độ sáng chuẩn của cảnh (không cân sáng thêm)
    k0 = plans[photos[0].i]
    if not any("EV" in n for n in k0.notes):
        assert np.abs(k0.table - looks[1]["table"]).max() < 1e-6
    # preset: chọn hồ sơ màu (Look có đủ tham số) + bảng ở cấp trên cùng, như Lightroom ghi
    pr = ET.parse(info["files"][1]).getroot().find(f"{RDF}RDF/{RDF}Description")
    assert pr.get(f"{CRS}PresetType") == "Normal" and pr.get(f"{CRS}HasSettings") == "True"
    assert pr.get(f"{CRS}CameraProfile") == "Adobe Standard" and pr.get(f"{CRS}Table_{fp}")
    lk_el = pr.find(f"{CRS}Look/{RDF}Description")
    lk0 = acr.read_look(info["files"][0])
    uuid0 = ET.parse(info["files"][0]).getroot().find(f"{RDF}RDF/{RDF}Description").get(f"{CRS}UUID")
    assert lk_el.get(f"{CRS}Name") == "Cưới Hà – cả lô" and lk_el.get(f"{CRS}UUID") == uuid0 and len(uuid0) == 32
    par = lk_el.find(f"{CRS}Parameters/{RDF}Description")
    assert par.get(f"{CRS}RGBTable") == lk0.params["RGBTable"] and pr.get(f"{CRS}Table_{lk0.params['RGBTable']}")
    assert par.find(f"{CRS}ToneCurvePV2012/{RDF}Seq") is not None
    # chạy lại cùng tên: thay file cũ (không sinh thêm), một cảnh: một hồ sơ đúng tên
    info2 = acr.export(looks[1:2], "Cưới Hà – cảnh 1", base, None, [], log=lambda m: None)
    assert info2["ten"] == ["Cưới Hà – cảnh 1"] and len(list((tmp / "acr" / "Editer").glob("*.xmp"))) == 6
    # không có nền (ảnh JPG): hồ sơ dùng được cả cho ảnh JPG / TIFF
    info3 = acr.export(looks[1:2], "Ảnh JPG", None, None, [], log=lambda m: None)
    d = ET.parse(info3["files"][0]).getroot().find(f"{RDF}RDF/{RDF}Description")
    assert d.get(f"{CRS}SupportsOutputReferred") == "True" and d.get(f"{CRS}LookTable") is None
    assert acr.file_name('a/b:c*?"<>|') == "a-b-c------" and acr.file_name("  ") == "Editer"
    print("[acr] hồ sơ màu + preset (cả lô + từng cảnh), giữ nền Adobe Color, Camera Raw đọc lệch TB ΔE < 0,2: OK")


def main(tmp: Path) -> None:
    t0 = time.time()
    test_format()
    test_export(tmp)
    print(f"ACR OK ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        main(Path(d))
