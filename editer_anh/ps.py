"""NHỜ PHOTOSHOP (CAMERA RAW THẬT) DỰNG ẢNH ĐỂ ĐO MÀU.

Tool không có bộ dựng ảnh của Camera Raw, nên muốn biết chắc màu ra sao thì nhờ
chính Photoshop mở RAW (Camera Raw đọc file .xmp cạnh ảnh), thu nhỏ, lưu PNG sRGB.
Điều khiển Photoshop qua COM của Windows (thư viện pywin32) + ExtendScript.

Để không đụng vào file .xmp của ảnh gốc, mỗi lần dựng tool tạo một "bản sao ảo"
(hard link — không tốn dung lượng) của file RAW trong thư mục tạm .editer-tam cạnh
ảnh, ghi .xmp thử nghiệm cho bản sao đó, dựng xong thì xoá.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

_RENDER_JS = r"""
(function () {
    var d = null;
    try {
        app.displayDialogs = DialogModes.NO;
        d = app.open(new File(%(src)s));
        try { d.flatten(); } catch (e0) {}
        if (d.mode != DocumentMode.RGB) d.changeMode(ChangeMode.RGB);
        d.convertProfile("sRGB IEC61966-2.1", Intent.RELATIVECOLORIMETRIC, true, false);
        if (d.bitsPerChannel != BitsPerChannelType.EIGHT) d.bitsPerChannel = BitsPerChannelType.EIGHT;
        var w = d.width.as("px"), h = d.height.as("px"), s = %(size)d / Math.max(w, h);
        if (s < 1) d.resizeImage(UnitValue(Math.round(w * s), "px"), UnitValue(Math.round(h * s), "px"),
                                 72, ResampleMethod.BICUBIC);
        d.saveAs(new File(%(dst)s), new PNGSaveOptions(), true, Extension.LOWERCASE);
        d.close(SaveOptions.DONOTSAVECHANGES);
        return "ok";
    } catch (e) {
        try { if (d) d.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
        return "loi: " + e;
    }
})();
"""


def _js_path(p: Path) -> str:
    return json.dumps(str(Path(p).resolve()).replace("\\", "/"))


class Photoshop:
    """Kết nối Photoshop. `app` có thể truyền vào (để test không cần Photoshop)."""

    def __init__(self, app=None):
        if app is None:
            if sys.platform != "win32":
                raise RuntimeError("chỉ dùng được trên Windows")
            try:
                import pythoncom
                import win32com.client  # pywin32
            except ImportError as e:
                raise RuntimeError(f"thiếu thư viện pywin32: {e}") from e
            # Giao diện chạy việc ở luồng nền: luồng nào dùng COM (điều khiển Photoshop) cũng
            # phải khởi tạo COM trước, không thì lỗi "CoInitialize has not been called".
            pythoncom.CoInitialize()
            app = win32com.client.Dispatch("Photoshop.Application")
            try:
                app.DisplayDialogs = 3       # không hiện hộp thoại
            except Exception:
                pass
        self.app = app

    def js(self, code: str) -> str:
        return str(self.app.DoJavaScript(code))

    def version(self) -> str:
        return self.js("app.version")

    def render_file(self, src: Path, dst: Path, size: int = 1024) -> None:
        r = self.js(_RENDER_JS % {"src": _js_path(src), "dst": _js_path(dst), "size": size})
        if not r.startswith("ok"):
            raise RuntimeError(f"Photoshop không dựng được {Path(src).name}: {r}")

    def render_raw(self, raw_path: Path, xmp_content: str, workdir: Path, size: int = 1024) -> np.ndarray:
        """Dựng file RAW với nội dung .xmp cho trước -> ảnh sRGB uint8 (không đụng .xmp gốc)."""
        raw_path = Path(raw_path)
        workdir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        link = workdir / f"{raw_path.stem}__{tag}{raw_path.suffix}"
        try:
            os.link(raw_path, link)        # bản sao ảo, không tốn dung lượng (cùng ổ đĩa)
        except OSError:
            shutil.copy2(raw_path, link)
        side = link.with_suffix(".xmp")
        out = workdir / f"{raw_path.stem}__{tag}.png"
        try:
            side.write_text(xmp_content, encoding="utf-8")
            self.render_file(link, out, size)
            with Image.open(out) as im:
                return np.asarray(im.convert("RGB"))
        finally:
            for p in (link, side, out):
                try:
                    p.unlink()
                except OSError:
                    pass


def check() -> tuple[Photoshop | None, str]:
    """-> (Photoshop, mô tả) nếu kết nối được, không thì (None, lý do)."""
    try:
        ps = Photoshop()
        return ps, f"Photoshop {ps.version()}"
    except Exception as e:
        return None, str(e)


def photoshop_exe() -> str | None:
    """Đường dẫn Photoshop.exe (từ đăng ký của Windows, hoặc hỏi Photoshop đang chạy)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe") as k:
                    v, _ = winreg.QueryValueEx(k, None)
                    if v and Path(v).exists():
                        return str(v)
            except OSError:
                continue
    except ImportError:
        pass
    for base in {os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"}:
        found = sorted(Path(base, "Adobe").glob("Adobe Photoshop*/Photoshop.exe"))
        if found:
            return str(found[-1])        # bản mới nhất (tên thư mục có năm: 2024, 2025...)
    try:
        p = Path(str(Photoshop().app.Path)) / "Photoshop.exe"
        return str(p) if p.exists() else None
    except Exception:
        return None


def open_files(files: list[str], limit: int = 60) -> int:
    """Mở các ảnh RAW trong Photoshop: Camera Raw hiện ra (nhiều ảnh thì có dải ảnh bên trái)
    với thông số tool đã ghi — chỉnh tay rồi bấm Done là lưu vào .xmp."""
    from .ngoai import launch
    exe = photoshop_exe()
    if not exe:
        raise RuntimeError("không tìm thấy Photoshop trên máy")
    files = [str(f) for f in files][:limit]
    launch([exe, *files])
    return len(files)


def workdir_for(folder: Path) -> Path:
    return Path(folder) / ".editer-tam"


def cleanup(folder: Path) -> None:
    shutil.rmtree(workdir_for(folder), ignore_errors=True)
