"""Test Editer trong Lightroom Classic.

- Phần phân tích (editer-anh.exe lr-dong-bo / lr-canh) trên ảnh DNG giả lập.
- Plugin Lua (Editer.lrplugin) chạy bằng Lua 5.1 (như Lightroom) với Lightroom giả: lấy ảnh + thông số trong
  "catalog", gọi chương trình thật, áp kết quả vào catalog; nhận ra ảnh bạn chỉnh, đồng bộ lại lần 2,
  bạn chỉnh thêm 1 ảnh đã đồng bộ -> ảnh đó thành ảnh mẫu.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from editer_anh import cli, color, lightroom, raw  # noqa: E402
from make_dng import make_dng  # noqa: E402

PLUGIN = ROOT / "lightroom" / "Editer.lrplugin"
SHOTS = [("A", 0, 5200, 0.9), ("A", 20, 5600, 0.6), ("A", 40, 5000, 1.0),          # cảnh A: ngoài trời
         ("B", 1800, 3300, 0.8), ("B", 1820, 3500, 0.5), ("B", 1840, 3200, 0.9)]  # cảnh B: trong nhà đèn vàng


def make_job(tmp: Path) -> list[Path]:
    job = tmp / "job"
    job.mkdir()
    out = []
    for i, (_, t, temp, ev) in enumerate(SHOTS):
        p = job / f"IMG_{i:04d}.dng"
        make_dng(p, temp, 4, seed=70 + i, exposure=ev)
        os.utime(p, (1_700_000_000 + t,) * 2)
        out.append(p)
    return out


def lr_settings(temp=None, tint=None, ev=0.0, vib=0, edited=True) -> dict:
    """Thông số như photo:getDevelopSettings() của Lightroom (số là số, không phải chuỗi)."""
    s = {"ProcessVersion": "15.4", "WhiteBalance": "As Shot", "Temperature": 5000 + (temp or 0) % 7, "Tint": 3,
         "Exposure2012": 0, "Contrast2012": 0, "Vibrance": 0, "Saturation": 0, "Sharpness": 40,
         "ToneCurveName2012": "Linear", "CameraProfile": "Adobe Standard", "LensProfileEnable": 1,
         "LensProfileSetup": "LensDefaults", "LensProfileName": f"Adobe (Lens {temp})", "CropTop": 0, "CropLeft": 0,
         "CropBottom": 1, "CropRight": 1, "HasCrop": False, "EnableMaskGroupBasedCorrections": True}
    if edited:
        s.update({"WhiteBalance": "Custom", "Temperature": temp, "Tint": tint, "Exposure2012": ev, "Vibrance": vib,
                  "HueAdjustmentOrange": -6, "ToneCurveName2012": "Custom",
                  "ToneCurvePV2012": [0, 18, 128, 132, 255, 250], "CropTop": 0.1, "HasCrop": True,
                  "LensProfileSetup": "Custom", "PerspectiveVertical": -12, "RetouchAreas": [{"SpotType": "heal"}],
                  "MaskGroupBasedCorrections": [
                      {"What": "Correction", "CorrectionName": "Da", "LocalExposure2012": 0.05,
                       "CorrectionMasks": [{"What": "Mask/Image", "MaskSubType": 3, "MaskSubCategoryID": 2,
                                            "InputDigest": "ABC"}]},
                      {"What": "Correction", "CorrectionName": "Co to tay", "LocalExposure2012": -0.4,
                       "CorrectionMasks": [{"What": "Mask/Paint"}]},
                      {"What": "Correction", "CorrectionName": "Sang mat",
                       "CorrectionMasks": [{"What": "Mask/CircularGradient"}]}]})
    return s


def test_helper(tmp: Path, paths: list[Path]) -> None:
    job = {"ai": False, "anh": [{"path": str(p), "mau": i in (0, 4)} for i, p in enumerate(paths)]}
    job["anh"][0]["thong_so"] = {"Exposure2012": 0.35, "WhiteBalance": "Custom", "Temperature": 5900, "Tint": 10}
    job["anh"][4]["thong_so"] = {"Exposure2012": 0.6, "WhiteBalance": "As Shot", "Temperature": 3450, "Tint": 2}
    jp, op = tmp / "viec.json", tmp / "ket-qua.json"
    jp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    res = cli.main(["lr-dong-bo", str(jp), str(op), "--khong-ai"])
    out = json.loads(op.read_text(encoding="utf-8"))
    assert out["ok"] and res["ok"] and out["so_mau"] == 2 and len(out["anh"]) == 4, out
    as_shot = {i: raw.load(p).as_shot_temp for i, p in enumerate(paths)}
    for d in out["anh"]:
        i = d["i"]
        want_r = 0 if SHOTS[i][0] == "A" else 4
        assert d["r"] == want_r and d["cung_canh"], d
        if want_r == 4:
            assert d["as_shot"] and "Temperature" not in d, d                # ảnh mẫu As Shot -> As Shot
        else:
            dm = color.mired(as_shot[i]) - color.mired(as_shot[0])
            want = color.mired(5900) + (abs(dm) - 25) * (1 if dm > 0 else -1) * (abs(dm) > 25)
            assert abs(color.mired(d["Temperature"]) - want) < 1.5 and abs(d["Tint"] - 10) <= 1, d
    # lỗi -> vẫn ghi ket-qua.json để plugin báo cho bạn
    jp.write_text(json.dumps({"anh": [{"path": str(p)} for p in paths]}), encoding="utf-8")
    try:
        cli.main(["lr-dong-bo", str(jp), str(op), "--khong-ai"])
        raise AssertionError("phải báo lỗi khi không có ảnh mẫu")
    except SystemExit:
        pass
    err = json.loads(op.read_text(encoding="utf-8"))
    assert not err["ok"] and "chưa có ảnh nào bạn đã chỉnh" in err["loi"].lower(), err
    jp.write_text(json.dumps({"ai": False, "anh": [{"path": str(p)} for p in paths]}), encoding="utf-8")
    sc = cli.main(["lr-canh", str(jp), str(op), "--khong-ai"])
    assert sc["ok"] and len(sc["canh"]) == 2 and [c["anh"] for c in sc["canh"]] == [[0, 1, 2], [3, 4, 5]], sc
    assert [c["mau"] for c in sc["canh"]] == [1, 4]
    print("[lightroom] phần phân tích (lr-dong-bo / lr-canh): OK")


def lua_runtime():
    from lupa import lua51
    return lua51.LuaRuntime(unpack_returned_tuples=True)


def test_core() -> None:
    lua = lua_runtime()
    lua.execute(f"package.path = '{PLUGIN.as_posix()}/?.lua;' .. package.path")
    core = lua.eval("require('EditerCore')")
    # JSON qua lại với Python, có tiếng Việt, \\u, emoji (cặp surrogate)
    data = {"a": [1, 2.5, -3, True, "cô dâu \"chú rể\"\n"], "b": {"x": "Ảnh 😀"}, "c": [], "n": None}
    for txt in (json.dumps(data, ensure_ascii=False), json.dumps(data, ensure_ascii=True)):
        back = json.loads(core.encode(core.decode(txt)))
        assert back["a"] == data["a"] and back["b"] == data["b"] and back["c"] == [] and "n" not in back, back
    fp = core.fingerprint
    base = {"WhiteBalance": "As Shot", "Temperature": 5100, "Tint": 3, "Vibrance": 0, "CropTop": 0,
            "LensProfileName": "A", "CameraProfileDigest": "X"}

    def tab(d):
        return lua.eval("function(s) return require('EditerCore').decode(s) end")(json.dumps(d))
    f0 = fp(tab(base))
    assert f0 == fp(tab({**base, "Temperature": 5600, "Tint": -4})), "As Shot: nhiệt độ máy đo khác nhau vẫn là 1 nền"
    assert f0 == fp(tab({**base, "CropTop": 0.2, "LensProfileName": "B", "CameraProfileDigest": "Y"}))
    assert f0 != fp(tab({**base, "Vibrance": 10}))
    custom = {**base, "WhiteBalance": "Custom"}
    assert fp(tab(custom)) != fp(tab({**custom, "Temperature": 5600}))
    he = core.hasEdits
    assert not he(tab(base)) and he(tab({**base, "Vibrance": 12})) and he(tab({**base, "CameraProfile": "Camera Standard"}))
    assert he(tab({**base, "MaskGroupBasedCorrections": [{"What": "Correction"}]})) and not he(tab({**base, "Sharpness": 60}))

    # ảnh nào bạn chỉnh: nền = preset lúc import (giống hệt nhau), 2 ảnh khác nền = ảnh mẫu
    preset = {**base, "Vibrance": 8, "Contrast2012": 10}
    items = [{"id": str(i), "settings": preset if i not in (3, 7) else {**preset, "Exposure2012": 0.4}}
             for i in range(10)]
    for it in items:
        it["fp"] = fp(tab(it["settings"]))
    roles, baseline = core.detectRoles(tab(items), tab({}))
    assert baseline == items[0]["fp"]
    assert [roles[i + 1] for i in range(10)] == ["mau" if i in (3, 7) else "dongbo" for i in range(10)]
    # có chụp lại ở bước 1: đổi so với lúc đó = ảnh mẫu; tool đã đồng bộ và chưa đụng lại = đồng bộ lại
    st = {"snap": {it["id"]: it["fp"] for it in items}, "sync": {}}
    st["snap"]["7"] = "khac"
    roles, _ = core.detectRoles(tab(items), tab(st))
    assert roles[8] == "mau" and roles[4] == "dongbo", dict(roles)
    st = {"snap": {}, "sync": {"3": items[3]["fp"]}}
    assert core.detectRoles(tab(items), tab(st))[0][4] == "dongbo"

    # mang sang ảnh khác: bỏ khung cắt / phối cảnh / xoá vết / ống kính / mask vị trí; mask AI giữ, bỏ mã kiểm tra
    ref = tab(lr_settings(5900, 10, 0.35, 12))
    s, skipped, ai = core.forSync(ref, tab({"sameCamera": False, "sameLens": False, "asShot": False}))
    d = json.loads(core.encode(s))
    for k in ("CropTop", "HasCrop", "PerspectiveVertical", "RetouchAreas", "LensProfileName", "CameraProfileDigest"):
        assert k not in d, k
    assert d["LensProfileSetup"] == "LensDefaults" and d["HueAdjustmentOrange"] == -6 and ai == 1
    masks = d["MaskGroupBasedCorrections"]
    assert [m["CorrectionName"] for m in masks] == ["Da"] and "InputDigest" not in json.dumps(masks)
    sk = list(skipped.values())
    assert any("Co to tay" in x and "cọ tô" in x for x in sk) and any("Sang mat" in x for x in sk), sk
    s2, _, _ = core.forSync(ref, tab({"sameCamera": True, "sameLens": True, "asShot": True}))
    d2 = json.loads(core.encode(s2))
    assert "Temperature" not in d2 and d2["LensProfileSetup"] == "Custom" and d2["LensProfileName"]
    win = core.commandLine(tab(["C:\\Editer\\editer-anh.exe", "lr-dong-bo", "C:\\a b\\viec.json"]), True)
    assert win == '""C:\\Editer\\editer-anh.exe" "lr-dong-bo" "C:\\a b\\viec.json""', win
    print("[lightroom] plugin: JSON, dấu vân tay, nhận ảnh mẫu, thông số mang sang: OK")


def test_plugin(tmp: Path, paths: list[Path]) -> None:
    """Chạy plugin (Lua 5.1) trong Lightroom giả, gọi chương trình thật."""
    home = Path(os.environ["HOME"])
    reg = lightroom.register()                         # ghi lightroom.json: plugin biết gọi chương trình ở đâu
    assert (home / "Editer" / "lightroom.json").is_file() and not reg["da_cai"]
    lua = lua_runtime()
    calls = []

    def py_execute(cmd):
        calls.append(cmd)
        return subprocess.run(cmd, shell=True, env={**os.environ, "PYTHONUTF8": "1"}).returncode
    g = lua.globals()
    g.FAKE_HOME = str(home)
    g.py_execute = py_execute
    g._PLUGIN = lua.table_from({"path": str(PLUGIN)})
    lua.execute(f"package.path = '{PLUGIN.as_posix()}/?.lua;{(ROOT / 'tests').as_posix()}/?.lua;' .. package.path")
    fake = lua.eval("require('lr_fake_sdk')")
    tab = lua.eval("function(s) return require('EditerCore').decode(s) end")
    refs = {0: lr_settings(5900, 10, 0.35, 12), 4: lr_settings(3900, 6, 0.6, 30)}
    photos = []
    for i, p in enumerate(paths):
        st = refs.get(i) or lr_settings(temp=i * 977, edited=False)
        photos.append(fake.photo(100 + i, str(p), tab(json.dumps(st))))
    photos.append(fake.photo(200, str(tmp / "clip.mp4"), tab(json.dumps(lr_settings(edited=False))), "VIDEO"))
    lua.eval("function(c, ps) for i, p in ipairs(ps) do c.photos[i] = p end end")(fake.catalog, lua.table_from(photos))

    def settings(i):
        return json.loads(lua.eval("function(p) return require('EditerCore').encode(p._settings) end")(photos[i]))

    lua.execute("require('EditerLR').dongBo()")
    msgs = [m.msg for m in fake.messages.values()]
    assert msgs and "Đã đồng bộ 4 ảnh theo 2 ảnh" in msgs[-1], msgs
    assert "Bỏ qua 1 ảnh không phải RAW" in msgs[-1] and "Co to tay" in msgs[-1], msgs[-1]
    assert "Update AI Settings" in msgs[-1], "mask AI: chưa tính ngay thì nhắc cách cập nhật"
    assert list(fake.writes.values()).count("Editer: đồng bộ theo ảnh bạn chỉnh") == 1, "1 lần ghi = 1 bước Undo"
    as_shot = {i: raw.load(p).as_shot_temp for i, p in enumerate(paths)}
    for i in (1, 2, 3, 5):
        s = settings(i)
        r = 0 if SHOTS[i][0] == "A" else 4
        assert s["Vibrance"] == refs[r]["Vibrance"] and s["HueAdjustmentOrange"] == -6, (i, s["Vibrance"])
        assert s["ToneCurvePV2012"] == [0, 18, 128, 132, 255, 250] and s["ToneCurveName2012"] == "Custom"
        assert s["CropTop"] == 0 and not s["HasCrop"] and "PerspectiveVertical" not in s and "RetouchAreas" not in s
        assert s["LensProfileName"] == f"Adobe (Lens {i * 977})", "ống kính của chính ảnh đó giữ nguyên"
        assert s["LensProfileSetup"] == "LensDefaults"
        assert [m["CorrectionName"] for m in s["MaskGroupBasedCorrections"]] == ["Da"]
        dm = color.mired(as_shot[i]) - color.mired(as_shot[r])
        want = color.mired(refs[r]["Temperature"]) + (abs(dm) - 25) * (1 if dm > 0 else -1) * (abs(dm) > 25)
        assert s["WhiteBalance"] == "Custom" and abs(color.mired(s["Temperature"]) - want) < 1.5, (i, s["Temperature"])
        assert photos[i].history == "Editer: đồng bộ"
    for i in refs:
        assert settings(i) == refs[i], "không đụng ảnh bạn đã chỉnh"
    state = json.loads((home / "Editer" / "lightroom-trang-thai.json").read_text(encoding="utf-8"))
    assert len(state["/tmp/editer-test.lrcat"]["sync"]) == 4

    # Lần 2: ảnh tool đã đồng bộ (chưa đụng lại) vẫn là ảnh cần đồng bộ, không thành ảnh mẫu
    lua.execute("require('EditerLR').dongBo()")
    assert "Đã đồng bộ 4 ảnh theo 2 ảnh" in [m.msg for m in fake.messages.values()][-1]
    # Bạn chỉnh thêm ảnh 1 (đã đồng bộ) -> thành ảnh mẫu; ảnh 2 (gần giờ chụp hơn) theo ảnh 1
    lua.eval("function(p) p._settings.Vibrance = 55 end")(photos[1])
    fake.confirm_answer = "ok"                          # lần này bấm "Tính ngay" mask AI
    lua.execute("require('EditerLR').dongBo()")
    last = [m.msg for m in fake.messages.values()][-1]
    assert "Đã đồng bộ 3 ảnh theo 3 ảnh" in last and settings(2)["Vibrance"] == 55, last
    assert settings(1)["Vibrance"] == 55 and photos[2].ai_updates == 1 and "Đã tính lại mask AI" in last

    # Bước 1: chia cảnh, mỗi cảnh 1 ảnh vào bộ sưu tập, ghi lại thông số lúc đó
    lua.execute("require('EditerLR').chonAnhMau()")
    coll = fake.collections["Editer – ảnh mẫu cần chỉnh"]
    names = sorted(Path(p._path).name for p in coll._photos.values())
    assert names == ["IMG_0001.dng", "IMG_0004.dng"], names
    last = [m.msg for m in fake.messages.values()][-1]
    assert fake.active is not None and "Cảnh nào (2 cảnh) cũng đã có ảnh bạn chỉnh" in last, last
    snap = json.loads((home / "Editer" / "lightroom-trang-thai.json").read_text(encoding="utf-8"))
    assert sorted(snap["/tmp/editer-test.lrcat"]["snap"]) == ["102", "103", "105"], "ảnh bạn đã chỉnh không bị coi là ảnh chưa chỉnh"
    # job mới (chưa chỉnh ảnh nào): mỗi cảnh 1 ảnh giữa cảnh
    for i in range(6):
        lua.eval("function(p, s) p._settings = s end")(photos[i], tab(json.dumps(lr_settings(temp=i * 977, edited=False))))
    lua.execute("require('EditerLR').chonAnhMau()")
    names = sorted(Path(p._path).name for p in coll._photos.values())
    assert names == ["IMG_0001.dng", "IMG_0004.dng"] and "2 cảnh chưa có ảnh bạn chỉnh" in [m.msg for m in fake.messages.values()][-1], names
    # bạn chỉnh ảnh 2 và 3 sau bước 1 -> bước 2 nhận ra đúng 2 ảnh đó là ảnh mẫu
    lua.eval("function(p) p._settings.Vibrance = 20; p._settings.WhiteBalance = 'Custom' end")(photos[2])
    lua.eval("function(p) p._settings.Exposure2012 = 0.7 end")(photos[3])
    lua.execute("require('EditerLR').dongBo()")
    assert "Đã đồng bộ 4 ảnh theo 2 ảnh" in [m.msg for m in fake.messages.values()][-1]
    assert settings(0)["Vibrance"] == 20 and settings(5)["Exposure2012"] != 0
    assert all("lr-dong-bo" in c or "lr-canh" in c for c in calls) and len(calls) == 6
    print("[lightroom] plugin trong Lightroom giả: đồng bộ, lần 2, chỉnh thêm 1 ảnh, bước 1 chọn ảnh mẫu: OK")


def test_install(tmp: Path) -> None:
    os.environ["APPDATA"] = str(tmp / "appdata")
    lightroom.modules_dir().parent.mkdir(parents=True)          # có Lightroom
    st = lightroom.status()
    assert st["co_lightroom"] and not st["da_cai"]
    res = lightroom.install(lambda m: None)
    dst = lightroom.installed_dir()
    assert res["da_cai"] and (dst / "Info.lua").is_file() and (dst / "EditerLR.lua").is_file()
    assert (dst / "phien-ban.txt").read_text(encoding="utf-8") == lightroom.__version__
    (dst / "phien-ban.txt").write_text("0.0.1", encoding="utf-8")   # bản cũ -> mở chương trình là tự thay
    assert lightroom.register()["cap_nhat"] and lightroom.status()["phien_ban"] == lightroom.__version__
    (dst / "phien-ban.txt").write_text("99.0.0", encoding="utf-8")  # plugin mới hơn (mở lại Editer bản cũ)
    assert not lightroom.register()["cap_nhat"] and lightroom.status()["phien_ban"] == "99.0.0", "không hạ cấp"
    cfg = json.loads(lightroom.CONFIG.read_text(encoding="utf-8"))
    lightroom.CONFIG.write_text(json.dumps({**cfg, "phien_ban": "99.0.0"}), encoding="utf-8")
    lightroom.register()
    assert json.loads(lightroom.CONFIG.read_text(encoding="utf-8"))["phien_ban"] == "99.0.0", "không trỏ về bản cũ"
    print("[lightroom] cài plugin vào thư mục Modules, tự cập nhật: OK")


def main(tmp: Path) -> None:
    paths = make_job(tmp)
    test_helper(tmp, paths)
    test_core()
    test_plugin(tmp, paths)
    test_install(tmp)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        main(Path(d))
    print("LIGHTROOM OK")
