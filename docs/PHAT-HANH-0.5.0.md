# LoongMate 0.5.0-beta — Ghi chú phát hành

Bản này tập trung vào **Việt hoá game GB/GBC/GBA** (dịch AI hoặc Google, câu dài, dấu tiếng Việt), độ ổn định khi tắt máy,
và thêm **Tua nhanh**. Việt hoá vẫn là tính năng **thử nghiệm**.

## a. Đã làm được

- **Các core tách chữ**: ASCII, bảng chữ dịch chuyển, bảng chữ không theo abc, DTE (cặp chữ), Huffman, chữ 16 bit, từ điển,
  kịch bản lệnh. LoongMate tự chọn core hợp với game.
- **Dịch bằng AI** (Gemini, Claude, API tương thích OpenAI — API key của bạn) **hoặc Google Dịch**. Chỉ dịch lời thoại; tên
  nhân vật, vật phẩm, địa danh giữ tiếng Anh để tra cứu hướng dẫn chơi.
- **Xử lý câu dài**: câu tiếng Việt dài hơn câu gốc được **dời** sang chỗ trống (ROM được mở rộng khi cần — giả lập trên Pocket 1
  đọc được ROM lớn hơn), **ngắt dòng** lại theo khung hộp thoại, câu vẫn quá khung thì nhờ AI **rút gọn** (tối đa hai lượt).
- **Dấu phủ**: core mGBA riêng (`mgba_vh`) vẽ dấu tiếng Việt lên đúng chữ trên màn hình cho game chưa có chữ có dấu trong ROM.
- **Kiểm từng byte** mỗi lần dựng bản chơi: có byte ghi ra ngoài vùng chữ là không dùng bản đó.
- **Số đo** (kiểm tự động trên PC):
  - Lớp phủ dấu phủ thêm khoảng **1% thời gian mỗi khung hình** (không game nào có khung vượt 2 ms) — không làm game chậm.
  - GB/GBC: khoảng **96–97% chữ được Việt hoá trọn vẹn** khi dùng AI (có rút gọn câu quá khung).
  - Quét 573 bản ROM GB/GBC/GBA: 560 bản chạy được với bản Việt hoá, 9 bản gặp trục trặc (xem danh sách), 4 bản chưa kiểm được.
- **Sửa trong bản này**: 12 game trước đây treo / màn đen sau khi Việt hoá đã chạy được (giữ ký hiệu là mã của game trong chữ,
  dòng trống cuối câu, chữ IN HOA cho game chỉ có chữ hoa, chữ canh giữa ở màn tiêu đề, bỏ con trỏ giả).
- **Việt hoá nạp một lần**: chơi xong là bản Việt hoá được gỡ khỏi RAM; muốn chơi lại tiếng Việt thì vào LoongMate nạp lại.
  Sửa lỗi tắt máy bị Leaf báo "chưa lưu" / đòi sửa thẻ.
- **Tua nhanh** (mới): Máy → Tua nhanh. Trong game bấm **Menu + R1** (hoặc L1) để bật/tắt, tốc độ 2x / 3x / 4x / tối đa.
  Leaf mặc định chưa gán nút nào cho tua nhanh. Tắt là trả cấu hình RetroArch về như cũ.

## b. Danh sách game

Xem [DANH-SACH-GAME.md](DANH-SACH-GAME.md):

- **Đã test trên máy, chạy tốt** — chỉ game đã được xác nhận bằng chơi thử (bản này chưa có, sẽ cập nhật).
- **Chạy được trên giả lập kiểm tự động, chưa test tay** — 560 bản.
- **Còn lỗi / chưa hỗ trợ** — Pokémon Ruby, Pokémon Sapphire, Sigma Star Saga, Star Wars: Flight of the Falcon, Mega Man & Bass,
  Dragon Warrior Monsters, The Adventures of Elmo in Grouchland. LoongMate tự nhận ra và không Việt hoá ROM các game này.

## c. Còn chờ nâng cấp

- Sửa các game trong danh sách lỗi.
- **Google Dịch** không rút gọn được câu: câu dịch dài quá khung sẽ **giữ tiếng Anh**. Dùng AI để Việt hoá trọn vẹn hơn.
- **Chất lượng câu dịch** do máy tạo, chưa được người kiểm hết; kiểm tự động chưa đi qua mọi màn chơi.
- Kế hoạch: tách lõi Việt hoá dùng chung nhiều máy, **ứng dụng PC**, thêm máy khác (TrimUI…), Tailscale + PocketBridge đi kèm
  bản phát hành.

## d. Tuyên bố miễn trừ trách nhiệm

- LoongMate là **phần mềm miễn phí**. Phần mềm **không kèm và không phân phối ROM** hay BIOS; bạn tự dùng ROM mà bạn sở hữu
  hợp pháp.
- Bản Việt hoá **chỉ được dựng trong RAM**, khoá theo từng máy, **không xuất ra được**. **Cấm chia sẻ hoặc buôn bán** ROM hay
  bản Việt hoá. Tên game và thương hiệu thuộc về chủ sở hữu tương ứng.
- Phần mềm được cung cấp **"nguyên trạng" (as is)**, không có bảo hành dưới bất kỳ hình thức nào. Tác giả không chịu trách nhiệm
  về hỏng save, hỏng dữ liệu hay việc sử dụng sai mục đích.
- Hãy **sao lưu save** trước khi dùng. Bản dịch do máy tạo nên **có thể sai nghĩa**.
