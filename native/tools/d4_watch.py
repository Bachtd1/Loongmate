#!/usr/bin/env python3
"""Thử "kế hoạch đã duyệt": chờ tới khi native ghi file kế hoạch (bước XEM TRƯỚC xong), rồi đổi sandbox NGAY TRƯỚC khi người
dùng (phím giả) xác nhận. Chạy nền song song với lm-home. Chỉ ghi dưới <gốc sandbox> (tên bắt đầu lm-sandbox).
  d4_watch.py <file kế hoạch> <hành động> <gốc sandbox> [file cổng] [giây chờ tối đa=240]
  Có <file cổng>: tạo nó SAU khi đổi sandbox; lm-home với phím GATE (lm_common.c) dừng ở đó cho tới khi file xuất hiện -> thứ tự tất định.
hành động:
  add_dup        thêm một BẢN TRÙNG MỚI (không có trong kế hoạch): phải còn nguyên sau khi thực hiện
  append_removed ghi thêm byte vào một file đang nằm trong danh sách XOÁ (dedupe): phải bị TỪ CHỐI (sai SHA-256)
  change_cue     sửa nội dung 'Eta Disc.cue' (batchfix): bước sửa .cue đó phải bị TỪ CHỐI
  append_moved   ghi thêm byte vào ROM đầu tiên trong kế hoạch chuyển (move): game đó phải bị TỪ CHỐI (kích thước/giờ sửa đã đổi)
  tamper_plan    sửa nội dung file kế hoạch mà không đổi plan_id: engine phải từ chối CẢ kế hoạch (mã thoát 3), không làm gì
  add_legacy     thêm file mới vào thư mục tên cũ 'Roms/PS' (batchfix gộp thư mục): file mới không được bị gộp"""
import json, os, shutil, sys, time

plan, action, root = sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3])
gate = sys.argv[4] if len(sys.argv) > 4 else None
wait = float(sys.argv[5]) if len(sys.argv) > 5 else 240
assert os.path.basename(root).startswith("lm-sandbox"), "từ chối: gốc phải tên lm-sandbox*"
t0 = time.time()
while not os.path.isfile(plan):
    if time.time() - t0 > wait:
        sys.exit("hết giờ chờ file kế hoạch")
    time.sleep(0.1)
time.sleep(1.5)          # để chắc file đã ghi xong và native đã hiện màn kế hoạch
doc = json.load(open(plan, encoding="utf-8"))
if action == "add_dup":
    src = os.path.join(root, "sd1", "Roms", "GBA", "Gamma Land.gba")
    dst = os.path.join(root, "sd2", "Roms", "GBA", "Gamma Land.gba")
    shutil.copyfile(src, dst)
    print("watch: đã thêm bản trùng MỚI", dst, "(kế hoạch", doc["plan_id"][:12], "có", len(doc["items"]), "mục)")
elif action == "append_removed":
    victim = doc["items"][0]["remove"][0]["path"]
    with open(victim, "ab") as f:
        f.write(b"doi-sau-khi-xem")
    print("watch: đã sửa file trong danh sách XOÁ:", victim)
elif action == "change_cue":
    p = os.path.join(root, "sd1", "Roms", "PSX", "Eta Disc.cue")
    with open(p, "a", encoding="utf-8") as f:
        f.write("REM doi sau khi xem\n")
    print("watch: đã sửa", p)
elif action == "append_moved":
    victim = doc["items"][0]["rom_path"]
    with open(victim, "ab") as f:
        f.write(b"doi-sau-khi-xem")
    print("watch: đã sửa ROM trong kế hoạch chuyển:", victim)
elif action == "tamper_plan":
    doc["items"].append({"kem": "gia"})      # đổi 'items' nhưng giữ nguyên plan_id -> plan_id không khớp
    json.dump(doc, open(plan, "w", encoding="utf-8"))
    print("watch: đã sửa file kế hoạch (giữ nguyên plan_id):", plan)
elif action == "add_legacy":
    p = os.path.join(root, "sd1", "Roms", "PS", "Moi Xuat Hien.chd")
    with open(p, "wb") as f:
        f.write(b"x" * 4096)
    print("watch: đã thêm file mới vào thư mục tên cũ:", p)
else:
    sys.exit("hành động lạ")
if gate:
    open(gate, "w").write("go")
