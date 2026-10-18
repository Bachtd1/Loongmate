# Thay đổi

## 0.1.0-beta — 2026-10-18

Bản công khai đầu tiên. Đã dùng thật trên Miniloong Pocket 1 với Leaf 0.11.0 và 0.13.0.

**Save**
- SaveVault: snapshot save/state của hai thẻ, lưu theo nội dung; khôi phục từng game hoặc cả bản, sao lưu hiện trạng
  trước khi ghi, nhật ký và rollback khi mất điện.
- Từ chối khôi phục/rollback khi emulator đang chạy.
- LoongMate Agent (service của Leaf): tự sao lưu sau khi thoát game, lịch sử pin/nhiệt, kiểm toàn vẹn hàng tuần.

**Thư viện**
- Library Doctor đọc danh mục hệ máy của chính Leaf: báo game Leaf không hiện (CPS1/2/3 ngoài ARCADE trên Leaf 0.13).
- Sửa an toàn (ghép ảnh bìa lệch tên, cất ảnh thừa, sửa `.cue`, tạo thư mục) và Sửa hàng loạt (gộp thư mục, chuyển game
  sai thư mục) có Hoàn tác.
- Ghi chú gom theo hệ máy; số game thiếu ảnh bìa lấy từ thư viện của Leaf, kèm hướng dẫn Scrape Missing Artwork.
- BIOS: kiểm md5, nhận biết hệ chạy được bằng BIOS giả lập (Dreamcast, PS1, GBA, NDS), BIOS Dreamcast đặt vào `BIOS/dc/`,
  báo file sai chỗ, cài BIOS từ `Import/`.
- Dọn ROM trùng giữa hai thẻ bằng SHA-256.
- Mã lỗi ổn định `LM-…` cho mỗi loại vấn đề (xem `docs/MA-LOI.md`).

**Thêm game**
- Nhập game từ `Import/`: nhận hệ bằng header, giải nén zip an toàn, chặn zip bom và file chạy được.
- Tải từ web: https, tải tiếp khi mất mạng, chặn địa chỉ nội bộ, gắn ảnh bìa khớp game từ trang tải.
- Homebrew Hub (chỉ game có giấy phép rõ), Thư viện riêng từ PC/NAS qua PocketBridge, Patch ROM IPS/UPS/BPS.
- Hỏi lưu vào SD1 hay SD2; tải tiếp luôn cài đúng thẻ chứa file tải dù hai thẻ đổi chỗ.
- Game mới tải về, Tìm kiếm game trên hai thẻ.

**Leaf**
- Sao lưu cấu hình Leaf/RetroArch, so từng khoá, khôi phục theo nhóm; tự chụp khi Leaf đổi phiên bản.
- Tự tiếp tục khi mở game từ Games/Favorites qua Agent, không vá Leaf (thử nghiệm, mặc định tắt).
- Gói kèm runtime Python, tự cài lại nếu bị xoá; sống sót qua cập nhật Leaf.
