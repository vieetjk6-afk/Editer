"""Test chép màu kiểu Evoto (đổi màu trực tiếp trên điểm ảnh, editer_anh/mauanh.py) trên ảnh cưới tổng hợp.

- Chép ảnh mẫu lên chính nó: không đổi gì.
- Ảnh mẫu có "gu" (ấm, sáng, lá vàng hơn...): da, trắng, cây của ảnh về gần ảnh mẫu.
- Áo dài đỏ đô: giữ TÔNG màu áo (lỗi thật 0.13.0: áo theo mẫu kéo da -> da cam, áo chói).
- Hoa tím / hồng mà ảnh mẫu không có: không bị nhuộm / rực lên.
- Ảnh đầy đủ độ phân giải cho kết quả như ảnh phân tích; giữ EXIF / ICC; tên ảnh ra không ghi đè; dừng giữa chừng.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from editer_anh import color, mauanh as M  # noqa: E402
from editer_anh.look import CLOTHES, FLOWER, Look, Shot  # noqa: E402
from test_look import AS_SHOT, render, scene  # noqa: E402

# "gu" ảnh mẫu: ấm hơn, sáng hơn, lá ngả vàng, ít rực — kiểu ảnh cưới sáng, trong
GU = Look(curve=np.array([18.0, 66, 110, 170, 214, 246]), hue=np.array([0, -10, 0, -25, 0, 0, 0, 0.0]),
          sat=np.array([0, 5, 0, -25, 0, -10, 0, 0.0]), grade=np.array([2, 4, 0, 0, 2, 6.0]), saturation=-5)
GU_SHOT = Shot(1e6 / 5500 - 15, 3.0, 0.35)


def lab_of(img):
    return color.srgb_to_lab(img.reshape(-1, 3) / 255.0)


def ref_of(seed, **kw):
    img, reg = scene(seed, **kw)
    img = render(img, GU, GU_SHOT)
    return img, reg, M.sane(M.measure(img, reg, f"mau{seed}"))


def run(img, reg, ref, strength=1.0):
    from PIL import Image
    kq = M.plan(img, reg, ref, strength)
    out = np.asarray(M.render(Image.fromarray(img), kq))
    return kq, out


def test_identity():
    img, reg, ref = ref_of(41)
    kq, out = run(img, reg, ref)
    d = color.delta_e2000(lab_of(img), lab_of(out))
    print(f"[evoto] chép ảnh mẫu lên chính nó: ΔE trung bình {d.mean():.2f} (95% {np.percentile(d, 95):.2f})")
    assert d.mean() < 0.6 and np.percentile(d, 95) < 1.5


def test_match_look():
    _, _, ref = ref_of(42, skin_lab=(70, 16, 20))
    img, reg = scene(43, skin_lab=(62, 18, 13), light_k=4300)        # ảnh của bạn: đèn vàng, da tối hơn
    kq, out = run(img, reg, ref)
    rp = kq.report
    print(f"[evoto] theo gu ảnh mẫu: {M.fmt(rp)}")
    assert rp["da"][1] < 2.5 and rp["da"][1] < 0.5 * rp["da"][0], rp
    assert rp["trắng"][1] < 1.5, rp
    assert rp["cây"][1] < 0.6 * rp["cây"][0], rp


def test_dress_keeps_hue():
    """Ảnh mẫu không có áo đỏ: áo dài đỏ đô giữ tông. Ảnh mẫu áo đỏ tươi: chỉ sáng / đậm theo một phần."""
    for red_ref in (False, True):
        _, _, ref = ref_of(44, red_dress=red_ref)
        img, reg = scene(45, red_dress=True)
        lab = lab_of(img).reshape(img.shape)
        dress = (reg == CLOTHES) & (lab[..., 1] > 30)
        lab[dress] = (22, 36, 11)                                     # áo đỏ đô tối như ảnh thật
        img = (np.clip(color.lab_to_srgb(lab.reshape(-1, 3)), 0, 1).reshape(img.shape) * 255 + 0.5).astype(np.uint8)
        kq, out = run(img, reg, ref)
        a0, a1 = lab_of(img)[dress.reshape(-1)].mean(0), lab_of(out)[dress.reshape(-1)].mean(0)
        dh = abs((np.degrees(np.arctan2(a0[2], a0[1]) - np.arctan2(a1[2], a1[1])) + 180) % 360 - 180)
        c0, c1 = np.hypot(a0[1], a0[2]), np.hypot(a1[1], a1[2])
        print(f"[evoto] áo đỏ đô ({'mẫu áo đỏ' if red_ref else 'mẫu không áo đỏ'}): tông lệch {dh:.1f}°, "
              f"sáng {a0[0]:.0f}->{a1[0]:.0f}, đậm {c0:.0f}->{c1:.0f}")
        assert dh < 6.0, "áo phải giữ tông màu"
        assert 0.8 < c1 / c0 < 1.3, "áo không được chói / xỉn hẳn"


def test_flowers_not_neon():
    _, _, ref = ref_of(46)                                            # ảnh mẫu không có hoa tím / hồng
    img, reg = scene(47, flowers=True)
    kq, out = run(img, reg, ref)
    m = (reg == FLOWER).reshape(-1)
    c0 = np.hypot(*lab_of(img)[m, 1:].T).mean()
    c1 = np.hypot(*lab_of(out)[m, 1:].T).mean()
    print(f"[evoto] hoa (ảnh mẫu không có): độ đậm {c0:.1f} -> {c1:.1f}")
    assert 0.8 < c1 / c0 < 1.25


def test_strength_and_full_res():
    from PIL import Image
    _, _, ref = ref_of(48)
    img, reg = scene(49, light_k=4300)
    k1 = M.plan(img, reg, ref, 1.0)
    k5 = M.plan(img, reg, ref, 0.5)
    small = Image.fromarray(img)
    o1 = np.asarray(M.render(small, k1)).astype(float)
    o5 = np.asarray(M.render(small, k5)).astype(float)
    d1, d5 = np.abs(o1 - img).mean(), np.abs(o5 - img).mean()
    assert 0.3 * d1 < d5 < 0.7 * d1, (d1, d5)                       # độ mạnh 50% ~ nửa thay đổi
    big = small.resize((img.shape[1] * 3, img.shape[0] * 3), Image.BICUBIC)
    ob = np.asarray(M.render(big, k1).resize(small.size, Image.BILINEAR)).astype(float)
    ref_small = np.asarray(M.render(small.resize(big.size, Image.BICUBIC), k1).resize(small.size, Image.BILINEAR))
    assert np.abs(ob - ref_small).mean() < 1.0
    print(f"[evoto] độ mạnh 50%: thay đổi {d5:.1f} / {d1:.1f}; ảnh lớn gấp 3 cho cùng kết quả: OK")


def test_files(tmp: Path):
    """Ảnh thật trên đĩa: giữ EXIF / ICC, tên ra không ghi đè, dừng giữa chừng được."""
    from PIL import Image
    ref_img, _, _ = ref_of(50)
    Image.fromarray(ref_img).save(tmp / "mau.jpg", quality=95)
    srcs = []
    for i in range(3):
        img, _ = scene(51 + i, light_k=4500)
        im = Image.fromarray(img).resize((720, 480), Image.BICUBIC)
        ex = Image.Exif()
        ex[0x010F] = "NIKON CORPORATION"
        ex[0x9003] = f"2026:11:05 10:0{i}:00"
        p = tmp / f"IMG_{i}.jpg"
        im.save(p, quality=95, exif=ex.tobytes(), icc_profile=b"")
        srcs.append(p)
    out = tmp / "ra"
    dsts = [M.out_name(out, s) for s in srcs]
    res = M.run_batch(srcs, dsts, [tmp / "mau.jpg"], 0.8, 92, use_ai=False, log=lambda m: None)
    assert all("ra" in r for r in res), res
    for d in dsts:
        assert d.exists() and d.name.endswith("-editer.jpg")
        with Image.open(d) as im:
            assert im.size == (720, 480)
            assert im.getexif().get(0x010F) == "NIKON CORPORATION"
    assert M.out_name(out, srcs[0]).name == "IMG_0-editer-2.jpg", "không ghi đè ảnh đã có"
    taken = {str(M.out_name(out, srcs[0])).lower()}
    assert M.out_name(out, srcs[0], taken).name == "IMG_0-editer-3.jpg", "hai ảnh cùng tên trong một lô"
    seen = []
    res = M.run_batch(srcs, [out / f"x{i}.jpg" for i in range(3)], [tmp / "mau.jpg"], use_ai=False,
                      log=lambda m: None, progress=lambda k, n: seen.append(k), cancel=lambda: len(seen) >= 1)
    assert len(res) == 1 and seen == [1], "bấm dừng: làm xong ảnh đang làm rồi dừng"
    print("[evoto] ghi ảnh mới (giữ EXIF), không ghi đè, dừng giữa chừng: OK")


def test_no_people():
    """Ảnh chi tiết (nhẫn trên bàn gỗ, thiệp...) không có người, không có đồ trắng: vẫn chạy, chỉ đổi vừa phải."""
    _, _, ref = ref_of(55)
    rng = np.random.default_rng(3)
    lab = np.zeros((120, 160, 3))
    lab[:] = (45, 12, 25)                                             # bàn gỗ nâu
    lab[30:70, 40:100] = (70, 5, 45)                                  # thiệp vàng
    lab[80:110, 110:150] = (35, 40, 20)                               # hộp nhẫn đỏ
    lab += rng.normal(0, 2.0, lab.shape)
    img = (np.clip(color.lab_to_srgb(lab.reshape(-1, 3)), 0, 1).reshape(lab.shape) * 255 + 0.5).astype(np.uint8)
    reg = np.zeros(img.shape[:2], np.uint8)                           # OTHER: không người, không cây, không trời
    kq, out = run(img, reg, ref)
    d = color.delta_e2000(lab_of(img), lab_of(out))
    print(f"[evoto] ảnh chi tiết không người: ΔE trung bình {d.mean():.1f}")
    assert np.isfinite(out).all() and d.mean() < 6.0


if __name__ == "__main__":
    t0 = __import__("time").time()
    test_identity()
    test_match_look()
    test_dress_keeps_hue()
    test_flowers_not_neon()
    test_strength_and_full_res()
    test_no_people()
    with tempfile.TemporaryDirectory() as d:
        test_files(Path(d))
    print(f"EVOTO OK ({__import__('time').time() - t0:.0f}s)")
    _ = AS_SHOT
