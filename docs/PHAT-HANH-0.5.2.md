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
