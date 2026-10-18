#!/bin/sh
# ts-down.sh — tắt Tailscale trên Miniloong Pocket 1 và XÁC NHẬN đã tắt thật.
#
# Dùng:
#   sh ts-down.sh              # rời tailnet, dừng daemon, giữ state đăng nhập
#   sh ts-down.sh --logout     # rời tailnet VÀ xoá state đăng nhập (lần sau phải đăng nhập lại)
#
# Biến môi trường: TS_DIR, TS_STATE_DIR (xem ts-up.sh).
# POSIX sh — không dùng [[ ]], mảng, local.

set -u

TS_DIR="${TS_DIR:-$(cd "$(dirname "$0")" && pwd)}"
TS_STATE_DIR="${TS_STATE_DIR:-$TS_DIR/state}"
TS_SOCK="$TS_STATE_DIR/tailscaled.sock"
TS_PID="$TS_STATE_DIR/tailscaled.pid"

DO_LOGOUT=0
[ "${1:-}" = "--logout" ] && DO_LOGOUT=1

# --- 1. Rời tailnet trước khi hạ daemon ------------------------------------
if [ -S "$TS_SOCK" ] && [ -x "$TS_DIR/tailscale" ]; then
    if [ "$DO_LOGOUT" -eq 1 ]; then
        echo "Đăng xuất khỏi tailnet (xoá state đăng nhập)..."
        "$TS_DIR/tailscale" --socket="$TS_SOCK" logout || \
            echo "CẢNH BÁO: logout không thành công, vẫn tiếp tục dừng daemon." >&2
    else
        echo "Ngắt kết nối tailnet (giữ state đăng nhập)..."
        "$TS_DIR/tailscale" --socket="$TS_SOCK" down || \
            echo "CẢNH BÁO: 'down' không thành công, vẫn tiếp tục dừng daemon." >&2
    fi
else
    echo "Không thấy socket $TS_SOCK — daemon có lẽ đã dừng."
fi

# --- 2. Dừng daemon --------------------------------------------------------
if [ -f "$TS_PID" ]; then
    PID=$(cat "$TS_PID" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Dừng tailscaled (pid $PID)..."
        kill "$PID" 2>/dev/null
        i=0
        while [ $i -lt 10 ]; do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
            i=$((i + 1))
        done
        if kill -0 "$PID" 2>/dev/null; then
            echo "Không chịu tắt sau 10 giây, buộc dừng (SIGKILL)." >&2
            kill -9 "$PID" 2>/dev/null
            sleep 1
        fi
    fi
    rm -f "$TS_PID"
fi

# Quét sót: tiến trình khởi động từ phiên SSH trước, không có pidfile.
LEFT=$(ps 2>/dev/null | grep "[t]ailscaled" | awk '{print $1}')
if [ -n "$LEFT" ]; then
    echo "Còn tiến trình tailscaled ngoài pidfile: $LEFT — dừng tiếp."
    for p in $LEFT; do kill "$p" 2>/dev/null; done
    sleep 2
    for p in $LEFT; do kill -9 "$p" 2>/dev/null; done
fi

# --- 3. XÁC NHẬN đã chết — không tin bước 2 đã chạy xong -------------------
sleep 1
REMAIN=$(ps 2>/dev/null | grep -c "[t]ailscaled")
if [ "$REMAIN" -gt 0 ]; then
    echo "" >&2
    echo "LỖI: vẫn còn $REMAIN tiến trình tailscaled đang chạy." >&2
    ps 2>/dev/null | grep "[t]ailscaled" >&2
    echo "      Cần làm: dừng tay bằng 'kill -9 <pid>' với pid ở trên." >&2
    exit 1
fi

rm -f "$TS_SOCK" 2>/dev/null

echo "Đã tắt Tailscale. Xác nhận: 0 tiến trình tailscaled còn chạy."
if [ "$DO_LOGOUT" -eq 1 ]; then
    echo "State đăng nhập đã xoá — lần sau phải đăng nhập lại (hoặc dùng TS_AUTHKEY)."
else
    echo "State đăng nhập còn giữ — lần sau 'sh ts-up.sh' là vào lại được ngay."
fi
