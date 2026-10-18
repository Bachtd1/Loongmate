#!/bin/sh
# Thử "Homebrew (tải trực tiếp)" trên Pocket 1 THẬT, OFFSCREEN (không chiếm màn hình), thẻ GIẢ trong /userdata/lm-hb-sandbox/{sd1,sd2}, Internet THẬT (Wi-Fi).
# Binary native + engine LẤY TỪ WORKING TREE. KHÔNG ghi vào thẻ thật, không kill tiến trình nào.
#   binary : /userdata/lm-hb-sandbox/bin/lm-home      engine: /tmp/lm-hb/engine      sandbox: /userdata/lm-hb-sandbox/{sd1,sd2,ud,ud2}
#   hb-run.sh setup                        # đẩy lm-home + engine + run.sh, so md5 (không file rỗng), dựng sandbox
#   hb-run.sh cli <đối số CLI...>          # chạy `cli.py --json homebrew ...` trên sandbox (userdata ud; UD=ud2 để đổi)
#   hb-run.sh native <tên> <phím,...>      # lm-home offscreen. Ảnh -> native/shots/hb_<tên>[.n].png   (UD=ud2 để đổi userdata)
#   hb-run.sh pyshot <tên> <phím,...>      # giao diện Python (ui_sdl.py) offscreen. Ảnh -> native/shots/hb_<tên>[.n].png
#   hb-run.sh reset [cards|all]            # xoá nội dung thẻ giả (cards) hoặc cả userdata/cache (all), xác nhận
#   hb-run.sh clean                        # xoá toàn bộ /userdata/lm-hb-sandbox và /tmp/lm-hb, xác nhận
# Luôn LOONGMATE_NO_RESCAN=1. Thẻ Leaf có thể ở /mnt/sdcard hoặc /media/sdcard1 (đổi chỗ khi khởi động): dò theo env.sh, không cứng đường dẫn.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HERE/.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
SB=/userdata/lm-hb-sandbox; PK=/tmp/lm-hb; NB=$SB/bin
PY=/userdata/loongmate-runtime/python/bin/python3
UD=${UD:-ud}
LEAFENV='for c in /mnt/sdcard /media/sdcard1; do [ -f $c/.system/leaf/platforms/mlp1/launcher/env.sh ] && { SDCARD_PATH=$c; export SDCARD_PATH; . $c/.system/leaf/platforms/mlp1/launcher/env.sh; break; }; done'
CMD=${1:-}

fetch_shots() {
  mkdir -p "$HERE/shots"
  for f in $($SSH "cd $NB && ls hb_$1*.ppm 2>/dev/null"); do
    $SSH "cat $NB/$f" > "$HERE/shots/$f"
    b=${f%.ppm}
    python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "$HERE/shots/$f" "$HERE/shots/$b.png"
    rm -f "$HERE/shots/$f"
    echo "shot: native/shots/$b.png"
  done
}

md5_check() {   # $1 = file cục bộ, $2 = đường dẫn trên máy
  L=$(md5sum "$1" | cut -d' ' -f1); R=$($SSH "md5sum $2" | cut -d' ' -f1)
  [ "$L" = "$R" ] || { echo "md5 LỆCH $2: $L != $R" >&2; exit 1; }
  [ "$R" != d41d8cd98f00b204e9800998ecf8427e ] || { echo "FILE RỖNG $2" >&2; exit 1; }
}

case $CMD in
setup)
  $SSH "mkdir -p $NB $PK $SB/sd1 $SB/sd2 $SB/ud $SB/ud2"
  tar -cf - -C "$HERE/build" lm-home | $SSH "tar -xf - -C $NB && chmod +x $NB/lm-home"
  md5_check "$HERE/build/lm-home" $NB/lm-home
  tar -cf - --exclude=__pycache__ -C "$REPO" engine | $SSH "rm -rf $PK/engine && tar -xf - -C $PK"
  tar -cf - -C "$REPO" systems.json bios_hashes.json | $SSH "tar -xf - -C $PK"
  for f in $(cd "$REPO/engine" && find . -name '*.py' | sort); do md5_check "$REPO/engine/$f" "$PK/engine/$f"; done
  md5_check "$REPO/systems.json" $PK/systems.json
  echo "md5 khớp toàn bộ (lm-home, engine, systems.json; không file rỗng)"
  ;;
cli)
  shift
  ARGS=""; for a in "$@"; do ARGS="$ARGS '$a'"; done
  $SSH "$LEAFENV; cd $PK; LOONGMATE_NO_RESCAN=1 $PY $PK/engine/cli.py --primary $SB/sd1 --secondary $SB/sd2 --userdata $SB/$UD --quiet --json --no-rescan homebrew $ARGS"
  ;;
native)
  NAME=$2; KEYS=${3:-}
  KARG=""; [ -n "$KEYS" ] && KARG="--keys $KEYS"
  $SSH "$LEAFENV; cd $NB; rm -f hb_$NAME*.ppm; LOONGMATE_NO_RESCAN=1 LM_SHOTB_MS=\${LM_SHOTB_MS:-1500} SDL_VIDEODRIVER=offscreen timeout 290 ./lm-home --pak $PK --python $PY --userdata $SB/$UD --primary $SB/sd1 --secondary $SB/sd2 \
    --screenshot hb_$NAME.ppm $KARG >hb_$NAME.out 2>hb_$NAME.err; echo rc=\$?; tail -3 hb_$NAME.err; true"
  fetch_shots "$NAME"
  ;;
pyshot)
  NAME=$2; KEYS=${3:-}
  $SSH "$LEAFENV; cd $PK; rm -f $NB/hb_$NAME*.ppm; LOONGMATE_NO_RESCAN=1 SDL_VIDEODRIVER=offscreen timeout 290 $PY $PK/engine/ui_sdl.py --primary $SB/sd1 --secondary $SB/sd2 --userdata $SB/$UD \
    --screenshot $NB/hb_$NAME.ppm --script '$KEYS' >$NB/hb_$NAME.out 2>$NB/hb_$NAME.err; echo rc=\$?; tail -3 $NB/hb_$NAME.err; true"
  fetch_shots "$NAME"
  ;;
reset)
  $SSH "rm -rf $SB/sd1/* $SB/sd1/.loongmate-import $SB/sd2/* $SB/sd2/.loongmate-import; $( [ "${2:-cards}" = all ] && echo "rm -rf $SB/ud/* $SB/ud2/*;" ) find $SB/sd1 $SB/sd2 -type f | wc -l | sed 's/^/file còn trên thẻ giả: /'"
  ;;
clean)
  $SSH "rm -rf $SB $PK; ls -d $SB $PK 2>&1 | sed 's/^/còn: /'; echo done"
  ;;
*) sed -n 2,13p "$0"; exit 2 ;;
esac
