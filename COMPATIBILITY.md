# Tương thích

Những gì đã kiểm thật trên máy, những gì mới kiểm bằng test tự động, và những gì chưa kiểm. Mục nào chưa chạy trên máy
thật thì ghi rõ.

## Thiết bị và Leaf

| Thiết bị | Leaf | Kết quả |
| --- | --- | --- |
| Miniloong Pocket 1 (RK3566, 1 GB RAM, hai khe microSD) | 0.11.0 | Dùng hằng ngày từ 2026-10-02 |
| Miniloong Pocket 1 | 0.13.0 | Cập nhật từ 0.11.0 ngày 2026-10-17: LoongMate giữ nguyên (60/60 file giống hệt), Agent tự chạy lại, cấu hình RetroArch không đổi |
| Thiết bị / OS khác | — | Chưa hỗ trợ |

Leaf hiện chỉ có bản cho Pocket 1 (`platforms/mlp1`). LoongMate đọc danh mục hệ máy của bản Leaf đang cài, nên phần lớn
thay đổi thư mục giữa các bản Leaf được tự nhận ra. Tự tiếp tục dựa vào dòng log `launch-game requested … resume=…` và
socket `jawakad`; nếu bản Leaf sau đổi chúng, game chỉ chạy lại từ đầu như Leaf gốc.

## Thẻ nhớ

| Cấu hình | Kết quả |
| --- | --- |
| Một thẻ (thẻ Leaf) | Hỗ trợ |
| Hai thẻ, thẻ Leaf + thẻ game | Hỗ trợ; hai thẻ có thể đổi `/mnt/sdcard` ↔ `/media/sdcard1` sau khởi động, app tự nhận lại |
| FAT32 (vfat) | Đã kiểm |
| exFAT | Chưa kiểm |

## Emulator và đường dẫn save

| Emulator | Save/state | Đã kiểm trên máy |
| --- | --- | --- |
| RetroArch (mọi core) | `Saves/<tên core>/`, `States/<tên core>/` trên thẻ chứa ROM | Có |
| Mupen64Plus (N64) | `Saves/`, `.userdata/mlp1/mupen64plus/` | Có |
| Flycast (Dreamcast) | `Saves/Flycast/`; BIOS ở `BIOS/dc/` | Có (chạy bằng BIOS giả lập reios khi thiếu BIOS thật) |
| PPSSPP (PSP) | `.userdata/mlp1/ppsspp/` | Chưa |
| DraStic (NDS) | dữ liệu riêng của DraStic | Chưa |
| YabaSanshiro (Saturn) | `.userdata/mlp1/yabasanshiro/` | Chưa |

## BIOS giả lập

Thiếu BIOS thật thì các hệ sau vẫn chạy phần lớn game; LoongMate báo là ghi chú, không phải cảnh báo.

| Hệ | Emulator | Cơ sở |
| --- | --- | --- |
| Dreamcast | Flycast | Log trên máy: "Did not load BIOS, using reios" |
| PlayStation | PCSX-ReARMed | Cấu hình Leaf `pcsx_rearmed_bios = "auto"` |
| GBA | mGBA | BIOS tích hợp |
| NDS | DraStic | BIOS và firmware giả lập |

## Phần mềm phát triển

| Thành phần | Phiên bản |
| --- | --- |
| Python trên máy | CPython 3.12 ARM64 (đi kèm gói) |
| Python để chạy test trên PC | 3.10 – 3.12 |
| Binary native | AArch64, glibc ≤ 2.38 (CI kiểm) |
