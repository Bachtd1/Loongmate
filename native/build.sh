#!/bin/sh
# Build native (ARM64) trong Docker. Kết quả: native/build/lm-home, native/build/lm-hello
# Cần sysroot trước: native/fetch-sysroot.sh (một lần, cần SSH tới máy).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/sysroot/device-libs.tar" ] || { echo "Thiếu sysroot: chạy native/fetch-sysroot.sh" >&2; exit 1; }
docker build -t loongmate-native-build -f "$HERE/Dockerfile" "$HERE"
mkdir -p "$HERE/build"
# MSYS_NO_PATHCONV: Git Bash trên Windows tự đổi /work thành đường dẫn Windows nếu không tắt.
MSYS_NO_PATHCONV=1 docker run --rm -v "$HERE":/work -w /work loongmate-native-build make "$@"
