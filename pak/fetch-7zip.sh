#!/bin/sh
# Tải 7-Zip chính thức (bản tĩnh Linux ARM64, 7zzs) cho Nhập game > GIẢI NÉN (.7z/.rar/.zip có mật khẩu...).
# Bản GHIM theo phiên bản + SHA-256: lệch là dừng, không dùng. Kết quả: pak/vendor/7zip/{7zzs,License.txt} (không đưa vào git);
# build.sh chép 7zzs vào LoongMate.pak/bin/, build-all.sh kèm License.txt vào GIAY-PHEP/.
set -eu
VER=26.04
URL="https://github.com/ip7z/7zip/releases/download/$VER/7z2604-linux-arm64.tar.xz"
SHA256=5b0ac3aa91c1e3499f011d6af69d7f6b4de3ef2f011d9e2ff44c58ebd04aa388
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/vendor/7zip"

die() { echo "[fetch-7zip.sh] LỖI: $*" >&2; exit 1; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

if [ -s "$OUT/7zzs" ] && [ -s "$OUT/License.txt" ] && [ "$(cat "$OUT/VERSION" 2>/dev/null)" = "$VER" ]; then
    echo "[fetch-7zip.sh] Đã có 7-Zip $VER ở $OUT"
    exit 0
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TARBALL="${LOONGMATE_7Z_TARBALL:-}"
if [ -z "$TARBALL" ]; then
    TARBALL="$TMP/7z.tar.xz"
    echo "[fetch-7zip.sh] Tải $URL"
    curl -fsSL --retry 3 -o "$TARBALL" "$URL" || die "không tải được $URL"
fi
[ "$(sha "$TARBALL")" = "$SHA256" ] || die "SHA-256 không khớp bản ghim $VER — không dùng file này"
mkdir -p "$TMP/x"
if ! tar -xJf "$TARBALL" -C "$TMP/x" 7zzs License.txt 2>/dev/null; then
    # Không có xz (vd Git Bash trên Windows): giải bằng Python, chỉ lấy đúng 2 file thường cần dùng.
    PY_BIN="$(for p in python3 python py; do "$p" -c "" >/dev/null 2>&1 && { echo "$p"; break; }; done)"
    [ -n "$PY_BIN" ] || die "không có xz hay python để giải $TARBALL"
    "$PY_BIN" -I -c "
import sys, tarfile, shutil
with tarfile.open(sys.argv[1], 'r:xz') as t:
    for n in ('7zzs', 'License.txt'):
        m = t.getmember(n)
        assert m.isfile(), n
        with t.extractfile(m) as src, open(sys.argv[2] + '/' + n, 'wb') as dst:
            shutil.copyfileobj(src, dst)
" "$TARBALL" "$TMP/x" || die "tarball thiếu 7zzs/License.txt"
fi
mkdir -p "$OUT"
cp "$TMP/x/7zzs" "$OUT/7zzs"
cp "$TMP/x/License.txt" "$OUT/License.txt"
chmod +x "$OUT/7zzs"
echo "$VER" > "$OUT/VERSION"
echo "[fetch-7zip.sh] Xong: $OUT/7zzs (7-Zip $VER, linux-arm64, tĩnh)"
