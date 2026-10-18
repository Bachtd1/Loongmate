# Thay đổi

## 0.2.0-beta — 2026-10-18

- **Giao diện tiếng Anh**: Máy > Ngôn ngữ / Language (hoặc dòng đầu màn Bắt đầu), đổi là có hiệu lực ngay. Dịch cả giao
  diện native lẫn thông báo của engine (~3000 chuỗi, danh mục `i18n/en.json`). Giao diện Python dự phòng vẫn tiếng Việt.
- Khoá khi thức dậy: bấm nút lúc đang khoá thì máy rung ngắn (tối đa 3 giây một lần), mở khoá rung dài một nhịp;
  trước đây khoá không có dấu hiệu gì nên dễ tưởng máy treo.
- **Bắt đầu**: lần đầu mở app hiện các bước nên làm, mỗi bước có trạng thái thật và làm ngay tại chỗ (bật Agent, sao lưu
  lần đầu, quét thư viện, tự tiếp tục, khoá khi thức dậy). Mở lại ở Máy > Hướng dẫn bắt đầu. Lệnh `onboarding`.
- Home báo "Agent chưa chạy" ở ô MÁY khi đã cài LoongMate Agent mà service không chạy (tự sao lưu, tự tiếp tục, khoá
  đều ngừng khi đó).
- Màn Máy chia nhóm: Khi chơi game, Pin & Agent, Bảo trì, Nâng cao.
- Rà mọi nhãn bằng font thật trên máy, rút gọn 22 nhãn/tiêu đề bị cắt "..."; công cụ `native/tools/label_audit.py`.

## 0.1.2-beta — 2026-10-18

- **Khoá khi thức dậy** (Máy → Khoá khi thức dậy, mặc định tắt): chống bấm nhầm khi để máy trong túi. Mỗi lần máy thức
  sau khi ngủ, LoongMate Agent chặn mọi phím tới khi giữ **Select + Start 1 giây**; không mở khoá trong 10 giây thì máy
  ngủ lại. Nút nguồn vẫn dùng bình thường (bấm để ngủ, giữ 2 giây để tắt máy). Không sửa file nào của Leaf; Agent dừng
  hay lỗi thì phím tự được trả lại. Lệnh: `pocketlock status | on | off`.

## 0.1.1-beta — 2026-10-18

- **Sửa: tự tiếp tục qua Agent bỏ qua mọi game trên thẻ Leaf (SD1).** Leaf ghi đường dẫn game SD1 dạng tương đối
  (`Roms/SNES/…`) còn RetroArch báo tuyệt đối (`/mnt/sdcard/Roms/SNES/…`); trước đây so chuỗi y nguyên nên coi là "game
  khác". Game trên SD2 không bị ảnh hưởng.
- Tự kiểm tương thích với bản Leaf đang chạy (log `launch-game`, dòng xác nhận chạy game, socket `jawakad`): sau khi cập
  nhật Leaf, màn **Tự tiếp tục game** báo "tương thích" hoặc "lệch". Chỉ khi lệch mới gợi ý Hot Tabs (vá launcher).
- Màn hiện lần tự tiếp tục gần nhất (đã nạp state, game chưa có state…); `autoresume status` cũng in các thông tin này.
- Màn **Leaf Hot Tabs** đổi thành **Tự tiếp tục game**: dòng "Qua Agent" lên đầu với tên ngắn không bị cắt, báo LoongMate
  Agent có đang chạy không. Phần Hot Tabs (launcher, bật/tắt, cài/gỡ) chỉ hiện khi trên thẻ có Hot Tabs hoặc bộ cài;
  trước đây máy không có Hot Tabs vẫn thấy dòng Hot Tabs "Bật" gây hiểu nhầm.
- Giao diện Python dự phòng có thêm nút bật/tắt tự tiếp tục qua Agent.

## 0.1.0-beta — 2026-10-18

Bản công khai đầu tiên. Đã dùng thật trên Miniloong Pocket 1 với Leaf 0.11.0 và 0.13.0.

**Save**
- SaveVault: snapshot save/state của hai thẻ, lưu theo nội dung; khôi phục từng game hoặc cả bản, sao lưu hiện trạng
  trước khi ghi, nhật ký và rollback khi mất điện.
- Từ chối khôi phục/rollback khi emulator đang chạy.
- LoongMate Agent (service của Leaf): tự sao lưu sau khi thoát game, lịch sử pin/nhiệt, kiểm toàn vẹn hàng tuần.

**Thư viện**
- Library Doctor đọc danh mục hệ máy của chính Leaf: báo game Leaf không hiện (CPS1/2/3 ngoài ARCADE trên Leaf 0.13).
- Sửa an toàn (ghép ảnh bìa lệch tên, cất ảnh thừa, sửa `.cue`, tạo thư mục) và Sửa hàng loạt (gộp thư mục, chuyển game
  sai thư mục) có Hoàn tác.
- Ghi chú gom theo hệ máy; số game thiếu ảnh bìa lấy từ thư viện của Leaf, kèm hướng dẫn Scrape Missing Artwork.
- BIOS: kiểm md5, nhận biết hệ chạy được bằng BIOS giả lập (Dreamcast, PS1, GBA, NDS), BIOS Dreamcast đặt vào `BIOS/dc/`,
  báo file sai chỗ, cài BIOS từ `Import/`.
- Dọn ROM trùng giữa hai thẻ bằng SHA-256.
- Mã lỗi ổn định `LM-…` cho mỗi loại vấn đề (xem `docs/MA-LOI.md`).

**Thêm game**
- Nhập game từ `Import/`: nhận hệ bằng header, giải nén zip an toàn, chặn zip bom và file chạy được.
- Tải từ web: https, tải tiếp khi mất mạng, chặn địa chỉ nội bộ, gắn ảnh bìa khớp game từ trang tải.
- Homebrew Hub (chỉ game có giấy phép rõ), Thư viện riêng từ PC/NAS qua PocketBridge, Patch ROM IPS/UPS/BPS.
- Hỏi lưu vào SD1 hay SD2; tải tiếp luôn cài đúng thẻ chứa file tải dù hai thẻ đổi chỗ.
- Game mới tải về, Tìm kiếm game trên hai thẻ.

**Leaf**
- Sao lưu cấu hình Leaf/RetroArch, so từng khoá, khôi phục theo nhóm; tự chụp khi Leaf đổi phiên bản.
- Tự tiếp tục khi mở game từ Games/Favorites qua Agent, không vá Leaf (thử nghiệm, mặc định tắt).
- Gói kèm runtime Python, tự cài lại nếu bị xoá; sống sót qua cập nhật Leaf.
