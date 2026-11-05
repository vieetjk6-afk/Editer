"""Đọc / ghi file XMP sidecar của Camera Raw.

- Đọc: lấy toàn bộ thông số crs:* (thanh trượt) + model máy từ một file .xmp.
- Mẫu (template): từ một .xmp bạn đã chỉnh, giữ lại phần "look" (profile, HSL,
  curve, color grading, mask AI người...) và bỏ những thứ chỉ đúng cho riêng
  tấm đó (crop, mask kéo tay / gradient, xoá vết...).
- Ghi: mẫu + các thông số riêng từng ảnh -> <tên ảnh>.xmp cạnh file RAW.
  Camera Raw / Bridge sẽ tự đọc file này khi mở ảnh.
"""
from __future__ import annotations

import copy
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)

CRS = "{%s}" % NS["crs"]
RDF = "{%s}" % NS["rdf"]

# Đánh dấu file .xmp do Editer ghi (Bridge / Camera Raw giữ nguyên namespace lạ khi ghi lại file)
ED = "{http://ns.editer.vn/anh/1.0/}"
ET.register_namespace("editer", ED[1:-1])

# Thuộc tính chỉ đúng cho đúng một tấm ảnh -> không đưa vào mẫu: cắt khung, xoay, Upright, kéo phối cảnh tay
_DROP_ATTR_PREFIX = ("Crop", "RawFileName", "HasCrop", "AlreadyApplied", "HasSettings", "Upright",
                     "PerspectiveVertical", "PerspectiveHorizontal", "PerspectiveRotate", "PerspectiveScale",
                     "PerspectiveAspect", "PerspectiveX", "PerspectiveY")
# Profile ống kính CỤ THỂ của tấm đó (24-70 khác 85mm...) -> bỏ, Camera Raw tự nhận ống kính từng ảnh
_LENS_ID = {"LensProfileName", "LensProfileFilename", "LensProfileDigest", "LensProfileIsEmbedded"}
_DROP_CHILDREN = {"GradientBasedCorrections", "CircularGradientBasedCorrections",
                  "PaintBasedCorrections", "RetouchInfo", "RetouchAreas", "UprightFourSegments_0",
                  "FilterList"}  # FilterList = Denoise/Raw Details, gắn với file gốc
# Mask chỉ phụ thuộc nội dung ảnh (AI) thì mang sang ảnh khác được; mask vị trí thì không
_PORTABLE_MASKS = {"Mask/Image"}
# Mã kiểm tra của mask AI gắn với ảnh gốc -> bỏ để Camera Raw tính lại trên ảnh mới
_DROP_MASK_ATTR = {"InputDigest", "InputDigestVersion", "LocalInputDigest",
                   "LocalInputDigestVersion", "ErrorReason"}

# Thông số "chi tiết" (nét, khử nhiễu, ống kính) — không ảnh hưởng màu
DETAIL_KEYS = [
    "Sharpness", "SharpenRadius", "SharpenDetail", "SharpenEdgeMasking",
    "LuminanceSmoothing", "LuminanceNoiseReductionDetail", "LuminanceNoiseReductionContrast",
    "ColorNoiseReduction", "ColorNoiseReductionDetail", "ColorNoiseReductionSmoothness",
    "LensProfileEnable", "LensProfileSetup", "AutoLateralCA",
]

# Cách Camera Raw ghi số: có dấu +, số chữ số thập phân
_FMT_2DEC = {"Exposure2012"}
# Lưu ý: Camera Raw lưu bánh xe Color Grading vùng TỐI/SÁNG bằng tên cũ
# SplitToningShadowHue/Saturation, SplitToningHighlightHue/Saturation (+ SplitToningBalance);
# chỉ vùng giữa và tổng thể mới dùng ColorGradeMidtone*/ColorGradeGlobal*.
_FMT_UNSIGNED = {"Temperature", "ColorGradeMidtoneHue", "ColorGradeMidtoneSat", "ColorGradeBlending",
                 "ColorGradeGlobalHue", "ColorGradeGlobalSat",
                 "SplitToningShadowHue", "SplitToningShadowSaturation",
                 "SplitToningHighlightHue", "SplitToningHighlightSaturation"}


def fmt_value(key: str, val) -> str:
    if isinstance(val, str):
        return val
    if key in _FMT_2DEC:
        return f"{val:+.2f}" if abs(val) >= 0.005 else "0.00"
    v = int(round(val))
    if key in _FMT_UNSIGNED:
        return str(v)
    return f"{v:+d}" if v != 0 else "0"


def _desc_of(root: ET.Element) -> ET.Element:
    for d in root.iter(RDF + "Description"):
        if any(k.startswith(CRS) for k in d.attrib) or any(c.tag.startswith(CRS) for c in d):
            return d
    raise ValueError("không thấy thông số Camera Raw (crs:) trong file")


def _parse(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8", errors="replace")
    i = text.find("<x:xmpmeta")
    j = text.rfind("</x:xmpmeta>")
    if i < 0 or j < 0:
        raise ValueError(f"{path.name}: không phải file XMP")
    return ET.fromstring(text[i:j + len("</x:xmpmeta>")])


def read_settings(path: str | Path) -> dict:
    """-> {'crs': {Tên: 'giá trị'}, 'model': 'NIKON Z5_2' | None, 'masks': [...]}"""
    path = Path(path)
    root = _parse(path)
    desc = _desc_of(root)
    crs = {k[len(CRS):]: v for k, v in desc.attrib.items() if k.startswith(CRS)}
    model = None
    for d in root.iter(RDF + "Description"):
        m = d.attrib.get("{%s}Model" % NS["tiff"])
        if m:
            model = m
            break
        el = d.find("tiff:Model", NS)
        if el is not None and el.text:
            model = el.text
            break
    masks = [li.attrib.get(CRS + "What", "") for li in root.iter(RDF + "li") if CRS + "MaskName" in li.attrib or li.attrib.get(CRS + "What", "").startswith("Mask/")]
    return {"crs": crs, "model": model, "masks": masks}


def read_curve(path: str | Path, name: str = "ToneCurvePV2012") -> list[tuple[int, int]] | None:
    """Các điểm Tone Curve (x, y) trong file .xmp, không có thì None."""
    desc = _desc_of(_parse(Path(path)))
    el = desc.find(f"crs:{name}", NS)
    if el is None:
        return None
    pts = []
    for li in el.iter(RDF + "li"):
        try:
            x, y = (int(float(v)) for v in (li.text or "").split(","))
            pts.append((x, y))
        except ValueError:
            continue
    return pts or None


def number(crs: dict, key: str) -> float | None:
    v = crs.get(key)
    if v is None or not re.fullmatch(r"[+-]?\d+(\.\d+)?", v.strip()):
        return None
    return float(v)


def make_template(path: str | Path) -> str:
    """Từ một XMP đã chỉnh -> chuỗi XML mẫu (chỉ phần rdf:Description crs)."""
    root = _parse(Path(path))
    desc = _desc_of(root)
    new = ET.Element(RDF + "Description", {RDF + "about": ""})
    _copy_attrs(desc, new)
    for child in desc:
        if not child.tag.startswith(CRS):
            continue
        name = child.tag[len(CRS):]
        if name in _DROP_CHILDREN:
            continue
        if name == "MaskGroupBasedCorrections":
            child = _portable_masks(child)
            if child is None:
                continue
        new.append(child)
    return ET.tostring(new, encoding="unicode")


def _copy_attrs(desc: ET.Element, new: ET.Element, keep_lens: bool = False, keep_profile_digest: bool = True,
                drop=()) -> None:
    """Chép thông số crs:* sang mẫu, bỏ thứ chỉ đúng cho đúng tấm đó."""
    for k, v in desc.attrib.items():
        if not k.startswith(CRS):
            continue
        name = k[len(CRS):]
        if name.startswith(_DROP_ATTR_PREFIX) or name in drop:
            continue
        if name == "PerspectiveUpright" and v.strip() == "5":
            continue                                  # Upright "Guided": đường dẫn hướng vẽ trên riêng tấm đó
        if not keep_lens and name in _LENS_ID:
            continue
        if not keep_profile_digest and name == "CameraProfileDigest":
            continue                                  # khác đời máy: Camera Raw tìm profile theo tên
        new.set(k, v)
    if not keep_lens and new.get(CRS + "LensProfileSetup") == "Custom":
        new.set(CRS + "LensProfileSetup", "LensDefaults")


def clean_template(template_xml: str) -> str:
    """Mẫu học từ bản cũ (trước 0.10): bỏ profile ống kính cụ thể, phối cảnh, Upright của riêng một tấm ảnh."""
    try:
        d = ET.fromstring(template_xml)
    except ET.ParseError:
        return template_xml
    new = ET.Element(d.tag, {k: v for k, v in d.attrib.items() if not k.startswith(CRS)})
    _copy_attrs(d, new)
    for c in d:
        new.append(c)
    return ET.tostring(new, encoding="unicode")


def _portable_masks(group: ET.Element) -> ET.Element | None:
    """Giữ các nhóm chỉnh cục bộ chỉ dùng mask AI (người, da, tóc, trời...)."""
    seq = group.find("rdf:Seq", NS)
    if seq is None:
        return None
    keep = []
    for li in list(seq):
        kinds = set()
        for m in li.iter():
            w = m.attrib.get(CRS + "What")
            if w and w.startswith("Mask/"):
                kinds.add(w)
        if kinds and kinds <= _PORTABLE_MASKS:
            for m in li.iter():
                for a in list(m.attrib):
                    if a.startswith(CRS) and a[len(CRS):] in _DROP_MASK_ATTR:
                        del m.attrib[a]
            keep.append(li)
    for li in list(seq):
        seq.remove(li)
    for li in keep:
        seq.append(li)
    return group if keep else None


def _crs_hash(desc: ET.Element) -> str:
    """Dấu vân tay các thông số crs:* (để biết sau này Camera Raw có lưu lại thông số khác không)."""
    import hashlib
    items = sorted((k[len(CRS):], v) for k, v in desc.attrib.items() if k.startswith(CRS))
    return hashlib.sha1("\n".join(f"{k}={v}" for k, v in items).encode("utf-8")).hexdigest()[:16]


def written_by_editer(path: str | Path) -> bool:
    """File .xmp do Editer ghi mà bạn CHƯA chỉnh lại trong Camera Raw?
    - Còn nguyên x:xmptk="Editer" -> đúng.
    - Bridge / Photo Mechanic chỉ gắn sao, nhãn màu, từ khoá (ghi lại cả file bằng bộ XMP của Adobe) nhưng
      thông số Camera Raw còn y như lúc tool ghi -> vẫn là của tool.
    - Camera Raw đã lưu lại (bấm Done / Open) -> thông số đổi -> ảnh bạn đã chỉnh."""
    try:
        root = _parse(Path(path))
    except Exception:
        return False
    node = root if root.tag == "{%s}xmpmeta" % NS["x"] else root.find(".//x:xmpmeta", NS)
    tk = (node.attrib.get("{%s}xmptk" % NS["x"], "") if node is not None else "")
    if tk.startswith("Editer"):
        return True
    mark = next((d.attrib[ED + "CrsHash"] for d in root.iter(RDF + "Description") if ED + "CrsHash" in d.attrib),
                None)
    if not mark:
        return False
    try:
        return _crs_hash(_desc_of(root)) == mark
    except ValueError:
        return False


_EDIT_KEY = re.compile(r"(Exposure2012|Contrast2012|Highlights2012|Shadows2012|Whites2012|Blacks2012|Texture|"
                       r"Clarity2012|Dehaze|Vibrance|Saturation|Parametric(Shadows|Darks|Lights|Highlights)|"
                       r"(Hue|Saturation|Luminance)Adjustment\w+|SplitToning\w*Saturation|ColorGrade\w*(Sat|Lum)|"
                       r"GrainAmount|PostCropVignetteAmount|(Red|Green|Blue)(Hue|Saturation)|ShadowTint)")


def has_edits(path: str | Path) -> bool:
    """File .xmp có chỉnh gì thật không? (mở trong Camera Raw rồi bấm Done mà không đổi gì thì Camera Raw
    vẫn có thể lưu .xmp toàn thông số mặc định — ảnh đó không phải ảnh mẫu)."""
    try:
        root = _parse(Path(path))
        desc = _desc_of(root)
    except Exception:
        return False
    crs = {k[len(CRS):]: v for k, v in desc.attrib.items() if k.startswith(CRS)}
    if crs.get("WhiteBalance", "As Shot") != "As Shot":
        return True
    if any(_EDIT_KEY.fullmatch(k) and (number(crs, k) or 0) != 0 for k in crs):
        return True
    if crs.get("ToneCurveName2012", "Linear") not in ("Linear", ""):
        return True
    if crs.get("CameraProfile", "Adobe Standard") not in ("Adobe Standard", "Embedded", ""):
        return True                                   # chọn profile khác (Camera Standard...)
    look_ = desc.find("crs:Look", NS)
    if look_ is not None:
        name = next((d.attrib.get(CRS + "Name") for d in look_.iter(RDF + "Description")), None)
        if name and name != "Adobe Color":
            return True
    grp = desc.find("crs:MaskGroupBasedCorrections", NS)
    return grp is not None and len(grp.findall(".//rdf:li", NS)) > 0


def user_edited(sidecar: str | Path | None) -> bool:
    """Ảnh BẠN đã chỉnh (làm mẫu khi đồng bộ): có .xmp không do tool ghi, có thông số Camera Raw và có chỉnh thật."""
    if sidecar is None or written_by_editer(sidecar):
        return False
    try:
        crs = read_settings(sidecar)["crs"]
    except Exception:
        return False
    return bool(crs) and crs.get("HasSettings", "True") == "True" and has_edits(sidecar)


# Đồng bộ: bỏ chỉnh cục bộ theo VỊ TRÍ (cọ tô, gradient thẳng/tròn, xoá vết) — chỉ đúng chỗ trên tấm đó;
# giữ mask theo NỘI DUNG (AI: người, da, trời, chủ thể, nền; vùng màu/độ sáng) — Camera Raw tính lại trên ảnh mới
_SYNC_DROP_CHILDREN = {"PaintBasedCorrections", "GradientBasedCorrections", "CircularGradientBasedCorrections",
                       "RetouchInfo", "RetouchAreas", "UprightFourSegments_0", "FilterList"}
_SYNC_MASKS = {"Mask/Image", "Mask/RangeMask"}
_MASK_KIND = {"Mask/Paint": "cọ tô tay", "Mask/Gradient": "gradient thẳng", "Mask/CircularGradient": "gradient tròn"}


def sync_template(path: str | Path, same_camera: bool = True, same_lens: bool = False,
                  drop=()) -> tuple[str, list[str]]:
    """Đồng bộ trong CÙNG CẢNH: lấy hết thông số ảnh bạn đã chỉnh (profile, curve, HSL, grading, chi
    tiết, mask AI...), trừ thứ chỉ đúng cho đúng tấm đó: cắt khung, xoay/upright/phối cảnh, xoá vết,
    chỉnh cục bộ theo vị trí, profile ống kính khi khác ống kính.
    -> (mẫu XML, tên các chỉnh cục bộ không mang sang)."""
    root = _parse(Path(path))
    desc = _desc_of(root)
    new = ET.Element(RDF + "Description", {RDF + "about": ""})
    _copy_attrs(desc, new, keep_lens=same_lens, keep_profile_digest=same_camera, drop=set(drop))
    skipped: list[str] = []
    for child in desc:
        if not child.tag.startswith(CRS) or child.tag[len(CRS):] in _SYNC_DROP_CHILDREN:
            continue
        if child.tag == CRS + "MaskGroupBasedCorrections":
            seq = child.find("rdf:Seq", NS)
            for li in list(seq if seq is not None else []):
                kinds = {m.attrib.get(CRS + "What", "") for m in li.iter()}
                kinds = {k for k in kinds if k.startswith("Mask/")}
                if not kinds or not kinds <= _SYNC_MASKS:
                    seq.remove(li)
                    d = li.find("rdf:Description", NS)
                    name = (d.attrib.get(CRS + "CorrectionName", "") if d is not None else "") or "mask"
                    why = ", ".join(sorted(_MASK_KIND.get(k, k.replace("Mask/", "")) for k in kinds - _SYNC_MASKS))
                    skipped.append(f"{name} ({why or 'không rõ loại'})")
                    continue
                for m in li.iter():
                    for a in list(m.attrib):
                        if a.startswith(CRS) and a[len(CRS):] in _DROP_MASK_ATTR:
                            del m.attrib[a]               # mask AI: Camera Raw tính lại trên ảnh mới
            if seq is None or not len(seq):
                continue
        new.append(child)
    return ET.tostring(new, encoding="unicode"), skipped


def template_drop_masks(template_xml: str) -> str:
    d = ET.fromstring(template_xml)
    for c in list(d):
        if c.tag == CRS + "MaskGroupBasedCorrections":
            d.remove(c)
    return ET.tostring(d, encoding="unicode")


def template_attrs(template_xml: str) -> dict:
    d = ET.fromstring(template_xml)
    return {k[len(CRS):]: v for k, v in d.attrib.items() if k.startswith(CRS)}


def sidecar_path(raw_path: str | Path) -> Path:
    return Path(raw_path).with_suffix(".xmp")


def find_sidecar(raw_path: str | Path) -> Path | None:
    p = Path(raw_path)
    for ext in (".xmp", ".XMP", ".Xmp"):
        q = p.with_suffix(ext)
        if q.exists():
            return q
    return None


# Mask AI của Camera Raw — cấu trúc lấy đúng từ preset/XMP do Camera Raw 18 ghi ra
_MASKS = {
    "da": [("People - Facial Skin", "3", "2"), ("People - Body Skin", "3", "4")],
    "nen": [("Background 1", "0", "22")],
}
_LOCAL_ZERO = ["LocalExposure", "LocalHue", "LocalContrast", "LocalClarity", "LocalSharpness",
               "LocalBrightness", "LocalToningHue", "LocalToningSaturation", "LocalContrast2012",
               "LocalHighlights2012", "LocalShadows2012", "LocalWhites2012", "LocalBlacks2012",
               "LocalClarity2012", "LocalDehaze", "LocalLuminanceNoise", "LocalMoire", "LocalDefringe",
               "LocalTexture", "LocalGrain"]


def _num(v: float) -> str:
    t = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


def _local_correction(item: dict) -> ET.Element:
    """Một nhóm chỉnh cục bộ: [Exposure, Temperature, Tint, Saturation] trên mask AI.
    Camera Raw lưu giá trị cục bộ chia thang: Exposure / 4, các thanh khác / 100."""
    import uuid
    kind = "da" if item["ten"].endswith("Da") else "nen"
    li = ET.Element(RDF + "li")
    d = ET.SubElement(li, RDF + "Description")
    attrs = {"What": "Correction", "CorrectionAmount": "1", "CorrectionActive": "true",
             "CorrectionName": item["ten"], "CorrectionSyncID": uuid.uuid4().hex.upper()}
    attrs.update({k: "0" for k in _LOCAL_ZERO})
    attrs.update({"LocalExposure2012": _num(item["ev"] / 4), "LocalTemperature": _num(item["temp"] / 100),
                  "LocalTint": _num(item["tint"] / 100), "LocalSaturation": _num(item["sat"] / 100),
                  "LocalCurveRefineSaturation": "100"})
    for k, v in attrs.items():
        d.set(CRS + k, v)
    seq = ET.SubElement(ET.SubElement(d, CRS + "CorrectionMasks"), RDF + "Seq")
    for name, sub, cat in _MASKS[kind]:
        ET.SubElement(seq, RDF + "li", {CRS + k: v for k, v in {
            "What": "Mask/Image", "MaskActive": "true", "MaskName": name, "MaskBlendMode": "0",
            "MaskInverted": "false", "MaskSyncID": uuid.uuid4().hex.upper(), "MaskValue": "1",
            "MaskVersion": "1", "MaskSubType": sub, "MaskSubCategoryID": cat,
            "ReferencePoint": "0.500000 0.500000"}.items()})
    return li


def read_local(path: str | Path) -> dict:
    """Các chỉnh cục bộ do Editer ghi (tên 'Editer - ...') -> {tên: {ev, temp, tint, sat}}."""
    out = {}
    for d in _desc_of(_parse(Path(path))).iter(RDF + "Description"):
        name = d.attrib.get(CRS + "CorrectionName", "")
        if not name.startswith("Editer - "):
            continue
        v = {k: float(d.attrib.get(CRS + k, "0") or 0)
             for k in ("LocalExposure2012", "LocalTemperature", "LocalTint", "LocalSaturation")}
        out[name] = {"ev": v["LocalExposure2012"] * 4, "temp": v["LocalTemperature"] * 100,
                     "tint": v["LocalTint"] * 100, "sat": v["LocalSaturation"] * 100}
    return out


def build_xmp(template_xml: str | None, values: dict, curve: list[tuple[int, int]] | None = None,
              tool_name: str = "Editer") -> str:
    """Mẫu + thông số riêng (+ chỉnh cục bộ '_local') -> nội dung file .xmp hoàn chỉnh."""
    if template_xml:
        desc = ET.fromstring(template_xml)
    else:
        desc = ET.Element(RDF + "Description", {RDF + "about": ""})
    desc.set(CRS + "HasSettings", "True")
    desc.set(CRS + "AlreadyApplied", "False")
    values = dict(values)
    local = values.pop("_local", None)
    for k, v in values.items():
        desc.set(CRS + k, fmt_value(k, v))
    if local:
        grp = desc.find("crs:MaskGroupBasedCorrections", NS)
        if grp is None:
            grp = ET.SubElement(desc, CRS + "MaskGroupBasedCorrections")
        seq = grp.find("rdf:Seq", NS)
        if seq is None:
            seq = ET.SubElement(grp, RDF + "Seq")
        for item in local:
            seq.append(_local_correction(item))
    if curve is not None:
        for c in list(desc):
            if c.tag == CRS + "ToneCurvePV2012":
                desc.remove(c)
        tc = ET.SubElement(desc, CRS + "ToneCurvePV2012")
        seq = ET.SubElement(tc, RDF + "Seq")
        for x, y in curve:
            ET.SubElement(seq, RDF + "li").text = f"{int(x)}, {int(y)}"
        desc.set(CRS + "ToneCurveName2012", "Custom")
    desc.set(ED + "CrsHash", _crs_hash(desc))
    xmpmeta = ET.Element("{%s}xmpmeta" % NS["x"], {"{%s}xmptk" % NS["x"]: tool_name})
    rdf = ET.SubElement(xmpmeta, RDF + "RDF")
    rdf.append(desc)
    ET.indent(xmpmeta, space=" ")
    return ET.tostring(xmpmeta, encoding="unicode") + "\n"


_NOT_IN_PRESET = {"WhiteBalance", "Temperature", "Tint", "Exposure2012"}


def build_preset(name: str, values: dict, curve: list[tuple[int, int]] | None,
                 group: str = "Editer") -> str:
    """Preset Camera Raw (chỉ phần "look": curve, HSL, color grading...). Không chứa
    cân bằng trắng / exposure vì hai thứ đó phải theo từng ảnh."""
    import uuid
    desc = ET.Element(RDF + "Description", {RDF + "about": ""})
    head = {"PresetType": "Normal", "Cluster": "", "UUID": uuid.uuid4().hex.upper(),
            "SupportsAmount2": "False", "SupportsAmount": "False", "SupportsColor": "True",
            "SupportsMonochrome": "True", "SupportsHighDynamicRange": "True",
            "SupportsNormalDynamicRange": "True", "SupportsSceneReferred": "True",
            "SupportsOutputReferred": "True", "RequiresRGBTables": "False",
            "CameraModelRestriction": "", "Copyright": "", "ContactInfo": "",
            "Version": "18.0", "ProcessVersion": "15.4"}
    for k, v in head.items():
        desc.set(CRS + k, v)
    for k, v in values.items():
        if k not in _NOT_IN_PRESET and not k.startswith("_"):   # "_local" (mask từng ảnh) không vào preset
            desc.set(CRS + k, fmt_value(k, v))
    if curve:
        desc.set(CRS + "ToneCurveName2012", "Custom")
    desc.set(CRS + "HasSettings", "True")
    XL = "{http://www.w3.org/XML/1998/namespace}lang"
    for tag, text in (("Name", name), ("ShortName", ""), ("SortName", ""), ("Group", group), ("Description", "")):
        alt = ET.SubElement(ET.SubElement(desc, CRS + tag), RDF + "Alt")
        li = ET.SubElement(alt, RDF + "li", {XL: "x-default"})
        li.text = text or None
    if curve:
        seq = ET.SubElement(ET.SubElement(desc, CRS + "ToneCurvePV2012"), RDF + "Seq")
        for x, y in curve:
            ET.SubElement(seq, RDF + "li").text = f"{int(x)}, {int(y)}"
    xmpmeta = ET.Element("{%s}xmpmeta" % NS["x"], {"{%s}xmptk" % NS["x"]: "Editer"})
    ET.SubElement(xmpmeta, RDF + "RDF").append(desc)
    ET.indent(xmpmeta, space=" ")
    return ET.tostring(xmpmeta, encoding="unicode") + "\n"


def preset_dir() -> Path:
    """Thư mục preset của Camera Raw trên Windows."""
    import os
    return Path(os.environ.get("APPDATA", Path.home())) / "Adobe" / "CameraRaw" / "Settings"


def _top_descs(root: ET.Element) -> list[ET.Element]:
    rdf = root if root.tag == RDF + "RDF" else root.find("rdf:RDF", NS)
    return [] if rdf is None else [d for d in rdf if d.tag == RDF + "Description"]


def _keep_prefixes(text: str) -> None:
    """Giữ tên viết tắt namespace của file cũ (xmp:, photoshop:, dc:...) thay vì ns0:, ns1:."""
    import io
    used = set(ET._namespace_map.values())
    try:
        for _, (prefix, uri) in ET.iterparse(io.StringIO(text), events=("start-ns",)):
            if (prefix and uri not in ET._namespace_map and prefix not in used
                    and not re.fullmatch(r"ns\d+", prefix)):
                ET.register_namespace(prefix, uri)
                used.add(prefix)
    except (ET.ParseError, ValueError):
        pass


def carry_meta(old: Path, content: str) -> str:
    """Ghi thông số mới mà GIỮ thông tin khác trong .xmp cũ: sao, nhãn màu, từ khoá, IPTC... (Bridge,
    Photo Mechanic, Lightroom ghi) — chỉ thay phần thông số Camera Raw."""
    try:
        text = old.read_text(encoding="utf-8", errors="replace")
        old_root = _parse(old)
        new_root = ET.fromstring(content)
    except Exception:
        return content
    i = text.find("<x:xmpmeta")
    _keep_prefixes(text[i:text.rfind("</x:xmpmeta>") + len("</x:xmpmeta>")] if i >= 0 else text)
    descs = _top_descs(new_root)
    if not descs:
        return content
    nd = descs[0]
    tags = {c.tag for c in nd}
    added = 0
    for od in _top_descs(old_root):
        for k, v in od.attrib.items():
            if k.startswith((CRS, ED)) or k == RDF + "about" or k in nd.attrib:
                continue
            nd.set(k, v)
            added += 1
        for ch in od:
            if ch.tag.startswith((CRS, ED)) or ch.tag in tags:
                continue
            nd.append(copy.deepcopy(ch))
            tags.add(ch.tag)
            added += 1
    if not added:
        return content
    ET.indent(new_root, space=" ")
    return ET.tostring(new_root, encoding="unicode") + "\n"


def write_sidecar(raw_path: str | Path, content: str, overwrite: bool = False) -> str:
    """Ghi .xmp cạnh ảnh. Không bao giờ đè file bạn đã chỉnh nếu không cho phép;
    khi đè thì giữ bản cũ thành .xmp.bak. -> 'ghi' | 'bo-qua' | 'de'"""
    old = find_sidecar(raw_path)
    target = old or sidecar_path(raw_path)
    status = "ghi"
    if old is not None:
        if not overwrite:
            return "bo-qua"
        bak = old.with_name(old.name + ".bak")
        if not bak.exists():
            shutil.copy2(old, bak)
        status = "de"
        content = carry_meta(old, content)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    return status
