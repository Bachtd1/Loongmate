#!/bin/sh
# LoongMateAgent.pak - tien trinh nen cua LoongMate cho Miniloong Pocket 1 (Leaf).
#
# Hai che do (cung mau voi Tailscale.pak):
#   launch.sh                 bam tu tab Apps: in trang thai agent ra log roi thoat
#   launch.sh service run     Leaf service supervisor goi: chay engine.agent o
#                             FOREGROUND (exec) de supervisor theo doi duoc
#
# Agent ghi lich su pin/nhiet moi 5 phut va tu sao luu khi thoat game. Khong
# cham kernel, boot hook hay phan vung he thong.
# Engine DUNG CHUNG voi ../LoongMate.pak/engine (khong copy trung): can cai
# LoongMate.pak (da build kem engine/agent.py) truoc.
# POSIX sh (BusyBox): khong [[ ]], khong mang.

set -u

PAK_DIR="$(cd "$(dirname "$0")" && pwd)"

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

# Cung thu muc du lieu voi LoongMate.pak de dung chung SaveVault.
APP_DATA="$USERDATA_ROOT/LoongMate"
mkdir -p "$APP_DATA/logs" 2>/dev/null
LOG="$APP_DATA/logs/agent_launch.log"
say() { echo "$1"; echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG" 2>/dev/null; }

ENGINE_ROOT="$PAK_DIR/../LoongMate.pak"
if [ ! -f "$ENGINE_ROOT/engine/agent.py" ]; then
    say "LOI: khong thay $ENGINE_ROOT/engine/agent.py. Can cai LoongMate.pak ban moi (sh pak/build.sh) truoc."
    exit 1
fi

# Giải nén runtime vào thư mục tạm rồi mới đổi tên: mất điện giữa chừng không để lại python3 "có mà thiếu thư viện"
# (lần mở sau sẽ thấy python3 rồi chết, không bao giờ giải nén lại).
extract_runtime() {
    rt_tmp="/userdata/.loongmate-runtime.tmp"
    rm -rf "$rt_tmp" && mkdir -p "$rt_tmp" &&
        tar -xzf "$1" -C "$rt_tmp" &&
        [ -x "$rt_tmp/loongmate-runtime/python/bin/python3" ] &&
        rm -rf /userdata/loongmate-runtime &&
        mv "$rt_tmp/loongmate-runtime" /userdata/loongmate-runtime
    rt_rc=$?
    rm -rf "$rt_tmp"
    return $rt_rc
}

PYTHON_BIN=""
for cand in "${LOONGMATE_PYTHON:-}" "$APP_DATA/runtime/python/bin/python3" "/userdata/loongmate-runtime/python/bin/python3" "$ENGINE_ROOT/runtime/python/bin/python3"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
        PYTHON_BIN="$cand"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    for tar_cand in "$ENGINE_ROOT/loongmate-runtime.tar.gz" "$ENGINE_ROOT/runtime.tar.gz" "$PAK_DIR/loongmate-runtime.tar.gz"; do
        if [ -f "$tar_cand" ]; then
            say "Dang giai nen Python runtime vao /userdata..."
            extract_runtime "$tar_cand" >> "$LOG" 2>&1 || say "LOI: giai nen $tar_cand that bai (xem $LOG)."
            break
        fi
    done
    for cand in "${LOONGMATE_PYTHON:-}" "$APP_DATA/runtime/python/bin/python3" "/userdata/loongmate-runtime/python/bin/python3" "$ENGINE_ROOT/runtime/python/bin/python3"; do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            PYTHON_BIN="$cand"
            break
        fi
    done
fi
if [ -z "$PYTHON_BIN" ]; then
    say "LOI: khong tim thay Python. Cai runtime vao /userdata/loongmate-runtime/python (xem INSTALL.md)."
    exit 1
fi

PYTHONPATH="$ENGINE_ROOT"
PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH PYTHONDONTWRITEBYTECODE

# ---------------------------------------------------------------------------
# Che do SERVICE: exec de agent la tien trinh chinh cua service.
# --wait-lock: neu ban cu van dang tat thi doi toi da 30 giay; neu van bi giu
# (co ban chay tay) thi thoat 0, KHONG thoat khac 0 - tranh restart lap ->
# backoff -> breaker mo (loi da gap voi tailscaled 2026-10-02).
# ---------------------------------------------------------------------------
if [ "${1:-}" = "service" ]; then
    say "Service khoi dong."
    exec "$PYTHON_BIN" -m engine.agent --run --wait-lock 30 --userdata "$APP_DATA"
fi

# ---------------------------------------------------------------------------
# Che do BAM TU TAB APPS: in trang thai.
# ---------------------------------------------------------------------------
say "Trang thai LoongMate Agent:"
"$PYTHON_BIN" -m engine.agent --userdata "$APP_DATA" 2>&1 | while IFS= read -r line; do say "  $line"; done
exit 0
