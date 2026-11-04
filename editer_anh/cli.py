"""Dòng lệnh Editer Ảnh.

  hoc    <thư mục job đã chỉnh>...  -o phong-cach.editer
  chinh  <thư mục RAW> --phong-cach phong-cach.editer
  chinh  <thư mục RAW> --mau-jpg mau.jpg [--phong-cach ...]
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from pathlib import Path

from . import __version__, color, match, raw, style, xmp

# Đặt tất cả về 0 khi chỉnh theo JPG (để kết quả không phụ thuộc mặc định của máy)
_NEUTRAL = {
    "Contrast2012": 0, "Highlights2012": 0, "Shadows2012": 0, "Whites2012": 0, "Blacks2012": 0,
    "Texture": 0, "Clarity2012": 0, "Dehaze": 0, "Vibrance": 0, "Saturation": 0,
    "ParametricShadows": 0, "ParametricDarks": 0, "ParametricLights": 0, "ParametricHighlights": 0,
    "ColorGradeMidtoneHue": 0, "ColorGradeMidtoneSat": 0, "ColorGradeShadowLum": 0,
    "ColorGradeMidtoneLum": 0, "ColorGradeHighlightLum": 0, "ColorGradeBlending": 50,
    "ColorGradeBalance": 0, "ColorGradeGlobalHue": 0, "ColorGradeGlobalSat": 0, "ColorGradeGlobalLum": 0,
    "SplitToningShadowSaturation": 0, "SplitToningHighlightSaturation": 0,
    "CameraProfile": "Adobe Color", "ProcessVersion": "15.4", "Version": "18.0",
    "LensProfileEnable": 1,
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def cmd_hoc(a) -> None:
    t0 = time.time()
    m = style.learn(a.thu_muc, log=_log, workers=a.luong)
    out = Path(a.ra)
    style.save(m, out)
    _log(f"\nĐã học từ {m['n_photos']} ảnh ({', '.join(f'{k}: {v}' for k, v in m['counts'].items())}).")
    _log("Độ chính xác (kiểm tra chéo trên chính ảnh của bạn):")
    names = {"d_mired": "Nhiệt độ màu", "d_tint": "Tint", "Exposure2012": "Exposure"}
    for t, r in m["report"].items():
        n = names.get(t, t)
        if math.isnan(r["mae"]):
            _log(f"  {n:14s}: chưa đủ ảnh để kiểm tra (n={r['n']})")
            continue
        if t == "d_mired":
            k = 1e6 / (1e6 / 5500 - r["mae"]) - 5500
            kb = 1e6 / (1e6 / 5500 - r["mae_median"]) - 5500
            _log(f"  {n:14s}: lệch trung bình ±{k:.0f}K  (nếu chỉ dùng 1 số cố định: ±{kb:.0f}K)")
        else:
            _log(f"  {n:14s}: lệch trung bình ±{r['mae']:.2f}  (nếu chỉ dùng 1 số cố định: ±{r['mae_median']:.2f})")
    _log(f"\nĐã lưu: {out.resolve()}  ({time.time() - t0:.0f}s)")


def _values_from_style(m: dict, ph: raw.Photo, pred: dict) -> dict:
    v = {}
    if ph.as_shot_temp and "d_mired" in pred:
        mir = color.mired(ph.as_shot_temp) + pred["d_mired"]
        v["WhiteBalance"] = "Custom"
        v["Temperature"] = float(min(max(1e6 / max(mir, 20.0), 2000), 50000))
        if ph.as_shot_tint is not None and "d_tint" in pred:
            v["Tint"] = float(min(max(ph.as_shot_tint + pred["d_tint"], -150), 150))
    for k, val in pred.items():
        if k not in ("d_mired", "d_tint"):
            v[k] = val
    return v


def _template_for(m: dict | None, ph: raw.Photo, no_mask: bool) -> str | None:
    if not m:
        return None
    t = m["templates"].get(ph.model) or m["templates"][m["default_model"]]
    return xmp.template_drop_masks(t) if no_mask else t


def cmd_chinh(a) -> None:
    folder = Path(a.thu_muc)
    raws = raw.list_raws(folder, recursive=not a.khong_thu_muc_con)
    if not raws:
        raise SystemExit(f"Không thấy file RAW nào trong {folder}")
    if not a.ghi_de:
        todo = [r for r in raws if xmp.find_sidecar(r) is None]
        skipped = len(raws) - len(todo)
        if skipped:
            _log(f"Bỏ qua {skipped} ảnh đã có .xmp (bạn đã chỉnh). Thêm --ghi-de để chỉnh lại cả những ảnh đó.")
        raws = todo
    if not raws:
        _log("Không còn ảnh nào cần chỉnh.")
        return
    m = style.load(a.phong_cach) if a.phong_cach else None
    if not m and not a.mau_jpg:
        raise SystemExit("Cần --phong-cach (file đã học) hoặc --mau-jpg (ảnh mẫu).")

    _log(f"Đọc {len(raws)} ảnh...")
    photos = style.load_many(raws, a.luong, _log)
    ok = [(r, p) for r, p in zip(raws, photos) if not p.error and p.preview is not None]
    for r, p in zip(raws, photos):
        if p.error or p.preview is None:
            _log(f"  lỗi {r.name}: {p.error or 'không có ảnh xem trước'}")
    raws, photos = [r for r, _ in ok], [p for _, p in ok]
    scenes = style.group_scenes(photos, a.cach_canh)
    _log(f"Chia được {len(set(scenes))} cảnh (theo giờ chụp).")

    curve = None
    if a.mau_jpg:
        refs = [raw.load_image(p, 512) for p in a.mau_jpg]
        ref = match.merge_refs(refs)
        stats = [match.Stats.of(p.preview, 20000, i) for i, p in enumerate(photos)]
        values = []
        for s in stats:
            dm, dt, dev = match.wb_exposure(s, ref, a.do_manh)
            values.append({"d_mired": dm, "d_tint": dt, "Exposure2012": dev})
        style.smooth_by_scene(values, scenes, ("d_mired", "d_tint", "Exposure2012"), 0.4)
        sims = [match.simulate(s, v["d_mired"], v["d_tint"], v["Exposure2012"]) for s, v in zip(stats, values)]
        curve, look = match.batch_look(sims, ref)
        all_vals = []
        for ph, v in zip(photos, values):
            out = dict(_NEUTRAL)
            if m:
                tattrs = xmp.template_attrs(m["templates"].get(ph.model) or m["templates"][m["default_model"]])
                for k in xmp.DETAIL_KEYS:
                    if k in tattrs:
                        out[k] = tattrs[k]
                out["Version"] = tattrs.get("Version", out["Version"])
            out.update(look)
            if ph.as_shot_temp:
                mir = color.mired(ph.as_shot_temp) + v["d_mired"]
                out["WhiteBalance"] = "Custom"
                out["Temperature"] = float(min(max(1e6 / max(mir, 20.0), 2000), 50000))
                out["Tint"] = float(min(max((ph.as_shot_tint or 0) + v["d_tint"], -150), 150))
            else:
                out["WhiteBalance"] = "As Shot"
            out["Exposure2012"] = v["Exposure2012"]
            all_vals.append(out)
        templates = [_masks_only(m, ph, a.khong_mask) for ph in photos]
    else:
        preds = style.predict(m, photos)
        style.smooth_by_scene(preds, scenes)
        all_vals = [_values_from_style(m, ph, pr) for ph, pr in zip(photos, preds)]
        templates = [_template_for(m, ph, a.khong_mask) for ph in photos]

    rows, cnt = [], {"ghi": 0, "de": 0, "bo-qua": 0, "thu": 0}
    for r, ph, sc, v, tpl in zip(raws, photos, scenes, all_vals, templates):
        content = xmp.build_xmp(tpl, v, curve, tool_name=f"Editer {__version__}")
        status = "thu" if a.thu else xmp.write_sidecar(r, content, a.ghi_de)
        cnt[status] += 1
        rows.append({
            "anh": r.name, "canh": sc + 1, "may": ph.model or "",
            "as_shot_K": round(ph.as_shot_temp) if ph.as_shot_temp else "",
            "Temperature": round(v["Temperature"]) if "Temperature" in v else "",
            "Tint": round(v["Tint"]) if "Tint" in v else "",
            "Exposure": f"{v.get('Exposure2012', 0):+.2f}", "trang_thai": status,
        })
    report = folder / "editer-bao-cao.csv"
    try:
        with open(report, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    except OSError:
        report = None
    for row in rows[: min(len(rows), 12)]:
        _log(f"  {row['anh']:24s} cảnh {row['canh']:<3} {row['Temperature']!s:>5}K tint {row['Tint']!s:>4} exp {row['Exposure']}")
    if len(rows) > 12:
        _log(f"  ... và {len(rows) - 12} ảnh khác")
    if curve:
        _log(f"Tone curve chung: {curve}")
    _log(f"\nXONG: ghi mới {cnt['ghi']}, ghi đè {cnt['de']} (bản cũ giữ ở .xmp.bak), chạy thử {cnt['thu']}.")
    if report:
        _log(f"Bảng chi tiết: {report}")
    if not a.thu:
        _log("Mở thư mục bằng Adobe Bridge hoặc Camera Raw để xem và chỉnh tiếp.")


def _masks_only(m: dict | None, ph: raw.Photo, no_mask: bool) -> str | None:
    """Khi chỉnh theo JPG: chỉ mượn mask AI (da, tóc...) từ phong cách, không mượn màu."""
    if not m or no_mask:
        return None
    import xml.etree.ElementTree as ET
    t = ET.fromstring(m["templates"].get(ph.model) or m["templates"][m["default_model"]])
    new = ET.Element(t.tag, {k: v for k, v in t.attrib.items() if not k.startswith(xmp.CRS)})
    for c in t:
        if c.tag == xmp.CRS + "MaskGroupBasedCorrections":
            new.append(c)
    return ET.tostring(new, encoding="unicode")


def cmd_xem(a) -> None:
    ph = raw.load(a.anh)
    if ph.error:
        raise SystemExit(ph.error)
    _log(f"{Path(a.anh).name}: máy {ph.model}, As Shot {ph.as_shot_temp and round(ph.as_shot_temp)}K "
         f"tint {ph.as_shot_tint and round(ph.as_shot_tint)}, ISO {ph.iso}, ảnh xem trước {ph.preview.shape[1]}x{ph.preview.shape[0]}")


STYLE_DIR = Path.home() / "Editer"
STYLE_FILE = STYLE_DIR / "phong-cach.editer"


def _ask_path(prompt: str, must_dir: bool | None = None, allow_empty: bool = False) -> str:
    while True:
        s = input(prompt).strip().strip('"').strip()
        if not s and allow_empty:
            return ""
        p = Path(s)
        if s and p.exists() and (must_dir is None or p.is_dir() == must_dir):
            return str(p)
        _log("  Không thấy thư mục/file đó, thử lại (có thể kéo thả vào cửa sổ này).")


def _ask_many(prompt: str, must_dir: bool) -> list[str]:
    _log(prompt + " (mỗi lần 1 mục, Enter trống để xong)")
    out = []
    while True:
        s = _ask_path(f"  [{len(out) + 1}] ", must_dir, allow_empty=bool(out))
        if not s:
            return out
        out.append(s)


def menu() -> None:
    """Bấm đúp editer-anh.exe -> menu hỏi từng bước, không cần gõ lệnh."""
    _log(f"===== EDITER ẢNH {__version__} =====")
    _log("Ảnh không rời khỏi máy. Tool chỉ ghi file .xmp cạnh ảnh RAW, không sửa ảnh gốc.\n")
    _log("  1. Học phong cách từ các job đã chỉnh (RAW + .xmp)")
    _log("  2. Chỉnh một thư mục RAW theo phong cách đã học")
    _log("  3. Chỉnh một thư mục RAW theo màu của ảnh JPG mẫu")
    _log("  4. Thoát")
    c = input("\nChọn (1-4): ").strip()
    try:
        if c == "1":
            dirs = _ask_many("Kéo thả các thư mục job đã chỉnh vào đây", True)
            STYLE_DIR.mkdir(exist_ok=True)
            main(["hoc", *dirs, "-o", str(STYLE_FILE)])
        elif c == "2":
            if not STYLE_FILE.exists():
                _log("Chưa có phong cách — chọn mục 1 trước.")
            else:
                d = _ask_path("Kéo thả thư mục RAW cần chỉnh: ", True)
                main(["chinh", d, "--phong-cach", str(STYLE_FILE)])
        elif c == "3":
            jpgs = _ask_many("Kéo thả ảnh JPG mẫu", False)
            d = _ask_path("Kéo thả thư mục RAW cần chỉnh: ", True)
            args = ["chinh", d, "--mau-jpg", *jpgs]
            if STYLE_FILE.exists() and input("Giữ độ nét, khử nhiễu, mask da/tóc từ phong cách của bạn? (c/k) [c]: ").strip().lower() != "k":
                args += ["--phong-cach", str(STYLE_FILE)]
            main(args)
    except SystemExit as e:
        if e.code not in (0, None):
            _log(f"\n{e.code}")
    except Exception as e:  # không để cửa sổ tắt mất mà không thấy lỗi
        import traceback
        traceback.print_exc()
        _log(f"\nLỖI: {e}")
    input("\nNhấn Enter để đóng...")


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if argv is None and len(sys.argv) == 1:
        return menu()
    ap = argparse.ArgumentParser(prog="editer-anh", description="Editer Ảnh — tự chỉnh màu RAW, ghi .xmp cho Camera Raw")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="lenh", required=True)

    h = sub.add_parser("hoc", help="học phong cách từ các job đã chỉnh (RAW + .xmp)")
    h.add_argument("thu_muc", nargs="+")
    h.add_argument("-o", "--ra", default="phong-cach.editer")
    h.add_argument("--luong", type=int, default=min(8, os.cpu_count() or 4))
    h.set_defaults(func=cmd_hoc)

    c = sub.add_parser("chinh", help="chỉnh một thư mục RAW (ghi .xmp)")
    c.add_argument("thu_muc")
    c.add_argument("--phong-cach", help="file phong cách đã học (.editer)")
    c.add_argument("--mau-jpg", nargs="+", help="1 hoặc vài ảnh JPG có màu muốn theo")
    c.add_argument("--do-manh", type=float, default=0.85, help="mức theo màu ảnh mẫu, 0..1 (mặc định 0.85)")
    c.add_argument("--cach-canh", type=float, default=4.0, help="số phút cách nhau thì tính là cảnh mới")
    c.add_argument("--ghi-de", action="store_true", help="chỉnh cả ảnh đã có .xmp (giữ bản cũ .xmp.bak)")
    c.add_argument("--thu", action="store_true", help="chạy thử, không ghi file")
    c.add_argument("--khong-mask", action="store_true", help="không mang mask AI (da/tóc) sang")
    c.add_argument("--khong-thu-muc-con", action="store_true")
    c.add_argument("--luong", type=int, default=min(8, os.cpu_count() or 4))
    c.set_defaults(func=cmd_chinh)

    x = sub.add_parser("xem", help="xem thông tin một file RAW")
    x.add_argument("anh")
    x.set_defaults(func=cmd_xem)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
