<img src="branding/loongmate.svg" width="96" align="right" alt="">

# LoongMate

[![test](https://github.com/Bachtd1/Loongmate/actions/workflows/test.yml/badge.svg)](https://github.com/Bachtd1/Loongmate/actions/workflows/test.yml)
[![Release](https://img.shields.io/github/v/release/Bachtd1/Loongmate?include_prereleases)](https://github.com/Bachtd1/Loongmate/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Ứng dụng bảo vệ, chẩn đoán và phục hồi cho **Miniloong Pocket 1** chạy **Leaf**: giữ save an toàn, tìm và sửa lỗi thư viện
(kể cả game Leaf không hiện), giữ cấu hình RetroArch qua các lần cập nhật Leaf. Chạy ngay trên máy, điều khiển bằng nút,
không cần máy tính hay dòng lệnh.

*English summary below.*

| Màn chính | Danh sách vấn đề | Chọn thẻ lưu game |
| --- | --- | --- |
| ![Màn chính](docs/images/home.png) | ![Danh sách vấn đề](docs/images/issues.png) | ![Chọn thẻ](docs/images/card-picker.png) |

*Ảnh chụp trong emulator ARM64 với thẻ giả (pin, dung lượng và lỗi "không phải FAT32" là của môi trường giả, không phải máy thật).*

## LoongMate làm gì

- **Save an toàn**: sao lưu save/state của cả hai thẻ (tự động sau mỗi lần thoát game khi bật Agent), khôi phục từng game,
  tự sao lưu hiện trạng trước khi ghi, từ chối khi game còn chạy.
- **Thư viện đúng như Leaf thấy**: đối chiếu với danh mục hệ máy của chính bản Leaf đang cài, báo game Leaf không hiện
  (ví dụ CPS1/2/3 ngoài thư mục ARCADE), ảnh bìa lệch tên, `.cue` hỏng, đĩa thiếu `.m3u`, ROM trùng giữa hai thẻ, BIOS.
  Sửa có xem trước và **Hoàn tác**.
- **Thêm game an toàn**: Nhập game từ `Import/`, Tải từ web (https, tải tiếp khi mất mạng, gắn ảnh bìa khớp game),
  Homebrew Hub (chỉ game có giấy phép rõ), thư viện riêng trên PC/NAS. Hỏi lưu vào SD1 hay SD2.
- **Cấu hình Leaf**: sao lưu `retroarch.cfg`, override, remap, core option; so từng khoá trước/sau cập nhật Leaf;
  khôi phục theo nhóm.
- **Tự tiếp tục** chỗ đang chơi khi mở game từ Games/Favorites, không vá file nào của Leaf.

## Tình trạng tính năng

Đã kiểm trên máy thật với Leaf 0.11.0 và 0.13.0 (xem [COMPATIBILITY.md](COMPATIBILITY.md)).

| Tính năng | Kênh |
| --- | --- |
| SaveVault (sao lưu/khôi phục save), Agent tự sao lưu | Ổn định |
| Library Doctor, Sửa an toàn, Sửa hàng loạt + Hoàn tác, Dọn ROM trùng | Ổn định |
| Nhập game, BIOS (kiểm md5, cài từ Import/) | Ổn định |
| Chọn thẻ lưu game, Game mới tải về, Tìm kiếm game | Ổn định |
| Sao lưu cấu hình Leaf/RetroArch | Thử nghiệm |
| Tải từ web, gắn ảnh bìa từ trang tải | Thử nghiệm |
| Tự tiếp tục qua Agent (mặc định tắt) | Thử nghiệm |
| Homebrew Hub, Thư viện riêng (PocketBridge), Patch ROM | Thử nghiệm |
| Đường dẫn save của DraStic/PPSSPP | Chưa kiểm trên máy |

*Ổn định*: đã dùng thật trên máy, có test tự động. *Thử nghiệm*: chạy được, còn giới hạn hoặc mới kiểm trên một máy.

## Cài nhanh

1. Tải `LoongMate-AllInOne.zip` ở mục [Releases](../../releases).
2. Tắt máy, cắm thẻ chứa Leaf vào máy tính, chép **nội dung** thư mục `THE-NHO` vào gốc thẻ (gộp với `Apps` có sẵn).
3. Bật máy, mở **Apps → LoongMate**. Lần đầu app tự cài Python đi kèm (khoảng 3 giây).
4. Nên bật **Settings → Services → LoongMate Agent** (*Start with Leaf* + *Run*).

Hướng dẫn đầy đủ: [INSTALL.md](INSTALL.md). Cập nhật Leaf không xoá LoongMate: [docs/CAP-NHAT-LEAF.md](docs/CAP-NHAT-LEAF.md).
Gỡ: xoá `Apps/mlp1/LoongMate.pak` và `Apps/mlp1/LoongMateAgent.pak`.

## Nguyên tắc an toàn

- Không sửa kernel, bootloader, phân vùng hệ thống hay file của Leaf.
- Không tự xoá file: file bị thay hoặc dọn đi vào `Quarantine/`; sửa hàng loạt có nhật ký và Hoàn tác.
- Mọi thay đổi có bước xem trước và hộp xác nhận mặc định *Huỷ*.
- Thẻ FAT32 và mất điện đột ngột vẫn có rủi ro: app giảm rủi ro bằng staging trên thẻ đích, fsync, kiểm SHA-256 và
  nhật ký khôi phục, nhưng không hứa "không bao giờ mất dữ liệu". Hãy giữ một bản sao save ngoài máy.

## Nội dung và bản quyền

LoongMate **không** kèm, không tải và không chỉ đường tới ROM hay BIOS thương mại. Tải từ web là trình tải chung cho
link bạn tự đưa vào; nó không chạy JavaScript và không vượt quảng cáo, đếm ngược hay captcha. Chỉ tải và nhập nội dung
bạn có quyền sử dụng. BIOS phải do bạn tự trích từ máy của mình.

## Tài liệu

| Tài liệu | Nội dung |
| --- | --- |
| [INSTALL.md](INSTALL.md) | Cài đặt, sử dụng, sao lưu ra PC/NAS |
| [COMPATIBILITY.md](COMPATIBILITY.md) | Phiên bản Leaf và tính năng đã kiểm |
| [docs/](docs/) | Nhập game, Tải từ web, Homebrew, Thư viện riêng, Patch ROM, Cập nhật Leaf |
| [docs/MA-LOI.md](docs/MA-LOI.md) | Mã lỗi `LM-…` và cách xử lý |
| [PRIVACY.md](PRIVACY.md) | App dùng mạng khi nào, gói hỗ trợ chứa gì |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Engine, giao diện, cách ghi an toàn |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Build, test, gửi đóng góp |
| [SECURITY.md](SECURITY.md) | Báo lỗ hổng bảo mật |
| [ROADMAP.md](ROADMAP.md) | Dự định, và những gì sẽ không làm |
| [CHANGELOG.md](CHANGELOG.md) | Thay đổi theo phiên bản |

## Phát triển

Giao diện native C (thư viện Catastrophe của Leaf) gọi engine Python 3.12 (chỉ thư viện chuẩn) qua dòng lệnh và JSON.
~1060 test tự động: `python -m unittest discover -s tests`. Build, đóng gói, thử trong emulator ARM64: [CONTRIBUTING.md](CONTRIBUTING.md).

Phát triển bởi **BACHTD**. Giấy phép [MIT](LICENSE).

---

## English

**LoongMate** is an on-device protection, diagnostics and recovery app for the **Miniloong Pocket 1** running the
**Leaf** custom firmware. It runs as a regular app from Leaf's Apps tab, is fully button-driven, and never modifies
Leaf's own files.

- **Saves**: incremental snapshots of saves/states on both SD cards (automatic after each game with the Agent service),
  per-game restore with a pre-restore backup; restores are refused while an emulator is running.
- **Library Doctor**: checks your library against the system catalog of the Leaf version you actually run, so it finds
  games Leaf will not show (for example CPS1/2/3 folders on Leaf 0.13), misnamed cover art, broken `.cue` files, missing
  `.m3u` playlists, duplicates across cards and BIOS status. Fixes are previewed and can be undone.
- **Adding games**: import from an `Import/` folder (header-based system detection, safe zip extraction, BIOS recognised
  by md5), generic HTTPS downloads with resume, licensed homebrew, or your own PC/NAS library. Asks which card to use.
- **Leaf config backup**: snapshots of `retroarch.cfg`, overrides, remaps and core options, with a per-key diff after a
  Leaf update and grouped restore.
- **Resume from Games/Favorites** (experimental, off by default): loads your latest state like the Recents tab does,
  without patching Leaf.

Install: download `LoongMate-AllInOne.zip` from Releases, copy the contents of `THE-NHO` to the root of your Leaf SD card,
then open **Apps → LoongMate**. The bundled Python runtime unpacks itself on first launch.

LoongMate ships no ROMs or BIOS files and does not bypass ads, countdowns or captchas. Only download and import content
you are entitled to use. Tested on real hardware with Leaf 0.11.0 and 0.13.0; documentation is in Vietnamese.
MIT licensed. Developed by **BACHTD**.
