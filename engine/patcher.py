"""
LoongMate - Patch ROM: áp patch romhack (.ips / .ups / .bps) lên ROM của người dùng bằng cách TẠO FILE MỚI cạnh bản gốc.

Nguyên tắc:
  * "Hard patch": đọc ROM gốc, áp patch trong RAM, ghi ra một file mới (ghi nguyên tử: file tạm + fsync + os.replace) CÙNG thư mục
    <thẻ>/Roms/<hệ>/. Bản gốc KHÔNG BAO GIỜ bị sửa/ghi/đổi tên. Chỉ nhận ROM nằm dưới Roms/ của một thẻ.
  * Giải mã đúng đặc tả công bố (IPS, UPS, BPS), kiểm mọi giới hạn đọc/ghi: patch hỏng -> LibError("invalid", "Patch hỏng: ..."), không
    bao giờ IndexError/MemoryError. Giới hạn: patch <= 64 MiB, ROM nguồn <= 128 MiB, kết quả <= 128 MiB.
  * UPS/BPS có CRC32: kiểm CRC của chính patch, CRC + kích thước ROM nguồn (sai -> "mismatch", không ghi gì), CRC kết quả.
    IPS không có mã kiểm tra: ROM do người dùng chọn tay, kết quả ghi kèm cảnh báo.
  * Kho patch: <app_data>/patches/. Ngoài ra liệt kê (chỉ đọc) file .ips/.bps/.ups nằm trực tiếp trong <thẻ>/Import/.
  * Zip: dùng lại kiểm tra an toàn của importer (collect_zip: đường dẫn tuyệt đối/'..', symlink, zip bomb, mật khẩu, file thực thi...),
    chỉ giải nén các entry có đuôi patch.

Tiện ích dùng chung: homebrew.fat32_name (tên file), libsource._write_json_atomic/_ledger/_fsync_dir, importer (zip, IMPORT_DIR, catalog),
inventory.InventoryEngine (liệt kê ROM từng thẻ).
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import struct
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine import homebrew, libsource
from engine.libsource import LibError

PATCH_EXTS = (".ips", ".bps", ".ups")
STORE_DIR = "patches"
MAX_PATCH_BYTES = 64 * 1024 * 1024
MAX_ROM_BYTES = 128 * 1024 * 1024
MAX_TARGET_BYTES = 128 * 1024 * 1024
MAX_ZIP_PATCHES = 20
SNES_COPIER_HEADER = 512
MAX_CRC_BYTES = 1024 * 1024 * 1024        # tổng số byte băm CRC32 tối đa cho một lần tìm ROM khớp
MAX_NAME_CHARS = 120
META_CHARS = 500
IPS_WARNING = ("IPS không có mã kiểm tra: nếu đây không đúng bản ROM gốc, game có thể lỗi. "
               "Bản gốc không bị sửa — xoá file mới nếu không chạy.")
IPS_EOF = b"EOF"


# ======================================================================================
# giải mã (thuần bytes -> bytes)
# ======================================================================================
def _bad(msg: str) -> LibError:
    return LibError("invalid", "Patch hỏng: " + msg)


class _Reader:
    """Con trỏ đọc có chặn biên: mọi lần đọc vượt `end` đều thành LibError (không IndexError)."""

    def __init__(self, data: bytes, pos: int, end: int):
        self.d, self.pos, self.end = data, pos, end

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > self.end:
            raise _bad("kết thúc đột ngột (thiếu dữ liệu)")
        b = self.d[self.pos:self.pos + n]
        self.pos += n
        return b

    def varint(self) -> int:
        """byuu varint: data=0, shift=1; lặp: x=byte; data += (x&0x7f)*shift; nếu x&0x80 thì dừng; shift <<= 7; data += shift."""
        data, shift = 0, 1
        while True:
            if self.pos >= self.end:
                raise _bad("số nguyên bị cắt cụt")
            x = self.d[self.pos]
            self.pos += 1
            data += (x & 0x7F) * shift
            if x & 0x80:
                return data
            shift <<= 7
            data += shift
            if shift > (1 << 70):
                raise _bad("số nguyên quá lớn")


def _be(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _le32(b: bytes) -> int:
    return struct.unpack("<I", b)[0]


def detect_format(patch: bytes) -> str:
    if patch[:5] == b"PATCH":
        return "ips"
    if patch[:4] == b"UPS1":
        return "ups"
    if patch[:4] == b"BPS1":
        return "bps"
    raise _bad("không phải file IPS/UPS/BPS (sai dấu hiệu đầu file)")


def _check_patch_crc(patch: bytes) -> None:
    if len(patch) < 16:
        raise _bad("quá ngắn")
    if zlib.crc32(patch[:-4]) & 0xFFFFFFFF != _le32(patch[-4:]):
        raise _bad("CRC32 của chính file patch không khớp (file bị lỗi khi tải/chép)")


def _mismatch_source(want_crc: int, want_size: int, src: bytes) -> LibError:
    return LibError("mismatch", "Patch này dành cho bản ROM khác (cần CRC32 %08X, %d byte; ROM đã chọn là %08X, %d byte)."
                    % (want_crc, want_size, zlib.crc32(src) & 0xFFFFFFFF, len(src)))


def _check_source(want_crc: int, want_size: int, src: bytes) -> None:
    if len(src) != want_size or zlib.crc32(src) & 0xFFFFFFFF != want_crc:
        raise _mismatch_source(want_crc, want_size, src)


def _check_target(want_crc: int, out: bytes) -> None:
    if zlib.crc32(out) & 0xFFFFFFFF != want_crc:
        raise LibError("mismatch", "Kết quả sau khi patch không khớp CRC32 dự kiến của patch — không ghi file.")


# ---------------------------------------------------------------- IPS
def _ips_apply(patch: bytes, src: bytes) -> bytes:
    r = _Reader(patch, 5, len(patch))
    out = bytearray(src)
    while True:
        head = r.take(3)
        if head == IPS_EOF:
            break
        off = _be(head)
        size = _be(r.take(2))
        if size == 0:                                        # RLE: 2 byte đếm + 1 byte giá trị
            count = _be(r.take(2))
            data = r.take(1) * count
        else:
            data = r.take(size)
        end = off + len(data)
        if end > MAX_TARGET_BYTES:
            raise _bad("bản ghi ghi vượt giới hạn %d MiB" % (MAX_TARGET_BYTES >> 20))
        if end > len(out):
            out.extend(bytes(end - len(out)))                # khoảng trống giữa chừng điền 0
        out[off:end] = data
    rest = len(patch) - r.pos
    if rest == 3:                                            # độ dài cắt cụt (tuỳ chọn) sau EOF
        n = _be(r.take(3))
        if n < len(out):
            del out[n:]
        elif n > len(out):
            out.extend(bytes(n - len(out)))
    elif rest != 0:
        raise _bad("có %d byte thừa sau EOF" % rest)
    return bytes(out)


# ---------------------------------------------------------------- UPS
def _ups_header(patch: bytes) -> Tuple[int, int, int, int, int]:
    """(kích thước nguồn, kích thước đích, CRC nguồn, CRC đích, vị trí bắt đầu hunk). Đã kiểm CRC của patch."""
    if len(patch) < 18:
        raise _bad("quá ngắn")
    _check_patch_crc(patch)
    r = _Reader(patch, 4, len(patch) - 12)
    in_size, out_size = r.varint(), r.varint()
    if in_size > MAX_ROM_BYTES or out_size > MAX_TARGET_BYTES:
        raise _bad("khai kích thước ROM quá lớn (nguồn %d, đích %d byte)" % (in_size, out_size))
    return in_size, out_size, _le32(patch[-12:-8]), _le32(patch[-8:-4]), r.pos


def _ups_apply(patch: bytes, src: bytes) -> bytes:
    in_size, out_size, in_crc, out_crc, pos = _ups_header(patch)
    _check_source(in_crc, in_size, src)
    out = bytearray(src[:out_size])
    if len(out) < out_size:
        out.extend(bytes(out_size - len(out)))
    end = len(patch) - 12
    r = _Reader(patch, pos, end)
    cur = 0
    while r.pos < end:
        cur += r.varint()
        stop = patch.find(b"\x00", r.pos, end)               # chuỗi XOR kết thúc bằng byte 0x00
        if stop < 0:
            raise _bad("hunk UPS không có byte kết thúc")
        run = patch[r.pos:stop]
        if run:
            if cur + len(run) > out_size:
                raise _bad("hunk UPS ghi vượt kích thước ROM đích")
            seg = int.from_bytes(out[cur:cur + len(run)], "big") ^ int.from_bytes(run, "big")
            out[cur:cur + len(run)] = seg.to_bytes(len(run), "big")
        cur += len(run) + 1                                  # vị trí byte kết thúc cũng tiến 1
        r.pos = stop + 1
    _check_target(out_crc, out)
    return bytes(out)


# ---------------------------------------------------------------- BPS
def _bps_header(patch: bytes) -> Tuple[int, int, bytes, int, int, int]:
    """(kích thước nguồn, kích thước đích, metadata, CRC nguồn, CRC đích, vị trí bắt đầu action). Đã kiểm CRC của patch."""
    if len(patch) < 19:
        raise _bad("quá ngắn")
    _check_patch_crc(patch)
    r = _Reader(patch, 4, len(patch) - 12)
    src_size, tgt_size, meta_size = r.varint(), r.varint(), r.varint()
    if src_size > MAX_ROM_BYTES or tgt_size > MAX_TARGET_BYTES:
        raise _bad("khai kích thước ROM quá lớn (nguồn %d, đích %d byte)" % (src_size, tgt_size))
    meta = r.take(meta_size)
    return src_size, tgt_size, meta, _le32(patch[-12:-8]), _le32(patch[-8:-4]), r.pos


def _bps_apply(patch: bytes, src: bytes) -> bytes:
    src_size, tgt_size, _meta, src_crc, tgt_crc, pos = _bps_header(patch)
    _check_source(src_crc, src_size, src)
    r = _Reader(patch, pos, len(patch) - 12)
    out = bytearray()
    src_rel = tgt_rel = 0
    while len(out) < tgt_size:
        data = r.varint()
        cmd, length = data & 3, (data >> 2) + 1
        if len(out) + length > tgt_size:
            raise _bad("action vượt kích thước ROM đích")
        if cmd == 0:                                         # SourceRead: chép ROM nguồn tại đúng vị trí đang ghi
            at = len(out)
            if at + length > len(src):
                raise _bad("SourceRead vượt ROM nguồn")
            out += src[at:at + length]
        elif cmd == 1:                                       # TargetRead: dữ liệu nằm ngay trong patch
            out += r.take(length)
        elif cmd == 2:                                       # SourceCopy: offset tương đối có dấu
            d = r.varint()
            src_rel += (-1 if d & 1 else 1) * (d >> 1)
            if src_rel < 0 or src_rel + length > len(src):
                raise _bad("SourceCopy ngoài ROM nguồn")
            out += src[src_rel:src_rel + length]
            src_rel += length
        else:                                                # TargetCopy: chép từ chính kết quả, được phép chồng lấn
            d = r.varint()
            tgt_rel += (-1 if d & 1 else 1) * (d >> 1)
            if tgt_rel < 0 or tgt_rel >= len(out):
                raise _bad("TargetCopy ngoài phần đã ghi")
            dist = len(out) - tgt_rel                        # chồng lấn: byte sau đọc lại byte vừa ghi => lặp theo chu kỳ `dist`
            if length <= dist:
                out += out[tgt_rel:tgt_rel + length]
            else:
                pat = bytes(out[tgt_rel:])
                out += (pat * (length // dist + 1))[:length]
            tgt_rel += length
    if r.pos != len(patch) - 12:
        raise _bad("còn dữ liệu thừa sau khi đủ ROM đích")
    _check_target(tgt_crc, out)
    return bytes(out)


# ======================================================================================
# giao diện theo bytes / file
# ======================================================================================
def _crc_hex(v: int) -> str:
    return "%08x" % v


def info_bytes(patch: bytes) -> Dict[str, Any]:
    """Kiểm cấu trúc patch (và CRC của patch với UPS/BPS) rồi mô tả. Sai -> LibError("invalid")."""
    fmt = detect_format(patch)
    out: Dict[str, Any] = {"format": fmt, "size": len(patch), "source_crc32": None, "source_size": None, "target_size": None,
                           "metadata": ""}
    if fmt == "ips":
        _ips_apply(patch, b"")                               # chỉ để kiểm cấu trúc (bộ nhớ tối đa ~16 MiB)
    elif fmt == "ups":
        in_size, out_size, in_crc, _oc, _pos = _ups_header(patch)
        out.update(source_crc32=_crc_hex(in_crc), source_size=in_size, target_size=out_size)
    else:
        src_size, tgt_size, meta, src_crc, _tc, _pos = _bps_header(patch)
        out.update(source_crc32=_crc_hex(src_crc), source_size=src_size, target_size=tgt_size,
                   metadata=meta.decode("utf-8", errors="replace")[:META_CHARS])
    return out


def apply_bytes(patch: bytes, source: bytes) -> Tuple[bytes, bool]:
    """(ROM đích, đã kiểm CRC kết quả). IPS: verified=False. Mọi lỗi -> LibError."""
    if len(patch) > MAX_PATCH_BYTES:
        raise LibError("invalid", "Patch quá lớn (> %d MiB)." % (MAX_PATCH_BYTES >> 20))
    if len(source) > MAX_ROM_BYTES:
        raise LibError("too_large", "ROM nguồn quá lớn (> %d MiB)." % (MAX_ROM_BYTES >> 20))
    fmt = detect_format(patch)
    if fmt == "ips":
        out = _ips_apply(patch, source)
    elif fmt == "ups":
        out = _ups_apply(patch, source)
    else:
        out = _bps_apply(patch, source)
    if len(out) > MAX_TARGET_BYTES:
        raise LibError("too_large", "ROM sau khi patch lớn hơn %d MiB." % (MAX_TARGET_BYTES >> 20))
    return out, fmt != "ips"


def _read_patch(path: Path) -> bytes:
    try:
        if path.stat().st_size > MAX_PATCH_BYTES:
            raise LibError("invalid", "Patch quá lớn (> %d MiB)." % (MAX_PATCH_BYTES >> 20))
        return path.read_bytes()
    except OSError as e:
        raise LibError("not_found", "Không đọc được file patch: %s" % e)


def info(path) -> Dict[str, Any]:
    return info_bytes(_read_patch(Path(path)))


# ======================================================================================
# kho patch + liệt kê
# ======================================================================================
def store_dir(app_data) -> Path:
    return Path(app_data) / STORE_DIR


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _cards(env) -> List[Tuple[str, Path]]:
    return [(cid, Path(env.cards[cid])) for cid in ("primary", "secondary") if env.cards.get(cid)]


def _list_dir(d: Path) -> List[Path]:
    try:
        names = sorted(os.listdir(d), key=str.lower)
    except OSError:
        return []
    out = []
    for n in names:
        p = d / n
        if _ext(n) in PATCH_EXTS and not n.startswith(".") and p.is_file():
            out.append(p)
    return out


def _entry(pid: str, p: Path, where: str, card: Optional[str] = None) -> Dict[str, Any]:
    e: Dict[str, Any] = {"id": pid, "name": p.name, "path": str(p), "format": _ext(p.name)[1:], "size": 0, "where": where}
    if card:
        e["card"] = card
    try:
        e["size"] = p.stat().st_size
    except OSError:
        pass
    try:
        i = info(p)
        e.update(format=i["format"], size=i["size"], verifies=i["format"] in ("ups", "bps"), source_crc32=i["source_crc32"],
                 source_size=i["source_size"], metadata=i["metadata"])
    except LibError as ex:
        e.update(verifies=e["format"] in ("ups", "bps"), source_crc32=None, source_size=None, metadata="", error=ex.message)
    return e


def list_patches(env) -> List[Dict[str, Any]]:
    out = [_entry("s:" + p.name, p, "store") for p in _list_dir(store_dir(env.app_data))]
    imp = libsource._importer()
    for cid, root in _cards(env):
        out += [_entry("i:%s:%s" % (cid, p.name), p, "import", cid) for p in _list_dir(root / imp.IMPORT_DIR)]
    return out


def _find(env, patch_id: str) -> Dict[str, Any]:
    for e in list_patches(env):
        if e["id"] == patch_id:
            return e
    raise LibError("not_found", "Không có patch '%s'." % patch_id)


def get_info(env, patch_id: str) -> Dict[str, Any]:
    e = _find(env, patch_id)
    e["success"] = True
    return e


def _usable(e: Dict[str, Any]) -> Dict[str, Any]:
    if e.get("error"):
        raise LibError("invalid", e["error"])
    return e


# ---------------------------------------------------------------- lưu vào kho
def _write_atomic(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(".%s.%s.partial" % (dest.name, uuid.uuid4().hex[:8]))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
        libsource._fsync_dir(dest.parent)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise LibError("storage", "Không ghi được file: %s" % e)


def _lower_names(d: Path) -> Dict[str, str]:
    try:
        return {n.lower(): n for n in os.listdir(d)}
    except OSError:
        return {}


def _put_in_store(app_data, name: str, data: bytes) -> str:
    """Ghi vào kho dưới tên FAT32 an toàn. Đã có file GIỐNG HỆT (cùng tên hoặc " (n)") -> dùng lại; khác nội dung -> thêm " (2)"..."""
    d = store_dir(app_data)
    stem, ext = os.path.splitext(name)
    stem = homebrew.fat32_name(stem, 100)
    existing = _lower_names(d)
    for n in range(1, 1000):
        cand = (stem if n == 1 else "%s (%d)" % (stem, n)) + ext.lower()
        have = existing.get(cand.lower())
        if have is None:
            _write_atomic(d / cand, data)
            return cand
        try:
            if (d / have).read_bytes() == data:
                return have
        except OSError:
            pass
    raise LibError("storage", "Quá nhiều patch trùng tên trong kho.")


def store_patch_files(app_data, path) -> List[str]:
    """Chép một file patch (hoặc các patch trong một .zip) từ BẤT KỲ đâu vào kho. Trả danh sách tên đã lưu."""
    p = Path(path)
    ext = _ext(p.name)
    if ext in PATCH_EXTS:
        data = _read_patch(p)
        info_bytes(data)                                     # không lưu file không phải patch / patch hỏng
        return [_put_in_store(app_data, p.name, data)]
    if ext != ".zip":
        raise LibError("invalid", "Chỉ lưu được file .ips/.bps/.ups hoặc .zip chứa chúng.")
    imp = libsource._importer()
    # Dùng lại kiểm tra zip của importer; giới hạn file = giới hạn patch.
    src = imp.collect_zip(p, None, False, dataclasses.replace(imp.DEFAULT_LIMITS, max_file_bytes=MAX_PATCH_BYTES))
    try:
        if src.problems:
            raise LibError("invalid", "Zip bị chặn vì không an toàn: " + "; ".join(src.problems))
        members = [m for m in src.members if _ext(m.name) in PATCH_EXTS]
        if not members:
            raise LibError("invalid", "Trong file zip không có patch (.ips/.bps/.ups).")
        if len(members) > MAX_ZIP_PATCHES:
            raise LibError("invalid", "Zip chứa quá nhiều patch (%d > %d)." % (len(members), MAX_ZIP_PATCHES))
        loaded = []
        for m in members:
            with m.open() as f:
                data = f.read(MAX_PATCH_BYTES + 1)
            if len(data) > MAX_PATCH_BYTES:
                raise LibError("invalid", "Patch '%s' trong zip quá lớn." % m.name)
            try:
                info_bytes(data)
            except LibError as e:
                raise LibError("invalid", "'%s' trong zip: %s" % (m.name, e.message))
            loaded.append((m.name, data))
        return [_put_in_store(app_data, n, d) for n, d in loaded]
    finally:
        src.close()


def delete_patch(env, patch_id: str) -> Dict[str, Any]:
    e = _find(env, patch_id)
    if e["where"] != "store":
        raise LibError("invalid", "patch nằm trong Import/: xoá bằng tay hoặc dùng Nhập game")
    try:
        Path(e["path"]).unlink()
    except OSError as ex:
        raise LibError("storage", "Không xoá được patch: %s" % ex)
    libsource._ledger(env.app_data, "patch_deleted", patch=patch_id)
    return {"success": True, "id": patch_id, "message": "Đã xoá patch %s." % e["name"]}


# ======================================================================================
# liệt kê ROM + tìm ROM khớp
# ======================================================================================
def _list_roms(env, system: Optional[str] = None) -> List[Dict[str, Any]]:
    """ROM trong <thẻ>/Roms/<hệ>/ (đệ quy) của cả hai thẻ — dùng đúng InventoryEngine như lệnh `roms`. Bỏ Imgs/ và file không phải ROM."""
    from engine.inventory import InventoryEngine
    imp = libsource._importer()
    skip = imp.META_EXTS | imp.SAVE_EXTS
    want = system.lower() if system else None
    out = []
    for cid, root in _cards(env):
        try:
            roms = InventoryEngine(root).scan_card(root, cid)["roms"]
        except OSError:
            continue
        for r in roms:
            if Path(r["absolute_path"]).parent.name.lower() == "imgs" or r["extension"] in skip:
                continue
            if want and r["system"].lower() != want:
                continue
            out.append({"path": r["absolute_path"], "card": cid, "system": r["system"], "name": r["name"], "size": r["size"]})
    out.sort(key=lambda x: (x["name"].lower(), x["card"], x["system"].lower()))
    return out


def _crc_file(path: str, skip: int = 0) -> int:
    crc = 0
    with open(path, "rb") as f:
        if skip:
            f.seek(skip)
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def candidates(env, patch_id: str, system: Optional[str] = None) -> Dict[str, Any]:
    e = _usable(_find(env, patch_id))
    if not e["verifies"]:                                    # IPS: không có gì để kiểm -> người dùng chọn tay
        roms = _list_roms(env, system)
        if system is None:
            counts: Dict[str, int] = {}
            for r in _list_roms(env):
                counts[r["system"]] = counts.get(r["system"], 0) + 1
            return {"success": True, "verify": "none",
                    "systems": [{"system": s, "count": counts[s]} for s in sorted(counts, key=str.lower)]}
        return {"success": True, "verify": "none", "roms": roms}
    size, crc = e["source_size"], int(e["source_crc32"], 16)
    matches, scanned, budget, truncated = [], 0, MAX_CRC_BYTES, False
    for r in _list_roms(env, system):
        if r["size"] == size:
            skip = 0
        elif r["size"] == size + SNES_COPIER_HEADER:         # ROM SNES có header copier 512 byte: bỏ qua khi tính CRC
            skip = SNES_COPIER_HEADER
        else:
            continue
        if budget < r["size"]:
            truncated = True
            break
        budget -= r["size"]
        scanned += 1
        try:
            ok = _crc_file(r["path"], skip) == crc
        except OSError:
            continue
        if ok:
            matches.append(dict(r, header_skipped=bool(skip)))
    return {"success": True, "verify": "crc", "matches": matches, "source_crc32": e["source_crc32"], "source_size": size,
            "scanned": scanned, "truncated": truncated}


# ======================================================================================
# áp patch
# ======================================================================================
def _rom_under_roms(env, rom_path) -> Path:
    """Chỉ nhận file thường nằm dưới <thẻ>/Roms/ (đã resolve: symlink/'..' thoát ra ngoài đều bị từ chối)."""
    try:
        rp = Path(rom_path).resolve()
    except (OSError, RuntimeError):
        raise LibError("invalid", "Đường dẫn ROM không hợp lệ.")
    for _cid, root in _cards(env):
        try:
            base = (root / "Roms").resolve()
        except (OSError, RuntimeError):
            continue
        if rp != base and rp.is_relative_to(base) and rp.is_file():
            return rp
    raise LibError("invalid", "ROM phải là một file nằm trong thư mục Roms/ của thẻ nhớ.")


def _output_name(rom: Path, patch_name: str, out_name: Optional[str]) -> str:
    rom_stem, rom_ext = os.path.splitext(rom.name)
    ext = rom_ext
    if out_name:
        base = out_name.replace("\\", "/").rsplit("/", 1)[-1]
        stem, e2 = os.path.splitext(base)
        if e2:
            ext = "." + re.sub(r"[^A-Za-z0-9]", "", e2)[:10]
        if not stem.strip(". "):
            raise LibError("invalid", "Tên file mới không hợp lệ.")
        avail = MAX_NAME_CHARS - len(ext) - 6                # chừa chỗ cho " (99)"
        return homebrew.fat32_name(stem, avail) + ext
    avail = MAX_NAME_CHARS - len(ext) - 6
    tag = " [%s]" % os.path.splitext(patch_name)[0][:40]
    return homebrew.fat32_name(rom_stem[:max(1, avail - len(tag))] + tag, avail) + ext


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return path.stat().st_size == len(data) and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()
    except OSError:
        return False


def apply(env, patch_id: str, rom_path, out_name: Optional[str] = None, header_skipped: bool = False) -> Dict[str, Any]:
    e = _usable(_find(env, patch_id))
    rom = _rom_under_roms(env, rom_path)
    patch = _read_patch(Path(e["path"]))
    try:
        size = rom.stat().st_size
        if size > MAX_ROM_BYTES:
            raise LibError("too_large", "ROM lớn hơn %d MiB — không áp patch được trên máy." % (MAX_ROM_BYTES >> 20))
        data = rom.read_bytes()
    except OSError as ex:
        raise LibError("not_found", "Không đọc được ROM: %s" % ex)
    header = b""
    if header_skipped:
        if len(data) < SNES_COPIER_HEADER:
            raise LibError("invalid", "ROM nhỏ hơn 512 byte, không có header để bỏ qua.")
        header, data = data[:SNES_COPIER_HEADER], data[SNES_COPIER_HEADER:]
    target, verified = apply_bytes(patch, data)
    result = header + target
    if len(result) > MAX_TARGET_BYTES:
        raise LibError("too_large", "ROM sau khi patch lớn hơn %d MiB." % (MAX_TARGET_BYTES >> 20))

    name = _output_name(rom, e["name"], out_name)
    stem, ext = os.path.splitext(name)
    folder = rom.parent
    existing = _lower_names(folder)
    already, dest = False, None
    for n in range(1, 1000):
        cand = name if n == 1 else "%s (%d)" % (stem, n) + ext
        have = existing.get(cand.lower())
        if have is None:
            dest = folder / cand
            break
        if _same_bytes(folder / have, result):               # lần trước đã tạo đúng file này
            dest, already = folder / have, True
            break
    if dest is None:
        raise LibError("storage", "Quá nhiều file trùng tên trong thư mục ROM.")
    # Chốt chặn cuối: tuyệt đối không ghi đè ROM gốc (đường dẫn trùng tên không phân biệt hoa/thường trên FAT32 cũng bị chặn).
    if not already and dest.name.lower() == rom.name.lower():
        raise LibError("invalid", "Tên file mới trùng với ROM gốc — từ chối để không ghi đè bản gốc.")
    if not already:
        _write_atomic(dest, result)
    sha = hashlib.sha256(result).hexdigest()
    libsource._ledger(env.app_data, "patch_applied", patch=patch_id, rom=str(rom), output=str(dest), sha256=sha, verified=verified,
                      already=already, header_skipped=header_skipped)
    return {"success": True, "output": str(dest), "name": dest.name, "size": len(result), "sha256": sha, "verified": verified,
            "already": already, "changed": not already, "warning": None if verified else IPS_WARNING,
            "message": ("File đã có sẵn: %s" if already else "Đã tạo %s cạnh bản gốc.") % dest.name}


# ======================================================================================
# Tải từ web: định tuyến patch ra khỏi đường cài ROM
# ======================================================================================
def _zip_is_patch_only(path: Path) -> bool:
    """Zip có >= 1 entry patch và KHÔNG có entry nào importer coi là ROM/đĩa (đuôi trong systems.json) -> đây là gói patch."""
    rom_exts = set(libsource._importer().load_catalog()["ext"])
    try:
        with zipfile.ZipFile(path) as zf:
            exts = [_ext(i.filename) for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError, NotImplementedError):
        return False                                         # zip hỏng: để importer báo lỗi như thường
    return any(x in PATCH_EXTS for x in exts) and not any(x in rom_exts for x in exts)


def route_web_job(env, job_dir: Path, job: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, str]]]:
    """Với job nguồn "web": file .ips/.bps/.ups, hoặc .zip chỉ chứa patch -> lưu vào kho và BỎ khỏi job["files"] (importer không thấy).
    Trả (tên đã lưu, [{name, message}] lỗi). Chạy lại được (lưu giống hệt thì dùng lại)."""
    stored: List[str] = []
    failed: List[Dict[str, str]] = []
    keep = []
    for f in job["files"]:
        p = job_dir / "files" / f["local"]
        ext = _ext(f["local"])
        if ext in PATCH_EXTS or (ext == ".zip" and _zip_is_patch_only(p)):
            try:
                stored += store_patch_files(env.app_data, p)
            except LibError as ex:
                failed.append({"name": f["name"], "message": ex.message})
        else:
            keep.append(f)
    job["files"] = keep
    if stored:
        libsource._ledger(env.app_data, "patch_stored", job=job["job_id"], names=stored)
    return stored, failed
