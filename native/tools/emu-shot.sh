#!/bin/sh
# Thử lm-home trong container ARM64 (qemu) khi KHÔNG có máy: dùng libc/SDL của máy (sysroot), font DejaVu thay font theme Leaf,
# python3 của Ubuntu, dữ liệu sandbox sinh bằng make_sandbox.py. Chỉ để kiểm LOGIC màn hình; ảnh KHÔNG thay thế ảnh chụp trên máy thật.
#   [EMU_POST='lệnh sh chạy sau khi lm-home thoát (vd. đếm snapshot)'] [EMU_EXTRA='tham số lm-home thêm'] [EMU_PREP='lệnh sh chạy trong container sau khi dựng sandbox'] emu-shot.sh <tên> <phím,...>
set -eu
EMU_PREP=${EMU_PREP:-:}; EMU_EXTRA=${EMU_EXTRA:-}; EMU_POST=${EMU_POST:-:}
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HERE/.." && pwd)
NAME=$1; KEYS=${2:-}
OUT=${EMU_OUT:-$HERE/build/emu-out}; mkdir -p "$OUT"
docker image inspect lm-emu >/dev/null 2>&1 || docker build -t lm-emu -f "$HERE/tools/Dockerfile.emu" "$HERE/tools"
MSYS_NO_PATHCONV=1 docker run --rm -v "$HERE":/native:ro -v "$REPO":/repo:ro -v "$OUT":/out lm-emu sh -c "
  set -e
  mkdir -p /sysroot /pak/res /sb && tar -xf /native/sysroot/device-libs.tar -C /sysroot
  cp -r /repo/engine /pak/ && cp /repo/systems.json /repo/bios_hashes.json /pak/res/ && cp /repo/systems.json /repo/bios_hashes.json /pak/
  python3 /native/tools/make_sandbox.py /sb/lm-sandbox-emu >/dev/null
  $EMU_PREP
  rm -f /out/$NAME*.ppm
  cd /out
  SDL_VIDEODRIVER=offscreen CAT_FONTS_DIR=/fonts LOONGMATE_NO_RESCAN=1 timeout 600 \
    /sysroot/ld-linux-aarch64.so.1 --library-path /sysroot:/usr/lib/aarch64-linux-gnu /native/build/lm-home \
    --pak /pak --python /usr/bin/python3 --userdata /sb/lm-sandbox-emu/userdata --primary /sb/lm-sandbox-emu/sd1 \
    --secondary /sb/lm-sandbox-emu/sd2 $EMU_EXTRA --screenshot /out/$NAME.ppm ${KEYS:+--keys $KEYS} 2>&1 | tail -5
  $EMU_POST
"
for f in "$OUT"/$NAME*.ppm; do
  python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "$f" "${f%.ppm}.png" && rm -f "$f"
  echo "shot: ${f%.ppm}.png"
done
