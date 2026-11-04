"""Mở chương trình khác (Photoshop, bản Editer mới) từ bản đóng gói.

Bản đóng gói (PyInstaller) đặt vài biến môi trường và thư mục DLL riêng; chương trình con
thừa hưởng những thứ đó có thể nạp nhầm DLL của Editer hoặc tưởng mình là một phần của
Editer. Hàm `launch` dọn sạch trước khi mở.
"""
from __future__ import annotations

import os
import subprocess
import sys


def clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch(args: list[str], cwd: str | None = None) -> subprocess.Popen:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        k32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(32768)
        had = k32.GetDllDirectoryW(32768, buf)
        k32.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(args, cwd=cwd, env=clean_env())
        finally:
            if had:
                k32.SetDllDirectoryW(buf.value)
    return subprocess.Popen(args, cwd=cwd, env=clean_env())
