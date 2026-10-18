#!/bin/sh
# Gói all-in-one để share: LoongMate + LoongMate Agent + Leaf Hot Tabs, giải nén là chép thẳng vào thẻ.
#
# Usage: sh pak/build-all.sh            (HOTTABS_DIR=... để trỏ repo leaf-hottabs, mặc định ../leaf-hottabs)
#        HOTTABS_DIR=none sh pak/build-all.sh   gói không kèm Hot Tabs (bản phát hành công khai: tự tiếp tục qua Agent)
# Output: pak/build/LoongMate-AllInOne.zip
#
#   LoongMate-AllInOne/
#   ├── DOC-TRUOC.txt
#   ├── THE-NHO/                    chép NỘI DUNG thư mục này vào gốc thẻ Leaf
#   │   ├── Apps/mlp1/LoongMate.pak
#   │   ├── Apps/mlp1/LoongMateAgent.pak
#   │   └── hottabs/                Hot Tabs cho Leaf mới nhất có bản dựng (HOTTABS_DEFAULT)
#   └── HotTabs-ban-khac/<ver>/hottabs/   các phiên bản Leaf khác
#
# Không gói Tailscale (cần tải binary + đăng nhập tailnet riêng, xem TAILSCALE.md) và PocketBridge (repo riêng).
# POSIX sh only, no bash-isms.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOTTABS_DIR="${HOTTABS_DIR:-$PROJECT_ROOT/../leaf-hottabs}"
BUILD_DIR="$SCRIPT_DIR/build"
NAME="LoongMate-AllInOne"
STAGE="$BUILD_DIR/$NAME"
ZIP_PATH="$BUILD_DIR/$NAME.zip"

die() { echo "[build-all.sh] LỖI: $*" >&2; exit 1; }

VERSIONS=""
HOTTABS_DEFAULT=""
if [ "$HOTTABS_DIR" != "none" ]; then
    [ -d "$HOTTABS_DIR/dist" ] || die "không thấy $HOTTABS_DIR/dist (đặt HOTTABS_DIR trỏ tới repo leaf-hottabs đã build, hoặc HOTTABS_DIR=none)."
    VERSIONS="$(ls -d "$HOTTABS_DIR"/dist/hottabs-v* 2>/dev/null | sed 's|.*/hottabs-||' | sort -t. -k1,1 -k2,2n -k3,3n)"
    [ -n "$VERSIONS" ] || die "không có bộ Hot Tabs nào trong $HOTTABS_DIR/dist (chạy scripts/package.sh bên leaf-hottabs)."
    HOTTABS_DEFAULT="${HOTTABS_DEFAULT:-$(echo "$VERSIONS" | tail -1)}"
fi

echo "[build-all.sh] Dựng LoongMate.pak"
sh "$SCRIPT_DIR/build.sh" >/dev/null

rm -rf "$STAGE"
mkdir -p "$STAGE/THE-NHO/Apps/mlp1"
cp -R "$BUILD_DIR/LoongMate.pak" "$STAGE/THE-NHO/Apps/mlp1/"
if [ -f "$SCRIPT_DIR/loongmate-runtime.tar.gz" ] && [ ! -f "$STAGE/THE-NHO/Apps/mlp1/LoongMate.pak/loongmate-runtime.tar.gz" ]; then
    cp "$SCRIPT_DIR/loongmate-runtime.tar.gz" "$STAGE/THE-NHO/Apps/mlp1/LoongMate.pak/"
fi
cp -R "$SCRIPT_DIR/Apps/mlp1/LoongMateAgent.pak" "$STAGE/THE-NHO/Apps/mlp1/"
echo "  - Apps/mlp1/LoongMate.pak, Apps/mlp1/LoongMateAgent.pak"
if [ -f "$STAGE/THE-NHO/Apps/mlp1/LoongMate.pak/loongmate-runtime.tar.gz" ]; then
    echo "  - Kèm runtime CPython di động (tự giải nén vào /userdata khi mở lần đầu)"
fi

# Bộ Hot Tabs: kiểm md5 launcher đã patch khớp manifest trước khi gói, tránh share bản hỏng.
check_hottabs() {
    d="$1"
    for f in hottabs.sh hottabs.manifest bin/jawaka-launcher; do [ -s "$d/$f" ] || die "thiếu $d/$f"; done
    want="$(sed -n 's/^patched\.jawaka-launcher=//p' "$d/hottabs.manifest" | head -1)"
    got="$(md5sum < "$d/bin/jawaka-launcher" | cut -c1-32)"
    [ "$want" = "$got" ] || die "$d/bin/jawaka-launcher md5 $got khác manifest $want"
}
for v in $VERSIONS; do
    src="$HOTTABS_DIR/dist/hottabs-$v"
    check_hottabs "$src"
    if [ "$v" = "$HOTTABS_DEFAULT" ]; then dst="$STAGE/THE-NHO/hottabs"; else dst="$STAGE/HotTabs-ban-khac/$v/hottabs"; fi
    mkdir -p "$dst"
    cp -R "$src/." "$dst/"
    echo "  - Hot Tabs $v -> ${dst#$STAGE/}"
done

# Script chạy trên máy (buildroot) phải là LF, xem ghi chú trong build.sh.
find "$STAGE" -name "*.sh" | while read -r f; do tr -d '\015' < "$f" > "$f.lf" && mv "$f.lf" "$f"; done

OTHERS="$(echo "$VERSIONS" | grep -vx "$HOTTABS_DEFAULT" | tr '\n' ' ')"
if [ -n "$VERSIONS" ]; then
    HT_TREE="
     hottabs/"
    RESUME_DOC="3. LEAF HOT TABS (tuỳ chọn: mở game từ Games/Favorites là tiếp tục chỗ đang chơi)
   Hot Tabs thay launcher của Leaf nên PHẢI ĐÚNG PHIÊN BẢN LEAF.
   Xem phiên bản: Menu -> Info -> Device.
   - Leaf $HOTTABS_DEFAULT: thư mục hottabs/ đã đúng, không cần làm gì thêm.
   - Leaf khác: xoá hottabs/ trên thẻ, chép HotTabs-ban-khac/<phiên bản>/hottabs vào gốc thẻ.
     Có sẵn: ${OTHERS:-(không có)}
   - Không có bản nào khớp: bỏ qua Hot Tabs. LoongMate sẽ báo \"không khớp phiên bản Leaf\"
     và không cho cài, máy không bị ảnh hưởng.
   Cài: LoongMate -> Máy -> Leaf Hot Tabs -> Cài lại Hot Tabs, xác nhận, rồi KHỞI ĐỘNG LẠI máy.
   Bộ cài tự sao lưu launcher gốc và kiểm md5 trước khi thay; gỡ ở cùng màn đó.
   Khi cập nhật Leaf, launcher trở về bản gốc (vô hại); cần bộ Hot Tabs mới cho bản Leaf mới."
else
    HT_TREE=""
    RESUME_DOC="3. TỰ TIẾP TỤC (thử nghiệm, mặc định tắt: mở game từ Games/Favorites là tiếp tục chỗ đang chơi)
   Cần bật LoongMate Agent (mục 2). Rồi: LoongMate -> Máy -> Leaf Hot Tabs ->
   \"Tự tiếp tục qua Agent (không vá launcher)\" -> Bật.
   Không sửa file nào của Leaf, dùng được qua các lần cập nhật Leaf."
fi
cat > "$STAGE/DOC-TRUOC.txt" <<EOF
LoongMate cho Miniloong Pocket 1 (Leaf CFW) - DEVELOPED BY BACHTD

1. CÀI LOONGMATE
   Tắt máy, cắm thẻ nhớ chứa Leaf vào máy tính. Chép TOÀN BỘ NỘI DUNG thư mục THE-NHO
   vào gốc thẻ (gộp với thư mục Apps có sẵn, KHÔNG ghi đè/xoá gì khác).
   Kết quả trên thẻ:
     Apps/mlp1/LoongMate.pak
     Apps/mlp1/LoongMateAgent.pak$HT_TREE
   Trên máy: Apps -> LoongMate. Không thấy thì System -> Rescan Library.
   LƯU Ý: Lần đầu tiên mở LoongMate, app sẽ tự động giải nén bộ CPython runtime (~3-5 giây)
   vào bộ nhớ trong máy (/userdata/loongmate-runtime). Từ các lần sau, app mở ngay tức thì.

2. LOONGMATE AGENT (nên bật: tự sao lưu save chạy nền)
   Settings -> Services -> LoongMate Agent: bật "Start with Leaf" rồi "Run".
   Agent dùng chung engine với LoongMate.pak, cần cài LoongMate.pak cùng lúc.

$RESUME_DOC

Hướng dẫn đầy đủ, mã lỗi, báo lỗi: https://github.com/Bachtd1/Loongmate
LoongMate không sửa kernel, boot hay phân vùng hệ thống; không xoá file (chỉ chuyển vào Quarantine/).
EOF
sed 's/$/\r/' "$STAGE/DOC-TRUOC.txt" > "$STAGE/DOC-TRUOC.tmp" && mv "$STAGE/DOC-TRUOC.tmp" "$STAGE/DOC-TRUOC.txt"  # dễ đọc bằng Notepad

# Giấy phép bên thứ ba (MIT yêu cầu kèm thông báo khi phân phối bản dựng): Jawaka (Hot Tabs), Catastrophe + cJSON (lm-home),
# CPython (runtime, LICENSE.txt lấy từ chính tarball đi kèm).
mkdir -p "$STAGE/GIAY-PHEP"
cp "$SCRIPT_DIR"/licenses/*.txt "$STAGE/GIAY-PHEP/"
[ -n "$VERSIONS" ] || rm -f "$STAGE/GIAY-PHEP/Jawaka-LICENSE.txt"   # không kèm Hot Tabs thì không phân phối mã Jawaka
RT="$STAGE/THE-NHO/Apps/mlp1/LoongMate.pak/loongmate-runtime.tar.gz"
if [ -f "$RT" ]; then
    tar -xzOf "$RT" loongmate-runtime/python/lib/python3.12/LICENSE.txt > "$STAGE/GIAY-PHEP/CPython-LICENSE.txt" \
        || die "không đọc được LICENSE.txt của CPython trong $RT"
fi
JAWAKA_NOTE=""
[ -z "$VERSIONS" ] || JAWAKA_NOTE="- Jawaka (launcher Leaf, bản vá Hot Tabs: hottabs/bin/jawaka-launcher) - MIT - Jawaka-LICENSE.txt
  https://github.com/Utility-Muffin-Research-Kitchen/Jawaka
"
cat > "$STAGE/GIAY-PHEP/README.txt" <<EOF
Gói này kèm phần mềm bên thứ ba, phân phối theo giấy phép gốc của từng dự án:
${JAWAKA_NOTE}- Catastrophe (thư viện giao diện, trong LoongMate.pak/bin/lm-home) - MIT - Catastrophe-LICENSE.txt
- cJSON (trong lm-home) - MIT - cJSON-LICENSE.txt
- CPython 3.12 (loongmate-runtime.tar.gz) - PSF License - CPython-LICENSE.txt
  (giấy phép các thư viện đi kèm CPython nằm trong runtime sau khi giải nén)
EOF

PY_BIN="$(for p in python3 python py; do "$p" -c "" >/dev/null 2>&1 && { echo "$p"; break; }; done)"
rm -f "$ZIP_PATH"
if command -v zip >/dev/null 2>&1; then
    ( cd "$BUILD_DIR" && zip -r -q "$NAME.zip" "$NAME" )
elif [ -n "$PY_BIN" ]; then
    ( cd "$BUILD_DIR" && "$PY_BIN" -c "
import zipfile, pathlib
root = pathlib.Path('$NAME')
with zipfile.ZipFile('$NAME.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file():
            z.write(p, p.as_posix())
" )
else
    die "không có 'zip' hay python để nén; cây đã dựng ở $STAGE."
fi
echo "[build-all.sh] Hoàn tất: $ZIP_PATH (Hot Tabs mặc định: ${HOTTABS_DEFAULT:-không kèm})"
