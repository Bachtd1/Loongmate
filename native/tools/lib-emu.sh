#!/bin/sh
# Thử màn "Thư viện riêng" của lm-home (native) trong container ARM64 (qemu) KHI KHÔNG CÓ MÁY: agent PocketBridge THẬT (linux/arm64, build từ HEAD
# repo pocketbridge) + proxy giới hạn tốc độ/cắt kết nối + thẻ giả, tất cả trong container. Ảnh KHÔNG thay thế ảnh chụp trên máy thật.
#   AGENT=<đường dẫn pocketbridge-agent-linux-arm64 trên máy này> native/tools/lib-emu.sh <kịch bản> [phím thêm]
# Kịch bản: menu | pair | browse | fetch | pause | resume | py
#   menu   : Library -> mục "Thư viện riêng"
#   pair   : Thư viện riêng (chưa có node, 1 bundle) -> Ghép đôi -> xác nhận (mặc định Huỷ) -> kết quả
#   browse : node -> thư mục -> hệ máy -> game, X chọn nhiều, Y sắp xếp, Lọc
#   fetch  : chọn 2 game -> kiểm với server -> xác nhận -> tải có THANH TIẾN ĐỘ (proxy 600 KB/s) -> kết quả -> danh sách đã có "đã có"
#   pause  : tải file lớn (proxy chậm) -> bấm B giữa chừng (tạm dừng) -> job còn .part -> mở lại lm-home -> "Tải dở" -> tiếp tục -> cài
#   resume : một job bị CẮT KẾT NỐI giữa chừng (proxy) -> mục "Tải dở" -> tiếp tục
#   py     : GIAO DIỆN PYTHON (ui_sdl.py) cho cùng luồng: nodes, game + chọn nhiều, xác nhận, tiến độ %, kết quả (mỗi ảnh một lần chạy)
# Ảnh -> native/build/emu-out/lib_<kịch bản>.<n>.png
set -eu
SCEN=${1:?kịch bản}
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HERE/.." && pwd)
OUT=${EMU_OUT:-$HERE/build/emu-out}; mkdir -p "$OUT"
AGENT=${AGENT:?đặt AGENT=<pocketbridge-agent-linux-arm64>}
AG_DIR=$(cd "$(dirname "$AGENT")" && (pwd -W 2>/dev/null || pwd)); AG_NAME=$(basename "$AGENT")
docker image inspect lm-emu >/dev/null 2>&1 || docker build -t lm-emu -f "$HERE/tools/Dockerfile.emu" "$HERE/tools"
KEYS2=""
HOME_KEYS="DOWN,RIGHT,A"       # Home -> thẻ Library (chọn ô thứ 4) -> mở
case $SCEN in
  menu)   KEYS="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,SHOT"; PREP=":" ;;
  pair)   KEYS="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,A,SHOT,A,SHOT,A,SHOT,RIGHT,A,SHOT,A,SHOT"
          PREP="svc bundle" ;;
  browse) KEYS="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,A,SHOT,A,SHOT,A,SHOT,A,SHOT,X,DOWN,X,SHOT,Y,SHOT,UP,UP,UP,A,SHOT,A,SHOT"
          PREP="svc bundle pair" ;;
  fetch)  KEYS="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,A,A,A,A,X,DOWN,X,UP,UP,UP,SHOT,A,SHOT,RIGHT,A,SHOTB,SHOT,A,SHOT"
          PREP="svc bundle pair slow" ;;
  resume) KEYS="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,A,SHOT,DOWN,SHOT,A,SHOT,RIGHT,A,SHOT,A,SHOT"
          PREP="svc bundle pair cutjob" ;;
  pause)  KEYS="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,A,A,A,A,X,UP,UP,A,RIGHT,A,BUSYB,SHOT,A,SHOT"
          KEYS2="$HOME_KEYS,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,A,SHOT,DOWN,SHOT,A,SHOT,RIGHT,A,SHOTB,SHOT,A,SHOT"
          PREP="svc bundle pair slow" ;;
  py)     KEYS=""; PREP="svc bundle pair slow" ;;
  *) echo "kịch bản lạ: $SCEN" >&2; exit 2 ;;
esac
[ -n "${2:-}" ] && KEYS="$KEYS,$2"
MSYS_NO_PATHCONV=1 docker run --rm -v "$HERE":/native:ro -v "$REPO":/repo:ro -v "$AG_DIR":/agentdir:ro -v "$OUT":/out lm-emu sh -c "
  set -e
  mkdir -p /sysroot /pak/res && tar -xf /native/sysroot/device-libs.tar -C /sysroot
  cp -r /repo/engine /pak/ && cp /repo/systems.json /repo/bios_hashes.json /pak/res/ && cp /repo/systems.json /repo/bios_hashes.json /pak/
  W=/tmp/lm-libemu
  DS='python3 /native/tools/lib_devsvc.py'
  export LOONGMATE_SECRETS_DIR=\$W/secrets LOONGMATE_LIB_PROXY=none LOONGMATE_NO_RESCAN=1
  CLI() { python3 /pak/engine/cli.py --primary \$W/sd1 --secondary \$W/sd2 --userdata \$W/userdata --quiet --json --no-rescan library \"\$@\"; }
  for step in $PREP; do
    case \$step in
      svc) cp /agentdir/$AG_NAME /tmp/agent && chmod +x /tmp/agent && \$DS start --repo /pak --agent /tmp/agent --work \$W --mb 3 ;;
      bundle) \$DS bundle --work \$W ;;
      pair) CLI pair \$W/sd1/PocketBridge-import/bundle.json --yes | head -c 300; echo ;;
      slow) \$DS proxy --work \$W --rate ${RATE:-600000} ;;
      cutjob) \$DS proxy --work \$W --cut 900000; CLI fetch pocket1 roms 'GBA/Homebrew Demo.gba' --yes | head -c 400; echo; \$DS proxy --work \$W --cut 0 ;;
    esac
  done
  rm -f /out/lib_${SCEN}*.ppm
  cd /out
  if [ \"$SCEN\" = py ]; then
    mkdir -p /lnch/res/fonts/Nunito && cp /fonts/font.ttf /lnch/res/fonts/Nunito/Nunito-Bold.ttf
    L8=DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN,DOWN
    PY() { n=\$1; sc=\$2; UMRK_LAUNCHER_PATH=/lnch SDL_VIDEODRIVER=offscreen /sysroot/ld-linux-aarch64.so.1 --library-path /sysroot:/usr/lib/aarch64-linux-gnu /usr/bin/python3 /pak/engine/ui_sdl.py --screen library --primary \$W/sd1 --secondary \$W/sd2 --userdata \$W/userdata --screenshot /out/lib_py.\$n.ppm --script \"\$sc\" 2>&1 | tail -2; }
    PY 1 \"\$L8,A\"
    PY 2 \"\$L8,A,A,A,A,X,DOWN,X\"
    PY 3 \"\$L8,A,A,A,A,X,DOWN,X,UP,UP,UP,A\"
    PY 4 \"\$L8,A,A,A,A,X,DOWN,X,UP,UP,UP,A,RIGHT,~A,~WAIT,~WAIT,~WAIT\"
    PY 5 \"\$L8,A,A,A,A,X,DOWN,X,UP,UP,UP,A,RIGHT,A\"
    PY 6 \"\$L8,A,A,A,A,A\"
  fi
  LM_SHOTB_MS=${LM_SHOTB_MS:-1500} SDL_VIDEODRIVER=offscreen CAT_FONTS_DIR=/fonts timeout 1500 \
    /sysroot/ld-linux-aarch64.so.1 --library-path /sysroot:/usr/lib/aarch64-linux-gnu /native/build/lm-home \
    --pak /pak --python /usr/bin/python3 --userdata \$W/userdata --primary \$W/sd1 --secondary \$W/sd2 --screenshot /out/lib_$SCEN.ppm --keys $KEYS 2>&1 | tail -4
  if [ -n \"$KEYS2\" ]; then
    echo '--- sau lần chạy 1 (tạm dừng): jobs ---'; CLI jobs | head -c 700; echo; find \$W/sd2/.loongmate-import -type f 2>/dev/null | sort
    LM_SHOTB_MS=${LM_SHOTB_MS:-1500} SDL_VIDEODRIVER=offscreen CAT_FONTS_DIR=/fonts timeout 1500       /sysroot/ld-linux-aarch64.so.1 --library-path /sysroot:/usr/lib/aarch64-linux-gnu /native/build/lm-home       --pak /pak --python /usr/bin/python3 --userdata \$W/userdata --primary \$W/sd1 --secondary \$W/sd2 --screenshot /out/lib_${SCEN}_b.ppm --keys $KEYS2 2>&1 | tail -3
  fi
  echo '--- sau kịch bản: Roms/ của thẻ phụ ---'; find \$W/sd2/Roms \$W/sd1/Roms -type f 2>/dev/null | sort || true
  echo '--- jobs ---'; CLI jobs | head -c 600; echo
  \$DS stop --work \$W --rm
"
for f in "$OUT"/lib_${SCEN}*.ppm; do
  python -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "$f" "${f%.ppm}.png" && rm -f "$f"
  echo "shot: ${f%.ppm}.png"
done
