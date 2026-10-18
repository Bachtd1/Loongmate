#!/bin/sh
# Chỉ để thử đường LỖI của khôi phục save một game trong container: chuyển mọi lệnh cho python3 thật, trừ
# `saves restore ... --yes` thì trả JSON giả theo chế độ trong /tmp/hfake_mode (fail | unverified). Không ghi gì lên dữ liệu.
mode=$(cat /tmp/hfake_mode 2>/dev/null || echo none)
yes=0; restore=0
for a in "$@"; do
  [ "$a" = "--yes" ] && yes=1
  [ "$a" = "restore" ] && restore=1
done
if [ $yes = 1 ] && [ $restore = 1 ]; then
  case $mode in
    fail) echo '{"error":"RESTORE_FAILED","message":"Không đủ chỗ trống trên thẻ để ghi pre-restore snapshot.","key":"zelda minish"}'; exit 1 ;;
    unverified) echo '{"success":false,"key":"zelda minish","name":"Zelda Minish","snapshot_id":"snap_x","files":[],"restored":[],"failed":["Saves/mGBA/Zelda Minish.srm"],"pre_restore_id":"snap_20261002_999999_pre_restore","preserved_conflicts":[],"conflicts_dir":"","rejected_paths":[],"skipped_readonly":[],"verified":false,"message":"1 file không khớp sau khi ghi (hoặc bị bỏ qua). Có thể quay lại bằng snapshot snap_20261002_999999_pre_restore."}'; exit 1 ;;
  esac
fi
exec python3 "$@"
