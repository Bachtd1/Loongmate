#!/usr/bin/env python3
"""PPM (P6) -> PNG, chỉ dùng thư viện chuẩn (zlib + struct). Dùng: ppm2png.py in.ppm [out.png]"""
import struct
import sys
import zlib


def read_ppm(path):
    data = open(path, "rb").read()
    toks, pos = [], 0
    while len(toks) < 4:                      # magic, w, h, maxval
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":         # chú thích
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        toks.append(data[pos:end])
        pos = end
    pos += 1                                  # đúng 1 byte trắng sau maxval
    if toks[0] != b"P6" or toks[3] != b"255":
        raise SystemExit(f"{path}: không phải P6/255")
    w, h = int(toks[1]), int(toks[2])
    pix = data[pos:]
    if len(pix) != w * h * 3:
        raise SystemExit(f"{path}: thiếu dữ liệu ({len(pix)} != {w * h * 3})")
    return w, h, pix


def write_png(path, w, h, pix):
    def chunk(tag, body):
        c = struct.pack(">I", len(body)) + tag + body
        return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + pix[y * w * 3:(y + 1) * w * 3] for y in range(h))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + ".png"
    write_png(dst, *read_ppm(src))
    print(dst)
