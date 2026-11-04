# Editer

Hai tool AI chạy trên Windows cho thợ chụp/quay cưới và sự kiện:

1. **Editer Ảnh** — tự chỉnh màu theo phong cách riêng, học từ file `.xmp` Camera Raw
   của các job cũ, ghi ra `.xmp` để mở lại trong Camera Raw/Bridge chỉnh tiếp.
2. **Editer Video** — lọc footage, ghép theo nhịp nhạc thành bản dựng thô 5–10 phút,
   xuất ra project CapCut.

Cả hai điều khiển bằng chat. Ảnh RAW và footage gốc **không rời máy**.

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
| `cong-cu/` | Script tiện ích (thu thập mẫu…) |
| `mau/` | Dữ liệu mẫu bạn gửi lên |
