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

FORMAT_VERSION = 2
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


def _row_key(rp: Path, sp: Path) -> str:
    return f"{rp.resolve()}|{rp.stat().st_size}|{sp.stat().st_mtime_ns}"


def learn(folders: list[str | Path], log=print, workers: int = 8, previous: dict | None = None) -> dict:
    """Học (hoặc học thêm, nếu có `previous`: chỉ đọc ảnh mới/ảnh có .xmp vừa sửa)."""
    from . import ai
    emb_model = ai.Embedder.get()
    log("AI hiểu ảnh: " + (f"BẬT ({emb_model.device})" if emb_model else "TẮT (không có mô hình)"))
    pairs = find_pairs(folders)
    old = {r["key"]: r for r in (previous or {}).get("dataset", [])}
    if not pairs and not old:
        raise SystemExit("Không thấy cặp ảnh RAW + .xmp nào trong các thư mục đã chọn.")
    keyed = [(_row_key(rp, sp), rp, sp) for rp, sp in pairs]
    # đọc lại: ảnh mới / .xmp vừa sửa / ảnh trước đây chưa đọc được cân bằng trắng gốc (máy mới)
    need = [(k, rp, sp) for k, rp, sp in keyed
            if k not in old or (emb_model is not None and old[k].get("emb") is None)
            or ("d_mired" not in old[k]["targets"] and "Temperature" in old[k]["crs"])]
    log(f"Tìm thấy {len(pairs)} ảnh đã chỉnh; {len(need)} ảnh mới/đã sửa cần đọc"
        + (f", {len(old)} ảnh đã học từ trước." if old else "."))
    photos = load_many([rp for _, rp, _ in need], workers, log) if need else []
    for line in raw.wb_summary(photos):
        log(line)
    fresh = []
    for (k, rp, sp), ph in zip(need, photos):
        if ph.error:
            log(f"  bỏ qua {rp.name}: {ph.error}")
            continue
        try:
            s = xmp.read_settings(sp)
        except Exception as e:
            log(f"  bỏ qua {sp.name}: {e}")
            continue
        fresh.append((ph, {"key": k, "raw": str(rp.resolve()), "xmp": str(sp), "model": ph.model or s["model"] or "?",
                           "crs": s["crs"], "masks": s["masks"], "targets": _targets_of(ph, s["crs"]),
                           "feat": raw.features(ph), "emb": None}))
    if emb_model and fresh:
        log(f"  AI đang xem {len(fresh)} ảnh...")
        embs = emb_model.embed([ph.preview for ph, _ in fresh])
        for (_, r), e in zip(fresh, embs):
            r["emb"] = e.astype(np.float16)
    # Gộp: bản mới thay bản cũ của cùng file RAW
    replaced = {r["raw"] for _, r in fresh}
    rows = [r for r in old.values() if r["raw"] not in replaced] + [r for _, r in fresh]
    if len(rows) < 10:
        raise SystemExit("Cần ít nhất 10 ảnh đã chỉnh để học.")

    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)
    templates = dict((previous or {}).get("templates", {}))
    for m, rs in by_model.items():
        alive = [r for r in rs if Path(r["xmp"]).exists()]
        if alive:
            templates[m] = xmp.make_template(_pick_template(alive))
    default_model = max(by_model, key=lambda m: len(by_model[m]))
    cams = sorted(by_model)

    targets = ["d_mired", "d_tint"]
    for k, thr in CANDIDATES.items():
        vals = [r["targets"][k] for r in rows if k in r["targets"]]
        if len(vals) >= 10 and st.pstdev(vals) >= thr:
            targets.append(k)

    ai_state = _fit_ai(rows, emb_model)
    X, cols = _matrix(rows, cams, ai_state, None)
    knn_cols = _knn_cols(cols)
    noai_cols = [j for j, c in enumerate(cols) if c < len(FEATURES) or j == len(cols) - 1]
    models, report = {}, {}
    for t in targets:
        idx = [i for i, r in enumerate(rows) if t in r["targets"]]
        if len(idx) < 10:
            continue
        y = np.array([rows[i]["targets"][t] for i in idx])
        models[t], report[t] = _fit(X[idx], y, len(cams), knn_cols, noai_cols if ai_state else None)
    return {
        "format": FORMAT_VERSION, "templates": templates, "default_model": default_model,
        "cameras": cams, "targets": list(models), "models": models, "report": report,
        "n_photos": len(rows), "cols": cols, "ai": ai_state,
        "counts": {m: len(rs) for m, rs in by_model.items()}, "dataset": rows,
        "history": (previous or {}).get("history", []) + [_history_entry(report, len(rows))],
    }


def _history_entry(report: dict, n: int) -> dict:
    import time
    return {"time": time.time(), "n": n, **{t: r["mae"] for t, r in report.items()}}


N_PCA = 16


def _fit_ai(rows: list[dict], emb_model) -> dict | None:
    E = [r["emb"] for r in rows if r.get("emb") is not None]
    if emb_model is None or len(E) < 10:
        return None
    E = np.array(E, np.float32)
    mean = E.mean(axis=0)
    _, _, vt = np.linalg.svd(E - mean, full_matrices=False)
    return {"mean": mean, "comps": vt[:N_PCA].astype(np.float32),
            "labels": emb_model.label_emb.copy(), "label_keys": emb_model.label_keys,
            "dim": int(E.shape[1])}


def _ai_feats(emb, ai_state) -> list[float]:
    n = N_PCA + len(ai_state["label_keys"])
    if emb is None:
        return [math.nan] * n
    e = np.asarray(emb, np.float32)
    return list((e - ai_state["mean"]) @ ai_state["comps"].T) + list(e @ ai_state["labels"].T)


def _matrix(rows: list[dict], cams: list[str], ai_state: dict | None, cols: list[int] | None):
    """Bảng đầu vào: số đo ảnh + EXIF (+ dấu vân tay AI + điểm nhãn cảnh) + đời máy."""
    data = []
    for r in rows:
        v = [r["feat"][f] for f in FEATURES]
        if ai_state:
            v += _ai_feats(r.get("emb"), ai_state)
        m = r["model"] if r["model"] in cams else None
        v.append(cams.index(m) if m else 0)
        data.append(v)
    X = np.array(data, float)
    if cols is None:  # bỏ cột trống (vd file CR3 không đọc được EXIF)
        cols = [i for i in range(X.shape[1] - 1) if np.isfinite(X[:, i]).any()] + [X.shape[1] - 1]
    return X[:, cols], cols


_KNN_BASE = ["mired", "tint", "y_p50", "y_p95", "clip_hi", "n_a", "n_b", "flash"]


def _knn_cols(cols: list[int]) -> list[int]:
    """Cột dùng để tìm 'ảnh cũ giống nhất': dấu vân tay AI + vài số đo chính."""
    want = {FEATURES.index(f) for f in _KNN_BASE}
    want |= set(range(len(FEATURES), len(FEATURES) + N_PCA))
    return [j for j, c in enumerate(cols[:-1]) if c in want]


class _Blend:
    """Trung bình của hai cách đoán."""
    def __init__(self, a, b):
        self.a, self.b = a, b

    def predict(self, X):
        return 0.5 * (self.a.predict(X) + self.b.predict(X))


class _Cols:
    def __init__(self, est, cols):
        self.est, self.cols = est, cols

    def fit(self, X, y):
        self.est.fit(X[:, self.cols], y)
        return self

    def predict(self, X):
        return self.est.predict(X[:, self.cols])

    def get_params(self, deep=True):
        return {"est": self.est, "cols": self.cols}

    def set_params(self, **p):
        for k, v in p.items():
            setattr(self, k, v)
        return self


def _fit(X, y, n_cams, knn_cols, noai_cols=None):
    """Thử 3 cách, kiểm tra chéo trên chính ảnh của bạn, giữ cách sai ít nhất:
       - gbm: cây quyết định tăng cường (học quy luật)
       - knn: lấy theo các ảnh cũ giống nhất (bạn từng chỉnh tấm giống thế nào)
       - blend: trung bình hai cách trên"""
    from sklearn.base import clone
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.model_selection import KFold
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    n = len(y)
    cat = [False] * (X.shape[1] - 1) + [n_cams > 1]
    gbm = HistGradientBoostingRegressor(
        max_iter=250, learning_rate=0.05, max_leaf_nodes=15,
        min_samples_leaf=max(5, n // 50), l2_regularization=1.0,
        categorical_features=cat if any(cat) else None, random_state=0)
    knn = _Cols(make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                              KNeighborsRegressor(n_neighbors=max(2, min(7, n // 6)), weights="distance")),
                knn_cols)
    base = float(np.mean(np.abs(y - np.median(y))))
    rep = {"n": n, "mae": math.nan, "mae_median": base, "method": "gbm", "all": {}}
    if n >= 40:
        preds = {"gbm": np.zeros(n), "knn": np.zeros(n)}
        for tr, te in KFold(5, shuffle=True, random_state=0).split(X):
            for name, est in (("gbm", gbm), ("knn", knn)):
                e = clone(est) if name == "gbm" else _Cols(clone(knn.est), knn_cols)
                preds[name][te] = e.fit(X[tr], y[tr]).predict(X[te])
        preds["blend"] = 0.5 * (preds["gbm"] + preds["knn"])
        if noai_cols is not None:   # để so: nếu không có AI thì sai bao nhiêu
            cat2 = [False] * (len(noai_cols) - 1) + [n_cams > 1]
            g2 = clone(gbm).set_params(categorical_features=cat2 if any(cat2) else None)
            p2 = np.zeros(n)
            for tr, te in KFold(5, shuffle=True, random_state=0).split(X):
                p2[te] = g2.fit(X[tr][:, noai_cols], y[tr]).predict(X[te][:, noai_cols])
            rep["mae_no_ai"] = float(np.mean(np.abs(p2 - y)))
        rep["all"] = {k: float(np.mean(np.abs(p - y))) for k, p in preds.items()}
        rep["method"] = min(rep["all"], key=rep["all"].get)
        rep["mae"] = rep["all"][rep["method"]]
    gbm.fit(X, y)
    if rep["method"] == "gbm":
        return gbm, rep
    knn.fit(X, y)
    return (knn if rep["method"] == "knn" else _Blend(gbm, knn)), rep


def predict(model: dict, photos: list[raw.Photo], embs: np.ndarray | None = None) -> list[dict]:
    cams = model["cameras"]
    rows = []
    for i, p in enumerate(photos):
        m = p.model if p.model in cams else model["default_model"]
        rows.append({"feat": raw.features(p), "model": m,
                     "emb": None if embs is None else embs[i]})
    X, _ = _matrix(rows, cams, model.get("ai"), model["cols"])
    out = [dict() for _ in photos]
    for t, est in model["models"].items():
        pred = est.predict(X)
        lo, hi = LIMITS.get(t, (-100, 100))
        for o, v in zip(out, pred):
            o[t] = float(np.clip(v, lo, hi))
    return out


def save(model: dict, path: str | Path) -> None:
    """Ghi phong cách. Bản cũ được SAO LƯU (thư mục sao-luu, giữ 5 bản gần nhất) — không
    bao giờ mất dữ liệu đã học, kể cả khi cập nhật phiên bản mới."""
    import shutil
    import time
    path = Path(path)
    if path.exists():
        bk = path.parent / "sao-luu"
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bk / f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        for old in sorted(bk.glob(f"{path.stem}-*{path.suffix}"))[:-5]:
            old.unlink()
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(model, f)
    tmp.replace(path)


def load(path: str | Path) -> dict:
    # Chỉ mở file phong cách do chính bạn tạo ra (pickle).
    with open(path, "rb") as f:
        m = pickle.load(f)
    if m.get("format") != FORMAT_VERSION:
        raise SystemExit("File phong cách không đúng phiên bản, hãy học lại.")
    m["templates"] = {k: xmp.clean_template(v) for k, v in m.get("templates", {}).items()}
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


def group_scenes(photos: list[raw.Photo], gap_minutes: float = 4.0, embs: np.ndarray | None = None,
                 min_sim: float = 0.72) -> list[int]:
    """Chia cảnh theo giờ chụp: cách nhau > gap phút là sang cảnh mới.
    Có AI thì thêm: nội dung đổi hẳn (vd từ nhà thờ ra sân) và cách nhau > 1 phút
    cũng tính là cảnh mới."""
    order = sorted(range(len(photos)), key=lambda i: photos[i].time or 0)
    scene = [0] * len(photos)
    cur, last, ref = 0, None, None
    for i in order:
        t = photos[i].time or 0
        new = last is not None and t - last > gap_minutes * 60
        if not new and embs is not None and ref is not None and last is not None and t - last > 60:
            new = float(np.dot(embs[i], ref) / (np.linalg.norm(ref) + 1e-9)) < min_sim
        if new:
            cur += 1
            ref = None
        scene[i] = cur
        if embs is not None:
            e = embs[i].astype(np.float32)
            ref = e if ref is None else 0.7 * ref + 0.3 * e
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
