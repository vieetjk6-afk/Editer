"""Thí nghiệm: tạo ảnh mẫu bằng một phép chỉnh BIẾT TRƯỚC, xem match có đoán
đúng hướng không (ấm/lạnh, sáng/tối, xanh lá nhạt, điểm đen nhấc)."""
import sys
import numpy as np
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from editer_anh import color, match  # noqa: E402
import skimage.data as d


def transform(rgb_u8, warm_mired=-25, ev=-0.4, green_desat=0.5, lift=20):
    rgb = rgb_u8 / 255.0
    st = match.Stats.of(rgb_u8)
    # WB: ảnh ấm hơn (giảm "mired xuất hiện" = trông thấp K hơn => cộng mired)
    tp, ti = color.xy_to_temp_tint(*st.neutral)
    src = st.neutral
    dst = color.temp_tint_to_xy(1e6 / (color.mired(tp) - warm_mired), ti)
    lin = color.srgb_to_linear(rgb)
    lin = color.xyz_to_linear(color.linear_to_xyz(lin) @ match._bradford(src, dst).T) * 2 ** ev
    out = np.clip(color.linear_to_srgb(lin), 0, 1)
    hsv = color.rgb_to_hsv(out.reshape(-1, 3))
    g = (hsv[:, 0] > 80) & (hsv[:, 0] < 160)
    flat = out.reshape(-1, 3)
    gray = flat @ np.array([0.2126, 0.7152, 0.0722])
    flat[g] = gray[g, None] + (flat[g] - gray[g, None]) * green_desat
    out = flat.reshape(out.shape)
    out = lift / 255 + out * (1 - lift / 255)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def run(img, name):
    ref = transform(img)
    p, r = match.Stats.of(img), match.Stats.of(ref)
    dm, dt, dev = match.wb_exposure(p, r)
    sim = match.simulate(p, dm, dt, dev)
    curve, params = match.batch_look([sim], r)
    print(f"[{name}] dMired={dm:+.1f} (âm = tăng K = ấm hơn) dTint={dt:+.1f} dEV={dev:+.2f}")
    print("   curve", curve)
    print("   green sat", round(params["SaturationAdjustmentGreen"]), "yellow sat", round(params["SaturationAdjustmentYellow"]),
          "aqua", round(params["SaturationAdjustmentAqua"]), "red", round(params["SaturationAdjustmentRed"]))
    assert dm < -3, "phải ấm hơn"
    assert dev < 0, "phải tối hơn"
    assert curve[0][1] >= 8, "phải nhấc điểm đen"
    return params


def colorful(seed=0, h=300, w=400):
    """Ảnh nhiều mảng màu (có xanh lá, xanh dương, da...)"""
    rng = np.random.default_rng(seed)
    hsv_img = np.zeros((h, w, 3))
    for _ in range(120):
        y0, x0 = rng.integers(0, h - 30), rng.integers(0, w - 30)
        hsv_img[y0:y0 + rng.integers(20, 90), x0:x0 + rng.integers(20, 90)] = [rng.uniform(0, 360), rng.uniform(0.2, 0.8), rng.uniform(0.3, 0.9)]
    from colorsys import hsv_to_rgb
    flat = np.array([hsv_to_rgb(a / 360, b, c) for a, b, c in hsv_img.reshape(-1, 3)])
    flat += rng.normal(0, 0.01, flat.shape)
    return (np.clip(flat, 0, 1).reshape(h, w, 3) * 255).astype(np.uint8)


if __name__ == "__main__":
    for img, n in [(d.astronaut(), "astronaut"), (d.coffee(), "coffee"), (d.chelsea(), "chelsea")]:
        run(img, n)
    prm = run(colorful(), "nhieu-mau")
    assert prm["SaturationAdjustmentGreen"] < -15, "xanh lá phải nhạt đi"
    assert abs(prm["SaturationAdjustmentRed"]) < abs(prm["SaturationAdjustmentGreen"])
    # Ảnh giống hệt mẫu -> gần như không chỉnh gì
    img = d.coffee()
    p = match.Stats.of(img)
    dm, dt, dev = match.wb_exposure(p, p)
    curve, params = match.batch_look([match.simulate(p, dm, dt, dev)], p)
    print("identity:", round(dm, 2), round(dt, 2), round(dev, 2), curve, max(abs(v) for v in params.values() if isinstance(v, float)) if params else 0)
    assert abs(dm) < 1 and abs(dev) < 0.05
    assert all(abs(y - x) <= 3 for x, y in curve)
    print("OK")
