# Thay đổi

## 0.5.1-beta — 2026-10-31

- Sửa 7 game trước đây bị chặn Việt hoá (treo / màn đen): Pokémon Ruby, Pokémon Sapphire, Mega Man & Bass, Dragon Warrior
  Monsters, The Adventures of Elmo in Grouchland, Sigma Star Saga, Star Wars: Flight of the Falcon.
- Quét 573 bản ROM: cả 573 chạy được với bản Việt hoá (4 bản MBC2 kiểm trên máy bằng gambatte). Kèm Hot Tabs (Leaf v0.12.1/v0.11.0)
  và hướng dẫn Thư viện riêng qua PocketBridge. Chi tiết:
  [docs/PHAT-HANH-0.5.1.md](docs/PHAT-HANH-0.5.1.md).

## 0.5.0-beta — 2026-10-31

- **Việt hoá GB/GBC/GBA**: quét 573 bản ROM — 560 chạy được với bản Việt hoá; sửa 12 game trước đây treo / màn đen (ký hiệu là
  mã của game trong chữ, dòng trống cuối câu, game chỉ có chữ HOA, chữ canh giữa ở màn tiêu đề, con trỏ giả). 7 game còn trục
  trặc được nhận ra tự động và không Việt hoá ROM (xem [docs/DANH-SACH-GAME.md](docs/DANH-SACH-GAME.md)).
- **Dấu phủ**: core `mgba_vh` vẽ dấu tiếng Việt lên chữ trên màn hình; lớp phủ thêm khoảng 1% thời gian mỗi khung hình.
- **Câu dài**: GB/GBC ~96–97% chữ Việt hoá trọn vẹn khi dùng AI (dời câu, ngắt dòng theo khung, AI rút gọn câu quá khung).
- **Việt hoá nạp một lần**: chơi xong gỡ khỏi RAM, chơi lại thì nạp lại trong LoongMate. Sửa lỗi tắt máy Leaf báo chưa lưu /
  đòi sửa thẻ.
- **Tua nhanh** (mới): Máy → Tua nhanh, Menu + R1/L1, 2x/3x/4x/tối đa.
- Chi tiết, giới hạn và miễn trừ trách nhiệm: [docs/PHAT-HANH-0.5.0.md](docs/PHAT-HANH-0.5.0.md).

## 0.4.2-beta — 2026-10-28

Việt hoá cá nhân (GB/GBC/GBA, thử nghiệm):

- **Core tách chữ mới**: bước tự chọn core; bảng chữ không theo abc (FF IV–VI Advance), DTE (cặp chữ, game GB/GBC hãng Nhật),
  Huffman (kiểu Fire Emblem GBA), chữ 16 bit (FF I&II, Sword of Mana, FFTA), tệp kịch bản kiểu Tales of Phantasia, kịch bản lệnh
  kiểu Lufia.
- **Câu dài hơn bản gốc ghi được ở nhiều game hơn**: GB dời câu sang bank khác, dùng lại chỗ của câu đã dời; bản ghi cố định /
  khối RLE / bảng chỉ mục tương đối dùng hết ô chữ; câu một dòng dời được (menu, nhãn) dài thêm ~25% tới bề rộng màn GBA.
- Nhận thêm chữ menu, tên đồ, hội thoại nhiều mã điều khiển (đối chiếu từ điển tiếng Anh).
- **Ít lần gọi AI hơn nhiều**: gom câu theo số ký tự, câu lặp dịch một lần, chỉ gửi thuật ngữ có trong lô (Dragon Warrior I & II:
  ~220 → 31 lần gọi). ROM châu Âu nhiều thứ tiếng: không gửi câu Pháp/Đức/Tây Ban Nha/Ý đi dịch (chỉ dịch phần tiếng Anh).
- **6 bước có trạng thái** (Bước K/6), dịch tiếp khi dừng giữa chừng; màn **HẾT HẠN MỨC** (dịch tiếp bằng Google/offline hay để
  mai); **Dịch lại bằng AI** các trang đã dịch bằng Google/offline.
- **Kiểm từng byte** mỗi lần dựng bản chơi: có byte ghi ngoài vùng chữ là không cho dùng bản đó (game gốc vẫn chơi bình thường).
- Sửa: Dragon Warrior I & II hết hạn mức nhanh rồi báo "JSON không hợp lệ" / bản Việt hoá không dùng được; 5 lỗi có thể ghi hỏng
  ROM khi dời câu; nhiều lỗi quy trình dịch và core tách (kiểm toán).
- Đo trên 208 game tiếng Anh có chữ: "Việt hoá tốt" 50% → 58% (đo tự động trên PC, chưa chơi thử từng game). Chi tiết, giới hạn
  kiểm thử và hạn chế: xem ghi chú phát hành v0.4.2-beta.

## 0.4.1-beta — 2026-10-25

- **Dấu phủ (thử nghiệm)**: game chưa có dấu trong ROM vẫn hiện chữ không dấu, lớp phủ vẽ thêm dấu lên đúng chữ trên màn hình.
- Việt hoá nhận thêm game ít chữ, game chỉ chữ HOA, bản Nhật đã có fan dịch tiếng Anh; sửa chỗ có thể làm hỏng game khi vá chữ
  và lỗi cấu hình RetroArch của Dịch game.

## 0.4.0-beta — 2026-10-25

LoongMate từ bản này là **phần mềm miễn phí (freeware)**: chỉ phát hành bản dựng sẵn, không kèm mã nguồn. Xem LICENSE.

- **Dịch game (thử nghiệm)**, Máy → Dịch game: lớp phủ tiếng Việt cho chữ trong game RetroArch qua AI Service có sẵn của
  RetroArch. Bấm phím dịch (Menu + R2) hoặc tự động khi màn hình đổi. Offline bằng gói tải về máy (OCR + dịch, không cần mạng khi
  chơi) hoặc AI (Gemini, Claude, API tương thích OpenAI) bằng API key của bạn. Bật cho từng game. Xem docs/DICH-GAME.md.
- **Việt hoá cá nhân (thử nghiệm, chỉ GB/GBC/GBA)**: "Dịch cả game" rồi **▶ Chơi bản Việt hoá** — chữ Việt nằm ngay trong game,
  không lớp phủ, không giật. Đồng ý điều khoản một lần là **tự động**: mở game từ Leaf (Games, Recents, Favorites) luôn ra tiếng
  Việt (cần LoongMate Agent). Chỉ dùng được trên máy đã dịch (bản dịch mã hoá theo máy, ROM Việt hoá chỉ nằm trong RAM, ROM gốc
  không bị sửa). **Không mua bán, không xin donate bằng bản Việt hoá.**
  - Đọc được chữ dạng ASCII, bảng chữ riêng của game (Pokémon, Mega Man Battle Network, Breath of Fire...), khối nén LZ77/RLE
    của GBA; câu dịch dài hơn bản gốc được dời chỗ qua con trỏ (cả bảng chỉ mục tương đối kiểu Boktai), dùng cả khung hộp thoại,
    tên người nói giữ một dòng riêng; câu quá khung thì xin AI bản ngắn hơn.
  - **Có dấu** khi LoongMate nhận ra font của game (Advance Wars 1/2; font ô cố định như Castlevania Aria of Sorrow, Bomberman
    Max...); font ít ô trống thì "có dấu X%" (chữ hiếm mất dấu thanh); font nén/riêng thì **không dấu** — báo rõ ở dòng game,
    gửi game muốn hỗ trợ qua GitHub Issues.
  - **Không có API key**: tự dùng Google Dịch (miễn phí), mã/thẻ của game được giữ nguyên.
  - Dịch bền: lỗi định dạng từ AI không làm dừng cả quá trình (chia nhỏ lô, dịch lại phần thiếu); lỗi khoá/quyền thì báo ngay.

## 0.3.0-beta — 2026-10-22

- **Giải nén .7z, .rar (RAR4/RAR5), .tar.gz/.bz2/.xz, .gz, .zst, .cab...** trong Nhập game: mục archive hiện "giải nén", bấm
  GIẢI NÉN. Kiểm như zip TRƯỚC khi ghi (đường dẫn, link, kích thước, zip bomb, chỗ trống), giải vào thư mục tạm cùng thẻ rồi
  mới thành `Import/<tên>/`; archive nhiều phần (`.part1.rar`, `.7z.001`) đi cùng nhau. Kèm 7-Zip 26.04 chính thức (bản tĩnh
  ARM64, ghim SHA-256, giấy phép trong GIAY-PHEP/).
- **Archive có mật khẩu** (cả .zip có mật khẩu, trước đây bị chặn): tự thử mật khẩu người đăng/người dùng để lại — chú thích
  archive, tên file (`[pass xyz]`), file .txt/.nfo cạnh archive, `Import/passwords.txt`, mật khẩu đã dùng thành công. Không dò
  mật khẩu (tối đa 30 ứng viên). Không ra thì màn MẬT KHẨU ARCHIVE cho chọn hoặc gõ bằng bàn phím.
- **Tải từ web và Thư viện riêng nhận .7z/.rar/...**: tải xong tự giải nén rồi cài; cần mật khẩu mà không đoán được thì
  archive được chuyển vào `Import/` để giải ở Nhập game.
- **Tải từ web, lưới ảnh bìa**: trang có nhiều game hiện thành lưới ô ảnh bìa + tên (Y: danh sách). Ô TẢI (file hoặc link
  "Download" trong nội dung, đã lọc quảng cáo như danh sách) đứng đầu và được chọn sẵn: các bước tải chỉ cần bấm A; bước tải
  kế tiếp giữ ảnh bìa của trang game. Ảnh tải nền (không chặn phím), chỉ https, chỉ PNG/JPEG/GIF thật, cache tự dọn.
- **R1 trang sau, L1 trang trước** khi duyệt danh sách game nhiều trang (dò rel=next/prev, nút Next/›/Tiếp, số trang
  trong địa chỉ — chung cho mọi trang web).
- **Game tải từ web được gắn ảnh bìa** của trang game đã đi qua (Leaf hiện ngay sau khi quét lại); tải lại game đã có mà
  chưa có ảnh cũng được gắn; ảnh có sẵn luôn giữ nguyên.
- **Chọn tất cả** trong Nhập game và Thư viện riêng; đã chọn (X) nhiều game thì A cài/nhập/xoá TẤT CẢ game đã chọn (Thư
  viện riêng, Homebrew, Nhập game, Xoá ROM) thay vì chỉ game đang đứng; vẫn qua xem trước + xác nhận.
- Hiểu các hệ máy do pak cài thêm (Pak Rat, vd J2ME.pak: `Roms/J2ME`, `.jar`/`.kjx`): Library Doctor không còn báo
  thư mục đó là lạ; Nhập game nhận và cài `.jar` vào đúng thư mục (chỉ khi pak đã cài; không có pak thì `.jar` vẫn bị chặn
  như file chạy được).

## 0.2.1-beta — 2026-10-18

- **Sửa: điều hướng tự chạy loạn sau khi mở khoá.** Nút (D-pad) đang giữ đúng lúc khoá bật: app nhận "nhấn" nhưng
  "nhả" bị khoá giữ lại nên tưởng nút vẫn giữ. Mở khoá giờ trả lại tín hiệu nhả cho app.
- **Sửa: khoá không rung.** Rung qua tay cầm ảo chỉ được Leaf mở khi đang chạy game; giờ dùng rung giao diện của Leaf
  (theo cài đặt rung giao diện, chạy cả ngoài game): bấm nút lúc khoá rung "chặn", mở khoá rung "xác nhận".

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
