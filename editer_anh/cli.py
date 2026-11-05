"""Dòng lệnh Editer Ảnh.

  hoc    <thư mục job đã chỉnh>...  -o phong-cach.editer
  chinh  <thư mục RAW> --phong-cach phong-cach.editer
  chinh  <thư mục RAW> --mau-jpg mau.jpg [--photoshop] [--luu-preset TÊN] [--phong-cach ...]
  kiem-tra-photoshop [ảnh RAW]
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import __version__, color, look, nhatky, raw, style, xmp
from . import ps as psmod

# Đặt tất cả về 0 khi chỉnh theo JPG (để kết quả không phụ thuộc mặc định của máy)
_NEUTRAL = {
    "Contrast2012": 0, "Highlights2012": 0, "Shadows2012": 0, "Whites2012": 0, "Blacks2012": 0,
    "Texture": 0, "Clarity2012": 0, "Dehaze": 0, "Vibrance": 0, "Saturation": 0,
    "ParametricShadows": 0, "ParametricDarks": 0, "ParametricLights": 0, "ParametricHighlights": 0,
    "ColorGradeMidtoneHue": 0, "ColorGradeMidtoneSat": 0, "ColorGradeShadowLum": 0,
    "ColorGradeMidtoneLum": 0, "ColorGradeHighlightLum": 0, "ColorGradeBlending": 50,
    "ColorGradeBalance": 0, "ColorGradeGlobalHue": 0, "ColorGradeGlobalSat": 0, "ColorGradeGlobalLum": 0,
    "CameraProfile": "Adobe Color", "ProcessVersion": "15.4", "Version": "18.0",
    "LensProfileEnable": 1,
}


def _log(msg: str) -> None:
    print(msg, flush=True)


_NAMES = {"d_mired": "Nhiệt độ màu", "d_tint": "Tint", "Exposure2012": "Exposure",
          "Contrast2012": "Contrast", "Highlights2012": "Highlights", "Shadows2012": "Shadows",
          "Whites2012": "Whites", "Blacks2012": "Blacks", "Vibrance": "Vibrance"}
_METHODS = {"gbm": "học quy luật", "knn": "theo ảnh cũ giống nhất", "blend": "kết hợp 2 cách"}


def _fmt_err(t: str, v: float) -> str:
    if t == "d_mired":
        return f"±{1e6 / (1e6 / 5500 - v) - 5500:.0f}K"
    return f"±{v:.2f}"


def cmd_hoc(a) -> dict:
    t0 = time.time()
    out = Path(a.ra)
    prev = None
    if out.exists() and not a.hoc_lai:
        try:
            prev = style.load(out)
            _log(f"Học thêm vào phong cách đã có ({prev['n_photos']} ảnh). Thêm --hoc-lai để học lại từ đầu.")
        except SystemExit:
            _log("File phong cách cũ khác phiên bản — học lại từ đầu.")
    m = style.learn(a.thu_muc, log=_log, workers=a.luong, previous=prev)
    out.parent.mkdir(parents=True, exist_ok=True)
    style.save(m, out)
    _log(f"\nĐã học từ {m['n_photos']} ảnh ({', '.join(f'{k}: {v}' for k, v in m['counts'].items())}).")
    _log("Độ chính xác (giấu 1/5 số ảnh, cho tool đoán, so với cách bạn chỉnh thật):")
    res = {"file": str(out.resolve()), "n": m["n_photos"], "counts": m["counts"], "rows": [], "tien_bo": []}
    for t, r in m["report"].items():
        n = _NAMES.get(t, t)
        if math.isnan(r["mae"]):
            _log(f"  {n:13s}: chưa đủ ảnh để kiểm tra (n={r['n']}, cần 40+)")
            res["rows"].append({"ten": n, "lech": None, "n": r["n"]})
            continue
        res["rows"].append({"ten": n, "lech": _fmt_err(t, r["mae"]), "co_dinh": _fmt_err(t, r["mae_median"]),
                            "cach": _METHODS.get(r["method"], r["method"]),
                            "khong_ai": _fmt_err(t, r["mae_no_ai"]) if "mae_no_ai" in r else None})
        _log(f"  {n:13s}: lệch TB {_fmt_err(t, r['mae']):>7s}  | dùng 1 số cố định: {_fmt_err(t, r['mae_median']):>7s}"
             f"  | cách tốt nhất: {_METHODS.get(r['method'], r['method'])}"
             + (f"  | nếu không có AI: {_fmt_err(t, r['mae_no_ai'])}" if "mae_no_ai" in r else ""))
    hist = [h for h in m.get("history", []) if not math.isnan(h.get("d_mired", math.nan))]
    if len(hist) >= 2:
        _log("Tiến bộ qua các lần học (nhiệt độ màu): " + " -> ".join(
            f"{_fmt_err('d_mired', h['d_mired'])} ({h['n']} ảnh)" for h in hist[-6:]))
        res["tien_bo"] = [{"lech": _fmt_err("d_mired", h["d_mired"]), "n": h["n"]} for h in hist[-6:]]
    _log(f"\nĐã lưu: {out.resolve()}  ({time.time() - t0:.0f}s)")
    return res


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


def cmd_chinh(a) -> dict:
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
        return {"thu_muc": str(folder), "ghi": 0, "de": 0, "so_anh": 0, "csv": None, "chep_mau": None,
                "thong_bao": "Không còn ảnh nào cần chỉnh (mọi ảnh đã có .xmp). Bật 'Ghi đè' để chỉnh lại."}
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
    for line in raw.wb_summary(photos):
        _log(line)
    from . import ai
    emb_model = None if a.khong_ai else ai.Embedder.get()
    if m and m.get("ai") and emb_model is None:
        _log("CẢNH BÁO: phong cách được học có AI nhưng máy này không bật được AI — kết quả sẽ kém chính xác hơn.")
    embs = None
    if emb_model:
        _log(f"AI đang xem {len(photos)} ảnh ({emb_model.device})...")
        embs = emb_model.embed([p.preview for p in photos])
    scenes = style.group_scenes(photos, a.cach_canh, embs)
    scene_names = {}
    if embs is not None:
        for sc in set(scenes):
            e = embs[[i for i, x in enumerate(scenes) if x == sc]].mean(axis=0)
            scene_names[sc] = ", ".join(emb_model.top_labels(e / np.linalg.norm(e)))
    _log(f"Chia được {len(set(scenes))} cảnh" + (" (theo giờ chụp + nội dung ảnh)." if embs is not None else " (theo giờ chụp)."))
    for sc in sorted(scene_names)[:15]:
        _log(f"  cảnh {sc + 1}: {scene_names[sc]} ({scenes.count(sc)} ảnh)")

    jpg_info = None
    if a.mau_jpg:
        all_vals, curves, templates, jpg_info = _copy_color(a, folder, m, photos, scenes, embs, emb_model)
    else:
        preds = style.predict(m, photos, embs if m.get("ai") else None)
        style.smooth_by_scene(preds, scenes)
        all_vals = [_values_from_style(m, ph, pr) for ph, pr in zip(photos, preds)]
        templates = [_template_for(m, ph, a.khong_mask) for ph in photos]
        curves = [None] * len(photos)

    rows, cnt = [], {"ghi": 0, "de": 0, "bo-qua": 0, "thu": 0}
    for r, ph, sc, v, tpl, crv in zip(raws, photos, scenes, all_vals, templates, curves):
        content = xmp.build_xmp(tpl, v, crv, tool_name=f"Editer {__version__}")
        status = "thu" if a.thu else xmp.write_sidecar(r, content, a.ghi_de)
        cnt[status] += 1
        rows.append({
            "anh": r.name, "canh": sc + 1, "loai_canh": scene_names.get(sc, ""), "may": ph.model or "",
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
    if curves and curves[0]:
        _log(f"Tone curve chung: {curves[0]}")
    _log(f"\nXONG: ghi mới {cnt['ghi']}, ghi đè {cnt['de']} (bản cũ giữ ở .xmp.bak), chạy thử {cnt['thu']}.")
    if report:
        _log(f"Bảng chi tiết: {report}")
    if not a.thu:
        _log("Mở thư mục bằng Adobe Bridge hoặc Camera Raw để xem và chỉnh tiếp.")
    return {"thu_muc": str(folder), "ghi": cnt["ghi"], "de": cnt["de"], "thu": cnt["thu"], "so_anh": len(rows),
            "tep": [str(r) for r in raws],
            "csv": str(report) if report else None, "so_canh": len(set(scenes)),
            "canh": [{"so": sc + 1, "ten": scene_names.get(sc, ""), "anh": scenes.count(sc)}
                     for sc in sorted(set(scenes))], "chep_mau": jpg_info, "curve": curves[0] if curves else None}


def _compose_jpg(m: dict | None, ph: raw.Photo, lk: look.Look, shot: look.Shot, no_mask: bool):
    """Thông số .xmp khi chép màu: nền trung tính + (độ nét, khử nhiễu từ phong cách) + look."""
    out = dict(_NEUTRAL)
    if m:
        tattrs = xmp.template_attrs(m["templates"].get(ph.model) or m["templates"][m["default_model"]])
        for k in xmp.DETAIL_KEYS:
            if k in tattrs:
                out[k] = tattrs[k]
        out["Version"] = tattrs.get("Version", out["Version"])
    vals, crv = look.xmp_values(lk, shot)
    out.update(vals)
    return out, crv, _masks_only(m, ph, no_mask)


def _blend(lk: look.Look, shots: list, cams: list, s: float):
    """Chép màu một phần (--do-manh < 1): đi một đoạn từ ảnh gốc tới kết quả."""
    if s >= 0.999:
        return lk, shots
    base = look.Look()
    mix = look.Look(base.curve + s * (lk.curve - base.curve), s * lk.hue, s * lk.sat, s * lk.lum,
                    s * lk.grade, s * lk.saturation, s * lk.vibrance)
    mix.skin_loc, mix.bg_loc = s * lk.skin_loc, s * lk.bg_loc
    out = []
    for sh, c in zip(shots, cams):
        m = None if (sh.mired is None or c.mired is None) else c.mired + s * (sh.mired - c.mired)
        t = None if (sh.tint is None or c.tint is None) else c.tint + s * (sh.tint - c.tint)
        out.append(look.Shot(m, t, s * sh.ev))
    return mix, out


def _copy_color(a, folder: Path, m: dict | None, photos: list, scenes: list, embs, emb_model, factory=None,
                ten: str | None = None):
    """factory: bộ dựng ảnh thật có sẵn (vd Lightroom dựng qua plugin) thay cho Photoshop.
    ten: tên bên dựng ảnh thật để ghi nhật ký ("Lightroom" / mặc định "Camera Raw")."""
    from dataclasses import replace as _replace

    from . import ai
    ten = ten or ("Lightroom" if factory is not None else "Camera Raw")
    lr = ten == "Lightroom"                      # Lightroom dựng ảnh thật (không phải Camera Raw qua Photoshop)
    # Ảnh dọc phải ĐỨNG THẲNG để AI nhận mặt/người đúng (và hiển thị đúng chiều)
    photos = [_replace(p, preview=p.upright(), flip=0) if p.preview is not None else p for p in photos]
    seg = None if a.khong_ai else ai.Segmenter.get()
    if seg:
        _log(f"AI phân vùng (da, trắng, trời, cây): BẬT ({seg.device}, ~{seg.sec * 1000:.0f} ms/ảnh)")
    else:
        _log("AI phân vùng: TẮT — chỉ khớp tông sáng tối, điểm trắng và 8 dải màu.")
    seg_all = bool(seg) and (seg.fast or a.vung_tat_ca)
    if seg and not seg_all:
        _log("  (chỉ phân vùng ảnh đại diện vì máy đang chạy CPU — thêm --vung-tat-ca để phân vùng mọi ảnh)")
    segment = seg.segment if seg else None
    ref = look.load_reference(a.mau_jpg, segment, emb_model)
    found = ref.sel.found()
    _log("Ảnh mẫu: thấy " + (", ".join(found) if found else "không có vùng đặc biệt (chỉ khớp màu chung)"))
    ps_obj = None
    if a.photoshop and factory is None:
        _log("Đang kết nối Photoshop...")
        ps_obj, msg = psmod.check()
        if ps_obj:
            _log(f"  Đã kết nối {msg} — Camera Raw thật sẽ dựng ảnh để kiểm tra màu.")
        else:
            _log(f"  Không kết nối được Photoshop ({msg}) — dùng ước lượng mô phỏng.")
    if ps_obj is not None:
        work = psmod.workdir_for(folder)

        def factory(rep_photos):
            def render(i, lk, shot):
                vals, crv, tpl = _compose_jpg(m, rep_photos[i], lk, shot, a.khong_mask)
                img = ps_obj.render_raw(Path(rep_photos[i].path), xmp.build_xmp(tpl, vals, crv), work, a.co_anh)
                if shot.mired is None and ps_obj.last_wb:      # As Shot -> Camera Raw cho biết số thật
                    render.as_shot[i] = ps_obj.last_wb[:2]
                    nhatky.note(f"As Shot theo Camera Raw {Path(rep_photos[i].path).name}: "
                                f"{ps_obj.last_wb[0]:.0f}K tint {ps_obj.last_wb[1]:+.0f} | tool tính: "
                                f"{rep_photos[i].as_shot_temp and round(rep_photos[i].as_shot_temp)}K "
                                f"({rep_photos[i].wb_src})")
                return img
            render.as_shot = {}
            return render
    n_reps = a.so_anh_dai_dien or (4 if (ps_obj is not None or factory is not None) else 6)
    try:
        looks, shots, info = look.copy_color(photos, scenes, embs, ref, segment, _log, factory,
                                             n_reps, a.so_luot, seg_all)
    finally:
        if ps_obj is not None:
            psmod.cleanup(folder)
            ps_obj.restore()
    gui_info = {"mo_phong": info.get("mo_phong"), "camera_raw": info.get("camera_raw"),
                "ref_found": found, "photoshop": ps_obj is not None,
                "anh_dai_dien": info.get("anh_dai_dien", []), "preset": None,
                "mask_ai": info.get("mask_ai"), "giu_goc": info.get("giu_goc", False),
                "tep_dai_dien": [photos[i].path for i in info.get("reps", [])]}
    if info.get("mask_ai"):
        mk = info["mask_ai"]
        xem = "mở ảnh trong Develop để xem" if lr else "mở ảnh trong Camera Raw để xem"
        word = {True: f"CÓ (đã đo trên {ten})", "mo_phong": f"CÓ (theo mô phỏng — {ten} không áp mask "
                f"khi dựng tự động nên chưa đo được; {xem})", False: "không"}
        _log(f"Chỉnh riêng từng phần bằng mask AI: da {word[mk['da']]}, nền {word[mk['nen']]}")
    if "camera_raw" in info:
        _log(f"KẾT QUẢ ĐO TRÊN {ten.upper()} (ảnh đại diện): {look.fmt_report(info['camera_raw'])}")
    else:
        _log(f"Ước lượng (chưa kiểm tra bằng {ten}): {look.fmt_report(info['mo_phong'])}")
    _log("  (ΔE dưới 2: mắt gần như không phân biệt; 2-5: gần; trên 5: còn khác rõ)")
    cams = look.as_shot_shots(photos)
    vals, curves, tpls = [], [], []
    for ph, lk, sh, cam in zip(photos, looks, shots, cams):
        lk, (sh,) = _blend(lk, [sh], [cam], a.do_manh)
        v, crv, tpl = _compose_jpg(m, ph, lk, sh, a.khong_mask)
        vals.append(v)
        curves.append(crv)
        tpls.append(tpl)
    if a.luu_preset:
        name = f"Editer - {a.luu_preset}"
        d = xmp.preset_dir()
        safe = "".join(c for c in name if c not in '\\/:*?"<>|')
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{safe}.xmp").write_text(xmp.build_preset(name, vals[0], curves[0]), encoding="utf-8")
            _log(f"Đã lưu preset Camera Raw: {d / (safe + '.xmp')} (nhóm 'Editer'; chỉ có màu, không có WB/exposure)")
            gui_info["preset"] = str(d / (safe + ".xmp"))
        except OSError as e:
            _log(f"Không lưu được preset: {e}")
    return vals, curves, tpls, gui_info


def cmd_dong_bo(a) -> dict:
    from . import dongbo
    folder = Path(a.thu_muc)
    if not folder.is_dir():
        raise SystemExit(f"Không thấy thư mục: {folder}")
    res = dongbo.sync_folder(folder, _log, a.luong, use_ai=not a.khong_ai)
    _log("Mở thư mục bằng Adobe Bridge hoặc Camera Raw để xem và chỉnh tiếp.")
    return res


def cmd_lr(a) -> dict:
    """Plugin Lightroom gọi: lr-canh / lr-dong-bo <job.json> <out.json>."""
    from . import lightroom
    kind = {"lr-canh": "canh", "lr-chep-mau": "chep-mau"}.get(a.lenh, "dong-bo")
    res = lightroom.run_job(kind, a.job, a.ra, _log, a.luong, use_ai=not a.khong_ai)
    if not res.get("ok"):
        raise SystemExit(1)
    return res


def cmd_lr_cai(a) -> dict:
    from . import lightroom
    return lightroom.install(_log)


def cmd_kiem_tra_ps(a) -> dict:
    ps_obj, msg = psmod.check()
    if not ps_obj:
        raise SystemExit(f"Không kết nối được Photoshop: {msg}")
    _log(f"Kết nối OK: {msg}")
    res = {"ok": True, "msg": msg, "anh": None, "giay": None}
    if a.anh:
        p = Path(a.anh)
        _log(f"Thử dựng {p.name} bằng Camera Raw (thông số trung tính)...")
        vals, crv = look.xmp_values(look.Look(), look.Shot(None, None, 0.0))
        out = dict(_NEUTRAL)
        out.update(vals)
        t = time.time()
        try:
            img = ps_obj.render_raw(p, xmp.build_xmp(None, out, crv), psmod.workdir_for(p.parent))
        finally:
            psmod.cleanup(p.parent)
            ps_obj.restore()
        _log(f"  OK: {img.shape[1]}x{img.shape[0]} px trong {time.time() - t:.1f}s, màu trung bình {img.reshape(-1, 3).mean(0).round()}")
        res["anh"], res["giay"] = img, round(time.time() - t, 1)
    return res


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
    _log("Ảnh không rời khỏi máy. Tool chỉ ghi file .xmp cạnh ảnh RAW, không sửa ảnh gốc.")
    _log(f"(Nhật ký: {nhatky.FILE})")
    _log("Mẹo: bấm đúp 'Editer Anh.exe' cùng thư mục để dùng giao diện cửa sổ (kéo thả, xem ảnh).\n")
    _log("  1. Học phong cách từ các job đã chỉnh (RAW + .xmp)")
    _log("  2. Chỉnh một thư mục RAW theo phong cách đã học")
    _log("  3. Chép màu ảnh JPG mẫu cho một thư mục RAW")
    _log("  4. Đồng bộ từ ảnh bạn đã chỉnh (mỗi cảnh chỉnh tay 1 ảnh trong Camera Raw -> cả cảnh theo)")
    _log("  5. Kiểm tra kết nối Photoshop (thử mở 1 ảnh RAW bằng Camera Raw)")
    _log("  6. Cài plugin Editer vào Lightroom Classic (chỉnh thẳng trong catalog đang mở)")
    _log("  7. Thoát")
    c = input("\nChọn (1-7): ").strip()
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
            jpgs = _ask_many("Kéo thả ảnh JPG mẫu (ảnh có màu bạn muốn chép)", False)
            d = _ask_path("Kéo thả thư mục RAW cần chỉnh: ", True)
            args = ["chinh", d, "--mau-jpg", *jpgs]
            if STYLE_FILE.exists() and input("Giữ độ nét, khử nhiễu, mask da/tóc từ phong cách của bạn? (c/k) [c]: ").strip().lower() != "k":
                args += ["--phong-cach", str(STYLE_FILE)]
            _log("Dùng Photoshop để Camera Raw thật dựng thử và sửa cho ĐÚNG màu? (chính xác hơn,")
            _log("mất thêm vài phút; Photoshop sẽ tự mở các ảnh đại diện rồi đóng lại)")
            if input("(c/k) [c]: ").strip().lower() != "k":
                args.append("--photoshop")
            name = input("Lưu màu này thành preset Camera Raw? Nhập tên (Enter để bỏ qua): ").strip()
            if name:
                args += ["--luu-preset", name]
            main(args)
        elif c == "4":
            _log("Trước đó: mở mỗi cảnh 1 ảnh trong Camera Raw, chỉnh đến khi ưng, bấm Done (lưu .xmp).")
            d = _ask_path("Kéo thả thư mục RAW của job: ", True)
            main(["dong-bo", d])
        elif c == "5":
            f = _ask_path("Kéo thả 1 file RAW (NEF/ARW/CR3...) vào đây: ", False)
            main(["kiem-tra-photoshop", f])
        elif c == "6":
            main(["lr-cai"])
    except SystemExit as e:
        if e.code not in (0, None):
            _log(f"\n{e.code}")
    except BaseException as e:  # không để cửa sổ tắt mất mà không thấy lỗi
        import traceback
        traceback.print_exc()
        _log(f"\nLỖI: {e}")
        _log(f"Gửi file nhật ký này để được hỗ trợ: {nhatky.FILE}")
    input("\nNhấn Enter để đóng...")


def main(argv=None) -> None:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--thu-card":   # tiến trình con thử card
        from . import ai
        sys.exit(ai.probe_main(sys.argv[2:]))
    if argv is None:   # lần gọi đầu tiên (từ dòng lệnh hoặc bấm đúp)
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
        nhatky.setup(__version__)
        if "--cpu" in sys.argv[1:]:
            os.environ["EDITER_CPU"] = "1"
        if len([a for a in sys.argv[1:] if a != "--cpu"]) == 0:
            return menu()
    ap = argparse.ArgumentParser(prog="editer-anh", description="Editer Ảnh — tự chỉnh màu RAW, ghi .xmp cho Camera Raw")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--cpu", action="store_true", help="chạy AI bằng CPU, không dùng card đồ hoạ")
    sub = ap.add_subparsers(dest="lenh", required=True)

    h = sub.add_parser("hoc", help="học phong cách từ các job đã chỉnh (RAW + .xmp)")
    h.add_argument("thu_muc", nargs="+")
    h.add_argument("-o", "--ra", default="phong-cach.editer")
    h.add_argument("--hoc-lai", action="store_true", help="bỏ dữ liệu đã học, học lại từ đầu")
    h.add_argument("--luong", type=int, default=min(8, os.cpu_count() or 4))
    h.set_defaults(func=cmd_hoc)

    c = sub.add_parser("chinh", help="chỉnh một thư mục RAW (ghi .xmp)")
    c.add_argument("thu_muc")
    c.add_argument("--phong-cach", help="file phong cách đã học (.editer)")
    c.add_argument("--mau-jpg", nargs="+", help="1 hoặc vài ảnh JPG có màu muốn theo")
    c.add_argument("--do-manh", type=float, default=1.0, help="mức chép màu, 0..1 (mặc định 1 = chép đủ)")
    c.add_argument("--photoshop", action="store_true",
                   help="nhờ Photoshop (Camera Raw thật) dựng ảnh để kiểm tra và sửa màu cho đúng")
    c.add_argument("--so-anh-dai-dien", type=int, default=0, help="số ảnh đại diện (mặc định 6, có Photoshop: 4)")
    c.add_argument("--so-luot", type=int, default=5, help="số lượt sửa với Camera Raw (mặc định 5)")
    c.add_argument("--co-anh", type=int, default=1024, help="cỡ ảnh Camera Raw dựng để đo (px)")
    c.add_argument("--luu-preset", help="lưu màu chép được thành preset Camera Raw với tên này")
    c.add_argument("--vung-tat-ca", action="store_true", help="phân vùng mọi ảnh (chậm nếu không có card đồ hoạ)")
    c.add_argument("--cach-canh", type=float, default=4.0, help="số phút cách nhau thì tính là cảnh mới")
    c.add_argument("--ghi-de", action="store_true", help="chỉnh cả ảnh đã có .xmp (giữ bản cũ .xmp.bak)")
    c.add_argument("--thu", action="store_true", help="chạy thử, không ghi file")
    c.add_argument("--khong-mask", action="store_true", help="không mang mask AI (da/tóc) sang")
    c.add_argument("--khong-thu-muc-con", action="store_true")
    c.add_argument("--khong-ai", action="store_true", help="không dùng AI hiểu ảnh")
    c.add_argument("--luong", type=int, default=min(8, os.cpu_count() or 4))
    c.set_defaults(func=cmd_chinh)

    d = sub.add_parser("dong-bo", help="đồng bộ từ ảnh bạn đã chỉnh tay (mỗi cảnh 1 ảnh) sang cả cảnh")
    d.add_argument("thu_muc")
    d.add_argument("--khong-ai", action="store_true", help="không dùng AI (chia cảnh theo giờ chụp, sáng theo cả ảnh)")
    d.add_argument("--luong", type=int, default=min(8, os.cpu_count() or 4))
    d.set_defaults(func=cmd_dong_bo)

    for name, hlp in (("lr-dong-bo", "(plugin Lightroom gọi) đồng bộ trong catalog"),
                      ("lr-canh", "(plugin Lightroom gọi) chia cảnh, chọn ảnh mẫu"),
                      ("lr-chep-mau", "(plugin Lightroom gọi) chép màu từ ảnh JPG mẫu")):
        q = sub.add_parser(name, help=hlp)
        q.add_argument("job")
        q.add_argument("ra")
        q.add_argument("--khong-ai", action="store_true")
        q.add_argument("--luong", type=int, default=min(8, os.cpu_count() or 4))
        q.set_defaults(func=cmd_lr)
    li = sub.add_parser("lr-cai", help="cài plugin Editer vào Lightroom Classic")
    li.set_defaults(func=cmd_lr_cai)

    k = sub.add_parser("kiem-tra-photoshop", help="kiểm tra kết nối Photoshop (và thử dựng 1 ảnh RAW)")
    k.add_argument("anh", nargs="?")
    k.set_defaults(func=cmd_kiem_tra_ps)

    x = sub.add_parser("xem", help="xem thông tin một file RAW")
    x.add_argument("anh")
    x.set_defaults(func=cmd_xem)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    main()
