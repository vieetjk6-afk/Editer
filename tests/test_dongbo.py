"""Test "Đồng bộ từ ảnh bạn đã chỉnh": mỗi cảnh 1 ảnh bạn chỉnh trong Camera Raw -> cả cảnh theo đúng
thông số đó, chỉ cân lại nhiệt độ màu / độ sáng từng ảnh; ảnh bạn chỉnh không bị đụng tới; sao / nhãn màu
(Bridge, Photo Mechanic) giữ nguyên; không mang sang thứ chỉ đúng cho riêng tấm mẫu."""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from editer_anh import cli, color, dongbo, raw, xmp  # noqa: E402
from make_dng import make_dng  # noqa: E402

ACR_XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 9.1-c002 79.f354efc, 2023/11/09-12:05:53">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="3"
   crs:Version="18.7" crs:ProcessVersion="15.4" crs:WhiteBalance="{wb}" crs:Temperature="{temp}" crs:Tint="{tint}"
   crs:Exposure2012="{ev}" crs:Saturation="{sat}" crs:HueAdjustmentOrange="-6" crs:CameraProfile="Adobe Standard"
   crs:CameraProfileDigest="ABCDEF0123456789" crs:RawFileName="IMG.dng"
   crs:HasCrop="True" crs:CropTop="0.1" crs:CropLeft="0.05" crs:CropBottom="0.9" crs:CropRight="0.95"
   crs:LensProfileEnable="1" crs:LensProfileSetup="Custom" crs:LensProfileName="Adobe (Nikon NIKKOR Z 24-70mm f/4 S)"
   crs:LensProfileFilename="NIKON Z 24-70 f4.lcp" crs:LensProfileDigest="1234" crs:LensProfileDistortionScale="100"
   crs:PerspectiveVertical="-12" crs:PerspectiveUpright="5" crs:UprightVersion="151388160"
   crs:ToneCurveName2012="Custom" crs:HasSettings="True">
   <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 18</rdf:li><rdf:li>128, 132</rdf:li><rdf:li>255, 250</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
   <crs:MaskGroupBasedCorrections><rdf:Seq>
    <rdf:li><rdf:Description crs:What="Correction" crs:CorrectionName="Da" crs:LocalExposure2012="0.05">
     <crs:CorrectionMasks><rdf:Seq><rdf:li crs:What="Mask/Image" crs:MaskName="People - Facial Skin"
      crs:MaskSubType="3" crs:MaskSubCategoryID="2" crs:InputDigest="ABC"/></rdf:Seq></crs:CorrectionMasks>
    </rdf:Description></rdf:li>
    <rdf:li><rdf:Description crs:What="Correction" crs:CorrectionName="Co to tay" crs:LocalExposure2012="-0.4">
     <crs:CorrectionMasks><rdf:Seq><rdf:li crs:What="Mask/Paint" crs:MaskName="Brush 1"/></rdf:Seq></crs:CorrectionMasks>
    </rdf:Description></rdf:li>
    <rdf:li><rdf:Description crs:What="Correction" crs:CorrectionName="Sang mat" crs:LocalExposure2012="0.1">
     <crs:CorrectionMasks><rdf:Seq><rdf:li crs:What="Mask/CircularGradient" crs:Top="0.2" crs:Left="0.3"/></rdf:Seq></crs:CorrectionMasks>
    </rdf:Description></rdf:li>
    <rdf:li><rdf:Description crs:What="Correction" crs:CorrectionName="Vung xanh" crs:LocalSaturation="-0.2">
     <crs:CorrectionMasks><rdf:Seq><rdf:li crs:What="Mask/RangeMask" crs:MaskName="Color Range 1"/></rdf:Seq></crs:CorrectionMasks>
    </rdf:Description></rdf:li>
   </rdf:Seq></crs:MaskGroupBasedCorrections>
   <crs:CircularGradientBasedCorrections><rdf:Seq><rdf:li crs:What="Correction"/></rdf:Seq></crs:CircularGradientBasedCorrections>
   <crs:RetouchAreas><rdf:Seq><rdf:li crs:SpotType="heal"/></rdf:Seq></crs:RetouchAreas>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""

# Photo Mechanic / Bridge: chỉ có sao, nhãn màu, từ khoá — chưa có thông số Camera Raw
PM_XMP = """<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.5.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmlns:photomechanic="http://ns.camerabits.com/photomechanic/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
   xmp:Rating="4" xmp:Label="Red" photomechanic:ColorClass="1">
   <dc:subject><rdf:Bag><rdf:li>cô dâu</rdf:li><rdf:li>chọn</rdf:li></rdf:Bag></dc:subject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""


ACR_DEFAULT = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 9.1-c002 79.f354efc">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:Version="18.7" crs:ProcessVersion="15.4" crs:WhiteBalance="As Shot" crs:Exposure2012="0.00"
   crs:Contrast2012="0" crs:Highlights2012="0" crs:Shadows2012="0" crs:Whites2012="0" crs:Blacks2012="0"
   crs:Texture="0" crs:Clarity2012="0" crs:Dehaze="0" crs:Vibrance="0" crs:Saturation="0" crs:Sharpness="40"
   crs:ColorNoiseReduction="25" crs:ToneCurveName2012="Linear" crs:CameraProfile="Adobe Standard"
   crs:HasSettings="True" crs:RawFileName="IMG.dng">
   <crs:Look><rdf:Description crs:Name="Adobe Color" crs:Amount="1"/></crs:Look>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def acr(wb="Custom", temp=5500, tint=0, ev=0.0, sat=0) -> str:
    return ACR_XMP.format(wb=wb, temp=temp, tint=tint, ev=ev, sat=sat)


def resaved(tool_xmp: str, by_acr: bool) -> str:
    """Bridge (gắn sao) hoặc Camera Raw (bấm Done) ghi lại file .xmp tool đã ghi."""
    s = re.sub(r'x:xmptk="Editer[^"]*"', 'x:xmptk="Adobe XMP Core 9.1-c002 79.f354efc"', tool_xmp)
    if by_acr:
        return s.replace("<rdf:Description ", '<rdf:Description crs:RawFileName="IMG.dng" crs:Texture="0" ', 1)
    return s.replace("<rdf:Description ", '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="5" ', 1)


def test_templates(tmp: Path) -> None:
    p = tmp / "mau.xmp"
    p.write_text(acr(), encoding="utf-8")
    tpl, skipped = xmp.sync_template(p)
    a = xmp.template_attrs(tpl)
    assert "LensProfileName" not in a and "LensProfileDigest" not in a and a["LensProfileSetup"] == "LensDefaults"
    assert a["LensProfileEnable"] == "1" and a["LensProfileDistortionScale"] == "100"
    assert "PerspectiveVertical" not in a and "PerspectiveUpright" not in a and "UprightVersion" not in a
    assert "RawFileName" not in a and a["CameraProfileDigest"] == "ABCDEF0123456789"
    assert any("Co to tay" in s and "cọ tô" in s for s in skipped) and any("Sang mat" in s for s in skipped), skipped
    assert "Vung xanh" in tpl and "People - Facial Skin" in tpl and "CircularGradientBasedCorrections" not in tpl
    a = xmp.template_attrs(xmp.sync_template(p, same_camera=False, same_lens=True)[0])
    assert "CameraProfileDigest" not in a and a["CameraProfile"] == "Adobe Standard"
    assert a["LensProfileName"].startswith("Adobe (Nikon") and a["LensProfileSetup"] == "Custom"
    a = xmp.template_attrs(xmp.sync_template(p, drop=("Temperature", "Tint"))[0])
    assert "Temperature" not in a and "Tint" not in a
    # mẫu "phong cách" (học từ job cũ) cũng không mang profile ống kính / phối cảnh của riêng tấm đó
    a = xmp.template_attrs(xmp.make_template(p))
    assert "LensProfileName" not in a and "PerspectiveVertical" not in a and a["LensProfileSetup"] == "LensDefaults"
    # file phong cách học từ bản cũ (mẫu còn profile ống kính / phối cảnh) -> làm sạch khi dùng
    old_tpl = xmp._desc_of(xmp._parse(p))
    import xml.etree.ElementTree as ET
    a = xmp.template_attrs(xmp.clean_template(ET.tostring(old_tpl, encoding="unicode")))
    assert "LensProfileName" not in a and "PerspectiveVertical" not in a and a["LensProfileSetup"] == "LensDefaults"
    assert a["Saturation"] == "0" and a["HueAdjustmentOrange"] == "-6"
    p.write_text(acr().replace('crs:PerspectiveUpright="5"', 'crs:PerspectiveUpright="1"'), encoding="utf-8")
    assert xmp.template_attrs(xmp.sync_template(p)[0])["PerspectiveUpright"] == "1"   # Upright Auto: tính lại từng ảnh

    # nhận ra .xmp tool ghi kể cả khi Bridge / Photo Mechanic gắn sao (ghi lại file); Camera Raw lưu lại = bạn đã chỉnh
    tool = xmp.build_xmp(None, {"Saturation": 40, "Exposure2012": 0.3})
    for name, text, want in (("tool", tool, True), ("bridge", resaved(tool, False), True),
                             ("acr", resaved(tool, True), False), ("pm", PM_XMP, False), ("mau", acr(), False)):
        q = tmp / f"{name}.xmp"
        q.write_text(text, encoding="utf-8")
        assert xmp.written_by_editer(q) is want, name
        assert xmp.user_edited(q) is (name in ("acr", "mau")), name
    # mở trong Camera Raw rồi bấm Done mà KHÔNG chỉnh gì (toàn mặc định) -> không phải ảnh mẫu
    q = tmp / "mac-dinh.xmp"
    q.write_text(ACR_DEFAULT, encoding="utf-8")
    assert not xmp.user_edited(q) and not xmp.has_edits(q)
    q.write_text(ACR_DEFAULT.replace('crs:Vibrance="0"', 'crs:Vibrance="+12"'), encoding="utf-8")
    assert xmp.user_edited(q), "chỉnh 1 thanh trượt cũng là ảnh mẫu"
    q.write_text(ACR_DEFAULT.replace('crs:CameraProfile="Adobe Standard"', 'crs:CameraProfile="Camera Standard"'),
                 encoding="utf-8")
    assert xmp.user_edited(q), "chỉ đổi profile cũng là ảnh mẫu"
    print("[đồng bộ] mẫu: bỏ ống kính / phối cảnh / mask vị trí, nhận ra .xmp tool ghi: OK")


def test_exposure() -> None:
    def ph(iso=400, t=1 / 200, n=2.8, flash=0.0):
        return raw.Photo(path="x", iso=iso, shutter=t, fnumber=n, flash=flash)
    skin, no = True, False
    d, how = dongbo.exposure_shift(ph(), ph(), (0.30, skin), (0.27, skin))
    assert d == 0 and how == "thông số chụp", (d, how)                         # cùng ánh sáng, da lệch chút
    d, how = dongbo.exposure_shift(ph(), ph(iso=200), (0.30, skin), (0.12, skin))
    assert abs(d - 1) < 1e-9 and how == "thông số chụp", (d, how)              # chụp tối 1 stop -> +1 EV
    d, how = dongbo.exposure_shift(ph(), ph(t=1 / 100), (0.30, skin), (0.30, skin))
    assert abs(d) < 1e-9 and how == "da người", (d, how)                       # ánh sáng tối đi: da vẫn sáng như mẫu
    d, how = dongbo.exposure_shift(ph(flash=1), ph(flash=1, iso=1600), (0.30, skin), (0.15, skin))
    assert abs(d - 0.75) < 1e-9 and how == "da người", (d, how)                # flash: thông số chụp không nói lên gì
    d, how = dongbo.exposure_shift(ph(flash=1), ph(iso=1600), (0.30, no), (0.03, no))
    assert d == 1.0 and how == "độ sáng chung", (d, how)                       # không da + flash: nhẹ tay, có giới hạn
    d, how = dongbo.exposure_shift(ph(), ph(iso=800), (0.30, skin), (0.30, no))
    assert abs(d + 1) < 1e-9 and how == "thông số chụp", (d, how)
    sony = ph(t=1 / 100)
    sony.make, sony.model = "SONY", "ILCE-7M4"
    d, how = dongbo.exposure_shift(ph(), sony, (0.30, skin), (0.30, skin))
    assert abs(d + 1) < 1e-9 and how == "thông số chụp", (d, how)              # khác máy: máy dựng ảnh khác nhau
    print("[đồng bộ] exposure: thông số chụp / da người / flash: OK")


def main(tmp: Path) -> None:
    test_templates(tmp)
    test_exposure()
    job = tmp / "job"
    job.mkdir()
    shots = [("A", 0, 5200, 0.9), ("A", 20, 5600, 0.6), ("A", 40, 5000, 1.0),          # cảnh A: ngoài trời
             ("B", 1800, 3300, 0.8), ("B", 1820, 3500, 0.5), ("B", 1840, 3200, 0.9)]  # cảnh B: trong nhà đèn vàng
    for i, (_, t, temp, ev) in enumerate(shots):
        p = job / f"IMG_{i:04d}.dng"
        make_dng(p, temp, 4, seed=50 + i, exposure=ev)
        os.utime(p, (1_700_000_000 + t,) * 2)
    refs = {0: (5900, 10, 0.35, -12), 4: (3900, 6, 0.6, 8)}            # bạn chỉnh tay: ảnh 0 (cảnh A), ảnh 4 (cảnh B)
    for i, (temp, tint, ev, sat) in refs.items():
        xmp.sidecar_path(job / f"IMG_{i:04d}.dng").write_text(acr("Custom", temp, tint, ev, sat), encoding="utf-8")
    before = {i: xmp.sidecar_path(job / f"IMG_{i:04d}.dng").read_bytes() for i in refs}
    side = {i: xmp.sidecar_path(job / f"IMG_{i:04d}.dng") for i in range(6)}
    side[1].write_text(PM_XMP, encoding="utf-8")                         # ảnh 1: Photo Mechanic gắn sao, nhãn
    side[2].write_text(xmp.build_xmp(None, {"Saturation": 40}), encoding="utf-8")       # ảnh 2: tool ghi trước đó
    side[5].write_text(resaved(xmp.build_xmp(None, {"Saturation": 40}), False), encoding="utf-8")  # 5: + sao Bridge
    side[3].write_text(ACR_DEFAULT, encoding="utf-8")       # 3: mở Camera Raw, bấm Done, không chỉnh gì
    assert xmp.written_by_editer(side[2]) and xmp.written_by_editer(side[5])
    assert not xmp.written_by_editer(side[0])

    res = cli.main(["dong-bo", str(job), "--khong-ai"])
    assert res["so_mau"] == 2 and res["ghi"] == 0 and res["de"] == 4, res
    assert Path(res["tep"][0]).name == "IMG_0000.dng" and Path(res["tep"][-1]).name == "IMG_0005.dng"
    for i in refs:
        assert side[i].read_bytes() == before[i], "không được đụng ảnh bạn đã chỉnh"
    for i in (1, 2, 3, 5):
        assert (job / f"IMG_{i:04d}.xmp.bak").exists()
    text1 = side[1].read_text(encoding="utf-8")
    assert 'xmp:Rating="4"' in text1 and 'xmp:Label="Red"' in text1 and "cô dâu" in text1, text1[:600]
    assert 'photomechanic:ColorClass="1"' in text1
    assert 'xmp:Rating="5"' in side[5].read_text(encoding="utf-8")
    as_shot = {i: raw.load(job / f"IMG_{i:04d}.dng").as_shot_temp for i in range(6)}
    for i in (1, 2, 3, 5):
        crs = xmp.read_settings(side[i])["crs"]
        r = 0 if shots[i][0] == "A" else 4
        assert xmp.written_by_editer(side[i]), i
        assert crs["Saturation"] == str(refs[r][3]) or crs["Saturation"] == f"+{refs[r][3]}", crs["Saturation"]
        assert crs["HueAdjustmentOrange"] == "-6" and crs["CameraProfile"] == "Adobe Standard"
        assert crs["CameraProfileDigest"] == "ABCDEF0123456789", "cùng máy: giữ đúng profile"
        assert "CropTop" not in crs and "HasCrop" not in crs, "không mang khung cắt của ảnh mẫu"
        assert "LensProfileName" not in crs and crs["LensProfileSetup"] == "LensDefaults", "ống kính: tự nhận từng ảnh"
        assert "PerspectiveVertical" not in crs and "PerspectiveUpright" not in crs
        text = side[i].read_text(encoding="utf-8")
        assert "People - Facial Skin" in text and "Vung xanh" in text
        assert "Brush 1" not in text and "Sang mat" not in text and "RetouchAreas" not in text
        assert "CircularGradientBasedCorrections" not in text
        assert "InputDigest" not in text, "mask AI phải được Camera Raw tính lại trên ảnh mới"
        assert xmp.read_curve(side[i]) == [(0, 18), (128, 132), (255, 250)]
        # cùng cảnh: giữ nhiệt độ màu của ảnh mẫu; As Shot chênh > 25 mired mới dịch phần vượt
        d = color.mired(as_shot[i]) - color.mired(as_shot[r])
        want = color.mired(refs[r][0]) + (abs(d) - 25) * (1 if d > 0 else -1) * (abs(d) > 25)
        got = color.mired(float(crs["Temperature"]))
        assert abs(got - want) < 1.5, (i, got, want, crs["Temperature"])
        assert abs(float(crs["Tint"]) - refs[r][1]) <= 1, crs["Tint"]
    print(f"[đồng bộ] {res['ghi'] + res['de']} ảnh theo 2 ảnh bạn chỉnh: giữ đúng thông số, cân lại WB/sáng, "
          "giữ sao / nhãn màu: OK")

    # Ảnh mẫu để As Shot -> ảnh khác cũng As Shot (theo máy từng ảnh), không chép số nhiệt độ của ảnh mẫu
    side[4].write_text(acr("As Shot", 3412, 7, 0.6, 8), encoding="utf-8")
    cli.main(["dong-bo", str(job), "--khong-ai"])
    for i in (3, 5):
        crs = xmp.read_settings(side[i])["crs"]
        assert crs["WhiteBalance"] == "As Shot" and "Temperature" not in crs and "Tint" not in crs, crs
    assert 'xmp:Rating="5"' in side[5].read_text(encoding="utf-8"), "đồng bộ lại lần 2 vẫn giữ sao"
    print("[đồng bộ] ảnh mẫu As Shot, đồng bộ lại lần 2: OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        main(Path(d))
    print("ĐỒNG BỘ OK")
