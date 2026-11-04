"""Điểm vào cho bản .exe (PyInstaller)."""
import multiprocessing

from editer_anh.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
