# Bảo mật

## Báo lỗ hổng

Đừng mở issue công khai cho lỗ hổng bảo mật. Dùng **Security → Report a vulnerability** (GitHub private vulnerability
reporting) của repo này. Ghi: phiên bản LoongMate và Leaf, cách tái hiện, ảnh hưởng.

Phản hồi đầu tiên trong vòng 7 ngày. Đây là dự án một người làm lúc rảnh, nên thời gian sửa tuỳ mức độ; lỗi có thể làm mất
dữ liệu hoặc chạy mã từ xa được ưu tiên trước.

## Phạm vi

Đáng báo:
- Ghi hoặc xoá file ngoài vùng cho phép (path traversal qua zip, `.cue`, `.m3u`, tên file từ web).
- Tải từ web hoặc Homebrew Hub truy cập được mạng nội bộ, hoặc chạy được nội dung tải về.
- Gói hỗ trợ lộ mật khẩu, khoá hoặc token.
- Thao tác làm mất save mà không có bản sao lưu.

Không thuộc phạm vi: người có quyền SSH vào máy (đã có toàn quyền), lỗ hổng của Leaf, RetroArch hay emulator.

## Phiên bản được hỗ trợ

Chỉ bản phát hành mới nhất.
