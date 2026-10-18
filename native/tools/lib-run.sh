#!/bin/sh
# Thử "Thư viện riêng" trên Pocket 1 THẬT, OFFSCREEN (không chiếm màn hình), thẻ GIẢ trong /userdata/lm-libtest, agent PocketBridge linux/arm64
# (build từ HEAD repo pocketbridge) bind 127.0.0.1 trên chính máy. KHÔNG ghi vào thẻ thật, không kill tiến trình nào ngoài PID do script này tạo.
#   AGENT=<pocketbridge-agent-linux-arm64 trên máy này> native/tools/lib-run.sh setup    # đẩy lm-home + engine + công cụ + agent, so md5 (không file rỗng)
#   native/tools/lib-run.sh e2e [--change-midway]       # lib_e2e.py trên máy: luồng đầy đủ qua CLI + dọn (--secrets-default: dùng /userdata/LoongMate/secrets thật rồi dọn)
#   native/tools/lib-run.sh svc-start                    # dựng thư viện giả + agent + proxy (nền) + bundle ghép đôi mới ở sd1/PocketBridge-import
#   native/tools/lib-run.sh native <tên> <phím,...>      # lm-home offscreen trên sandbox (cùng dãy phím như lib-emu.sh). Ảnh -> native/shots/c4_<tên>[.n].png
#   native/tools/lib-run.sh proxy --rate N | --cut N     # đổi proxy giữa các lần chạy (0 = tắt)
#   native/tools/lib-run.sh svc-stop                     # kill ĐÚNG PID đã ghi + xoá /userdata/lm-libtest/svc, xác nhận sạch
#   native/tools/lib-run.sh clean                        # xoá toàn bộ /userdata/lm-libtest và /tmp/lm-lib, xác nhận
# Luôn LOONGMATE_NO_RESCAN=1. CHƯA từng chạy trên máy thật tại thời điểm viết (máy offline); các lệnh theo đúng khuôn của imp-run.sh đã chạy được.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HERE/.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
BASE=/userdata/lm-libtest; PK=/tmp/lm-lib; SVC=$BASE/svc
PY=/userdata/loongmate-runtime/python/bin/python3
CMD=${1:-}

fetch_shots() {
  mkdir -p "$HERE/shots"
  for f in $($SSH "cd $BASE && ls c4_$1*.ppm 2>/dev/null"); do
    $SSH "cat $BASE/$f" > "$HERE/shots/$f"
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
  : "${AGENT:?đặt AGENT=<pocketbridge-agent-linux-arm64>}"
  $SSH "mkdir -p $BASE/bin $PK/res"
  tar -cf - -C "$HERE/build" lm-home | $SSH "tar -xf - -C $BASE/bin && chmod +x $BASE/bin/lm-home"
  md5_check "$HERE/build/lm-home" $BASE/bin/lm-home
  tar -cf - -C "$(dirname "$AGENT")" "$(basename "$AGENT")" | $SSH "tar -xf - -C $BASE/bin && mv $BASE/bin/$(basename "$AGENT") $BASE/bin/pocketbridge-agent && chmod +x $BASE/bin/pocketbridge-agent"
  md5_check "$AGENT" $BASE/bin/pocketbridge-agent
  tar -cf - -C "$HERE/tools" lib_e2e.py lib_devsvc.py | $SSH "tar -xf - -C $BASE/bin"
  tar -cf - --exclude=__pycache__ -C "$REPO" engine | $SSH "rm -rf $PK/engine && tar -xf - -C $PK"
  tar -cf - -C "$REPO" systems.json bios_hashes.json | $SSH "tar -xf - -C $PK/res && cp $PK/res/systems.json $PK/res/bios_hashes.json $PK/"
  for f in $(cd "$REPO/engine" && find . -name '*.py' | sort); do md5_check "$REPO/engine/$f" "$PK/engine/$f"; done
  echo "md5 khớp toàn bộ (lm-home, agent, engine; không file rỗng)"
  $SSH "$BASE/bin/pocketbridge-agent version; uname -m; $PY --version"
  ;;
e2e)
  shift
  $SSH "cd $PK; $PY $BASE/bin/lib_e2e.py --agent $BASE/bin/pocketbridge-agent --repo $PK --work $BASE/lm-e2e --python $PY $*"
  ;;
svc-start)
  $SSH "$PY $BASE/bin/lib_devsvc.py start --repo $PK --agent $BASE/bin/pocketbridge-agent --work $SVC --mb 3 && $PY $BASE/bin/lib_devsvc.py bundle --work $SVC"
  ;;
proxy)
  shift
  $SSH "$PY $BASE/bin/lib_devsvc.py proxy --work $SVC $*"
  ;;
native)
  NAME=$2; KEYS=${3:-}
  KARG=""; [ -n "$KEYS" ] && KARG="--keys $KEYS"
  $SSH "cd $BASE; rm -f c4_$NAME*.ppm; LOONGMATE_SECRETS_DIR=$SVC/secrets LOONGMATE_LIB_PROXY=none LOONGMATE_NO_RESCAN=1 LM_SHOTB_MS=\${LM_SHOTB_MS:-1500} SDL_VIDEODRIVER=offscreen timeout 290 \
    $BASE/bin/lm-home --pak $PK --python $PY --userdata $SVC/userdata --primary $SVC/sd1 --secondary $SVC/sd2 --screenshot c4_$NAME.ppm $KARG >c4_$NAME.out 2>c4_$NAME.err; echo rc=\$?; tail -3 c4_$NAME.err; true"
  fetch_shots "$NAME"
  ;;
svc-stop)
  $SSH "$PY $BASE/bin/lib_devsvc.py stop --work $SVC --rm"
  ;;
clean)
  $SSH "[ -f $SVC/pids.json ] && $PY $BASE/bin/lib_devsvc.py stop --work $SVC --rm || true; rm -rf $BASE $PK; ls -d $BASE $PK 2>&1 | sed 's/^/còn: /'; echo done"
  ;;
*) sed -n 2,14p "$0"; exit 2 ;;
esac
