# Patch ROM — áp bản vá romhack lên game của bạn

LoongMate có thể áp các bản vá **.ips / .bps / .ups** (romhack, bản dịch của người hâm mộ, bản sửa lỗi...) lên ROM bạn đã có,
ngay trên máy. Kết quả là **một file ROM mới nằm cạnh bản gốc**; bản gốc không bao giờ bị sửa.

## Patch là gì

Một file patch không chứa game. Nó chỉ chứa *phần khác biệt* giữa ROM gốc và ROM đã chỉnh sửa. Vì vậy:

- Bạn phải có sẵn **ROM gốc do chính bạn sao lưu từ băng/đĩa của mình** (LoongMate không cung cấp ROM).
- Patch chỉ đúng với **một bản ROM gốc cụ thể** (đúng vùng, đúng phiên bản). Dùng nhầm bản thì game lỗi hoặc không chạy.

## Lấy patch ở đâu

Từ các cộng đồng romhack/dịch game: trang của tác giả, kho patch của cộng đồng... Thường là file `.ips`, `.bps`, `.ups`,
hoặc một file `.zip` chứa chúng (kèm file đọc-tôi). Hãy đọc mô tả của tác giả để biết patch dành cho bản ROM nào.

## Cách làm trên máy

1. **Lấy patch về.** Có hai đường:
   - *Tải từ web*: mở trang của tác giả, chọn link `.ips`/`.bps`/`.ups` (hoặc `.zip` chỉ có patch). Patch **không** bị cài vào
     thư mục game; nó được cất vào kho patch của LoongMate. Máy báo "Đã lưu N patch — mở Library → Patch ROM để áp dụng."
   - *Thả vào thẻ nhớ*: chép file patch thẳng vào thư mục `Import/` của thẻ (không để trong thư mục con). LoongMate chỉ đọc
     chúng ở đó, không di chuyển hay xoá.
2. Vào **Library → Patch ROM** và chọn patch.
3. Chọn ROM gốc:
   - Patch **BPS/UPS**: máy tự tìm trên cả hai thẻ ROM nào **khớp đúng** (so CRC32). Có ROM khớp thì chọn, không có thì báo
     rõ là chưa tìm thấy bản gốc phù hợp.
   - Patch **IPS**: IPS không có mã kiểm tra nên máy không tự biết ROM nào đúng. Bạn chọn hệ máy rồi chọn ROM bằng tay.
4. Xác nhận. Máy tạo file mới trong cùng thư mục `Roms/<hệ>/` với tên dạng `Tên game [tên patch].đuôi` rồi quét lại thư viện
   Leaf để game hiện ra. Nếu đã có file trùng tên: cùng nội dung thì báo "đã có", khác nội dung thì thêm ` (2)`, ` (3)`...

## Được kiểm tra những gì

| Định dạng | Kiểm tra |
|---|---|
| BPS, UPS | CRC32 của chính file patch (phát hiện tải/chép lỗi), **CRC32 và kích thước ROM gốc** (sai thì từ chối, không ghi gì), CRC32 của kết quả |
| IPS | Chỉ kiểm cấu trúc file. **Không kiểm được ROM gốc có đúng hay không** — máy hiện cảnh báo; nếu game lỗi, xoá file mới (bản gốc vẫn nguyên) |

Patch hỏng, bị cắt cụt hoặc cố ý làm hại đều bị từ chối bằng thông báo "Patch hỏng: ..." — không làm treo máy, không ghi file dở.
File `.zip` đi qua đúng bộ kiểm tra an toàn của Nhập game (chặn đường dẫn `..`, file thực thi, zip bomb) và chỉ lấy ra các file patch.

ROM SNES có **header copier 512 byte** (hay gặp ở file `.smc`) vẫn được nhận ra khi tìm bằng CRC: máy bỏ qua 512 byte đó để so,
áp patch lên phần còn lại rồi giữ nguyên header ở file mới.

## Giới hạn

- Chỉ hỗ trợ **.ips, .bps, .ups**. **xdelta (.xdelta/.vcdiff) và PPF chưa hỗ trợ.**
- IPS không có mã kiểm tra (xem trên).
- Patch ≤ 64 MiB; ROM gốc ≤ 128 MiB; ROM sau khi patch ≤ 128 MiB.
- ROM đang nằm trong file nén (`.zip` trong `Roms/`) phải giải nén trước.
- Chỉ áp lên file nằm trong `Roms/` của thẻ. Patch nằm trong `Import/` chỉ đọc được; muốn xoá phải xoá bằng tay.
- Chỉ xoá được patch trong kho của LoongMate.

## Điều khiển

- **Library → Patch ROM (romhack):** D-pad chọn patch, A = Áp dụng, X = Xoá patch (chỉ patch trong kho), B = quay lại.
- **Áp dụng BPS/UPS:** máy tự tìm ROM gốc khớp CRC trên cả hai thẻ → chọn ROM → hộp xác nhận (mặc định Huỷ) → A.
- **Áp dụng IPS:** chọn hệ máy → chọn game → hộp xác nhận (có cảnh báo không kiểm được) → A.
- File mới hiện trong danh sách game của Leaf sau khi quét lại (tự động).

## Dòng lệnh (PC/kiểm thử)

```
loongmate patch list --json
loongmate patch info ID --json
loongmate patch candidates ID [--system S] --json
loongmate patch apply ID --rom ĐƯỜNG_DẪN_ROM [--name TÊN] [--header-skipped] --yes --json
loongmate patch delete ID --yes --json
loongmate patch store ĐƯỜNG_DẪN_PATCH_HOẶC_ZIP --json
```

`ID` là `s:<tên file>` (kho patch) hoặc `i:<primary|secondary>:<tên file>` (thư mục `Import/`).
