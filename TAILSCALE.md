# Cài Tailscale cho Miniloong Pocket 1 (Leaf CFW)

Hướng dẫn cài Tailscale lên Pocket 1 **ở user-space**, không chạm kernel, không chạm boot hook,
không chạm phân vùng eMMC — đúng theo nguyên tắc an toàn của dự án LoongMate.

> **Tailscale KHÔNG phải một phần của LoongMate.** Tài liệu nghiên cứu đã chốt: LoongMate không
> làm Tailscale installer và không quản lý daemon Tailscale. Bạn cài Tailscale độc lập theo tài
> liệu này; LoongMate chỉ dùng hostname/IP tailnet như một địa chỉ mạng bình thường (xem
> [mục 7](#7-dùng-tailscale-với-savevault-của-loongmate)).

---

## ✅ Đã kiểm chứng trên thẻ thật (2026-10-02)

Đường dẫn trong tài liệu này đã sửa theo hợp đồng thật của Leaf (`env.sh` trên thẻ):
gốc thẻ là `/mnt/sdcard` (viết thường), dữ liệu app ở `$SDCARD_PATH/.userdata/mlp1/`, thẻ khe 2 ở
`/media/sdcard1`. Userland là **ARM64 64-bit** nên gói `arm64` là đúng.

## ⚠️ Những gì vẫn CHƯA kiểm chứng trên máy thật

Tôi viết hướng dẫn này từ tài liệu chính thức của Tailscale và Leaf, **không phải từ một lần cài
thành công trên Pocket 1**. Bốn điều sau quyết định hướng dẫn này chạy hay không, và **chỉ máy bạn
trả lời được**:

| Điều cần biết | Vì sao quyết định | Cách tự kiểm (mục 1) |
|---|---|---|
| Có `/dev/net/tun` không | Không có thì phải chạy chế độ userspace, mất tính trong suốt | `ls -l /dev/net/tun` |
| Có phân vùng Linux ghi được không | FAT32 không lưu được quyền `0600` cho file state của tailscaled | `mount`, `df -h` |
| Thẻ SD có cho chạy file thực thi không | vfat thường mount với quyền exec, nhưng không chắc | `./tailscaled --version` |
| ~~Kiến trúc CPU~~ | **ĐÃ XÁC NHẬN: ARM64 64-bit** (đọc ELF header binary của Leaf trên thẻ) → dùng bản `arm64` | không cần kiểm nữa |

Chỗ nào tôi không chắc, tôi ghi **(CHƯA KIỂM CHỨNG)** ngay tại đó. Nếu một bước thất bại, mục
[8. Xử lý sự cố](#8-xử-lý-sự-cố) có nhánh xử lý.

**Rủi ro thực tế cần cân nhắc trước khi làm:**

- Pocket 1 chỉ có **1 GB RAM**. `tailscaled` chiếm khoảng 50–90 MB. Đừng để nó chạy nền khi đang
  chơi game nặng (PS1/DC/N64) — hãy bật/tắt theo nhu cầu (mục 6).
- Tailscale giữ kết nối thường trực → **tốn pin** rõ rệt khi máy ở chế độ chờ.
- Hướng dẫn này chỉ copy file vào thẻ SD và chạy một tiến trình user-space. Nó **không** ghi vào
  `.system`, không sửa boot hook, nên **gỡ ra là sạch** (mục 9). Nếu máy không boot được vì lý do
  khác, recovery card của Leaf vẫn dùng được như bình thường.

---

## 1. Kiểm tra thiết bị trước khi cài

Bật **SSH Server** trên Pocket 1: `Apps → SSH Server` (Leaf có app này sẵn). Ghi lại IP máy hiện
trên màn hình, rồi từ PC:

```bash
ssh -p 2222 sshadmin@<IP-của-Pocket-1>
```

> SSH Server của Leaf chạy ở cổng 2222, user `sshadmin` (đã kiểm trên máy). Mật khẩu hoặc cách thêm khoá xem trên
> màn hình app SSH Server.

Chạy đúng 6 lệnh sau và **lưu lại output**, cả hướng dẫn này dựa vào chúng:

```sh
# 1. Kiến trúc CPU — phải là aarch64
uname -m

# 2. Có thiết bị TUN hay không? (quyết định chế độ chạy)
ls -l /dev/net/tun 2>&1

# 3. Phân vùng nào ghi được và là filesystem Linux thật?
mount | grep -E "ext4|f2fs|ext2|ext3"
df -h

# 4. Thẻ SD mount ở đâu, filesystem gì?
mount | grep -i sdcard

# 5. Biến môi trường Leaf cấp cho app
echo "SDCARD_PATH=$SDCARD_PATH"
echo "SDCARD_PATHS=$SDCARD_PATHS"
echo "USERDATA_PATH=$USERDATA_PATH"

# 6. Có sẵn công cụ gì để tải và giải nén?
command -v curl wget tar gzip python3
```

**Đọc kết quả:**

- `uname -m` ra `aarch64` → dùng gói `arm64`. Ra thứ khác thì **dừng lại**, hướng dẫn này không đúng cho máy bạn.
- Lệnh 2 ra một dòng `crw-rw-rw- ... /dev/net/tun` → bạn có TUN → dùng **Chế độ A** (mục 4).
- Lệnh 2 ra `No such file or directory` → **Chế độ B — userspace** (mục 5). Đây là trường hợp phổ
  biến trên handheld dùng kernel BSP.
- Lệnh 3 có một dòng `ext4`/`f2fs` ghi được → dùng nó làm thư mục state (an toàn nhất).
  Không có → phải đặt state trên thẻ SD và đọc kỹ cảnh báo FAT32 ở mục 3.
- Lệnh 6 thiếu cả `curl` và `wget` → tải file trên PC rồi copy qua thẻ SD (mục 3, cách 2).

---

## 2. Chọn chế độ chạy

| | Chế độ A — TUN | Chế độ B — userspace |
|---|---|---|
| Điều kiện | có `/dev/net/tun` | không có TUN |
| Cờ chạy | (không cần cờ đặc biệt) | `--tun=userspace-networking` |
| Thiết bị khác SSH/SFTP **vào** Pocket 1 | ✅ bình thường | ✅ được — tailscaled chuyển tiếp TCP đến `127.0.0.1` cùng cổng |
| Pocket 1 kết nối **ra** thiết bị khác | ✅ trong suốt, mọi app | ⚠️ chỉ app biết dùng proxy SOCKS5 (xem mục 7) |
| `ping` trong tailnet | ✅ | ❌ (dùng `tailscale ping`) |

Điểm quan trọng cho LoongMate: trong **Chế độ B**, chiều *vào* vẫn tốt (PC kéo save từ máy), nhưng
chiều *ra* (máy tự đẩy save lên PC/NAS qua SFTP) phải đi qua SOCKS5. Mục 7 hướng dẫn cả hai.

---

## 3. Tải và đặt binary

Tailscale phát hành **binary tĩnh pure-Go, không phụ thuộc glibc** — chạy được trên hệ thống tối
giản như Leaf. Phiên bản stable lúc viết tài liệu: **1.102.4**
(kiểm bản mới nhất tại <https://pkgs.tailscale.com/stable/>).

### Đặt ở đâu

Đặt trên **thẻ SD chính**, không đặt trên eMMC:

```
/mnt/sdcard/.userdata/mlp1/tailscale/
├── tailscaled          # daemon
├── tailscale           # CLI
└── state/              # state + socket (xem cảnh báo FAT32 bên dưới)
```

Lý do: eMMC chỉ 8 GB và do bản phát hành Leaf quản lý; `.userdata/` là vùng state bền vững của
người dùng nên **còn nguyên sau khi Leaf cập nhật OTA**. Tuyệt đối không đặt vào `.system/`.

> ⚠️ **Cảnh báo FAT32 (quan trọng).** Thẻ SD của Leaf là FAT32/vfat. FAT32 **không lưu được quyền
> sở hữu và chmod**, mà `tailscaled` muốn file state có quyền `0600`. Hệ quả có thể là một dòng cảnh
> báo, hoặc tailscaled từ chối chạy **(CHƯA KIỂM CHỨNG trên Leaf)**.
> **Cách xử lý:** nếu lệnh 3 ở mục 1 tìm được một phân vùng `ext4`/`f2fs` ghi được, hãy đặt **state**
> ở đó (ví dụ `/data/tailscale-state`) và chỉ để **binary** trên thẻ SD:
> ```sh
> TS_STATE_DIR=/data/tailscale-state   # đổi theo máy bạn
> ```
> Nếu không có phân vùng Linux nào, thử đặt state trên thẻ SD trước; nếu tailscaled báo lỗi quyền,
> dùng `/tmp/tailscale-state` (nằm trong RAM → **mất sau mỗi lần khởi động lại**, phải `tailscale up`
> lại mỗi lần; dùng auth key tái sử dụng ở mục 4 để bớt thao tác).

### Cách 1 — tải trực tiếp trên máy (nếu có curl/wget)

```sh
TS_DIR=/mnt/sdcard/.userdata/mlp1/tailscale
TS_VER=1.102.4
mkdir -p "$TS_DIR"
cd "$TS_DIR"

# Dùng curl, hoặc đổi sang: wget -O ts.tgz <URL>
curl -fL -o ts.tgz "https://pkgs.tailscale.com/stable/tailscale_${TS_VER}_arm64.tgz"

tar -xzf ts.tgz
mv "tailscale_${TS_VER}_arm64/tailscaled" "tailscale_${TS_VER}_arm64/tailscale" .
rm -rf ts.tgz "tailscale_${TS_VER}_arm64"
chmod +x tailscaled tailscale
```

### Cách 2 — tải trên PC rồi copy (nếu máy không có curl/wget)

1. Trên PC tải `https://pkgs.tailscale.com/stable/tailscale_1.102.4_arm64.tgz`
2. Giải nén, lấy đúng 2 file `tailscaled` và `tailscale`
3. Copy vào thẻ SD, thư mục `.userdata/tailscale/` — qua **Central Scrutinizer** (quản lý file
   qua Wi-Fi bằng trình duyệt, Leaf có sẵn), hoặc cắm thẻ vào PC, hoặc `scp`:
   ```bash
   scp tailscaled tailscale root@<IP>:/mnt/sdcard/.userdata/mlp1/tailscale/
   ```

### Kiểm chứng ngay (đừng bỏ bước này)

```sh
cd /mnt/sdcard/.userdata/mlp1/tailscale
./tailscaled --version
```

- In ra số phiên bản → binary chạy được, thẻ SD cho phép exec. Đi tiếp.
- `Permission denied` → thẻ mount không cho exec. Copy 2 binary sang một phân vùng Linux ghi được
  (tìm ở lệnh 3 mục 1), `chmod +x`, rồi chạy từ đó. Nhớ là nó sẽ **mất sau khi Leaf update** nếu
  bạn đặt ngoài `.userdata/` — ghi lại để cài lại sau.
- `not found` / `Exec format error` → sai kiến trúc. Kiểm lại `uname -m`, tải đúng gói `arm64`.

---

## 4. Chế độ A — có `/dev/net/tun`

Mở **hai** phiên SSH (hoặc dùng `&` như dưới).

```sh
TS_DIR=/mnt/sdcard/.userdata/mlp1/tailscale
TS_STATE_DIR=/mnt/sdcard/.userdata/mlp1/tailscale/state   # đổi sang phân vùng ext4 nếu có (mục 3)
mkdir -p "$TS_STATE_DIR"

# Khởi động daemon (cần quyền root)
"$TS_DIR/tailscaled" \
  --state="$TS_STATE_DIR/tailscaled.state" \
  --socket="$TS_STATE_DIR/tailscaled.sock" \
  --statedir="$TS_STATE_DIR" \
  >> "$TS_STATE_DIR/tailscaled.log" 2>&1 &
```

Đăng nhập vào tailnet:

```sh
"$TS_DIR/tailscale" --socket="$TS_STATE_DIR/tailscaled.sock" up \
  --hostname=pocket1 \
  --accept-routes
```

Lệnh sẽ in một URL. Mở URL đó trên PC/điện thoại và duyệt thiết bị.

**Đỡ phải bấm mỗi lần:** tạo một **auth key tái sử dụng** tại
<https://login.tailscale.com/admin/settings/keys> rồi dùng:

```sh
"$TS_DIR/tailscale" --socket="$TS_STATE_DIR/tailscaled.sock" up \
  --hostname=pocket1 --authkey=tskey-auth-XXXX
```

> 🔒 Auth key là bí mật. Đừng để nó trong file trên thẻ SD mà bạn sẽ chia sẻ, và **đừng đưa nó vào
> support bundle của LoongMate**. (LoongMate có bộ lọc bí mật, nhưng đừng dựa vào đó — hãy truyền
> key qua biến môi trường lúc chạy.)

---

## 5. Chế độ B — không có TUN (userspace networking)

Khác Chế độ A đúng ở cờ `--tun=userspace-networking` và hai cờ proxy:

```sh
TS_DIR=/mnt/sdcard/.userdata/mlp1/tailscale
TS_STATE_DIR=/mnt/sdcard/.userdata/mlp1/tailscale/state
mkdir -p "$TS_STATE_DIR"

"$TS_DIR/tailscaled" \
  --tun=userspace-networking \
  --socks5-server=localhost:1055 \
  --outbound-http-proxy-listen=localhost:1055 \
  --state="$TS_STATE_DIR/tailscaled.state" \
  --socket="$TS_STATE_DIR/tailscaled.sock" \
  --statedir="$TS_STATE_DIR" \
  >> "$TS_STATE_DIR/tailscaled.log" 2>&1 &

"$TS_DIR/tailscale" --socket="$TS_STATE_DIR/tailscaled.sock" up --hostname=pocket1
```

**Cần hiểu đúng về chế độ này:**

- **Chiều vào hoạt động.** tailscaled chuyển tiếp TCP từ tailnet đến `127.0.0.1` **cùng số cổng**.
  Nên SSH server của Leaf (cổng 22) sẽ tới được ở `pocket1:22` trong tailnet — miễn là sshd đang
  lắng nghe trên `0.0.0.0` hoặc `127.0.0.1`.
- **Chiều ra cần proxy.** Mọi kết nối *từ* Pocket 1 *ra* tailnet phải qua SOCKS5 `localhost:1055`.
  Không app nào tự biết điều này.
- `ping` thường **không** hoạt động trong tailnet. Dùng `tailscale ping <tên-máy>`.
- Không có `--accept-routes` theo nghĩa thông thường (không có interface để nhận route).

---

## 6. Kiểm chứng và bật/tắt theo nhu cầu

### Kiểm chứng (chạy thật, đừng đoán)

```sh
TS="/mnt/sdcard/.userdata/mlp1/tailscale/tailscale --socket=/mnt/sdcard/.userdata/mlp1/tailscale/state/tailscaled.sock"

$TS status          # phải thấy pocket1 và các thiết bị khác
$TS ip -4           # địa chỉ 100.x.y.z của Pocket 1
$TS ping <tên-PC>   # đo đường tới PC; cho biết đi direct hay qua DERP relay
```

Từ **PC**, kiểm chiều vào:

```bash
ping pocket1                       # chỉ chạy ở Chế độ A
ssh root@pocket1                   # chạy ở cả hai chế độ
tailscale status | grep pocket1
```

Nếu `$TS status` báo `Logged out` → `tailscale up` lại. Nếu báo `stopped` → `tailscale up` hoặc
`tailscale set --operator=...` không cần thiết ở đây; cứ `up` lại là đủ.

### Script bật/tắt

Dự án có sẵn hai script dùng được luôn, ở thư mục [`tailscale/`](tailscale/):

- `tailscale/ts-up.sh` — tự dò TUN, chọn chế độ, khởi động daemon nếu chưa chạy, rồi `up`.
- `tailscale/ts-down.sh` — `tailscale down` rồi dừng daemon, **xác nhận tiến trình đã chết**.

Copy cả thư mục vào `/mnt/sdcard/.userdata/mlp1/tailscale/` rồi:

```sh
sh /mnt/sdcard/.userdata/mlp1/tailscale/ts-up.sh
sh /mnt/sdcard/.userdata/mlp1/tailscale/ts-down.sh
```

### Tự khởi động cùng máy (cách chính thức của Leaf)

**Sửa lại điều tôi nói ở bản đầu tài liệu này:** tôi từng viết là không nên làm tự khởi động vì
phải chạm vào boot hook. Điều đó **sai**. Leaf có sẵn một **service supervisor** cho pak, và đó
chính là cách đúng — không đụng kernel, boot hook hay `.system`.

Cơ chế (đã đọc từ chính thiết bị): pak khai báo khối `"service"` trong `pak.json`, Leaf ghi trạng
thái vào `.umrk/mlp1/services-control.db` với hai cờ riêng biệt:

| Cờ trong DB | Nhãn trên giao diện | Nghĩa |
|---|---|---|
| `start_with_leaf` | **Enable** | tự lên mỗi lần máy khởi động |
| `session_run` | **Start** / **Stop** | bật/tắt ngay trong phiên này |

Supervisor còn có `restart: on-failure`, đếm `restart_count`, backoff và circuit breaker — nên nếu
`tailscaled` chết, Leaf tự bật lại.

`Tailscale.pak` trong dự án này **đã khai báo service** đúng chuẩn đó:

```json
"service": {
  "schema": 1, "id": "org.loongmate.tailscale",
  "run": { "path": "launch.sh", "args": ["service", "run"] },
  "restart": "on-failure", "default_enabled": false, "stop_grace_ms": 5000,
  "lifecycle": { "game": "ignore", "stop_on_storage_change": true, "stop_on_suspend": false }
}
```

Ý nghĩa của `lifecycle` ở đây:

- `game: "ignore"` — **vẫn chạy khi đang chơi game**, để không rớt kết nối. Đánh đổi: `tailscaled`
  chiếm khoảng 50–90 MB trong tổng 955 MB RAM. Nếu game nặng (PS1/DC/N64) bị giật, hãy đổi thành
  `"stop"` hoặc tắt Tailscale trước khi chơi.
- `stop_on_storage_change: true` — binary nằm trên thẻ SD, nên khi thẻ bị tháo thì dừng cho an toàn.
- `stop_on_suspend: false` — không dừng khi máy ngủ, nhờ vậy tỉnh lại là có mạng luôn.

**Cách bật:**

1. Trên máy: **System → Rescan Library** (hoặc khởi động lại) để Leaf nhận pak mới và đăng ký service.
2. Vào màn **Services** trong menu của Leaf, tìm **Tailscale**.
3. Chọn **Enable** (tự lên cùng máy) và **Start** (bật ngay).

Sau đó Tailscale sống liên tục: `tailscaled` tự thử lại khi Wi-Fi mất rồi có lại, nên bạn không
cần làm gì khi đổi mạng. Muốn khỏi phải bấm URL đăng nhập sau này, lưu một auth key tái sử dụng
vào `$SDCARD_PATH/.userdata/mlp1/tailscale/authkey.txt`.

> ⚠️ **Đừng để một `tailscaled` chạy tay song song với service.** Cả hai dùng cùng state dir, nên
> instance thứ hai báo "already running" rồi thoát mã khác 0; supervisor thấy fail liền restart,
> lặp lại tới mức `backoff` và `breaker_open`, cuối cùng **không instance nào sống**. Đây là lỗi
> thật đã gặp 2026-10-02. `Tailscale.pak` nay tự xử: ở chế độ service nó dừng mọi `tailscaled` cũ
> và dọn socket mồ côi trước khi khởi động, tức service luôn giành quyền sở hữu tiến trình.
>
> Kiểm nhanh xem tiến trình do ai sở hữu:
> ```sh
> TSPID=$(ps | grep "[t]ailscaled" | awk '{print $1}' | head -1)
> cat /proc/$(awk '/PPid/{print $2}' /proc/$TSPID/status)/comm   # phải là loong_pangu
> ps | grep -c "[t]ailscaled"                                    # phải là 1
> ```
>
> Nếu breaker đã mở: vào Services bấm **Stop** rồi **Start** lại; `breaker_open` và
> `backoff_failure_count` sẽ về 0.

> Nếu màn Services báo *"Unavailable services cannot be enabled"*, nghĩa là Leaf chưa xác thực được
> pak — kiểm lại `pak.json` có đúng khối `service` và `launch.sh` có quyền thực thi, rồi rescan lại.

Vẫn có thể bật/tắt tay bằng cách bấm **Apps → Tailscale** (lần bấm đầu bật, bấm lại tắt), hoặc qua
SSH bằng `ts-up.sh` / `ts-down.sh`.

## 7. Dùng Tailscale với SaveVault của LoongMate

Tailscale biến PC/NAS/Raspberry Pi thành một địa chỉ **ổn định** cho SaveVault, không cần port
forwarding và không phụ thuộc IP LAN đổi theo mạng Wi-Fi.

### Chuẩn bị đầu nhận (trên PC/NAS)

Bật SSH server trên PC/NAS và **đưa nó vào cùng tailnet**. Tạo thư mục nhận:

```bash
mkdir -p ~/pocket1-backup
```

Tạo SSH key **riêng cho LoongMate** (tài liệu nghiên cứu: "Dùng SSH key riêng cho LoongMate;
private key có quyền chỉ chủ sở hữu đọc") — tạo trên Pocket 1:

```sh
ssh-keygen -t ed25519 -f /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate -N ""
cat /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate.pub
```

Dán nội dung `.pub` vào `~/.ssh/authorized_keys` trên PC/NAS.

> ⚠️ Lại là FAT32: thẻ SD **không giữ được quyền `0600`** cho private key. `ssh` thường từ chối dùng
> key có quyền quá rộng (`UNPROTECTED PRIVATE KEY FILE`). Cách xử lý: đặt key trên phân vùng Linux
> ghi được, hoặc thêm `-o StrictModes=no` phía client **(giảm an toàn — chỉ dùng khi bạn hiểu rõ
> đánh đổi)**. Đây là lý do tài liệu nghiên cứu đặt `known-hosts` và key vào vùng dữ liệu app chứ
> không nằm rải rác.

### Chế độ A — dùng hostname tailnet trực tiếp

```sh
# Kiểm đường truyền trước khi tin
ssh -i /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate <user>@<tên-PC-trong-tailnet> "echo OK"
```

Đi qua được thì dùng `<tên-PC-trong-tailnet>` làm endpoint SFTP của SaveVault.

### Chế độ B — phải đi qua SOCKS5

`ssh`/`sftp` không tự dùng SOCKS5. Thêm `ProxyCommand` (cần `nc` có hỗ trợ `-X 5`):

```sh
ssh -o ProxyCommand='nc -X 5 -x localhost:1055 %h %p' \
    -i /mnt/sdcard/.userdata/mlp1/LoongMate/id_loongmate \
    <user>@<tên-PC-trong-tailnet> "echo OK"
```

Nếu `nc` trên Leaf không có `-X` **(CHƯA KIỂM CHỨNG)**, đảo chiều cho đơn giản hơn nhiều: để **PC
kéo** thay vì **máy đẩy**. Chiều vào vẫn hoạt động trong Chế độ B, nên từ PC:

```bash
# Chạy TRÊN PC: kéo snapshot SaveVault từ Pocket 1 về
rsync -av root@pocket1:/mnt/sdcard/.userdata/mlp1/LoongMate/SaveVault/ ~/pocket1-backup/
```

Đây là cách tôi khuyên dùng ở Chế độ B: ít thành phần hơn, không cần proxy, và LoongMate trên máy
chỉ cần làm snapshot **cục bộ** (`loongmate backup`) — việc nó đã làm tốt.

### Lưu ý về host key

Tài liệu nghiên cứu yêu cầu: lần kết nối đầu hiển thị fingerprint và xác nhận; các lần sau **từ
chối** nếu host key đổi. Khi dùng `ssh` tay, điều này tương đương việc **không** dùng
`StrictHostKeyChecking=no`. Hãy đọc kỹ fingerprint ở lần đầu thay vì bấm `yes` theo quán tính.

---

## 8. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Việc cần làm |
|---|---|---|
| `Exec format error` | sai kiến trúc | `uname -m` phải là `aarch64`; tải lại gói `arm64` |
| `Permission denied` khi chạy `./tailscaled` | thẻ mount `noexec` | chuyển 2 binary sang phân vùng Linux ghi được, `chmod +x` |
| `failed to connect to local tailscaled` | daemon chưa chạy, hoặc sai `--socket` | `ps | grep tailscaled`; dùng **cùng** đường dẫn `--socket` cho cả `tailscaled` và `tailscale` |
| Lỗi quyền khi ghi `tailscaled.state` | FAT32 không lưu `0600` | đổi `TS_STATE_DIR` sang `ext4`/`f2fs`, hoặc tạm dùng `/tmp` (mất sau reboot) |
| `tailscaled` chạy rồi tự chết | hết RAM, hoặc thiếu TUN mà không có cờ userspace | đọc `tailscaled.log`; thoát game trước khi bật; thêm `--tun=userspace-networking` |
| `tailscale up` treo ở URL đăng nhập | DNS hoặc giờ hệ thống sai | `date` (lệch giờ làm TLS lỗi); kiểm DNS bằng `nslookup login.tailscale.com` |
| `ping pocket1` không chạy nhưng `ssh` được | đang ở Chế độ B | bình thường — dùng `tailscale ping` |
| Kết nối chậm, `tailscale ping` báo `via DERP` | không đục được NAT | chấp nhận, hoặc mở UDP 41641 trên router; `tailscale netcheck` để xem chi tiết |
| Sau khi Leaf update thì mất Tailscale | đặt file ngoài `.userdata/` | cài lại theo mục 3, lần này đặt trong `.userdata/` |

Luôn đọc log thật trước khi đoán:

```sh
tail -n 50 /mnt/sdcard/.userdata/mlp1/tailscale/state/tailscaled.log
```

---

## 9. Gỡ sạch

```sh
TS_DIR=/mnt/sdcard/.userdata/mlp1/tailscale
TS_STATE_DIR="$TS_DIR/state"

# 1. Rời tailnet (xoá cả state đăng nhập)
"$TS_DIR/tailscale" --socket="$TS_STATE_DIR/tailscaled.sock" logout

# 2. Dừng daemon
kill "$(cat "$TS_STATE_DIR/tailscaled.pid" 2>/dev/null)" 2>/dev/null \
  || killall tailscaled 2>/dev/null

# 3. XÁC NHẬN đã chết (đừng tin bước 2 đã chạy xong)
ps | grep -c "[t]ailscaled"    # phải ra 0

# 4. Xoá file
rm -rf "$TS_DIR"

# 5. XÁC NHẬN đã sạch
ls -la /mnt/sdcard/.userdata/mlp1/ | grep -i tailscale   # không ra gì là xong
```

Cuối cùng, xoá thiết bị `pocket1` khỏi
<https://login.tailscale.com/admin/machines> để nó không chiếm slot trong tailnet.

Vì toàn bộ cài đặt chỉ là file trong `.userdata/` và một tiến trình user-space, gỡ xong là máy trở
về đúng trạng thái trước khi cài — không còn dấu vết trong kernel, boot hook hay `.system`.

---

## Nguồn

- [Tailscale — Static binaries / stable packages](https://pkgs.tailscale.com/stable/) — phiên bản và tên file `tailscale_<VER>_arm64.tgz`
- [Tailscale — Userspace networking mode](https://tailscale.com/kb/1112/userspace-networking/) — cờ `--tun=userspace-networking`, `--socks5-server`, `--outbound-http-proxy-listen`
- [Tailscale CLI reference](https://tailscale.com/docs/reference/tailscale-cli) — `up`, `status`, `ping`, `logout`
- [tailscale/tailscale #5233](https://github.com/tailscale/tailscale/issues/5233) — hành vi chuyển tiếp chiều vào tới `127.0.0.1` ở chế độ userspace
- [Leaf — Features tour](https://leaf.game/guide/features/) — SSH Server, Central Scrutinizer
- [Leaf — Adding games & ROMs](https://leaf.game/guide/games/) — yêu cầu FAT32 cho thẻ nhớ
- [Disco Boy](https://github.com/Utility-Muffin-Research-Kitchen/DiscoBoy) — quy ước `$USERDATA_PATH`, `$SDCARD_PATHS` và cấu trúc `Apps/mlp1/<Name>.pak`
