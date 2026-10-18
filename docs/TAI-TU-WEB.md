# Tải từ web

Mở một trang web https bất kỳ ngay trên Pocket 1, xem danh sách link (và ô tìm kiếm nếu trang có), chọn link file rồi tải. File được kiểm tra và cài vào đúng thư mục `Roms/<hệ máy>` bằng **Nhập game** — không có đường cài thứ hai.

Đây là công cụ chung, giống trình quản lý tải / trình duyệt chữ. Nó không có mã riêng cho trang nào.

> Chỉ tải nội dung bạn có quyền sử dụng (game tự làm, homebrew, bản sao lưu game bạn sở hữu hợp pháp). Bạn chịu trách nhiệm về những gì mình tải.

## Cách dùng

1. Library → **Tải từ web**.
2. Gõ địa chỉ trang (không cần gõ `https://`), hoặc chọn một dấu trang / trang gần đây / link trong `Import/links.txt`.
3. Trang hiện ra dưới dạng danh sách link. Link file (ROM, `.zip`, `.7z`, `.rar`, hoặc link có thuộc tính tải) được đánh dấu riêng. Nếu trang có ô tìm kiếm đơn giản, chọn nó và gõ từ khoá.
4. Chọn link file → xem kế hoạch (tên, hệ máy, dung lượng, thẻ đích) → xác nhận. Tải chạy nền có phần trăm; mất mạng thì tiếp tục được từ chỗ dừng.

**Gom nhóm và ẩn link rác (quy tắc chung, không riêng trang nào):** link file luôn hiện trước, rồi link có chữ "Download/Tải về/Mirror..." hoặc link dẫn tới trang tải (`/download/`), rồi các link còn lại. Link trong menu/đầu trang/chân trang/thanh bên được xếp riêng vào nhóm "điều hướng" (trừ link phân trang). Link quảng cáo (`rel="sponsored"`) và link tới mạng xã hội/mạng quảng cáo từ trang khác bị ẩn, trang cho biết đã ẩn bao nhiêu.
Nút tải không phải thẻ `<a>` nhưng có link sẵn trong HTML (`data-download-url`) vẫn được nhận thành link file. Nếu trang không có link file nào nhưng có nút "Download" chạy bằng JavaScript/đếm ngược, trang hiện cảnh báo: LoongMate không chạy JavaScript và không vượt đếm ngược, hãy lấy link tải trên PC rồi dán vào `Import/links.txt`.

### Điều khiển

- **Màn Tải từ web và màn trang:** D-pad chọn, Trái/Phải lật trang danh sách, L1/R1 cuộn khung giải thích, A = Mở / Tải / Tìm / Lưu,
  Y = xoá dấu trang, X = huỷ job tải dở (màn Tải từ web) / **về đầu** (trên trang: đóng mọi trang đang mở, về màn Tải từ web; cũng có mục "Về trang chủ Web" trong mỗi trang),
  B = quay lại (về trang trước). Duyệt sâu không bị chặn: từ trang thứ ~12 trở đi, trang cũ nhất ngay sau màn Tải từ web tự đóng bớt.
- **Chọn thẻ đích (SD1 / SD2):** khi cả hai thẻ đều ghi được và đủ chỗ, màn **CHỌN THẺ LƯU GAME** hiện ra (chung cho Tải từ web, Homebrew, Thư viện riêng, Nhập game): A chọn SD2 (thẻ phụ) hoặc SD1 (thẻ Leaf). Chỉ một thẻ dùng được thì dùng luôn thẻ đó; thẻ phụ không dùng được thì hỏi trước khi dùng thẻ Leaf.
- **Hộp xác nhận tải:** mặc định Huỷ; hiển thị rõ thẻ đích đã chọn (`SD1` hoặc `SD2`), Trái/Phải chọn nút, A bấm. Xem dòng **Máy chủ thật**: nếu là tên lạ (trang quảng cáo) thì Huỷ.
- **Đang tải:** B = tạm dừng (tải tiếp ở mục Tải dở).
- **Bàn phím:** D-pad di chuyển, A gõ phím, B xoá lùi (giữ để xoá nhanh; ô trống thì không thoát), X dấu cách,
  Y đổi bàn phím (a-z / A-Z / ký hiệu), L1/R1 dời con trỏ, SELECT nhảy tới XONG. Bấm XONG để mở, HUỶ để bỏ.

### Mẹo `Import/links.txt`

Gõ địa chỉ dài trên máy cầm tay rất mệt. Trên PC, tạo file `links.txt` trong thư mục `Import/` ở gốc thẻ (thẻ chính hoặc thẻ phụ), mỗi dòng một địa chỉ `https://...`. Dòng bắt đầu bằng `#` là chú thích; dòng không phải https bị bỏ qua. Tối đa 50 link, file đọc tối đa 64 KiB. Các link này hiện ở màn hình đầu của Tải từ web.

## Dòng lệnh (cho kiểm tra/giao diện native)

```
loongmate web home --json
loongmate web open URL --json
loongmate web fetch URL... [--card auto|primary|secondary] [--conflict keep|replace|copy|cancel] [--dry-run] [--yes] [--progress] --json
loongmate web jobs | resume JOB --yes | cancel JOB --yes
loongmate web bookmark add URL [--title T] | bookmark remove URL | history clear
```

Dữ liệu duyệt (lịch sử tối đa 50, dấu trang tối đa 100) nằm ở `<userdata>/LoongMate/web.json`; file hỏng thì bắt đầu lại từ trống.

## Những gì được kiểm tra

- **Chứng thư HTTPS** kiểm tra đầy đủ bằng bộ CA của máy. Chứng thư sai/không tin cậy thì dừng, không tải.
- **Chỉ https.** Địa chỉ `http://` bị từ chối; link `http://` trong trang bị bỏ và đếm lại.
- **Chặn địa chỉ nội bộ.** Trang web không thể khiến máy gọi vào localhost, mạng LAN (10.x, 192.168.x, 172.16–31.x), link-local, CGNAT/Tailscale (100.64/10), IPv6 nội bộ. Mọi bước chuyển hướng đều bị kiểm tra lại.
- **Trang HTML thay vì file** (trang quảng cáo/đếm ngược/đăng nhập) bị từ chối ở cả bước hỏi lẫn lúc tải; bản tải dở bị xoá, không cài.
- **Nhập game** chặn file chạy được/script, kiểm zip bomb, nhận hệ máy theo đuôi/header, cài kiểu giao dịch.
- **Giới hạn kích thước:** tối đa 2 GiB mỗi file (giới hạn FAT32/Nhập game); máy chủ không báo kích thước thì không tải. Cần trống khoảng 2 lần dung lượng + 128 MB trên thẻ đích.
- **Tải bền:** có `.part`, tiếp tục bằng Range + If-Range; nội dung đổi giữa chừng thì tải lại từ đầu thay vì ghép. Ghi SHA-256 của file đã tải.
- Mỗi trang tối đa 3 MiB, tối đa 6 lần chuyển hướng, tối đa 400 link/trang, ≤ 2 yêu cầu/giây. User-Agent trung thực (`LoongMate/1`), không giả danh trình duyệt.

## Giới hạn

- **Không chạy JavaScript**, không cookie, không đăng nhập, không gửi form POST. Trang cần các thứ đó sẽ không dùng được.
- **Không vượt** quảng cáo, đếm ngược, captcha hay chống bot.
- **`.7z`, `.rar`** (và các nén khác ngoài `.zip`) chưa giải nén được trên máy: tải bằng PC, giải nén rồi đưa vào `Import/`.
- Chỉ form tìm kiếm GET có đúng một ô nhập chữ được hỗ trợ (tối đa 5 form/trang).
- Máy chủ trả dữ liệu nén dù đã yêu cầu không nén: không đọc được trang đó.
- Rủi ro còn lại: tên miền được phân giải lại lúc nối socket (DNS rebinding) không chặn được ở tầng này.

## Chạy test

`python -m unittest tests.test_web` (không cần Internet: máy chủ HTTPS loopback tự ký + thẻ giả + importer thật).

Chứng minh test biết đỏ: tạm làm hỏng rồi chạy lại, test tương ứng phải đỏ — kiểm `is_global` trong `_check_ip` (engine/web.py) → nhóm `TestSsrf`/`TestRedirectHop` đỏ; điều kiện từ chối `text/html` trong `homebrew.download_file` → `test_html_served_at_download_time_is_removed_and_not_installed` đỏ; chuỗi `={q}` trong `parse_page` → `test_get_form_template_with_hidden_and_placeholder` đỏ.
