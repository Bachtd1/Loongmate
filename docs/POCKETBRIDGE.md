# Thư viện game riêng qua PocketBridge (+ Tailscale)

Duyệt, tải và cài game từ **thư mục ROM trên máy của chính bạn** (PC Windows, Mac, Linux, Raspberry Pi, NAS) ngay trên
Pocket 1 — trong nhà qua Wi-Fi, ở xa qua **Tailscale**. LoongMate là phía Pocket 1 (màn **Thư viện riêng**); phía máy chứa
ROM là **PocketBridge Agent**.

Tải PocketBridge: **https://github.com/Bachtd1/pocketbridge/releases** (chọn bản mới nhất).

| Gói | Dùng cho |
|---|---|
| `PocketBridge-Agent-Windows.zip` | PC Windows 10/11 (x64) |
| `PocketBridge-Agent-macOS.zip` | Mac Apple Silicon |
| `PocketBridge-Agent-Linux.zip` | PC Linux (x64) và Raspberry Pi / máy ARM64 |
| `PocketBridge-Pocket1.zip` | app PocketBridge trên Pocket 1 (bảng trạng thái máy chủ, tuỳ chọn — Thư viện riêng đã có sẵn trong LoongMate) |

## 1. Cài Agent trên máy chứa ROM

Giải nén gói đúng hệ điều hành rồi chạy (thay `<IP>` bằng IP Tailscale `100.x.y.z` hoặc IP LAN của máy):

- **Windows** — PowerShell **chạy bằng Administrator**, trong thư mục vừa giải nén:
  `.\install.ps1 -Listen <IP> -Id pocket1 -Scopes library:read`
- **Linux / Raspberry Pi**: `sudo sh linux/install.sh --listen <IP> --id pocket1 --scopes library:read`
- **macOS**: `sudo sh macos/install.sh --listen <IP> --id pocket1 --scopes library:read`

Bộ cài kiểm `SHA256SUMS` trước khi cài, tạo dịch vụ chạy nền và in ra **file ghép đôi** (pairing bundle).

| | Windows | Linux / Pi | macOS |
|---|---|---|---|
| Cấu hình | `C:\ProgramData\PocketBridge\config.yaml` | `/etc/pocketbridge/config.yaml` | `/Library/Application Support/PocketBridge/config.yaml` |
| File ghép đôi | `C:\ProgramData\PocketBridge\pairing\pairing-bundle-pocket1.json` | in ra màn hình, hoặc thêm `--pair-out pocket1-bundle.json` | như Linux |

## 2. Khai báo thư mục ROM

Mở `config.yaml` (bằng quyền Administrator / root), thêm mục `library` rồi khởi động lại agent:

```yaml
library:
  roots:
    - id: roms
      name: ROM của tôi
      path: D:\Roms          # Linux/Pi: /srv/roms   macOS: /Users/<bạn>/Roms
```

Đặt tên **thư mục cấp 1 theo hệ máy của Leaf** (`GB`, `GBC`, `GBA`, `SNES`, `PSX`, `GENESIS`…) để LoongMate cài đúng hệ.
Game nhiều file (`.cue` + `.bin`) để chung một thư mục con.

## 3. Ghép đôi Pocket 1

1. Chép file ghép đôi vào thẻ Leaf: **`<thẻ>/PocketBridge-import/`** (hoặc `Import/`). Mã ghép đôi dùng **một lần**, hết hạn sau 15 phút
   — hết hạn thì tạo lại bằng lệnh `pair` của agent.
2. Trên Pocket 1: **LoongMate → Thư viện → Thư viện riêng → Ghép đôi**, chọn file, xác nhận. LoongMate ghim chứng chỉ của agent,
   đổi mã lấy khoá truy cập riêng, rồi **xoá file ghép đôi**.

## 4. Duyệt và cài game

**Thư viện riêng → (máy của bạn) → thư mục → hệ máy → game**: X chọn nhiều, A "Tải & cài". LoongMate kiểm SHA-256, chọn thẻ
đích (mặc định thẻ phụ), cài qua trình **Nhập game** như game tự chép. Mất Wi-Fi giữa chừng: mở lại **"Tải dở"** để tải tiếp.

## Ở xa nhà: Tailscale

Cài Tailscale trên máy chứa ROM và trên Pocket 1, đăng nhập **cùng một tailnet**, rồi dùng IP Tailscale (`100.x.y.z`) cho
`-Listen` / `--listen`. Agent chỉ nghe đúng IP đó, không mở ra Internet. Hướng dẫn cài Tailscale trên Pocket 1 sẽ có trong bản sau.

## An toàn và pháp lý

- Agent chỉ **cho đọc** đúng các thư mục bạn khai báo; không tải gì từ Internet; mọi kết nối dùng TLS đã ghim chứng chỉ + khoá
  truy cập riêng cho từng máy.
- Chỉ đặt vào thư viện nội dung bạn **có quyền sử dụng** (bản dump của chính bạn, homebrew). LoongMate và PocketBridge không cung
  cấp ROM.
- PocketBridge là phần mềm miễn phí, cung cấp "nguyên trạng", chưa có chữ ký số cho binary (SHA256SUMS chỉ chống tải lỗi).
