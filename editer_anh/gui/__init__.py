"""GIAO DIỆN cửa sổ cho Editer Ảnh (bấm, kéo thả — không cần gõ lệnh).

Dùng pywebview (trên Windows là Microsoft Edge WebView2, có sẵn trong Windows 10/11):
giao diện viết bằng HTML (index.html), phần xử lý bằng Python (api.py).
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from .. import __version__, nhatky
from .api import Api

HTML = Path(__file__).with_name("index.html")


def _bind_drop(window) -> None:
    """Kéo thả file/thư mục từ Windows vào cửa sổ -> lấy đường dẫn thật (pywebview >= 5)."""
    try:
        from webview.dom import DOMEventHandler
    except Exception:
        return

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        window.evaluate_js(f"window.onNativeDrop && window.onNativeDrop({json.dumps(paths)})")

    def noop(e):
        pass
    try:
        doc = window.dom.document
        doc.events.dragover += DOMEventHandler(noop, True, True, debounce=500)
        doc.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception as e:
        nhatky.note(f"không bật được kéo thả: {e}")


def run(selftest: str | None = None) -> None:
    """Mở cửa sổ giao diện. selftest = đường dẫn file: mở cửa sổ, ghi 'GUI OK' khi giao diện
    chạy được rồi tự đóng (dùng để kiểm tra bản build)."""
    nhatky.setup(__version__)      # bật nhật ký TRƯỚC khi nạp thư viện cửa sổ (nếu sập ở đó vẫn có ghi)
    nhatky.note("mở giao diện")
    import webview
    api = Api()
    win = webview.create_window(f"Editer Ảnh {__version__}", html=HTML.read_text(encoding="utf-8"),
                                js_api=api, width=1240, height=860, min_size=(980, 680),
                                background_color="#16171a")
    api._win = win

    def on_start(w):
        _bind_drop(w)
        if selftest:
            def check():
                ok = False
                for _ in range(60):
                    try:
                        ok = w.evaluate_js("window.__editerReady === true") is True
                    except Exception:
                        ok = False
                    if ok:
                        break
                    threading.Event().wait(1.0)
                Path(selftest).write_text("GUI OK" if ok else "GUI TIMEOUT", encoding="utf-8")
                w.destroy()
            threading.Thread(target=check, daemon=True).start()
    webview.start(on_start, win, debug="--debug" in sys.argv)


def main() -> None:
    """Điểm vào của Editer Anh.exe (cửa sổ). Lỗi thì báo bằng hộp thoại, không im lặng tắt."""
    selftest = None
    if "--tu-kiem-tra" in sys.argv:
        i = sys.argv.index("--tu-kiem-tra")
        selftest = sys.argv[i + 1] if i + 1 < len(sys.argv) else "gui-selftest.txt"
    try:
        run(selftest)
    except Exception as e:
        import traceback
        nhatky.setup(__version__)
        nhatky.note("giao diện lỗi:\n" + traceback.format_exc())
        if selftest:
            Path(selftest).write_text(f"GUI ERROR {e}", encoding="utf-8")
            return
        msg = (f"Không mở được giao diện: {e}\n\n"
               "Cách xử lý: cài 'Microsoft Edge WebView2 Runtime' (miễn phí, của Microsoft), hoặc dùng "
               "editer-anh.exe (bản menu chữ) trong cùng thư mục.\n\n"
               f"Chi tiết lỗi: {nhatky.FILE}")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Editer Ảnh", 0x10)
        else:
            print(msg)
