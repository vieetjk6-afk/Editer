# Phân tích mẫu lần 1 (release `mau`, 04/11/2026)

Dữ liệu gốc không đưa vào git (ảnh/tên file của khách). Đây chỉ là kết luận.

## Máy
RTX 3060 12GB, RAM 64GB, Windows 11 Pro. CapCut 9.7.0.4153.

## Ảnh: 397 file XMP (Nikon Z5 II, một job)

**Look nền gần như cố định trên mọi ảnh:**
- Profile `DVLOP III`.
- HSL cố định (ví dụ Cam: Hue −11 / Sat −28 / Lum +7; Xanh lá: Sat −54 / Lum −55).
- Highlights −13, Shadows +5, Whites +16, Blacks +7, Texture +5, Clarity −5,
  Vibrance +41, Saturation −4. Độ lệch chuẩn dưới 1.

**Phần thay đổi theo từng ảnh** (đây là phần AI cần học):

| Thông số | Trung vị | Độ lệch | Khoảng |
|---|---|---|---|
| Temperature | 5650 | 562 | 3950 – 6450 |
| Exposure | +0.05 | 0.38 | −1.25 – +1.85 |
| Contrast | −7 | 6.9 | −7 – +11 |
| Tint | +23 | 3.4 | +11 – +26 |

**Mask cục bộ:**
- 396/397 ảnh có mask AI "Person 1 - Hair" (chỉnh tóc).
- 115 ảnh có mask Linear Gradient.
- 2 ảnh có mask Radial.

Mask AI người không ghi sẵn vào XMP được, vì Camera Raw phải tự tính lại mask trên từng ảnh.
Cần thử: ghi định nghĩa mask vào XMP rồi xem Camera Raw có tự tính lại không.

**Kết luận:** tool ảnh = áp look nền + đoán Temperature, Tint, Exposure, Contrast theo
từng ảnh và từng cảnh. Bài toán gọn, dữ liệu phù hợp. Cần thêm XMP từ nhiều job khác
(Sony ARW, Canon CR3, các bối cảnh ánh sáng khác nhau).

## Video: 4 project CapCut

| Project | Lưu lần cuối bằng | draft_content.json |
|---|---|---|
| My Le | 9.3.5 (22/08) | JSON đọc được |
| 0907 | 9.4.0 (11/09) | JSON đọc được |
| 0827 | ? | mã hoá |
| NHUNG | 9.7 (04/11) | mã hoá |

Có vẻ các bản CapCut mới mã hoá project khi lưu. Hướng xử lý: **không giải mã**
(đó là cơ chế bảo vệ của CapCut). Tool chỉ **ghi project mới ở dạng JSON thường**,
với điều kiện CapCut 9.7 vẫn mở được dạng này. Cần thử trước.

**Cách dựng của bạn (My Le, phim cưới 10'15", 1080p30):**
- Track chính: 174 đoạn, trung bình 3.5s/đoạn (0.6 – 19s).
- 3 track video phụ (overlay), 1 adjustment layer phủ cả phim.
- Nhạc chính: "Bèo dạt mây trôi (Epic)", thêm tiếng động môi trường và âm thanh gốc của clip.
- Có dùng beat marker, HSL trên 79 clip, 15 transition.

Từ các project đọc được (cũ) có thể học được nhịp cắt, tỉ lệ cảnh, cách dùng nhạc.
Với project mới (mã hoá) thì học từ file video đã xuất.
