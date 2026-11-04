# Editer

Hai tool AI chạy trên Windows cho thợ chụp/quay cưới và sự kiện:

1. **Editer Ảnh** — tự chỉnh màu theo phong cách riêng, học từ file `.xmp` Camera Raw
   của các job cũ, ghi ra `.xmp` để mở lại trong Camera Raw/Bridge chỉnh tiếp.
2. **Editer Video** — lọc footage, ghép theo nhịp nhạc thành bản dựng thô 5–10 phút,
   xuất ra project CapCut.

Cả hai điều khiển bằng chat. Ảnh RAW và footage gốc **không rời máy**.

## Editer Ảnh (bản 0.3)

**Tải về:** tab **Releases** → **Editer Ảnh** (bản mới nhất) → `Editer-Anh-Windows.zip`.
Giải nén, bấm đúp `editer-anh.exe`.
Không cần cài Python. Cách dùng xem `packaging/HUONG-DAN.txt`.

Có 3 chức năng:
1. **Học phong cách** từ các job đã chỉnh (RAW + `.xmp`). Look nền (profile, HSL, curve,
   mask AI) lấy từ một file `.xmp` tiêu biểu, riêng cho từng đời máy. Phần chỉnh riêng
   từng tấm (nhiệt độ màu, tint, exposure, contrast…) do mô hình học. Học xong tool báo
   độ lệch trung bình, đo bằng kiểm tra chéo trên chính ảnh của bạn.
2. **Chỉnh theo phong cách**: ghi `.xmp` cho thư mục RAW mới. Ảnh cùng cảnh (chia theo
   giờ chụp) được kéo về cùng tông màu.
3. **Chép màu theo JPG mẫu** (0.3, `editer_anh/look.py`):
   - AI phân vùng (SegFormer, ONNX) tìm **da, trắng/trung tính, trời, cây** trên ảnh mẫu và
     ảnh RAW. Các số đo cần khớp: màu da, điểm trắng, trời, cây, phân bố sáng tối, ám màu
     vùng tối/sáng, 8 dải màu.
   - Tìm thông số Camera Raw (Nhiệt độ màu, Tint, Exposure, Tone Curve, HSL, Color Grading,
     Saturation/Vibrance) bằng cách mô phỏng thanh trượt và tối ưu. Có phạt thay đổi lớn
     để không ra thông số cực đoan, và khoá độ dốc curve trong khoảng 0,5–1,8.
   - `--photoshop`: Photoshop (Camera Raw thật) dựng các ảnh đại diện, tool đo ΔE thật rồi
     sửa lặp lại. Mỗi lượt học "độ mạnh thật" của từng nhóm thanh trượt, bù chênh lệch
     thật/mô phỏng, dùng vùng tin cậy và bỏ lượt tệ hơn. Kết quả in ra là **ΔE đo trên ảnh
     Camera Raw dựng thật**.
   - Cân bằng trắng và exposure được chuyển từ ảnh đại diện sang cả lô, theo điểm trắng và
     độ sáng da của từng ảnh.
   - `--luu-preset TÊN`: lưu phần màu thành preset Camera Raw.

**AI hiểu ảnh (0.2):** mô hình CLIP (ONNX, ~90MB, chạy CPU trên máy, không cần mạng) biến
mỗi ảnh thành "dấu vân tay nội dung". Dấu vân tay này được dùng để:
- làm đầu vào cho mô hình học;
- tìm các ảnh cũ giống nhất (kNN);
- gắn nhãn và chia cảnh.

Với mỗi thông số, tool thử 3 cách (học quy luật / theo ảnh cũ giống nhất / kết hợp), kiểm
tra chéo rồi giữ cách sai ít nhất. Tool cũng báo độ lệch khi không có AI để so sánh.
**Học thêm:** dữ liệu đã học được lưu trong file phong cách. Lần sau tool chỉ đọc ảnh mới
hoặc ảnh có `.xmp` vừa sửa, và lưu lịch sử độ chính xác qua các lần học.

Dòng lệnh (cho người dùng nâng cao):

```
editer-anh hoc  <thư mục job>... -o phong-cach.editer
editer-anh chinh <thư mục RAW> --phong-cach phong-cach.editer [--thu] [--ghi-de]
editer-anh chinh <thư mục RAW> --mau-jpg mau.jpg [--do-manh 0.85]
```

Giới hạn hiện tại:
- Không có Photoshop thì khớp màu chỉ là ước lượng trên ảnh xem trước nhúng trong RAW.
- Thử với "Camera Raw giả" (cố tình lệch HSL/grading khoảng 1,6 lần so với mô phỏng): vòng
  kiểm tra đưa độ lệch trung bình từ khoảng 16 xuống khoảng 3–4 sau 6 lần dựng (da ΔE
  khoảng 1–2). Chưa đo với Camera Raw thật.
- Mask AI (da, tóc) được mang sang ảnh mới, nhưng chưa thử thực tế việc Camera Raw có
  tự tính lại mask hay không.
- Bản ≤ 0.2 ghi Color Grading bằng khoá Camera Raw không hiểu (`ColorGradeShadowHue`),
  nên phần đó không có tác dụng. Đã sửa ở bản 0.3 (`SplitToning...`).

Chạy test: `python tests/test_look.py`, `python tests/test_ps.py`,
`python tests/test_e2e.py <thư mục tạm>`. Test có AI cần mô hình: `python packaging/get_model.py models`
(cần mạng), hoặc mô hình giả `python tests/make_dummy_model.py <thư mục>` + `EDITER_MODELS=<thư mục>`.

## Gửi dữ liệu mẫu

1. Tải repo về: nút **Code → Download ZIP**, giải nén.
2. Mở thư mục `cong-cu`, **bấm đúp `thu-mau.bat`** và làm theo câu hỏi:
   - chọn 1–3 project CapCut (nên có 1 phim cưới đã dựng xong + 1 project thử nhỏ);
   - dán thư mục các job ảnh cũ có file `.xmp`.
3. Script tạo file `editer-mau-....zip` ngoài Desktop. Script chỉ đọc và copy,
   không sửa file nào; nó **không** copy video, nhạc hay ảnh.
4. Đưa file zip lên repo:
   - **Dưới 25MB**: vào repo trên GitHub → nhánh `claude/beautiful-bohr-vi7ua4`
     → thư mục `mau` → **Add file → Upload files** → kéo file zip vào → **Commit changes**.
   - **Trên 25MB**: tab **Releases → Draft a new release** → tag `mau-1` → kéo file zip
     vào ô đính kèm → **Publish release**.

Repo đang để **Private** — giữ nguyên như vậy, vì file `.xmp` có tên file và thông tin
máy ảnh của các job.

## Thư mục

| Thư mục | Nội dung |
|---|---|
| `editer_anh/` | Mã nguồn Editer Ảnh |
| `packaging/` | Đóng gói .exe + hướng dẫn |
| `tests/` | Test (có tạo file DNG giả lập) |
| `docs/` | Ghi chép phân tích |
| `cong-cu/` | Script tiện ích (thu thập mẫu…) |
| `mau/` | Dữ liệu mẫu bạn gửi lên |
