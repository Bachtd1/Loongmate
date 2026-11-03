# LoongMate cho TrimUI Smart Pro S — Hướng dẫn sử dụng

Bản **riêng** cho TrimUI Smart Pro S chạy **TrimUI OS gốc**, tách khỏi bản Leaf (Miniloong Pocket 1). Tải đúng gói:

| Máy | Gói trong [Releases](../../../releases) |
|---|---|
| Miniloong Pocket 1 (Leaf) | `LoongMate-AllInOne.zip` |
| TrimUI Smart Pro S (TrimUI OS) | `LoongMate-TrimUI-<phiên bản>.zip` |

Không chép gói TrimUI lên Pocket 1 và ngược lại.

## Cài đặt

1. Tắt máy, cắm thẻ nhớ TrimUI vào máy tính.
2. Giải nén `LoongMate-TrimUI-<phiên bản>.zip`, chép hai thư mục **`Apps`** và **`System`** vào gốc thẻ (gộp với thư mục có sẵn).
3. Mở `Apps/show.json` trên thẻ, thêm hai mục vào danh sách (nếu chưa có):
   ```json
   {"label": "LoongMate", "show": 1},
   {"label": "Tailscale", "show": 1}
   ```
4. Cắm thẻ vào máy, khởi động lại. Trong **Apps** có **LoongMate** và **Tailscale**.

Lần mở LoongMate đầu tiên sau mỗi lần bật máy chậm khoảng 5 giây (giải nén Python đi kèm vào RAM — thẻ FAT32 không chạy được).
Lần đầu cài, LoongMate thêm lõi **"mGBA Viet hoa (LoongMate)"** cho GB / GBC / GBA và đặt làm mặc định; cấu hình cũ được lưu ở
`Emus/<hệ>/config.json.truoc-loongmate`. Lõi này vẫn là mGBA, game thường chạy như cũ.

## Dùng

### Việt hoá game GB / GBC / GBA
1. **LoongMate → Dịch game → chọn game → Dịch cả game.** Có Wi-Fi: dùng AI (API key của bạn) hoặc Google Dịch; không mạng: tải
   **gói dịch offline** một lần (Dịch game → Tải gói, khoảng 136 MB).
2. Xong thì **Chơi bản Việt hoá**, thoát LoongMate, vào hệ máy của game (GB / GBC / GBA) và mở game.
3. Bản Việt hoá chỉ nằm trong RAM tới khi tắt máy. ROM gốc trên thẻ không đổi; LoongMate không xuất file ROM Việt hoá.
4. Game chưa có dấu trong ROM thì lõi "mGBA Viet hoa" vẽ dấu lên chữ (dấu phủ). Một vài trang đầu mỗi lần chơi có thể chưa có dấu
   (lõi đang học hình chữ của game).

### Tailscale (thư viện / truy cập máy từ xa)
- **LoongMate → Máy → Vào tailnet (quét QR)**: quét mã bằng điện thoại, đăng nhập tài khoản Tailscale, bấm Connect. **Giữ máy bật
  và có Wi-Fi tới khi màn hình báo xong** (máy ngủ là mất Wi-Fi). Từ lần sau Tailscale tự kết nối khi bật máy.
- Kernel TrimUI không có TUN nên Tailscale chạy kiểu userspace: chiều vào máy dùng được; chiều ra (máy gọi máy khác) qua proxy
  `localhost:1055`.
- Tailscale SSH (vào máy bằng root qua tailnet) **tắt mặc định**; bật bằng cách tạo file rỗng `Apps/Tailscale/ssh-on` trên thẻ.

### Các chức năng khác (giống bản Pocket 1)
Sao lưu / khôi phục save (SaveVault), kiểm sức khoẻ máy, lịch sử pin & nhiệt, kiểm BIOS, danh sách ROM, thiết bị trong mạng,
LoongMate Agent chạy nền khi bật máy (`System/starts/loongmate.sh`: sao lưu save, ghi pin/nhiệt). Log:
`.userdata/trimui/LoongMate/logs/`.

## Khác bản Pocket 1 (Leaf)

| Chức năng | TrimUI | Lý do |
|---|---|---|
| Hot Tabs, Tự tiếp tục game | Không có | Dựa vào launcher Leaf; MainUI của TrimUI là chương trình đóng |
| Khoá khi thức dậy | Không có | Cần tay cầm ảo của Leaf |
| Tua nhanh, Cấu hình Leaf | Không có | Ghi vào `retroarch.cfg` / cấu hình của Leaf |
| Quét thư viện | Có, nhưng không gợi ý đổi tên / gộp thư mục hệ máy | TrimUI dùng tên thư mục riêng (PS, FC, SFC, Imgs...) |
| Thư viện riêng (PocketBridge) | Cùng Wi-Fi: có; qua Tailscale: chưa | Tailscale userspace cần proxy |

Các mục chỉ có trên Leaf được ẩn khỏi giao diện trên TrimUI.

## Gỡ cài đặt
Xoá `Apps/LoongMate`, `Apps/Tailscale`, `System/starts/loongmate.sh`, bỏ hai mục trong `Apps/show.json`. Trong `Emus/GB`, `Emus/GBC`,
`Emus/GBA`: đổi `config.json.truoc-loongmate` thành `config.json` để trả lại như cũ.

## Miễn trừ trách nhiệm
Bản thử nghiệm (beta). Chỉ dùng với game bạn sở hữu hợp pháp; sao lưu save trước khi thử. LoongMate là phần mềm miễn phí, không
kèm mã nguồn, không phát hành bản dịch hay ROM nào. Xem thêm [README](../README.md#miễn-trừ-trách-nhiệm).
