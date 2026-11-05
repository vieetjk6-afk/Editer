"""Test bộ chép màu theo vùng (look.py) bằng ảnh tổng hợp có vùng biết trước."""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editer_anh import color, look, xmp  # noqa: E402
from editer_anh.look import CLOTHES, FACE, OTHER, PLANT, SKIN, SKY, Look, Shot  # noqa: E402

AS_SHOT = Shot(1e6 / 5500, 0.0, 0.0)


FLOWER = getattr(look, "FLOWER", OTHER)


def scene(seed: int, h: int = 160, w: int = 240, light_k: float | None = None, ev: float = 0.0,
          skin_lab=(68, 14, 18), red_dress: bool = False, flowers: bool = False):
    """Ảnh 'cưới' tổng hợp: tường, trời, cây, váy trắng, vest đen, mặt, tay, vài đồ vật màu
    (+ áo dài đỏ nếu red_dress)."""
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
        reg[m] = FACE
    arm = (yy > h * 0.55) & (yy < h * 0.62) & (xx > x0 - int(w * 0.06)) & (xx < x0)   # một cánh tay
    lab[arm] = (skin_lab[0] - 4, skin_lab[1] + 1, skin_lab[2] + 2)
    reg[arm] = SKIN
    if red_dress:   # cô dâu áo dài đỏ
        dm = (yy > h * 0.45) & (xx > pw + 6) & (xx < pw + int(w * 0.16))
        lab[dm] = (40, 55, 30)
        reg[dm] = CLOTHES
    for c in [(45, 55, 30), (80, 0, 70), (40, 35, -35)]:        # đồ vật màu
        y, x = rng.integers(sky_h, h - 25), rng.integers(pw, x0 - 25)
        lab[y:y + 22, x:x + 22] = c
    if flowers:   # tường hoa trang trí: hồng phấn, tím, trắng hồng (như ảnh cưới thật)
        fy0, fx1 = sky_h, int(w * 0.5)
        lab[fy0:fy0 + int(h * 0.25), :fx1] = (78, 22, -4)
        lab[fy0:fy0 + int(h * 0.12), fx1 // 2:fx1] = (58, 30, -34)
        lab[fy0 + int(h * 0.12):fy0 + int(h * 0.25), fx1 // 3:fx1 // 2] = (88, 9, 2)
        reg[fy0:fy0 + int(h * 0.25), :fx1] = FLOWER
    lab += rng.normal(0, 1.2, lab.shape)
    rgb = color.lab_to_srgb(lab.reshape(-1, 3))
    lin = color.srgb_to_linear(rgb)
    if light_k:   # chụp dưới đèn màu (ảnh ám theo nguồn sáng)
        M = look._bradford(color.D65_XY, color.temp_tint_to_xy(light_k, 0))
        lin = color.xyz_to_linear(color.linear_to_xyz(lin) @ M.T)
    lin = lin * 2 ** ev
    out = np.clip(color.linear_to_srgb(lin), 0, 1).reshape(h, w, 3)
    return (out * 255 + 0.5).astype(np.uint8), reg


def render(img, lk: Look, sh: Shot, k=look.DEFAULT, base_shot=AS_SHOT, reg=None):
    px = img.reshape(-1, 3) / 255.0
    y = look.simulate(px, Look(), base_shot, lk, sh, k, reg=None if reg is None else reg.reshape(-1))
    return (np.clip(y, 0, 1).reshape(img.shape) * 255 + 0.5).astype(np.uint8)


def stats_of(img, reg, seed=0, ref=False):
    s, idx = look.make_sample(img, reg, seed=seed)
    sel = look.select(s, ref=ref)
    m = look.measure(s.px, sel)
    return s, idx, sel, (look.sane_ref(m) if ref else m)     # như load_reference


TRUE_LOOK = Look(curve=np.array([22.0, 62, 100, 158, 200, 238]),
                 hue=np.array([0, -20, 0, 30, 0, 0, 0, 0.0]),
                 sat=np.array([10, 15, 0, -50, 0, -20, 0, 0.0]),
                 lum=np.array([0, 10, 0, -10, 0, 0, 0, 0.0]),
                 grade=np.array([-6, -8, 0, 0, 5, 9.0]), saturation=-5, vibrance=10)
TRUE_SHOT = Shot(1e6 / 5500 - 12, 4.0, -0.3)


def test_self_match():
    img, reg = scene(1)
    ref_img = render(img, TRUE_LOOK, TRUE_SHOT)
    _, _, ref_sel, ref = stats_of(ref_img, reg, seed=9, ref=True)
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
    _, _, ref_sel, ref = stats_of(ref_img, ref_reg, ref=True)
    s, idx, sel, m0 = stats_of(img, reg)
    lk, shots = look.solve([look.Rep(s.px, sel, Look(), AS_SHOT)], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT])
    k_new = 1e6 / shots[0].mired
    out = render(img, lk, shots[0])
    m = look.measure(look.sample_at(out, idx, s).px, sel)
    print(f"[đèn vàng] Nhiệt độ: 5500K -> {k_new:.0f}K; trước: {look.fmt_report(look.report([m0], ref))}"
          f"  | sau: {look.fmt_report(look.report([m], ref))}")
    assert k_new < 4300, "ảnh ám vàng phải được chỉnh nhiệt độ thấp xuống (lạnh lại)"
    assert look.report([m], ref)["trắng"] < 2.0


# "Camera Raw giả": cố tình khác mô phỏng của tool (thang HSL, grading, WB, chỉnh cục bộ,
# và cách dựng gốc)
FAKE = look.Consts(hue_frac=0.8, lum_k=22.0, grade_k=55.0, loc_temp=0.35, loc_tint=0.14)


def fake_acr(preview, lk, sh, reg=None, masks_work=True):
    base = render(preview, Look(curve=np.array([0.0, 40, 92, 166, 216, 255]), sat=np.full(8, 12.0)),
                  AS_SHOT, look.DEFAULT)               # Adobe Color khác ảnh xem trước của máy
    if not masks_work:   # Camera Raw không áp mask AI -> chỉnh cục bộ không có tác dụng
        lk = lk.copy()
        lk.skin_loc, lk.bg_loc = np.zeros(4), np.zeros(4)
    lk2 = lk.copy()
    lk2.skin_loc = lk.skin_loc * np.array([1.3, 1, 1, 0.8])   # thang cục bộ cũng khác
    return render(base, lk2, Shot(sh.mired, sh.tint, sh.ev + 0.15), FAKE, reg=reg)


def test_closed_loop_fixes_wrong_simulator():
    """Ảnh mẫu = "Camera Raw giả" dựng chính ảnh này với một bộ thông số biết trước (nên
    có đáp án hoàn hảo). Mô phỏng của tool KHÁC Camera Raw giả (thang HSL/grading, cách
    dựng gốc, exposure) => ước lượng ban đầu lệch; vòng kiểm tra phải kéo về gần đáp án."""
    target, reg = scene(4, light_k=4200, ev=-0.4)
    true = TRUE_LOOK.copy()
    true.skin_loc = np.array([0.3, 15, 5, 10.0])               # đáp án có chỉnh riêng vùng da
    ref_img = fake_acr(target, true, Shot(1e6 / 4300, 3.0, 0.5), reg)
    _, _, ref_sel, ref = stats_of(ref_img, reg, seed=77, ref=True)
    s, idx, sel, m0 = stats_of(target, reg)
    reps = [look.Rep(s.px, sel, Look(), AS_SHOT, s.reg)]
    lk0, sh0 = look.solve(reps, ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=False)
    t = time.time()
    best, hist, used = look.refine(reps, lambda i, lk, sh: fake_acr(target, lk, sh, reg), lambda im: reg,
                                   ref, ref_sel, lk0, sh0, [AS_SHOT], iters=5)
    assert used == (True, True), f"phải nhận ra mask da/nền có tác dụng: {used}"
    first, last = hist[0][0], best[2]
    print(f"[vòng kiểm tra] điểm lệch: {first:.2f} -> {last:.2f} sau {len(hist)} lượt ({time.time() - t:.1f}s)")
    print(f"    cuối: {look.fmt_report(best[3])}")
    # Thí nghiệm trên 6 cảnh: trung bình 15.7 -> 3.7, da ΔE TB 1.9, trắng TB 2.5.
    assert last < first * 0.3, "vòng kiểm tra phải giảm lệch rõ rệt"
    assert best[3]["da"] < 2.0 and best[3]["trắng"] < 4.0


def test_probe_masks_off():
    """Camera Raw không áp mask AI -> tool phải nhận ra và tắt chỉnh cục bộ."""
    target, reg = scene(6, light_k=4600)
    ref_img = fake_acr(scene(7)[0], TRUE_LOOK, Shot(1e6 / 5500, 0, 0.1), scene(7)[1])
    _, _, ref_sel, ref = stats_of(ref_img, scene(7)[1], ref=True)
    s, idx, sel, _ = stats_of(target, reg)
    reps = [look.Rep(s.px, sel, Look(), AS_SHOT, s.reg)]
    lk0, sh0 = look.solve(reps, ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=False)
    best, hist, used = look.refine(reps, lambda i, lk, sh: fake_acr(target, lk, sh, reg, masks_work=False),
                                   lambda im: reg, ref, ref_sel, lk0, sh0, [AS_SHOT], iters=1, log=lambda m: None)
    assert used[1] is False and used[0] in (False, "mo_phong"), used
    assert not np.any(best[0].bg_loc), "nền: không đo được thì không ghi"
    if used[0] == "mo_phong":   # da: chỉ ghi phần CÒN LỆCH ước lượng theo mô phỏng, nhẹ tay
        assert np.all(np.abs(best[0].skin_loc) <= [0.6, 25, 25, 30]), best[0].skin_loc
    print(f"[mask AI không hoạt động] nhận ra; da: {used[0]} {np.round(best[0].skin_loc, 2)}: OK")


def test_never_worse_than_original():
    """Ảnh gốc (Camera Raw, As Shot) đã giống ảnh mẫu -> vòng kiểm tra không được làm tệ đi:
    không tìm được gì tốt hơn rõ rệt thì GIỮ ẢNH GỐC."""
    target, reg = scene(17, light_k=5200)
    base = fake_acr(target, Look(), AS_SHOT, reg)                  # ảnh gốc Camera Raw dựng
    _, _, ref_sel, ref = stats_of(base, reg, seed=5, ref=True)     # ảnh mẫu = chính ảnh gốc
    s, idx = look.make_sample(base, reg, seed=0)
    sel = look.select(s)
    reps = [look.Rep(s.px, sel, Look(), AS_SHOT, s.reg)]
    lk0, sh0 = look.solve(reps, ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=False)
    best, hist, used = look.refine(reps, lambda i, lk, sh: fake_acr(target, lk, sh, reg, masks_work=False),
                                   lambda im: reg, ref, ref_sel, lk0, sh0, [AS_SHOT], iters=2, log=lambda m: None,
                                   bases0=[(idx, s, sel)], base_imgs=[base])
    if best[3].get("_giu_goc"):
        assert not np.any(best[0].sat) and best[1][0].mired == AS_SHOT.mired, "giữ gốc = không đổi gì"
    else:
        assert best[2] <= hist[0][0] + 1e-9
    print(f"[không tệ hơn gốc] giữ gốc: {bool(best[3].get('_giu_goc'))}, điểm {best[2]:.2f}: OK")


def test_keep_red_dress():
    """Ảnh mẫu tông ấm, có mặt người; ảnh đích có áo dài đỏ: áo phải giữ màu đỏ."""
    target, reg = scene(9, light_k=4400, red_dress=True)
    ref_img, rreg = scene(10, skin_lab=(72, 18, 24))
    ref_img = render(ref_img, Look(grade=np.array([0, 0, 0, 0, 4, 8.0]), sat=np.array([0, 10, 0, -40, 0, 0, 0, 0.0])),
                     Shot(1e6 / 5200, 0, 0.2))
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    s, idx, sel, m0 = stats_of(target, reg)
    assert "clothes" in m0, "phải nhận ra áo màu"
    rep_ = look.Rep(s.px, sel, Look(), AS_SHOT, s.reg, {"clothes": m0["clothes"]})
    lk, shots = look.solve([rep_], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=(True, True))
    out = render(target, lk, shots[0], reg=reg)
    m = look.measure(look.sample_at(out, idx, s).px, sel)
    rp = look.report([m], ref, [{"clothes": m0["clothes"]}])
    print(f"[giữ áo đỏ] {look.fmt_report(rp)}  | da cục bộ {np.round(lk.skin_loc, 2)}")
    assert rp["áo"] < 3.0, "áo đỏ bị đổi màu"
    assert rp["da"] < 2.0 and rp["trắng"] < 2.0


def test_dark_red_curtain():
    """Lỗi thật đã gặp: ảnh mẫu có rèm ĐỎ SẪM chiếm vùng tối -> bản cũ tưởng đó là "ám màu
    vùng tối" và nhuộm cam cả ảnh. Giờ vùng tối trung tính (vest đen) phải giữ trung tính."""
    ref_img, rreg = scene(12)
    lab = color.srgb_to_lab(ref_img.reshape(-1, 3) / 255.0).reshape(ref_img.shape)
    lab[: int(ref_img.shape[0] * 0.5), :] = (18, 38, 18)          # rèm đỏ sẫm che nửa trên
    rreg = rreg.copy()
    rreg[: int(ref_img.shape[0] * 0.5), :] = OTHER
    ref_img = (color.lab_to_srgb(lab.reshape(-1, 3)).reshape(ref_img.shape) * 255 + 0.5).astype(np.uint8)
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    target, reg = scene(13)
    s, idx, sel, m0 = stats_of(target, reg)
    lk, shots = look.solve([look.Rep(s.px, sel, Look(), AS_SHOT, s.reg)], ref, ref_sel, Look(), [AS_SHOT],
                           [AS_SHOT], use_local=False)
    out = render(target, lk, shots[0])
    suit = (reg == CLOTHES) & (color.srgb_to_lab(target.reshape(-1, 3) / 255.0)[:, 0].reshape(reg.shape) < 30)
    ab = color.srgb_to_lab(out.reshape(-1, 3) / 255.0)[suit.reshape(-1), 1:].mean(axis=0)
    print(f"[rèm đỏ] màu vest đen sau khi chép: a*={ab[0]:+.1f} b*={ab[1]:+.1f} (gần 0 = vẫn trung tính)")
    assert np.hypot(*ab) < 4.0, "vùng tối trung tính bị nhuộm màu theo rèm đỏ"


def test_orange_reference_whites():
    """Lỗi thật đã gặp (bản 0.4/0.5 đầu): ảnh mẫu nhiều gỗ / tường CAM -> tool lấy chính màu cam
    làm "màu trắng" (a*=+21, b*=+35) và kéo cả ảnh RAW sang cam. "Trắng" phải là vùng sáng, ít màu."""
    import skimage.data as skd
    s, _ = look.make_sample(skd.coffee(), None, seed=5)        # tách cà phê trắng trên bàn gỗ cam
    m = look.measure(s.px, look.select(s, ref=True))
    assert "neutral" in m and np.hypot(*m["neutral"]) < 15, f"trắng của ảnh cà phê bị nhận thành cam: {m.get('neutral')}"
    ref_img, rreg = scene(14)
    lab = color.srgb_to_lab(ref_img.reshape(-1, 3) / 255.0).reshape(ref_img.shape)
    wall = (rreg == OTHER) & (lab[..., 0] > 60) & (lab[..., 0] < 85)
    lab[wall] = (58, 20, 38)                                    # tường gỗ cam
    ref_img = (color.lab_to_srgb(lab.reshape(-1, 3)).reshape(ref_img.shape) * 255 + 0.5).astype(np.uint8)
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    target, reg = scene(15)
    s, idx, sel, m0 = stats_of(target, reg)
    lk, shots = look.solve([look.Rep(s.px, sel, Look(), AS_SHOT, s.reg)], ref, ref_sel, Look(), [AS_SHOT],
                           [AS_SHOT], use_local=False)
    out = render(target, lk, shots[0])
    dress = (reg == CLOTHES) & (color.srgb_to_lab(target.reshape(-1, 3) / 255.0)[:, 0].reshape(reg.shape) > 85)
    ab = color.srgb_to_lab(out.reshape(-1, 3) / 255.0)[dress.reshape(-1), 1:].mean(axis=0)
    k_new = 1e6 / shots[0].mired
    print(f"[ảnh mẫu tường cam] trắng ảnh mẫu a*,b* = {np.round(ref['neutral'], 1)}; váy trắng sau khi chép: "
          f"a*={ab[0]:+.1f} b*={ab[1]:+.1f}; nhiệt độ {k_new:.0f}K")
    assert np.hypot(*ab) < 8.0 and k_new < 9000, "váy trắng bị nhuộm cam theo tường"


def test_flowers_keep_color():
    """Lỗi thật đã gặp (bản 0.5.2, ảnh áo dài đỏ): ảnh mẫu toàn đỏ/nâu (rèm đỏ, không có hoa lá)
    -> tool làm XỈN tường hoa hồng/tím và lá xanh của ảnh RAW (thành xám). Ảnh mẫu không cho biết
    gì về hoa/lá thì không được đụng tới màu của chúng."""
    ref_img, rreg = scene(12, skin_lab=(70, 20, 22))
    lab = color.srgb_to_lab(ref_img.reshape(-1, 3) / 255.0).reshape(ref_img.shape)
    lab[: int(ref_img.shape[0] * 0.5), :] = (22, 40, 20)            # rèm đỏ sẫm che nửa trên
    lab[(rreg == PLANT)] = (30, 30, 18)                              # không có cây: gỗ / rèm
    rreg = rreg.copy()
    rreg[: int(ref_img.shape[0] * 0.5), :] = OTHER
    rreg[rreg == PLANT] = OTHER
    ref_img = (color.lab_to_srgb(lab.reshape(-1, 3)).reshape(ref_img.shape) * 255 + 0.5).astype(np.uint8)
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    target, reg = scene(16, light_k=6200, red_dress=True, flowers=True)
    s, idx, sel, m0 = stats_of(target, reg)
    keep = {"clothes": m0["clothes"]} if "clothes" in m0 else None
    rep_ = look.Rep(s.px, sel, Look(), AS_SHOT, s.reg, keep)
    lk, shots = look.solve([rep_], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=False)
    out = render(target, lk, shots[0])
    lab0 = color.srgb_to_lab(target.reshape(-1, 3) / 255.0)
    lab1 = color.srgb_to_lab(out.reshape(-1, 3) / 255.0)
    chroma = {}
    for name, code in (("hoa", FLOWER), ("lá", PLANT)):
        msk = (reg == code).reshape(-1)
        c0 = np.hypot(*lab0[msk, 1:].T).mean()
        c1 = np.hypot(*lab1[msk, 1:].T).mean()
        chroma[name] = c1 / c0
    m = look.measure(look.sample_at(out, idx, s).px, sel)
    rp = look.report([m], ref, [keep] if keep else None)
    print(f"[tường hoa] độ đậm màu sau/trước: hoa {chroma['hoa']:.2f}, lá {chroma['lá']:.2f}; {look.fmt_report(rp)}")
    assert chroma["hoa"] > 0.85 and chroma["lá"] > 0.85, f"hoa/lá bị làm xỉn: {chroma}"
    assert rp["da"] < 3.0, rp


def test_scene_matched_references():
    """Nhiều ảnh mẫu khác cảnh (trong nhà tông ấm, ngoài trời tông lạnh): mỗi ảnh RAW phải theo ảnh
    mẫu GIỐNG CẢNH NHẤT, không theo "trung bình" của tất cả (trung bình thì ảnh nào cũng lệch)."""
    warm = render(scene(20)[0], Look(), Shot(1e6 / 7600, 6.0, 0.0))     # mẫu A: tông ấm
    cool = render(scene(21)[0], Look(), Shot(1e6 / 4300, -4.0, 0.0))    # mẫu B: tông lạnh
    items, pooled_px, pooled_reg, globs, n0 = [], [], [], [], 0
    rng = np.random.default_rng(0)
    e_a, e_b = rng.normal(size=16), rng.normal(size=16)
    for name, (img, reg), e in (("A.jpg", (warm, scene(20)[1]), e_a), ("B.jpg", (cool, scene(21)[1]), e_b)):
        s, _ = look.make_sample(img, reg, seed=n0)
        items.append({"ten": name, "stats": look.measure(s.px, look.select(s, ref=True)), "emb": e / np.linalg.norm(e)})
        pooled_px.append(s.px)
        pooled_reg.append(s.reg)
        globs.append(s.glob + n0)
        n0 += len(s.px)
    ps = look.Sample(np.concatenate(pooled_px), np.concatenate(pooled_reg), np.concatenate(globs))
    psel = look.select(ps, ref=True)
    ref = look.Reference(ps, psel, look.measure(ps.px, psel), None, items)
    reps, kelvin = [], {}
    for name, sc, e in (("a", 22, e_a), ("b", 23, e_b)):
        img, reg = scene(sc)
        s, idx = look.make_sample(img, reg, seed=sc)
        tgt, used = look.scene_target(ref, e + rng.normal(scale=0.05, size=16))
        assert used and used[0][0] == (0 if name == "a" else 1), used
        reps.append(look.Rep(s.px, look.select(s), Look(), AS_SHOT, s.reg, None, tgt))
    lk, shots = look.solve(reps, ref.stats, psel, Look(), [AS_SHOT, AS_SHOT], [AS_SHOT, AS_SHOT], use_local=False)
    kelvin = [1e6 / sh.mired for sh in shots]
    print(f"[ảnh mẫu theo cảnh] ảnh giống mẫu ấm: {kelvin[0]:.0f}K, ảnh giống mẫu lạnh: {kelvin[1]:.0f}K")
    assert kelvin[0] - kelvin[1] > 1500, kelvin


def test_same_photo_pixel_match():
    """Ảnh mẫu là bản đã chỉnh (xuất nhỏ, như gửi qua Zalo/Facebook) của CHÍNH một ảnh RAW: khớp
    từng điểm ảnh phải ra gần như y hệt, tốt hơn khớp theo vùng."""
    from PIL import Image
    target, reg = scene(24, light_k=4800)
    edited = render(target, TRUE_LOOK, TRUE_SHOT, reg=reg)
    small = np.asarray(Image.fromarray(edited).resize((180, 120), Image.LANCZOS))
    moved = np.roll(target, 30, axis=1)          # ảnh chụp liên tiếp, người/khung lệch một chút
    assert look.same_frame(target, small) > 0.8, look.same_frame(target, small)
    assert look.same_frame(target, moved) < 0.8, look.same_frame(target, moved)
    _, _, ref_sel, ref = stats_of(edited, reg, seed=3, ref=True)
    s, idx = look.make_sample(target, reg, seed=1)
    lab_all = color.srgb_to_lab(edited.reshape(-1, 3) / 255.0)
    out_de = {}
    for mode in ("vùng", "điểm"):
        sel = look.select(s)
        tgt = dict(ref)
        if mode == "điểm":
            sel.pix, tgt["pix"] = look.pixel_targets(target, s, idx, small)
        rep_ = look.Rep(s.px, sel, Look(), AS_SHOT, s.reg, None, tgt)
        lk, shots = look.solve([rep_], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=False)
        out = render(target, lk, shots[0])
        out_de[mode] = float(np.mean(color.delta_e2000(color.srgb_to_lab(out.reshape(-1, 3) / 255.0), lab_all)))
    print(f"[cùng khung] lệch trung bình từng điểm ảnh: khớp theo vùng ΔE {out_de['vùng']:.2f} -> "
          f"khớp từng điểm ΔE {out_de['điểm']:.2f}")
    assert out_de["điểm"] < 3.0 and out_de["điểm"] <= out_de["vùng"] + 0.05, out_de


def test_dark_red_reference_no_mud():
    """Lỗi thật (bản 0.8.0, ảnh mẫu rèm đỏ tối): chỗ sáng nhất ảnh mẫu chỉ là mặt (L~64), màu trắng và
    da bị ánh đỏ hắt lên -> tool ép rèm trắng của ảnh RAW thành xám, đẩy nhiệt độ màu lên 16000K, mặt
    cam đỏ. Giờ: rèm trắng vẫn sáng, không ngả hồng; nhiệt độ màu không chạy xa As Shot; da không cam đỏ."""
    ref_img, rreg = scene(30, skin_lab=(64, 30, 26))                # da bị ánh đỏ hắt lên
    lab = color.srgb_to_lab(ref_img.reshape(-1, 3) / 255.0).reshape(ref_img.shape)
    bg = (rreg == OTHER) | (rreg == SKY) | (rreg == PLANT)
    lab[bg] = (16, 34, 16)                                         # rèm đỏ tối phủ hết phông
    rreg = np.where(bg, OTHER, rreg).astype(np.uint8)
    dress = rreg == CLOTHES
    lab[dress & (lab[..., 0] > 80)] = (82, 9, 9)                   # váy trắng nhưng ánh đỏ (hồng)
    ref_img = (color.lab_to_srgb(lab.reshape(-1, 3)).reshape(ref_img.shape) * 255 + 0.5).astype(np.uint8)
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    assert np.hypot(*ref["neutral"]) <= np.hypot(2.5, 10.0) + 1e-6, ref["neutral"]
    target, reg = scene(31)                                        # tường be + váy trắng sáng
    s, idx, sel, m0 = stats_of(target, reg)
    keep = {"clothes": m0["clothes"]} if "clothes" in m0 else None
    lk, shots = look.solve([look.Rep(s.px, sel, Look(), AS_SHOT, s.reg, keep)], ref, ref_sel, Look(),
                           [AS_SHOT], [AS_SHOT], use_local=False)
    out = render(target, lk, shots[0])
    lab0 = color.srgb_to_lab(target.reshape(-1, 3) / 255.0).reshape(target.shape)
    lab1 = color.srgb_to_lab(out.reshape(-1, 3) / 255.0).reshape(out.shape)
    white = (reg == CLOTHES) & (lab0[..., 0] > 85)
    face = reg == FACE
    k_new = 1e6 / shots[0].mired
    wl, wa = lab1[white, 0].mean(), lab1[white, 1].mean()
    fa, fb = lab1[face, 1].mean(), lab1[face, 2].mean()
    print(f"[ảnh mẫu rèm đỏ tối] váy trắng L {lab0[white, 0].mean():.0f} -> {wl:.0f}, a* {wa:+.1f}; "
          f"nhiệt độ {k_new:.0f}K; da a*,b* = {fa:+.1f},{fb:+.1f} (độ đậm {np.hypot(fa, fb):.0f})")
    assert wl > 80, "váy/rèm trắng bị ép tối theo ảnh mẫu"
    assert wa < 4.0, "màu trắng bị nhuộm hồng theo ánh rèm đỏ"
    assert 4100 < k_new < 8200, "nhiệt độ màu chạy quá xa As Shot"
    assert np.hypot(fa, fb) < 36, "da bị cam đỏ theo ánh rèm"


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


def test_match_ref_dress_keep_flowers():
    """Lỗi thật (0.12.2, Lightroom): ảnh mẫu K.Studio áo dài ĐỎ TƯƠI (L 41, C 52, tông 27°), không có hoa
    rực; kết quả: áo đỏ đô tối (L 17-26, C 34-41, 16-20°), hoa hồng/tím chói, sàn xanh két.
    Giờ: áo cùng tông với áo mẫu -> theo màu áo mẫu; màu ảnh mẫu không có (hoa tím/hồng) -> giữ màu gốc."""
    ref_img, rreg = scene(21, red_dress=True)                      # áo dài đỏ tươi (40, 55, 30)
    ref_img = render(ref_img, Look(sat=np.array([0, 10, 0, -30, 0, 0, 0, 0.0])), Shot(1e6 / 5300, 0, 0.2))
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    assert "clothes" in ref, "ảnh mẫu phải nhận ra áo đỏ"
    target, reg = scene(22, red_dress=True, flowers=True)
    lab = color.srgb_to_lab(target.reshape(-1, 3) / 255.0).reshape(target.shape)
    dress = (reg == CLOTHES) & (lab[..., 1] > 30)
    lab[dress] = (22, 36, 11)                                      # áo đỏ đô tối như ảnh của bạn
    target = (np.clip(color.lab_to_srgb(lab.reshape(-1, 3)), 0, 1).reshape(target.shape) * 255 + 0.5).astype(np.uint8)
    s, idx, sel, m0 = stats_of(target, reg)
    keep = look.make_keep(m0, ref)
    assert "clothes" not in keep, "ảnh mẫu cũng áo đỏ -> phải theo màu áo mẫu, không giữ áo đô"
    kept = [k for k in keep if k.startswith("band")]
    assert kept, "hoa tím / hồng (ảnh mẫu không có) -> phải giữ màu gốc"
    rep_ = look.Rep(s.px, sel, Look(), AS_SHOT, s.reg, keep or None)
    lk, shots = look.solve([rep_], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT], use_local=(True, True))
    out = render(target, lk, shots[0], reg=reg)
    m = look.measure(look.sample_at(out, idx, s).px, sel)
    rp = look.report([m], ref, [keep])
    d0 = float(color.delta_e2000(m0["clothes"], ref["clothes"]))
    print(f"[áo theo mẫu, giữ hoa] {look.fmt_report(rp)} | áo trước khi chép lệch mẫu ΔE {d0:.1f}")
    assert rp["áo theo mẫu"] < 0.5 * d0, "áo phải về gần màu áo mẫu (đỏ tươi)"
    assert rp["giữ màu"] < 4.0, "hoa không được bị nhuộm / rực lên"
    assert rp["da"] < 3.0 and rp["trắng"] < 2.5


def test_overcast_sky_kept():
    """Ảnh mẫu trời xanh (K.Studio), ảnh của bạn trời mây trắng (lối đi vườn dừa): không được ép trời mây
    thành trời xanh của mẫu (cả vùng sáng ám xanh lơ). Thời tiết là nội dung ảnh, không phải gu chỉnh màu."""
    ref_img, rreg = scene(23)
    _, _, ref_sel, ref = stats_of(ref_img, rreg, ref=True)
    assert "sky" in ref
    target, reg = scene(24)
    lab = color.srgb_to_lab(target.reshape(-1, 3) / 255.0).reshape(target.shape)
    sky = reg == SKY
    lab[sky] = np.array([88.0, 0.0, -2.0]) + np.random.default_rng(5).normal(0, 1.0, (int(sky.sum()), 3))
    target = (np.clip(color.lab_to_srgb(lab.reshape(-1, 3)), 0, 1).reshape(target.shape) * 255 + 0.5).astype(np.uint8)
    s, idx, sel, m0 = stats_of(target, reg)
    assert look.sky_mismatch(m0["sky"], ref["sky"]), "trời mây trắng vs trời xanh -> khác kiểu"
    assert not look.sky_mismatch(ref["sky"], ref["sky"]) and look.sky_mismatch([70, 15, 35], ref["sky"])
    chroma = {}
    for guard in (False, True):
        s, idx, sel, m0 = stats_of(target, reg)
        if guard:
            assert look.drop_sky_if_mismatch(sel, m0, ref) and sel.sky is None and "sky" not in m0
        rep_ = look.Rep(s.px, sel, Look(), AS_SHOT, s.reg, look.make_keep(m0, ref) or None)
        lk, shots = look.solve([rep_], ref, ref_sel, Look(), [AS_SHOT], [AS_SHOT])
        out = render(target, lk, shots[0], reg=reg)
        lo = color.srgb_to_lab(out.reshape(-1, 3) / 255.0).reshape(out.shape)
        chroma[guard] = float(np.hypot(*lo[sky][:, 1:].mean(axis=0)))
        if guard:
            m = look.measure(look.sample_at(out, idx, s).px, sel)
            rp = look.report([m], ref)
    print(f"[trời mây] độ đậm màu trời sau khi chép: ép theo mẫu {chroma[False]:.1f} -> giữ {chroma[True]:.1f} | "
          f"{look.fmt_report(rp)}")
    assert chroma[True] < 8.0, "trời mây phải còn gần trắng / xám"
    assert "trời" not in rp and rp["da"] < 3.0 and rp["trắng"] < 2.5


def test_skin_guard():
    """Lỗi thật (0.12.1, Lightroom): da ảnh gốc ΔE 9.2, kết quả da ΔE 15.0 (mặt xám) dù trắng / cây tốt hơn
    -> không được nhận. Da chỉ được lệch thêm tối đa 1 ΔE hoặc 10%."""
    base = {"da": 9.2, "trắng": 10.7, "cây": 18.8}
    assert not look.skin_ok({"da": 15.0, "trắng": 1.4, "cây": 8.0}, base)
    assert look.skin_ok({"da": 10.0, "trắng": 1.4}, base) and look.skin_ok({"da": 3.0}, base)
    assert look.skin_ok({"trắng": 1.0}, base) and look.skin_ok({"da": 30.0}, None)
    print("[da] không nhận kết quả làm da tệ hơn ảnh gốc: OK")


def test_probe_as_shot():
    """Lightroom không cho biết As Shot thật (số đọc về là nhiệt độ cũ của ảnh) -> đo bằng ảnh dựng thêm.
    Máy giả: As Shot tool tính lệch As Shot thật 12 mired / tint -30 (như Z5 II lệch Camera Raw ~50 tint);
    bộ dựng giả có độ nhạy WB khác mô phỏng (wb_gain 1.4) -> vẫn phải đo ra đúng As Shot thật."""
    true = Shot(1e6 / 5450, 21.0, 0.0)
    real = look.Consts(wb_gain=1.4)
    imgs, cams = [], []
    for seed, (dm, dt) in ((3, (12.0, -30.0)), (7, (6.6, -50.0))):     # 6.6 / -50: số đo thật Z5 II
        img, _ = scene(seed)
        imgs.append(img)
        cams.append(Shot(true.mired + dm, true.tint + dt, 0.0))
    calls = []

    def lr_render(j, lk, sh):
        calls.append(sh)
        return render(imgs[j], lk, sh if sh.mired is not None else true, real, base_shot=true)
    base = [lr_render(j, Look(), Shot(None, None, 0.0)) for j in range(2)]
    got = look.probe_as_shot(lr_render, [0, 1], base, cams, print)
    for j in (0, 1):
        t, ti = got[j]
        assert abs(1e6 / t - true.mired) < 2.0 and abs(ti - true.tint) < 3.0, (j, 1e6 / t, ti, true)
    assert len(calls) <= 2 + 2 * 3, "tối đa 3 lần dựng thêm mỗi ảnh"
    print(f"[As Shot] đo trên ảnh dựng: {got[0][0]:.0f}K tint {got[0][1]:+.1f} (thật 5450K +21): OK")


if __name__ == "__main__":
    import tempfile
    test_self_match()
    test_tungsten_needs_cooler_wb()
    test_closed_loop_fixes_wrong_simulator()
    test_probe_masks_off()
    test_never_worse_than_original()
    test_keep_red_dress()
    test_dark_red_curtain()
    test_orange_reference_whites()
    test_flowers_keep_color()
    test_scene_matched_references()
    test_same_photo_pixel_match()
    test_dark_red_reference_no_mud()
    with tempfile.TemporaryDirectory() as d:
        test_xmp_roundtrip(Path(d))
    test_transfer()
    test_skin_guard()
    test_probe_as_shot()
    test_match_ref_dress_keep_flowers()
    test_overcast_sky_kept()
    print("LOOK OK")
