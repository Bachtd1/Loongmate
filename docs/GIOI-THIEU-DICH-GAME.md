# Dịch game sang tiếng Việt ngay trên Miniloong Pocket 1

LoongMate 0.4.1-beta đưa **Dịch game** và **Việt hoá cá nhân** lên Pocket 1 (Leaf CFW): chơi game GB / GBC / GBA bằng tiếng Việt,
chạy ngay trên máy, không cần máy tính. Đây là tính năng **THỬ NGHIỆM** — hãy đọc kỹ phần hạn chế bên dưới.

LoongMate là **phần mềm miễn phí (freeware)**: chỉ phát hành bản dựng sẵn, không kèm mã nguồn (điều khoản: `DIEU-KHOAN-SU-DUNG.txt`
trong gói).

## Có gì mới trong 0.4.1-beta

- **Dấu phủ (thử nghiệm)**: với game mà LoongMate chưa vẽ được chữ có dấu vào font, bản Việt hoá vẫn hiện chữ không dấu của
  game, và một lớp phủ trong suốt **vẽ thêm dấu** lên đúng từng chữ đang hiện trên màn hình (học font ngay trên màn hình, không
  cần mạng). Lớp phủ đặt theo đúng khung hình của RetroArch; khi phóng to nhiều lần thì dùng lớp phủ nhỏ hơn để giảm giật.
- Việt hoá nhận thêm game ít chữ và game chỉ có chữ HOA; bản Nhật đã được fan dịch tiếng Anh cũng Việt hoá được.
- Sửa nhiều chỗ có thể làm hỏng game khi vá chữ, và lỗi cấu hình RetroArch của tính năng Dịch game.

(Tính năng chính của 0.4.0-beta vẫn giữ nguyên: Việt hoá tự động khi mở game từ Leaf, Google Dịch khi không có API key, có dấu thật
cho game nhận ra font như Advance Wars 1/2, Heroes of Might and Magic II, Moomin's Tale, NFL Blitz 20-02...)

## Hai cách dịch

| | **Lớp phủ dịch** | **Việt hoá cá nhân** |
| --- | --- | --- |
| Hệ máy | Mọi game RetroArch | **Chỉ GB / GBC / GBA** |
| Cách làm | Chụp màn hình khi chơi, dịch, hiện chữ Việt đè lên | Dịch trước cả game, chữ Việt nằm ngay trong game |
| Ưu điểm | Dùng được cho mọi game | Đúng thời điểm, không giật, không cần mạng khi chơi |
| Nhược điểm | Chậm hơn, có thể giật tiếng | Chỉ game nào bóc tách được chữ |

## Hướng dẫn sử dụng chi tiết

### 1. Cài đặt

1. Tải `LoongMate-AllInOne.zip` ở cuối trang.
2. Tắt máy, cắm thẻ nhớ chứa Leaf vào máy tính, chép **toàn bộ nội dung** thư mục `THE-NHO` vào gốc thẻ (gộp với thư mục `Apps`
   có sẵn, không xoá gì khác).
3. Bật máy, mở **Apps → LoongMate**. Lần đầu app tự cài Python đi kèm (khoảng 3 giây).
4. Bật **Settings → Services → LoongMate Agent**: *Start with Leaf* + *Run*. Việt hoá tự động và dấu phủ cần Agent.

### 2. Chọn công cụ dịch (Máy → Dịch game)

- **Không làm gì**: Việt hoá dùng **Google Dịch** (miễn phí, không cần key; dịch từng câu nên kém ngữ cảnh hơn AI).
- **Khuyên dùng — Google Gemini (miễn phí có giới hạn)**:
  1. Trên điện thoại/máy tính, vào **https://aistudio.google.com/apikey**, đăng nhập Google, bấm **Create API key**, chép key.
  2. Trên Pocket 1: **Máy → Dịch game → Công cụ** → chọn **Gemini**.
  3. Dòng **API key (AI)**, chọn một cách nhập:
     - **A — từ điện thoại** (dễ nhất): máy hiện mã QR, điện thoại cùng Wi-Fi quét mã rồi dán key (trang chỉ có trong Wi-Fi nhà,
       dùng một lần, tự đóng sau 5 phút);
     - **X — từ thẻ**: tạo file `loongmate-ai-keys.txt` ở gốc thẻ, dòng `gemini: KEY` (file tự xoá sau khi lưu);
     - **Y — gõ** bằng bàn phím trên màn hình.
     Key lưu trong bộ nhớ trong của máy (chỉ root đọc), không nằm trên thẻ.
  4. Dòng **Model** để trống là dùng model mặc định của LoongMate.
- Có thể dùng **Claude** (Anthropic) hoặc **API tương thích OpenAI** (nhập thêm **Địa chỉ API** và **Model**). Tài khoản Claude
  Pro / ChatGPT Plus không dùng làm API được — cần API key riêng.
- **Offline**: tải gói dịch offline (OCR + dịch trên máy) cho lớp phủ dịch, không cần mạng khi chơi.

### 3. Lớp phủ dịch (mọi game RetroArch)

1. **Máy → Dịch game** → bật **Dịch game**, chọn chế độ **phím tắt** (Menu + R2) hoặc **tự động** (dịch khi màn hình đổi).
2. Mở game một lần để nó hiện trong danh sách, rồi **bật dịch cho game đó** (hoặc bật "Mọi game").
3. Trong game: bấm **Menu + R2** để dịch màn hình hiện tại.

### 4. Việt hoá cá nhân (GB / GBC / GBA)

1. Mở game một lần từ Leaf để nó hiện trong **Máy → Dịch game**.
2. Chọn game → **X — DỊCH CẢ GAME**. Chờ dịch xong (vài phút tới vài chục phút tuỳ độ dài game). Dừng giữa chừng / mất mạng: chạy
   lại là dịch tiếp phần còn thiếu.
3. Dòng **▶ Chơi bản Việt hoá (thử nghiệm)** cho biết: có dấu / có dấu X% / **KHÔNG DẤU** (kèm lý do).
4. Bấm **A** → đọc trang điều khoản → **Đồng ý và chơi**. LoongMate đóng lại, game mở bằng tiếng Việt.
5. Từ đó mở game từ Leaf (Games, Recents, Favorites) là **tự động** ra tiếng Việt. Muốn chơi bản gốc: dòng ▶ Chơi bản Việt hoá →
   **X — TẮT TỰ ĐỘNG**.
6. Dùng chung file save với game gốc. Bản Việt hoá chỉ dùng được trên máy đã dịch, game gốc trên thẻ không bị sửa.

## Hạn chế hiện tại (đọc trước khi dùng)

- **Nhiều game chưa bóc tách được chữ**: game lưu chữ nén / mã hoá kiểu riêng (một số RPG như Golden Sun, Fire Emblem...) chưa
  Việt hoá được — vẫn dùng được lớp phủ dịch.
- **Chữ có dấu tuỳ game**: chỉ một số game có dấu thật trong ROM. Các game khác dùng **dấu phủ**, có thể **đặt dấu sai chỗ, sai
  chữ, hoặc không hiện dấu** ở một số màn hình; khi không khớp được thì hiện chữ không dấu.
- **Có thể gây giật / lag**: lớp phủ dịch và dấu phủ chạy cùng lúc với game trên máy nhỏ, có thể làm game hoặc âm thanh giật,
  nhất là ở chế độ tự động. Gặp giật: tắt dấu phủ / lớp phủ cho game đó, hoặc dùng chế độ phím tắt.
- Vài câu dịch hỏng mã của game hoặc không vừa khung chữ sẽ **giữ tiếng Anh** (để không làm hỏng game).
- Chất lượng dịch phụ thuộc AI / Google Dịch; Google Dịch có thể tạm giới hạn khi dịch nhiều — chờ rồi chạy lại.
- Việt hoá thật **chỉ GB / GBC / GBA** (file `.gb` `.gbc` `.gba`, không phải `.zip`).

## Báo lỗi, góp ý — rất cần các bạn

Tính năng còn mới, mỗi game một kiểu. Gặp game không Việt hoá được, dấu sai, giật, hay lỗi bất kỳ:
- tạo **[GitHub Issue](https://github.com/Bachtd1/Loongmate/issues)** (ghi **đúng tên file ROM**, mô tả lỗi, ảnh chụp màn hình
  nếu có — chụp bằng Menu + L1, ảnh ở thư mục `Screenshots` trên thẻ), hoặc
- **nhắn tin trực tiếp cho tác giả**.

Mọi báo cáo sẽ được tổng hợp để cải thiện các bản sau.

## Hướng sắp tới

1. **Bóc tách 100% chữ của game GB / GBC / GBA** (kể cả game nén / mã hoá kiểu riêng) và có dấu cho càng nhiều game càng tốt —
   làm xong nhóm này trước.
2. Sau đó mới mở rộng sang **NES / SNES**.
3. **Tối ưu lớp phủ dịch và dấu phủ** để tránh gây giật / lag.

## Miễn trừ trách nhiệm

- LoongMate **không cung cấp** game, ROM hay BIOS, và không chịu trách nhiệm về nội dung bản dịch hay việc sử dụng game. Bạn tự
  chịu trách nhiệm có bản quyền hợp lệ của game.
- Bản Việt hoá **chỉ dùng được trên máy đã dịch**: bản dịch mã hoá theo máy, ROM Việt hoá chỉ nằm trong RAM, **không sửa game
  gốc**, không xuất ra file.
- **KHÔNG mua bán, KHÔNG xin donate, KHÔNG trục lợi** dưới bất kỳ hình thức nào từ bản Việt hoá. Tôn trọng quyền sở hữu trí tuệ
  của nhà phát triển và nhà phát hành game.
- Phần mềm cung cấp "nguyên trạng", không có bảo đảm. Hãy giữ bản sao lưu save quan trọng.

---

**English** — LoongMate 0.4.1-beta (freeware, prebuilt only): game translation overlay for any RetroArch game, and an experimental
personal Vietnamese patch for GB/GBC/GBA games (automatic once accepted, device-only, original ROM untouched; Google Translate
when no API key, Google Gemini recommended). New "accent overlay" draws Vietnamese accents over games whose font cannot be patched
(experimental: may misplace accents or cause lag). Many games cannot be extracted yet. Report problems via GitHub Issues.
Roadmap: 100% text extraction for GB/GBC/GBA first, then NES/SNES, and overlay performance.
