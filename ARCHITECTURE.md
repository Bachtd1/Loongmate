# Kiến trúc LoongMate

LoongMate gồm hai phần chạy trên máy: giao diện C (`lm-home`) và engine Python 3.12 (chỉ thư viện chuẩn). Giao diện
không tự đọc hay ghi thẻ nhớ; mọi việc đi qua engine bằng `fork` + `execv` với mảng tham số (không qua shell), kết quả
trả về là JSON.

```
Leaf ── Apps → LoongMate.pak/launch.sh ── bin/lm-home (C, Catastrophe/SDL2)
                                              │  execv: python3 engine/cli.py <lệnh> --json
                                              ▼
                                         engine/*.py ──► thẻ SD1/SD2, .userdata/mlp1/LoongMate/
Leaf ── Services → LoongMateAgent.pak ── engine/agent.py (vòng lặp nền: tự sao lưu, pin/nhiệt, tự tiếp tục)
```

## Engine (`engine/`)

| Nhóm | Module | Việc |
| --- | --- | --- |
| Điểm vào | `cli.py`, `cli_impl.py` | Mọi lệnh; `--json` cho giao diện |
| Nền tảng | `probe.py`, `leafsys.py`, `jawaka.py` | Đọc `env.sh` của Leaf, tìm thẻ (SD1/SD2 có thể đổi chỗ), danh mục hệ máy của bản Leaf đang chạy, socket `jawakad` |
| Save | `savevault.py`, `autobackup.py`, `remote.py`, `transfer.py` | Snapshot theo nội dung, khôi phục, kéo về PC/NAS |
| Thư viện | `inventory.py`, `doctor.py`, `batchfix.py`, `dedupe.py`, `hashcache.py`, `artwork.py`, `bios.py` | Quét, phát hiện vấn đề (mã lỗi ở `docs/MA-LOI.md`), sửa có nhật ký và Hoàn tác |
| Thêm game | `importer.py`, `web.py`, `homebrew.py`, `libsource.py`, `nodes.py`, `patcher.py` | Nhập từ `Import/`, tải https, homebrew, thư viện riêng, patch IPS/UPS/BPS |
| Leaf | `leafconfig.py`, `autoresume.py`, `hottabs.py` | Sao lưu cấu hình RetroArch, tự tiếp tục qua Agent |
| Máy | `health.py`, `telemetry.py`, `status.py`, `update.py` | Chẩn đoán, gói hỗ trợ, lịch sử pin/nhiệt, cập nhật có quay lui |
| Giao diện dự phòng | `ui_screens.py`, `ui_sdl.py`, `tui.py` | Giao diện Python khi `lm-home` không chạy được |

Dữ liệu tĩnh: `systems.json` (hệ máy, thư mục, đuôi file, BIOS) và `bios_hashes.json` (md5 công khai). Khi máy có danh
mục của Leaf (`.system/leaf/platforms/mlp1/defaults/systems.json`), Library Doctor dùng nó để biết chính xác game nào
Leaf sẽ hiện.

## Giao diện (`native/src/`)

`lm_home.c` (màn chính) và mỗi nhóm màn một file (`lm_library.c`, `lm_saves.c`, `lm_web.c`, `lm_import.c`…). `lm_ui.c`
là bộ khung danh sách + khung giải thích + hộp xác nhận dùng chung; `lm_run.c` chạy engine và đọc JSON (cJSON).
Build chéo ARM64 trong Docker (`native/build.sh`), thử bằng emulator ARM64 (`native/tools/emu-shot.sh`).

## Cách một thao tác ghi được làm an toàn

```
XEM TRƯỚC → XÁC NHẬN (mặc định Huỷ) → STAGING trên thẻ đích → fsync → KIỂM (SHA-256/md5) → đổi tên nguyên tử → NHẬT KÝ
```

- File bị thay hoặc dọn đi vào `Quarantine/<thời điểm>/`, không bị xoá.
- Sửa hàng loạt ghi nhật ký từng bước vào `.userdata/mlp1/LoongMate/`; Hoàn tác chạy ngược nhật ký.
- Khôi phục save tự sao lưu hiện trạng trước, từ chối khi emulator còn chạy.
- Mất điện giữa chừng: lần mở sau, engine đọc nhật ký và staging còn sót để hoàn tất hoặc quay lui.

## Test

`tests/` có hơn 1000 test `unittest`: thẻ giả trong thư mục tạm, máy chủ HTTPS giả, giả lập mất điện giữa các bước ghi,
socket `jawakad` giả. CI chạy trên Ubuntu với Python 3.12 và kiểm binary ARM64 (`.github/workflows/test.yml`).
