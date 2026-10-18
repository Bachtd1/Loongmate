#!/bin/sh
# Triển khai build/lm-home vào /userdata/lm-native-c4/ trên máy, chạy OFFSCREEN (không chiếm màn hình thật),
# tiêm phím giả, chụp nhiều ảnh (token SHOT trong --keys) rồi kéo về native/shots/c4_<tên>[.n].png.
#   c4-shot.sh sandbox <tên> <phím,phím,SHOT,...>   # dữ liệu giả ở /userdata/lm-sandbox-c4 (được phép ghi)
#   c4-shot.sh real    <tên> <phím,...>             # dữ liệu THẬT của máy: CHỈ dùng phím chỉ đọc
# Luôn đặt LOONGMATE_NO_RESCAN=1. Không bao giờ chạy chế độ màn hình thật qua SSH.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
MODE=$1; NAME=$2; KEYS=${3:-}
REMOTE=/userdata/lm-native-c4
$SSH "mkdir -p $REMOTE"
tar -cf - -C "$HERE/build" lm-home | $SSH "tar -xf - -C $REMOTE && chmod +x $REMOTE/lm-home"
LOCAL_MD5=$(md5sum "$HERE/build/lm-home" | cut -d' ' -f1)
REMOTE_MD5=$($SSH "md5sum $REMOTE/lm-home" | cut -d' ' -f1)
[ "$LOCAL_MD5" = "$REMOTE_MD5" ] || { echo "md5 lệch: $LOCAL_MD5 != $REMOTE_MD5" >&2; exit 1; }
case $MODE in
  sandbox) S=/userdata/lm-sandbox-c4
           ARGS="--pak $S/pak --userdata $S/userdata --primary $S/sd1 --secondary $S/sd2" ;;
  real)    ARGS="--pak /mnt/sdcard/Apps/mlp1/LoongMate.pak" ;;
  *) echo "mode phải là sandbox|real" >&2; exit 2 ;;
esac
KARG=""; [ -n "$KEYS" ] && KARG="--keys $KEYS"
$SSH ". /mnt/sdcard/.system/leaf/platforms/mlp1/launcher/env.sh; cd $REMOTE; rm -f c4_$NAME*.ppm; \
  LOONGMATE_NO_RESCAN=1 SDL_VIDEODRIVER=offscreen timeout 280 ./lm-home $ARGS --screenshot c4_$NAME.ppm $KARG >c4_$NAME.out 2>c4_$NAME.err; echo rc=\$?; tail -3 c4_$NAME.err"
mkdir -p "$HERE/shots"
for f in $($SSH "cd $REMOTE && ls c4_$NAME*.ppm 2>/dev/null"); do
  $SSH "cat $REMOTE/$f" > "$HERE/shots/$f"
  b=${f%.ppm}
  python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "$HERE/shots/$f" "$HERE/shots/$b.png" 2>/dev/null     || python "$HERE/tools/ppm2png.py" "$HERE/shots/$f" "$HERE/shots/$b.png" >/dev/null
  rm -f "$HERE/shots/$f"
  echo "shot: shots/$b.png"
done
