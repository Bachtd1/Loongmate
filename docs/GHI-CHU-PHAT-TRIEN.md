# Ghi chú phát triển LoongMate (lưu trữ)

> Nhật ký phát triển giai đoạn đầu (tháng 10/2026), giữ lại để tham khảo. Số liệu và trạng thái ở đây có thể đã cũ —
> xem README.md và COMPATIBILITY.md cho tình trạng hiện tại.

Bộ công cụ bảo trì, sao lưu tăng tiến và kiểm toán thư viện cho máy chơi game cầm tay
**Miniloong Pocket 1** (Rockchip RK3566, màn 4:3 960×720, hai khe microSD) chạy **Leaf CFW**.

Bắt đầu từ một tài liệu nghiên cứu sản phẩm nội bộ (không công bố).

> **Lời hứa sản phẩm:** mở một app để biết Pocket 1 có ổn không, save đã an toàn chưa, và library
> có lỗi gì cần sửa.

---

## 🔎 Hợp đồng Leaf — đã kiểm chứng trên thiết bị thật (2026-10-02)

Đọc trực tiếp từ thẻ của một máy Pocket 1 đang chạy Leaf, nguồn là
`.system/leaf/platforms/mlp1/launcher/env.sh` (chính Leaf ghi: *"Source this file from launchers,
paks, and platform scripts"*). **Bốn giả định trước đó của tôi đều sai** — nay đã sửa theo đúng
hợp đồng:

| Hạng mục | Tôi từng đoán | Thực tế trên máy |
|---|---|---|
| Gốc thẻ | `/mnt/SDCARD` | **`/mnt/sdcard`** (viết thường; `/mnt/SDCARD` là của tg5040) |
| Thẻ khe 2 | `/mnt/SDCARD2` | **`/media/sdcard1`** (`$UMRK_SECONDARY_SDCARD_PATH`) |
| Dữ liệu app | `$USERDATA_PATH/LoongMate` | **`$SDCARD_PATH/.userdata/mlp1/LoongMate`** (có tầng platform) |
| Metadata pak | `manifest.json` | **`pak.json`** (`name`, `icon`, `platform`, `pak_version`, `min_jawaka_version`) |

Những gì cũng đã xác nhận: userland **ARM64 64-bit** (đọc ELF header binary của Leaf);
`SDCARD_PATHS` / `SAVES_PATHS` / `STATES_PATHS` là danh sách **phân cách bằng `:`** theo từng thẻ
(`UMRK_ENV_VERSION=2`); SSH server của Leaf dùng **dropbear**, state ở
`.userdata/mlp1/umrk-ssh-server/`; tên thư mục hệ máy thật gồm cả `CPS1/CPS2/CPS3/FBNEO/MAME2003/MAME2010/MD32X`
mà danh mục ban đầu không có. `launch.sh` nay **nạp `env.sh`** thay vì tự đoán đường dẫn.

Vẫn **chưa** biết: Leaf có `python3` hay không (rootfs không nằm trên thẻ), và có `/dev/net/tun`
hay không — cả hai cần chạy lệnh trên máy.

## 📊 Trạng thái thật của từng phần

Bảng này nói đúng mức độ đã kiểm chứng, không nói quá. Phần nào chưa chạy trên máy thật thì ghi rõ.

| Thành phần | Trạng thái | Đã kiểm chứng tới đâu |
|---|---|---|
| Core engine (probe, inventory, doctor, savevault, health) | Dùng được | 43 test tự động chạy trên dữ liệu thật, **đều xanh** |
| CLI (`engine/cli.py`) | Dùng được | chạy thật end-to-end trên PC |
| Trình giả lập trên PC | Dùng được | 14 test API, gồm 8 test bảo mật, đã chứng minh biết báo đỏ |
| Sao lưu ra thư mục cục bộ (SD2/USB) | Dùng được | push/pull/restore end-to-end thật |
| Sao lưu ra PC | **Dùng được** | `pc/pull_saves.py` kéo qua Tailscale (máy không có `sftp`); đã kéo thật 89 MB |
| Giao diện trên máy (`engine/ui_sdl.py`) | **Chạy thật** | SDL2 qua ctypes, driver `wayland`; 7 màn có khung giải thích + hộp xác nhận |
| Dọn ROM trùng / xoá ROM (`engine/dedupe.py`) | Dùng được | trên thẻ thật: 203 file trùng, thu hồi ~2 GB cho SD1; bản có save không bao giờ bị xoá |
| Giao diện native (C, Catastrophe) | **Dùng được** | `bin/lm-home` trong `LoongMate.pak`, mã ở `native/`; `launch.sh` rơi về giao diện SDL Python nếu native không khởi động được (một app duy nhất) |
| Nâng cấp 2026-10-02 | **Dùng được** | chuyển game giữa thẻ, ảnh bìa chéo thẻ, sửa hàng loạt + hoàn tác, BIOS md5, sao lưu khi thoát game, lịch sử pin/nhiệt, Remote Nodes, tự Rescan Library, PC kéo save, Home native Catastrophe — xem INSTALL.md mục 10 |
| Cài trên Pocket 1 thật | **Đã cài và chạy thật** | sao lưu 191 file save thật, verify đủ 191 file, quét 2 thẻ trong 14 giây |
| Quét thư viện thật | Đã chạy | quét 3.600+ ROM thật trên thẻ Leaf: tìm 2 file `.cue` hỏng, đề xuất 1 bản sửa an toàn |

**Nguyên tắc an toàn** (theo tài liệu nghiên cứu): chạy hoàn toàn ở user-space; read-only khi quét;
mọi thay đổi phải xác nhận; không sửa kernel/boot hook/`.system`; không format thẻ; không tải
ROM/BIOS; không xoá file — chỉ chuyển vào `Quarantine/`.

---

## 🌟 Tính năng

### Home — Readiness
Trả lời 4 câu hỏi trong 2 giây đầu: **máy có ổn không** (pin, nhiệt độ, sạc), **thẻ có ổn không**
(dung lượng, filesystem, read-only), **save đã an toàn chưa** (số snapshot, thời điểm gần nhất),
**library có lỗi không** (số issue theo mức độ). Số liệu library dùng cache và **luôn kèm mốc thời
gian** — không bao giờ hiện số cũ như thể là hiện tại.

Khi không đọc được cảm biến, LoongMate hiện **"không đọc được"** chứ không hiện số bịa.

### SaveVault — sao lưu & khôi phục
* **Tăng tiến thật**: so sánh size/mtime, chỉ băm SHA-256 file đã đổi; lưu trữ theo nội dung
  (content-addressed) nên file không đổi **không bị chép lại**. Đo thật: snapshot thứ hai khi không
  có gì đổi tốn 0 lần băm và gần như 0 byte.
* **Nguyên tử**: ghi `.partial` → `fsync` → đổi tên; `pak.json` (commit marker) ghi **sau
  cùng**, nên snapshot dở dang không bao giờ bị coi là hợp lệ.
* **Khôi phục an toàn**: dry-run không ghi một byte; tự tạo pre-restore snapshot; nhật ký chống mất
  điện kèm `recover --rollback`; xung đột thì **giữ cả hai bản** — bản đang chơi được cất vào
  `SaveVault/conflicts/`, không ném vào thư mục `Saves/` của bạn.
* **Chặn khi thẻ sắp đầy**: tính cả dung lượng sắp ghi, không chỉ dung lượng còn trống.
* `verify` băm lại toàn bộ snapshot để chứng minh nó thật sự đọc lại được.

### Library Doctor — kiểm toán thư viện
Chỉ đọc khi quét. Kiểm: filesystem thẻ phụ, cấu trúc thư mục chuẩn, phần mở rộng theo
`systems.json` (**dùng đúng tên thư mục Leaf**: `NES`, `SNES`, `PSX`, `GENESIS`, `SATURN`… kèm
alias legacy), path traversal trong `.cue`/`.m3u` (**cả dạng không đặt trong ngoặc kép**), `.cue`
hỏng, sai hoa/thường, game đa đĩa thiếu `.m3u`, BIOS thiếu, save mồ côi, trùng tên giữa hai thẻ,
dung lượng thấp.

Thư mục hệ máy không có trong danh mục **được báo ra**, không bị bỏ qua im lặng — vì "bỏ qua im
lặng" chính là cách một thư viện chưa từng được kiểm vẫn hiện màu xanh.

### Device Health & Support Bundle
Chẩn đoán không phá hoại, rồi xuất `loongmate_support_<timestamp>.zip` gồm `summary.json`,
`checks.txt`, `mounts.txt`, `environment.txt`, `library_issues.json`, `logs.txt`.

**Gói chỉ chứa dữ liệu thật** — không có log bịa. Toàn bộ nội dung đi qua bộ lọc bí mật (mật khẩu,
`psk`, `wpa_passphrase`, `Authorization: Bearer`, JWT, API key, SSH private key). Privacy Mode ẩn
danh tên game **ở mọi file trong gói**, không chỉ một file.

---

## 📁 Cấu trúc dự án

```text
pocket1-app/
├── engine/                  # Core engine (Python 3, chỉ stdlib)
│   ├── probe.py             # sysfs pin/nhiệt, mount & filesystem, biến môi trường Leaf
│   ├── inventory.py         # quét ROM/ảnh/save, kiểm biên đường dẫn
│   ├── doctor.py            # luật kiểm toán + Quarantine
│   ├── savevault.py         # snapshot tăng tiến, object store, journal, rollback
│   ├── health.py            # chẩn đoán + gói hỗ trợ đã lọc bí mật
│   ├── remote.py            # backend từ xa: thư mục cục bộ và SFTP
│   ├── config.py            # config.json (từ chối lưu mật khẩu thô)
│   ├── cli.py               # CLI đầy đủ
│   └── tui.py               # giao diện D-pad chạy trên máy
├── systems.json             # danh mục hệ máy theo tên thư mục Leaf + alias
├── tests/                   # 43 test (suite, hồi quy, API)
├── pak/                     # gói ứng dụng cho Leaf + build.sh
├── simulator/               # trình giả lập Pocket 1 trên PC
├── tailscale/               # script bật/tắt Tailscale trên máy
├── INSTALL.md               # cài & dùng trên máy thật
└── TAILSCALE.md             # cài Tailscale cho Pocket 1
```

---

## 🚀 Bắt đầu nhanh

**Trên máy thật** → đọc [INSTALL.md](INSTALL.md). **Cập nhật Leaf** → đọc [docs/CAP-NHAT-LEAF.md](docs/CAP-NHAT-LEAF.md).

**Thử trên PC trước:**

```powershell
# Trình giả lập 960x720, kéo được pin/nhiệt độ/trạng thái thẻ
python simulator/server.py 8080      # mở http://127.0.0.1:8080/

# CLI trên một thẻ đã cắm vào PC
python engine/cli.py --primary E:\ readiness
python engine/cli.py --primary E:\ scan
python engine/cli.py --primary E:\ backup
python engine/cli.py --primary E:\ restore --dry-run
python engine/cli.py --primary E:\ verify
```

Phím trong trình giả lập: mũi tên / `WASD` = D-pad, `Enter`/`J` = A, `Esc`/`K` = B, `U` = X (sao
lưu nhanh), `I` = Y (xuất gói hỗ trợ).

Lệnh đầy đủ: `readiness scan backup restore health bundle verify recover endpoint push pull`.
Mã thoát: `0` thành công, `1` thất bại, `2` lỗi sử dụng/môi trường, `3` `scan` có issue critical.
Thêm `--json` cho mọi lệnh nếu muốn parse bằng máy.

---

## ✅ Kiểm thử

```powershell
python -m unittest tests.test_suite tests.test_engine_regressions tests.test_api
```

43 test, chạy **hành vi thật** (không grep mã nguồn, không ghim hằng số chết). Mỗi test hồi quy
đều đã được chứng minh **biết báo đỏ**: cố tình tái tạo lại lỗi gốc rồi xác nhận test đỏ, sau đó
hoàn nguyên.

Các lỗi được bảo vệ bởi test hồi quy gồm: biên đường dẫn `Roms` vs `Roms_evil`; nhận dạng
filesystem theo tiền tố dài nhất (lỗi khiến bài kiểm tra "SD2 phải FAT32" âm thầm luôn PASS); không
bịa số liệu phần cứng; quét không đổi mtime; sao lưu tăng tiến; traversal `snapshot_id`; tương
thích snapshot định dạng cũ; rollback sau mất điện; `verify_snapshot` phát hiện hỏng; chặn theo
dung lượng; traversal `.cue` không ngoặc kép; thư mục hệ máy Leaf hợp lệ không bị báo nhầm; danh
mục hỏng phải báo rõ; lọc bí mật; Privacy Mode trên ZIP thật; `execute_fix` chặn ngoài vùng; thẻ
SD2 rút giữa chừng; **save cùng kích thước sửa trong cùng giây vẫn phải được sao lưu đúng**; **hai
snapshot cùng giây không được ghi đè nhau**.

---

## 📌 Giới hạn đã biết

- **SFTP chưa chạy với server thật.** Logic đồng bộ, thứ tự commit và ghim host key đã kiểm chứng
  bằng transport giả; phần gọi binary `sftp` thật thì chưa.
- **Chưa chạy trên Pocket 1 thật.** Không rõ Leaf có `python3`, cấp chính xác biến môi trường nào,
  hay pak có được cấp TTY. [INSTALL.md](INSTALL.md) mục 1 có đủ lệnh để bạn tự kiểm.
- **Giao diện native chỉ kiểm chứng trên Pocket 1 qua SSH/offscreen**; nếu nó không khởi động được thì `launch.sh` tự mở giao diện Python (xem INSTALL.md).
- **Danh sách BIOS cố ý bảo thủ**: chỉ điền cho hệ máy có tên file chắc chắn. Hệ nào chưa chắc thì
  để trống, thà thiếu còn hơn báo thiếu BIOS sai.
- **`systems.json` có 6 thư mục Leaf** (`GW`, `PC98`, `PICO8`, `DOS`, `EASYRPG`, `PORTS`) đặt
  `extension_check: false` vì danh sách phần mở rộng chưa kiểm chứng — LoongMate báo rõ là đã bỏ
  qua bài kiểm tra đó thay vì báo sai rồi đưa nhầm ROM vào Quarantine.
