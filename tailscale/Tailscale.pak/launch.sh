#!/bin/sh
# Tailscale.pak — Tailscale ở user-space cho Miniloong Pocket 1 (Leaf).
#
# Hai chế độ:
#   launch.sh                 bấm từ tab Apps: bật nếu đang tắt, tắt nếu đang chạy
#   launch.sh service run     Leaf service supervisor gọi: chạy tailscaled ở
#                             FOREGROUND để supervisor theo dõi được tiến trình
#
# Chạy ở chế độ service là cách để Tailscale tự lên cùng máy và tự kết nối lại
# khi Wi-Fi trở lại: tailscaled sống liên tục và tự retry, còn Leaf lo restart
# nếu nó chết (pak.json khai "restart": "on-failure").
#
# Không chạm kernel, boot hook hay phân vùng hệ thống.
# POSIX sh (BusyBox): không [[ ]], không mảng, không local ngoài hàm.

set -u

PAK_DIR="$(cd "$(dirname "$0")" && pwd)"

# Nạp hợp đồng đường dẫn của Leaf nếu tìm được, thay vì tự đoán.
find_card_root() {
    probe="$1"
    while [ "$probe" != "/" ] && [ -n "$probe" ]; do
        if [ -d "$probe/.system/leaf/platforms" ]; then
            echo "$probe"
            return 0
        fi
        probe=$(dirname "$probe")
    done
    return 1
}
CARD_ROOT=$(find_card_root "$PAK_DIR") || CARD_ROOT=""
if [ -n "$CARD_ROOT" ]; then
    ENV_FILE="$CARD_ROOT/.system/leaf/platforms/${PLATFORM:-mlp1}/launcher/env.sh"
    # shellcheck disable=SC1090
    [ -f "$ENV_FILE" ] && . "$ENV_FILE" 2>/dev/null
fi
: "${SDCARD_PATH:=${CARD_ROOT:-/mnt/sdcard}}"
USERDATA_ROOT="${USERDATA_PATH:-$SDCARD_PATH/.userdata/${PLATFORM:-mlp1}}"

TS_DIR="$USERDATA_ROOT/tailscale"

# State phải nằm trên filesystem Linux thật, KHÔNG phải thẻ FAT32: tailscaled
# muốn file state quyền 0600 mà FAT32 không lưu được quyền. KIỂM CHỨNG TRÊN MÁY
# THẬT 2026-10-02: /userdata là ext4, ghi được, còn ~3.7 GB, và nằm trên eMMC nên
# không mất khi cập nhật Leaf. Phải dùng ĐÚNG MỘT đường dẫn ở mọi chế độ, nếu
# không mỗi lần khởi động sẽ tạo state mới và bắt đăng nhập lại tailnet.
if [ -d /userdata ] && [ -w /userdata ]; then
    TS_STATE_DIR="/userdata/tailscale-state"
else
    TS_STATE_DIR="$TS_DIR/state"
fi
TS_SOCK="$TS_STATE_DIR/tailscaled.sock"
LOG="$TS_STATE_DIR/launch.log"

mkdir -p "$TS_STATE_DIR" 2>/dev/null
say() { echo "$1"; echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG" 2>/dev/null; }

if [ ! -x "$TS_DIR/tailscaled" ]; then
    say "LỖI: không thấy $TS_DIR/tailscaled (hoặc không chạy được)."
    say "Cần làm: chép tailscaled/tailscale bản arm64 vào $TS_DIR rồi chmod +x."
    exit 1
fi

# Không có /dev/net/tun thì buộc phải chạy userspace-networking. Trên Pocket 1
# đã KIỂM CHỨNG là KHÔNG có TUN, nên thực tế luôn đi nhánh userspace. Chiều VÀO
# máy vẫn hoạt động (netstack chuyển tiếp TCP tới 127.0.0.1 cùng cổng, đã kiểm
# bằng SSH qua tailnet); chiều RA cần proxy SOCKS5 localhost:1055.
tun_args() {
    if [ -c /dev/net/tun ]; then
        echo ""
    else
        echo "--tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055"
    fi
}

backend_state() {
    "$TS_DIR/tailscale" --socket="$TS_SOCK" status --json 2>/dev/null \
        | sed -n 's/.*"BackendState"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -1
}

# Đăng nhập nếu cần. Chạy được cả khi gọi từ nền.
ensure_logged_in() {
    i=0
    while [ $i -lt 30 ]; do
        [ -S "$TS_SOCK" ] && break
        sleep 1
        i=$((i + 1))
    done
    [ -S "$TS_SOCK" ] || { say "Quá 30 giây không thấy socket tailscaled."; return 1; }

    st=$(backend_state)
    if [ "$st" = "Running" ]; then
        say "Đã đăng nhập sẵn và đang chạy (IP: $("$TS_DIR/tailscale" --socket="$TS_SOCK" ip -4 2>/dev/null))."
        return 0
    fi

    if [ -f "$TS_DIR/authkey.txt" ]; then
        TS_AUTHKEY=$(cat "$TS_DIR/authkey.txt" 2>/dev/null)
        "$TS_DIR/tailscale" --socket="$TS_SOCK" up \
            --hostname="${TS_HOSTNAME:-pocket1}" --accept-dns=false \
            --authkey="$TS_AUTHKEY" >> "$LOG" 2>&1 || true
    else
        # Giữ NGUYÊN bộ cờ cố định ở mọi lần chạy: "tailscale up" sẽ báo lỗi nếu
        # bộ cờ khác với prefs đã lưu, dù máy vẫn đang kết nối bình thường.
        "$TS_DIR/tailscale" --socket="$TS_SOCK" up \
            --hostname="${TS_HOSTNAME:-pocket1}" --accept-dns=false >> "$LOG" 2>&1 || true
    fi

    # Xác minh bằng trạng thái thật, không bằng mã thoát của lệnh trên.
    i=0
    while [ $i -lt 20 ]; do
        st=$(backend_state)
        [ "$st" = "Running" ] && break
        sleep 1
        i=$((i + 1))
    done
    if [ "$st" = "Running" ]; then
        say "Tailscale đã lên. IP tailnet: $("$TS_DIR/tailscale" --socket="$TS_SOCK" ip -4 2>/dev/null)"
        return 0
    fi
    say "Tailscale chưa lên (trạng thái: ${st:-không đọc được})."
    if [ "$st" = "NeedsLogin" ]; then
        say "Cần làm: mở URL đăng nhập được in trong $LOG, hoặc lưu auth key vào $TS_DIR/authkey.txt."
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Chế độ SERVICE: tailscaled chạy foreground cho supervisor của Leaf theo dõi.
# ---------------------------------------------------------------------------
if [ "${1:-}" = "service" ]; then
    say "Service khởi động (chế độ$( [ -c /dev/net/tun ] && echo ' TUN' || echo ' userspace'))."

    # Service PHẢI giành quyền sở hữu tiến trình, không được đánh nhau với một
    # tailscaled khởi động bằng tay. GẶP THẬT 2026-10-02: một tailscaled chạy
    # tay đang giữ tailscaled.sock và state dir; service exec thêm một cái nữa
    # trên CÙNG state → "already running" → thoát mã khác 0 → restart on-failure
    # lặp lại → backoff → breaker_open, và cuối cùng chẳng cái nào sống.
    # Vì vậy: dừng mọi tailscaled cũ, dọn socket mồ côi, rồi mới exec.
    if ps 2>/dev/null | grep -q "[t]ailscaled"; then
        say "Phát hiện tailscaled đang chạy ngoài service — dừng nó để service sở hữu tiến trình."
        killall tailscaled 2>/dev/null
        i=0
        while [ $i -lt 10 ]; do
            ps 2>/dev/null | grep -q "[t]ailscaled" || break
            sleep 1
            i=$((i + 1))
        done
        if ps 2>/dev/null | grep -q "[t]ailscaled"; then
            say "Không tắt được bằng TERM, buộc dừng."
            killall -9 tailscaled 2>/dev/null
            sleep 1
        fi
    fi
    # Socket mồ côi còn sót sẽ làm tailscaled từ chối khởi động.
    [ -S "$TS_SOCK" ] && ! ps 2>/dev/null | grep -q "[t]ailscaled" && rm -f "$TS_SOCK"

    # Đăng nhập ở tiến trình phụ, vì tiến trình chính phải là tailscaled.
    ( ensure_logged_in ) &
    # shellcheck disable=SC2086
    exec "$TS_DIR/tailscaled" $(tun_args) \
        --state="$TS_STATE_DIR/tailscaled.state" \
        --socket="$TS_SOCK" \
        --statedir="$TS_STATE_DIR" >> "$TS_STATE_DIR/tailscaled.log" 2>&1
fi

# ---------------------------------------------------------------------------
# Chế độ BẤM TỪ TAB APPS: bật/tắt.
# ---------------------------------------------------------------------------
if ps 2>/dev/null | grep -q "[t]ailscaled"; then
    say "Tailscale đang chạy — tiến hành tắt."
    if [ -x "$TS_DIR/ts-down.sh" ]; then
        TS_DIR="$TS_DIR" TS_STATE_DIR="$TS_STATE_DIR" sh "$TS_DIR/ts-down.sh" >> "$LOG" 2>&1
    else
        killall tailscaled 2>/dev/null
    fi
    say "Đã tắt Tailscale."
    say "Lưu ý: nếu service 'start with Leaf' đang bật, Leaf sẽ bật lại ở lần khởi động sau."
    exit 0
fi

say "Đang bật Tailscale..."
# shellcheck disable=SC2086
"$TS_DIR/tailscaled" $(tun_args) \
    --state="$TS_STATE_DIR/tailscaled.state" \
    --socket="$TS_SOCK" \
    --statedir="$TS_STATE_DIR" >> "$TS_STATE_DIR/tailscaled.log" 2>&1 &
echo $! > "$TS_STATE_DIR/tailscaled.pid"

if ensure_logged_in; then
    exit 0
fi
exit 1
