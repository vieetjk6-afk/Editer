"""Điểm vào cho Editer Anh.exe (giao diện cửa sổ, không có cửa sổ dòng lệnh)."""
import multiprocessing

from editer_anh.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
