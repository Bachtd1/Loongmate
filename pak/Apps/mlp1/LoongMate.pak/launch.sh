#!/bin/sh
# LoongMate launcher script for Miniloong Pocket 1 (Leaf CFW)
# Platform: mlp1 (Rockchip RK3566, 960x720)
# POSIX sh only (no bash-isms): Leaf/BusyBox may not provide bash.

PAK_DIR="$(cd "$(dirname "$0")" && pwd)"

# Leaf công bố hợp đồng đường dẫn trong env.sh, và chính file đó ghi: "Source
# this file from launchers, paks, and platform scripts. Existing environment
# values win." Nạp nó thay vì tự đoán đường dẫn — nó đặt đúng SDCARD_PATH,
# USERDATA_PATH, SDCARD_PATHS, SAVES_PATHS, UMRK_SECONDARY_SDCARD_PATH cho
# từng nền tảng. KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02: trên mlp1 thẻ ở
# /mnt/sdcard (viết thường), userdata ở $SDCARD_PATH/.userdata/mlp1, thẻ khe 2
# ở /media/sdcard1. Mọi giá trị này trước đây tôi đoán và đều đoán sai.
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
    if [ -f "$ENV_FILE" ]; then
        # shellcheck disable=SC1090
        . "$ENV_FILE" || echo "Cảnh báo: nạp $ENV_FILE thất bại, dùng giá trị mặc định."
    fi
fi

if [ -z "$SDCARD_PATH" ]; then
    export SDCARD_PATH="${CARD_ROOT:-/mnt/sdcard}"
fi

USERDATA_ROOT="${USERDATA_PATH:-$SDCARD_PATH/.userdata/${PLATFORM:-mlp1}}"
APP_DATA="$USERDATA_ROOT/LoongMate"

mkdir -p "$APP_DATA/logs"
mkdir -p "$APP_DATA/manifests"
mkdir -p "$APP_DATA/jobs"
mkdir -p "$APP_DATA/Quarantine"
mkdir -p "$APP_DATA/SaveVault/snapshots"

LOG_FILE="$APP_DATA/logs/loongmate_$(date +%Y%m%d).log"

log_and_show() {
    # Writes a one-line message to the log and echoes it, so it reaches both
    # the support bundle (health.py reads this directory) and whatever
    # terminal/console is attached to this launch. No tee needed: tee here
    # used to write the log line twice and send the terminal copy to
    # /dev/null, so the message reached neither place correctly.
    echo "$1"
    echo "$1" >> "$LOG_FILE"
}

run_logged() {
    # Runs "$@", appending combined stdout/stderr to $LOG_FILE and, when tee
    # and mkfifo are available, also streaming it live to our own stdout.
    # POSIX sh has no pipefail/PIPESTATUS, so piping straight into tee would
    # make EXIT_CODE capture tee's exit status instead of the app's (the bug
    # this script used to have with a bare "cmd | ...; EXIT_CODE=$?"). Using
    # a named pipe lets the app run un-piped so its real exit code is kept.
    if command -v tee >/dev/null 2>&1 && command -v mkfifo >/dev/null 2>&1; then
        pipe_file="$APP_DATA/logs/.loongmate_pipe_$$"
        rm -f "$pipe_file"
        if mkfifo "$pipe_file" 2>/dev/null; then
            tee -a "$LOG_FILE" < "$pipe_file" &
            tee_pid=$!
            "$@" > "$pipe_file" 2>&1
            run_exit_code=$?
            wait "$tee_pid" 2>/dev/null
            rm -f "$pipe_file"
            return $run_exit_code
        fi
    fi
    "$@" >> "$LOG_FILE" 2>&1
    return $?
}

log_and_show "[$(date '+%Y-%m-%d %H:%M:%S')] Starting LoongMate on Leaf mlp1..."

# Engine is bundled inside the pak itself (see pak/build.sh: it copies
# engine/*.py into $PAK_DIR/engine/). Path is always relative to PAK_DIR, so
# it never escapes the SD card root the way "../../../../engine/cli.py" did.
# KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02: Leaf/LoongOS trên Pocket 1 KHÔNG có
# python3 lẫn python trong PATH (rootfs buildroot tối giản). Vì vậy ưu tiên tìm
# runtime CPython di động đặt kèm, trước khi thử PATH.
# Ghi đè được bằng biến LOONGMATE_PYTHON nếu bạn đặt runtime ở chỗ khác.
PYTHON_BIN=""
for cand in     "$LOONGMATE_PYTHON"     "$APP_DATA/runtime/python/bin/python3"     "/userdata/loongmate-runtime/python/bin/python3"     "$PAK_DIR/runtime/python/bin/python3"
do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
        PYTHON_BIN="$cand"
        break
    fi
done

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

# Nếu chưa tìm thấy Python, kiểm tra gói runtime nén đi kèm (.tar.gz) để tự động giải nén vào /userdata (ext4)
if [ -z "$PYTHON_BIN" ]; then
    for tar_cand in "$PAK_DIR/loongmate-runtime.tar.gz" \
                    "$PAK_DIR/runtime.tar.gz" \
                    "$CARD_ROOT/loongmate-runtime.tar.gz" \
                    "$CARD_ROOT/runtime.tar.gz" \
                    "$APP_DATA/loongmate-runtime.tar.gz"
    do
        if [ -f "$tar_cand" ]; then
            log_and_show "Tìm thấy gói runtime: $tar_cand — đang tự động giải nén vào /userdata..."
            if extract_runtime "$tar_cand" >> "$LOG_FILE" 2>&1; then
                log_and_show "Giải nén runtime vào /userdata thành công."
            else
                log_and_show "CẢNH BÁO: giải nén $tar_cand thất bại (xem log)."
            fi
            break
        fi
    done

    # Quét lại sau khi giải nén
    for cand in "$LOONGMATE_PYTHON" \
                "$APP_DATA/runtime/python/bin/python3" \
                "/userdata/loongmate-runtime/python/bin/python3" \
                "$PAK_DIR/runtime/python/bin/python3"
    do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            PYTHON_BIN="$cand"
            break
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    fi
fi

# MỘT app duy nhất: giao diện native (C, bin/lm-home) là mặc định; giao diện Python (engine/ui_sdl.py) là dự phòng.
# Rơi về Python khi native KHÔNG KHỞI ĐỘNG ĐƯỢC: thiếu binary, không chạy được (mã 126/127: sai kiến trúc, thiếu
# thư viện động), hoặc thoát mã khác 0 / chết do signal trong vài giây đầu (SDL/font/thư viện lỗi lúc khởi động).
# Native thoát mã 0 (người dùng bấm thoát) thì KHÔNG mở Python. Native chết muộn hơn vài giây (đang dùng) thì chỉ ghi log:
# bật Python bất ngờ giữa phiên còn tệ hơn. LOONGMATE_UI=python ép dùng Python. Native tự tìm Python như đoạn trên (cùng thứ tự).
NATIVE_BIN="$PAK_DIR/bin/lm-home"
FAST_FAIL_S=3
USE_PYTHON_UI=1
if [ "$LOONGMATE_UI" = "python" ]; then
    log_and_show "LOONGMATE_UI=python: bỏ qua giao diện native, dùng giao diện Python."
elif [ ! -x "$NATIVE_BIN" ]; then
    log_and_show "Không có giao diện native ($NATIVE_BIN thiếu hoặc không chạy được): dùng giao diện Python."
else
    log_and_show "Mở giao diện native (bin/lm-home)..."
    NATIVE_T0=$(date +%s)
    "$NATIVE_BIN" --pak "$PAK_DIR" --userdata "$APP_DATA" "$@" >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    NATIVE_T1=$(date +%s)
    NATIVE_SECS=$((NATIVE_T1 - NATIVE_T0))
    if [ "$EXIT_CODE" -eq 0 ]; then
        USE_PYTHON_UI=0
    elif [ "$EXIT_CODE" -eq 126 ] || [ "$EXIT_CODE" -eq 127 ]; then
        log_and_show "Giao diện native không chạy được (mã $EXIT_CODE: sai kiến trúc hoặc thiếu thư viện) — chuyển sang giao diện Python."
    elif [ "$NATIVE_SECS" -le "$FAST_FAIL_S" ]; then
        if [ "$EXIT_CODE" -gt 128 ]; then
            log_and_show "Giao diện native chết ngay khi khởi động (mã $EXIT_CODE = signal $((EXIT_CODE - 128)), sau ${NATIVE_SECS}s) — chuyển sang giao diện Python."
        else
            log_and_show "Giao diện native thoát mã $EXIT_CODE ngay khi khởi động (${NATIVE_SECS}s; thường do không mở được SDL/font/thư viện) — chuyển sang giao diện Python."
        fi
    else
        log_and_show "Giao diện native thoát mã $EXIT_CODE sau ${NATIVE_SECS}s (đã chạy một lúc nên KHÔNG tự mở Python) — xem $LOG_FILE."
        USE_PYTHON_UI=0
    fi
fi

if [ "$USE_PYTHON_UI" -eq 0 ]; then
    : # native đã chạy xong, EXIT_CODE giữ mã của nó
elif [ -n "$PYTHON_BIN" ]; then
    # KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02: pak không được Leaf cấp terminal khi
    # chạy từ tab Apps, nên TUI (chỉ in ANSI ra stdout) không hiện gì — màn
    # hình đen rồi bật ra. engine/ui_sdl.py vẽ thẳng lên màn hình qua SDL2 nên
    # KHÔNG cần terminal; ưu tiên thử nó trước. Nếu nó thoát khác 0 (thiếu
    # SDL2/font/engine lỗi...), ghi rõ lý do vào log rồi mới rơi về nhánh cũ:
    # có terminal (SSH) thì mở TUI đầy đủ; không có terminal thì làm một việc
    # hữu ích (sao lưu nhanh) và ghi kết quả vào file dễ đọc.
    log_and_show "Thử mở giao diện SDL (engine/ui_sdl.py)..."
    run_logged "$PYTHON_BIN" "$PAK_DIR/engine/ui_sdl.py" --userdata "$APP_DATA"
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -ne 0 ]; then
        log_and_show "Giao diện SDL thoát với mã $EXIT_CODE — xem $LOG_FILE để biết lý do. Rơi về phương án dự phòng."
        if [ -t 0 ] && [ -t 1 ]; then
            run_logged "$PYTHON_BIN" "$PAK_DIR/engine/tui.py" --userdata "$APP_DATA"
            EXIT_CODE=$?
        else
            log_and_show "Không có terminal (khởi chạy từ tab Apps) — chạy sao lưu nhanh SaveVault."
            run_logged "$PYTHON_BIN" "$PAK_DIR/engine/cli.py" --userdata "$APP_DATA" --quiet backup
            EXIT_CODE=$?
            # Pak không hiện được gì lên màn hình, nên ghi kết quả ra một file ngắn
            # dễ đọc. Mở được ngay trên máy bằng app Thing-File, không cần SSH.
            RESULT_FILE="$APP_DATA/LAN-CHAY-GAN-NHAT.txt"
            if [ "$EXIT_CODE" -eq 0 ]; then
                SNAP_COUNT=$(ls "$APP_DATA/SaveVault/snapshots" 2>/dev/null | wc -l)
                {
                    echo "LoongMate — lần chạy gần nhất"
                    echo "Thời điểm : $(date '+%Y-%m-%d %H:%M:%S')"
                    echo "Kết quả   : SAO LƯU THÀNH CÔNG (giao diện SDL không mở được)"
                    echo "Số snapshot hiện có: $SNAP_COUNT"
                    echo ""
                    echo "Giao diện SDL thất bại, xem lý do trong:"
                    echo "$LOG_FILE"
                } > "$RESULT_FILE" 2>/dev/null
                log_and_show "Sao lưu xong ($SNAP_COUNT snapshot). Kết quả ghi ở $RESULT_FILE"
            else
                {
                    echo "LoongMate — lần chạy gần nhất"
                    echo "Thời điểm : $(date '+%Y-%m-%d %H:%M:%S')"
                    echo "Kết quả   : SAO LƯU THẤT BẠI (mã $EXIT_CODE)"
                    echo ""
                    echo "Lý do chi tiết nằm trong:"
                    echo "$LOG_FILE"
                } > "$RESULT_FILE" 2>/dev/null
                log_and_show "Sao lưu THẤT BẠI (mã $EXIT_CODE). Đọc $LOG_FILE để biết lý do."
            fi
        fi
    fi
else
    log_and_show "[LoongMate] Không tìm thấy Python nào để chạy. Leaf trên Pocket 1 không có python3 sẵn, nên LoongMate cần một runtime CPython di động. Hành động cần làm: cài runtime vào /userdata/loongmate-runtime/python (xem INSTALL.md mục 2), hoặc đặt biến LOONGMATE_PYTHON trỏ tới python3 có sẵn, hoặc dùng LoongMate CLI trên PC."
    EXIT_CODE=1
fi

log_and_show "[$(date '+%Y-%m-%d %H:%M:%S')] LoongMate exited with code $EXIT_CODE"
exit $EXIT_CODE
