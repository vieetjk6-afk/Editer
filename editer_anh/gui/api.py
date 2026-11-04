"""Phần "não" của giao diện: các hàm giao diện (JavaScript) gọi được.

Việc nặng (đọc ảnh, AI, Photoshop) chạy ở luồng nền; giao diện hỏi `trang_thai()` vài lần
mỗi giây để lấy dòng nhật ký mới, tiến độ và kết quả.
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

from .. import __version__, nhatky, raw, xmp

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def thumb(arr_or_path, side: int = 240) -> str | None:
    """Ảnh -> data URL JPEG nhỏ để hiện trên giao diện."""
    try:
        if isinstance(arr_or_path, np.ndarray):
            im = Image.fromarray(arr_or_path)
        else:
            from ..look import load_ref_image
            im = Image.fromarray(load_ref_image(arr_or_path, side * 2))
        im = im.convert("RGB")
        im.thumbnail((side, side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


class _Capture(io.TextIOBase):
    """Bắt mọi dòng in ra (print/_log) để gửi lên giao diện, đồng thời giữ đường cũ (nhật ký)."""

    def __init__(self, prev, sink):
        self.prev, self.sink, self._part = prev, sink, ""

    def write(self, s: str) -> int:
        if self.prev is not None:
            try:
                self.prev.write(s)
            except Exception:
                pass
        self._part += s
        while "\n" in self._part:
            line, self._part = self._part.split("\n", 1)
            self.sink(line)
        return len(s)

    def flush(self) -> None:
        if self.prev is not None:
            try:
                self.prev.flush()
            except Exception:
                pass


class Api:
    """Mọi hàm không bắt đầu bằng "_" đều gọi được từ giao diện (window.pywebview.api.<tên>)."""

    def __init__(self):
        self._win = None
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._state = {"chay": False, "viec": None, "ket_qua": None, "loi": None,
                       "tien_do": None, "buoc": "", "bat_dau": None}

    # ------------------------------------------------------------------ thông tin
    def info(self) -> dict:
        from ..cli import STYLE_FILE
        st = {"co": STYLE_FILE.exists(), "duong_dan": str(STYLE_FILE)}
        if st["co"]:
            try:
                from .. import style
                m = style.load(STYLE_FILE)
                st.update({"n": m["n_photos"], "may": list(m["counts"]),
                           "ngay": time.strftime("%d/%m/%Y", time.localtime(STYLE_FILE.stat().st_mtime))})
            except BaseException as e:   # file cũ/hỏng: báo, không làm sập giao diện
                st.update({"co": False, "loi": str(e)})
        from ..ai import GPU_OFF
        return {"phien_ban": __version__, "phong_cach": st, "nhat_ky": str(nhatky.FILE),
                "windows": sys.platform == "win32", "gpu_tat": GPU_OFF.exists()}

    def ping(self) -> str:
        return "ok"

    def thu_lai_card(self) -> dict:
        """Bỏ đánh dấu "card đồ hoạ lỗi": lần chạy sau thử lại card — an toàn, vì thử trong
        tiến trình con (nếu card làm sập thì chỉ tiến trình con chết)."""
        from ..ai import GPU_OFF, GPU_PROBE
        for f in (GPU_OFF, GPU_PROBE):
            try:
                f.unlink()
            except OSError:
                pass
        return self.info()

    # ------------------------------------------------------------------ chọn file / thư mục
    def _dialog(self, kind: str, multiple: bool = False, types=()):
        if self._win is None:
            return []
        import webview
        fd = getattr(webview, "FileDialog", None)
        mode = {"folder": fd.FOLDER if fd else webview.FOLDER_DIALOG,
                "open": fd.OPEN if fd else webview.OPEN_DIALOG}[kind]
        res = self._win.create_file_dialog(mode, allow_multiple=multiple, file_types=types)
        return list(res or [])

    def chon_thu_muc(self) -> str | None:
        r = self._dialog("folder")
        return r[0] if r else None

    def chon_anh_mau(self) -> list[str]:
        return self._dialog("open", True, ("Ảnh (*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.webp)", "Tất cả (*.*)"))

    def chon_raw(self) -> str | None:
        exts = ";".join(f"*{e}" for e in sorted(raw.RAW_EXT))
        r = self._dialog("open", False, (f"Ảnh RAW ({exts})", "Tất cả (*.*)"))
        return r[0] if r else None

    # ------------------------------------------------------------------ xem nhanh
    def xem_thu_muc(self, path: str) -> dict:
        p = Path(path)
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            return {"ok": False, "loi": f"Không thấy thư mục: {path}"}
        raws = raw.list_raws(p)
        co_xmp = sum(1 for r in raws if xmp.find_sidecar(r) is not None)
        anh = []
        for r in raws[: 6]:
            ph = raw.load(r, 200)
            if ph.preview is not None:
                anh.append(thumb(ph.preview, 200))
        return {"ok": True, "path": str(p), "ten": p.name or str(p), "so_raw": len(raws), "so_xmp": co_xmp,
                "anh": anh}

    def xem_anh(self, paths: list[str]) -> list[dict]:
        out = []
        for s in paths or []:
            p = Path(s)
            if p.is_file() and p.suffix.lower() in IMG_EXT:
                out.append({"path": str(p), "ten": p.name, "anh": thumb(p, 240)})
        return out

    def xem_raw(self, path: str) -> dict:
        p = Path(path)
        if not (p.is_file() and p.suffix.lower() in raw.RAW_EXT):
            return {"ok": False, "loi": "Hãy chọn 1 file ảnh RAW (NEF, ARW, CR3...)."}
        ph = raw.load(p, 240)
        return {"ok": True, "path": str(p), "ten": p.name, "anh": thumb(ph.preview, 240) if ph.preview is not None else None}

    def phan_loai(self, paths: list[str]) -> dict:
        """Đường dẫn kéo thả vào cửa sổ -> thư mục / ảnh mẫu / RAW."""
        out = {"thu_muc": [], "anh": [], "raw": []}
        for s in paths or []:
            p = Path(s)
            if p.is_dir():
                out["thu_muc"].append(str(p))
            elif p.suffix.lower() in IMG_EXT:
                out["anh"].append(str(p))
            elif p.suffix.lower() in raw.RAW_EXT:
                out["raw"].append(str(p))
        return out

    def mo(self, path: str) -> None:
        """Mở thư mục / file bằng ứng dụng mặc định của Windows."""
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._push(f"Không mở được {path}: {e}")

    # ------------------------------------------------------------------ chạy việc
    def _push(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            m = re.search(r"đã đọc (\d+)/(\d+)", line)
            if m:
                self._state["tien_do"] = [int(m.group(1)), int(m.group(2))]
            elif line.strip() and not line.startswith(" " * 4):
                self._state["buoc"] = line.strip()[:160]

    def _argv(self, viec: str, o: dict) -> list[str]:
        from ..cli import STYLE_DIR, STYLE_FILE
        if viec == "chep-mau":
            a = ["chinh", o["thu_muc"], "--mau-jpg", *o["anh_mau"]]
            if o.get("photoshop"):
                a.append("--photoshop")
            if o.get("giu_chi_tiet") and STYLE_FILE.exists():
                a += ["--phong-cach", str(STYLE_FILE)]
            if o.get("ghi_de"):
                a.append("--ghi-de")
            if o.get("do_manh") is not None:
                a += ["--do-manh", f"{float(o['do_manh']):.2f}"]
            if (o.get("preset") or "").strip():
                a += ["--luu-preset", o["preset"].strip()]
            return a
        if viec == "phong-cach":
            a = ["chinh", o["thu_muc"], "--phong-cach", str(STYLE_FILE)]
            return a + (["--ghi-de"] if o.get("ghi_de") else [])
        if viec == "hoc":
            STYLE_DIR.mkdir(parents=True, exist_ok=True)
            a = ["hoc", *o["thu_muc_list"], "-o", str(STYLE_FILE)]
            return a + (["--hoc-lai"] if o.get("hoc_lai") else [])
        if viec == "kiem-tra-ps":
            return ["kiem-tra-photoshop", o["raw"]]
        raise ValueError(viec)

    def bat_dau(self, viec: str, tuy_chon: dict) -> dict:
        with self._lock:
            if self._state["chay"]:
                return {"ok": False, "loi": "Đang chạy một việc khác, chờ xong đã."}
            try:
                argv = self._argv(viec, tuy_chon or {})
            except (KeyError, ValueError) as e:
                return {"ok": False, "loi": f"Thiếu thông tin: {e}"}
            self._lines = []
            self._state.update({"chay": True, "viec": viec, "ket_qua": None, "loi": None, "tien_do": None,
                                "buoc": "Đang bắt đầu...", "bat_dau": time.time()})
        threading.Thread(target=self._run, args=(viec, argv), daemon=True).start()
        return {"ok": True}

    def _run(self, viec: str, argv: list[str]) -> None:
        from .. import cli
        old = sys.stdout
        sys.stdout = _Capture(old, self._push)
        res, err = None, None
        try:
            nhatky.note(f"giao diện: {viec} {argv}")
            res = self._clean(cli.main(argv))
        except SystemExit as e:
            err = str(e.code) if e.code not in (0, None) else None
        except BaseException as e:
            err = f"{type(e).__name__}: {e}"
            for line in traceback.format_exc().splitlines():
                self._push(line)
            self._push(f"(Chi tiết lỗi đã ghi vào nhật ký: {nhatky.FILE})")
        finally:
            sys.stdout = old
        with self._lock:
            self._state.update({"chay": False, "ket_qua": res, "loi": err})

    @staticmethod
    def _clean(res):
        """Kết quả -> dữ liệu gửi được lên giao diện (ảnh numpy -> ảnh nhỏ)."""
        if isinstance(res, dict):
            out = {}
            for k, v in res.items():
                out[k] = thumb(v, 360) if isinstance(v, np.ndarray) else Api._clean(v)
            return out
        if isinstance(res, (list, tuple)):
            return [Api._clean(v) for v in res]
        if isinstance(res, (np.floating, np.integer)):
            return res.item()
        return res

    def trang_thai(self) -> dict:
        with self._lock:
            lines, self._lines = self._lines, []
            st = dict(self._state)
        st["dong"] = lines
        st["giay"] = round(time.time() - st["bat_dau"], 0) if st.get("bat_dau") else 0
        return st
