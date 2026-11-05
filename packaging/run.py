"""Điểm vào cho bản .exe (PyInstaller) — chạy từ mã nguồn cũng được (plugin Lightroom gọi khi thử)."""
import multiprocessing
import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from editer_anh.cli import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
