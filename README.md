# Editer

Hai tool AI chạy trên Windows cho thợ chụp/quay cưới và sự kiện:

1. **Editer Ảnh** — tự chỉnh màu theo phong cách riêng, học từ file `.xmp` Camera Raw
   của các job cũ, ghi ra `.xmp` để mở lại trong Camera Raw/Bridge chỉnh tiếp.
2. **Editer Video** — lọc footage, ghép theo nhịp nhạc thành bản dựng thô 5–10 phút,
   xuất ra project CapCut.

Cả hai điều khiển bằng chat. Ảnh RAW và footage gốc **không rời máy**.

## Editer Ảnh (bản 0.2)

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
3. **Chỉnh theo JPG mẫu**: từng ảnh được chỉnh cân bằng trắng + exposure theo ảnh mẫu.
   Cả lô dùng chung Tone Curve + HSL + Color Grading, tính từ toàn bộ lô so với ảnh mẫu.
   Có thể giữ độ nét, khử nhiễu và mask da/tóc từ phong cách của bạn.

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
- Tool không có bộ dựng ảnh của Camera Raw. Khớp màu được ước lượng trên ảnh xem trước
  nhúng trong RAW, nên là điểm xuất phát rất gần, không phải khớp tuyệt đối.
- Mask AI (da, tóc) được mang sang ảnh mới, nhưng chưa thử thực tế việc Camera Raw có
  tự tính lại mask hay không.

Chạy test: `python tests/test_match.py` và `python tests/test_e2e.py <thư mục tạm>`.

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
