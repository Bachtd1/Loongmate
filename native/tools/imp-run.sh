#!/bin/sh
# Thử Local Importer (Nhập game) trên máy thật: binary native + engine LẤY TỪ WORKING TREE, thẻ GIẢ trong sandbox.
#   binary : /userdata/lm-native-imp/lm-home     engine: /tmp/lm-imp/engine     sandbox: /userdata/lm-sandbox-imp/{sd1,sd2,userdata}
#   imp-run.sh setup                       # đẩy binary + engine + công cụ, so md5, dựng sandbox (imp_prep.py)
#   imp-run.sh push                        # chỉ đẩy lại binary + công cụ, so md5
#   imp-run.sh cli <đối số CLI...>         # chạy `cli.py --json import ...` trên sandbox (đo thời gian bằng `time` phía máy)
#   imp-run.sh sandbox <tên> <phím,...>    # native offscreen trên sandbox (REBUILD=1: dựng lại sandbox sạch trước). Ảnh -> native/shots/c4_<tên>[.n].png
#   imp-run.sh pyshot <tên> <phím,...>     # giao diện Python (ui_sdl.py) offscreen trên sandbox
#   imp-run.sh real-scan                   # `import scan` trên thẻ THẬT (chỉ đọc; userdata = sandbox) + chứng minh không ghi
#   imp-run.sh clean                       # xoá lm-native-imp, lm-sandbox-imp, /tmp/lm-imp và xác nhận sạch
# Luôn LOONGMATE_NO_RESCAN=1. KHÔNG cài gì vào Roms/ thật. KHÔNG kill/khởi động lại tiến trình nào trên máy.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HERE/.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
NB=/userdata/lm-native-imp; SB=/userdata/lm-sandbox-imp; PK=/tmp/lm-imp
PY=/userdata/loongmate-runtime/python/bin/python3
# thẻ Leaf = thẻ có env.sh (hai thẻ có thể đổi chỗ): đặt SDCARD_PATH rồi nạp env.sh -> SDCARD_PATH + UMRK_SECONDARY_SDCARD_PATH thật
LEAFENV='for c in /mnt/sdcard /media/sdcard1; do [ -f $c/.system/leaf/platforms/mlp1/launcher/env.sh ] && { SDCARD_PATH=$c; export SDCARD_PATH; . $c/.system/leaf/platforms/mlp1/launcher/env.sh; break; }; done'
CMD=${1:-}

fetch_shots() {
  mkdir -p "$HERE/shots"
  for f in $($SSH "cd $NB && ls c4_$1*.ppm 2>/dev/null"); do
    $SSH "cat $NB/$f" > "$HERE/shots/$f"
    b=${f%.ppm}
    python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "$HERE/shots/$f" "$HERE/shots/$b.png"
    rm -f "$HERE/shots/$f"
    echo "shot: native/shots/$b.png"
  done
}

push_all() {
  $SSH "mkdir -p $NB $PK/res"
  tar -cf - -C "$HERE/build" lm-home | $SSH "tar -xf - -C $NB && chmod +x $NB/lm-home"
  tar -cf - -C "$HERE/tools" imp_prep.py | $SSH "tar -xf - -C $NB"
  L=$(md5sum "$HERE/build/lm-home" | cut -d' ' -f1); R=$($SSH "md5sum $NB/lm-home" | cut -d' ' -f1)
  [ "$L" = "$R" ] || { echo "md5 lm-home LỆCH: $L != $R" >&2; exit 1; }
  echo "md5 lm-home khớp $L"
}

case $CMD in
setup)
  push_all
  tar -cf - --exclude=__pycache__ -C "$REPO" engine | $SSH "rm -rf $PK/engine && tar -xf - -C $PK"
  tar -cf - -C "$REPO" systems.json bios_hashes.json | $SSH "tar -xf - -C $PK/res && cp $PK/res/systems.json $PK/res/bios_hashes.json $PK/"
  fail=0
  for f in $(cd "$REPO/engine" && find . -name '*.py' | sort); do
    l=$(md5sum "$REPO/engine/$f" | cut -d' ' -f1); r=$($SSH "md5sum $PK/engine/$f" | cut -d' ' -f1)
    [ "$l" = "$r" ] || { echo "md5 LỆCH engine/$f"; fail=1; }
    [ "$r" != d41d8cd98f00b204e9800998ecf8427e ] || { echo "FILE RỖNG engine/$f"; fail=1; }
  done
  [ $fail = 0 ] || exit 1
  echo "md5 engine khớp toàn bộ (không file rỗng)"
  $SSH "$LEAFENV; $PY $NB/imp_prep.py $SB $PK && du -sh $SB/sd1 $SB/sd2"
  ;;
push) push_all ;;
cli)
  shift
  ARGS=""; for a in "$@"; do ARGS="$ARGS '$a'"; done
  $SSH "$LEAFENV; cd $PK; LOONGMATE_NO_RESCAN=1 $PY $PK/engine/cli.py --primary $SB/sd1 --secondary $SB/sd2 --userdata $SB/userdata --quiet --json --no-rescan import $ARGS"
  ;;
sandbox)
  NAME=$2; KEYS=${3:-}
  [ "${REBUILD:-}" = 1 ] && $SSH "$LEAFENV; $PY $NB/imp_prep.py $SB $PK >/dev/null"
  KARG=""; [ -n "$KEYS" ] && KARG="--keys $KEYS"
  $SSH "$LEAFENV; cd $NB; rm -f c4_$NAME*.ppm; LOONGMATE_NO_RESCAN=1 SDL_VIDEODRIVER=offscreen timeout 290 ./lm-home --pak $PK --python $PY --userdata $SB/userdata --primary $SB/sd1 --secondary ${SEC:-$SB/sd2} \
    --screenshot c4_$NAME.ppm $KARG >c4_$NAME.out 2>c4_$NAME.err; echo rc=\$?; tail -3 c4_$NAME.err; true"
  fetch_shots "$NAME"
  ;;
pyshot)
  NAME=$2; KEYS=${3:-}
  [ "${REBUILD:-}" = 1 ] && $SSH "$LEAFENV; $PY $NB/imp_prep.py $SB $PK >/dev/null"
  $SSH "$LEAFENV; cd $PK; rm -f $NB/c4_$NAME*.ppm; LOONGMATE_NO_RESCAN=1 SDL_VIDEODRIVER=offscreen timeout 290 $PY $PK/engine/ui_sdl.py --primary $SB/sd1 --secondary $SB/sd2 --userdata $SB/userdata \
    --screenshot $NB/c4_$NAME.ppm --script '$KEYS' >$NB/c4_$NAME.out 2>$NB/c4_$NAME.err; echo rc=\$?; tail -3 $NB/c4_$NAME.err; true"
  fetch_shots "$NAME"
  ;;
real-scan)
  # KHÔNG nạp env.sh để lấy thẻ phụ: khi hai thẻ đã đổi chỗ, env.sh trả mặc định /media/sdcard1 = chính thẻ Leaf (đã kiểm 2026-10-04).
  # Môi trường thật của Leaf (jawakad) có UMRK_SECONDARY_SDCARD_PATH đúng; ở đây dò: thẻ có env.sh = Leaf, thẻ kia đang mount = phụ.
  $SSH "mkdir -p $NB $SB/userdata && touch $NB/marker && sleep 1"
  $SSH "P=; S=; for c in /mnt/sdcard /media/sdcard1; do if [ -f \$c/.system/leaf/platforms/mlp1/launcher/env.sh ]; then P=\$c; elif grep -q \" \$c \" /proc/mounts; then S=\$c; fi; done;     echo primary=\$P secondary=\$S; cd $PK; $PY $PK/engine/cli.py --primary \$P --secondary \$S --userdata $SB/userdata --quiet --json import scan | head -c 2500"
  echo "--- file/thư mục trên HAI thẻ thật mới hơn mốc ---"
  $SSH "find /mnt/sdcard /media/sdcard1 -newer $NB/marker 2>/dev/null | grep -v 'System Volume Information' | head -40; echo '--- hết danh sách ---'"
  ;;
clean)
  $SSH "rm -rf $NB $SB $PK; ls -d $NB $SB $PK 2>&1 | sed 's/^/còn: /'; echo done"
  ;;
*) sed -n 2,12p "$0"; exit 2 ;;
esac
