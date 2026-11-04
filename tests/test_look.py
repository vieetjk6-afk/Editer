"""Test bộ chép màu theo vùng (look.py) bằng ảnh tổng hợp có vùng biết trước."""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editer_anh import color, look, xmp  # noqa: E402
from editer_anh.look import CLOTHES, OTHER, PLANT, SKIN, SKY, Look, Shot  # noqa: E402

AS_SHOT = Shot(1e6 / 5500, 0.0, 0.0)


def scene(seed: int, h: int = 160, w: int = 240, light_k: float | None = None, ev: float = 0.0,
          skin_lab=(68, 14, 18)):
    """Ảnh 'cưới' tổng hợp: tường, trời, cây, váy trắng, vest đen, da, vài đồ vật màu."""
    rng = np.random.default_rng(seed)
    lab = np.zeros((h, w, 3))
    reg = np.full((h, w), OTHER, np.uint8)
    lab[:] = (75, 3, 12)                                        # tường be
    sky_h = int(h * rng.uniform(0.2, 0.35))
    lab[:sky_h] = np.stack([np.linspace(85, 70, sky_h)[:, None].repeat(w, 1),
                            np.full((sky_h, w), -5.0), np.full((sky_h, w), -30.0)], -1)
    reg[:sky_h] = SKY
    ph, pw = int(h * 0.35), int(w * rng.uniform(0.25, 0.4))
    lab[h - ph:, :pw] = (45, -25, 25)                           # cây
    reg[h - ph:, :pw] = PLANT
    x0 = int(w * 0.55)
    lab[int(h * 0.45):, x0:x0 + int(w * 0.18)] = (92, 0, 3)     # váy trắng
    reg[int(h * 0.45):, x0:x0 + int(w * 0.18)] = CLOTHES
    lab[int(h * 0.5):, x0 + int(w * 0.2):x0 + int(w * 0.32)] = (18, 0, -1)   # vest đen
    reg[int(h * 0.5):, x0 + int(w * 0.2):x0 + int(w * 0.32)] = CLOTHES
    yy, xx = np.mgrid[:h, :w]
    for cx in (x0 + int(w * 0.09), x0 + int(w * 0.26)):         # 2 khuôn mặt
        m = ((yy - h * 0.38) / (h * 0.08)) ** 2 + ((xx - cx) / (w * 0.035)) ** 2 < 1
        lab[m] = skin_lab
        reg[m] = SKIN
    for c in [(45, 55, 30), (80, 0, 70), (40, 35, -35)]:        # đồ vật màu
        y, x = rng.integers(sky_h, h - 25), rng.integers(pw, x0 - 25)
        lab[y:y + 22, x:x + 22] = c
    lab += rng.normal(0, 1.2, lab.shape)
    rgb = color.lab_to_srgb(lab.reshape(-1, 3))
    lin = color.srgb_to_linear(rgb)
    if light_k:   # chụp dưới đèn màu (ảnh ám theo nguồn sáng)
        M = look._bradford(color.D65_XY, color.temp_tint_to_xy(light_k, 0))
        lin = color.xyz_to_linear(color.linear_to_xyz(lin) @ M.T)
    lin = lin * 2 ** ev
    out = np.clip(color.linear_to_srgb(lin), 0, 1).reshape(h, w, 3)
    return (out * 255 + 0.5).astype(np.uint8), reg


def render(img, lk: Look, sh: Shot, k=look.DEFAULT, base_shot=AS_SHOT):
    px = img.reshape(-1, 3) / 255.0
    y = look.simulate(px, Look(), base_shot, lk, sh, k)
    return (np.clip(y, 0, 1).reshape(img.shape) * 255 + 0.5).astype(np.uint8)


def stats_of(img, reg, seed=0):
    s, idx = look.make_sample(img, reg, seed=seed)
    sel = look.select(s)
    return s, idx, sel, look.measure(s.px, sel)


TRUE_LOOK = Look(curve=np.array([22.0, 62, 100, 158, 200, 238]),
                 hue=np.array([0, -20, 0, 30, 0, 0, 0, 0.0]),
                 sat=np.array([10, 15, 0, -50, 0, -20, 0, 0.0]),
                 lum=np.array([0, 10, 0, -10, 0, 0, 0, 0.0]),
                 grade=np.array([-6, -8, 0, 0, 5, 9.0]), saturation=-5, vibrance=10)
TRUE_SHOT = Shot(1e6 / 5500 - 12, 4.0, -0.3)


def test_self_match():
    img, reg = scene(1)
    ref_img = render(img, TRUE_LOOK, TRUE_SHOT)
    _, _, ref_sel, ref = stats_of(ref_img, reg, seed=9)
    s, idx, sel, _ = stats_of(img, reg)
    t = time.time()
    lk, shots = look.solve([look.Rep(s.px, sel, Look(), AS_SHOT)], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT])
    out = render(img, lk, shots[0])
    m = look.measure(look.sample_at(out, idx, s).px, sel)
    rp = look.report([m], ref)
    print(f"[tự khớp] {look.fmt_report(rp)}  ({time.time() - t:.1f}s)")
    assert rp["da"] < 1.0 and rp["trắng"] < 1.0 and rp["sáng tối"] < 1.0, rp


def test_tungsten_needs_cooler_wb():
    img, reg = scene(2, light_k=3000)            # ảnh ám cam (đèn vàng, máy để 5500K)
    ref_img, ref_reg = scene(3)                  # ảnh mẫu trung tính
    _, _, ref_sel, ref = stats_of(ref_img, ref_reg)
    s, idx, sel, m0 = stats_of(img, reg)
    lk, shots = look.solve([look.Rep(s.px, sel, Look(), AS_SHOT)], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT])
    k_new = 1e6 / shots[0].mired
    out = render(img, lk, shots[0])
    m = look.measure(look.sample_at(out, idx, s).px, sel)
    print(f"[đèn vàng] Nhiệt độ: 5500K -> {k_new:.0f}K; trước: {look.fmt_report(look.report([m0], ref))}"
          f"  | sau: {look.fmt_report(look.report([m], ref))}")
    assert k_new < 4300, "ảnh ám vàng phải được chỉnh nhiệt độ thấp xuống (lạnh lại)"
    assert look.report([m], ref)["trắng"] < 2.0


# "Camera Raw giả": cố tình khác mô phỏng của tool (thang HSL, grading, WB, và cách dựng gốc)
FAKE = look.Consts(hue_frac=0.8, lum_k=22.0, grade_k=55.0)


def fake_acr(preview, lk, sh):
    base = render(preview, Look(curve=np.array([0.0, 40, 92, 166, 216, 255]), sat=np.full(8, 12.0)),
                  AS_SHOT, look.DEFAULT)               # Adobe Color khác ảnh xem trước của máy
    return render(base, lk, Shot(sh.mired, sh.tint, sh.ev + 0.15), FAKE)


def test_closed_loop_fixes_wrong_simulator():
    """Ảnh mẫu = "Camera Raw giả" dựng chính ảnh này với một bộ thông số biết trước (nên
    có đáp án hoàn hảo). Mô phỏng của tool KHÁC Camera Raw giả (thang HSL/grading, cách
    dựng gốc, exposure) => ước lượng ban đầu lệch; vòng kiểm tra phải kéo về gần đáp án."""
    target, reg = scene(4, light_k=4200, ev=-0.4)
    ref_img = fake_acr(target, TRUE_LOOK, Shot(1e6 / 4300, 3.0, 0.5))
    _, _, ref_sel, ref = stats_of(ref_img, reg, seed=77)
    s, idx, sel, _ = stats_of(target, reg)
    reps = [look.Rep(s.px, sel, Look(), AS_SHOT)]
    lk0, sh0 = look.solve(reps, ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT])
    t = time.time()
    best, hist = look.refine(reps, lambda i, lk, sh: fake_acr(target, lk, sh), lambda im: reg,
                             ref, ref_sel, lk0, sh0, [AS_SHOT], iters=5)
    first, last = hist[0][0], best[2]
    print(f"[vòng kiểm tra] điểm lệch: {first:.2f} -> {last:.2f} sau {len(hist)} lượt ({time.time() - t:.1f}s)")
    print(f"    cuối: {look.fmt_report(best[3])}")
    # Thí nghiệm trên 6 cảnh: trung bình 15.7 -> 3.7, da ΔE TB 1.9, trắng TB 2.5.
    assert last < first * 0.3, "vòng kiểm tra phải giảm lệch rõ rệt"
    assert best[3]["da"] < 2.0 and best[3]["trắng"] < 4.0


def test_xmp_roundtrip(tmp: Path):
    vals, curve = look.xmp_values(TRUE_LOOK, TRUE_SHOT)
    p = tmp / "rt.xmp"
    p.write_text(xmp.build_xmp(None, vals, curve), encoding="utf-8")
    crs = xmp.read_settings(p)["crs"]
    assert "SplitToningShadowHue" in crs and "ColorGradeShadowHue" not in crs
    lk, sh = look.from_crs(crs, xmp.read_curve(p))
    assert np.allclose(lk.hue, TRUE_LOOK.hue, atol=0.6) and np.allclose(lk.sat, TRUE_LOOK.sat, atol=0.6)
    assert np.allclose(lk.curve, TRUE_LOOK.curve, atol=0.6)
    assert np.allclose(lk.grade, TRUE_LOOK.grade, atol=1.0), (lk.grade, TRUE_LOOK.grade)
    assert abs(1e6 / sh.mired - 1e6 / TRUE_SHOT.mired) < 1 and abs(sh.ev - TRUE_SHOT.ev) < 0.01
    pre = xmp.build_preset("Editer - thử", vals, curve)
    q = tmp / "preset.xmp"
    q.write_text(pre, encoding="utf-8")
    c2 = xmp.read_settings(q)["crs"]
    assert c2["PresetType"] == "Normal" and "Temperature" not in c2 and "SplitToningShadowSaturation" in c2
    print("[xmp] đọc/ghi + preset: OK")


def test_transfer():
    r = Shot(180.0, 5.0, 0.3)
    same = look.transfer(r, (150.0, 2.0), (150.0, 2.0), Shot(181.0, 3.0), Shot(181.0, 3.0), 0.2, 0.2)
    assert abs(same.mired - 180) < 1e-9 and abs(same.ev - 0.3) < 1e-9
    # cùng ánh sáng, máy tự cân bằng trắng khác (ảnh xem trước trông khác) -> vẫn ra cùng WB
    other = look.transfer(r, (150.0, 2.0), (150.0 + 10, 2.0), Shot(181.0, 3.0), Shot(171.0, 3.0), 0.2, 0.1)
    assert abs(other.mired - 180) < 1e-9 and abs(other.ev - 1.3) < 1e-9
    print("[chuyển sang cả lô] OK")


if __name__ == "__main__":
    import tempfile
    test_self_match()
    test_tungsten_needs_cooler_wb()
    test_closed_loop_fixes_wrong_simulator()
    with tempfile.TemporaryDirectory() as d:
        test_xmp_roundtrip(Path(d))
    test_transfer()
    print("LOOK OK")
