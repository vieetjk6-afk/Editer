"""Tải mô hình CLIP (ONNX, bản lượng tử hoá) và tính sẵn vector cho các nhãn cảnh.

Chạy lúc đóng gói (GitHub Actions) hoặc một lần trên máy có mạng:
    python packaging/get_model.py models
Kết quả: models/clip-vision.onnx + models/clip-labels.npz
"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editer_anh.ai import LABELS, LABELS_NAME, MODEL_NAME  # noqa: E402

REPO = "Xenova/clip-vit-base-patch32"
BASE = f"https://huggingface.co/{REPO}/resolve/main/"


def fetch(name: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    print("tải", name, flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(BASE + name, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


def pick(files: list[str], prefix: str) -> str:
    for cand in (f"onnx/{prefix}_quantized.onnx", f"onnx/{prefix}_int8.onnx", f"onnx/{prefix}.onnx"):
        if cand in files:
            return cand
    raise SystemExit(f"không thấy {prefix} trong {REPO}: {files}")


def main(out: str) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_tai"
    cache.mkdir(exist_ok=True)
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{REPO}", timeout=60) as r:
        files = [s["rfilename"] for s in json.load(r)["siblings"]]
    vis = fetch(pick(files, "vision_model"), cache / "vision.onnx")
    txt = fetch(pick(files, "text_model"), cache / "text.onnx")
    tok = fetch("tokenizer.json", cache / "tokenizer.json")

    import onnxruntime as ort
    from tokenizers import Tokenizer
    tk = Tokenizer.from_file(str(tok))
    sess = ort.InferenceSession(str(txt), providers=["CPUExecutionProvider"])
    in_names = [i.name for i in sess.get_inputs()]
    outs = [o.name for o in sess.get_outputs()]
    out_name = "text_embeds" if "text_embeds" in outs else outs[0]
    embs = []
    for _, _, prompt in LABELS:
        ids = np.array([tk.encode(prompt).ids], dtype=np.int64)
        feed = {"input_ids": ids}
        if "attention_mask" in in_names:
            feed["attention_mask"] = np.ones_like(ids)
        e = sess.run([out_name], feed)[0].reshape(-1)
        embs.append(e / np.linalg.norm(e))
    np.savez(out / LABELS_NAME, keys=np.array([k for k, _, _ in LABELS]), emb=np.array(embs, np.float32))
    shutil.copy2(vis, out / MODEL_NAME)
    shutil.rmtree(cache)
    print("xong:", out / MODEL_NAME, f"{(out / MODEL_NAME).stat().st_size / 1e6:.0f}MB", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "models")
