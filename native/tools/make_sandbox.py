#!/usr/bin/env python3
"""Dựng dữ liệu thử (sandbox) cho giao diện native: 2 thẻ giả + userdata, có ROM trùng, lỗi thư viện,
dữ liệu telemetry. Chạy TRÊN MÁY (hoặc PC) bằng Python của LoongMate:  make_sandbox.py <thư-mục-gốc>
Chỉ ghi dưới <thư-mục-gốc>; xoá sạch rồi dựng lại mỗi lần chạy."""
import os, shutil, sys, time, random

root = os.path.abspath(sys.argv[1])
assert root.rstrip("/").split("/")[-1].startswith("lm-sandbox"), "từ chối: thư mục gốc phải tên lm-sandbox*"
if os.path.isdir(root):
    for name in ("sd1", "sd2", "userdata"):
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
rng = random.Random(7)


def put(rel, size, seed=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    r = random.Random(seed if seed is not None else rel)
    with open(p, "wb") as f:
        f.write(bytes(r.getrandbits(8) for _ in range(min(size, 4096))) * (size // 4096 + 1))
        f.truncate(size)
    return p


def text(rel, s):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


K, M = 1024, 1024 * 1024
for sd in ("sd1", "sd2"):
    for d in ("Roms", "Saves", "States", "BIOS", "Images"):
        os.makedirs(os.path.join(root, sd, d), exist_ok=True)
# game có save + bản trùng ở thẻ kia (bản có save phải được giữ)
put("sd1/Roms/GBA/Alpha Quest.gba", 1 * M, "alpha")
put("sd2/Roms/GBA/Alpha Quest.gba", 1 * M, "alpha")
put("sd1/Saves/Alpha Quest.srm", 8 * K, "alphasave")
# trùng thường (không save)
put("sd1/Roms/PSX/Shared Racer.chd", 3 * M, "shared")
put("sd2/Roms/PSX/Shared Racer.chd", 3 * M, "shared")
put("sd1/Roms/SNES/Beta Hero.sfc", 2 * M, "beta")
put("sd2/Roms/SNES/Beta Hero.sfc", 2 * M, "beta")
put("sd1/Images/SNES/Beta Hero.png", 12 * K, "betaart")
# game riêng từng thẻ
put("sd1/Roms/GBA/Gamma Land.gba", 1 * M, "gamma")
put("sd1/Roms/NES/Delta Ninja.nes", 512 * K, "delta")
put("sd2/Roms/GB/Epsilon Boy.gb", 256 * K, "eps")
put("sd1/Roms/PSX/Zeta Cue.bin", 2 * M, "zetabin")
text("sd1/Roms/PSX/Zeta Cue.cue", 'FILE "Zeta Cue.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
# .cue trỏ sai tên file (để có sửa .cue)
put("sd1/Roms/PSX/Eta Disc (Track 1).bin", 1 * M, "etabin")
text("sd1/Roms/PSX/Eta Disc.cue", 'FILE "eta disc (track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
# nhiều đĩa không có .m3u
put("sd1/Roms/PSX/Theta Saga (Disc 1).chd", 2 * M, "t1")
put("sd1/Roms/PSX/Theta Saga (Disc 2).chd", 2 * M, "t2")
# thư mục tên cũ (alias PS)
put("sd1/Roms/PS/Iota Legacy.chd", 1 * M, "iota")
# thiếu thư mục chuẩn: không tạo Roms/MD ... (doctor tự báo theo catalog)
# BIOS sai hash
put("sd1/BIOS/scph5501.bin", 512 * K, "bios")
# userdata: telemetry 7 ngày + trạng thái agent (không có khoá => 'chưa từng chạy')
os.makedirs(os.path.join(root, "userdata", "telemetry"), exist_ok=True)
now = time.time()
rows = ["ts,battery_pct,status,soc_c,gpu_c,batt_c,cpu_khz"]
pct = 100.0
t = now - 7 * 86400
while t < now - 600:
    hour = time.localtime(t).tm_hour
    if 8 <= hour < 23:   # ban ngày xả, ban đêm tắt máy (không mẫu)
        pct = max(5.0, pct - rng.uniform(0.2, 0.9))
        soc = 38 + 28 * rng.random() + (12 if rng.random() < 0.04 else 0)
        rows.append(f"{int(t)},{int(pct)},Discharging,{soc:.1f},{soc - 3:.1f},{30 + 5 * rng.random():.1f},1400000")
    else:
        pct = 100.0
    t += 300
ym = time.strftime("%Y-%m", time.localtime(now))
# telemetry.load đọc các file tháng; tách theo tháng của từng mẫu
by_month = {}
for ln in rows[1:]:
    by_month.setdefault(time.strftime("%Y-%m", time.localtime(int(ln.split(",")[0]))), []).append(ln)
for m, lns in by_month.items():
    with open(os.path.join(root, "userdata", "telemetry", m + ".csv"), "w", encoding="utf-8", newline="") as f:
        f.write(rows[0] + "\n" + "\n".join(lns) + "\n")
print("sandbox ok:", root, "mẫu telemetry:", len(rows) - 1)
