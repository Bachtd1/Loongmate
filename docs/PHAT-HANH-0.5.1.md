# LoongMate 0.5.1-beta — Ghi chú phát hành

Bản sửa lỗi cho Việt hoá GB/GBC/GBA. Các phần khác (Tua nhanh, dấu phủ, Việt hoá nạp một lần, miễn trừ trách nhiệm) giữ như
[0.5.0-beta](PHAT-HANH-0.5.0.md).

## Đã sửa

- 7 game trước đây bị chặn Việt hoá vì treo / màn đen giờ đã chạy: **Pokémon Ruby**, **Pokémon Sapphire**, **Mega Man & Bass**,
  **Dragon Warrior Monsters**, **The Adventures of Elmo in Grouchland**, **Sigma Star Saga**, **Star Wars: Flight of the Falcon**.
- Các bản sửa là luật chung, áp cho mọi game: giữ byte tham số dính vào chữ, giữ đúng ô chữ cố định, bỏ nhận nhầm mã máy / bảng
  dữ liệu là con trỏ câu, chừa chỗ an toàn ở cuối vùng trống, câu được mã máy trỏ thẳng chỉ dời trong cỡ ROM gốc.

## Số đo (kiểm tự động trên PC)

- Quét 573 bản ROM GB/GBC/GBA: **569 bản chạy được** với bản Việt hoá; 4 bản chưa kiểm được (chính bản gốc không chạy trong bộ
  kiểm). Danh sách: [DANH-SACH-GAME.md](DANH-SACH-GAME.md).
- Lớp phủ dấu phủ: không game nào có khung hình vượt 2 ms (trung bình dưới 0,1 ms mỗi khung).
- Đổi lấy an toàn, số trang được Việt hoá giảm khoảng 1,3% (chủ yếu ở các game vừa sửa).

## Giới hạn còn lại

- **Sigma Star Saga**, **Star Wars: Flight of the Falcon**: game không đọc được chữ đặt ngoài cỡ ROM gốc, ROM gần như hết chỗ trống
  — chỉ câu vừa chỗ cũ được Việt hoá, phần lớn giữ tiếng Anh.
- Google Dịch không rút gọn được câu: câu dài quá khung giữ tiếng Anh. Chất lượng câu dịch do máy tạo, chưa kiểm hết.
- Kiểm tự động không thay cho chơi thử; mục "Đã test trên máy" trong danh sách game sẽ cập nhật khi được xác nhận.

Miễn trừ trách nhiệm: xem [PHAT-HANH-0.5.0.md](PHAT-HANH-0.5.0.md#d-tuyên-bố-miễn-trừ-trách-nhiệm) và README.
