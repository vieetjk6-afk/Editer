"""NHẬT KÝ: ghi lại mọi thứ hiện trên màn hình vào file C:\\Users\\<tên>\\Editer\\nhat-ky.txt.

Kể cả khi chương trình bị SẬP ở tầng thấp (thư viện C/C++, driver card đồ hoạ) — lúc đó
cửa sổ tắt ngay, Python không kịp báo lỗi — `faulthandler` vẫn ghi được chương trình đang
làm gì lúc sập. Gửi file này là biết lỗi ở đâu.
"""
from __future__ import annotations

import faulthandler
import io
import platform
import sys
import time
from pathlib import Path

DIR = Path.home() / "Editer"
FILE = DIR / "nhat-ky.txt"
_fh = None


class _Tee(io.TextIOBase):
    """Vừa in ra màn hình vừa ghi vào nhật ký."""

    def __init__(self, stream, fh):
        self.stream, self.fh = stream, fh

    def write(self, s: str) -> int:
        for out in (self.stream, self.fh):
            try:
                out.write(s)
                out.flush()
            except Exception:
                pass
        return len(s)

    def flush(self) -> None:
        for out in (self.stream, self.fh):
            try:
                out.flush()
            except Exception:
                pass

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", "utf-8")

    def isatty(self) -> bool:
        try:
            return self.stream.isatty()
        except Exception:
            return False


def setup(version: str) -> Path | None:
    """Bật nhật ký (gọi một lần lúc khởi động). Lỗi gì cũng không được làm hỏng chương trình."""
    global _fh
    if _fh is not None:
        return FILE
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        if FILE.exists() and FILE.stat().st_size > 5_000_000:
            FILE.replace(FILE.with_name("nhat-ky.cu.txt"))
        _fh = open(FILE, "a", encoding="utf-8", buffering=1)
        _fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} | Editer {version} | "
                  f"{platform.platform()} | {' '.join(sys.argv[1:]) or '(menu)'}\n")
        _fh.flush()
        faulthandler.enable(file=_fh, all_threads=True)
        sys.stdout = _Tee(sys.stdout, _fh)
        sys.stderr = _Tee(sys.stderr, _fh)
        return FILE
    except Exception:
        return None


def note(msg: str) -> None:
    """Ghi riêng vào nhật ký (không in ra màn hình)."""
    if _fh is not None:
        try:
            _fh.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            _fh.flush()
        except Exception:
            pass
