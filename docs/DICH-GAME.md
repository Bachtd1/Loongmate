# Dịch game (thử nghiệm)

Máy → **Dịch game**: lớp phủ tiếng Việt đè lên chữ trong game **RetroArch**. Bấm phím dịch là game dừng và hiện bản dịch; hoặc để
tự động dịch mỗi khi màn hình đổi.

## Cách hoạt động

- Dùng **AI Service** có sẵn trong RetroArch của Leaf: RetroArch gửi ảnh màn hình tới `http://127.0.0.1:4404/`, LoongMate trả ảnh
  trong suốt có chữ tiếng Việt, RetroArch vẽ đè lên game. Không có web dashboard, không mở cổng ra mạng.
- Bộ dịch là **một tiến trình riêng** do LoongMate Agent chạy khi game **đã bật dịch** đang chơi, và dừng khi thoát game — gói offline
  không chiếm RAM lúc không dùng. Cần LoongMate Agent đang chạy.
- Bật tính năng: LoongMate ghi các khoá `ai_service_*` và phím dịch vào `retroarch.cfg` chung của Leaf, **lưu bản gốc**; tắt là trả
  nguyên trạng. Phải thoát game trước khi bật/tắt (RetroArch ghi đè cấu hình khi thoát).
- Chỉ game chạy bằng RetroArch. Emulator độc lập (PPSSPP độc lập, DraStic, Mupen64...) không có cơ chế lớp phủ.
- Không làm game giật: RetroArch giải mã ảnh lớp phủ trên luồng chính của game, nên lớp phủ được vẽ nhỏ (tối đa ~160 nghìn điểm
  ảnh, vd GBA 480×320: 12 ms thay vì 43 ms của bản 960 px), màn không đổi thì LoongMate **giữ yêu cầu** (lớp phủ vẫn hiện,
  RetroArch không phải chụp/giải mã lại), màn không có chữ trả ảnh 8×8. Bộ dịch chạy ở mức ưu tiên thấp (nice 10).
- Chữ trong game đổi (sang trang thoại): bản dịch cũ được **xoá ngay**, bản mới hiện khi đọc + dịch xong ở luồng nền.

## Công cụ dịch

| Công cụ | Cần | Ghi chú |
|---|---|---|
| **Offline** | Tải gói (~140 MB cho tiếng Anh, ~260 MB cho Nhật) | OCR PP-OCR mobile + dịch Argos/CTranslate2, chạy trên máy, không cần mạng khi chơi. Đo trên máy: ~7 s lần đầu (nạp mô hình), ~3–5 s/màn sau đó, RAM ~300 MB. Tiếng Anh tốt; Nhật/Trung/Hàn dịch qua tiếng Anh nên kém hơn. |
| **Gemini** | API key Google AI Studio | Mặc định `gemini-3.8-flash`. |
| **Claude** | API key Anthropic | Mặc định `claude-opus-5-5`; nhanh/rẻ hơn: `claude-haiku-5-5`. |
| **API tương thích OpenAI** | API key + địa chỉ + tên model | OpenAI, OpenRouter, DeepSeek, Groq... — model phải đọc được ảnh. |

Với AI, ảnh màn hình được gửi tới nhà cung cấp bạn chọn (có thể tốn phí theo nhà cung cấp). Lỗi thì báo lỗi, **không** tự chuyển sang
nhà cung cấp khác.

Gói offline tải từ nguồn gốc (PyPI, RapidOCR/modelscope, Argos); mỗi file được ghim địa chỉ + SHA-256 + kích thước, lệch là bỏ.
Lưu ở `<dữ liệu LoongMate>/gametrans/`; gỡ ở Máy → Dịch game → Gói tải về (X).

## Phạm vi dịch

- **Chỉ khung thoại** (mặc định): sau bước tìm chữ, chỉ giữ cụm chữ giống khung thoại (rộng, nhiều dòng), bỏ HUD, số liệu, nhãn
  ngắn trước khi đọc; LoongMate nhớ **dải** khung thoại của từng game (cả bề ngang, nới thêm một dòng trên/dưới, tự nới rộng khi
  game hiện khung thoại ở chỗ khác — vd Advance Wars đổi trên/dưới theo người nói) và lần sau quét dải đó trước (trống thì quét cả
  màn). Game đã có bản dịch cả game: dòng ngắn trong dải ("Hey!", "Huh?") cũng đọc, hiện nếu có trong kịch bản.
  Đo trên máy khi đang chạy game (Offline): mọi chữ ~4 s, chỉ khung thoại ~2.6 s, đã biết vị trí ~1.9 s; hộp thoại gặp lại: tức thì.
- **Mọi chữ trên màn**: dịch cả menu, tên vật phẩm, trạng thái.

## Ngôn ngữ gốc

**Tự nhận theo game** (mặc định): theo tên ROM — `(Japan)` là tiếng Nhật; bản dịch fan `[T-En...]` và `(USA)`/`(Europe)` là tiếng
Anh; game đã bóc kịch bản từ ROM luôn là tiếng Anh. Tự nhận ra tiếng Nhật mà chưa tải gói Nhật (Offline) thì đọc như tiếng Anh.
Chọn rõ một ngôn ngữ thì áp dụng cho mọi game. Ngôn ngữ của từng game hiện trong danh sách Game.

## Bản dịch cả game

Máy → Dịch game → (game) → X **DỊCH CẢ GAME**: bóc kịch bản từ ROM (game lưu chữ ASCII như Advance Wars USA: ~4.700 câu) hoặc
lấy các câu đã học khi chơi, dịch cả bộ theo thứ tự có ngữ cảnh (AI nếu có key). Xong là **tự bật dịch cho game đó**.
Khi chơi: đọc khung thoại → dóng trang vào đúng thông điệp trong kịch bản (chịu lỗi OCR; trang chỉ có đuôi câu thì nối theo trang
trước) → hiện bản dịch câu đó, không gọi API. Đổi luật bóc chữ (bản mới thêm câu ngắn, menu) thì bấm X lần nữa: chỉ dịch phần mới.

## Việt hoá cá nhân — THỬ NGHIỆM, chỉ game GB/GBC/GBA

"Dịch cả game" còn dịch theo từng hộp thoại và cho phép **chơi bản Việt hoá**: chữ Việt nằm ngay trong game — đúng thời điểm,
không OCR, không lớp phủ, không giật tiếng. Hiện chỉ làm cho game **GB, GBC, GBA** (file `.gb`, `.gbc`, `.gba`); hệ khác vẫn dùng
lớp phủ dịch.

- **Dịch bằng gì**: AI bằng API key của bạn (Gemini, Claude, API tương thích OpenAI — hiểu ngữ cảnh, giữ tên riêng thống nhất).
  **Không có API key**: tự dùng **Google Dịch** (miễn phí, không cần key; dịch từng câu nên kém ngữ cảnh hơn AI). Chữ có dấu vẽ
  như nhau với cả hai.
- **Mở lần đầu**: Máy → Dịch game → (game) → **▶ Chơi bản Việt hoá** → đọc trang điều khoản → A "Đồng ý và chơi".
- **Tự động** (sau khi đồng ý một lần): mở game từ Leaf (Games, Recents, Favorites) là ra tiếng Việt — LoongMate Agent chuẩn bị
  sẵn bản Việt hoá trong RAM khi bạn không chơi, gỡ ngay khi game đã nạp xong, thoát game thì chuẩn bị lại. Cần bật LoongMate
  Agent. Tắt: dòng ▶ Chơi bản Việt hoá → X "TẮT TỰ ĐỘNG".
- **Dịch đủ, không sót**:
  - Câu dịch dài hơn câu gốc được dời sang chỗ trống của ROM, khi tìm được con trỏ của game (kể cả bảng chỉ mục tương đối kiểu
    Boktai).
  - Dài hơn một khung thì **tự thêm trang**: bấm A sang trang như bản gốc. LoongMate tự nhận mã sang trang của từng game từ
    kịch bản; không nhận chắc thì không thêm.
  - GBA dịch cả **tên vật phẩm, phép, menu** (bảng tên của game), không dài hơn tên gốc để vừa cột.
  - Câu vẫn không vừa thì xin AI bản ngắn hơn. Câu hỏng mã của game hoặc vẫn không vừa thì chỉ trang đó giữ tiếng Anh (không
    làm hỏng game).
- **Có dấu hay không** — tuỳ font của game, LoongMate tự nhận và báo rõ ở dòng ▶ Chơi bản Việt hoá:
  - **Có dấu**: font kiểu bảng con trỏ glyph (Advance Wars 1, 2) và font ô cố định theo mã chữ (vd Castlevania Aria of Sorrow,
    Bomberman Max...): LoongMate vẽ chữ Việt từ chính nét chữ của game vào ô trống của font.
  - **Có dấu X%**: font còn ít ô trống — chữ thường gặp đủ dấu, chữ hiếm mất dấu thanh (vd "ộ" thành "ô").
  - **Dấu phủ** (thử nghiệm): font của game nén/định dạng riêng (vd Boktai) hoặc quá ít ô trống (vd Pokémon
    Gold/Silver/Crystal chỉ còn 9 ô). Chữ trong ROM không dấu; khi chơi, LoongMate **tự vẽ dấu lên chữ trên màn hình** bằng
    chính nét chữ của game (học font ngay trong vài giây đầu, nhớ cho lần sau). Tự bật khi mở game Việt hoá, không cần bấm
    phím và không cần bật lớp phủ dịch. Máy không chặn được lệnh mạng của RetroArch (thiếu iptables) thì vào game giữ
    **Menu + R2** một lần. Không vẽ được khi khung thoại có nền chuyển màu/trong suốt (vẫn chữ không dấu). Muốn dấu nằm hẳn
    trong game: **báo ở [GitHub Issues](https://github.com/Bachtd1/Loongmate/issues)**, ghi đúng tên file ROM.
- **Của ai người đấy dùng**: bản dịch lưu **mã hoá bằng khoá riêng của máy** (khoá trong bộ nhớ trong, không nằm trên thẻ) — chép
  thẻ sang máy khác không dùng được. **Không có file ROM Việt hoá nào trên thẻ**: ROM Việt hoá chỉ được dựng trong RAM (`/tmp`,
  chỉ root đọc), cùng tên ROM gốc nên dùng chung file lưu game. **ROM gốc không bao giờ bị sửa.** (Người có quyền root trên chính
  máy đó vẫn có thể tự lấy — đây là máy và bản dịch của họ; mục đích là không xuất/chia sẻ được.)
- **Dịch cả game** gồm hai bước có hiện tiến độ + thời gian còn lại: dịch kịch bản, rồi Việt hoá ROM. Cần Wi-Fi. Mất mạng giữa
  chừng thì tự tạm dừng, phần đã dịch được giữ: chọn lại để dịch tiếp. Xong thì hỏi **chơi ngay**. Đoạn nào bản dịch không vừa
  khung chữ của game thì giữ tiếng Anh và hiện số đoạn đó ở dòng ▶ Chơi bản Việt hoá (bấm X ở dòng game để dịch lại ngắn hơn).
- Trước lần chơi đầu luôn có trang miễn trừ trách nhiệm: LoongMate không cung cấp game, không chịu trách nhiệm về bản dịch;
  **không mua bán, không xin donate, không trục lợi** từ bản Việt hoá; tôn trọng quyền sở hữu trí tuệ.
- Giữ nguyên mã trong chữ của game: màu chữ, tên người chơi, ngắt chờ, thẻ lệnh (`<NAME>`, `<WEIGHT>`...).

## API key

Lưu trong bộ nhớ trong (`/userdata/LoongMate/secrets/gametrans-<nhà cung cấp>.key`, quyền 0600), không nằm trên thẻ. Ba cách nhập
(Máy → Dịch game → API key (AI)):

- **A — từ điện thoại**: máy hiện mã QR, quét bằng điện thoại cùng Wi-Fi, dán key, bấm Lưu (máy thử key trước khi lưu). Trang chỉ
  có trong mạng nhà, dùng một lần, tự đóng sau 5 phút, không bao giờ trả key ra.
- **X — từ thẻ**: file `loongmate-ai-keys.txt` ở gốc thẻ, mỗi dòng `gemini: KEY`, `anthropic: KEY` hoặc `openai: KEY`: key được lưu
  và **file bị xoá khỏi thẻ** (thẻ FAT ai cũng đọc được).
- **Y — gõ** bằng bàn phím trên màn hình.

## Dùng trong game

1. Máy → Dịch game: chọn công cụ, ngôn ngữ gốc, tải gói / nhập key, **Bật**.
2. Mở game RetroArch một lần → game hiện trong danh sách "Game" → bật dịch cho game đó (hoặc bật "Mọi game RetroArch").
3. Trong game: giữ **Menu** + **R2** (đổi được: L2, L3).
   - Chế độ **Phím nóng**: game dừng, hiện bản dịch; bấm lại để chơi tiếp.
   - Chế độ **Tự động**: bấm một lần, sau đó RetroArch gửi màn hình liên tục, chỉ dịch lại khi màn đổi; bấm lại để dừng.

Lỗi gần nhất hiện ở cuối màn Dịch game; chi tiết trong `logs/gametrans.log`.

## Lệnh

```
loongmate gametrans status
loongmate gametrans set --enabled on|off --engine offline|gemini|anthropic|openai --source en|ja|zh|ko|auto \
                        --mode hotkey|auto --hotkey r2|l2|l3 --all-games on|off --model NAME --base-url https://...
loongmate gametrans key set gemini|anthropic|openai --value KEY | key clear PROVIDER | key import
loongmate gametrans game on|off ROM
loongmate gametrans pack status|build|remove ROM
loongmate gametrans viethoa status ROM | viethoa play ROM --accept-disclaimer | viethoa auto ROM --off
loongmate gametrans packs install [--progress] | packs remove --yes
loongmate gametrans test ẢNH.png --out KẾT-QUẢ.png
```
