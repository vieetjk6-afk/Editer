"""CẬP NHẬT PHIÊN BẢN MỚI — không mất dữ liệu đã học.

- Dữ liệu (phong cách đã học + bản sao lưu, nhật ký, cấu hình) nằm ở C:\\Users\\<tên>\\Editer —
  KHÔNG nằm trong thư mục chương trình, nên bản mới đọc lại được ngay.
- Bản mới được tải từ mục Releases của repo GitHub và giải nén ra thư mục RIÊNG cạnh bản cũ
  (không ghi đè bản đang chạy). Bản cũ để nguyên, xoá khi không cần nữa.
- Repo đang để riêng tư nên cần "mã truy cập GitHub" (token, chỉ cần quyền ĐỌC) — lưu trong
  C:\\Users\\<tên>\\Editer\\cau-hinh.json, chỉ trên máy bạn.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import __version__, nhatky

REPO = "vieetjk6-afk/Editer"
ASSET = "Editer-Anh-Windows.zip"
API = "https://api.github.com"
DATA = Path.home() / "Editer"


def config_path() -> Path:
    return DATA / "cau-hinh.json"


def ver(tag: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_token(token: str) -> None:
    cfg = load_config()
    cfg["github_token"] = token.strip()
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def _req(url: str, token: str | None, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    h = {"Accept": accept, "User-Agent": f"Editer/{__version__}", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=h)


def newest(releases: list[dict]) -> dict | None:
    """Bản Editer Ảnh mới nhất (tag anh-vX.Y.Z) có file cài đặt."""
    cands = [r for r in releases if str(r.get("tag_name", "")).startswith("anh-v") and not r.get("draft")
             and any(a.get("name") == ASSET for a in r.get("assets", []))]
    return max(cands, key=lambda r: ver(r["tag_name"]), default=None)


def check(token: str | None = None) -> dict:
    """-> {co_ban_moi, phien_ban, hien_tai, ghi_chu, _release} hoặc {can_token: True, loi} / {loi}."""
    token = token if token is not None else load_config().get("github_token")
    try:
        with urllib.request.urlopen(_req(f"{API}/repos/{REPO}/releases?per_page=30", token), timeout=30) as r:
            rel = newest(json.load(r))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            if not token:
                msg = "Repo Editer để riêng tư: cần mã truy cập GitHub (làm 1 lần) để tải bản mới."
            elif e.code == 401:
                msg = "Mã truy cập không đúng hoặc đã hết hạn — tạo mã mới."
            else:
                msg = ("Mã truy cập chưa được quyền đọc repo Editer (khi tạo mã: chọn repo Editer, "
                       "quyền Contents = Read-only).")
            return {"can_token": True, "loi": msg}
        return {"loi": f"GitHub trả lỗi {e.code}"}
    except Exception as e:
        return {"loi": f"Không kết nối được GitHub: {e}"}
    if not rel:
        return {"co_ban_moi": False, "hien_tai": __version__, "phien_ban": None}
    v = ".".join(map(str, ver(rel["tag_name"])))
    return {"co_ban_moi": ver(rel["tag_name"]) > ver(__version__), "phien_ban": v, "hien_tai": __version__,
            "ghi_chu": (rel.get("body") or "")[:600], "_release": rel}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(rel: dict, token: str | None, progress=None) -> Path:
    """Tải file cài và giải nén ra thư mục riêng cạnh bản đang chạy -> đường dẫn exe mới."""
    asset = next(a for a in rel["assets"] if a["name"] == ASSET)
    v = ".".join(map(str, ver(rel["tag_name"])))
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else DATA / "ban-cai" / "hien-tai"
    dest = app_dir.parent / f"Editer-Anh-{v}"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:                      # thư mục cài không ghi được -> để trong thư mục dữ liệu
        dest = DATA / "ban-cai" / f"Editer-Anh-{v}"
        dest.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "cap-nhat" / f"{ASSET}.{v}.part"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    # Repo riêng tư: GitHub trả về địa chỉ tải tạm (chuyển hướng) — KHÔNG gửi mã truy cập tới địa chỉ đó
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(_req(asset["url"], token, "application/octet-stream"), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        resp = urllib.request.urlopen(urllib.request.Request(e.headers["Location"],
                                                             headers={"User-Agent": "Editer"}), timeout=60)
    total = int(resp.headers.get("Content-Length") or asset.get("size") or 0)
    done = 0
    with resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if total and done < total:
        raise RuntimeError(f"tải chưa đủ ({done}/{total} byte) — mạng bị ngắt, thử lại")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(dest)
    tmp.unlink(missing_ok=True)
    exe = next(dest.rglob("Editer Anh.exe"), None) or next(dest.rglob("*.exe"))
    nhatky.note(f"đã tải bản {v} vào {dest}")
    return exe
