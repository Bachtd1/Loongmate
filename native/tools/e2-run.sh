#!/bin/sh
# Đợt E2 (xoá nhiều game): chạy binary native MỚI trên máy thật, OFFSCREEN (không chiếm màn hình), với engine LẤY TỪ WORKING TREE (engine trên thẻ là bản cũ).
#   binary : /userdata/lm-native-e2/lm-home      engine: /tmp/lm-e2/engine      sandbox: /userdata/lm-sandbox-e2/{sd1,sd2,userdata}
#   e2-run.sh setup                       # đẩy binary + engine + công cụ thử, so md5, dựng sandbox
#   e2-run.sh push                        # chỉ đẩy lại binary + công cụ thử, so md5
#   e2-run.sh sandbox <tên> <phím,...>    # chạy trên sandbox (được phép ghi). Biến: PREP="snaps,agent_bad,..." (d4_prep.py, chạy TRƯỚC khi mở),
#                                         #   WATCH="<loại kế hoạch>:<hành động>" (d4_watch.py, đổi sandbox NGAY SAU khi kế hoạch được ghi; đặt phím GATE trong <phím> ngay trước phím xác nhận),
#                                         #   REBUILD=1 (dựng lại sandbox sạch trước khi chạy)
#   e2-run.sh real <tên> <phím,...>       # dữ liệu THẬT: CHỈ phím chỉ đọc; tự so sánh cây thư mục trước/sau để chứng minh không ghi
#   e2-run.sh clean                       # xoá /userdata/lm-native-e2, /userdata/lm-sandbox-e2, /tmp/lm-e2 và xác nhận sạch
# Ảnh về native/shots/c4_<tên>[.n].png (đặt <tên> dạng e2_xxx). Luôn LOONGMATE_NO_RESCAN=1. KHÔNG kill/khởi động lại tiến trình nào trên máy.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HERE/.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
NB=/userdata/lm-native-e2; SB=/userdata/lm-sandbox-e2; PK=/tmp/lm-e2
PY=/userdata/loongmate-runtime/python/bin/python3
ENVSH=". /mnt/sdcard/.system/leaf/platforms/mlp1/launcher/env.sh"
CMD=${1:-}

fetch_shots() {   # $1 = tên
  mkdir -p "$HERE/shots"
  for f in $($SSH "cd $NB && ls c4_$1*.ppm 2>/dev/null"); do
    $SSH "cat $NB/$f" > "$HERE/shots/$f"
    b=${f%.ppm}
    python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "$HERE/shots/$f" "$HERE/shots/$b.png"
    rm -f "$HERE/shots/$f"
    echo "shot: native/shots/$b.png"
  done
}

case $CMD in
setup)
  $SSH "mkdir -p $NB $PK/res"
  tar -cf - -C "$HERE/build" lm-home | $SSH "tar -xf - -C $NB && chmod +x $NB/lm-home"
  tar -cf - -C "$HERE/tools" make_sandbox.py d4_prep.py d4_watch.py | $SSH "tar -xf - -C $NB"
  tar -cf - --exclude=__pycache__ -C "$REPO" engine | $SSH "rm -rf $PK/engine && tar -xf - -C $PK"
  tar -cf - -C "$REPO" systems.json bios_hashes.json | $SSH "tar -xf - -C $PK/res && cp $PK/res/systems.json $PK/res/bios_hashes.json $PK/"
  L=$(md5sum "$HERE/build/lm-home" | cut -d' ' -f1); R=$($SSH "md5sum $NB/lm-home" | cut -d' ' -f1)
  [ "$L" = "$R" ] || { echo "md5 lm-home LỆCH: $L != $R" >&2; exit 1; }
  echo "md5 lm-home khớp $L"
  fail=0
  for f in $(cd "$REPO/engine" && find . -name '*.py' | sort); do
    l=$(md5sum "$REPO/engine/$f" | cut -d' ' -f1); r=$($SSH "md5sum $PK/engine/$f" | cut -d' ' -f1)
    [ "$l" = "$r" ] || { echo "md5 LỆCH engine/$f"; fail=1; }
  done
  [ $fail = 0 ] || exit 1
  echo "md5 engine khớp toàn bộ"
  $SSH "$ENVSH; $PY $NB/make_sandbox.py $SB && ls $SB"
  ;;
push)   # chỉ đẩy lại binary + công cụ thử (sau khi build lại), so md5
  tar -cf - -C "$HERE/build" lm-home | $SSH "tar -xf - -C $NB && chmod +x $NB/lm-home"
  tar -cf - -C "$HERE/tools" make_sandbox.py d4_prep.py d4_watch.py | $SSH "tar -xf - -C $NB"
  L=$(md5sum "$HERE/build/lm-home" | cut -d' ' -f1); R=$($SSH "md5sum $NB/lm-home" | cut -d' ' -f1)
  [ "$L" = "$R" ] || { echo "md5 lm-home LỆCH: $L != $R" >&2; exit 1; }
  echo "md5 lm-home khớp $L"
  ;;
sandbox)
  NAME=$2; KEYS=${3:-}
  [ "${REBUILD:-}" = 1 ] && $SSH "$ENVSH; $PY $NB/make_sandbox.py $SB >/dev/null"
  [ -n "${PREP:-}" ] && $SSH "$ENVSH; $PY $NB/d4_prep.py $SB $PK $PREP"
  W=""
  if [ -n "${WATCH:-}" ]; then
    kind=${WATCH%%:*}; act=${WATCH#*:}
    W="($PY $NB/d4_watch.py $SB/userdata/jobs/native-$kind.plan.json $act $SB $NB/c4_$NAME.ppm.gate > $NB/watch_$NAME.log 2>&1 &);"
  fi
  KARG=""; [ -n "$KEYS" ] && KARG="--keys $KEYS"
  $SSH "$ENVSH; cd $NB; rm -f c4_$NAME*.ppm c4_$NAME.ppm.gate watch_$NAME.log; $W \
    LOONGMATE_NO_RESCAN=1 SDL_VIDEODRIVER=offscreen timeout 290 ./lm-home --pak $PK --python $PY --userdata $SB/userdata --primary $SB/sd1 --secondary $SB/sd2 \
    --screenshot c4_$NAME.ppm $KARG >c4_$NAME.out 2>c4_$NAME.err; echo rc=\$?; tail -3 c4_$NAME.err; cat watch_$NAME.log 2>/dev/null; true"
  fetch_shots "$NAME"
  ;;
real)
  NAME=$2; KEYS=${3:-}
  KARG=""; [ -n "$KEYS" ] && KARG="--keys $KEYS"
  # chứng minh KHÔNG ghi: đặt mốc thời gian TRƯỚC khi chạy, rồi liệt kê mọi file/thư mục trên thẻ thật mới hơn mốc.
  # (busybox find không có -printf; agent LoongMate của người dùng đang chạy có thể tự ghi telemetry/agent_state — tên các file đó là của agent, không phải của lm-home.)
  $SSH "mkdir -p $NB && touch $NB/marker && sleep 1"
  $SSH "$ENVSH; cd $NB; rm -f c4_$NAME*.ppm;     LOONGMATE_NO_RESCAN=1 SDL_VIDEODRIVER=offscreen timeout 290 ./lm-home --pak $PK --python $PY     --screenshot c4_$NAME.ppm $KARG >c4_$NAME.out 2>c4_$NAME.err; echo rc=\$?; tail -3 c4_$NAME.err"
  echo "--- file/thư mục trên thẻ thật mới hơn mốc (mới hơn = có ai đó ghi trong lúc chạy) ---"
  $SSH "find /mnt/sdcard -newer $NB/marker 2>/dev/null | grep -v 'System Volume Information' | head -40; echo '--- hết danh sách ---'"
  fetch_shots "$NAME"
  ;;
clean)
  $SSH "rm -rf $NB $SB $PK; ls -d $NB $SB $PK 2>&1 | sed 's/^/còn: /'; echo done"
  ;;
*) sed -n 2,13p "$0"; exit 2 ;;
esac
