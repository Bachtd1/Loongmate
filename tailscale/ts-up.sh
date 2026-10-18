#!/bin/sh
# ts-up.sh — bật Tailscale ở user-space trên Miniloong Pocket 1 (Leaf CFW).
#
# Tự dò /dev/net/tun để chọn chế độ, khởi động tailscaled nếu chưa chạy, rồi
# đăng nhập tailnet. Không chạm kernel, boot hook hay phân vùng eMMC.
#
# Dùng:
#   sh ts-up.sh                      # lần đầu: in URL để đăng nhập
#   TS_AUTHKEY=tskey-auth-xxx sh ts-up.sh   # không cần bấm URL
#
# Biến môi trường ghi đè được:
#   TS_DIR        thư mục chứa binary (mặc định: thư mục của script này)
#   TS_STATE_DIR  thư mục state (mặc định: $TS_DIR/state)
#   TS_HOSTNAME   tên máy trong tailnet (mặc định: pocket1)
#   TS_AUTHKEY    auth key tái sử dụng; KHÔNG lưu vào file trên thẻ SD
#
# POSIX sh — Leaf/BusyBox có thể không có bash. Không dùng [[ ]], mảng, local.

set -u

TS_DIR="${TS_DIR:-$(cd "$(dirname "$0")" && pwd)}"
TS_STATE_DIR="${TS_STATE_DIR:-$TS_DIR/state}"
TS_HOSTNAME="${TS_HOSTNAME:-pocket1}"
TS_SOCK="$TS_STATE_DIR/tailscaled.sock"
TS_PID="$TS_STATE_DIR/tailscaled.pid"
TS_LOG="$TS_STATE_DIR/tailscaled.log"

die() {
    echo "LỖI: $1" >&2
    [ -n "${2:-}" ] && echo "      Cần làm: $2" >&2
    exit 1
}

# --- 1. Kiểm binary có mặt và chạy được ------------------------------------
[ -f "$TS_DIR/tailscaled" ] || die \
    "không thấy $TS_DIR/tailscaled" \
    "tải gói arm64 theo TAILSCALE.md mục 3 và đặt vào $TS_DIR"
[ -f "$TS_DIR/tailscale" ] || die \
    "không thấy $TS_DIR/tailscale" \
    "tải gói arm64 theo TAILSCALE.md mục 3 và đặt vào $TS_DIR"

chmod +x "$TS_DIR/tailscaled" "$TS_DIR/tailscale" 2>/dev/null

if ! "$TS_DIR/tailscaled" --version >/dev/null 2>&1; then
    die "không chạy được $TS_DIR/tailscaled" \
        "kiểm 'uname -m' phải là aarch64; nếu thẻ mount noexec thì chuyển binary sang phân vùng ext4 ghi được (TAILSCALE.md mục 3)"
fi

# --- 2. Thư mục state ------------------------------------------------------
if ! mkdir -p "$TS_STATE_DIR" 2>/dev/null; then
    die "không tạo được $TS_STATE_DIR" \
        "đặt TS_STATE_DIR sang thư mục ghi được, ví dụ: TS_STATE_DIR=/tmp/ts-state sh ts-up.sh"
fi

# --- 3. Daemon đang chạy rồi thì đừng chạy thêm ----------------------------
daemon_alive() {
    [ -f "$TS_PID" ] || return 1
    _pid=$(cat "$TS_PID" 2>/dev/null) || return 1
    [ -n "$_pid" ] || return 1
    kill -0 "$_pid" 2>/dev/null
}

if daemon_alive; then
    echo "tailscaled đã chạy (pid $(cat "$TS_PID")). Bỏ qua bước khởi động."
else
    # --- 4. Chọn chế độ theo thiết bị TUN có thật hay không ----------------
    if [ -c /dev/net/tun ]; then
        echo "Thấy /dev/net/tun → Chế độ A (TUN, trong suốt)."
        TUN_ARGS=""
    else
        echo "Không thấy /dev/net/tun → Chế độ B (userspace-networking)."
        echo "  Lưu ý: chiều VÀO máy vẫn chạy; chiều RA phải qua SOCKS5 localhost:1055."
        TUN_ARGS="--tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055"
    fi

    echo "Khởi động tailscaled, log: $TS_LOG"
    # shellcheck disable=SC2086  # TUN_ARGS phải tách thành nhiều tham số
    "$TS_DIR/tailscaled" $TUN_ARGS \
        --state="$TS_STATE_DIR/tailscaled.state" \
        --socket="$TS_SOCK" \
        --statedir="$TS_STATE_DIR" \
        >> "$TS_LOG" 2>&1 &
    echo $! > "$TS_PID"

    # --- 5. Chờ socket xuất hiện, tối đa 15 giây --------------------------
    i=0
    while [ $i -lt 30 ]; do
        [ -S "$TS_SOCK" ] && break
        if ! daemon_alive; then
            echo "--- 20 dòng cuối của log ---" >&2
            tail -n 20 "$TS_LOG" >&2 2>/dev/null
            die "tailscaled chết ngay sau khi khởi động" \
                "đọc log ở trên; thường do hết RAM (thoát game trước) hoặc thiếu quyền ghi state trên FAT32 (TAILSCALE.md mục 3)"
        fi
        sleep 1
        i=$((i + 1))
    done
    [ -S "$TS_SOCK" ] || die "quá 15 giây mà không thấy socket $TS_SOCK" \
        "đọc $TS_LOG để biết lý do"
fi

# --- 6. Đăng nhập / bật tailnet --------------------------------------------
# Kiểm TRẠNG THÁI THẬT trước và sau, không tin mã thoát của "tailscale up".
# Gặp thật 2026-10-02: nếu máy đã đăng nhập từ trước, daemon tự kết nối lại khi
# khởi động, nhưng "tailscale up" vẫn trả mã 1 kèm "changing settings via
# 'tailscale up' requires mentioning all non-default flags". Script cũ báo
# "Bật Tailscale thất bại" trong khi máy đã online — báo sai trạng thái còn tệ
# hơn không báo gì.
backend_state() {
    "$TS_DIR/tailscale" --socket="$TS_SOCK" status --json 2>/dev/null         | sed -n 's/.*"BackendState"[[:space:]]*:[[:space:]]*"\([^"]*\)".*//p'         | head -1
}

STATE_NOW=$(backend_state)
if [ "$STATE_NOW" = "Running" ]; then
    echo "Đã đăng nhập sẵn và đang chạy — không cần đăng nhập lại."
else
    echo "Đăng nhập tailnet với hostname '$TS_HOSTNAME' (trạng thái: ${STATE_NOW:-chưa rõ})..."
    # Giữ NGUYÊN một bộ cờ cố định ở mọi lần chạy, nếu không "tailscale up" sẽ
    # than phiền vì bộ cờ khác với prefs đã lưu.
    if [ -n "${TS_AUTHKEY:-}" ]; then
        "$TS_DIR/tailscale" --socket="$TS_SOCK" up             --hostname="$TS_HOSTNAME" --accept-dns=false --authkey="$TS_AUTHKEY" || true
    else
        "$TS_DIR/tailscale" --socket="$TS_SOCK" up             --hostname="$TS_HOSTNAME" --accept-dns=false || true
    fi

    i=0
    while [ $i -lt 15 ]; do
        STATE_NOW=$(backend_state)
        [ "$STATE_NOW" = "Running" ] && break
        sleep 1
        i=$((i + 1))
    done
    if [ "$STATE_NOW" != "Running" ]; then
        die "Tailscale chưa lên (trạng thái: ${STATE_NOW:-không đọc được})"             "nếu là NeedsLogin thì mở URL đăng nhập in ở trên; nếu lỗi TLS thì kiểm giờ bằng 'date'"
    fi
fi

# --- 7. Báo trạng thái thật, không đoán ------------------------------------
echo ""
echo "--- tailscale status ---"
"$TS_DIR/tailscale" --socket="$TS_SOCK" status
echo ""
echo "IP trong tailnet: $("$TS_DIR/tailscale" --socket="$TS_SOCK" ip -4 2>/dev/null || echo 'chưa có')"
echo "Tắt bằng: sh $TS_DIR/ts-down.sh"
