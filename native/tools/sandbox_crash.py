#!/usr/bin/env python3
"""Giả lập một phiên khôi phục bị gián đoạn trong SANDBOX (để thử màn Rollback). Chạy trên máy:
   sandbox_crash.py <thư-mục-gốc lm-sandbox*>   — cần ít nhất một snapshot pre_restore (chạy thử khôi phục trước)."""
import json, os, sys, time
root = os.path.abspath(sys.argv[1])
assert os.path.basename(root).startswith("lm-sandbox"), "từ chối: chỉ chạy trong lm-sandbox*"
snaps = os.path.join(root, "userdata", "SaveVault", "snapshots")
pre = sorted(d for d in os.listdir(snaps) if "pre_restore" in d)
assert pre, "chưa có snapshot pre_restore: thực hiện một lần khôi phục trong sandbox trước"
latest = sorted(d for d in os.listdir(snaps) if "pre_restore" not in d)[-1]
j = {"status": "in_progress", "snapshot_id": latest, "pre_restore_id": pre[-1], "started_at": int(time.time()),
     "planned_files": ["Saves/Alpha Quest.srm", "Saves/Other.srm"], "restored_files": ["Saves/Alpha Quest.srm"]}
with open(os.path.join(root, "userdata", "SaveVault", "restore_journal.json"), "w", encoding="utf-8") as f:
    json.dump(j, f, indent=2)
print("đã tạo journal in_progress, pre_restore_id =", pre[-1])
