# LoongMate 0.5.3-beta — Ghi chú phát hành

Hai gói riêng: **`LoongMate-AllInOne.zip`** cho Miniloong Pocket 1 (Leaf) và **`LoongMate-TrimUI-0.5.3-beta.zip`** cho TrimUI
Smart Pro S (TrimUI OS gốc, bản đầu tiên — xem [docs/TRIMUI.md](TRIMUI.md)).

## Mới
- **TrimUI Smart Pro S** (beta): giao diện 1280x720, nút A/B X/Y đúng kiểu Nintendo, Agent tự chạy khi bật máy, Việt hoá GB/GBC/GBA
  với lõi "mGBA Viet hoa" đặt sẵn làm mặc định, Tailscale userspace. Chức năng chỉ có trên Leaf được ẩn; quét thư viện không gợi ý
  đổi tên thư mục theo kiểu Leaf.
- **Vào tailnet bằng QR** ngay trên máy (Máy → Vào tailnet): quét bằng điện thoại, không cần PC hay rút thẻ.

## Sửa (core dịch, dùng chung mọi game)
- **Câu dài sang khung mới** (bấm A như game thường) thay vì tràn khung / đè chữ: nhận ra mã chờ phím có tham số (Breath of Fire
  "09 02"), dàn lại cả khung, không còn dòng mồ côi. Breath of Fire: 2860 khung, 1 khung quá 3 dòng (câu hỏi Có/Không).
- **Sword of Mana**: hết font rác (con trỏ font bị nhận nhầm là con trỏ câu).
- **Dragon Warrior Monsters**: hết đứng hình ở câu thoại đầu (bảng dữ liệu bị nhận nhầm là bảng con trỏ câu).
- Cập nhật LoongMate mà chưa tắt máy: "Chơi bản Việt hoá" tự dựng lại bản Việt hoá theo mã mới.
- Tải gói dịch offline / Google Dịch trên máy không có bộ chứng thư hệ thống (TrimUI) dùng bộ CA đi kèm.
- Kiểm diện rộng: 1043 ROM (bố cục khung không đổi ở game đang đúng), 777 ROM GB/GBC (con trỏ), bộ test engine Việt hoá.

## Giới hạn còn lại
- Game tìm câu qua **bảng độ lệch / đếm câu** (Dragon Warrior Monsters, Sword of Mana và khoảng 68 game khác): câu dịch dài hơn câu
  gốc chưa ghi được, **vẫn còn câu tiếng Anh**. Đang làm tầng chung "dựng lại cả khối" cho mọi game kiểu này.
- **Summon Night: Swordcraft Story**: lời thoại nằm trong khối nén, chưa dịch được.
- Dấu phủ: một vài trang đầu mỗi lần chơi có thể chưa có dấu; dấu có lúc chập chờn khi chữ hiện dần.
- Kiểm tự động không thay cho chơi thử.

Miễn trừ trách nhiệm: xem [PHAT-HANH-0.5.0.md](PHAT-HANH-0.5.0.md#d-tuyên-bố-miễn-trừ-trách-nhiệm) và README.
