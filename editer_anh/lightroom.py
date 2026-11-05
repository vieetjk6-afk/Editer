"""Editer trong LIGHTROOM CLASSIC — chỉnh thẳng trong catalog đang mở (thấy ngay, Ctrl+Z hoàn tác được).

Plugin `lightroom/Editer.lrplugin` (Lua, chạy trong Lightroom) lấy danh sách ảnh + thông số trong catalog,
gọi chương trình này để phân tích (đọc file RAW, AI chia cảnh / tìm da, As Shot), rồi tự áp kết quả bằng
API của Lightroom — không qua file .xmp nên không cần "Read Metadata from Files".

Lệnh (plugin gọi, trao đổi bằng file JSON):
  lr-canh <job.json> <out.json>     chia cảnh, chọn 1 ảnh mỗi cảnh để bạn chỉnh làm mẫu
  lr-dong-bo <job.json> <out.json>  mỗi ảnh còn lại theo ảnh mẫu nào, Exposure, nhiệt độ màu / tint
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


def run_job(kind: str, job_path: str, out_path: str, log=print, workers: int = 8, use_ai: bool = True) -> dict:
    """Đọc job.json, chạy, ghi out.json. Lỗi cũng ghi vào out.json để plugin báo cho bạn."""
    out = Path(out_path)
    try:
        job = json.loads(Path(job_path).read_text(encoding="utf-8-sig"))
        use_ai = use_ai and job.get("ai", True)
        res = (run_scenes if kind == "canh" else run_sync)(job, log, workers, use_ai)
    except SystemExit as e:
        res = {"ok": False, "loi": str(e.code if e.code is not None else "dừng")}
    except Exception as e:  # báo về Lightroom thay vì im lặng
        import traceback
        traceback.print_exc()
        res = {"ok": False, "loi": f"{type(e).__name__}: {e}"}
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    if not res.get("ok"):
        log(f"LỖI: {res.get('loi')}")
    return res
