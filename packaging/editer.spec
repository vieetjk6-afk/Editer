# -*- mode: python ; coding: utf-8 -*-
# Đóng gói 2 chương trình dùng chung thư viện (thư mục _internal):
#   "Editer Anh.exe" — giao diện cửa sổ (bấm, kéo thả)
#   "editer-anh.exe" — bản menu chữ / dòng lệnh
# Chạy:  pyinstaller --noconfirm packaging/editer.spec
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [
    (os.path.join(ROOT, "models"), "models"),
    (os.path.join(ROOT, "editer_anh", "gui", "index.html"), os.path.join("editer_anh", "gui")),
    (os.path.join(ROOT, "lightroom", "Editer.lrplugin"), os.path.join("lightroom", "Editer.lrplugin")),
]
binaries = []
hidden = ["win32com.client", "pythoncom", "pywintypes", "editer_anh.gui", "editer_anh.gui.api", "editer_anh.dongbo", "editer_anh.lightroom",
          "editer_anh.mauanh", "editer_anh.acr", "scipy.sparse.linalg", "scipy.interpolate", "PIL.ImageCms"]
for pkg in ("sklearn", "rawpy", "onnxruntime", "webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as e:  # gói không có trên máy build: bỏ qua
        print("bỏ qua", pkg, e)
        continue
    datas += d
    binaries += b
    hidden += h


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[ROOT], binaries=binaries, datas=datas,
                    hiddenimports=hidden, hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)


a_cli = analysis("run.py")
a_gui = analysis("gui_run.py")
exe_cli = EXE(PYZ(a_cli.pure), a_cli.scripts, [], exclude_binaries=True, name="editer-anh",
              console=True, upx=False)
exe_gui = EXE(PYZ(a_gui.pure), a_gui.scripts, [], exclude_binaries=True, name="Editer Anh",
              console=False, upx=False)
coll = COLLECT(exe_cli, exe_gui, a_cli.binaries, a_cli.datas, a_gui.binaries, a_gui.datas,
               strip=False, upx=False, name="editer-anh")
