#!/bin/sh
# Kéo các thư viện cần để link từ CHÍNH MÁY Pocket 1 (chỉ đọc) về native/sysroot/device-libs.tar.
# Dùng: native/fetch-sysroot.sh   (đặt LM_SSH nếu khác mặc định)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
mkdir -p "$HERE/sysroot"
# Ghi ra file tạm rồi mới đổi tên: đứt giữa chừng không để lại tar cụt.
# Chỉ những gì thật sự cần để link: SDL2/SDL2_ttf/SDL2_image và phụ thuộc trực tiếp của chúng.
$SSH 'cd /usr/lib && ls -d libc.so* libm.so* ld-linux* libpthread* libdl* librt* libSDL2.so libSDL2-2.0.so*   libSDL2_ttf* libSDL2_image* libfreetype* libpng* libz.so* libharfbuzz.so* libharfbuzz.so.0* 2>/dev/null   | grep -v -E "libSDL2_(gfx|mixer|net)" | tar -cf - -T -' > "$HERE/sysroot/device-libs.tar.part"
mv "$HERE/sysroot/device-libs.tar.part" "$HERE/sysroot/device-libs.tar"
tar -tf "$HERE/sysroot/device-libs.tar" | grep -q '^libSDL2-2.0.so.0.2800.5$' \
  || { echo "tar thiếu libSDL2 2.28.5 — máy khác phiên bản?" >&2; exit 1; }
echo "OK: $HERE/sysroot/device-libs.tar"
