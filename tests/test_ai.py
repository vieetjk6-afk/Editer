"""Test với mô hình CLIP THẬT (chạy trên máy build, nơi có mô hình)."""
import sys
from pathlib import Path

import numpy as np
import skimage.data as d

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editer_anh import ai  # noqa: E402

e = ai.Embedder.get()
assert e is not None, "không nạp được mô hình AI"
print("thiết bị:", e.device)
astro, coffee, cat = d.astronaut(), d.coffee(), d.chelsea()
v = e.embed([astro, astro[:, ::-1], coffee, cat])
sim = v @ v.T
print("giống nhau:\n", sim.round(2))
assert sim[0, 1] > 0.9, "ảnh lật ngang phải gần như giống hệt"
assert sim[0, 1] > sim[0, 2] + 0.1, "ảnh khác nội dung phải khác xa hơn"
for name, x in (("astronaut", v[0]), ("coffee", v[2]), ("chelsea", v[3])):
    print(name, "->", e.top_labels(x, 4))
assert "chân dung" in e.top_labels(v[0], 4), "ảnh phi hành gia phải được nhận là chân dung"
print("AI OK")
