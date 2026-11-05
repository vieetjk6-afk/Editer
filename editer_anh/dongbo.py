"""ĐỒNG BỘ TỪ ẢNH BẠN ĐÃ CHỈNH — cách chắc chắn đúng màu nhất.

Bạn chỉnh tay 1 (hoặc vài) ảnh mỗi cảnh trong Camera Raw (có thể bắt đầu từ kết quả "Chép màu"
rồi cân lại), bấm Done. Tool áp ĐÚNG thông số đó (profile, curve, HSL, color grading, chi tiết,
mask AI) cho các ảnh còn lại của cảnh, chỉ tự cân lại cho từng ảnh — như Sync của Lightroom:
  - Nhiệt độ màu / Tint: GIỮ NGUYÊN như ảnh mẫu (cùng cảnh = cùng ánh sáng); chỉ dịch khi cân bằng
    trắng máy tự đo chênh lớn (ánh sáng đổi thật: ra bóng râm, bật đèn...);
  - Exposure: so độ sáng DA NGƯỜI trên ảnh máy chụp (có AI) với thông số chụp ISO / tốc độ / khẩu
    ("Match Total Exposures" của Lightroom). Hai cách khớp nhau -> theo thông số chụp (chính xác);
    lệch nhiều (ánh sáng đổi, flash TTL) -> theo da. Không có da -> theo thông số chụp; có flash
    (thông số chụp không nói lên độ sáng) -> theo độ sáng chung, nhẹ tay.
Không mang sang: cắt khung, xoay / phối cảnh, xoá vết, chỉnh cục bộ theo VỊ TRÍ (cọ tô, gradient),
profile ống kính khi ảnh chụp bằng ống kính khác.
Không phải ĐOÁN màu từ ảnh của người khác -> không sai lệch kiểu chép màu.

Ảnh "bạn đã chỉnh" = có .xmp do Camera Raw / Lightroom lưu (không phải .xmp tool ghi mà bạn chưa
mở lại). Ảnh đó KHÔNG bao giờ bị ghi đè. Sao / nhãn màu / từ khoá trong .xmp cũ được giữ nguyên.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import numpy as np

from . import color, look, raw, style, xmp


def split(folder: str | Path) -> tuple[list[Path], list[Path]]:
    """-> (ảnh bạn đã chỉnh — làm mẫu, ảnh cần đồng bộ: chưa có .xmp, .xmp do tool ghi, chỉ có sao / nhãn,
    hoặc Camera Raw lưu toàn thông số mặc định)."""
    refs, todo = [], []
    for r in raw.list_raws(folder):
        (refs if xmp.user_edited(xmp.find_sidecar(r)) else todo).append(r)
    return refs, todo


def _cam_ev(ph) -> float | None:
    """Lượng sáng máy thu (log2 ISO·t/N²): chụp tối hơn 1 stop -> cần +1 EV."""
    if ph.iso and ph.shutter and ph.fnumber:
        return math.log2(ph.iso * ph.shutter / ph.fnumber ** 2)
    return None


def _soft(d: float, dead: float) -> float:
    """Phần chênh vượt quá ngưỡng (bỏ phần nhỏ: đó là máy tự cân bằng trắng dao động, không phải ánh sáng đổi)."""
    return math.copysign(max(0.0, abs(d) - dead), d)


def _key(s: str | None) -> str:
    return " ".join((s or "").upper().split())


def same_camera(a, b) -> bool:
    return (_key(a.make), _key(a.model)) == (_key(b.make), _key(b.model))


def same_lens(a, b) -> bool:
    return bool(_key(a.lens)) and _key(a.lens) == _key(b.lens)


def _pick(i: int, photos, scenes, embs, ref_idx: list[int]) -> int:
    """Ảnh mẫu cho ảnh i: cùng cảnh (ưu tiên cùng máy, rồi gần giờ chụp nhất); không có thì giống nội dung nhất."""
    def dt(r):
        return abs((photos[r].time or 0) - (photos[i].time or 0))
    same = [r for r in ref_idx if scenes[r] == scenes[i]]
    if same:
        return min([r for r in same if same_camera(photos[r], photos[i])] or same, key=dt)
    if embs is not None:
        return max(ref_idx, key=lambda r: float(embs[r] @ embs[i]))
    return min(ref_idx, key=dt)


def brightness(img: np.ndarray, reg: np.ndarray | None) -> tuple[float, bool]:
    """Độ sáng (tuyến tính) trên ảnh máy chụp: trung vị vùng da người nếu thấy đủ da, không thì cả ảnh.
    -> (độ sáng, có phải đo trên da)."""
    lin = color.srgb_to_linear(img.reshape(-1, 3).astype(np.float64) / 255.0)
    y = lin @ np.array([0.2126, 0.7152, 0.0722])
    if reg is not None and reg.size == y.size:
        skin = np.isin(reg.reshape(-1), list(look.SKIN_SET))
        if skin.mean() > 0.005:
            return float(np.median(y[skin])) + 1e-4, True
    return float(np.median(y)) + 1e-4, False


def exposure_shift(ref, tgt, b_ref: tuple[float, bool], b_tgt: tuple[float, bool]) -> tuple[float, str]:
    """Exposure cộng thêm cho ảnh tgt so với ảnh mẫu ref (EV) + cách tính.
    b_* = brightness() trên ảnh máy chụp (cùng cách máy dựng ảnh -> so với nhau được; khác đời máy thì
    máy dựng ảnh khác nhau -> tin thông số chụp hơn)."""
    er, ej = _cam_ev(ref), _cam_ev(tgt)
    d_cam = er - ej if er is not None and ej is not None and not (ref.flash or tgt.flash) else None
    d_img = math.log2(b_ref[0] / b_tgt[0])
    if d_cam is not None and not same_camera(ref, tgt):
        return d_cam, "thông số chụp"
    if b_ref[1] and b_tgt[1]:
        # ảnh máy chụp đã qua đường cong tương phản: chênh 1 EV thật ~ 1,3 EV trên ảnh -> nhân 0,75
        d_skin = float(np.clip(0.75 * d_img, -2.0, 2.0))
        if d_cam is not None and abs(d_skin - d_cam) <= 0.6:
            return d_cam, "thông số chụp"
        return d_skin, "da người"
    if d_cam is not None:
        return d_cam, "thông số chụp"
    return float(np.clip(0.5 * d_img, -1.0, 1.0)), "độ sáng chung"


def sync_folder(folder: str | Path, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    folder = Path(folder)
    refs, todo = split(folder)
    log(f"Ảnh bạn đã chỉnh (làm mẫu): {len(refs)} · ảnh sẽ đồng bộ: {len(todo)}")
    if not refs:
        raise SystemExit("Chưa có ảnh nào bạn đã chỉnh trong thư mục này. Mở mỗi cảnh 1 ảnh trong Camera Raw, "
                         "chỉnh đến khi ưng, bấm Done (lưu .xmp) rồi chạy lại.")
    if not todo:
        return {"thu_muc": str(folder), "ghi": 0, "de": 0, "so_mau": len(refs), "anh": [],
                "thong_bao": "Mọi ảnh đều đã được bạn chỉnh — không còn ảnh nào để đồng bộ."}
    paths = refs + todo
    log(f"Đọc {len(paths)} ảnh...")
    photos = style.load_many(paths, workers, log)
    for line in raw.wb_summary(photos):
        log(line)
    embs, seg = None, None
    if use_ai:
        from . import ai
        emb = ai.Embedder.get()
        if emb is not None:
            log(f"AI đang xem {len(photos)} ảnh ({emb.device})...")
            embs = emb.embed([p.preview if p.preview is not None else np.zeros((8, 8, 3), np.uint8)
                              for p in photos])
        seg = ai.Segmenter.get()
    scenes = style.group_scenes(photos, embs=embs)
    ref_idx = [i for i in range(len(refs)) if not photos[i].error and photos[i].preview is not None]
    if not ref_idx:
        raise SystemExit("Không đọc được ảnh mẫu (ảnh bạn đã chỉnh).")
    cams = look.as_shot_shots(photos)

    ok = [i for i, p in enumerate(photos) if not p.error and p.preview is not None]
    bright: dict[int, tuple[float, bool]] = {}
    if seg is not None:
        log(f"AI tìm da người để cân độ sáng ({len(ok)} ảnh)...")
    for n, i in enumerate(ok, 1):
        up = photos[i].upright()
        try:
            reg = seg.segment(up) if seg is not None else None
        except Exception:
            reg = None
        bright[i] = brightness(up, reg)
        if seg is not None and (n % 50 == 0 or n == len(ok)):
            log(f"  đã xem {n}/{len(ok)}")

    tpl_cache: dict = {}
    crs_cache: dict = {}
    cnt = {"ghi": 0, "de": 0}
    how = Counter()
    rows = []
    for i in range(len(refs), len(paths)):
        ph = photos[i]
        if ph.error or ph.preview is None:
            log(f"  bỏ qua {Path(ph.path).name}: {ph.error or 'không có ảnh xem trước'}")
            continue
        r = _pick(i, photos, scenes, embs, ref_idx)
        ref_xmp = xmp.find_sidecar(paths[r])
        if r not in crs_cache:
            crs_cache[r] = xmp.read_settings(ref_xmp)["crs"]
        crs = crs_cache[r]
        d_ev, cach = exposure_shift(photos[r], ph, bright[r], bright[i])
        how[cach] += 1
        vals: dict = {"Exposure2012": float(np.clip((xmp.number(crs, "Exposure2012") or 0.0) + d_ev, -5, 5))}
        t_ref = xmp.number(crs, "Temperature")
        as_shot = crs.get("WhiteBalance") == "As Shot" or t_ref is None
        if as_shot:
            wb_txt = "As Shot (như ảnh mẫu)"
        else:
            m = 1e6 / t_ref
            tint = xmp.number(crs, "Tint") or 0.0
            if cams[r].mired is not None and cams[i].mired is not None:
                m += _soft(cams[i].mired - cams[r].mired, 25.0)
                tint += _soft((cams[i].tint or 0.0) - (cams[r].tint or 0.0), 10.0)
            vals.update({"WhiteBalance": "Custom", "Temperature": float(np.clip(1e6 / m, 2000, 50000)),
                         "Tint": float(np.clip(tint, -150, 150))})
            wb_txt = f"{vals['Temperature']:.0f}K tint {vals['Tint']:+.0f}"
        cam_ok, lens_ok = same_camera(photos[r], ph), same_lens(photos[r], ph)
        key = (r, cam_ok, lens_ok)
        if key not in tpl_cache:
            tpl, skipped = xmp.sync_template(ref_xmp, same_camera=cam_ok, same_lens=lens_ok,
                                             drop=("Temperature", "Tint") if as_shot else ())
            if skipped and not any(k[0] == r for k in tpl_cache):
                log(f"  {Path(paths[r]).name}: không mang sang {', '.join(skipped)} — chỉ đúng vị trí trong ảnh đó")
            tpl_cache[key] = tpl
        st = xmp.write_sidecar(ph.path, xmp.build_xmp(tpl_cache[key], vals), overwrite=True)
        cnt["ghi" if st == "ghi" else "de"] += 1
        same = scenes[r] == scenes[i]
        rows.append({"ten": Path(ph.path).name, "mau": Path(paths[r]).name, "cung_canh": same,
                     "wb": wb_txt, "exp": vals["Exposure2012"], "cach": cach,
                     "khac_may": not cam_ok})
        log(f"  {Path(ph.path).name:<22} <- {Path(paths[r]).name}{'' if same else ' (khác cảnh, giống nhất)'}"
            f"{'' if cam_ok else ' (khác máy)'}: {wb_txt}, exp {vals['Exposure2012']:+.2f} ({cach})")
    if how:
        log("Độ sáng: " + ", ".join(f"{n} ảnh theo {k}" for k, n in how.most_common()))
    log(f"XONG: đồng bộ {cnt['ghi'] + cnt['de']} ảnh theo {len(ref_idx)} ảnh bạn đã chỉnh "
        f"({cnt['de']} ảnh ghi lại .xmp cũ — bản cũ giữ ở .xmp.bak, sao / nhãn màu giữ nguyên).")
    order = sorted(range(len(paths)), key=lambda k: (photos[k].time or 0, str(paths[k])))
    return {"thu_muc": str(folder), "ghi": cnt["ghi"], "de": cnt["de"], "so_mau": len(ref_idx), "anh": rows,
            "tep": [str(paths[k]) for k in order]}
