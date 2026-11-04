"""HỌC PHONG CÁCH từ các job bạn đã chỉnh (RAW + .xmp cạnh nhau).

Phong cách của bạn = hai phần:
  - "Look" nền: profile, HSL, curve, grading, mask AI... gần như giống nhau mọi
    ảnh -> lấy nguyên từ một file .xmp tiêu biểu (mẫu), riêng cho từng đời máy.
  - Phần chỉnh riêng từng tấm (thường là Nhiệt độ màu, Tint, Exposure,
    Contrast...) -> mô hình học: nhìn ảnh + EXIF, đoán bạn sẽ kéo bao nhiêu.

Nhiệt độ màu được học dưới dạng ĐỘ LỆCH so với As Shot của máy (đơn vị mired),
không phải số K tuyệt đối — nên dùng được cho mọi điều kiện ánh sáng.
"""
from __future__ import annotations

import math
import pickle
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import color, raw, xmp

FORMAT_VERSION = 1
# Thông số có thể thay đổi theo từng ảnh (chỉ học cái nào bạn thực sự hay đổi)
CANDIDATES = {
    "Exposure2012": 0.05, "Contrast2012": 1.0, "Highlights2012": 1.0, "Shadows2012": 1.0,
    "Whites2012": 1.0, "Blacks2012": 1.0, "Texture": 1.0, "Clarity2012": 1.0, "Dehaze": 1.0,
    "Vibrance": 1.0, "Saturation": 1.0,
}
LIMITS = {"Exposure2012": (-5, 5), "d_mired": (-150, 150), "d_tint": (-150, 150)}
FEATURES = ["mired", "tint", "log_iso", "log_shutter", "fnumber", "focal", "flash", "hour",
            "y_p5", "y_p25", "y_p50", "y_p75", "y_p95", "y_p99", "clip_hi", "clip_lo",
            "n_a", "n_b", "gw_a", "gw_b", "hi_a", "hi_b", "chroma"]


def find_pairs(folders: list[str | Path]) -> list[tuple[Path, Path]]:
    pairs = []
    for f in folders:
        for r in raw.list_raws(f):
            s = xmp.find_sidecar(r)
            if s is not None:
                pairs.append((r, s))
    return pairs


def _targets_of(ph: raw.Photo, crs: dict) -> dict:
    t = {}
    temp = xmp.number(crs, "Temperature")
    tint = xmp.number(crs, "Tint")
    if temp and ph.as_shot_temp:
        t["d_mired"] = color.mired(temp) - color.mired(ph.as_shot_temp)
    if tint is not None and ph.as_shot_tint is not None:
        t["d_tint"] = tint - ph.as_shot_tint
    for k in CANDIDATES:
        v = xmp.number(crs, k)
        if v is not None:
            t[k] = v
    return t


def _pick_template(rows: list[dict]) -> Path:
    """Chọn file .xmp 'giống số đông' nhất làm mẫu."""
    keys = Counter(k for r in rows for k in r["crs"])
    common = [k for k, c in keys.items() if c >= 0.8 * len(rows)]
    med = {}
    for k in common:
        vals = [xmp.number(r["crs"], k) for r in rows]
        vals = [v for v in vals if v is not None]
        if vals:
            med[k] = (st.median(vals), (st.pstdev(vals) or 1.0))
    prof = Counter(r["crs"].get("CameraProfile") for r in rows).most_common(1)[0][0]
    nmask = Counter(len(r["masks"]) for r in rows).most_common(1)[0][0]
    best, best_d = None, math.inf
    for r in rows:
        d = 0.0 if r["crs"].get("CameraProfile") == prof else 100.0
        d += 0.0 if len(r["masks"]) == nmask else 10.0
        for k, (m, s) in med.items():
            v = xmp.number(r["crs"], k)
            d += 5.0 if v is None else abs(v - m) / s
        if d < best_d:
            best, best_d = r["xmp"], d
    return best


def learn(folders: list[str | Path], log=print, workers: int = 8) -> dict:
    pairs = find_pairs(folders)
    if not pairs:
        raise SystemExit("Không thấy cặp ảnh RAW + .xmp nào trong các thư mục đã chọn.")
    log(f"Tìm thấy {len(pairs)} ảnh đã chỉnh. Đang đọc...")
    photos = load_many([p for p, _ in pairs], workers, log)
    rows = []
    for (rp, sp), ph in zip(pairs, photos):
        if ph.error:
            log(f"  bỏ qua {rp.name}: {ph.error}")
            continue
        try:
            s = xmp.read_settings(sp)
        except Exception as e:
            log(f"  bỏ qua {sp.name}: {e}")
            continue
        ph.model = ph.model or s["model"]
        rows.append({"photo": ph, "crs": s["crs"], "masks": s["masks"], "xmp": sp,
                     "targets": _targets_of(ph, s["crs"]), "feat": raw.features(ph)})
    if len(rows) < 10:
        raise SystemExit("Cần ít nhất 10 ảnh đã chỉnh để học.")

    by_model = defaultdict(list)
    for r in rows:
        by_model[r["photo"].model or "?"].append(r)
    templates = {m: xmp.make_template(_pick_template(rs)) for m, rs in by_model.items()}
    default_model = max(by_model, key=lambda m: len(by_model[m]))
    cams = sorted(by_model)

    # Thông số nào bạn thực sự thay đổi giữa các ảnh?
    targets = ["d_mired", "d_tint"]
    for k, thr in CANDIDATES.items():
        vals = [r["targets"][k] for r in rows if k in r["targets"]]
        if len(vals) >= 10 and st.pstdev(vals) >= thr:
            targets.append(k)

    X = np.array([[r["feat"][f] for f in FEATURES] + [cams.index(r["photo"].model or "?")] for r in rows], float)
    # Bỏ các cột không có dữ liệu (vd file CR3 không đọc được EXIF)
    cols = [i for i in range(X.shape[1] - 1) if np.isfinite(X[:, i]).any()] + [X.shape[1] - 1]
    X = X[:, cols]
    models, report, medians = {}, {}, {}
    for t in targets:
        idx = [i for i, r in enumerate(rows) if t in r["targets"]]
        if len(idx) < 10:
            continue
        y = np.array([rows[i]["targets"][t] for i in idx])
        medians[t] = float(np.median(y))
        est, mae, base = _fit(X[idx], y, len(cams))
        models[t] = est
        report[t] = {"n": len(idx), "mae": mae, "mae_median": base}
    per_model_temp = {m: float(np.median([xmp.number(r["crs"], "Temperature") or 5500 for r in rs]))
                      for m, rs in by_model.items()}
    return {
        "format": FORMAT_VERSION, "templates": templates, "default_model": default_model,
        "cameras": cams, "targets": list(models), "models": models, "medians": medians,
        "report": report, "n_photos": len(rows), "median_temp": per_model_temp, "cols": cols,
        "counts": {m: len(rs) for m, rs in by_model.items()},
    }


def _fit(X, y, n_cams):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import KFold, cross_val_predict
    cat = [False] * (X.shape[1] - 1) + [n_cams > 1]
    n = len(y)
    est = HistGradientBoostingRegressor(
        max_iter=250, learning_rate=0.05, max_leaf_nodes=15,
        min_samples_leaf=max(5, n // 50), l2_regularization=1.0,
        categorical_features=cat if any(cat) else None, random_state=0)
    base = float(np.mean(np.abs(y - np.median(y))))
    mae = math.nan
    if n >= 40:
        pred = cross_val_predict(est, X, y, cv=KFold(5, shuffle=True, random_state=0))
        mae = float(np.mean(np.abs(pred - y)))
    est.fit(X, y)
    return est, mae, base


def predict(model: dict, photos: list[raw.Photo]) -> list[dict]:
    cams = model["cameras"]
    X = np.array([[raw.features(p)[f] for f in FEATURES] +
                  [cams.index(p.model) if p.model in cams else cams.index(model["default_model"])]
                  for p in photos], float)[:, model["cols"]]
    out = [dict() for _ in photos]
    for t, est in model["models"].items():
        pred = est.predict(X)
        lo, hi = LIMITS.get(t, (-100, 100))
        for o, v in zip(out, pred):
            o[t] = float(np.clip(v, lo, hi))
    return out


def save(model: dict, path: str | Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: str | Path) -> dict:
    # Chỉ mở file phong cách do chính bạn tạo ra (pickle).
    with open(path, "rb") as f:
        m = pickle.load(f)
    if m.get("format") != FORMAT_VERSION:
        raise SystemExit("File phong cách không đúng phiên bản, hãy học lại.")
    return m


def load_many(paths, workers=8, log=print) -> list[raw.Photo]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = [None] * len(paths)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(raw.load, p): i for i, p in enumerate(paths)}
        done = 0
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
            done += 1
            if done % 50 == 0 or done == len(paths):
                log(f"  đã đọc {done}/{len(paths)}")
    return out


def group_scenes(photos: list[raw.Photo], gap_minutes: float = 4.0) -> list[int]:
    """Chia cảnh theo giờ chụp: cách nhau > gap phút là sang cảnh mới."""
    order = sorted(range(len(photos)), key=lambda i: photos[i].time or 0)
    scene = [0] * len(photos)
    cur, last = 0, None
    for i in order:
        t = photos[i].time or 0
        if last is not None and t - last > gap_minutes * 60:
            cur += 1
        scene[i] = cur
        last = t
    return scene


def smooth_by_scene(values: list[dict], scenes: list[int], keys=("d_mired", "d_tint"), w: float = 0.5) -> None:
    """Kéo nhiệt độ màu các ảnh cùng cảnh về gần nhau -> màu da đồng đều."""
    groups = defaultdict(list)
    for i, s in enumerate(scenes):
        groups[s].append(i)
    for idx in groups.values():
        if len(idx) < 3:
            continue
        for k in keys:
            vals = [values[i][k] for i in idx if k in values[i]]
            if len(vals) < 3:
                continue
            m = float(np.median(vals))
            for i in idx:
                if k in values[i]:
                    values[i][k] = (1 - w) * values[i][k] + w * m
