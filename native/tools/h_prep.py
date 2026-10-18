#!/usr/bin/env python3
"""Dữ liệu thử đợt H (sắp xếp/lọc ROM, Game lớn nhất, khôi phục save một game) cho sandbox của make_sandbox.py.
Chỉ ghi dưới <gốc sandbox> (tên phải bắt đầu bằng lm-sandbox). Dùng:
  h_prep.py <gốc> <thư mục chứa engine/> <bước,...>
bước: world | md5
  world: thêm ~70 ROM (kích thước khác nhau, rải 5 hệ, hai thẻ), save/state cho vài game, rồi 4 snapshot với save đổi dần
         (Zelda 4 phiên bản, Kirby có save ở snapshot cũ rồi bị xoá khỏi thẻ => chỉ còn trong snapshot).
  md5:   in md5 của MỌI file dưới Saves/ và States/ của hai thẻ (so trước/sau khi khôi phục để chứng minh game khác không đổi)."""
import hashlib, json, os, random, subprocess, sys, time

root, engine_parent, steps = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), sys.argv[3].split(",")
assert os.path.basename(root).startswith("lm-sandbox"), "từ chối: gốc phải tên lm-sandbox*"
sd1, sd2, ud = (os.path.join(root, n) for n in ("sd1", "sd2", "userdata"))
cli = os.path.join(engine_parent, "engine", "cli.py")
K = 1024


def put(path, size, seed):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = random.Random(seed)
    blk = bytes(r.getrandbits(8) for _ in range(min(size, 4096)))
    with open(path, "wb") as f:
        f.write(blk * (size // len(blk) + 1))
        f.truncate(size)


def run(*a):
    r = subprocess.run([sys.executable, cli, "--primary", sd1, "--secondary", sd2, "--userdata", ud, "--quiet", "--json", "--no-rescan", *a],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout


def write(path, data, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


for st in steps:
    if st == "world":
        # ---- ROM: kích thước KHÁC NHAU từng file để thứ tự theo dung lượng tất định
        n = 0
        plan = [("GBA", "gba", 22, 90 * K), ("NES", "nes", 15, 40 * K), ("SNES", "sfc", 12, 150 * K), ("GB", "gb", 12, 30 * K), ("MD", "md", 10, 70 * K)]
        names = ["Zelda Minish", "Kirby Nightmare", "Metroid Fusion", "Mario Kart", "Wario Ware", "Advance Wars", "Fire Emblem", "Golden Sun",
                 "Pokemon Ruby", "Castlevania Aria", "Sonic Advance", "Mega Man Zero", "Final Fantasy", "Dragon Quest", "Tetris Worlds",
                 "Bomberman Max", "Donkey Kong", "Yoshi Island", "Star Fox", "F-Zero", "Contra Hard", "Gradius Neo", "Đại Chiến Rồng", "Ếch Xanh"]
        for si, (sysname, ext, count, base) in enumerate(plan):
            for i in range(count):
                nm = f"{names[(i * 3 + si) % len(names)]} {sysname[0]}{i + 1:02d}"
                size = base + (n * 7919 % 400) * K // 4 + n * 1000        # khác nhau từng file, không theo thứ tự tên
                card = sd1 if (n % 4) else sd2
                put(os.path.join(card, "Roms", sysname, nm + "." + ext), size, "rom%d" % n)
                n += 1
        # game chủ đích cho thử nghiệm (tên cố định)
        put(os.path.join(sd1, "Roms/GBA/Zelda Minish.gba"), 3 * 1024 * K, "z")          # lớn nhất, CÓ save
        put(os.path.join(sd1, "Roms/GBA/Kirby Nightmare.gba"), 2800 * K, "k")           # lớn nhì, CÓ save (save ở thẻ 1)
        put(os.path.join(sd2, "Roms/SNES/Chrono Quest.sfc"), 2600 * K, "c")             # ROM ở thẻ 2, save ở thẻ 2
        put(os.path.join(sd2, "Roms/SNES/Mother Quest.sfc"), 2500 * K, "m")             # ROM ở thẻ 2, save ở thẻ 1 (has_save tính MỌI thẻ)
        put(os.path.join(sd1, "Roms/NES/Mega Block.nes"), 2400 * K, "mb")               # không save
        # tên dài, chỉ khác nhau ở đuôi (thử nhãn bị cắt GIỮA ở màn Xoá ROM)
        for j, nm in enumerate(["Pokemon Mystery Dungeon - Red Rescue Team (USA).gba", "Pokemon Mystery Dungeon - Blue Rescue Team (USA).gba",
                                "Pokemon Mystery Dungeon - Blue Rescue Team (Europe).gba", "Bomberman Max 2 - Bomberman Version (Blue).gba",
                                "Bomberman Max 2 - Bomberman Version (Red).gba", "Đại Chiến Rồng Huyền Thoại Phần Hai - Bản Tiếng Việt (Disc 1).gba",
                                "Đại Chiến Rồng Huyền Thoại Phần Hai - Bản Tiếng Việt (Disc 2).gba"]):
            put(os.path.join(sd1, "Roms/GBA", nm), 2300 * K - j * 5 * K, "long%d" % j)
        for j, nm in enumerate(["Final Fantasy VII - Bản Dịch Tiếng Việt Đầy Đủ Có Dấu (USA) (Disc 1).cue", "Final Fantasy VII - Bản Dịch Tiếng Việt Đầy Đủ Có Dấu (USA) (Disc 2).cue"]):
            put(os.path.join(sd1, "Roms/PS", nm), 2200 * K - j * 5 * K, "ff%d" % j)
        T = 1_700_000_000
        z = os.path.join(sd1, "Saves/mGBA/Zelda Minish.srm")
        zs = os.path.join(sd1, "States/mGBA/Zelda Minish.state1")
        k = os.path.join(sd1, "Saves/mGBA/Kirby Nightmare.srm")
        c = os.path.join(sd2, "Saves/Snes9x/Chrono Quest.srm")
        mo = os.path.join(sd1, "Saves/Snes9x/Mother Quest.srm")
        other = os.path.join(sd1, "Saves/mGBA/Metroid Fusion G03.srm")
        write(z, b"zelda-v1" * 512, T)
        write(k, b"kirby-v1" * 256, T)
        write(c, b"chrono-v1" * 300, T)
        write(mo, b"mother-v1" * 200, T)
        write(other, b"metroid-v1" * 100, T)
        snaps = []

        def snap(label):
            out = json.loads(run("backup"))
            snaps.append(out.get("snapshot_id") or out.get("id") or out)
            time.sleep(1.1)          # id snapshot theo giây
        snap("s1")
        write(z, b"zelda-v2" * 600, T + 100)
        write(zs, b"state-A" * 900, T + 100)
        write(c, b"chrono-v2" * 300, T + 100)
        snap("s2")
        write(z, b"zelda-v3" * 700, T + 200)
        snap("s3")
        os.remove(k)                                    # Kirby chỉ còn trong snapshot
        write(z, b"zelda-v4" * 800, int(time.time()))   # bản đang chơi MỚI HƠN mọi snapshot
        snap("s4")
        write(z, b"zelda-now" * 800, int(time.time()))  # hiện tại khác snapshot cuối (để có "khác bản hiện tại")
        print("world ok: rom =", n + 5, "snapshots =", snaps)
    elif st == "md5":
        for card in (sd1, sd2):
            for sub in ("Saves", "States"):
                base = os.path.join(card, sub)
                for dp, _, fs in os.walk(base):
                    for fn in sorted(fs):
                        p = os.path.join(dp, fn)
                        print(hashlib.md5(open(p, "rb").read()).hexdigest(), os.path.relpath(p, root))
    else:
        sys.exit("bước lạ: " + st)
