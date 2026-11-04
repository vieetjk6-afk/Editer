"""Tải các mô hình AI (ONNX) cho Editer Ảnh.

Chạy lúc đóng gói (GitHub Actions) hoặc một lần trên máy có mạng:
    python packaging/get_model.py models
Kết quả:
  models/clip-vision.onnx + clip-labels.npz   — CLIP: "dấu vân tay" nội dung + nhãn cảnh
  models/seg-canh.onnx                        — SegFormer-B0 ADE20K: trời, cây, nước, người...
  models/seg-nguoi.onnx (+ .fp16.onnx cho card đồ hoạ) — SegFormer-B2: mặt, tay, tóc, váy...
  models/seg-nhan.json                        — tên các vùng của hai mô hình phân vùng
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
SEG_REPOS = {   # tên file -> (repo, các biến thể cần tải)
    "seg-canh": ("Xenova/segformer-b0-finetuned-ade-512-512", {"model_quantized": ""}),
    "seg-nguoi": ("Xenova/segformer_b2_clothes", {"model_quantized": "", "model_fp16": ".fp16"}),
}


def fetch(name: str, dest: Path, repo: str = REPO) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    print("tải", repo, name, flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/main/{name}", timeout=300) as r, \
            open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


def list_files(repo: str) -> list[str]:
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}", timeout=60) as r:
        return [s["rfilename"] for s in json.load(r)["siblings"]]


def get_segmenters(out: Path, cache: Path) -> None:
    from editer_anh.ai import SEG_LABELS
    names = {}
    for fname, (repo, variants) in SEG_REPOS.items():
        files = list_files(repo)
        for variant, suffix in variants.items():
            src = f"onnx/{variant}.onnx"
            if src not in files:
                raise SystemExit(f"{repo} không có {src}: {files}")
            shutil.copy2(fetch(src, cache / f"{fname}{suffix}.onnx", repo), out / f"{fname}{suffix}.onnx")
        cfg = json.loads(fetch("config.json", cache / f"{fname}-config.json", repo).read_text(encoding="utf-8"))
        id2label = cfg["id2label"]
        names["canh" if fname == "seg-canh" else "nguoi"] = [id2label[str(i)] for i in range(len(id2label))]
    (out / SEG_LABELS).write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    print("nhãn phân vùng:", {k: len(v) for k, v in names.items()}, "| người:", names.get("nguoi"), flush=True)


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
    print("text model: vào", in_names, "ra", outs, "-> dùng", out_name, flush=True)

    def text_emb(prompt: str) -> np.ndarray:
        ids = np.array([tk.encode(prompt).ids], dtype=np.int64)
        feed = {"input_ids": ids}
        if "attention_mask" in in_names:
            feed["attention_mask"] = np.ones_like(ids)
        e = sess.run([out_name], feed)[0]
        if e.ndim == 3:  # last_hidden_state: lấy vị trí token kết thúc (EOS)
            e = e[0, int(np.argmax(ids[0]))]
        e = e.reshape(-1)
        return e / np.linalg.norm(e)

    print("token mẫu:", tk.encode("a photo of a cat").ids, flush=True)
    embs = [text_emb(p) for _, _, p in LABELS]
    np.savez(out / LABELS_NAME, keys=np.array([k for k, _, _ in LABELS]), emb=np.array(embs, np.float32))
    shutil.copy2(vis, out / MODEL_NAME)
    _sanity(out, text_emb)
    get_segmenters(out, cache)
    _sanity_seg(out)
    shutil.rmtree(cache)
    print("xong:", out / MODEL_NAME, f"{(out / MODEL_NAME).stat().st_size / 1e6:.0f}MB", flush=True)


def _sanity(out: Path, text_emb) -> None:
    """Kiểm tra cơ bản: ảnh con mèo phải giống câu 'con mèo' nhất."""
    try:
        import skimage.data as d
    except ImportError:
        return
    from editer_anh.ai import Embedder
    Embedder._inst = None
    e = Embedder(out)
    img = e.embed([d.chelsea(), d.astronaut()])
    prompts = ["a photo of a cat", "a photo of a dog", "a photo of a car", "a photo of a woman", "a photo of a coffee cup"]
    T = np.array([text_emb(p) for p in prompts])
    sc = img @ T.T
    for name, row in zip(("chelsea (mèo)", "astronaut (người)"), sc):
        print(name, {p: round(float(v), 3) for p, v in zip(prompts, row)}, flush=True)
    assert int(np.argmax(sc[0])) == 0, "CLIP lỗi: ảnh mèo không khớp 'a photo of a cat'"
    assert int(np.argmax(sc[1])) == 3, "CLIP lỗi: ảnh người không khớp 'a photo of a woman'"
    print("kiểm tra CLIP: OK", flush=True)


def _sanity_seg(out: Path) -> None:
    """Kiểm tra cơ bản phân vùng: ảnh phi hành gia phải có da, ảnh tên lửa phải có trời."""
    try:
        import skimage.data as d
    except ImportError:
        return
    from editer_anh import look as L
    from editer_anh.ai import Segmenter
    seg = Segmenter(out)
    print(f"phân vùng: {seg.device}, {seg.sec * 1000:.0f} ms/ảnh", flush=True)
    for name, img, code, need in (("astronaut", d.astronaut(), L.SKIN, 0.01), ("rocket", d.rocket(), L.SKY, 0.10)):
        m = seg.segment(img)
        frac = {L.REGION_VI.get(c, "khác"): round(float(np.mean(m == c)), 3) for c in np.unique(m)}
        print(f"  {name}: {frac}", flush=True)
        assert np.mean(m == code) >= need, f"phân vùng lỗi ở ảnh {name}"
    print("kiểm tra phân vùng: OK", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "models")
