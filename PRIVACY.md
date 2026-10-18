# Quyền riêng tư

LoongMate không có telemetry, không có tài khoản, không tự gửi dữ liệu đi đâu.

## Khi nào app dùng mạng

Chỉ khi bạn chủ động chọn:

| Tính năng | Kết nối tới |
| --- | --- |
| Tải từ web | Đúng link bạn nhập (và trang chuyển hướng của nó) |
| Homebrew Hub | `hh3.gbdev.io` (danh mục homebrew) và link tải trong danh mục đó |
| Thư viện riêng | Máy chủ PocketBridge của chính bạn trên PC/NAS |
| Sao lưu ra PC/NAS | Máy bạn cấu hình |

## Dữ liệu lưu trên máy

Tất cả nằm trên thẻ nhớ của bạn: `.userdata/mlp1/LoongMate/` (snapshot save, nhật ký sửa, cấu hình, lịch sử pin/nhiệt)
và `Quarantine/` trên từng thẻ. Gỡ app: xoá hai thư mục `.pak`; xoá thêm `.userdata/mlp1/LoongMate/` nếu muốn bỏ cả dữ
liệu (snapshot save cũng nằm ở đó, hãy chắc là bạn không còn cần).

## Gói hỗ trợ (Home → Y)

Gói chỉ được tạo khi bạn bấm, lưu ở `.userdata/mlp1/LoongMate/Diagnostics/loongmate_support_<thời điểm>.zip`, không tự
gửi đi. Bên trong:

| File | Nội dung |
| --- | --- |
| `summary.json` | Thời điểm, trạng thái máy (pin, nhiệt), dung lượng thẻ; IP thay bằng `192.168.x.x` |
| `checks.txt` | Kết quả kiểm tra phần cứng/thẻ |
| `mounts.txt` | Bảng mount của hệ thống (`/proc/mounts`) |
| `library_issues.json` | Danh sách vấn đề thư viện; **tên ROM, ảnh, save được thay bằng `[ITEM_01]`…** |
| `logs.txt` | Tối đa 5 file log gần nhất của LoongMate (tổng ≤ 256 KB) |
| `environment.txt` | Các biến đường dẫn của Leaf, phiên bản Python |

Mọi file trong gói đi qua bộ lọc che: khoá riêng (`BEGIN … PRIVATE KEY`), header `Authorization`, JWT, `wpa_passphrase`,
và giá trị sau `password`, `psk`, `token`, `secret`, `api_key`… Tên file BIOS được giữ (không phải dữ liệu cá nhân).

Bộ lọc dựa trên mẫu nên không thể bảo đảm tuyệt đối: hãy mở gói xem trước khi đăng công khai.
