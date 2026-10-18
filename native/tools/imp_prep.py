#!/usr/bin/env python3
"""Dựng sandbox cho thử Local Importer (Nhập game): hai thẻ giả + userdata, mỗi thẻ có Import/ với nhiều tình huống.
Chạy TRÊN MÁY bằng Python của LoongMate:  imp_prep.py <thư-mục-gốc> <thư mục chứa engine/>
Chỉ ghi dưới <thư-mục-gốc> (tên phải bắt đầu bằng lm-sandbox); xoá sạch rồi dựng lại. Toàn bộ nội dung là byte tự tạo
(homebrew giả theo header trong tài liệu / dữ liệu ngẫu nhiên) — KHÔNG có ROM thương mại.

  sd2/Import: Homebrew Big.zip (GBA ~32 MB ngẫu nhiên, đo thời gian giải nén+băm), Mini Pack.zip (NES+GB+SNES nhỏ, một file TRÙNG
              tên khác nội dung với thư viện, một file giống hệt thư viện), evil.zip (path traversal), Disc Game.zip (cue+bin kiểu PSX)
  sd1/Import: Mystery.bin (mơ hồ), bomb.zip (zip bomb 40 MB số 0), Old.7z (chưa hỗ trợ), Loose.nes (file lẻ), run.zip (có .sh)
"""
import hashlib
import os
import random
import shutil
import struct
import sys
import zipfile

root = os.path.abspath(sys.argv[1])
assert os.path.basename(root).startswith("lm-sandbox"), "từ chối: thư mục gốc phải tên lm-sandbox*"
sys.path.insert(0, os.path.abspath(sys.argv[2]))
from engine import importer as IM   # noqa: E402

for name in ("sd1", "sd2", "userdata"):
    shutil.rmtree(os.path.join(root, name), ignore_errors=True)
for sd in ("sd1", "sd2"):
    for d in ("Roms", "Saves", "States", "BIOS", "Images", "Import"):
        os.makedirs(os.path.join(root, sd, d), exist_ok=True)
os.makedirs(os.path.join(root, "userdata"), exist_ok=True)
M = 1024 * 1024
rng = random.Random(11)


def gba(size, seed):
    d = bytearray(random.Random(seed).randbytes(size))
    d[0:4] = b"\x2e\x00\x00\xea"
    d[4:0xA0] = IM.GBA_LOGO
    d[0xA0:0xAC] = b"HOMEBREWTEST"
    d[0xB2] = 0x96
    d[0xBD] = (-(0x19 + sum(d[0xA0:0xBD]))) & 0xFF
    return bytes(d)


def nes(seed):
    return b"NES\x1a" + bytes([2, 1, 0, 0]) + bytes(8) + bytes([seed]) * 256


def gb(seed):
    d = bytearray(0x8000)
    d[0x104:0x134] = IM.GB_LOGO
    d[0x134:0x13A] = b"HBTEST"
    c = 0
    for b in d[0x134:0x14D]:
        c = (c - b - 1) & 0xFF
    d[0x14D] = c
    d[0x200] = seed
    return bytes(d)


def snes(seed):
    d = bytearray(0x10000)
    b = 0x7FC0
    d[b:b + 21] = b"HOMEBREW SNES TEST   "
    d[b + 0x15] = 0x20
    d[b + 0x1C:b + 0x1E] = struct.pack("<H", 0x1234 ^ 0xFFFF)
    d[b + 0x1E:b + 0x20] = struct.pack("<H", 0x1234)
    d[0x100] = seed
    return bytes(d)


def raw_sector(user, mode=1):
    s = bytearray(2352)
    s[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
    s[15] = mode
    s[16:16 + len(user)] = user
    return bytes(s)


def psx(n_sectors=1200):
    secs = [raw_sector(b"") for _ in range(16)]
    secs.append(raw_sector(b"\x01CD001\x01\x00" + b"PLAYSTATION".ljust(32)))
    body = bytearray(b"".join(secs))
    body[0x24F8:0x24F8 + 14] = b"Sony Computer "
    return bytes(body) + rng.randbytes(2352 * n_sectors)


def zipit(path, entries, method=zipfile.ZIP_DEFLATED, attrs=None):
    with zipfile.ZipFile(path, "w", method) as z:
        for n, data in entries.items():
            zi = zipfile.ZipInfo(n, (2025, 1, 1, 0, 0, 0))
            zi.compress_type = method
            if attrs and n in attrs:
                zi.create_system = 3
                zi.external_attr = attrs[n] << 16
            z.writestr(zi, data)


def put(rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


i2, i1 = os.path.join(root, "sd2", "Import"), os.path.join(root, "sd1", "Import")
zipit(os.path.join(i2, "Homebrew Big.zip"), {"Homebrew Big.gba": gba(32 * M, 1), "README.txt": b"homebrew test\n"})
zipit(os.path.join(i2, "Mini Pack.zip"), {"Conflict.nes": nes(5), "Same.gb": gb(2), "Tiny.sfc": snes(3), "notes.txt": b"x"})
zipit(os.path.join(i2, "evil.zip"), {"../../../escaped.gba": gba(4096, 9), "ok.gba": gba(4096, 8)})
cue = b'FILE "Disc Game.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
zipit(os.path.join(i2, "Disc Game.zip"), {"Disc Game.cue": cue, "Disc Game.bin": psx()})
put("sd1/Import/Mystery.bin", b"\x00" * 4096)
zipit(os.path.join(i1, "bomb.zip"), {"big.gba": b"\x00" * (40 * M)})
put("sd1/Import/Old.7z", b"7z\xbc\xaf\x27\x1c" + b"0" * 64)
put("sd1/Import/Loose.nes", nes(7))
zipit(os.path.join(i1, "run.zip"), {"install.sh": b"#!/bin/sh\necho pwned\n", "g.gba": gba(4096, 4)})
# thư viện sẵn có: một file cùng tên khác nội dung, một file giống hệt, save/state/BIOS cần nguyên vẹn
put("sd2/Roms/NES/Conflict.nes", nes(99))
put("sd1/Roms/GB/Same.gb", gb(2))
put("sd2/Saves/Conflict.srm", b"MY PRECIOUS SAVE")
put("sd2/States/Conflict.state", b"MY STATE")
put("sd2/BIOS/keep.bin", b"MY BIOS")
put("sd1/Roms/GBA/Existing Game.gba", gba(65536, 77))
# thẻ Leaf GIẢ cho thử pc/import_push.py (--sdcard <sd1>): chỉ cần env.sh điền USERDATA_PATH như env.sh thật
put("sd1/.system/leaf/platforms/mlp1/launcher/env.sh",
    b'[ -n "$USERDATA_PATH" ] || USERDATA_PATH="$SDCARD_PATH/.userdata/mlp1"\nexport USERDATA_PATH\n')
print("sandbox OK:", root)
