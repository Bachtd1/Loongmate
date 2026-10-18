# Khôi phục snapshot từ PC về Pocket 1 (khi mất thẻ / thẻ mới / lỡ xoá save)

1. Bật máy, bật Tailscale trên máy (xem `TAILSCALE.md`), kiểm tra `ssh -p 2222 sshadmin@pocket1 echo OK`.
2. Kiểm bản trên PC còn nguyên: `python pc/pull_saves.py --verify`
3. Chạy công cụ chọn snapshot (có hỏi xác nhận):
   ```
   python pc/restore_menu.py
   ```
   Nó liệt kê các snapshot đang có trên PC (thời điểm, số file, dung lượng, **đã có trên máy chưa**),
   bạn gõ số thứ tự, gõ `yes` để xác nhận, rồi nó đẩy về vault trên máy:
   - object được ghi trước và kiểm SHA-256 ngay trên máy; manifest ghi SAU CÙNG rồi đọc lại so khớp;
   - từ chối nếu object trên PC hỏng/thiếu (chạy lại `--verify` và kéo lại);
   - nếu máy đã có snapshot đó thì không đẩy gì, chỉ in hướng dẫn bước 4.
4. **Công cụ không tự khôi phục save trên máy.** Làm bước này trên máy, có xem trước:
   `LoongMate → SaveVault → chọn snapshot <id> → Xem trước khôi phục → Khôi phục`

## Dùng cho script / giao diện (không tương tác)

```
python pc/restore_menu.py --list --json                       # danh sách snapshot (thêm --offline để không hỏi máy)
python pc/restore_menu.py --push snap_20261002_213000_manual --yes --json
```
`--json` in một đối tượng có `ok`; khi `--push` thành công có thêm `objects_pushed`, `objects_already`,
`bytes_pushed`, `already_on_device`, `next_step`. Mã thoát: 0 ok | 1 lỗi dữ liệu/đẩy |
2 không kết nối được máy | 3 sai cách dùng hoặc không xác nhận.

Cách cũ vẫn dùng được: `python pc/pull_saves.py --push-snapshot <id> [--yes]`.

## Ghi chú

- Đường dẫn vault trên máy mặc định được **dò** theo thẻ Leaf (hai thẻ có thể đổi chỗ mount sau khi khởi động lại):
  `<USERDATA_PATH>/LoongMate/SaveVault`, hiện là `/media/sdcard1/.userdata/mlp1/LoongMate/SaveVault`; đổi bằng
  `--device-vault` hoặc chọn thẻ bằng `--sdcard`. Thử trước vào thư mục sandbox: `--device-vault /userdata/lm-sandbox/SaveVault`.
- Sau mỗi lần kéo thành công và verify đạt, `pull_saves.py` ghi `offsite.json` (mốc "bản sao ngoài máy")
  cạnh thư mục SaveVault trên máy để giao diện cảnh báo khi bản sao ngoài máy đã cũ. Kéo lỗi thì không ghi.
- Lịch tự kéo (`install_schedule.ps1`) mặc định truyền `--keep 30`: sau mỗi lần kéo không lỗi, PC chỉ giữ
  30 snapshot mới nhất và xoá object mồ côi (chỉ trên PC, không bao giờ xoá gì trên máy).

# Cập nhật LoongMate lên máy từ PC (có kiểm hash, có rollback)

```
python pc/deploy.py --dry-run          # dựng gói + verify trên PC, không đụng máy
python pc/deploy.py                    # build -> đẩy -> verify trên máy -> apply -> so md5 từng file
```
Từ chối chạy khi working tree git bẩn (trừ `--allow-dirty`). Cuối cùng in sẵn lệnh rollback. Trên máy:
`python3 .../engine/update.py rollback --apps-dir ... --app-data ... --yes` trả 3 pak về bản sao lưu mới
nhất (`<app-data>/pak-backup/`, giữ 3 bản). Thử trong sandbox bằng `--apps-dir`, `--app-data`, `--remote-dir`
(trên Git Bash nhớ đặt `MSYS_NO_PATHCONV=1`, nếu không `/userdata/...` bị đổi thành `C:/...`).
