# LoongMate 0.5.2-beta — Ghi chú phát hành

Bản sửa lỗi cho Việt hoá GB/GBC/GBA và lõi dấu phủ. Các phần khác (Tua nhanh, Việt hoá nạp một lần, Hot Tabs, hướng dẫn
PocketBridge, miễn trừ trách nhiệm) giữ như [0.5.1-beta](PHAT-HANH-0.5.1.md).

## Đã sửa

- **Bản dịch mất hết dấu** (máy dịch trả "Chao mung" thay cho "Chào mừng") giờ được nhận ra: AI được hỏi lại ngay lúc dịch, và
  **Sửa câu lỗi** đếm + dịch lại các trang đó. Trước đây lớp phủ không có dấu để vẽ (Dragon Warrior I & II: 103/1277 đoạn).
- **Dấu câu lạ giữa câu** (":", "!", ". " chen vào chỗ xuống dòng): bước dò khung chữ từng nhận nhầm dấu hai chấm sau tên người
  nói / dấu câu là mã sang trang. Sửa cho Dragon Warrior I & II, Dragon Warrior III, Dragon Warrior Monsters, Harvest Moon GB.
- **Câu một từ** ("Welcome......", "Incredible!") không còn bị coi là tên nhân vật nên được dịch; tên người nói thật ("King",
  "Lora") vẫn giữ tiếng Anh.
- **Dấu phủ**: dòng có tên người nói dính liền câu ("King:Chào nhé" — không có khoảng trống sau dấu hai chấm) giờ vẽ được dấu.
  Đo trên 84 game: 17 game ghép được nhiều chữ hơn, không game nào kém đi, thời gian mỗi khung như cũ.
- **Sigma Star Saga**: lời thoại được dời theo cả khối chương vào chỗ trống trong ROM (game không đọc chữ ngoài cỡ ROM gốc) —
  từ **135 lên 1319/1408 trang** tiếng Việt, ROM vẫn 8 MB. Sửa luôn lỗi bản cũ dời riêng câu đầu mỗi chương (game có thể đọc
  nhầm vào vùng rác khi tới lời thoại chương).

## Đi kèm và hướng dẫn

**Leaf Hot Tabs** (mở game từ Games / Favorites là vào thẳng chỗ đang chơi dở) — có sẵn trong `LoongMate-AllInOne.zip`, phải
đúng phiên bản Leaf (Menu → Info → Device):

| Leaf | Thư mục trong gói | Làm gì |
|---|---|---|
| **v0.12.1** | `THE-NHO/hottabs/` | đã đi kèm khi chép `THE-NHO` vào gốc thẻ |
| v0.11.0 | `HotTabs-ban-khac/v0.11.0/hottabs/` | xoá `hottabs/` cũ trên thẻ, chép thư mục này vào gốc thẻ |
| bản khác | — | không dùng Hot Tabs (LoongMate báo không khớp, không cho cài) |

Cài: **LoongMate → Máy → Tự tiếp tục game → Cài lại Hot Tabs** → xác nhận → **khởi động lại máy**. Gỡ ở cùng màn đó. Chi tiết:
[docs/HOT-TABS.md](HOT-TABS.md).

**Thư viện game riêng qua PocketBridge (+ Tailscale)** — duyệt và cài game từ thư mục ROM trên PC / Mac / Linux / Raspberry Pi
của bạn, ngay trên Pocket 1:

1. Tải PocketBridge Agent cho máy chứa ROM: https://github.com/Bachtd1/pocket-bridge/releases (Windows / macOS / Linux-Pi).
2. Cài agent (thay `<IP>` bằng IP LAN hoặc IP Tailscale `100.x.y.z`):
   - Windows (PowerShell Administrator): `.\install.ps1 -Listen <IP> -Id pocket1 -Scopes library:read`
   - Linux / Pi: `sudo sh linux/install.sh --listen <IP> --id pocket1 --scopes library:read`
   - macOS: `sudo sh macos/install.sh --listen <IP> --id pocket1 --scopes library:read`
3. Khai báo thư mục ROM trong `config.yaml` của agent (mục `library: roots:`), thư mục con đặt tên theo hệ máy Leaf
   (`GB`, `GBC`, `GBA`, `SNES`, `PSX`…), rồi khởi động lại agent.
4. Chép file ghép đôi (`pairing-bundle-pocket1.json`) vào `<thẻ>/PocketBridge-import/` (dùng một lần, hết hạn sau 15 phút).
5. Trên Pocket 1: **LoongMate → Thư viện → Thư viện riêng → Ghép đôi**, chọn file, xác nhận. Sau đó duyệt thư mục → hệ máy →
   game, A "Tải & cài".
6. Ở xa nhà: cài **Tailscale** trên máy chứa ROM và Pocket 1, cùng một tailnet, dùng IP `100.x.y.z` ở bước 2.

Chi tiết (đường dẫn cấu hình từng hệ điều hành, tải tiếp khi mất Wi-Fi, an toàn):
[docs/POCKETBRIDGE.md](POCKETBRIDGE.md).

## Số đo

- Dragon Warrior I & II (dịch AI trên Pocket 1): 1626/1752 đoạn Việt hoá; cảnh nhà vua đầu game hiện tiếng Việt, dấu phủ vẽ trên
  phần lớn các dòng.
- Sigma Star Saga (dịch AI trên Pocket 1): 1319/1408 trang trong ROM; game vào màn tiêu đề, đoạn mở đầu và màn chơi đầu không treo.
- Kiểm tự động trên PC: các game bị đổi cách dò khung chữ / cách ghi (Dragon Warrior I–III, Dragon Warrior Monsters 1–2, Harvest
  Moon GB, Metal Walker, Sigma Star Saga, Star Wars: Flight of the Falcon, Castlevania: Circle of the Moon, Final Fight One,
  Mother 3, Mega Man Battle Network 5) vẫn khởi động và chơi được với bản Việt hoá. Bản này không quét lại cả 573 ROM; các thay đổi
  chỉ đổi kết quả ở những game trên (đã dò trên toàn bộ ROM).

## Giới hạn còn lại

- **Lần chơi mới**: lõi học lại hình chữ từ đầu nên một hai trang thoại đầu tiên có thể chưa có dấu; các trang sau có dấu.
  Mang hình chữ đã học sang lần chơi sau đã thử nhưng làm kém đi ở 10/84 game, nên chưa bật.
- **Ô chữ cố định** (đoạn mở đầu Sigma Star Saga, bảng mô tả vật phẩm): câu dịch dài hơn ô giữ tiếng Anh.
- **Câu bị rút gọn cho vừa khung** có thể mất ý (ví dụ "Hãy nói chuyện với người trong phòng của ta" thành "Nói chuyện với
  phòng ta"). Chất lượng câu dịch do máy tạo, chưa có người đọc kiểm.
- **Star Wars: Flight of the Falcon** vẫn phần lớn tiếng Anh (ROM gần như hết chỗ trống).
- Kiểm tự động không thay cho chơi thử.

Miễn trừ trách nhiệm: xem [PHAT-HANH-0.5.0.md](PHAT-HANH-0.5.0.md#d-tuyên-bố-miễn-trừ-trách-nhiệm) và README.
