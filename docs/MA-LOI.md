# Mã lỗi Library Doctor

Mỗi vấn đề trong **Thư viện → Danh sách vấn đề** có một mã `LM-…`, hiện ở dòng "Mã lỗi" trong khung Giải thích.
Mã không phụ thuộc tên game hay thẻ nhớ, nên khi hỏi trên GitHub hoặc Discord chỉ cần gửi mã (kèm gói hỗ trợ nếu được).
Mã đã công bố không đổi nghĩa; loại vấn đề mới sẽ nhận mã mới.

Mức độ: **Nghiêm trọng** cần xử lý ngay, **Cảnh báo** nên xử lý, **Ghi chú** là thông tin, không phải lỗi.

## Danh mục và thư viện

| Mã | Vấn đề | Làm gì |
| --- | --- | --- |
| `LM-CAT-001` | Không đọc được danh mục hệ máy | Cài lại LoongMate (file `res/systems.json` hỏng hoặc thiếu) |
| `LM-LIB-001` | Thiếu thư mục chuẩn (`Roms`, `Saves`…) trên một thẻ | Sửa an toàn: tạo thư mục |
| `LM-LIB-002` | Thư mục trong `Roms/` không thuộc hệ máy nào | Kiểm tên thư mục; đổi theo tên Leaf dùng |
| `LM-LIB-003` | Thư mục hệ cũ (ví dụ `CPS1`) mà bản Leaf đang chạy không hiện | Sửa hàng loạt: gộp vào thư mục Leaf đọc (có Hoàn tác) |
| `LM-LIB-004` | File có đuôi mà Leaf không hiện trong thư mục đó | Sửa hàng loạt: chuyển sang đúng thư mục hệ, hoặc kiểm lại file |
| `LM-LIB-005` | Hệ máy chưa có danh sách đuôi file nên chưa kiểm | Ghi chú, không cần làm gì |
| `LM-LIB-006` | Game nhiều đĩa chưa có playlist `.m3u` | Sửa an toàn: tạo `.m3u` |
| `LM-LIB-007` | Cùng một ROM nằm trên cả hai thẻ | Thư viện → Dọn ROM trùng (so SHA-256 trước khi cất) |

## Ảnh bìa và save

| Mã | Vấn đề | Làm gì |
| --- | --- | --- |
| `LM-ART-001` | Game chưa có ảnh bìa | Leaf: Games → Menu → Scrape Missing Artwork |
| `LM-ART-002` | Ảnh bìa không khớp game nào | Sửa an toàn: ghép ảnh lệch tên, ảnh thừa vào `Quarantine/` |
| `LM-SAVE-001` | Save/state không còn game tương ứng | Ghi chú; LoongMate không xoá save, kiểm thẻ còn lại hoặc game đã đổi tên |

## BIOS

| Mã | Vấn đề | Làm gì |
| --- | --- | --- |
| `LM-BIOS-001` | Thiếu BIOS: cần ít nhất một trong các file liệt kê | Chép BIOS bạn tự trích từ máy của mình vào `Import/`, rồi Nhập game |
| `LM-BIOS-002` | Thiếu BIOS tuỳ chọn | Ghi chú; game vẫn chạy, một số tính năng có thể thiếu |
| `LM-BIOS-003` | Hệ đang chạy bằng BIOS giả lập | Ghi chú; phần lớn game chạy được (xem COMPATIBILITY.md) |
| `LM-BIOS-004` | Thiếu BIOS bắt buộc | Như `LM-BIOS-001` |

## Thẻ nhớ

| Mã | Vấn đề | Làm gì |
| --- | --- | --- |
| `LM-SD-001` | Thẻ SD2 không phải FAT32 | Sao lưu thẻ ra máy tính, định dạng FAT32, chép lại |
| `LM-SD-002` | Chưa xác định được định dạng thẻ SD2 | Ghi chú; kiểm lại sau khi khởi động lại máy |
| `LM-SD-003` | Thẻ SD2 chỉ đọc | Kiểm khoá trên adapter thẻ; thẻ có thể sắp hỏng, sao lưu ngay |
| `LM-SD-004` | Thẻ sắp hết hoặc còn ít dung lượng | Dọn ROM trùng, chuyển game sang thẻ còn lại |

## Quét và playlist

| Mã | Vấn đề | Làm gì |
| --- | --- | --- |
| `LM-SCAN-001` | Gặp đường dẫn ngoài vùng an toàn (liên kết vòng, symlink) khi quét | Gửi gói hỗ trợ; LoongMate đã bỏ qua đường dẫn đó |
| `LM-SCAN-002` | Lỗi khi quét một thư mục | Kiểm thẻ trên máy tính (chkdsk), gửi gói hỗ trợ nếu còn lỗi |
| `LM-CUE-001` | File `.cue` trỏ ra ngoài thư mục game | Sửa an toàn: cất `.cue` vào `Quarantine/` |
| `LM-CUE-002` | File `.cue` trỏ sai tên track | Sửa an toàn: sửa tên trong `.cue` (bản cũ vào `Quarantine/`) |
| `LM-CUE-003` | Sai chữ hoa/thường trong tên track | Sửa an toàn: sửa tên trong `.cue` |
| `LM-CUE-004` | `.cue` thiếu file track | Tải lại đủ các file `.bin`, hoặc Sửa an toàn: cất `.cue` vào `Quarantine/` |
| `LM-M3U-001` | Playlist `.m3u` trỏ ra ngoài thư mục game | Sửa an toàn: cất `.m3u` vào `Quarantine/` |
| `LM-M3U-002` | Playlist `.m3u` thiếu đĩa | Chép đủ đĩa, hoặc Sửa an toàn: cất `.m3u` vào `Quarantine/` |

`LM-000` nghĩa là loại vấn đề chưa có mã: hãy báo lỗi để thêm mã.
