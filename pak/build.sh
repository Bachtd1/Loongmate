#!/bin/sh
# Builds a ready-to-run LoongMate.pak: bundles the real Python engine and the
# current systems.json into the pak, then zips the result.
#
# Usage: sh pak/build.sh
# Output: pak/build/LoongMate.pak.zip (and the staged tree at pak/build/LoongMate.pak/)
#
# POSIX sh only, no bash-isms.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_PAK="$SCRIPT_DIR/Apps/mlp1/LoongMate.pak"
BUILD_DIR="$SCRIPT_DIR/build"
OUT_PAK="$BUILD_DIR/LoongMate.pak"
ZIP_PATH="$BUILD_DIR/LoongMate.pak.zip"

echo "[build.sh] Dọn thư mục build cũ: $OUT_PAK"
rm -rf "$OUT_PAK"
mkdir -p "$OUT_PAK"

echo "[build.sh] Sao chép khung pak từ $SRC_PAK"
cp -R "$SRC_PAK/." "$OUT_PAK/"

echo "[build.sh] Đóng gói engine thật vào $OUT_PAK/engine/"
mkdir -p "$OUT_PAK/engine"
for f in "$PROJECT_ROOT"/engine/*.py; do
    name="$(basename "$f")"
    cp "$f" "$OUT_PAK/engine/$name"
    echo "  - đã copy engine/$name"
done

echo "[build.sh] Đồng bộ res/systems.json từ systems.json gốc (ghi đè bản cũ trong pak)"
cp "$PROJECT_ROOT/systems.json" "$OUT_PAK/res/systems.json"
echo "  - đã copy systems.json -> res/systems.json"

echo "[build.sh] Đồng bộ res/bios_hashes.json (bios.py tìm cả res/)"
cp "$PROJECT_ROOT/bios_hashes.json" "$OUT_PAK/res/bios_hashes.json"
echo "  - đã copy bios_hashes.json -> res/bios_hashes.json"

echo "[build.sh] Danh mục ngôn ngữ (i18n/*.json -> res/i18n_<lang>.json; giao diện và engine đọc khi chọn Language)"
for f in "$PROJECT_ROOT"/i18n/*.json; do
    [ -f "$f" ] || continue                       # không có i18n/ (bản thử tối giản): chỉ tiếng Việt
    lang="$(basename "$f" .json)"
    case "$lang" in strings) continue ;; esac
    cp "$f" "$OUT_PAK/res/i18n_$lang.json"
    echo "  - đã copy i18n/$lang.json -> res/i18n_$lang.json"
done

echo "[build.sh] Dọn __pycache__/tests/playground/simulator nếu lỡ lọt vào pak"
find "$OUT_PAK" -type d -name "__pycache__" -prune -exec rm -rf {} \;
rm -rf "$OUT_PAK/tests" "$OUT_PAK/playground" "$OUT_PAK/simulator"

# Runtime CPython di động (~30 MB, KHÔNG nằm trong git): đặt ở pak/loongmate-runtime.tar.gz, gốc tar là loongmate-runtime/python/.
# launch.sh tự giải nén vào /userdata khi máy chưa có Python. Thiếu file này thì gói chỉ chạy trên máy đã có runtime.
RUNTIME_TAR="${LOONGMATE_RUNTIME_TAR:-$SCRIPT_DIR/loongmate-runtime.tar.gz}"
if [ -f "$RUNTIME_TAR" ]; then
    cp "$RUNTIME_TAR" "$OUT_PAK/loongmate-runtime.tar.gz"
    echo "  - đã kèm loongmate-runtime.tar.gz (runtime CPython di động)"
else
    echo "[build.sh] CẢNH BÁO: không có $RUNTIME_TAR — gói KHÔNG kèm Python, máy chưa cài runtime sẽ báo thiếu Python."
fi


# Chuẩn hoá line ending sang LF. Shell trên thiết bị (buildroot) KHÔNG chạy được
# script CRLF: nó báo "command not found" cho ký tự CR rồi chết ngay dòng đầu.
# Git Bash trên Windows lại chấp nhận CRLF, nên "sh -n" ở máy dev không bắt được
# lỗi này — chỉ chạy thật trên máy mới lộ ra (đã gặp 2026-10-02).
if command -v tr >/dev/null 2>&1; then
    tmp_lf="$OUT_PAK/.launch.lf"
    tr -d '\015' < "$OUT_PAK/launch.sh" > "$tmp_lf" && mv "$tmp_lf" "$OUT_PAK/launch.sh"
    echo "  - đã chuẩn hoá line ending của launch.sh sang LF"
fi
chmod +x "$OUT_PAK/launch.sh"
echo "  - đã chmod +x launch.sh"

# Binary native (giao diện C) nằm sẵn trong pak nguồn, đã được cp -R ở trên. launch.sh chạy nó trước, rơi về Python nếu không chạy được.
if [ ! -s "$SRC_PAK/bin/lm-home" ]; then
    echo "[build.sh] LỖI: thiếu hoặc rỗng $SRC_PAK/bin/lm-home (build bằng native/build.sh rồi chép native/build/lm-home vào đó)." >&2
    exit 1
fi
chmod +x "$OUT_PAK/bin/lm-home"
cmp -s "$SRC_PAK/bin/lm-home" "$OUT_PAK/bin/lm-home" || { echo "[build.sh] LỖI: bin/lm-home trong bản dựng khác bản nguồn." >&2; exit 1; }
echo "  - đã chép bin/lm-home (binary native) và chmod +x"

echo "[build.sh] Tạo $ZIP_PATH"
rm -f "$ZIP_PATH"
if command -v zip >/dev/null 2>&1; then
    ( cd "$BUILD_DIR" && zip -r -q "$(basename "$ZIP_PATH")" "$(basename "$OUT_PAK")" )
    echo "[build.sh] Hoàn tất: $ZIP_PATH"
elif PY_BIN="$(for p in python3 python py; do "$p" -c "" >/dev/null 2>&1 && { echo "$p"; break; }; done)" && [ -n "$PY_BIN" ]; then
    # Chạy thử chứ không chỉ "command -v": trên Windows, python3 có thể là shortcut rỗng của Microsoft Store.
    echo "[build.sh] Không có lệnh 'zip', dùng $PY_BIN -m zipfile thay thế."
    ( cd "$BUILD_DIR" && "$PY_BIN" -c "
import zipfile, pathlib
root = pathlib.Path('$(basename "$OUT_PAK")')
with zipfile.ZipFile('$(basename "$ZIP_PATH")', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in root.rglob('*'):
        if p.is_file():
            z.write(p, p.as_posix())
" )
    echo "[build.sh] Hoàn tất: $ZIP_PATH"
else
    echo "[build.sh] CẢNH BÁO: không tìm thấy lệnh 'zip' hay python trên máy này. Cây pak đã dựng sẵn tại $OUT_PAK, nhưng chưa nén thành zip. Hành động cần làm: cài zip (vd 'apt-get install zip') rồi chạy lại, hoặc tự nén thủ công thư mục trên."
fi
