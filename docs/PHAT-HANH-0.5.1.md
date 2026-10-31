# LoongMate 0.5.1-beta — Ghi chú phát hành

Bản sửa lỗi cho Việt hoá GB/GBC/GBA. Các phần khác (Tua nhanh, dấu phủ, Việt hoá nạp một lần, miễn trừ trách nhiệm) giữ như
[0.5.0-beta](PHAT-HANH-0.5.0.md).

## Đã sửa

- 7 game trước đây bị chặn Việt hoá vì treo / màn đen giờ đã chạy: **Pokémon Ruby**, **Pokémon Sapphire**, **Mega Man & Bass**,
  **Dragon Warrior Monsters**, **The Adventures of Elmo in Grouchland**, **Sigma Star Saga**, **Star Wars: Flight of the Falcon**.
- Các bản sửa là luật chung, áp cho mọi game: giữ byte tham số dính vào chữ, giữ đúng ô chữ cố định, bỏ nhận nhầm mã máy / bảng
  dữ liệu là con trỏ câu, chừa chỗ an toàn ở cuối vùng trống, câu được mã máy trỏ thẳng chỉ dời trong cỡ ROM gốc.

## Đi kèm

- **Leaf Hot Tabs** (tự tiếp tục game từ Games/Favorites) cho Leaf v0.12.1 và v0.11.0 — xem [HOT-TABS.md](HOT-TABS.md).
- Hướng dẫn **Thư viện game riêng qua PocketBridge + Tailscale** (cài agent trên PC/Mac/Pi, ghép đôi, đường dẫn) — xem
  [POCKETBRIDGE.md](POCKETBRIDGE.md). PocketBridge tải ở https://github.com/Bachtd1/pocketbridge/releases.

## Số đo (kiểm tự động)

- Quét 573 bản ROM GB/GBC/GBA: **cả 573 bản chạy được** với bản Việt hoá — 569 bản kiểm trên PC; 4 bản dùng mạch MBC2 (Ultima:
  Runes of Virtue I/II, Lazlos' Leap, Doraemon Kart) kiểm trên Pocket 1 bằng lõi gambatte của Leaf. Danh sách:
  [DANH-SACH-GAME.md](DANH-SACH-GAME.md).
- Game MBC2: Việt hoá dùng lõi mặc định của Leaf (lõi mGBA có dấu phủ không chạy được mạch này) — có chữ Việt, không có dấu phủ
  trong lõi.
- Lớp phủ dấu phủ: không game nào có khung hình vượt 2 ms (trung bình dưới 0,1 ms mỗi khung).
- Đổi lấy an toàn, số trang được Việt hoá giảm khoảng 1,3% (chủ yếu ở các game vừa sửa).

## Giới hạn còn lại

- **Sigma Star Saga**, **Star Wars: Flight of the Falcon**: game không đọc được chữ đặt ngoài cỡ ROM gốc, ROM gần như hết chỗ trống
  — chỉ câu vừa chỗ cũ được Việt hoá, phần lớn giữ tiếng Anh.
- Google Dịch không rút gọn được câu: câu dài quá khung giữ tiếng Anh. Chất lượng câu dịch do máy tạo, chưa kiểm hết.
- Kiểm tự động không thay cho chơi thử; mục "Đã test trên máy" trong danh sách game sẽ cập nhật khi được xác nhận.

Miễn trừ trách nhiệm: xem [PHAT-HANH-0.5.0.md](PHAT-HANH-0.5.0.md#d-tuyên-bố-miễn-trừ-trách-nhiệm) và README.
