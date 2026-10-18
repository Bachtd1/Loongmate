"""
Local Importer cho LoongMate (Library Sources, giai đoạn A) — nhập game do NGƯỜI DÙNG TỰ ĐƯA VÀO máy.

PHẠM VI PHÁP LÝ: chỉ nhập file có sẵn trên máy/thẻ (bản dump của chính họ, homebrew). Module này KHÔNG có
bất kỳ chức năng tải/cào website nào. Không gọi binary ngoài (7z/unrar): .7z/.rar báo "chưa hỗ trợ trên máy".

Nguồn nhập
  * <thẻ>/Import/ ở gốc MỖI thẻ (file lẻ, .zip, thư mục con) và đường dẫn tuỳ ý (CLI: import scan PATH...).
  * Thư mục Import/ : file lẻ trong đó gộp thành MỘT nhóm (để .cue gặp .bin của nó); mỗi .zip / thư mục con là
    một nguồn riêng. Một đường dẫn thư mục tuỳ ý là MỘT nguồn dạng thư mục (zip bên trong bị chặn: archive lồng nhau).
  * Một nguồn có thể chứa nhiều game: mỗi "unit" (game) được phân loại và cài riêng.

API (mọi hàm trả dict thuần JSON)
  scan(paths, cards, ...)            CHỈ ĐỌC: danh sách unit + phân loại + cảnh báo. Không ghi gì.
  build_plan(paths, cards, app_data, card_pref, select, ...)   CHỈ ĐỌC: scan + chọn thẻ + kiểm trùng + đích dự kiến.
  apply_plan(plan, cards, app_data, choices, conflicts, bios_ok, delete_source)   GHI (staging -> commit).
  list_jobs(cards, app_data) / recover(cards, app_data)
  ensure_import_dir(card_root)       tạo <thẻ>/Import/ (việc duy nhất ngoài apply có ghi).

GIỚI HẠN MẶC ĐỊNH (Limits; ghi rõ vì chọn "hợp lý cho máy cầm tay", không phải chuẩn nào)
  tối đa 1000 entry/archive; độ sâu thư mục 8; 2 GiB mỗi file (FAT32 trần 4 GiB-1); 4 GiB tổng giải nén;
  tỉ lệ nén (kích thước giải nén / kích thước nén) <= 150 cho entry >= 4 MiB và cho cả archive; dự phòng
  trống 128 MiB trên thẻ đích. Đếm byte THỰC khi giải nén, không tin kích thước khai báo trong header zip.

NGUỒN HEADER (đối chiếu tài liệu kỹ thuật; không có ROM thương mại để thử, test dùng byte header tự dựng theo tài liệu)
  GB/GBC : Pan Docs "The Cartridge Header" https://gbdev.io/pandocs/The_Cartridge_Header.html — logo 48 byte @0x104,
           cờ CGB @0x143 (0x80 = CGB + tương thích GB, 0xC0 = chỉ CGB), checksum @0x14D (x = x-b-1 trên 0x134..0x14C).
  GBA    : devkitPro gba-tools gbafix.c (https://github.com/devkitPro/gba-tools) — logo 156 byte @0x04, hằng 0x96 @0xB2,
           complement @0xBD = -(0x19 + tổng byte 0xA0..0xBC).
  NDS    : devkitPro ndstool (crc.h/crc.cpp/header.cpp) — logo (cùng logo GBA) @0xC0, CRC16 logo @0x15C (CRC16 bảng 0xA001,
           khởi tạo 0xFFFF, không đảo cuối), CRC16 header @0x15E trên 0x000..0x15D.
  NES/FDS: nesdev iNES ("NES\\x1A") và định dạng .fds ("FDS\\x1A"; đĩa thô: 0x01 + "*NINTENDO-HVC*").
  SNES   : SNES header (nesdev/snes.nesdev "ROM header"): tại 0x7FC0 (LoROM) / 0xFFC0 (HiROM), +0x200 nếu có header copier;
           complement @+0x1C và checksum @+0x1E cộng lại = 0xFFFF.
  N64    : thứ tự byte của 4 byte đầu: .z64 80 37 12 40, .v64 37 80 40 12, .n64 40 12 37 80 (n64brew "ROM Header").
  Genesis/32X: plutiedev.com/rom-header — "SEGA" @0x100; "SEGA 32X" là 32X (khác folder 32X vs MD32X -> hỏi người dùng).
  SMS/GG : Genesis Plus GX core/loadrom.c + smspower — "TMR SEGA" @0x1FF0/0x3FF0/0x7FF0, nibble cao byte +0x0F:
           3,4 = SMS; 5,6,7 = Game Gear.
  Đĩa    : RetroArch tasks/task_database_cue.c (MAGIC_NUMBERS) + ECMA-119 PVD: "SEGADISCSYSTEM" (Mega-CD) và
           "SEGA SEGASATURN" @0x10 (sector thô 2352) hoặc @0x00 (2048); "SEGA SEGAKATANA" (Dreamcast);
           "PLAYSTATION" trong System Identifier PVD (sector 16 +8 = 0x9320 thô / 0x8008 2048), "Sony Computer " @0x24F8
           (giấy phép PS1 CD); "PSP GAME" @0x8008.
  Không có header đáng tin (PCE/Lynx/Atari...) -> dựa phần mở rộng duy nhất trong systems.json, không thì hỏi.

# CHƯA KIỂM CHỨNG: (1) Leaf có tìm BIOS ở BIOS/ của thẻ phụ không (BIOS được đặt cùng thẻ đích với ROM);
# (2) Leaf có quét đệ quy thư mục con trong Roms/<hệ>/ (game nhiều file chỉ giữ layout tương đối so với gốc bộ);
# (3) chuỗi console "SEGA 32X" @0x100 chỉ dựa tài liệu plutiedev.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import time
import uuid
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.artwork import _fsync_dir, canon_system, system_dirs
from engine.doctor import CUE_FILE_LINE_RE, decode_playlist_bytes
from engine.inventory import compute_sha256, find_systems_catalog, is_safe_relative_path

MB = 1024 * 1024
GIB = 1024 * MB
HEAD_BYTES = 0x10400                 # đủ cho SNES HiROM (+copier) và PVD của sector thô 2352
IMPORT_DIR = "Import"
WORK_DIR = ".loongmate-import"       # <thẻ>/.loongmate-import/{staging,trash,installed.jsonl}
DONE_DIR = ".done"
LEDGER_NAME = "import-ledger.jsonl"
JOB_SCHEMA = 1


@dataclass(frozen=True)
class Limits:
    max_entries: int = 1000
    max_depth: int = 8
    max_file_bytes: int = 2 * GIB
    max_total_bytes: int = 4 * GIB
    max_ratio: int = 150
    ratio_min_bytes: int = 4 * MB
    reserve_bytes: int = 128 * MB
    max_bios_bytes: int = 16 * MB


DEFAULT_LIMITS = Limits()

EXEC_EXTS = {".sh", ".exe", ".bat", ".cmd", ".com", ".msi", ".apk", ".elf", ".so", ".dll", ".dylib", ".py",
             ".pyc", ".js", ".jar", ".ps1", ".vbs", ".scr", ".pl", ".rb", ".php"}
ARCHIVE_EXTS = {".zip", ".7z", ".rar", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".zst", ".cab", ".lzh", ".arj"}
UNSUPPORTED_ARCHIVE_EXTS = ARCHIVE_EXTS - {".zip"}
META_EXTS = {".txt", ".nfo", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".xml", ".diz",
             ".url", ".html", ".htm", ".json", ".ini", ".cfg", ".sfv", ".md5", ".sha1", ".db", ".lnk"}
SAVE_EXTS = {".srm", ".sav", ".state", ".sta", ".eep", ".mpk", ".fla", ".rtc", ".nv", ".ss0", ".ss1", ".dsv"}
IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
PLAYLIST_EXTS = {".cue", ".m3u", ".gdi"}
GENERIC_EXTS = {".bin", ".iso", ".img", ".rom", ""}      # đuôi chung: không cảnh báo khi header nói hệ khác
CONFLICT_ACTIONS = ("keep", "replace", "copy", "cancel")
MSG_NO_7Z = "chưa hỗ trợ trên máy — giải nén trên PC rồi đưa file ROM (hoặc .zip) vào Import/"
FAT_BAD = set('<>:"|?*')

GB_LOGO = bytes.fromhex("CEED6666CC0D000B03730083000C000D0008111F8889000EDCCC6EE6DDDDD999BBBB67636E0EECCCDDDC999FBBB9333E")
GBA_LOGO = bytes([
    0x24, 0xFF, 0xAE, 0x51, 0x69, 0x9A, 0xA2, 0x21, 0x3D, 0x84, 0x82, 0x0A, 0x84, 0xE4, 0x09, 0xAD,
    0x11, 0x24, 0x8B, 0x98, 0xC0, 0x81, 0x7F, 0x21, 0xA3, 0x52, 0xBE, 0x19, 0x93, 0x09, 0xCE, 0x20,
    0x10, 0x46, 0x4A, 0x4A, 0xF8, 0x27, 0x31, 0xEC, 0x58, 0xC7, 0xE8, 0x33, 0x82, 0xE3, 0xCE, 0xBF,
    0x85, 0xF4, 0xDF, 0x94, 0xCE, 0x4B, 0x09, 0xC1, 0x94, 0x56, 0x8A, 0xC0, 0x13, 0x72, 0xA7, 0xFC,
    0x9F, 0x84, 0x4D, 0x73, 0xA3, 0xCA, 0x9A, 0x61, 0x58, 0x97, 0xA3, 0x27, 0xFC, 0x03, 0x98, 0x76,
    0x23, 0x1D, 0xC7, 0x61, 0x03, 0x04, 0xAE, 0x56, 0xBF, 0x38, 0x84, 0x00, 0x40, 0xA7, 0x0E, 0xFD,
    0xFF, 0x52, 0xFE, 0x03, 0x6F, 0x95, 0x30, 0xF1, 0x97, 0xFB, 0xC0, 0x85, 0x60, 0xD6, 0x80, 0x25,
    0xA9, 0x63, 0xBE, 0x03, 0x01, 0x4E, 0x38, 0xE2, 0xF9, 0xA2, 0x34, 0xFF, 0xBB, 0x3E, 0x03, 0x44,
    0x78, 0x00, 0x90, 0xCB, 0x88, 0x11, 0x3A, 0x94, 0x65, 0xC0, 0x7C, 0x63, 0x87, 0xF0, 0x3C, 0xAF,
    0xD6, 0x25, 0xE4, 0x8B, 0x38, 0x0A, 0xAC, 0x72, 0x21, 0xD4, 0xF8, 0x07])
N64_MAGIC = {b"\x80\x37\x12\x40": ".z64 (big-endian)", b"\x37\x80\x40\x12": ".v64 (byte-swapped)",
             b"\x40\x12\x37\x80": ".n64 (little-endian)"}


class ImportBlocked(Exception):
    """Nội dung vi phạm quy tắc an toàn: chặn, không cài. str(e) là lý do (tiếng Việt)."""


# ======================================================================================
# tiện ích
# ======================================================================================
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if 0 < i < len(name) - 1 else ""


def _stem(name: str) -> str:
    i = name.rfind(".")
    return name[:i] if 0 < i < len(name) - 1 else name


def _append_jsonl(path: Path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def _ledger(app_data, event: str, **kw) -> None:
    _append_jsonl(Path(app_data) / LEDGER_NAME, {"at": _now_iso(), "event": event, **kw})


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with open(tmp, "wb") as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _checkpoint(name: str) -> None:
    """Điểm dừng cho test mất điện (test thay hàm này để 'giết' tiến trình). Không làm gì khi chạy thật."""


def _sha256_path(path: Path) -> str:
    return compute_sha256(Path(path))


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _pid_alive(pid: Any, boot_id: str) -> bool:
    """Chỉ chắc chắn trên Linux (máy thật): cùng boot_id và /proc/<pid> còn. Nơi khác: coi là đã chết."""
    if not boot_id or boot_id != _boot_id() or not isinstance(pid, int):
        return False
    return os.path.exists(f"/proc/{pid}")


# ======================================================================================
# danh mục hệ máy / BIOS
# ======================================================================================
def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else find_systems_catalog()
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    by_id: Dict[str, Dict[str, Any]] = {}
    ext_map: Dict[str, List[str]] = {}
    zip_only: List[str] = []
    for s in data.get("systems", []):
        sid = s["id"]
        by_id[sid] = s
        exts = [e.lower() for e in s.get("extensions", [])]
        for e in exts:
            if e in (".zip", ".7z"):
                continue
            ext_map.setdefault(e, []).append(sid)
        if ".zip" in exts and set(exts) <= {".zip", ".7z", ".chd"}:
            zip_only.append(sid)
    return {"by_id": by_id, "ext": ext_map, "zip_only": sorted(zip_only)}


def load_bios_md5(path: Optional[Path] = None) -> Dict[str, Any]:
    """{"by_md5": {md5: tên chuẩn}, "names": {tên thường: tên chuẩn}, "systems": {tên chuẩn: [hệ]}}"""
    from engine.bios import load_hashes
    hashes = load_hashes(path)
    by_md5: Dict[str, str] = {}
    for name in sorted(hashes):
        for m in hashes[name]["md5"]:
            by_md5.setdefault(m, name)
    return {"by_md5": by_md5, "names": {n: n for n in hashes},
            "systems": {n: list(h.get("systems", [])) for n, h in hashes.items()},
            "dirs": {n: h["dir"] for n, h in hashes.items() if isinstance(h.get("dir"), str) and h["dir"].isalnum()}}


# ======================================================================================
# nhận diện bằng header (xem NGUỒN HEADER ở đầu file)
# ======================================================================================
def _crc16_nds(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _detect_disc(head: bytes) -> Optional[Tuple[List[str], str, List[str]]]:
    raw = head[:12] == b"\x00" + b"\xff" * 10 + b"\x00"
    if raw:
        mode = head[15] if len(head) > 15 else 1
        u0, ssz, hdr = (24 if mode == 2 else 16), 2352, (24 if mode == 2 else 16)
    else:
        u0, ssz, hdr = 0, 2048, 0
    first = head[u0:u0 + 16]
    for magic, sid in ((b"SEGADISCSYSTEM", "SEGACD"), (b"SEGA SEGASATURN", "SATURN"), (b"SEGA SEGAKATANA", "DC")):
        if first.startswith(magic):
            return [sid], f"đĩa: '{magic.decode()}' @0x{u0:X}", []
    pvd = 16 * ssz + hdr
    sec = head[pvd:pvd + 2048]
    if sec[:6] == b"\x01CD001" or sec[1:6] == b"CD001":
        sysid = sec[8:40].strip(b"\x00 ")
        if sysid.startswith(b"PSP GAME"):
            return ["PSP"], "ISO9660 PVD System Identifier 'PSP GAME' @0x8008", []
        if sysid.startswith(b"PLAYSTATION"):
            lic = raw and head[0x24F8:0x24F8 + 14] == b"Sony Computer "
            warn = [] if lic else ["System Identifier 'PLAYSTATION' nhưng không thấy giấy phép PS1: có thể là PS2 (không hỗ trợ)"]
            return ["PSX"], f"ISO9660 PVD System Identifier 'PLAYSTATION' @0x{pvd + 8:X}" + (" + giấy phép PS1" if lic else ""), warn
    return None


def detect_header(head: bytes, name: str = "") -> Optional[Tuple[List[str], str, List[str]]]:
    """(hệ ứng viên theo thứ tự ưu tiên, bằng chứng, cảnh báo) hoặc None. CHỈ dùng byte header."""
    n = len(head)
    if head[:4] == b"NES\x1a":
        return ["NES"], "iNES 'NES\\x1A' @0", []
    if head[:4] == b"FDS\x1a" or (n > 15 and head[0] == 1 and head[1:15] == b"*NINTENDO-HVC*"):
        return ["FDS"], "Famicom Disk System header", []
    if head[:4] in N64_MAGIC:
        return ["N64"], f"N64 byte-order magic {N64_MAGIC[head[:4]]}", []
    if n >= 0xC0 and head[0xB2] == 0x96:       # GBA
        chk = (-(0x19 + sum(head[0xA0:0xBD]))) & 0xFF
        if chk == head[0xBD]:
            logo = head[4:0xA0] == GBA_LOGO
            return ["GBA"], "GBA header: 0x96 @0xB2 + complement @0xBD" + (" + logo Nintendo" if logo else ""), \
                ([] if logo else ["logo Nintendo trong header GBA không khớp (homebrew/hack?)"])
    if n >= 0x160 and head[0xC0:0xC0 + 156] == GBA_LOGO and \
            struct.unpack_from("<H", head, 0x15C)[0] == _crc16_nds(head[0xC0:0xC0 + 156]):
        warn = []
        if struct.unpack_from("<H", head, 0x15E)[0] != _crc16_nds(head[:0x15E]):
            warn.append("CRC header NDS không khớp (ROM bị vá/hỏng?)")
        return ["NDS"], "NDS header: logo @0xC0 + CRC16 logo @0x15C", warn
    if n >= 0x150 and head[0x104:0x134] == GB_LOGO:
        c = 0
        for b in head[0x134:0x14D]:
            c = (c - b - 1) & 0xFF
        warn = [] if c == head[0x14D] else ["checksum header Game Boy @0x14D không khớp (homebrew/hack?)"]
        flag = head[0x143]
        if flag == 0xC0:
            return ["GBC"], "Game Boy: logo @0x104 + cờ CGB-only 0xC0 @0x143", warn
        if flag == 0x80:
            return ["GBC", "GB"], "Game Boy: logo @0x104 + cờ CGB 0x80 @0x143 (chạy được cả GB lẫn GBC)", warn
        return ["GB"], "Game Boy: logo @0x104", warn
    if n >= 0x110 and head[0x100:0x104] == b"SEGA":
        if head[0x100:0x108] == b"SEGA 32X":
            return ["32X", "MD32X"], "Mega Drive header 'SEGA 32X' @0x100 (folder 32X hay MD32X: hỏi người dùng)", []
        return ["GENESIS"], "Mega Drive header 'SEGA' @0x100", []
    for off in (0x7FF0, 0x3FF0, 0x1FF0):
        if n >= off + 16 and head[off:off + 8] == b"TMR SEGA":
            nib = head[off + 0x0F] >> 4
            if nib in (3, 4):
                return ["MS"], f"'TMR SEGA' @0x{off:X}, vùng {nib} = SMS", []
            if nib in (5, 6, 7):
                return ["GG"], f"'TMR SEGA' @0x{off:X}, vùng {nib} = Game Gear", []
            return ["MS", "GG"], f"'TMR SEGA' @0x{off:X}, vùng {nib} không rõ SMS hay GG", []
    d = _detect_disc(head)
    if d:
        return d
    for copier in (0x200, 0) if n % 1024 == 512 or n >= 0x10200 else (0,):   # SNES (kiểm sau cùng: chỉ có checksum)
        for base, kind in ((0x7FC0, "LoROM"), (0xFFC0, "HiROM")):
            o = base + copier
            if n < o + 0x20:
                continue
            comp, chk = struct.unpack_from("<HH", head, o + 0x1C)
            title = head[o:o + 21]
            if (comp + chk) & 0xFFFF == 0xFFFF and all(32 <= c < 127 for c in title) and head[o + 0x15] & 0x0F <= 5:
                return ["SNES"], f"SNES header {kind} @0x{o:X}: complement + checksum = 0xFFFF", []
    return None


def classify_bytes(name: str, head: bytes, catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Phân loại một file: header trước, rồi phần mở rộng duy nhất, không thì needs_choice (không đoán).
    Trả {"status": "ready"|"needs_choice"|"unknown", "system", "candidates", "by", "evidence", "warnings"}."""
    ext = _ext(name)
    ext_ids = catalog["ext"].get(ext, [])
    warns: List[str] = []
    h = detect_header(head, name)
    if h:
        cands, evidence, hw = h
        cands = [c for c in cands if c in catalog["by_id"]]
        warns += hw
        if cands:
            if len(cands) == 1:
                system = cands[0]
            else:
                narrowed = [c for c in cands if c in ext_ids]
                system = narrowed[0] if len(narrowed) == 1 else None
            if system and ext_ids and system not in ext_ids and ext not in GENERIC_EXTS:
                warns.append(f"đuôi '{ext}' thường là {'/'.join(ext_ids)} nhưng nội dung là {system} — theo nội dung")
            if system:
                return {"status": "ready", "system": system, "candidates": [system], "by": "header",
                        "evidence": evidence, "warnings": warns}
            return {"status": "needs_choice", "system": None, "candidates": cands, "by": "header",
                    "evidence": evidence, "warnings": warns}
    if len(ext_ids) == 1:
        return {"status": "ready", "system": ext_ids[0], "candidates": ext_ids, "by": "extension",
                "evidence": f"đuôi '{ext}' chỉ thuộc hệ {ext_ids[0]} trong systems.json", "warnings": warns}
    if len(ext_ids) > 1:
        return {"status": "needs_choice", "system": None, "candidates": sorted(ext_ids), "by": None,
                "evidence": f"đuôi '{ext}' dùng chung cho nhiều hệ, không có header nhận diện được", "warnings": warns}
    return {"status": "unknown", "system": None, "candidates": [], "by": None,
            "evidence": f"đuôi '{ext}' không thuộc hệ nào trong systems.json", "warnings": warns}


# ======================================================================================
# nguồn: tên an toàn, archive, thư mục
# ======================================================================================
def _split_member(name: str) -> Tuple[List[str], List[str]]:
    """(thành phần đã chuẩn hoá, vấn đề). Chặn tuyệt đối, '..', NUL/ký tự điều khiển."""
    probs: List[str] = []
    raw = name.replace("\\", "/")
    if "\x00" in raw or any(ord(c) < 32 for c in raw):
        probs.append("tên chứa ký tự điều khiển/NUL")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        probs.append("đường dẫn tuyệt đối")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        probs.append("chứa '..' (path traversal)")
    return parts, probs


def _fat_clean(component: str) -> Tuple[str, bool]:
    """Thay ký tự FAT32 cấm bằng '_'; bỏ dấu chấm/khoảng trắng cuối. (tên mới, có đổi không)."""
    new = "".join("_" if c in FAT_BAD else c for c in component).rstrip(". ")
    return (new or "_"), new != component


class Member:
    __slots__ = ("rel", "size", "compress_size", "abspath", "zinfo", "zf", "problems", "_head")

    def __init__(self, rel, size, abspath=None, zinfo=None, zf=None, compress_size=None):
        self.rel, self.size, self.abspath, self.zinfo, self.zf = rel, size, abspath, zinfo, zf
        self.compress_size = compress_size
        self.problems: List[str] = []
        self._head: Optional[bytes] = None

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]

    def open(self):
        if self.zinfo is not None:
            return self.zf.open(self.zinfo)
        return open(self.abspath, "rb")

    def head(self, n: int = HEAD_BYTES) -> bytes:
        if self._head is None:
            with self.open() as f:
                self._head = f.read(n)
        return self._head


class Source:
    def __init__(self, path: Path, kind: str, card: Optional[str], in_import: bool):
        self.path, self.kind, self.card, self.in_import = Path(path), kind, card, in_import
        self.members: List[Member] = []
        self.problems: List[str] = []       # chặn CẢ nguồn (zip/thư mục)
        self.zf: Optional[zipfile.ZipFile] = None
        self.origin: List[Path] = [Path(path)]
        self.lookup_dir: Optional[Path] = None
        self.skipped: List[Dict[str, str]] = []
        self.unsupported: Optional[str] = None

    def close(self) -> None:
        if self.zf is not None:
            try:
                self.zf.close()
            except Exception:
                pass
            self.zf = None


def _cap(msgs: List[str], k: int = 6) -> List[str]:
    return msgs if len(msgs) <= k else msgs[:k] + [f"… và {len(msgs) - k} vấn đề khác"]


def _check_member_name(rel_parts: List[str], limits: Limits) -> List[str]:
    probs = []
    if len(rel_parts) - 1 > limits.max_depth:
        probs.append(f"thư mục lồng quá sâu (> {limits.max_depth})")
    if any(len(p.encode("utf-16-le")) // 2 > 255 for p in rel_parts):
        probs.append("tên quá dài (> 255 ký tự)")
    return probs


def _check_ext(name: str) -> Optional[str]:
    e = _ext(name)
    if e in EXEC_EXTS:
        return f"file thực thi/script '{name}' (đuôi {e})"
    if e in ARCHIVE_EXTS:
        return f"archive lồng nhau '{name}' (MVP không giải nén lồng)"
    return None


def collect_zip(path: Path, card, in_import, limits: Limits) -> Source:
    src = Source(path, "zip", card, in_import)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, NotImplementedError) as e:
        src.problems.append(f"không mở được ZIP: {e}")
        return src
    src.zf = zf
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        src.problems.append(f"quá nhiều entry ({len(infos)} > {limits.max_entries})")
        return src
    try:
        arch_size = path.stat().st_size
    except OSError:
        arch_size = 0
    total, seen, probs = 0, {}, []
    for zi in infos:
        parts, p = _split_member(zi.filename)
        mode = (zi.external_attr >> 16) & 0xFFFF if zi.create_system == 3 else 0
        kind = stat.S_IFMT(mode)
        if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
            p.append("symlink/hardlink/device trong archive" if kind == stat.S_IFLNK else "loại file đặc biệt (device/fifo/socket)")
        if zi.flag_bits & 0x1:
            p.append("entry có mật khẩu")
        if zi.is_dir() or kind == stat.S_IFDIR:
            if p:
                probs += [f"'{zi.filename}': {x}" for x in p]
            continue
        if not parts:
            p.append("tên rỗng")
        p += _check_member_name(parts, limits)
        if zi.file_size > limits.max_file_bytes:
            p.append(f"file quá lớn ({zi.file_size // MB} MB > {limits.max_file_bytes // MB} MB)")
        if zi.file_size >= limits.ratio_min_bytes and (
                zi.compress_size == 0 or zi.file_size / zi.compress_size > limits.max_ratio):
            p.append(f"tỉ lệ nén bất thường (> {limits.max_ratio}:1) — nghi zip bomb")
        if parts:
            e = _check_ext(parts[-1])
            if e:
                p.append(e)
            key = "/".join(parts).lower()
            if key in seen:
                p.append("hai entry trùng tên (không phân biệt hoa/thường)")
            seen[key] = True
        total += zi.file_size
        if p:
            probs += [f"'{zi.filename}': {x}" for x in p]
            continue
        src.members.append(Member("/".join(parts), zi.file_size, zinfo=zi, zf=zf, compress_size=zi.compress_size))
    if total > limits.max_total_bytes:
        probs.append(f"tổng giải nén {total // MB} MB > {limits.max_total_bytes // MB} MB")
    if total >= limits.ratio_min_bytes and arch_size and total / arch_size > limits.max_ratio:
        probs.append(f"tỉ lệ nén cả archive bất thường (> {limits.max_ratio}:1) — nghi zip bomb")
    src.problems += _cap(probs)
    return src


def collect_dir(path: Path, card, in_import, limits: Limits) -> Source:
    src = Source(path, "dir", card, in_import)
    probs, total, count = [], 0, 0
    root = Path(path)

    def walk(d: Path, parts: List[str]):
        nonlocal total, count
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError as e:
            probs.append(f"không đọc được thư mục '{d.name}': {e}")
            return
        for en in entries:
            if en.name.startswith(".") or en.name.lower() in IGNORED_NAMES:
                continue
            rel = parts + [en.name]
            try:
                if en.is_symlink():
                    probs.append(f"'{'/'.join(rel)}': symlink")
                    continue
                if en.is_dir(follow_symlinks=False):
                    if len(rel) > limits.max_depth:
                        probs.append(f"'{'/'.join(rel)}': thư mục lồng quá sâu (> {limits.max_depth})")
                        continue
                    walk(Path(en.path), rel)
                    continue
                if not en.is_file(follow_symlinks=False):
                    probs.append(f"'{'/'.join(rel)}': loại file đặc biệt (device/fifo/socket)")
                    continue
                st = en.stat(follow_symlinks=False)
            except OSError as e:
                probs.append(f"'{'/'.join(rel)}': {e}")
                continue
            count += 1
            p = _check_member_name(rel, limits)
            if count > limits.max_entries:
                if count == limits.max_entries + 1:
                    probs.append(f"quá nhiều file (> {limits.max_entries})")
                continue
            if st.st_size > limits.max_file_bytes:
                p.append(f"file quá lớn ({st.st_size // MB} MB)")
            e = _check_ext(en.name)
            if e:
                p.append(e)
            total += st.st_size
            if p:
                probs.extend(f"'{'/'.join(rel)}': {x}" for x in p)
                continue
            src.members.append(Member("/".join(rel), st.st_size, abspath=Path(en.path)))

    walk(root, [])
    if total > limits.max_total_bytes:
        probs.append(f"tổng dung lượng {total // MB} MB > {limits.max_total_bytes // MB} MB")
    src.problems += _cap(probs)
    return src


def collect_pool(files: List[Path], card, in_import, limits: Limits, lookup_dir: Optional[Path]) -> Source:
    """Nhóm file lẻ cùng thư mục. Vấn đề gắn theo từng file (không chặn các file không liên quan)."""
    src = Source(files[0], "pool", card, in_import)
    src.origin = list(files)
    src.lookup_dir = lookup_dir
    for f in sorted(files, key=lambda p: p.name.lower()):
        if f.name.startswith(".") or f.name.lower() in IGNORED_NAMES:
            continue
        try:
            st = f.lstat()
        except OSError as e:
            m = Member(f.name, 0, abspath=f)
            m.problems.append(str(e))
            src.members.append(m)
            continue
        m = Member(f.name, st.st_size, abspath=f)
        if stat.S_ISLNK(st.st_mode):
            m.problems.append("symlink")
        elif not stat.S_ISREG(st.st_mode):
            m.problems.append("loại file đặc biệt (device/fifo/socket)")
        else:
            m.problems += _check_member_name([f.name], limits)
            e = _check_ext(f.name)
            if e:
                m.problems.append(e)
            if st.st_size > limits.max_file_bytes:
                m.problems.append(f"file quá lớn ({st.st_size // MB} MB)")
        src.members.append(m)
    return src


def _magic_problems(m: Member, header_ok: bool) -> Optional[str]:
    """ELF / PE / shebang trong nội dung. Shebang chỉ xét khi file không có header ROM nhận diện được."""
    h = m.head(4096)
    if h[:4] == b"\x7fELF":
        return f"'{m.name}' là file thực thi ELF"
    if h[:2] == b"MZ" and len(h) >= 0x40:
        pe = struct.unpack_from("<I", h, 0x3C)[0]
        if pe + 4 <= len(h) and h[pe:pe + 4] == b"PE\x00\x00":
            return f"'{m.name}' là file thực thi Windows (PE)"
    if not header_ok and h[:3] == b"#!/":
        return f"'{m.name}' là script (shebang)"
    return None


# ======================================================================================
# phát hiện unit (game) trong một nguồn
# ======================================================================================
def _resolve(src: Source, by_key: Dict[str, Member], rel: str) -> Optional[Member]:
    m = by_key.get(rel.lower())
    if m is None and src.lookup_dir is not None:
        p = Path(src.lookup_dir) / rel
        try:
            for f in p.parent.iterdir():
                if f.name.lower() == p.name.lower() and f.is_file() and not f.is_symlink():
                    m = Member(rel, f.stat().st_size, abspath=f)
                    mp = _check_ext(f.name)
                    if mp:
                        m.problems.append(mp)
                    by_key[rel.lower()] = m
                    src.members.append(m)
                    src.origin.append(f)
                    break
        except OSError:
            pass
    return m


def _playlist_refs(m: Member, kind: str) -> Tuple[List[str], List[str]]:
    """(tham chiếu đã gộp với thư mục của playlist, lỗi). Tham chiếu thoát gốc/tuyệt đối là lỗi."""
    try:
        with m.open() as f:
            text = decode_playlist_bytes(f.read(1 * MB))
    except Exception as e:
        return [], [f"không đọc được '{m.name}': {e}"]
    ext = _ext(m.name)
    if ext == ".cue":
        raw = [a if a else b for a, b in CUE_FILE_LINE_RE.findall(text)]
    elif ext == ".gdi":
        raw = []
        for ln in text.splitlines()[1:]:
            mm = re.match(r'^\s*\d+\s+\d+\s+\d+\s+\d+\s+(?:"([^"]+)"|(\S+))', ln)
            if mm:
                raw.append(mm.group(1) or mm.group(2))
    else:
        raw = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    base = m.rel.rsplit("/", 1)[0].split("/") if "/" in m.rel else []
    refs, errs = [], []
    for r in raw:
        parts, p = _split_member(r)
        if p or not parts:
            errs.append(f"'{m.name}' tham chiếu '{r}' không hợp lệ ({', '.join(p) or 'rỗng'})")
            continue
        refs.append("/".join(base + parts))
    return refs, errs


def _origin(src: Source, files: List[Tuple[Member, str, str]]) -> List[str]:
    """File gốc của unit (để lưu dấu thời gian và chuyển vào .done): nhóm file lẻ -> đúng các file của unit."""
    if src.kind == "pool" and files:
        return [str(m.abspath) for m, _, _ in files if m.abspath is not None]
    return [str(src.path)]


def _unit_id(src: Source, files: List[Tuple[Member, str, str]]) -> str:
    stamps = []
    for p in _origin(src, files):
        try:
            st = os.stat(p)
            stamps.append([p, st.st_size, st.st_mtime_ns])
        except OSError:
            stamps.append([p, -1, -1])
    key = json.dumps([[m.rel, m.size] for m, _, _ in files] + stamps, sort_keys=True, ensure_ascii=False)
    return "u-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _base_unit(src: Source, name: str, kind: str, files: List[Tuple[Member, str, str]]) -> Dict[str, Any]:
    return {
        "id": _unit_id(src, files), "source": _origin(src, files)[0], "source_kind": src.kind, "card": src.card,
        "in_import": src.in_import, "origin": _origin(src, files),
        "name": name, "kind": kind, "status": "ready", "system": None, "folder": None, "candidates": [],
        "by": None, "evidence": "", "files": [{"member": m.rel, "dest_rel": d, "size": m.size, "role": r} for m, d, r in files],
        "bytes": sum(m.size for m, _, _ in files), "reasons": [], "warnings": [], "bios": None,
    }


def _finish_unit(u: Dict[str, Any], cls: Dict[str, Any], catalog) -> None:
    u["status"] = cls["status"]
    u["system"] = cls["system"]
    u["candidates"] = cls["candidates"]
    u["by"], u["evidence"] = cls["by"], cls["evidence"]
    u["warnings"] += cls["warnings"]
    if u["system"]:
        u["folder"] = catalog["by_id"][u["system"]].get("folder") or u["system"]


def discover_units(src: Source, catalog, bios, limits: Limits) -> List[Dict[str, Any]]:
    """Chia các member đã qua kiểm tra thành unit (game) và phân loại từng unit."""
    units: List[Dict[str, Any]] = []
    if src.unsupported:
        u = _base_unit(src, src.path.name, "rom", [])
        u.update(status="unsupported", reasons=[src.unsupported], bytes=_safe_size(src.path))
        return [u]
    if src.problems:
        u = _base_unit(src, src.path.name, "rom", [])
        u.update(status="blocked", reasons=list(src.problems), bytes=_safe_size(src.path))
        return [u]
    by_key = {m.rel.lower(): m for m in src.members}
    consumed: set = set()

    # nội dung: ELF/PE chặn mọi nguồn; shebang chỉ khi không nhận diện được header
    for m in list(src.members):
        if m.problems or _ext(m.name) in PLAYLIST_EXTS:
            continue
        try:
            hdr = detect_header(m.head(), m.name) is not None
            mp = _magic_problems(m, hdr)
        except Exception as e:
            mp = f"không đọc được '{m.name}': {e}"
        if mp:
            m.problems.append(mp)
    if src.kind != "pool":
        bad = [f"{m.rel}: {p}" for m in src.members for p in m.problems]
        if bad:
            u = _base_unit(src, src.path.name, "rom", [])
            u.update(status="blocked", reasons=_cap(bad), bytes=_safe_size(src.path))
            return [u]

    def closure(pl: Member, depth: int = 0) -> Tuple[List[Member], List[str]]:
        files, errs = [pl], []
        refs, e = _playlist_refs(pl, _ext(pl.name))
        errs += e
        for r in refs:
            t = _resolve(src, by_key, r)
            if t is None:
                errs.append(f"'{pl.name}' tham chiếu '{r.rsplit('/', 1)[-1]}' nhưng file này không có")
                continue
            if t not in files:
                files.append(t)
            if _ext(t.name) in PLAYLIST_EXTS and depth < 3:
                sub, e2 = closure(t, depth + 1)
                errs += e2
                files += [x for x in sub if x not in files]
        return files, errs

    def multi_unit(pl: Member, primary_cls: Optional[Dict[str, Any]] = None) -> None:
        files, errs = closure(pl)
        consumed.update(m.rel.lower() for m in files)
        root = _common_dir([m.rel for m in files])
        items = []
        for m in files:
            d = m.rel[len(root) + 1:] if root and m.rel.startswith(root + "/") else m.rel
            items.append((m, d, "playlist" if _ext(m.name) in PLAYLIST_EXTS else "track"))
        items.sort(key=lambda t: (t[2] != "track", t[1].lower()))
        u = _base_unit(src, _stem(pl.name), "multi", items)
        probs = errs + [f"{m.rel}: {p}" for m, _, _ in items for p in m.problems]
        for m, d, _ in items:
            for comp in d.split("/"):
                if _fat_clean(comp)[1]:
                    probs.append(f"tên '{comp}' có ký tự FAT32 cấm — game nhiều file không tự đổi tên (tham chiếu .cue sẽ hỏng); đổi tên trong gói rồi nhập lại")
                    break
        if probs:
            u.update(status="blocked", reasons=_cap(probs))
            units.append(u)
            return
        # hệ: header của track dữ liệu đầu tiên; không có header thì theo đuôi playlist (.gdi -> DC, .cue/.m3u -> hỏi)
        probe = next((m for m in files if _ext(m.name) not in PLAYLIST_EXTS), None)
        cls = classify_bytes(probe.name, probe.head(), catalog) if probe is not None else None
        if cls is None or cls["by"] != "header":
            cls = classify_bytes(pl.name, b"", catalog)
        _finish_unit(u, cls, catalog)
        if u["status"] == "unknown":
            u.update(status="needs_choice", candidates=sorted({s for e in (".cue", ".m3u") for s in catalog["ext"].get(e, [])}),
                     evidence="không nhận diện được đĩa")
        units.append(u)

    ms = sorted(src.members, key=lambda m: m.rel.lower())
    for pl in (m for m in ms if _ext(m.name) == ".m3u"):
        if pl.rel.lower() not in consumed:
            multi_unit(pl)
    for pl in (m for m in ms if _ext(m.name) in (".cue", ".gdi")):
        if pl.rel.lower() not in consumed:
            multi_unit(pl)

    leftovers: List[Member] = []
    for m in ms:
        k = m.rel.lower()
        if k in consumed:
            continue
        e = _ext(m.name)
        if e in SAVE_EXTS:
            src.skipped.append({"rel": m.rel, "reason": "file save/state — LoongMate không nhập save"})
            continue
        if e in META_EXTS:
            src.skipped.append({"rel": m.rel, "reason": "metadata (README/ảnh/...) — không cài"})
            continue
        if m.problems:
            u = _base_unit(src, _stem(m.name), "rom", [(m, m.name, "rom")])
            u.update(status="blocked", reasons=list(m.problems))
            units.append(u)
            continue
        leftovers.append(m)

    singles: List[Dict[str, Any]] = []
    unknown_members: List[Member] = []
    for m in leftovers:
        hdr = detect_header(m.head(), m.name)
        if not hdr and 0 < m.size <= limits.max_bios_bytes:
            md5 = _md5_member(m)
            canon = bios["by_md5"].get(md5)
            lname = m.name.lower()
            if canon or lname in bios["names"]:
                u = _base_unit(src, m.name, "bios", [(m, m.name, "bios")])
                if canon:
                    u.update(status="ready", system=None,
                             bios={"name": canon, "md5": md5, "systems": bios["systems"].get(canon, []),
                                   "dest": "/".join(["BIOS"] + ([bios["dirs"][canon]] if canon in bios.get("dirs", {}) else [])
                                                    + [canon])},
                             evidence=f"md5 {md5} khớp bios_hashes.json ({canon})")
                    if canon.lower() != lname:
                        u["warnings"].append(f"đổi tên '{m.name}' -> '{canon}' (đúng tên Leaf/libretro mong đợi)")
                else:
                    u.update(status="blocked", reasons=[f"tên giống BIOS '{m.name}' nhưng md5 {md5} không khớp bản hợp lệ trong bios_hashes.json — không nhập (không coi là game)"])
                units.append(u)
                continue
        cls = classify_bytes(m.name, m.head(), catalog)
        if cls["status"] == "unknown":
            unknown_members.append(m)
            continue
        u = _base_unit(src, _stem(m.name), "rom", [(m, _fat_name(m.name, None), "rom")])
        _fat_note(u, m.name)
        _finish_unit(u, cls, catalog)
        singles.append(u)
    units += singles

    if unknown_members:
        classified = [u for u in units if u["kind"] in ("rom", "multi", "bios") and u["status"] in ("ready", "needs_choice")]
        if src.kind == "zip" and not classified and len(unknown_members) >= 2:
            u = _base_unit(src, _stem(src.path.name), "archive", [])
            u["files"] = [{"member": src.path.name, "dest_rel": src.path.name, "size": _safe_size(src.path), "role": "archive"}]
            u["bytes"] = _safe_size(src.path)
            u.update(status="needs_choice", candidates=list(catalog["zip_only"]), by=None,
                     evidence=f"zip có {len(unknown_members)} file không nhận diện (kiểu bộ ROM arcade?) — cài NGUYÊN FILE zip, hãy chọn hệ")
            units.append(u)
        else:
            for m in unknown_members:
                if classified:
                    src.skipped.append({"rel": m.rel, "reason": "định dạng không nhận diện — bỏ qua"})
                else:
                    u = _base_unit(src, _stem(m.name), "rom", [(m, m.name, "rom")])
                    u.update(status="blocked", reasons=[f"không nhận diện được '{m.name}' (đuôi '{_ext(m.name)}' không thuộc hệ nào)"])
                    units.append(u)
    if not units and src.kind in ("zip", "dir"):
        u = _base_unit(src, src.path.name, "rom", [])
        u.update(status="empty", reasons=["không thấy file ROM nào trong gói"], bytes=_safe_size(src.path))
        units.append(u)
    return units


def _common_dir(rels: List[str]) -> str:
    dirs = [r.split("/")[:-1] for r in rels]
    common = dirs[0] if dirs else []
    for d in dirs[1:]:
        k = 0
        while k < len(common) and k < len(d) and common[k].lower() == d[k].lower():
            k += 1
        common = common[:k]
    return "/".join(common)


def _fat_name(name: str, _unused) -> str:
    return _fat_clean(name)[0]


def _fat_note(u: Dict[str, Any], orig: str) -> None:
    new = _fat_clean(orig)[0]
    if new != orig:
        u["warnings"].append(f"đổi tên '{orig}' -> '{new}' (ký tự FAT32 cấm)")


def _md5_member(m: Member) -> str:
    h = hashlib.md5()
    with m.open() as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_size(p: Path) -> int:
    try:
        return Path(p).stat().st_size if Path(p).is_file() else 0
    except OSError:
        return 0


# ======================================================================================
# thẻ & liệt kê nguồn
# ======================================================================================
def import_dir(card_root) -> Path:
    return Path(card_root) / IMPORT_DIR


def ensure_import_dir(card_root) -> Dict[str, Any]:
    """Tạo <thẻ>/Import/ nếu chưa có ("Mở thư mục nhập"). Việc ghi duy nhất ngoài apply."""
    d = import_dir(card_root)
    try:
        d.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(d), "message": f"Thư mục nhập: {d}"}
    except OSError as e:
        return {"ok": False, "path": str(d), "message": f"Không tạo được {d}: {e}"}


def _cards(primary, secondary) -> Dict[str, Optional[Path]]:
    return {"primary": Path(primary) if primary else None, "secondary": Path(secondary) if secondary else None}


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return False


def default_roots(cards) -> List[Path]:
    return [import_dir(c) for c in cards.values() if c and import_dir(c).is_dir()]


def _enumerate(paths, cards, limits: Limits) -> Tuple[List[Source], List[Dict[str, Any]]]:
    """Mỗi đường dẫn -> các Source. Trả (nguồn, lỗi)."""
    out: List[Source] = []
    errors: List[Dict[str, Any]] = []
    for raw in paths:
        p = Path(raw)
        card_of = next((cid for cid, c in cards.items() if c and _same(import_dir(c), p)), None)
        if not p.exists():
            errors.append({"path": str(p), "message": "không tồn tại"})
            continue
        if p.is_dir() and card_of:
            loose: List[Path] = []
            try:
                kids = sorted(os.scandir(p), key=lambda e: e.name.lower())
            except OSError as e:
                errors.append({"path": str(p), "message": str(e)})
                continue
            for k in kids:
                if k.name.startswith(".") or k.name.lower() in IGNORED_NAMES:
                    continue
                kp = Path(k.path)
                if k.is_symlink():
                    out.append(_blocked_pool(kp, card_of, "symlink"))
                elif k.is_dir(follow_symlinks=False):
                    out.append(collect_dir(kp, card_of, True, limits))
                elif _ext(k.name) == ".zip":
                    out.append(collect_zip(kp, card_of, True, limits))
                elif _ext(k.name) in UNSUPPORTED_ARCHIVE_EXTS:
                    out.append(_unsupported(kp, card_of, True))
                else:
                    loose.append(kp)
            if loose:
                out.append(collect_pool(loose, card_of, True, limits, p))
        elif p.is_dir():
            out.append(collect_dir(p, None, False, limits))
        elif _ext(p.name) == ".zip":
            out.append(collect_zip(p, None, False, limits))
        elif _ext(p.name) in UNSUPPORTED_ARCHIVE_EXTS:
            out.append(_unsupported(p, None, False))
        else:
            out.append(collect_pool([p], None, False, limits, p.parent))
    return out, errors


def _unsupported(p: Path, card, in_import) -> Source:
    s = Source(p, "file", card, in_import)
    s.unsupported = f"{_ext(p.name)}: {MSG_NO_7Z}"
    return s


def _blocked_pool(p: Path, card, why: str) -> Source:
    s = Source(p, "file", card, True)
    s.problems.append(why)
    return s


# ======================================================================================
# scan (CHỈ ĐỌC)
# ======================================================================================
def scan(paths: Optional[List[Any]], cards: Dict[str, Optional[Path]], limits: Limits = DEFAULT_LIMITS,
         catalog=None, bios=None) -> Dict[str, Any]:
    catalog = catalog or load_catalog()
    bios = bios or load_bios_md5()
    dirs = [{"card": cid, "path": str(import_dir(c)), "exists": import_dir(c).is_dir()}
            for cid, c in cards.items() if c]
    roots = [Path(p) for p in paths] if paths else default_roots(cards)
    sources, errors = _enumerate(roots, cards, limits)
    units: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    try:
        for s in sources:
            try:
                us = discover_units(s, catalog, bios, limits)
            except Exception as e:   # một nguồn hỏng không làm mất cả danh sách
                us = [{**_base_unit(s, s.path.name, "rom", []), "status": "blocked",
                       "reasons": [f"lỗi khi kiểm tra nguồn: {type(e).__name__}: {e}"]}]
            multi_sys = {u["system"] for u in us if u["kind"] in ("rom", "multi") and u["system"]}
            if len(multi_sys) > 1:
                for u in us:
                    u["warnings"].append("gói chứa nhiều hệ máy (chưa hỗ trợ manifest) — kiểm từng game")
            units += us
            skipped += [{"source": str(s.path), **x} for x in s.skipped]
    finally:
        for s in sources:
            s.close()
    counts: Dict[str, int] = {}
    for u in units:
        counts[u["status"]] = counts.get(u["status"], 0) + 1
    return {"import_dirs": dirs, "scan_roots": [str(r) for r in roots], "units": units, "errors": errors,
            "skipped": skipped, "summary": counts, "limits": asdict(limits)}


# ======================================================================================
# chọn thẻ đích (mục 5): phụ trước, không fallback âm thầm
# ======================================================================================
def _free(root: Path) -> int:
    try:
        return shutil.disk_usage(root).free
    except OSError:
        return 0


def _usable(root: Optional[Path], needed: int) -> Tuple[bool, str, int]:
    if root is None:
        return False, "không có thẻ", 0
    if not Path(root).is_dir():
        return False, "thẻ không gắn/không truy cập được", 0
    if not os.access(root, os.W_OK):
        return False, "thẻ chỉ đọc (không ghi được)", _free(root)
    free = _free(root)
    if free < needed:
        return False, f"không đủ chỗ (trống {free // MB} MB, cần {needed // MB} MB)", free
    return True, "", free


def choose_card(cards, needed: int, pref: str = "auto") -> Dict[str, Any]:
    """pref: auto | primary | secondary. auto = thẻ PHỤ nếu dùng được; nếu không thì KHÔNG tự dùng thẻ chính:
    trả needs_choice=True để người dùng quyết định (gọi lại với pref='primary')."""
    opts = []
    for cid in ("secondary", "primary"):
        ok, why, free = _usable(cards.get(cid), needed)
        opts.append({"card": cid, "root": str(cards[cid]) if cards.get(cid) else None, "free_bytes": free,
                     "usable": ok, "note": why or "dùng được"})
    by = {o["card"]: o for o in opts}
    res = {"selected": None, "root": None, "reason": "", "needs_choice": False, "options": opts,
           "needed_bytes": needed}
    if pref in ("primary", "secondary"):
        o = by[pref]
        if o["usable"]:
            res.update(selected=pref, root=o["root"],
                       reason=f"Bạn chọn thẻ {'chính' if pref == 'primary' else 'phụ'}: {o['root']} (trống {o['free_bytes'] // MB} MB, cần ~{needed // MB} MB).")
        else:
            res["reason"] = f"Thẻ {'chính' if pref == 'primary' else 'phụ'} không dùng được: {o['note']}."
        return res
    s = by["secondary"]
    if s["usable"]:
        res.update(selected="secondary", root=s["root"],
                   reason=f"Mặc định thẻ phụ {s['root']}: gắn, ghi được, trống {s['free_bytes'] // MB} MB ≥ cần ~{needed // MB} MB "
                          f"(dung lượng cần gồm giải nén + staging + dự phòng).")
        return res
    p = by["primary"]
    res["needs_choice"] = True
    res["reason"] = (f"Thẻ phụ không dùng được: {s['note']}. Không tự chuyển sang thẻ Leaf — hãy chọn."
                     + (f" Thẻ chính {p['root']} trống {p['free_bytes'] // MB} MB." if p["usable"] else
                        f" Thẻ chính cũng không dùng được: {p['note']}."))
    return res


# ======================================================================================
# kiểm trùng thư viện
# ======================================================================================
class LibIndex:
    """Chỉ mục file ROM theo (thẻ, hệ) — dựng lười từ Roms/<hệ tương đương>/ (đệ quy). Băm chỉ khi trùng kích thước."""

    def __init__(self, cards, hashcache=None):
        self.cards, self.hc = cards, hashcache
        self._idx: Dict[Tuple[str, str], List[Tuple[Path, int]]] = {}

    def files(self, folder: str) -> List[Tuple[Path, int]]:
        key = canon_system(folder)
        if (key, "") not in self._idx:
            out: List[Tuple[Path, int]] = []
            for c in self.cards.values():
                if not c:
                    continue
                for d in system_dirs(c, "Roms", folder):
                    for dp, dn, fn in os.walk(d):
                        dn[:] = [x for x in dn if not x.startswith(".")]
                        for f in fn:
                            if f.startswith("."):
                                continue
                            fp = Path(dp) / f
                            try:
                                out.append((fp, fp.stat().st_size))
                            except OSError:
                                pass
            self._idx[(key, "")] = out
        return self._idx[(key, "")]

    def sha(self, p: Path) -> str:
        return self.hc.hash(p, _sha256_path) if self.hc else _sha256_path(p)

    def find_identical(self, folder: str, size: int, sha: str) -> Optional[Path]:
        for p, sz in self.files(folder):
            if sz == size:
                try:
                    if self.sha(p) == sha:
                        return p
                except OSError:
                    continue
        return None


def _exists_ci(path: Path) -> Optional[Path]:
    """Đường dẫn thật nếu đã có file/thư mục cùng tên (FAT không phân biệt hoa/thường)."""
    if os.path.lexists(path):
        return path
    try:
        low = path.name.lower()
        for f in path.parent.iterdir():
            if f.name.lower() == low:
                return f
    except OSError:
        pass
    return None


def _final_paths(root: Path, u: Dict[str, Any], system: str, catalog, copy_tag: Optional[str] = None) -> List[Tuple[Dict[str, Any], Path]]:
    folder = catalog["by_id"][system].get("folder") or system
    base = Path(root) / "Roms" / folder
    out = []
    for f in u["files"]:
        rel = f["dest_rel"]
        if copy_tag:
            if u["kind"] == "multi":
                rel = f"{copy_tag}/{rel}"
            else:
                rel = f"{_stem(rel)} ({copy_tag}){_ext_raw(rel)}"
        out.append((f, base.joinpath(*rel.split("/"))))
    return out


def _ext_raw(name: str) -> str:
    i = name.rfind(".")
    return name[i:] if 0 < i < len(name) - 1 else ""


def _annotate(u: Dict[str, Any], root: Optional[Path], catalog, lib: LibIndex, member_sha: Callable[[Dict[str, Any]], Optional[str]]) -> None:
    """Thêm action/dest/conflict vào unit (CHỈ ĐỌC). Hash nội dung mới chỉ tính khi có file cùng kích thước đã tồn tại."""
    u.setdefault("action", "blocked")
    st = u["status"]
    if st in ("blocked", "unsupported", "empty"):
        u["action"] = st
        return
    if u["kind"] == "bios":
        u["action"] = "bios_confirm"
        if root is not None:
            dest = Path(root) / "BIOS" / u["bios"]["name"]
            ex = _exists_ci(dest)
            u["dest_paths"] = [str(dest)]
            if ex is not None:
                same = _md5_path(ex) == u["bios"]["md5"]
                u["action"] = "bios_present" if same else "bios_conflict"
                if not same:
                    u["reasons"].append(f"BIOS/{u['bios']['name']} đã có với nội dung khác — không bao giờ ghi đè BIOS")
        return
    if st == "needs_choice":
        u["action"] = "choose_system"
        return
    system = u["system"]
    u["action"] = "install"
    if root is None:
        return
    pairs = _final_paths(root, u, system, catalog)
    u["dest_paths"] = [str(p) for _, p in pairs]
    present, conflicts = 0, []
    for f, dst in pairs:
        sha = None
        ex = _exists_ci(dst)
        same_size_any = any(sz == f["size"] for _, sz in lib.files(u["folder"]))
        if same_size_any or ex is not None:
            sha = member_sha(f)
        ex_same = ex is not None and sha is not None and os.path.getsize(ex) == f["size"] and lib.sha(Path(ex)) == sha
        if sha and lib.find_identical(u["folder"], f["size"], sha):
            present += 1
        if ex is not None and not ex_same:
            conflicts.append(str(ex))
    if present == len(pairs) and pairs:
        u["action"] = "present"
    elif conflicts:
        u["action"] = "conflict"
        u["conflict"] = {"paths": conflicts, "options": list(CONFLICT_ACTIONS)}


def _md5_path(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(paths, cards, app_data, card_pref: str = "auto", select: Optional[List[str]] = None,
               limits: Limits = DEFAULT_LIMITS, hashcache=None, catalog=None, bios=None,
               choices: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """scan + chọn thẻ + kiểm trùng. CHỈ ĐỌC. `select` = các id unit được duyệt (None = mọi unit)."""
    catalog = catalog or load_catalog()
    sc = scan(paths, cards, limits, catalog, bios)
    units = sc["units"]
    if select is not None:
        wanted = set(select)
        units = [u for u in units if u["id"] in wanted]
    choices = choices or {}
    for u in units:
        c = choices.get(u["id"])
        if c and u["status"] == "needs_choice":
            if c in u["candidates"]:
                u.update(status="ready", system=c, by="user", folder=catalog["by_id"][c].get("folder") or c,
                         evidence=f"người dùng chọn {c}")
            else:
                u["warnings"].append(f"lựa chọn '{c}' không thuộc ứng viên {u['candidates']}")
    installable = [u for u in units if u["status"] in ("ready", "needs_choice")]
    needed = sum(u["bytes"] for u in installable) + limits.reserve_bytes
    card = choose_card(cards, needed, card_pref)
    root = Path(card["root"]) if card["selected"] else None
    lib = LibIndex(cards, hashcache)
    srcs: Dict[str, Source] = {}

    def member_sha_for(u):
        def f(fi):
            return _stream_sha_of_unit_file(u, fi, srcs, cards, limits)
        return f
    try:
        for u in units:
            try:
                _annotate(u, root, catalog, lib, member_sha_for(u))
            except Exception as e:
                u["action"] = "blocked"
                u["status"] = "blocked"
                u["reasons"].append(f"không kiểm trùng được: {type(e).__name__}: {e}")
    finally:
        for s in srcs.values():
            s.close()
    ok = card["selected"] is not None
    msg = card["reason"]
    return {"ok": ok, "message": msg, "card": card, "units": units, "needed_bytes": needed,
            "scan_roots": sc["scan_roots"], "import_dirs": sc["import_dirs"], "errors": sc["errors"],
            "skipped": sc["skipped"], "limits": sc["limits"], "needs_card_choice": card["needs_choice"]}


def _stream_sha_of_unit_file(u, fi, srcs, cards, limits) -> Optional[str]:
    """SHA-256 nội dung một file của unit (đọc từ nguồn, giới hạn như khi giải nén). None nếu không đọc được."""
    if u["kind"] == "archive":
        return _sha256_path(Path(u["source"]))
    key = u["source"]
    if key not in srcs:
        p = Path(key)
        if u["source_kind"] == "zip":
            srcs[key] = collect_zip(p, None, False, limits)
        elif u["source_kind"] == "dir":
            srcs[key] = collect_dir(p, None, False, limits)
        else:
            srcs[key] = collect_pool([Path(o) for o in u["origin"]], None, False, limits, p.parent)
    s = srcs[key]
    m = next((x for x in s.members if x.rel.lower() == fi["member"].lower()), None)
    if m is None:
        return None
    h = hashlib.sha256()
    n = 0
    with m.open() as f:
        for chunk in iter(lambda: f.read(1 * MB), b""):
            n += len(chunk)
            if n > limits.max_file_bytes:
                return None
            h.update(chunk)
    return h.hexdigest()


# ======================================================================================
# apply: staging -> verify -> kiểm trùng -> commit (có nhật ký để phục hồi)
# ======================================================================================
def _work(root: Path) -> Path:
    return Path(root) / WORK_DIR


def _stage_member(m: Member, dst: Path, staging_root: Path, limits: Limits, total: List[int]) -> Tuple[int, str]:
    real_root = os.path.abspath(staging_root)
    if not os.path.abspath(dst).startswith(real_root + os.sep):
        raise ImportBlocked(f"đích '{dst}' nằm ngoài staging")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not os.path.realpath(dst.parent).startswith(os.path.realpath(staging_root)):
        raise ImportBlocked(f"đích '{dst}' nằm ngoài staging (canonical)")
    h = hashlib.sha256()
    n = 0
    declared = m.size
    try:
        src = m.open()
    except RuntimeError as e:
        raise ImportBlocked(f"không mở được '{m.rel}': {e}")
    except NotImplementedError as e:
        raise ImportBlocked(f"phương pháp nén chưa hỗ trợ ('{m.rel}'): {e}")
    try:
        with src, open(dst, "xb") as out:
            for chunk in iter(lambda: src.read(1 * MB), b""):
                n += len(chunk)
                total[0] += len(chunk)
                if n > declared or n > limits.max_file_bytes:
                    raise ImportBlocked(f"'{m.rel}' giải nén ra nhiều hơn kích thước khai báo/giới hạn")
                if total[0] > limits.max_total_bytes:
                    raise ImportBlocked("tổng giải nén vượt giới hạn")
                h.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except zipfile.BadZipFile as e:
        raise ImportBlocked(f"'{m.rel}' hỏng (CRC/định dạng): {e}")
    except (EOFError, zlib.error) as e:
        raise ImportBlocked(f"'{m.rel}' giải nén lỗi: {e}")
    if n != declared:
        raise ImportBlocked(f"'{m.rel}' giải nén được {n} byte, khai báo {declared}")
    return n, h.hexdigest()


def _load_job(path: Path) -> Optional[Dict[str, Any]]:
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return j if isinstance(j, dict) and j.get("schema") == JOB_SCHEMA else None
    except (OSError, ValueError):
        return None


def _save_job(job_dir: Path, job: Dict[str, Any]) -> None:
    _write_json_atomic(job_dir / "job.json", job)


def apply_plan(plan: Dict[str, Any], cards, app_data, choices: Optional[Dict[str, str]] = None,
               conflicts: Optional[Dict[str, str]] = None, bios_ok=(), delete_source: bool = False,
               limits: Limits = DEFAULT_LIMITS, hashcache=None, catalog=None, bios=None,
               progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Thực hiện kế hoạch. `plan` = dict của build_plan (hoặc đọc từ file: cần 'card', 'units', 'scan_roots').
    Không bao giờ tự đổi thẻ: plan['card']['selected'] phải có. Trả kết quả từng unit."""
    catalog = catalog or load_catalog()
    bios = bios or load_bios_md5()
    choices, conflicts = choices or {}, conflicts or {}
    bios_all = "all" in set(bios_ok)
    bios_ok = set(bios_ok)
    res: Dict[str, Any] = {"success": False, "job_id": None, "message": "", "results": [], "installed": 0,
                           "already": 0, "blocked": 0, "failed": 0, "skipped": 0, "source_cleanup": [],
                           "card": None, "changed": False}
    sel = (plan.get("card") or {}).get("selected")
    if sel not in ("primary", "secondary") or not cards.get(sel):
        res["message"] = "Kế hoạch chưa chọn thẻ đích (cần người dùng chọn). Không cài gì."
        return res
    root = Path(cards[sel])
    res["card"] = {"selected": sel, "root": str(root)}
    plan_units = {u["id"]: u for u in plan.get("units", [])}
    if not plan_units:
        res["message"] = "Kế hoạch rỗng."
        return res
    ok, why, free = _usable(root, 0)
    if not ok:
        res["message"] = f"Thẻ đích không dùng được: {why}. Không cài gì."
        return res

    # dọn job chết trước (an toàn: chỉ dọn phần của chính LoongMate)
    recover(cards, app_data, only_cards=[sel])

    # quét lại nguồn: dùng nội dung THẬT bây giờ, không tin dữ liệu đường dẫn trong file kế hoạch
    roots = [Path(r) for r in plan.get("scan_roots", [])]
    sources, _errs = _enumerate(roots, cards, limits)
    fresh: Dict[str, Tuple[Source, Dict[str, Any]]] = {}
    try:
        for s in sources:
            try:
                for u in discover_units(s, catalog, bios, limits):
                    fresh[u["id"]] = (s, u)
            except Exception:
                continue
        todo: List[Tuple[Source, Dict[str, Any], Dict[str, Any]]] = []
        for uid, pu in plan_units.items():
            r = {"id": uid, "name": pu.get("name"), "status": "", "message": "", "dest": [], "bytes": 0}
            res["results"].append(r)
            if uid not in fresh:
                r.update(status="blocked", message="nguồn đã đổi hoặc không còn kể từ lúc lập kế hoạch — lập lại kế hoạch")
                continue
            s, u = fresh[uid]
            if u["status"] in ("blocked", "unsupported", "empty"):
                r.update(status="blocked", message="; ".join(u["reasons"]) or u["status"])
                continue
            if u["kind"] == "bios":
                if not (bios_all or uid in bios_ok):
                    r.update(status="skipped", message="BIOS cần xác nhận riêng (--bios-ok) — bỏ qua")
                    continue
            elif u["status"] == "needs_choice":
                c = choices.get(uid)
                if not c:
                    r.update(status="skipped", message="chưa chọn hệ máy — bỏ qua")
                    continue
                if c not in u["candidates"]:
                    r.update(status="blocked", message=f"hệ '{c}' không thuộc ứng viên {u['candidates']}")
                    continue
                u.update(status="ready", system=c, by="user", folder=catalog["by_id"][c].get("folder") or c)
            todo.append((s, u, r))
        needed = sum(u["bytes"] for _, u, _ in todo) + limits.reserve_bytes if todo else 0
        if todo and free < needed:
            for _, _, r in todo:
                r.update(status="failed", message=f"không đủ chỗ trên thẻ đích (trống {free // MB} MB, cần ~{needed // MB} MB)")
            res["message"] = "Thẻ đích không đủ chỗ. Không cài gì."
            _tally(res)
            return res
        if todo:
            _run_job(res, todo, root, sel, app_data, cards, catalog, conflicts, hashcache, limits, progress, bios)
        _tally(res)
        res["success"] = res["failed"] == 0 and res["blocked"] == 0 and (res["installed"] + res["already"]) > 0
        if not res["message"]:
            res["message"] = (f"Đã cài {res['installed']}, đã có {res['already']}, bị chặn {res['blocked']}, "
                              f"lỗi {res['failed']}, bỏ qua {res['skipped']}.")
        res["changed"] = res["installed"] > 0
        for s in sources:
            s.close()                       # Windows không cho đổi tên/xoá file zip còn mở
        res["source_cleanup"] = _cleanup_sources(res, fresh, delete_source)
    finally:
        for s in sources:
            s.close()
    return res


def _tally(res) -> None:
    for k in ("installed", "already", "blocked", "failed", "skipped"):
        res[k] = 0
    m = {"installed": "installed", "already_present": "already", "kept": "skipped", "skipped": "skipped",
         "cancelled": "skipped", "blocked": "blocked", "failed": "failed"}
    for r in res["results"]:
        k = m.get(r["status"])
        if k:
            res[k] += 1


def _run_job(res, todo, root: Path, card_id: str, app_data, cards, catalog, conflicts, hashcache, limits, progress, bios) -> None:
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job_dir = _work(root) / "staging" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    job = {"schema": JOB_SCHEMA, "job_id": job_id, "created_at": _now_iso(), "card": card_id, "card_root": str(root),
           "pid": os.getpid(), "boot_id": _boot_id(), "state": "staging", "units": []}
    _save_job(job_dir, job)
    res["job_id"] = job_id
    _ledger(app_data, "job_start", job=job_id, card=card_id, units=[u["id"] for _, u, _ in todo])
    lib = LibIndex(cards, hashcache)
    for i, (s, u, r) in enumerate(todo):
        if progress:
            progress({"phase": "unit", "i": i + 1, "n": len(todo), "name": u["name"]})
        rec = {"id": u["id"], "name": u["name"], "state": "pending", "files": [], "trash": [], "made_dirs": []}
        job["units"].append(rec)
        try:
            _do_unit(s, u, r, rec, job, job_dir, root, catalog, conflicts.get(u["id"], "keep"), lib, limits, app_data, bios)
        except ImportBlocked as e:
            r.update(status="blocked", message=str(e))
            _rollback_unit(rec, root)
            rec["state"] = "failed"
            _ledger(app_data, "unit_blocked", job=job_id, unit=u["id"], reason=str(e))
        except Exception as e:
            r.update(status="failed", message=f"{type(e).__name__}: {e}")
            _rollback_unit(rec, root)
            rec["state"] = "failed"
            _ledger(app_data, "unit_failed", job=job_id, unit=u["id"], reason=str(e))
        shutil.rmtree(job_dir / u["id"], ignore_errors=True)
        _save_job(job_dir, job)
    job["state"] = "done"
    _save_job(job_dir, job)
    _ledger(app_data, "job_done", job=job_id, installed=sum(1 for r in res["results"] if r["status"] == "installed"))
    shutil.rmtree(job_dir, ignore_errors=True)
    _fsync_dir(job_dir.parent)


def _do_unit(src: Source, u, r, rec, job, job_dir: Path, root: Path, catalog, conflict: str, lib: LibIndex,
             limits: Limits, app_data, bios) -> None:
    uid = u["id"]
    stage = job_dir / uid
    members = {m.rel.lower(): m for m in src.members}
    total = [0]
    rec["state"] = "staging"
    files = []
    if u["kind"] == "archive":                        # cài NGUYÊN zip (bộ ROM arcade)
        arch = Member(src.path.name, _safe_size(src.path), abspath=src.path)
        n, sha = _stage_member(arch, stage / src.path.name, stage, limits, total)
        files.append({"rel": src.path.name, "size": n, "sha256": sha, "staged": str(stage / src.path.name)})
    else:
        for f in u["files"]:
            m = members.get(f["member"].lower())
            if m is None:
                raise ImportBlocked(f"thiếu '{f['member']}' trong nguồn")
            dest_rel = f["dest_rel"]
            n, sha = _stage_member(m, stage.joinpath(*dest_rel.split("/")), stage, limits, total)
            files.append({"rel": dest_rel, "size": n, "sha256": sha, "staged": str(stage.joinpath(*dest_rel.split("/"))),
                          "role": f["role"]})
    _checkpoint("after_extract")
    # xác minh: đọc lại file đã ghi ra staging
    for f in files:
        if _sha256_path(Path(f["staged"])) != f["sha256"]:
            raise ImportBlocked(f"'{f['rel']}': đọc lại staging không khớp SHA-256 (thẻ ghi lỗi?)")
    for dp, _dn, _fn in os.walk(stage):
        _fsync_dir(Path(dp))
    rec["files"] = [{"rel": f["rel"], "size": f["size"], "sha256": f["sha256"], "staged": f["staged"], "tmp": None, "final": None,
                     "role": f.get("role", "")} for f in files]
    rec["state"] = "staged"
    job["state"] = "staged"
    _save_job(job_dir, job)
    _checkpoint("after_stage")

    if u["kind"] == "bios":
        rel = u["bios"].get("dest") or f"BIOS/{u['bios']['name']}"
        if not rel.startswith("BIOS/") or ".." in Path(rel).parts:
            raise ImportBlocked(f"đích BIOS lạ: {rel}")
        dest = root / rel
        ex = _exists_ci(dest)
        if ex is not None:
            if _md5_path(ex) == u["bios"]["md5"]:
                r.update(status="already_present", message=f"BIOS/{u['bios']['name']} đã có", dest=[str(ex)])
                rec["state"] = "skipped"
                return
            raise ImportBlocked(f"BIOS/{u['bios']['name']} đã có với nội dung khác — không ghi đè BIOS")
        _commit(rec, job, job_dir, root, [(rec["files"][0], dest)], [], app_data, u, r, catalog)
        return

    system = u["system"]
    folder = catalog["by_id"][system].get("folder") or system
    pairs = [(f, p) for f, (_, p) in zip(rec["files"], _final_paths(root, u, system, catalog))]
    # kiểm trùng bằng hash THẬT của file đã staging (không dùng hash ghi trong kế hoạch)
    present, conflicts, todo_pairs = 0, [], []
    for f, dst in pairs:
        ex = _exists_ci(dst)
        if ex is not None and os.path.getsize(ex) == f["size"] and _sha256_path(Path(ex)) == f["sha256"]:
            present += 1                                 # đúng file này đã nằm đúng chỗ: không đụng
            continue
        if lib.find_identical(folder, f["size"], f["sha256"]):
            present += 1                                 # cùng nội dung đã có ở chỗ khác trong thư viện
        todo_pairs.append((f, dst))                      # nếu cả bộ chưa đủ thì file này vẫn được cài cùng bộ
        if ex is not None:
            conflicts.append((f, dst, ex))               # tên đích bị chiếm bởi nội dung KHÁC
    if present == len(pairs):
        r.update(status="already_present", message="đã có trong thư viện (cùng SHA-256) — không cài lại")
        rec["state"] = "skipped"
        _ledger(app_data, "unit_present", job=job["job_id"], unit=uid)
        return
    pairs = todo_pairs
    trash: List[Tuple[Path, Path]] = []
    if conflicts:
        if conflict == "replace":
            for f, dst, ex in conflicts:
                trash.append((ex, _work(root) / "trash" / job["job_id"] / Path(ex).resolve().relative_to(root.resolve())))
        elif conflict == "copy":
            tag = _free_copy_tag(root, u, system, catalog)
            pairs = [(f, p) for f, (_, p) in zip(rec["files"], _final_paths(root, u, system, catalog, copy_tag=tag))]
        else:
            r.update(status="cancelled" if conflict == "cancel" else "kept",
                     message="đã có file cùng tên nhưng khác nội dung — " +
                             ("đã huỷ" if conflict == "cancel" else "giữ bản cũ (chọn replace/copy để cài)"),
                     dest=[str(ex) for _, _, ex in conflicts])
            rec["state"] = "skipped"
            return
    # đích phải nằm trong Roms/<hệ>/ của thẻ đích (không bao giờ Saves/States/BIOS)
    for _f, dst in pairs:
        if not is_safe_relative_path(root / "Roms" / folder, dst):
            raise ImportBlocked(f"đích '{dst}' nằm ngoài Roms/{folder}/")
    _commit(rec, job, job_dir, root, pairs, trash, app_data, u, r, catalog)


def _free_copy_tag(root: Path, u, system, catalog) -> str:
    """Nhãn bản sao còn trống: game nhiều file -> thư mục '<tên> (N)', file lẻ/zip -> '<stem> (N).<đuôi>'."""
    for n in range(2, 1000):
        tag = f"{u['name']} ({n})" if u["kind"] == "multi" else str(n)
        if not any(_exists_ci(p) for _, p in _final_paths(root, u, system, catalog, copy_tag=tag)):
            return tag
    raise ImportBlocked("không tìm được tên trống cho bản sao")


def _commit(rec, job, job_dir: Path, root: Path, pairs, trash, app_data, u, r, catalog) -> None:
    """Chuyển nguyên tử từ staging vào đích. Nhật ký ghi TRƯỚC khi đụng vào thư mục đích."""
    jid = job["job_id"]
    rec["files"] = [f for f, _ in pairs]          # chỉ các file thật sự được đặt vào đích (file đã đúng chỗ thì không đụng)
    for i, (f, dst) in enumerate(pairs):
        f["final"] = str(dst)
        f["tmp"] = str(dst.parent / f".{dst.name}.{jid[-6:]}.lmtmp")
    rec["trash"] = [{"from": str(a), "to": str(b)} for a, b in trash]
    # thư mục sẽ tạo
    made = []
    for _f, dst in pairs:
        d = dst.parent
        chain = []
        while not d.exists() and d != d.parent:
            chain.append(d)
            d = d.parent
        for c in reversed(chain):
            if str(c) not in made:
                made.append(str(c))
    rec["made_dirs"] = made
    rec["state"] = "committing"
    job["state"] = "committing"
    _save_job(job_dir, job)
    _checkpoint("before_commit")
    for _f, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
    for a, b in trash:
        b.parent.mkdir(parents=True, exist_ok=True)
        os.rename(a, b)
        _fsync_dir(a.parent)
    # tên tạm trong thư mục đích; playlist đổi tên cuối cùng để game không "hiện" khi còn thiếu track
    order = sorted(pairs, key=lambda t: t[0].get("role") == "playlist")
    for f, dst in order:
        if _exists_ci(dst) is not None:
            raise OSError(f"'{dst}' vừa xuất hiện — không ghi đè")
        os.rename(f["staged"], f["tmp"])
        _checkpoint("mid_commit")
    for d in {Path(f["staged"]).parent for f, _dst in order} | {dst.parent for _f, dst in pairs}:
        _fsync_dir(d)                                   # đợt rename thứ nhất xuống đĩa trước khi đổi sang tên cuối
    for f, dst in order:
        if _exists_ci(dst) is not None:
            raise OSError(f"'{dst}' vừa xuất hiện — không ghi đè")
        os.rename(f["tmp"], f["final"])
    for d in {dst.parent for _f, dst in pairs}:
        _fsync_dir(d)
    rec["state"] = "committed"
    _save_job(job_dir, job)
    try:
        _append_jsonl(_work(root) / "installed.jsonl", {
            "at": _now_iso(), "job": jid, "id": u["id"], "name": u["name"], "kind": u["kind"],
            "system": u["system"], "source": u["source"],
            "files": [{"path": str(Path(f["final"]).relative_to(root)), "size": f["size"], "sha256": f["sha256"]} for f in rec["files"]],
            "replaced": [x["from"] for x in rec["trash"]]})
        _ledger(app_data, "unit_committed", job=jid, unit=u["id"], name=u["name"], card=job["card"],
                files=[{"final": f["final"], "sha256": f["sha256"], "size": f["size"]} for f in rec["files"]],
                replaced=[x["from"] for x in rec["trash"]])
    except OSError:
        pass   # file đã vào đúng chỗ; sổ ghi lỗi không được làm hỏng game vừa cài
    r.update(status="installed", message=("đã cài" + (f" (thay {len(trash)} file cũ, bản cũ ở .loongmate-import/trash/)" if trash else "")),
             dest=[f["final"] for f in rec["files"]], bytes=sum(f["size"] for f in rec["files"]))


def _rollback_unit(rec: Dict[str, Any], root: Path) -> List[str]:
    """Đưa thư mục đích về trạng thái trước unit: xoá tên tạm/final do ta tạo (đúng SHA-256), trả bản cũ từ trash."""
    notes: List[str] = []
    if rec.get("state") not in ("committing", "staging", "staged", "pending"):
        return notes
    for f in rec.get("files", []):
        for key in ("tmp", "final"):
            p = f.get(key)
            if not p or not os.path.lexists(p):
                continue
            try:
                if key == "tmp" or (os.path.getsize(p) == f["size"] and _sha256_path(Path(p)) == f["sha256"]):
                    os.remove(p)
                    notes.append(f"xoá {p}")
                else:
                    notes.append(f"giữ nguyên {p} (không phải file do job tạo)")
            except OSError as e:
                notes.append(f"không xoá được {p}: {e}")
    for t in reversed(rec.get("trash", [])):
        try:
            if os.path.lexists(t["to"]) and not os.path.lexists(t["from"]):
                os.rename(t["to"], t["from"])
                notes.append(f"khôi phục {t['from']}")
        except OSError as e:
            notes.append(f"không khôi phục được {t['from']}: {e}")
    for d in reversed(rec.get("made_dirs", [])):
        try:
            os.rmdir(d)
        except OSError:
            pass
    return notes


def _cleanup_sources(res, fresh, delete_source: bool) -> List[Dict[str, Any]]:
    """File gốc trong Import/ chỉ rời chỗ khi unit (nhóm file lẻ) hoặc TOÀN BỘ unit của nguồn (zip/thư mục) đã
    cài hoặc đã có. Mặc định chuyển vào Import/.done/; xoá thật chỉ khi người dùng bật 'dọn file đã nhập'."""
    ok = {"installed", "already_present"}
    status_of = {r["id"]: r["status"] for r in res["results"]}
    groups: Dict[int, Tuple[Source, List[Dict[str, Any]]]] = {}
    for uid, (s, u) in fresh.items():
        groups.setdefault(id(s), (s, []))[1].append(u)
    out: List[Dict[str, Any]] = []
    for s, us in groups.values():
        if not s.in_import:
            continue
        if s.kind == "pool":
            moves = [o for u in us if status_of.get(u["id"]) in ok for o in u["origin"]]
        elif all(status_of.get(u["id"]) in ok for u in us):
            moves = [str(s.path)]
        else:
            moves = []
        for origin in dict.fromkeys(moves):
            origin = Path(origin)
            if not origin.exists():
                continue
            try:
                if delete_source:
                    shutil.rmtree(origin) if origin.is_dir() else origin.unlink()
                    out.append({"path": str(origin), "action": "deleted"})
                else:
                    done = origin.parent / DONE_DIR
                    done.mkdir(exist_ok=True)
                    dest = done / origin.name
                    if os.path.lexists(dest):
                        dest = done / f"{_stem(origin.name)}.{time.strftime('%Y%m%d-%H%M%S')}{_ext_raw(origin.name)}"
                    os.rename(origin, dest)
                    out.append({"path": str(origin), "action": "moved", "to": str(dest)})
            except OSError as e:
                out.append({"path": str(origin), "action": "failed", "message": str(e)})
    return out


# ======================================================================================
# jobs / recover
# ======================================================================================
def _job_dirs(cards, only_cards=None) -> List[Tuple[str, Path, Path]]:
    out = []
    for cid, c in cards.items():
        if not c or (only_cards and cid not in only_cards):
            continue
        base = _work(c) / "staging"
        try:
            for d in sorted(base.iterdir()):
                if d.is_dir():
                    out.append((cid, Path(c), d))
        except OSError:
            continue
    return out


def list_jobs(cards, app_data, recent: int = 10) -> Dict[str, Any]:
    pending = []
    for cid, c, d in _job_dirs(cards):
        j = _load_job(d / "job.json")
        alive = bool(j) and _pid_alive(j.get("pid"), j.get("boot_id", ""))
        pending.append({"job_id": d.name, "card": cid, "path": str(d), "state": (j or {}).get("state", "unreadable"),
                        "created_at": (j or {}).get("created_at"), "running": alive,
                        "units": [{"id": x.get("id"), "name": x.get("name"), "state": x.get("state")} for x in (j or {}).get("units", [])]})
    done = []
    try:
        lines = (Path(app_data) / LEDGER_NAME).read_text(encoding="utf-8").splitlines()
        for ln in lines[-400:]:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if e.get("event") in ("job_done", "job_recovered"):
                done.append(e)
    except OSError:
        pass
    return {"pending": pending, "recent": done[-recent:]}


def recover(cards, app_data, only_cards=None) -> Dict[str, Any]:
    """Job dở -> staging bị dọn; unit đang commit dở được hoàn tác (Roms/ không còn file nửa vời); unit đã
    commit xong được giữ. Job đang chạy thật (cùng boot, pid còn) thì bỏ qua."""
    reports = []
    for cid, c, d in _job_dirs(cards, only_cards):
        j = _load_job(d / "job.json")
        if j and _pid_alive(j.get("pid"), j.get("boot_id", "")) and j.get("pid") != os.getpid():
            reports.append({"job_id": d.name, "card": cid, "action": "running", "notes": ["đang chạy — bỏ qua"]})
            continue
        if j is None and (d / "job.json").exists():
            reports.append({"job_id": d.name, "card": cid, "action": "kept",
                            "notes": [f"job.json không đọc được hoặc thuộc phiên bản LoongMate khác (schema != {JOB_SCHEMA}): "
                                      f"giữ nguyên {d} để không mất file; mở bằng bản LoongMate đã tạo job, hoặc xoá tay nếu chắc chắn."]})
            continue
        notes: List[str] = []
        kept = 0
        for rec in (j or {}).get("units", []):
            if rec.get("state") == "committed":
                kept += 1
                continue
            notes += _rollback_unit(rec, c)
        try:
            shutil.rmtree(d)
            notes.append(f"dọn staging {d.name}")
        except OSError as e:
            notes.append(f"không dọn được {d}: {e}")
        try:
            _ledger(app_data, "job_recovered", job=d.name, card=cid, kept_units=kept, notes=notes)
        except OSError:
            pass
        reports.append({"job_id": d.name, "card": cid, "action": "cleaned", "kept_units": kept, "notes": notes})
    return {"recovered": reports}
