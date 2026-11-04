"""Test cập nhật phiên bản — giả GitHub, không cần mạng; dữ liệu đã học không bị đụng tới."""
import io
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from editer_anh import capnhat, ngoai  # noqa: E402
from editer_anh.gui.api import Api  # noqa: E402


def rel(tag, assets=True, draft=False):
    return {"tag_name": tag, "draft": draft, "body": f"ghi chú {tag}",
            "assets": [{"name": capnhat.ASSET, "url": f"https://api.github.com/x/assets/{tag}", "size": 0}]
            if assets else []}


class Resp(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def test_newest():
    rs = [rel("anh-v0.4.1"), rel("anh-v0.10.0"), rel("anh-v0.9.9"), rel("mau"),
          rel("anh-v1.0.0", assets=False), rel("anh-v2.0.0", draft=True)]
    assert capnhat.newest(rs)["tag_name"] == "anh-v0.10.0", "so phiên bản theo số, bỏ bản nháp / không có file"
    assert capnhat.newest([rel("mau")]) is None


def zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("editer-anh/Editer Anh.exe", b"MZ-gia")
        z.writestr("editer-anh/editer-anh.exe", b"MZ-gia")
        z.writestr("editer-anh/_internal/a.txt", b"x")
    return buf.getvalue()


def test_flow(tmp: Path):
    capnhat.DATA = tmp / "Editer"
    style = tmp / "Editer" / "phong-cach.editer"
    style.parent.mkdir(parents=True)
    style.write_bytes(b"du-lieu-da-hoc")
    seen = []
    data = zip_bytes()

    def urlopen(req, timeout=None):
        seen.append(req)
        url = req.full_url
        if "/releases" in url:
            auth = req.get_header("Authorization")
            if auth is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if auth != "Bearer dung":
                raise urllib.error.HTTPError(url, 401, "Bad credentials", {}, None)
            return Resp(json.dumps([rel("anh-v0.1.0"), rel("anh-v99.0.0")]).encode())
        assert url == "https://objects.example/tai", url
        assert req.get_header("Authorization") is None, "không được gửi mã truy cập tới địa chỉ tải tạm"
        return Resp(data, {"Content-Length": str(len(data))})

    class Opener:
        def open(self, req, timeout=None):
            assert req.get_header("Authorization") == "Bearer dung"
            assert req.get_header("Accept") == "application/octet-stream"
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://objects.example/tai"}, None)

    launched = []
    saved = (urllib.request.urlopen, urllib.request.build_opener, ngoai.launch)
    urllib.request.urlopen = urlopen
    urllib.request.build_opener = lambda *h: Opener()
    ngoai.launch = lambda args, cwd=None: launched.append(args)
    try:
        api = Api()
        r = api.kiem_tra_cap_nhat()
        assert r["can_token"] and "mã truy cập" in r["loi"] and not r["co_token"], r
        r = api.luu_token("sai")
        assert r["can_token"] and not capnhat.config_path().exists(), "mã sai thì không lưu"
        r = api.luu_token("  dung ")
        assert r["co_ban_moi"] and r["phien_ban"] == "99.0.0" and r["co_token"], r
        assert capnhat.load_config()["github_token"] == "dung"
        assert "_release" not in r, "không gửi dữ liệu thô lên giao diện"
        assert api.kiem_tra_cap_nhat()["co_ban_moi"], "lần sau tự dùng mã đã lưu"

        assert api.mo_ban_moi()["ok"] is False, "chưa tải thì chưa mở được"
        assert api.tai_ban_moi()["ok"]
        t = time.time()
        while api.trang_thai_cap_nhat()["chay"]:
            assert time.time() - t < 30
            time.sleep(0.05)
        st = api.trang_thai_cap_nhat()
        assert st["loi"] is None, st
        exe = Path(st["exe"])
        assert exe.name == "Editer Anh.exe" and exe.read_bytes() == b"MZ-gia", exe
        assert exe.parent.parent.name == "Editer-Anh-99.0.0", exe
        assert st["tien_do"] == [len(data), len(data)], st
        assert not list((capnhat.DATA / "cap-nhat").glob("*.part")), "phải xoá file tạm"
        assert api.mo_ban_moi()["ok"] and launched == [[str(exe)]], launched
        assert style.read_bytes() == b"du-lieu-da-hoc", "dữ liệu đã học phải còn nguyên"
    finally:
        urllib.request.urlopen, urllib.request.build_opener, ngoai.launch = saved
    print("[cập nhật] kiểm tra, lưu mã, tải, giải nén, mở bản mới: OK")


def test_clean_env():
    import os
    os.environ["_PYI_APPLICATION_HOME_DIR"] = "x"
    try:
        env = ngoai.clean_env()
        assert "_PYI_APPLICATION_HOME_DIR" not in env and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    finally:
        del os.environ["_PYI_APPLICATION_HOME_DIR"]


if __name__ == "__main__":
    test_newest()
    test_clean_env()
    with tempfile.TemporaryDirectory() as d:
        test_flow(Path(d))
    print("CẬP NHẬT OK")
