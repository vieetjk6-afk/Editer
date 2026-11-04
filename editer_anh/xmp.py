"""Đọc / ghi file XMP sidecar của Camera Raw.

- Đọc: lấy toàn bộ thông số crs:* (thanh trượt) + model máy từ một file .xmp.
- Mẫu (template): từ một .xmp bạn đã chỉnh, giữ lại phần "look" (profile, HSL,
  curve, color grading, mask AI người...) và bỏ những thứ chỉ đúng cho riêng
  tấm đó (crop, mask kéo tay / gradient, xoá vết...).
- Ghi: mẫu + các thông số riêng từng ảnh -> <tên ảnh>.xmp cạnh file RAW.
  Camera Raw / Bridge sẽ tự đọc file này khi mở ảnh.
"""
from __future__ import annotations

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

# Thuộc tính chỉ đúng cho đúng một tấm ảnh -> không đưa vào mẫu
_DROP_ATTR_PREFIX = ("Crop", "RawFileName", "HasCrop", "AlreadyApplied", "HasSettings",
                     "UprightFourSegments", "UprightPreview", "UprightTransform")
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
    for k, v in desc.attrib.items():
        if not k.startswith(CRS):
            continue
        name = k[len(CRS):]
        if name.startswith(_DROP_ATTR_PREFIX):
            continue
        new.set(k, v)
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


def build_xmp(template_xml: str | None, values: dict, curve: list[tuple[int, int]] | None = None,
              tool_name: str = "Editer") -> str:
    """Mẫu + thông số riêng -> nội dung file .xmp hoàn chỉnh."""
    if template_xml:
        desc = ET.fromstring(template_xml)
    else:
        desc = ET.Element(RDF + "Description", {RDF + "about": ""})
    desc.set(CRS + "HasSettings", "True")
    desc.set(CRS + "AlreadyApplied", "False")
    for k, v in values.items():
        desc.set(CRS + k, fmt_value(k, v))
    if curve is not None:
        for c in list(desc):
            if c.tag == CRS + "ToneCurvePV2012":
                desc.remove(c)
        tc = ET.SubElement(desc, CRS + "ToneCurvePV2012")
        seq = ET.SubElement(tc, RDF + "Seq")
        for x, y in curve:
            ET.SubElement(seq, RDF + "li").text = f"{int(x)}, {int(y)}"
        desc.set(CRS + "ToneCurveName2012", "Custom")
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
        if k not in _NOT_IN_PRESET:
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
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    return status
