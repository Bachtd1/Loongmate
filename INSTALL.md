# Cài và dùng LoongMate trên Miniloong Pocket 1 (Leaf CFW)

Hướng dẫn cài đặt và sử dụng thực tế trên máy thật. Xem [README.md](README.md) để biết LoongMate
làm gì, và [TAILSCALE.md](TAILSCALE.md) nếu muốn sao lưu qua tailnet.

---

## ⚠️ Đọc trước: chưa có bước nào ở đây được chạy thử trên Pocket 1 thật

Toàn bộ engine đã được kiểm thử tự động (43 test, chạy thật trên dữ liệu thật), nhưng **tôi không
có thiết bị để thử**. Ba điều sau quyết định LoongMate chạy được hay không trên máy bạn, và **chỉ
máy bạn trả lời được**:

| Chưa biết | Vì sao quan trọng | Kiểm bằng (mục 1) |
|---|---|---|
| Leaf có `python3` không | TUI trên máy viết bằng Python; không có thì chỉ dùng được CLI trên PC | `python3 --version` |
| Leaf cấp biến môi trường nào | Quyết định LoongMate tìm thẻ và thư mục dữ liệu ở đâu | `echo "$SDCARD_PATH"` … |
| pak có được cấp TTY không | Quyết định điều hướng bàn phím trong TUI có hoạt động không | chạy thử từ tab Apps |

Nếu `python3` không có, LoongMate **vẫn dùng được đầy đủ** — chỉ là bạn chạy CLI từ PC qua SSH
thay vì bấm trên máy (mục 6).

Những gì LoongMate **không** làm, theo đúng nguyên tắc trong tài liệu nghiên cứu: không sửa kernel,
không đụng boot hook, không ghi vào `.system`, không format thẻ, không tải ROM/BIOS, không xoá file
(chỉ chuyển vào `Quarantine/`).

---

## 1. Kiểm tra thiết bị trước khi cài

Bật **SSH Server** trên máy: `Apps → SSH Server`, ghi lại IP hiện trên màn hình. Từ PC:

```bash
ssh root@<IP-của-Pocket-1>
```

Chạy và **lưu lại output**:

```sh
python3 --version                 # có Python hay không
echo "SDCARD_PATH=$SDCARD_PATH"
echo "SDCARD_PATHS=$SDCARD_PATHS" # danh sách nhiều thẻ, nếu Leaf có cấp
echo "USERDATA_PATH=$USERDATA_PATH"
mount | grep -i sdcard            # thẻ mount ở đâu, filesystem gì
ls /sys/class/power_supply/       # tên driver pin
ls /sys/class/thermal/            # thermal zone
df -h /mnt/sdcard                 # dung lượng còn trống
command -v sftp ssh-keyscan       # cần cho sao lưu từ xa (mục 7)
```

**Đọc kết quả:**

- `python3` có → cài được app trên máy (mục 2). Không có → bỏ qua mục 2, dùng mục 6.
- `USERDATA_PATH` rỗng → LoongMate tự dùng `/mnt/sdcard/.userdata`, vẫn chạy được.
- `mount` không thấy thẻ khe 2 dù đã cắm → nhiều khả năng thẻ **không phải FAT32**; Leaf không
  mount thẻ exFAT. Xem mục 8.
- `/sys/class/power_supply/` rỗng → readiness sẽ hiện "không đọc được" cho pin. Đó là **đúng thiết
  kế**: LoongMate thà nói không biết còn hơn hiện số bịa.

---

## 2. Cài lên máy (đã kèm runtime Python tự giải nén)

Leaf trên Pocket 1 không có Python sẵn trong hệ điều hành. Gói LoongMate đã đóng gói kèm
bản CPython 3.12 ARM64 di động (`loongmate-runtime.tar.gz`). Khi mở app lần đầu trên máy,
`launch.sh` sẽ tự động giải nén bộ runtime này vào `/userdata/loongmate-runtime/` (~2-3 giây)
rồi khởi chạy app.

### Dựng gói

Trên PC, trong thư mục dự án:

```bash
sh pak/build.sh
```

Lệnh này copy `engine/*.py`, `systems.json`, binary native và runtime vào trong pak rồi nén thành
`pak/build/LoongMate.pak.zip`. **Phải chạy lại mỗi khi sửa code trong `engine/`** — pak mang một
bản sao, không phải symlink.

Runtime (~30 MB) **không nằm trong git**: đặt file ở `pak/loongmate-runtime.tar.gz` (gốc tar là
`loongmate-runtime/python/`, CPython 3.12 ARM64), hoặc trỏ `LOONGMATE_RUNTIME_TAR=<file>`. Máy đã có
runtime thì tạo lại được bằng `tar -czf loongmate-runtime.tar.gz -C /userdata loongmate-runtime` qua SSH.
Thiếu file này, `build.sh` vẫn dựng gói nhưng cảnh báo: máy chưa có runtime sẽ báo thiếu Python.

### Chép vào máy

Chọn một cách:

1. **Central Scrutinizer** (quản lý file qua trình duyệt, Leaf có sẵn): tải lên rồi giải nén.
2. **Rút thẻ cắm vào PC**: giải nén `LoongMate.pak.zip`, copy thư mục `LoongMate.pak` vào
   `Apps/mlp1/` ở gốc thẻ (hoặc dùng gói All-In-One chép thẳng thư mục `THE-NHO`).
3. **scp qua SSH**:
   ```bash
   scp -r pak/build/LoongMate.pak root@<IP>:/mnt/sdcard/Apps/mlp1/
   ssh root@<IP> "chmod +x /mnt/sdcard/Apps/mlp1/LoongMate.pak/launch.sh"
   ```

Kết quả phải là:

```
/mnt/sdcard/Apps/mlp1/LoongMate.pak/
├── launch.sh                  # phải có quyền thực thi
├── pak.json
├── bin/lm-home                # binary native
├── loongmate-runtime.tar.gz   # runtime CPython tự giải nén khi mở lần đầu
├── engine/*.py                # engine thật, chạy trên máy
└── res/systems.json
```

> Thẻ FAT32 không lưu được bit thực thi. Thường vfat mount với quyền exec cho mọi file nên vẫn
> chạy; nếu không, chạy `chmod +x` qua SSH như trên.

### Chạy

Trên máy: **Apps → LoongMate**. Nếu không thấy, chạy `System → Rescan Library` rồi quay lại tab Apps.

**Kiểm chứng trước khi tin:** mở app, màn Home phải hiện 4 thẻ. Nếu pin/nhiệt độ hiện "không đọc
được" thì đó là LoongMate báo thật rằng nó không đọc được sysfs trên máy bạn — hãy gửi output lệnh
`ls /sys/class/power_supply/` ở mục 1 nếu muốn hỗ trợ thêm.

**Nếu app mở rồi tắt ngay**, đọc log thật:

```sh
tail -n 50 /mnt/sdcard/.userdata/mlp1/LoongMate/logs/loongmate_$(date +%Y%m%d).log
```

Thông điệp lỗi của `launch.sh` luôn kèm việc cần làm tiếp.

---

## 3. Dùng app trên máy

Bấm **Apps → LoongMate**. App vẽ giao diện thật lên màn hình 960×720 qua SDL2 (Python + ctypes,
dùng chính thư viện SDL và font của Leaf — không cần biên dịch). Đã kiểm trên máy thật 2026-10-02:
driver video là `wayland`, gamepad nhận qua joystick API.

**Nút:** D-pad di chuyển • **A** chọn/mở • **B** quay lại (ở Home là thoát) • X/Y tuỳ màn, chân màn
hình luôn ghi đúng tác dụng trên màn đang mở. Gamepad của Pocket 1 theo bố cục Nintendo: A vật lý là
nút số 1, B là nút số 0. X/Y chưa dò xong — vào **Máy → Kiểm tra nút** để xem số của từng nút.
Nếu cần, ghi đè bằng biến môi trường `LOONGMATE_BTN_A/B/X/Y`.

**Nếu lỡ bị kẹt:** bấm một nút chưa được gán, màn hình hiện "Nút số N chưa được gán"; bấm chính nút
đó thêm lần nữa trong 2 giây là thoát.

| Màn | Có gì |
|---|---|
| Home | 4 thẻ: Máy, Thẻ nhớ, Save, Library — viền xanh/vàng/đỏ theo trạng thái |
| Máy | pin, nhiệt độ, 6 bài chẩn đoán (PASS/FAIL + cần làm gì), xuất gói hỗ trợ, kiểm tra nút |
| Thẻ nhớ | từng thẻ: filesystem, dung lượng, read-only, cảnh báo kèm hành động |
| SaveVault | sao lưu, xác minh, xem trước khôi phục, khôi phục (có xác nhận), rollback |
| Library | quét lại, danh sách vấn đề, áp dụng sửa an toàn, **dọn ROM trùng**, **xoá ROM** |

Mọi màn có **khung giải thích** cho mục đang chọn. Mọi thao tác xoá/khôi phục/sửa đều qua **hộp xác
nhận** liệt kê chính xác những gì sẽ làm, lựa chọn mặc định là **Huỷ**. Tác vụ dài (dọn ROM trùng
mất ~4 phút trên máy) chạy nền và hiện thời gian đã trôi qua.

Nếu giao diện không mở được (thiếu SDL/font), app tự rơi về sao lưu nhanh và ghi lý do vào log;
kết quả lần chạy gần nhất ở `.userdata/mlp1/LoongMate/LAN-CHAY-GAN-NHAT.txt`.

### Dọn ROM trùng và xoá ROM

Leaf và hệ điều hành không cho xoá ROM, nên LoongMate làm việc này — với các chốt an toàn:

- Bản trùng chỉ bị xoá khi **SHA-256 xác nhận còn một bản giống hệt**, và được băm lại ngay trước khi xoá.
- **Bản có save/state đi kèm không bao giờ bị xoá** (hai lớp chặn: lúc lập kế hoạch và lúc thực thi).
- Game có playlist (`.cue`/`.m3u`): bản nằm trong playlist luôn được giữ; xoá lẻ track là hỏng game.
- **Bạn chọn giữ bản ở thẻ nào** (Tự động / SD1 / SD2). Muốn giải phóng SD1 thì chọn giữ ở SD2.
- Xoá một game bất kỳ: xoá kèm track/đĩa/ảnh bìa, **save được giữ lại**.
- Mọi file đã xoá được ghi vào `.userdata/mlp1/LoongMate/deletion-ledger.jsonl`.

Xoá xong vào **System → Rescan Library** để Leaf cập nhật danh sách game.

Qua SSH dùng CLI tương đương:

```sh
cli.py dedupe --compare                       # so MB thu hồi trên từng thẻ, chỉ xem
cli.py dedupe --prefer-card secondary         # xem đề xuất
cli.py dedupe --prefer-card secondary --apply # xoá, có hỏi xác nhận
cli.py rom delete "/mnt/sdcard/Roms/GBA/Ten Game.gba"
cli.py quarantine list | purge
```

## 4. Quy trình dùng hằng ngày

**Trước khi chơi:** mở LoongMate, liếc 4 thẻ. Xanh hết thì chơi.

**Sau khi chơi xong một phiên quan trọng:** bấm **X** để snapshot. Sao lưu là tăng tiến — lần thứ
hai khi không có gì đổi gần như không tốn gì (chỉ ghi manifest), nên cứ bấm thoải mái.

**Mỗi vài tuần:** vào Library Doctor quét, đọc danh sách issue. Mọi sửa chữa đều phải bạn xác nhận;
file bị coi là hỏng được **chuyển vào `Quarantine/`**, không bao giờ bị xoá.

**Khi cần nhờ hỗ trợ:** bấm **Y** để xuất gói chẩn đoán. Gói đã lọc mật khẩu Wi-Fi, SSH key và
token; bật Privacy Mode thì tên game cũng được ẩn danh.

---

## 5. Khôi phục save

**Luôn xem trước đã:**

```sh
python3 /mnt/sdcard/Apps/mlp1/LoongMate.pak/engine/cli.py restore --dry-run
```

Dry-run **không ghi một byte nào**. Nó cho biết file nào sẽ thêm, file nào bị ghi đè, và file nào
đang xung đột.

Khôi phục thật:

```sh
# toàn bộ snapshot mới nhất (sẽ hỏi xác nhận)
python3 .../cli.py restore

# chỉ một file
python3 .../cli.py restore --file "Saves/Pokemon Emerald.srm"

# từ một snapshot cụ thể
python3 .../cli.py restore --snapshot snap_20261002_140558_manual
```

Những gì LoongMate tự động làm để bạn không mất dữ liệu:

- **Tự snapshot hiện trạng trước khi khôi phục** (pre-restore) — luôn có đường lùi.
- **Giữ bản đang chơi khi xung đột**: nếu save trên máy mới hơn bản trong snapshot, bản trên máy
  được chép vào `SaveVault/conflicts/<thời điểm>/` trước khi bị ghi đè. Không bao giờ mất cả hai.
- **Nhật ký chống mất điện**: mất điện giữa chừng thì lần mở sau LoongMate phát hiện và đề nghị
  rollback:
  ```sh
  python3 .../cli.py recover              # xem đã làm tới đâu
  python3 .../cli.py recover --rollback   # lùi về trạng thái trước khi khôi phục
  ```

**Kiểm tra bản sao lưu còn nguyên vẹn** (băm lại và đối chiếu manifest):

```sh
python3 .../cli.py verify
```

Đừng chỉ tin là có snapshot — thỉnh thoảng chạy `verify` để biết nó thật sự đọc lại được.

---

## 6. Dùng CLI từ PC (không cần Python trên máy)

Cách này luôn dùng được, kể cả khi Leaf không có `python3`. Cắm thẻ vào PC (hoặc mount qua mạng),
rồi từ thư mục dự án:

```powershell
python engine/cli.py --primary E:\ --userdata E:\.userdata\LoongMate readiness
python engine/cli.py --primary E:\ --userdata E:\.userdata\LoongMate scan
python engine/cli.py --primary E:\ --userdata E:\.userdata\LoongMate backup
```

`--primary` là gốc thẻ (nơi có `Roms/`, `Saves/`). Thêm `--secondary` nếu có thẻ khe 2.

Áp dụng sửa chữa — **mặc định phải xác nhận**:

```powershell
python engine/cli.py --primary E:\ scan --fix          # in danh sách rồi hỏi y/N
python engine/cli.py --primary E:\ scan --fix --yes    # dùng trong script
```

Chạy trong script thì dùng **mã thoát**: `0` thành công, `1` tác vụ thất bại, `2` lỗi sử dụng/môi
trường, `3` lệnh `scan` tìm thấy issue mức critical. Thêm `--json` cho mọi lệnh nếu muốn parse máy.

Chạy thử trên PC trước khi đụng thẻ thật: `python simulator/server.py 8080` rồi mở
`http://127.0.0.1:8080/` — trình giả lập có khung máy 960×720 và cho bạn kéo pin/nhiệt độ/trạng
thái thẻ để xem LoongMate phản ứng thế nào.

---

## 7. Sao lưu sang PC / NAS / Raspberry Pi

Có hai loại endpoint. **Hãy làm quen với `local` trước** — nó không cần mạng và chứng minh được quy
trình chạy đúng trước khi thêm biến số mạng vào.

### Thư mục cục bộ (thẻ SD2 hoặc USB)

```sh
python3 .../cli.py endpoint add --name sd2 --type local --remote-path /mnt/sdcard2/LoongMateBackup
python3 .../cli.py endpoint test --name sd2
python3 .../cli.py push --endpoint sd2
```

### SFTP sang PC/NAS

> ⚠️ **Phần SFTP chưa từng được chạy với một SSH server thật.** Nó được kiểm thử bằng một transport
> giả mô phỏng `sftp -b` trên thư mục tạm. Logic đồng bộ, thứ tự commit và việc ghim host key đều
> đã được kiểm chứng; phần gọi binary `sftp` thật thì **chưa**. Hãy thử trên dữ liệu không quan
> trọng trước, và luôn `verify` sau khi push.

Cần `sftp` trên máy (kiểm ở mục 1). Dùng SSH key riêng, **không bao giờ dùng mật khẩu** — LoongMate
từ chối lưu mật khẩu thô vào config.

```sh
# 1. Tạo key riêng cho LoongMate
ssh-keygen -t ed25519 -f /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate -N ""
cat /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate.pub
#    -> dán vào ~/.ssh/authorized_keys trên PC/NAS

# 2. Khai báo endpoint
python3 .../cli.py endpoint add --name nas --type sftp \
    --host 192.168.1.50 --user backup \
    --remote-path /srv/pocket1-backup \
    --key /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate

# 3. Ghim host key: in fingerprint ra để bạn xác nhận
python3 .../cli.py endpoint pin --name nas

# 4. Thử rồi đẩy
python3 .../cli.py endpoint test --name nas
python3 .../cli.py push --endpoint nas
```

Lần đầu hãy **đọc kỹ fingerprint** rồi mới xác nhận. Từ đó về sau, nếu host key đổi, LoongMate
**từ chối kết nối** và giải thích vì sao — đừng bỏ qua cảnh báo đó.

Kéo về:

```sh
python3 .../cli.py pull --endpoint nas --snapshot snap_20261002_140558_manual
python3 .../cli.py restore --snapshot snap_20261002_140558_manual --dry-run
```

Dùng Tailscale thì thay `--host` bằng hostname tailnet; xem [TAILSCALE.md](TAILSCALE.md) mục 7, đặc
biệt phần chế độ userspace (chiều ra cần proxy SOCKS5).

---

## 8. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Việc cần làm |
|---|---|---|
| App không hiện trong tab Apps | sai đường dẫn, hoặc chưa rescan | phải là `Apps/mlp1/LoongMate.pak/`; chạy `System → Rescan Library` |
| App mở rồi tắt ngay | thiếu `python3`, hoặc `launch.sh` không có quyền chạy | đọc log ở mục 2; `chmod +x launch.sh` |
| Pin/nhiệt độ hiện "không đọc được" | sysfs khác tên hoặc không có | đúng thiết kế, không phải lỗi; gửi `ls /sys/class/power_supply/` nếu cần hỗ trợ |
| Filesystem hiện `unknown` | không đọc được `/proc/mounts` | LoongMate **không đoán**; nó sẽ không kết luận thẻ sai định dạng khi chưa xác minh được |
| Thẻ khe 2 đã cắm mà không thấy | Leaf không mount thẻ exFAT | thẻ **phải là FAT32**; sao lưu dữ liệu ra, format FAT32, chép lại. LoongMate **không bao giờ tự format** |
| `scan` báo "thư mục hệ máy không rõ" | tên thư mục không thuộc danh mục Leaf | đổi sang tên chuẩn (vd `PS` → `PSX`); LoongMate chỉ báo, không tự đổi tên |
| `scan --fix` từ chối chạy | stdin không phải TTY và thiếu `--yes` | thêm `--yes` nếu bạn thật sự muốn áp dụng không hỏi |
| Backup báo `DISK_LOW_SPACE` | còn dưới 200 MB sau khi ghi | xoá snapshot cũ hoặc giải phóng thẻ; ngưỡng này cố ý chặn để không làm đầy thẻ |
| Lỡ quarantine nhầm file | — | file **vẫn còn**, xem `LoongMate/Quarantine/<thời điểm>/` và `quarantine-index.json`, chép ngược lại là xong |
| Sau khi Leaf update thì mất app | pak nằm trong `Apps/`, không mất; dữ liệu nằm trong `.userdata/` | cài lại pak theo mục 2; snapshot không bị ảnh hưởng |

Dữ liệu LoongMate nằm gọn ở `$USERDATA_PATH/LoongMate/` (mặc định
`/mnt/sdcard/.userdata/mlp1/LoongMate/`): `SaveVault/` (snapshot), `conflicts/`, `Quarantine/`,
`Diagnostics/` (gói hỗ trợ), `logs/`, `config.json`, `known-hosts`.

---

## 9. Gỡ

```sh
rm -rf /mnt/sdcard/Apps/mlp1/LoongMate.pak      # gỡ app
ls /mnt/sdcard/.userdata/mlp1/LoongMate              # DỮ LIỆU vẫn còn ở đây
```

Xoá app **không** xoá snapshot. Nếu muốn xoá sạch cả dữ liệu, hãy **chép snapshot ra chỗ khác
trước** rồi mới `rm -rf /mnt/sdcard/.userdata/mlp1/LoongMate`. LoongMate không ghi gì ra ngoài hai
đường dẫn này, nên gỡ xong là máy sạch.

## 10. Các tính năng nâng cấp (2026-10-02)

Tất cả đều có trong giao diện trên máy và có lệnh CLI tương đương. Mọi thao tác ghi đều qua hộp
xác nhận (mặc định **Huỷ**), có sổ ghi, và chạy xong thì **tự gọi Rescan Library** của Leaf
(dùng đúng giao thức `scan-library` mà Central Scrutinizer dùng; tắt bằng `--no-rescan`).

| Tính năng | Trên máy | CLI |
|---|---|---|
| **Chuyển game sang thẻ kia** — chép, xác minh SHA-256 rồi mới xoá nguồn; lỗi thì hoàn tác cả game. Save được chép sang nhưng **giữ lại ở thẻ cũ** | Thẻ nhớ → Chuyển game | `move <rom> --to secondary --apply`, `move --bulk --to secondary --target-free-mb 3072` |
| **Ảnh bìa chéo thẻ** khi dọn trùng | Library → Dọn ROM trùng → Chép ảnh bìa | `dedupe ... --copy-artwork` |
| **Sửa hàng loạt** (sửa `.cue`, tạo `.m3u`, gộp thư mục legacy, tạo thư mục) + **hoàn tác** | Library → Sửa hàng loạt / Hoàn tác | `fix batch [--apply]`, `fix undo` |
| **Kiểm tra BIOS bằng md5** (hash lấy từ tài liệu libretro, có ghi nguồn; không tải BIOS) | Library → Kiểm tra BIOS | `bios` |
| **Sao lưu tự động khi thoát game** + **lịch sử pin/nhiệt** | Máy → Trạng thái agent / Lịch sử | `agent status`, `history --days 7` |
| **Thiết bị trong mạng**: ping, kiểm cổng (SSH/SMB/Sunshine/RDP), Wake-on-LAN | Máy → Thiết bị trong mạng | `nodes list / ping / probe / wake` |
| **Rescan Library thủ công** | (tự động) | `rescan` |
| **Sức khoẻ pin** (ước lượng: `charge_full` so với 4000 mAh công bố — driver không báo dung lượng thiết kế; xu hướng cần ≥ 7 ngày mẫu) | Máy → Sức khoẻ pin | `battery` |
| **Cập nhật LoongMate** có kiểm SHA-256, tự sao lưu bản đang chạy, lỗi giữa chừng tự trả bản cũ | Máy → Cập nhật LoongMate / Quay về bản trước | `update status / verify / apply / rollback` |
| **Cảnh báo bản sao ngoài máy cũ** (vàng > 3 ngày, đỏ > 14 ngày, hoặc chưa từng kéo) | thẻ SAVE trên Home, màn SaveVault | `readiness` |

### Bật sao lưu tự động (service LoongMate Agent)
1. `System → Rescan Library` để Leaf nhận pak mới.
2. Màn **Services** → **LoongMate Agent** → **Enable** + **Start**.

Agent đọc dòng `game.finish … after writer barrier` trong log của Leaf (chính Leaf đã chờ emulator
ghi xong save), chờ thêm 10 giây rồi tạo snapshot nhãn `auto`. Tối thiểu 10 phút giữa hai lần.
Snapshot tự động được giữ **10 bản riêng**, không đẩy mất 5 bản thủ công của bạn. Mỗi 5 phút ghi
một mẫu pin/nhiệt vào `.userdata/mlp1/LoongMate/telemetry/`.
Chưa kiểm chứng với game thật: chơi thử một game rồi thoát, xem `Máy → Trạng thái agent`.

### PC tự kéo save về (qua Tailscale)
Máy không có `sftp`, nên PC kéo về:

```powershell
python pc\pull_saves.py                          # kéo về %USERPROFILE%\Pocket1Backup\SaveVault
python pc\pull_saves.py --verify                 # băm lại toàn bộ bản trên PC
powershell -ExecutionPolicy Bypass -File pc\install_schedule.ps1   # tự chạy mỗi 6 giờ + khi đăng nhập
```

Chỉ tải phần mới (lần thứ hai không tải gì), xác minh SHA-256 từng file, manifest ghi sau cùng,
không bao giờ xoá gì trên máy. Lịch mặc định chạy với `--keep 30` (giữ 30 snapshot mới nhất trên PC;
chỉ dọn khi lần kéo không có lỗi). Máy offline thì thoát mã 2 và không tạo gì trên PC.
Mỗi lần kéo thành công và băm lại đạt, PC ghi `.userdata/mlp1/LoongMate/offsite.json` lên máy —
Home dựa vào file này để cảnh báo khi bản sao ngoài máy đã cũ. Đã chạy thật: 186 object / 89 MB, lần đầu ~7,6 phút (tailnet
~0,2 MB/s), lần sau 10 giây. Mất thẻ thì chạy `python pcestore_menu.py`: chọn snapshot trên PC, xác nhận, công cụ đẩy lên máy
(object trước, manifest sau, kiểm hash trên máy); sau đó trên máy vào SaveVault → chọn snapshot →
Xem trước khôi phục → Khôi phục. Chi tiết: `pc/restore_to_device.md`.

### Cập nhật LoongMate từ PC
```powershell
python pc\deploy.py --dry-run     # dựng gói + manifest SHA-256, kiểm trên PC
python pc\deploy.py               # build, đẩy, verify + apply trên máy, so md5 từng file
```
Từ chối khi working tree git còn sửa dở (`--allow-dirty` để bỏ qua). Bản đang chạy được sao lưu vào
`.userdata/mlp1/LoongMate/pak-backup/` (giữ 3 bản); quay lại bằng Máy → Quay về bản trước hoặc lệnh
rollback mà `deploy.py` in ra. Cũng có thể chép gói vào `LoongMate-update/<phiên bản>/` trên thẻ rồi
dùng Máy → Cập nhật LoongMate.
Đường dẫn trên máy do script tự dò theo thẻ Leaf (hai thẻ có thể đổi chỗ sau khi khởi động lại).
Khi cập nhật chính Leaf, xem [docs/CAP-NHAT-LEAF.md](docs/CAP-NHAT-LEAF.md): cái gì còn, cái gì mất, cần làm gì.

### Một app LoongMate: giao diện native + dự phòng Python
Trong tab Apps chỉ còn **một** mục **LoongMate**. `launch.sh` mở giao diện native (C, toolkit Catastrophe
của Leaf, `bin/lm-home`); nếu native thiếu hoặc chết ngay lúc khởi động (không mở được SDL/font/thư viện:
mã 126/127, hoặc thoát mã khác 0 trong ~3 giây đầu) thì tự mở giao diện Python `engine/ui_sdl.py` và ghi
lý do vào `LoongMate/logs/`. Bấm thoát bình thường trong native thì không mở Python. Trong native có
Máy → **Mở giao diện Python (dự phòng)**; đặt `LOONGMATE_UI=python` để ép dùng Python. Mã và cách build:
`native/README.md`.

Máy đã cài bản cũ có thêm mục **LoongMate Native**: `pc/deploy.py` tự gỡ `LoongMateNative.pak` khi cập
nhật (đổi tên sang `.old`, sao lưu vào `pak-backup/`, `rollback` trả lại) rồi gọi rescan để Leaf cập nhật
danh sách Apps. Nếu mục cũ vẫn hiện thì tắt/mở lại tab Apps hoặc rescan thủ công.
