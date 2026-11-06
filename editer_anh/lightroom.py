"""Editer trong LIGHTROOM CLASSIC — chỉnh thẳng trong catalog đang mở (thấy ngay, Ctrl+Z hoàn tác được).

Plugin `lightroom/Editer.lrplugin` (Lua, chạy trong Lightroom) lấy danh sách ảnh + thông số trong catalog,
gọi chương trình này để phân tích (đọc file RAW, AI chia cảnh / tìm da, As Shot), rồi tự áp kết quả bằng
API của Lightroom — không qua file .xmp nên không cần "Read Metadata from Files".

Lệnh (plugin gọi, trao đổi bằng file JSON):
  lr-canh <job.json> <out.json>     chia cảnh, chọn 1 ảnh mỗi cảnh để bạn chỉnh làm mẫu
  lr-dong-bo <job.json> <out.json>  mỗi ảnh còn lại theo ảnh mẫu nào, Exposure, nhiệt độ màu / tint
  lr-chep-mau <job.json> <out.json> chép màu từ ảnh JPG mẫu; Lightroom dựng thử ảnh đại diện để đo và sửa
  lr-cai                            chép plugin vào thư mục Modules của Lightroom (tự nạp khi mở Lightroom)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from . import __version__, dongbo, style

DATA = Path.home() / "Editer"
CONFIG = DATA / "lightroom.json"
PLUGIN = "Editer.lrplugin"
VERSION_FILE = "phien-ban.txt"


def plugin_src() -> Path:
    """Thư mục plugin đi kèm chương trình."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for cand in (Path(getattr(sys, "_MEIPASS", exe_dir)) / "lightroom" / PLUGIN, exe_dir / PLUGIN,
                     exe_dir / "_internal" / "lightroom" / PLUGIN):
            if (cand / "Info.lua").is_file():
                return cand
    return Path(__file__).resolve().parents[1] / "lightroom" / PLUGIN


def modules_dir() -> Path:
    """Lightroom Classic tự nạp mọi plugin trong thư mục này khi khởi động (không cần Plug-in Manager)."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Adobe" / "Lightroom" / "Modules"


def installed_dir() -> Path:
    return modules_dir() / PLUGIN


def helper_cmd() -> list[str]:
    """Lệnh plugin gọi để phân tích ảnh: editer-anh.exe (bản cài) hoặc python + mã nguồn."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cli = exe.with_name("editer-anh.exe")
        return [str(cli if cli.exists() else exe)]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "packaging" / "run.py")]


def _installed_version(d: Path) -> str | None:
    try:
        return (d / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _copy_plugin(dst: Path) -> None:
    src = plugin_src()
    if not (src / "Info.lua").is_file():
        raise SystemExit(f"Không thấy plugin Lightroom đi kèm ({src}).")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dst / VERSION_FILE).write_text(__version__, encoding="utf-8")


def _ver(s: str | None) -> tuple:
    try:
        return tuple(int(x) for x in str(s).strip().split("."))
    except ValueError:
        return ()


def register() -> dict:
    """Ghi cho plugin biết chương trình nằm ở đâu (đổi mỗi lần cập nhật); plugin đã cài bản cũ hơn thì thay.
    Mở lại một bản Editer CŨ hơn thì không hạ cấp plugin / không trỏ plugin về bản cũ."""
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        old = json.loads(CONFIG.read_text(encoding="utf-8")).get("phien_ban")
    except (OSError, ValueError):
        old = None
    old_exe = None
    try:
        old_exe = json.loads(CONFIG.read_text(encoding="utf-8")).get("cmd", [None])[0]
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    if _ver(__version__) >= _ver(old) or not old_exe or not Path(old_exe).exists():
        CONFIG.write_text(json.dumps({"cmd": helper_cmd(), "phien_ban": __version__}, ensure_ascii=False,
                                     indent=1), encoding="utf-8")
    dst = installed_dir()
    updated = False
    if dst.is_dir() and _ver(_installed_version(dst)) < _ver(__version__):
        try:
            _copy_plugin(dst)
            updated = True
        except (OSError, SystemExit):
            pass
    return {"da_cai": (dst / "Info.lua").is_file(), "noi_cai": str(dst), "cap_nhat": updated,
            "phien_ban": _installed_version(dst)}


def status() -> dict:
    dst = installed_dir()
    return {"da_cai": (dst / "Info.lua").is_file(), "noi_cai": str(dst), "phien_ban": _installed_version(dst),
            "co_lightroom": modules_dir().parent.is_dir(), "lenh": helper_cmd()}


def install(log=print) -> dict:
    dst = installed_dir()
    _copy_plugin(dst)
    st = register()
    path = " ".join(helper_cmd())
    if any(ord(c) > 127 for c in path):
        log("Lưu ý: đường dẫn chương trình có dấu tiếng Việt — nếu Lightroom báo không chạy được Editer, "
            "chép thư mục Editer sang đường dẫn không dấu (vd D:\\Editer) rồi bấm cài lại.")
    log(f"Đã cài plugin vào {dst}")
    log("Khởi động lại Lightroom Classic -> menu Library (Thư viện) -> Plug-in Extras -> Editer.")
    return {**st, "da_cai": True}


# ------------------------------------------------------------------ lệnh plugin gọi
def _items(job: dict) -> list[dict]:
    items = job.get("anh") or []
    if not items:
        raise SystemExit("Không có ảnh nào được gửi từ Lightroom.")
    return items


def run_scenes(job: dict, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    """Chia cảnh (giờ chụp + AI) và chọn ảnh giữa mỗi cảnh để bạn chỉnh."""
    items = _items(job)
    paths = [it["path"] for it in items]
    log(f"Đọc {len(paths)} ảnh để chia cảnh...")
    photos = style.load_many(paths, workers, log)
    embs = None
    if use_ai:
        from . import ai
        emb = ai.Embedder.get()
        if emb is not None:
            import numpy as np
            log(f"AI đang xem {len(photos)} ảnh ({emb.device})...")
            embs = emb.embed([p.preview if p.preview is not None else np.zeros((8, 8, 3), np.uint8)
                              for p in photos])
    scenes = style.group_scenes(photos, embs=embs)
    out = []
    for sc in sorted(set(scenes)):
        idx = sorted((i for i in range(len(paths)) if scenes[i] == sc), key=lambda i: photos[i].time or 0)
        good = [i for i in idx if not photos[i].error and photos[i].preview is not None] or idx
        out.append({"so": len(out) + 1, "anh": idx, "mau": good[len(good) // 2]})
    log(f"Chia được {len(out)} cảnh.")
    return {"ok": True, "phien_ban": __version__, "canh": out}


def run_sync(job: dict, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    """Mỗi ảnh chưa chỉnh theo ảnh bạn chỉnh cùng cảnh: Exposure + nhiệt độ màu riêng (thông số còn lại
    plugin chép thẳng từ ảnh mẫu trong catalog)."""
    items = _items(job)
    is_ref = [bool(it.get("mau")) for it in items]
    if not any(is_ref):
        raise SystemExit("Chưa có ảnh nào bạn đã chỉnh trong các ảnh đã chọn.")
    if all(is_ref):
        return {"ok": True, "phien_ban": __version__, "anh": [], "so_mau": len(items)}
    paths = [it["path"] for it in items]
    log(f"Lightroom: {sum(is_ref)} ảnh bạn đã chỉnh (làm mẫu) · {len(items) - sum(is_ref)} ảnh sẽ đồng bộ")
    ctx = dongbo.analyse(paths, is_ref, log, workers, use_ai)
    if not ctx.ref_idx:
        raise SystemExit("Không đọc được file RAW của ảnh mẫu (ảnh bạn đã chỉnh) — ổ đĩa chưa cắm / file đã chuyển chỗ?")
    out, how = [], Counter()
    for i in range(len(items)):
        if is_ref[i]:
            continue
        ph = ctx.photos[i]
        if ph.error or ph.preview is None:
            out.append({"i": i, "loi": ph.error or "không đọc được ảnh"})
            log(f"  bỏ qua {Path(ph.path).name}: {ph.error or 'không đọc được ảnh'}")
            continue
        d = dongbo.decide(ctx, i, lambda r: items[r].get("thong_so") or {})
        how[d["cach"]] += 1
        out.append({"i": i, **d})
        log(dongbo.describe(ctx, i, d))
    if how:
        log("Độ sáng: " + ", ".join(f"{n} ảnh theo {k}" for k, n in how.most_common()))
    return {"ok": True, "phien_ban": __version__, "anh": out, "so_mau": len(ctx.ref_idx)}


# ------------------------------------------------------------------ chép màu từ JPG trong Lightroom
_LR_SKIP = {"Version", "ProcessVersion", "CameraProfile", "LensProfileEnable"}   # giữ profile / ống kính của ảnh


def _lr_mask(item: dict) -> dict:
    """Chỉnh cục bộ "Editer - Da/Nền" (mask AI) theo dạng thông số của Lightroom (giống cấu trúc .xmp)."""
    import uuid
    from . import xmp
    kind = "da" if item["ten"].endswith("Da") else "nen"
    corr = {"What": "Correction", "CorrectionAmount": 1, "CorrectionActive": True, "CorrectionName": item["ten"],
            "CorrectionSyncID": uuid.uuid4().hex.upper(),
            "LocalExposure2012": item["ev"] / 4, "LocalTemperature": item["temp"] / 100,
            "LocalTint": item["tint"] / 100, "LocalSaturation": item["sat"] / 100, "LocalCurveRefineSaturation": 100}
    for k in xmp._LOCAL_ZERO:
        corr.setdefault(k, 0)
    corr["CorrectionMasks"] = [{"What": "Mask/Image", "MaskActive": True, "MaskName": name, "MaskBlendMode": 0,
                                "MaskInverted": False, "MaskSyncID": uuid.uuid4().hex.upper(), "MaskValue": 1,
                                "MaskVersion": 1, "MaskSubType": int(sub), "MaskSubCategoryID": int(cat),
                                "ReferencePoint": "0.500000 0.500000"} for name, sub, cat in xmp._MASKS[kind]]
    return corr


def to_lr(vals: dict, curve) -> dict:
    """Thông số kiểu .xmp (Camera Raw) -> bảng thông số cho photo:applyDevelopSettings của Lightroom.
    Không đổi profile / ống kính / process version của ảnh (Lightroom giữ của bạn)."""
    out: dict = {}
    for k, v in vals.items():
        if k.startswith("_") or k in _LR_SKIP:
            continue
        out[k] = v if isinstance(v, str) else float(v)
    if curve:
        out["ToneCurveName2012"] = "Custom"
        out["ToneCurvePV2012"] = [int(c) for xy in curve for c in xy]      # Lightroom: x1, y1, x2, y2...
    local = vals.get("_local") or []
    if local:
        out["Editer_mask"] = [_lr_mask(it) for it in local]        # plugin gộp với mask sẵn có của ảnh
    return out


class RenderBridge:
    """Nhờ plugin Lightroom dựng ảnh thật: ghi yeu-cau-N.json -> plugin áp thông số vào ảnh, xuất JPEG nhỏ,
    ghi xong-N.json (đường dẫn ảnh + As Shot thật của Lightroom) -> đọc ảnh để đo."""

    def __init__(self, workdir: Path, index: dict, log=print, timeout: float = 300.0, job_id=None):
        self.dir, self.index, self.log, self.timeout, self.n = Path(workdir), index, log, timeout, 0
        self.job_id = job_id
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in list(self.dir.glob("yeu-cau-*.json")) + list(self.dir.glob("xong-*.json")):
            try:
                f.unlink()
            except OSError:
                pass

    def render(self, path: str, settings: dict, size: int):
        import time

        import numpy as np
        from PIL import Image
        self.n += 1
        req = {"n": self.n, "i": self.index[str(path)], "thong_so": settings, "co_anh": int(size),
               "viec_id": self.job_id}
        tmp = self.dir / f"yeu-cau-{self.n}.json.tmp"
        tmp.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / f"yeu-cau-{self.n}.json")
        done = self.dir / f"xong-{self.n}.json"
        t0 = time.time()
        while not done.exists():
            if time.time() - t0 > self.timeout:
                raise RuntimeError("Lightroom không trả ảnh dựng thử (quá thời gian chờ)")
            time.sleep(0.1)
        resp, err, raw = None, None, b""
        for _ in range(50):                      # file vừa xuất hiện có thể đang ghi dở
            try:
                raw = done.read_bytes()
                resp = json.loads(raw.decode("utf-8-sig"))
                break
            except (OSError, ValueError) as e:   # UnicodeDecodeError cũng là ValueError
                err = e
                time.sleep(0.1)
        if not isinstance(resp, dict):
            # nói rõ lý do + đầu file để sửa được (trước đây chỉ báo "không đọc được")
            raise RuntimeError(f"không đọc được kết quả dựng thử của Lightroom ({done.name}: {err or 'không phải JSON'};"
                               f" {len(raw)} byte, đầu file {raw[:160]!r})")
        if not resp.get("ok"):
            raise RuntimeError(resp.get("loi") or "Lightroom không dựng được ảnh")
        with Image.open(resp["anh"]) as im:
            img = np.asarray(im.convert("RGB"))
        try:
            Path(resp["anh"]).unlink()
        except OSError:
            pass
        return img, resp

    def factory(self, size: int):
        from . import cli

        def make(rep_photos):
            def render(i, lk, shot):
                vals, crv, _ = cli._compose_jpg(None, rep_photos[i], lk, shot, True)
                img, _ = self.render(rep_photos[i].path, to_lr(vals, crv), size)
                # KHÔNG lấy As Shot từ Lightroom: sau khi đặt "As Shot", getDevelopSettings vẫn trả nhiệt độ CŨ
                # của ảnh (lỗi thật ở 0.12.1: -37 mired) -> dùng As Shot tool tính (đã khớp Camera Raw).
                return img
            render.as_shot = {}
            render.probe_as_shot = True       # đo As Shot thật bằng ảnh dựng thêm (look.probe_as_shot)
            render.ten = "Lightroom"
            return render
        return make


def run_copy(job: dict, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    """Chép màu từ ảnh JPG mẫu cho các ảnh Lightroom gửi; có thư mục làm việc thì Lightroom dựng thử ảnh đại
    diện để đo và sửa (như Camera Raw qua Photoshop), không thì ước lượng mô phỏng."""
    import argparse

    from . import cli, look, raw
    items = _items(job)
    jpgs = [p for p in (job.get("mau_jpg") or []) if Path(p).is_file()]
    if not jpgs:
        raise SystemExit("Chưa chọn ảnh JPG mẫu (hoặc không mở được file ảnh mẫu).")
    paths = [it["path"] for it in items]
    log(f"Lightroom: chép màu từ {len(jpgs)} ảnh mẫu cho {len(paths)} ảnh")
    log(f"Đọc {len(paths)} ảnh...")
    loaded = style.load_many(paths, workers, log)
    keep = [i for i, p in enumerate(loaded) if not p.error and p.preview is not None]
    out = [{"i": i, "loi": loaded[i].error or "không đọc được ảnh"} for i in range(len(paths)) if i not in set(keep)]
    if not keep:
        raise SystemExit("Không đọc được file RAW nào — ổ đĩa chưa cắm / file đã chuyển chỗ?")
    photos = [loaded[i] for i in keep]
    for line in raw.wb_summary(photos):
        log(line)
    emb_model, embs = None, None
    if use_ai:
        from . import ai
        emb_model = ai.Embedder.get()
        if emb_model is not None:
            log(f"AI đang xem {len(photos)} ảnh ({emb_model.device})...")
            embs = emb_model.embed([p.preview for p in photos])
    scenes = style.group_scenes(photos, 4.0, embs)
    a = argparse.Namespace(khong_ai=not use_ai, vung_tat_ca=False, mau_jpg=jpgs, photoshop=False,
                           co_anh=int(job.get("co_anh") or 1024), khong_mask=True,
                           so_anh_dai_dien=int(job.get("so_anh_dai_dien") or 0), so_luot=int(job.get("so_luot") or 4),
                           do_manh=float(job.get("do_manh") or 1.0), luu_preset=None)
    factory = None
    if job.get("lam_viec"):
        bridge = RenderBridge(Path(job["lam_viec"]), {str(paths[i]): i for i in keep}, log,
                              job_id=job.get("viec_id"))
        factory = bridge.factory(a.co_anh)
        log("Lightroom sẽ dựng thử ảnh đại diện để đo màu thật và sửa dần.")
    try:
        vals, curves, _tpls, info = cli._copy_color(a, None, None, photos, scenes, embs, emb_model, factory=factory,
                                                    ten="Lightroom")
    except RuntimeError as e:
        if factory is None:
            raise
        log(f"Lightroom không dựng thử được ({e}) — dùng ước lượng mô phỏng.")
        vals, curves, _tpls, info = cli._copy_color(a, None, None, photos, scenes, embs, emb_model, ten="Lightroom")
    out += [{"i": keep[j], "thong_so": to_lr(v, c)} for j, (v, c) in enumerate(zip(vals, curves))]
    measured = bool(info.get("camera_raw"))
    rep = info.get("camera_raw") if measured else info.get("mo_phong")
    by_path = {str(paths[i]): i for i in keep}
    return {"ok": True, "phien_ban": __version__, "anh": out, "do_tren_lightroom": measured,
            "bao_cao": look.fmt_report(rep) if rep else "", "mask_ai": info.get("mask_ai"),
            "giu_goc": bool(info.get("giu_goc")),
            "dai_dien": [by_path[str(t)] for t in info.get("tep_dai_dien", []) if str(t) in by_path]}


OUT_DIR = "Editer chep mau"     # ảnh chép màu kiểu Evoto: thư mục con cạnh ảnh gốc


def run_copy_pixels(job: dict, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    """Chép màu kiểu Evoto, 2 bước để CẢ LÔ cùng một màu:
    - buoc "phan-tich": ảnh nhỏ Lightroom xuất của MỌI ảnh -> chia cảnh, gộp các ảnh mẫu thành một "gu", mỗi cảnh
      một bộ màu (từng ảnh chỉ cân lại độ sáng) -> ghi kế hoạch vào file `ke_hoach`;
    - buoc "ap": ảnh đầy đủ (từng đợt) -> áp đúng kế hoạch -> ảnh mới ở "Editer chep mau" cạnh ảnh gốc;
    - không có buoc: làm cả hai trên chính các ảnh được đưa vào.
    Ghi tiến độ cho plugin; có file `huy` thì dừng."""
    from . import mauanh
    items = list(job.get("anh") or [])
    buoc = job.get("buoc") or ""
    refs = [str(p) for p in (job.get("mau_jpg") or [])]
    if buoc != "ap" and not refs:
        return {"ok": False, "loi": "Chưa chọn ảnh mẫu."}
    if not items:
        return {"ok": False, "loi": "Không có ảnh nào để chép màu."}
    strength = max(0.0, min(1.0, float(job.get("do_manh", 100)) / 100.0))
    quality = int(job.get("chat_luong", 95))
    prog, huy = job.get("tien_do"), job.get("huy")

    def progress(k, n):
        if prog:
            mauanh.write_json(prog, {"xong": k, "tong": n, "viec_id": job.get("viec_id")})

    def cancel():
        return bool(huy) and Path(huy).exists()

    plans = None
    if buoc in ("phan-tich", ""):
        log(f"Lightroom: chép màu kiểu Evoto từ {len(refs)} ảnh mẫu cho {len(items)} ảnh (độ mạnh {strength:.0%})")
        segment, emb = mauanh._ai(use_ai, log)
        ref_ms = mauanh.load_refs(refs, segment, emb if len(refs) > 1 else None, log)
        photos = mauanh.load_photos([(it.get("i"), it["vao"], it.get("thoi_gian")) for it in items], segment, emb,
                                    log, progress if buoc else None, cancel)
        plans = mauanh.analyze(photos, ref_ms, strength, log)
        if buoc == "phan-tich":
            if job.get("ke_hoach"):
                mauanh.save_plan(job["ke_hoach"], plans)
            missing = [it.get("i") for it in items if it.get("i") not in plans]
            return {"ok": True, "phien_ban": __version__, "so_anh": len(plans), "thieu": missing,
                    "dung_giua_chung": bool(cancel())}
    if plans is None:
        plans = mauanh.load_plan(job["ke_hoach"])
    work, bad = [], []
    taken: set = set()
    for it in items:
        try:   # một ảnh không tạo được thư mục ra (ổ chỉ đọc, đường dẫn lạ...) không làm hỏng cả lô
            folder = Path(it.get("thu_muc") or Path(it["vao"]).parent)
            folder.mkdir(parents=True, exist_ok=True)
            dst = mauanh.out_name(folder, Path(it.get("goc") or it["vao"]), taken)
        except OSError as e:
            bad.append({"i": it.get("i"), "loi": f"không tạo được thư mục ảnh ra: {e}"})
            log(f"  {Path(str(it.get('goc') or it.get('vao'))).name}: LỖI không tạo được thư mục ảnh ra ({e})")
            continue
        taken.add(str(dst).lower())
        work.append((it, dst))
    res = mauanh.apply([(it.get("i"), it["vao"], dst) for it, dst in work], plans, quality, log, progress, cancel)
    out = bad + [{"i": it.get("i"), **r} for (it, _), r in zip(work, res)]
    n_ok = sum(1 for r in res if "ra" in r)
    stopped = len(res) < len(work)
    log(f"XONG: {n_ok} ảnh mới" + (f", {len(out) - n_ok} ảnh lỗi" if len(out) > n_ok else "")
        + (" (đã dừng giữa chừng)" if stopped else ""))
    return {"ok": True, "phien_ban": __version__, "anh": out, "so_anh": n_ok, "dung_giua_chung": stopped}


def run_job(kind: str, job_path: str, out_path: str, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    """Đọc job.json, chạy, ghi out.json. Lỗi cũng ghi vào out.json để plugin báo cho bạn."""
    out = Path(out_path)
    job: dict = {}
    try:
        job = json.loads(Path(job_path).read_text(encoding="utf-8-sig"))
        use_ai = use_ai and job.get("ai", True)
        fn = {"canh": run_scenes, "chep-mau": run_copy, "chep-mau-anh": run_copy_pixels}.get(kind, run_sync)
        res = fn(job, log, workers, use_ai)
    except SystemExit as e:
        res = {"ok": False, "loi": str(e.code if e.code is not None else "dừng")}
    except Exception as e:  # báo về Lightroom thay vì im lặng
        import traceback
        traceback.print_exc()
        res = {"ok": False, "loi": f"{type(e).__name__}: {e}"}
    res["viec_id"] = job.get("viec_id") if isinstance(job, dict) else None   # plugin chỉ nhận kết quả của đúng lần chạy này
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    if not res.get("ok"):
        log(f"LỖI: {res.get('loi')}")
    return res
