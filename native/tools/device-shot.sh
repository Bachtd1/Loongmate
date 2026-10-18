#!/bin/sh
# Triển khai binary vào /userdata/lm-native-test/ trên máy, chạy với SDL_VIDEODRIVER=offscreen
# (KHÔNG chiếm màn hình thật), chụp khung hình ra PPM, kéo về và đổi sang PNG.
#   tools/device-shot.sh <tên-ảnh> <binary> [đối số của binary...]
#   vd: tools/device-shot.sh home1 lm-home --pak /mnt/sdcard/Apps/mlp1/LoongMate.pak --keys DOWN,RIGHT
# Ảnh ra ở native/shots/<tên>.png. Không bao giờ chạy chế độ màn hình thật qua SSH.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
NAME=$1; BIN=$2; shift 2
REMOTE=/userdata/lm-native-test
mkdir -p "$HERE/shots"
$SSH "mkdir -p $REMOTE"
tar -cf - -C "$HERE/build" "$BIN" | $SSH "tar -xf - -C $REMOTE && chmod +x $REMOTE/$BIN"
LOCAL_MD5=$(md5sum "$HERE/build/$BIN" | cut -d' ' -f1)
REMOTE_MD5=$($SSH "md5sum $REMOTE/$BIN" | cut -d' ' -f1)
[ "$LOCAL_MD5" = "$REMOTE_MD5" ] || { echo "md5 lệch: $LOCAL_MD5 != $REMOTE_MD5" >&2; exit 1; }
$SSH ". /mnt/sdcard/.system/leaf/platforms/mlp1/launcher/env.sh; cd $REMOTE; rm -f $NAME.ppm; \
  SDL_VIDEODRIVER=offscreen timeout 120 ./$BIN --screenshot $NAME.ppm $* >$NAME.out 2>$NAME.err; echo rc=\$?"
$SSH "cat $REMOTE/$NAME.ppm" > "$HERE/shots/$NAME.ppm"
python3 "$HERE/tools/ppm2png.py" "$HERE/shots/$NAME.ppm" "$HERE/shots/$NAME.png" 2>/dev/null \
  || python "$HERE/tools/ppm2png.py" "$HERE/shots/$NAME.ppm" "$HERE/shots/$NAME.png"
