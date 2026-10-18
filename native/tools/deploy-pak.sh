#!/bin/sh
# Đẩy binary native + launch.sh vào LoongMate.pak trên máy (pak duy nhất; bin/lm-home nằm trong đó): tar qua ssh
# (KHÔNG kèm "< /dev/null": từng làm file thành 0 byte), so md5, đếm byte CR (phải 0) và `sh -n`.
# Để cài cả engine + gói đầy đủ có sao lưu/rollback thì dùng pc/deploy.py; script này chỉ để thử nhanh binary mới.
set -eu
HERE=$(cd "$(dirname "$0")/../.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
PAK=LoongMate.pak
SRC=$HERE/pak/Apps/mlp1/$PAK
DEST=${LM_APPS_DIR:-/mnt/sdcard/Apps/mlp1}
[ -x "$HERE/native/build/lm-home" ] || { echo "chưa build native/build/lm-home" >&2; exit 1; }
cp "$HERE/native/build/lm-home" "$SRC/bin/lm-home"
tar -cf - -C "$HERE/pak/Apps/mlp1" $PAK/bin/lm-home $PAK/launch.sh | $SSH "tar -xf - -C $DEST && chmod +x $DEST/$PAK/bin/lm-home $DEST/$PAK/launch.sh"
fail=0
for f in bin/lm-home launch.sh; do
  l=$(md5sum "$SRC/$f" | cut -d' ' -f1)
  r=$($SSH "md5sum $DEST/$PAK/$f" | cut -d' ' -f1)
  if [ "$l" = "$r" ]; then echo "md5 khớp $l  $f"; else echo "md5 LỆCH $f local=$l máy=$r"; fail=1; fi
done
$SSH "cd $DEST/$PAK && echo CR_launch=\$(tr -cd '\r' < launch.sh | wc -c) && sh -n launch.sh && echo sh-n-ok && ls -l bin/"
exit $fail
