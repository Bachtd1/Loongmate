"""
LibraryDoctor module for LoongMate.
Performs read-only audit on ROMs, artwork, playlists (.cue, .m3u), BIOS, and cards.
Generates Fix Plans that only execute after explicit confirmation, moving invalid files
to Quarantine/<timestamp>/ rather than deleting.
"""

import os
import re
import json
import shutil
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

from engine.inventory import is_safe_relative_path
from engine import leafsys

# Tên game để ghép ảnh/save lệch tên: bỏ thẻ (USA) (Rev 1) [!], hậu tố " # SNES" và dấu câu.
_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
# File hệ thống của emulator nằm trong Saves/States nhưng KHÔNG thuộc game nào (không phải "save mồ côi").
_EMU_SYSTEM_FILE_RE = re.compile(r"^(pcsx-card\d*\.mcd|default\.cfg|dc_nvmem\.bin|vmu_save_[a-d]\d\.bin|.*\.(cfg|ini|nv|xml|json|txt|bmp|png|jpg))$", re.I)
_EMU_SYSTEM_DIRS = {"cfg", "xdg", "nvram", "memcard", "diff", "hi", "ctrlr", "inp", "snap"}
GROUP_SHOW = 8          # số tên in trong mô tả của một nhóm ghi chú

# Mã lỗi ổn định cho người dùng báo lỗi (docs/MA-LOI.md). id của issue kèm tên hệ/file nên đổi theo thư viện;
# code chỉ phụ thuộc loại vấn đề. Đã công bố thì không đổi nghĩa, chỉ thêm mã mới.
ISSUE_CODES = (
    ("CATALOG_UNAVAILABLE", "LM-CAT-001"),
    ("MISSING_DIR_", "LM-LIB-001"), ("UNKNOWN_SYSTEM_FOLDER_", "LM-LIB-002"), ("LEGACY_SYSTEM_FOLDER_", "LM-LIB-003"),
    ("INVALID_EXT_", "LM-LIB-004"), ("EXT_CHECK_SKIPPED_", "LM-LIB-005"), ("MULTIDISC_NO_M3U_", "LM-LIB-006"),
    ("DUP_ACROSS_CARDS_", "LM-LIB-007"),
    ("MISSING_ART_", "LM-ART-001"), ("ORPHAN_ART_", "LM-ART-002"),
    ("ORPHAN_SAVE_", "LM-SAVE-001"),
    ("MISSING_BIOS_ANYOF_", "LM-BIOS-001"), ("MISSING_BIOS_OPTIONAL_", "LM-BIOS-002"), ("BIOS_HLE_", "LM-BIOS-003"),
    ("MISSING_BIOS_", "LM-BIOS-004"),
    ("SEC_FS_NOT_FAT32", "LM-SD-001"), ("SEC_FS_UNKNOWN", "LM-SD-002"), ("SEC_NOT_WRITABLE", "LM-SD-003"),
    ("LOW_FREE_SPACE_", "LM-SD-004"),
    ("UNSAFE_PATH_", "LM-SCAN-001"), ("SCAN_ERROR_", "LM-SCAN-002"),
    ("TRAVERSAL_CUE_", "LM-CUE-001"), ("CUE_SELFNAME_FIX_", "LM-CUE-002"), ("CASE_MISMATCH_CUE_", "LM-CUE-003"),
    ("BROKEN_CUE_", "LM-CUE-004"),
    ("TRAVERSAL_M3U_", "LM-M3U-001"), ("BROKEN_M3U_", "LM-M3U-002"),
)
_CODES_LONGEST_FIRST = sorted(ISSUE_CODES, key=lambda pc: -len(pc[0]))


def issue_code(issue_id: str) -> str:
    for prefix, code in _CODES_LONGEST_FIRST:
        if issue_id.startswith(prefix):
            return code
    return "LM-000"


def _title_key(stem: str) -> str:
    s = stem.split(" # ")[0]
    s = _TAG_RE.sub("", s)
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def _names_line(names) -> str:
    names = sorted(names)
    more = len(names) - GROUP_SHOW
    return ", ".join(names[:GROUP_SHOW]) + (f" ... và {more} khác" if more > 0 else "")

# Matches: FILE "quoted path" ...  OR  FILE unquoted_path ...
CUE_FILE_LINE_RE = re.compile(r'^\s*FILE\s+(?:"([^"]*)"|(\S+))', re.MULTILINE | re.IGNORECASE)


def decode_playlist_bytes(data: bytes) -> str:
    """Giải mã nội dung .cue/.m3u: bỏ BOM, thử UTF-8 nghiêm ngặt rồi mới latin-1 (không bao giờ ném lỗi).
    Cue do công cụ Windows cũ ghi thường là latin-1; đọc UTF-8 với errors="ignore" sẽ làm RƠI ký tự có dấu
    nên tên track không còn khớp file thật trên đĩa."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")

# Non-ROM files that legitimately live inside Roms/<SYSTEM>/ next to the games.
# Verified against a real Leaf card (Pocket 1, 2026-10-02): .png box art beside
# CPS1/2/3 sets, .bin disc tracks referenced by .cue on PSX, .sav/.srm saves, and
# launcher metadata. Flagging these as "invalid ROMs" and quarantining them would
# break games and destroy saves, so they are never reported as bad extensions.
COMPANION_EXTENSIONS = {
    # artwork / media
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp",
    # disc track data and playlists referenced by .cue/.m3u
    ".bin", ".img", ".iso", ".sub", ".ccd", ".sbi", ".toc",
    # saves and states living beside the game
    ".sav", ".srm", ".state", ".ss0", ".ss1", ".mcr", ".mcd", ".eep", ".fla", ".sra",
    # metadata, cheats, docs, launcher bookkeeping
    ".db", ".txt", ".nfo", ".bak-loongmate", ".xml", ".json", ".dat", ".cht", ".ini", ".cfg", ".md",
}


class LibraryDoctor:
    def __init__(self, primary_path: Path, secondary_path: Optional[Path], systems_catalog_path: Path, userdata_path: Path):
        self.primary_path = primary_path.resolve()
        self.secondary_path = secondary_path.resolve() if secondary_path else None
        self.userdata_path = userdata_path.resolve()
        self.catalog_error: Optional[str] = None
        self.catalog_version: Optional[str] = None
        self.systems_catalog = self._load_systems(systems_catalog_path)
        self.platform = os.environ.get("PLATFORM") or "mlp1"
        self.leaf = leafsys.load(self.primary_path, self.platform)
        if self.systems_catalog:
            self.systems_catalog = leafsys.overlay_catalog(self.systems_catalog, self.leaf)
        self.quarantine_dir = self.userdata_path / "Quarantine"
        self._rom_ext_cache = None

    def _canon_folder(self, folder: str) -> str:
        info = self.systems_catalog.get(folder.upper())
        return (info.get("folder") or folder).upper() if info else folder.upper()

    def _card_root_for(self, item: Dict[str, Any]) -> Optional[Path]:
        cid = item.get("card_id")
        return self.primary_path if cid == "primary" else self.secondary_path if cid == "secondary" else None

    def _move_target(self, folder: str, ext: str) -> Optional[str]:
        """Thư mục Leaf DUY NHẤT hiện được file đuôi `ext` (vd. .gbc -> GBC); None nếu không có hoặc nhiều lựa chọn."""
        if not self.leaf:
            return None
        own = leafsys.system_of(self.leaf, folder)
        sids = sorted(sid for sid, e in self.leaf["exts"].items() if ext.lower() in e and sid != own)
        if len(sids) != 1:
            return None
        info = self.systems_catalog.get(sids[0].upper())
        cand = (info or {}).get("folder") or sids[0]
        return cand if leafsys.system_of(self.leaf, cand) == sids[0] else None

    def _leaf_missing_art(self) -> Optional[Dict[str, List[str]]]:
        """{thư mục: [tên game]} các game Leaf ĐANG HIỆN mà chưa có ảnh bìa (library.db của Leaf, chỉ đọc). None nếu
        không đọc được (máy không chạy Leaf / thử trên PC) -> dùng luật so tên cũ."""
        db = self.primary_path / ".umrk" / self.platform / "library.db"
        if not db.is_file():
            return None
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
            try:
                rows = con.execute("SELECT rom_path FROM games WHERE image_root_kind IS NULL").fetchall()
            finally:
                con.close()
        except Exception:
            return None
        out: Dict[str, List[str]] = {}
        for (rp,) in rows:
            parts = Path(str(rp)).parts
            if "Roms" in parts and parts.index("Roms") + 2 < len(parts):        # Roms/<hệ>/.../<file>
                out.setdefault(parts[parts.index("Roms") + 1], []).append(parts[-1])
        return out

    def _load_systems(self, catalog_path: Path) -> Dict[str, Any]:
        if catalog_path.exists():
            try:
                data = json.loads(catalog_path.read_text(encoding="utf-8"))
                self.catalog_version = data.get("catalog_version") or data.get("version")
                systems_map = {}
                for s in data.get("systems", []):
                    # Canonical lookup keys: folder, id, and every legacy alias (upper-case).
                    if s.get("folder"):
                        systems_map[s["folder"].upper()] = s
                    if s.get("id"):
                        systems_map[s["id"].upper()] = s
                    for alias in s.get("aliases", []):
                        systems_map.setdefault(alias.upper(), s)
                return systems_map
            except Exception as e:
                self.catalog_error = f"Không đọc được systems.json ({catalog_path}): {e}"
                return {}
        self.catalog_error = f"Không tìm thấy systems.json tại {catalog_path}"
        return {}

    def _resolve_card_root(self, absolute_path: str) -> Optional[Path]:
        """Returns the card root (primary/secondary) that contains the given file path."""
        try:
            p = Path(absolute_path).resolve()
        except (OSError, RuntimeError):
            return None
        if self.secondary_path and is_safe_relative_path(self.secondary_path, p):
            return self.secondary_path
        if is_safe_relative_path(self.primary_path, p):
            return self.primary_path
        return None

    def _all_rom_extensions(self):
        """Every ROM extension known to the catalog, across all systems.

        Used to tell "valid ROM in the wrong folder" (worth a note) apart from
        "genuinely unrecognised file" (worth a warning). Cached on first use.
        """
        if self._rom_ext_cache is None:
            exts = set()
            for info in self.systems_catalog.values():
                for e in info.get("extensions", []):
                    exts.add(e.lower())
            self._rom_ext_cache = exts
        return self._rom_ext_cache

    def audit(self, inventory: Dict[str, Any], storage_probe: Dict[str, Any]) -> Dict[str, Any]:
        """Runs the complete read-only checks and returns structured issues."""
        issues = []
        fixable_actions = []

        # 0. Catalog availability: if it failed to load, every extension/BIOS check below
        # is effectively disabled, so this must surface loudly rather than silently.
        if self.catalog_error:
            issues.append({
                "id": "CATALOG_UNAVAILABLE",
                "severity": "critical",
                "category": "Structure",
                "title": "Không tải được danh mục hệ máy (systems.json)",
                "description": f"{self.catalog_error}. Do đó kiểm tra phần mở rộng ROM và kiểm tra BIOS thiếu đã bị BỎ QUA cho toàn bộ thư viện.",
                "action_type": "manual_guide",
                "guide": "Kiểm tra file systems.json có tồn tại và đúng định dạng JSON hợp lệ, sau đó chạy lại Scan.",
                "safe": True
            })

        # 1. SD2 FAT32 / read-only / free space checks
        self._check_storage(storage_probe, issues)

        # 2. Folder layout check
        std_folders = ["Roms", "Images", "Saves", "States", "BIOS"]
        for card_label, card_root in [("Primary", self.primary_path), ("Secondary", self.secondary_path)]:
            if not card_root or not card_root.exists():
                continue
            for folder in std_folders:
                p = card_root / folder
                if not p.exists():
                    issues.append({
                        "id": f"MISSING_DIR_{card_label.upper()}_{folder.upper()}",
                        "severity": "warning",
                        "category": "Structure",
                        "title": f"Thiếu thư mục chuẩn: {folder} trên thẻ {card_label}",
                        "description": f"Đường dẫn '{p}' chưa tồn tại.",
                        "action_type": "auto_create_dir",
                        "target_path": str(p),
                        "safe": True
                    })
                    fixable_actions.append({
                        "type": "create_dir",
                        "path": str(p),
                        "label": f"Tạo thư mục {folder} trên {card_label}"
                    })

        # Gather all ROM stems, systems, and artwork
        roms_by_sys_stem = {}
        roms_list = []
        if inventory.get("primary"):
            roms_list.extend(inventory["primary"]["roms"])
        if inventory.get("secondary"):
            roms_list.extend(inventory["secondary"]["roms"])

        for r in roms_list:
            sys_id = r["system"].upper()
            roms_by_sys_stem.setdefault((sys_id, r["stem"].lower()), []).append(r)

        images_by_sys_stem = {}
        images_list = []
        if inventory.get("primary"):
            images_list.extend(inventory["primary"]["images"])
        if inventory.get("secondary"):
            images_list.extend(inventory["secondary"]["images"])

        for img in images_list:
            sys_id = img["system"].upper()
            images_by_sys_stem.setdefault((sys_id, img["stem"].lower()), []).append(img)

        # 3. Unknown / legacy system folders + extension check against systems.json
        reported_unknown_sys = set()
        reported_legacy_sys = set()
        all_sys_folders = set(r["system"].upper() for r in roms_list)
        for sys_id in all_sys_folders:
            sys_info = self.systems_catalog.get(sys_id)
            if sys_info is None:
                if sys_id in reported_unknown_sys:
                    continue
                reported_unknown_sys.add(sys_id)
                issues.append({
                    "id": f"UNKNOWN_SYSTEM_FOLDER_{sys_id}",
                    "severity": "warning",
                    "category": "Structure",
                    "title": f"Thư mục hệ máy không rõ: Roms/{sys_id}",
                    "description": (
                        f"Thư mục 'Roms/{sys_id}' không nằm trong danh mục Leaf (systems.json), nên "
                        f"đã BỎ QUA kiểm tra phần mở rộng và BIOS cho hệ này. Hãy xác nhận đây có phải "
                        f"tên thư mục Leaf hợp lệ hay không."
                    ),
                    "action_type": "info_only",
                    "safe": True
                })
            elif sys_info.get("folder", "").upper() != sys_id:
                # Matched via an alias, not the canonical folder name.
                if sys_id in reported_legacy_sys:
                    continue
                reported_legacy_sys.add(sys_id)
                n = sum(1 for r in roms_list if r["system"].upper() == sys_id)
                hidden = sys_info.get("leaf_hidden_from") == sys_id
                issues.append({
                    "id": f"LEGACY_SYSTEM_FOLDER_{sys_id}",
                    "severity": "warning" if hidden else "info",
                    "category": "Structure",
                    "title": (f"Leaf không hiện {n} game trong Roms/{sys_id}" if hidden
                              else f"Thư mục hệ máy dùng tên cũ: Roms/{sys_id}"),
                    "description": (
                        (f"Bản Leaf đang cài không có thư mục '{sys_id}' (game arcade nằm trong "
                         f"'{sys_info.get('folder')}', chạy bằng FBNeo hoặc core FBA 2012 CPS1/2/3), nên {n} file trong "
                         f"Roms/{sys_id} không hiện trong Leaf. " if hidden else
                         f"'{sys_id}' là tên thư mục cũ; tên chuẩn của Leaf là '{sys_info.get('folder')}'. ")
                        + f"Sửa: Library > Sửa hàng loạt > Gộp Roms/{sys_id} vào Roms/{sys_info.get('folder')} "
                          f"(file trùng tên khác nội dung được giữ nguyên; có Hoàn tác)."
                    ),
                    "action_type": "batch_fix",
                    "safe": True
                })

        # Systems whose extension list is not authoritative opt out via
        # "extension_check": false. Without this, an empty "extensions" list
        # would mark every ROM in that folder as invalid AND queue it for
        # quarantine -- a destructive false positive under `scan --fix`.
        ext_check_skipped = set()
        for r in roms_list:
            sys_id = r["system"].upper()
            sys_info = self.systems_catalog.get(sys_id)
            if sys_info:
                allowed_exts = [e.lower() for e in sys_info.get("extensions", [])]
                if sys_info.get("extension_check", True) is False or not allowed_exts:
                    ext_check_skipped.add(sys_id)
                    continue
                ext = r["extension"]
                if ext in allowed_exts or ext in COMPANION_EXTENSIONS:
                    continue
                card_id = r.get("card_id", "unknown")
                if ext in self._all_rom_extensions():
                    # Valid ROM extension filed under another system's folder (e.g. a .gbc in Roms/GB).
                    # Leaf's own catalog says whether it is listed there; if not, the game is invisible.
                    shown = leafsys.shows_file(self.leaf, r["system"], ext)
                    if shown is True:
                        continue        # danh mục của chính Leaf nhận đuôi này ở thư mục này (vd. .gbc trong GB): không phải lỗi
                    target = self._move_target(r["system"], ext) if shown is False else None
                    issues.append({
                        "id": f"INVALID_EXT_{sys_id}_{card_id}_{r['stem']}",
                        "severity": "warning" if shown is False else "info",
                        "category": "ROMs",
                        "title": (f"Leaf không hiện: {r['name']} (đuôi {ext} không thuộc Roms/{r['system']})"
                                  if shown is False else f"Có thể đặt nhầm thư mục: {r['name']} ({r['system']})"),
                        "description": (
                            (f"Leaf chỉ liệt kê {', '.join(sorted(self.leaf['exts'].get(leafsys.system_of(self.leaf, r['system']), [])))} "
                             f"trong Roms/{r['system']}, nên game này không có trong Leaf. "
                             + (f"Sửa: Library > Sửa hàng loạt > Chuyển sang Roms/{target}/ (cùng thẻ, có Hoàn tác)."
                                if target else "Chuyển file sang thư mục đúng hệ máy."))
                            if shown is False else
                            f"'{ext}' là phần mở rộng ROM hợp lệ nhưng không thuộc hệ {sys_id}. "
                            f"Nếu game vẫn chạy thì bỏ qua; nếu không, chuyển sang thư mục đúng hệ."
                        ),
                        "action_type": "batch_fix" if target else "info_only",
                        "file_path": r["absolute_path"],
                        "move_to": target,
                        "safe": True
                    })
                    continue
                issues.append({
                    "id": f"INVALID_EXT_{sys_id}_{card_id}_{r['stem']}",
                    "severity": "warning",
                    "category": "ROMs",
                    "title": f"Phần mở rộng lạ: {r['name']} ({r['system']})",
                    "description": (
                        f"Phần mở rộng '{ext}' không nằm trong danh sách hỗ trợ của hệ {sys_id}: "
                        f"{allowed_exts}. LoongMate CHỈ ghi nhận, KHÔNG tự chuyển file đi — thư mục "
                        f"Roms/ thường chứa cả ảnh bìa, track đĩa .bin, save và metadata, nên cách ly "
                        f"theo phần mở rộng rất dễ phá hỏng game hoặc làm mất save. Hãy tự kiểm tra "
                        f"rồi xoá/di chuyển nếu chắc chắn."
                    ),
                    "action_type": "info_only",
                    "file_path": r["absolute_path"],
                    "safe": True
                })

        # Report skipped extension checks instead of hiding them: a check that
        # silently does nothing is how the whole library looked "clean" before.
        for sys_id in sorted(ext_check_skipped):
            issues.append({
                "id": f"EXT_CHECK_SKIPPED_{sys_id}",
                "severity": "info",
                "category": "ROMs",
                "title": f"Chưa kiểm tra phần mở rộng cho hệ {sys_id}",
                "description": (
                    f"Thư mục Roms/{sys_id} là thư mục hợp lệ của Leaf, nhưng danh mục "
                    f"chưa có danh sách phần mở rộng đã kiểm chứng cho hệ này nên "
                    f"LoongMate bỏ qua bài kiểm tra định dạng (thà không kiểm còn hơn "
                    f"báo sai rồi đưa nhầm ROM vào Quarantine). Nếu bạn biết chắc danh "
                    f"sách phần mở rộng, hãy bổ sung vào systems.json và đặt "
                    f"\"extension_check\": true."
                ),
                "action_type": "info_only",
                "safe": True
            })

        # 4. Artwork check: Missing art & Orphan art (một ghi chú cho mỗi hệ máy, không phải mỗi game)
        # 4a. Games missing artwork: lấy đúng danh sách Leaf đang hiện mà chưa có ảnh (library.db) nếu đọc được.
        missing_art = self._leaf_missing_art()
        if missing_art is None:
            missing_art = {}
            for (sys_id, stem), rom_items in roms_by_sys_stem.items():
                r0 = rom_items[0]
                allowed = [e.lower() for e in (self.systems_catalog.get(sys_id) or {}).get("extensions", [])]
                if (sys_id, stem) in images_by_sys_stem or r0["extension"] in COMPANION_EXTENSIONS                         or (allowed and r0["extension"] not in allowed):
                    continue
                missing_art.setdefault(r0["system"], []).append(r0["name"])
        for folder, names in sorted(missing_art.items()):
            issues.append({
                "id": f"MISSING_ART_{folder.upper()}",
                "severity": "info",
                "category": "Artwork",
                "title": f"{len(names)} game {folder} chưa có ảnh bìa",
                "description": _names_line(names),
                "action_type": "manual_guide",
                "guide": ("Trong Leaf: Scrape Missing Artwork (tải ảnh bìa còn thiếu qua ScreenScraper, cần Wi-Fi), "
                          "hoặc Scrape Artwork cho từng game. Tự thêm: chép ảnh PNG vào Images/" + folder +
                          "/<tên file ROM>.png trên cùng thẻ với game."),
                "items": sorted(names),
                "safe": True
            })

        # 4b. Orphan artwork: ảnh lệch tên ghép được với ĐÚNG MỘT ROM đang có mặt -> sửa; còn lại gom nhóm.
        orphans = {}
        for (sys_id, stem), img_items in images_by_sys_stem.items():
            if (sys_id, stem) not in roms_by_sys_stem:
                for img in img_items:
                    orphans.setdefault(sys_id, []).append(img)
        by_title = {}
        for r in roms_list:
            if r["extension"] in COMPANION_EXTENSIONS:
                continue
            canon = self._canon_folder(r["system"])
            by_title.setdefault((canon, _title_key(r["stem"])), []).append(r)
        art_have = {(self._canon_folder(i["system"]), i["stem"].lower(), i.get("card_id")) for i in images_list}
        left = {}
        for sys_id, imgs in sorted(orphans.items()):
            canon = self._canon_folder(sys_id)
            for img in imgs:
                cands = by_title.get((canon, _title_key(img["stem"])), [])
                stems = {(c["stem"], c.get("card_id")) for c in cands}
                if len(stems) == 1:
                    rom = cands[0]
                    rom_card = self._card_root_for(rom)
                    if (canon, rom["stem"].lower(), rom.get("card_id")) in art_have:
                        fixable_actions.append({
                            "type": "quarantine_file", "path": img["absolute_path"],
                            "label": f"Ảnh thừa {img['name']} ({sys_id}): game {rom['name']} đã có ảnh"})
                        continue
                    if rom_card is not None:
                        dest = rom_card / "Images" / rom["system"] / (rom["stem"] + Path(img["name"]).suffix.lower())
                        fixable_actions.append({
                            "type": "relink_art", "path": img["absolute_path"], "dest": str(dest),
                            "label": f"Ghép ảnh {img['name']} -> {rom['name']}"})
                        continue
                left.setdefault(sys_id, []).append(img["name"])
        for sys_id, names in sorted(left.items()):
            issues.append({
                "id": f"ORPHAN_ART_{sys_id}",
                "severity": "info",
                "category": "Artwork",
                "title": f"{len(names)} ảnh bìa {sys_id} không khớp game nào",
                "description": (_names_line(names) + ". Có thể game đã bị xoá hoặc nằm trên thẻ đang rút ra, "
                                "nên LoongMate không tự xoá các ảnh này."),
                "action_type": "info_only",
                "items": sorted(names),
                "safe": True
            })

        # 5 & 6. Check .cue and .m3u files for broken references & path traversal attacks
        for r in roms_list:
            ext = r["extension"]
            rom_path = Path(r["absolute_path"])
            parent_dir = rom_path.parent
            card_root = self._resolve_card_root(r["absolute_path"])

            if ext == ".cue":
                self._check_cue_file(rom_path, parent_dir, card_root, issues, fixable_actions)
            elif ext == ".m3u":
                self._check_m3u_file(rom_path, parent_dir, card_root, issues, fixable_actions)

        # 7. Multi-disc games duplicated without .m3u playlist.
        # Tiêu chí dùng CHUNG với batchfix (>= 2 SỐ đĩa khác nhau trong cùng thư mục, một file đại
        # diện mỗi đĩa, sắp theo số đĩa): một đĩa nhiều track (.cue + .bin cùng "(Disc 1)") không phải
        # đa đĩa. Import trễ vì batchfix -> dedupe -> doctor (tránh vòng import).
        from engine.batchfix import find_multidisc_games
        for game in find_multidisc_games(roms_list):
            sys_id, title, folder = game["system"], game["title"], game["folder"]
            expected_m3u = f"{title}.m3u".lower()
            if any(r["system"].upper() == sys_id and r["name"].lower() == expected_m3u for r in roms_list):
                continue
            names = [game["discs"][n] for n in sorted(game["discs"])]
            issues.append({
                "id": f"MULTIDISC_NO_M3U_{sys_id}_{title}",
                "severity": "warning",
                "category": "Multi-Disc",
                "title": f"Game đa đĩa chưa gom playlist: {title} ({len(names)} đĩa)",
                "description": f"Game có các đĩa {names} nhưng chưa có file {title}.m3u, khiến launcher hiển thị trùng lặp.",
                "action_type": "create_m3u",
                "discs": names,
                "folder": folder,
                "m3u_name": f"{title}.m3u",
                "safe": True
            })
            fixable_actions.append({
                "type": "create_m3u",
                "folder": folder,
                "name": f"{title}.m3u",
                "lines": names,
                "label": f"Tạo playlist {title}.m3u tự động"
            })

        # 8. Missing BIOS check for detected active systems
        active_systems = set(r["system"].upper() for r in roms_list)
        detected_bios_names = set()
        for b_card in [inventory.get("primary"), inventory.get("secondary")]:
            if b_card:
                for b in b_card.get("bios", []):
                    detected_bios_names.add(b["name"].lower())

        for sys_id in active_systems:
            sys_info = self.systems_catalog.get(sys_id)
            if sys_info:
                required_bios = sys_info.get("bios", [])
                optional_bios = sys_info.get("bios_optional", [])
                missing = [b for b in required_bios if b.lower() not in detected_bios_names]
                # Nhóm thay thế nhau (vd. scph5500/5501/5502 theo vùng game): đủ nếu có ÍT NHẤT một file.
                # Chỉ báo thiếu khi thiếu CẢ nhóm; file còn lại trong nhóm không bị coi là thiếu.
                hle = sys_info.get("bios_hle")
                for group in ([] if hle else sys_info.get("bios_any_of", [])):
                    if group and not any(b.lower() in detected_bios_names for b in group):
                        issues.append({
                            "id": f"MISSING_BIOS_ANYOF_{sys_id}_{'_'.join(group)}",
                            "severity": "warning",
                            "category": "BIOS",
                            "title": f"Thiếu BIOS cho hệ máy {sys_id}: cần ít nhất một trong {', '.join(group)}",
                            "description": (
                                f"Hệ máy {sys_id} có ROM nhưng BIOS/ không có file nào trong nhóm "
                                f"{', '.join(group)}. Các file này thay thế nhau theo vùng của game nên chỉ "
                                f"cần một file đúng vùng. LoongMate chỉ lập danh sách kiểm tra, không tự tải BIOS."
                            ),
                            "action_type": "export_checklist",
                            "missing": list(group),
                            "any_of": list(group),
                            "safe": True
                        })
                missing_optional = [b for b in optional_bios if b.lower() not in detected_bios_names]
                if missing_optional and not sys_info.get("bios_hle"):     # có BIOS giả lập: gộp vào ghi chú BIOS_HLE
                    issues.append({
                        "id": f"MISSING_BIOS_OPTIONAL_{sys_id}",
                        "severity": "info",
                        "category": "BIOS",
                        "title": f"Chưa có BIOS tuỳ chọn cho hệ máy {sys_id}: {', '.join(missing_optional)}",
                        "description": (
                            f"Không bắt buộc — một số game cần để chạy đúng. Hệ máy {sys_id} vẫn chạy được "
                            f"phần lớn game khi thiếu {', '.join(missing_optional)}. "
                            f"LoongMate chỉ lập danh sách kiểm tra, không tự tải BIOS."
                        ),
                        "action_type": "info_only",
                        "missing": missing_optional,
                        "optional": True,
                        "safe": True
                    })
                if hle:
                    group_missing = [g for g in sys_info.get("bios_any_of", [])
                                     if g and not any(b.lower() in detected_bios_names for b in g)]
                    no_real = missing + missing_optional + [b for g in group_missing for b in g]
                    if no_real:
                        issues.append({
                            "id": f"BIOS_HLE_{sys_id}",
                            "severity": "info",
                            "category": "BIOS",
                            "title": f"{sys_id} đang chạy bằng BIOS giả lập (chưa có BIOS thật)",
                            "description": hle,
                            "action_type": "manual_guide",
                            "guide": ("Không cần làm gì nếu game chạy bình thường. Có BIOS bạn tự trích từ máy của mình: chép "
                                      "vào Import/ rồi Library > Nhập game (nhận bằng md5, đặt đúng thư mục)."),
                            "missing": no_real,
                            "optional": True,
                            "safe": True
                        })
                    missing, missing_optional = [], []
                if missing_optional:
                    pass
                if missing:
                    issues.append({
                        "id": f"MISSING_BIOS_{sys_id}",
                        "severity": "warning",
                        "category": "BIOS",
                        "title": f"Thiếu BIOS cho hệ máy {sys_id}: {', '.join(missing)}",
                        "description": f"Hệ máy {sys_id} có ROM nhưng thiếu BIOS tương ứng trong BIOS/. Lưu ý: LoongMate chỉ lập danh sách kiểm tra, không tự tải BIOS.",
                        "action_type": "export_checklist",
                        "missing": missing,
                        "safe": True
                    })

        # 9. Orphan saves/states (saves with no corresponding game)
        all_rom_stems = set(r["stem"].lower() for r in roms_list)
        saves_list = []
        if inventory.get("primary"):
            saves_list.extend(inventory["primary"]["saves"])
            saves_list.extend(inventory["primary"]["states"])
        if inventory.get("secondary"):
            saves_list.extend(inventory["secondary"]["saves"])
            saves_list.extend(inventory["secondary"]["states"])

        orphan_saves = {}
        for sv in saves_list:
            name = sv["name"]
            parts = {x.lower() for x in Path(sv["absolute_path"]).parent.parts[-3:]}
            if _EMU_SYSTEM_FILE_RE.match(name) or parts & _EMU_SYSTEM_DIRS:
                continue                       # thẻ nhớ chung, cấu hình, ảnh chụp/thumbnail của emulator
            # Strip state slots like .state1, .state.auto, etc.; "Game.gba.sav" -> "game.gba" -> "game"
            stem = sv["stem"].lower()
            base_stem = re.sub(r"\.state.*$", "", stem)
            cands = {stem, base_stem, base_stem.rsplit(".", 1)[0]}
            if not cands & all_rom_stems:
                orphan_saves.setdefault(Path(sv["absolute_path"]).parent.name, []).append(name)
        for folder, names in sorted(orphan_saves.items()):
            issues.append({
                "id": f"ORPHAN_SAVE_{folder}",
                "severity": "info",
                "category": "Saves",
                "title": f"{len(names)} save/state trong {folder} không còn game",
                "description": (_names_line(names) + ". Game có thể đã bị xoá, đổi tên, hoặc nằm trên thẻ đang rút ra. "
                                "LoongMate không bao giờ tự xoá save; SaveVault vẫn giữ bản sao lưu."),
                "action_type": "info_only",
                "items": sorted(names),
                "safe": True
            })

        # 10. Duplicate name across primary/secondary cards (no hashing — size+name only)
        self._check_dup_across_cards(inventory, issues)

        # 11. unsafe_paths / scan_errors surfaced by InventoryEngine
        self._check_inventory_flags(inventory, issues)

        for i in issues:
            i["code"] = issue_code(i["id"])

        severity_counts = {
            "critical": len([i for i in issues if i["severity"] == "critical"]),
            "warning": len([i for i in issues if i["severity"] == "warning"]),
            "info": len([i for i in issues if i["severity"] == "info"]),
        }

        doctor_status = "green"
        if severity_counts["critical"] > 0:
            doctor_status = "red"
        elif severity_counts["warning"] > 0:
            doctor_status = "yellow"

        return {
            "status": doctor_status,
            "catalog_version": self.catalog_version,
            "total_issues": len(issues),
            "severity_counts": severity_counts,
            "issues": issues,
            "fixable_actions": fixable_actions
        }

    def _check_storage(self, storage_probe: Dict[str, Any], issues: List[Dict]):
        """SD2 filesystem/writable checks plus free-space checks for both cards."""
        sec_storage = storage_probe.get("secondary")
        if sec_storage and sec_storage.get("mounted"):
            # Default to True when the key is absent, for backward compatibility with
            # callers/tests that pre-date the fs_detected contract.
            fs_detected = sec_storage.get("fs_detected", True)
            if fs_detected is True and not sec_storage.get("is_fat32"):
                issues.append({
                    "id": "SEC_FS_NOT_FAT32",
                    "severity": "critical",
                    "category": "Storage",
                    "title": "Thẻ SD2 không phải định dạng FAT32",
                    "description": f"Thẻ SD2 đang dùng filesystem '{sec_storage.get('filesystem')}'. Leaf yêu cầu FAT32 (vfat) để gộp thư viện chính xác.",
                    "action_type": "manual_guide",
                    "guide": "Sao lưu dữ liệu SD2 sang máy tính, định dạng thẻ SD2 về FAT32/vfat (32KB cluster size), sau đó sao chép lại dữ liệu."
                })
            elif fs_detected is False:
                issues.append({
                    "id": "SEC_FS_UNKNOWN",
                    "severity": "info",
                    "category": "Storage",
                    "title": "Chưa xác minh được định dạng thẻ SD2",
                    "description": (
                        "Không đọc được /proc/mounts nên chưa thể xác minh filesystem của SD2. "
                        # CHƯA KIỂM CHỨNG TRÊN MÁY THỰC TẾ: hành vi exFAT trên Leaf theo tài liệu là
                        # thẻ không mount được, nên nhiều khả năng biểu hiện là thẻ có trong khe nhưng
                        # không xuất hiện trong /proc/mounts, chứ không phải mount với fs sai.
                        "Lưu ý: theo tài liệu Leaf, thẻ exFAT thường KHÔNG mount được, nên nếu thẻ có "
                        "trong khe mà không thấy trong mounts thì nhiều khả năng là do sai định dạng. "
                        "Không tự kết luận sai định dạng khi chưa xác minh được, vì khuyên format nhầm "
                        "có thể làm mất dữ liệu."
                    ),
                    "action_type": "manual_guide",
                    "guide": "Trên thiết bị, chạy 'mount | grep SDCARD' (hoặc kiểm tra qua SSH/console) để xác minh filesystem thật trước khi quyết định format."
                })
            if not sec_storage.get("writable"):
                issues.append({
                    "id": "SEC_NOT_WRITABLE",
                    "severity": "critical",
                    "category": "Storage",
                    "title": "Thẻ SD2 bị khoá Read-Only",
                    "description": "Thẻ SD2 không có quyền ghi. Kiểm tra lẫy khoá Write-Protect trên adapter hoặc lỗi filesystem.",
                    "action_type": "manual_guide",
                    "guide": "Rút thẻ kiểm tra lẫy vật lý hoặc chạy 'fsck.vfat -a' trên thiết bị."
                })

        for card_id, label in [("primary", "PRIMARY"), ("secondary", "SECONDARY")]:
            st = storage_probe.get(card_id)
            if not st or not st.get("mounted"):
                continue
            free_mb = st.get("free_mb")
            if free_mb is None:
                continue
            if free_mb < 200:
                issues.append({
                    "id": f"LOW_FREE_SPACE_{label}",
                    "severity": "critical",
                    "category": "Storage",
                    "title": f"Thẻ {label.title()} sắp hết dung lượng ({free_mb}MB)",
                    "description": (
                        f"Thẻ {label.title()} chỉ còn {free_mb}MB trống, dưới ngưỡng an toàn 200MB. "
                        "Snapshot và quarantine có thể làm đầy thẻ nếu tiếp tục."
                    ),
                    "action_type": "manual_guide",
                    "guide": "Xoá bớt file không cần thiết hoặc chuyển ROM sang thẻ còn trống trước khi backup/sửa lỗi."
                })
            elif free_mb < 1000:
                issues.append({
                    "id": f"LOW_FREE_SPACE_{label}",
                    "severity": "warning",
                    "category": "Storage",
                    "title": f"Thẻ {label.title()} còn ít dung lượng ({free_mb}MB)",
                    "description": f"Thẻ {label.title()} còn dưới 1000MB trống. Cân nhắc dọn bớt trước khi backup nhiều.",
                    "action_type": "info_only",
                    "safe": True
                })

    def _check_dup_across_cards(self, inventory: Dict[str, Any], issues: List[Dict]):
        """Flags same (system, name) present on both primary and secondary. Name+size only, no hashing."""
        primary_roms = inventory.get("primary", {}).get("roms", []) if inventory.get("primary") else []
        secondary_roms = inventory.get("secondary", {}).get("roms", []) if inventory.get("secondary") else []
        if not primary_roms or not secondary_roms:
            return

        primary_index = {}
        for r in primary_roms:
            key = (r["system"].upper(), r["name"].lower())
            primary_index.setdefault(key, []).append(r)

        reported = set()
        for r in secondary_roms:
            key = (r["system"].upper(), r["name"].lower())
            if key not in primary_index:
                continue
            if key in reported:
                continue
            reported.add(key)
            sys_id, name_lower = key
            p_match = primary_index[key][0]
            same_size = p_match["size"] == r["size"]
            issues.append({
                "id": f"DUP_ACROSS_CARDS_{sys_id}_{r['stem']}",
                "severity": "warning",
                "category": "ROMs",
                "title": f"Trùng tên giữa hai thẻ: {r['name']} ({sys_id})",
                "description": (
                    f"File '{r['name']}' xuất hiện trên cả Primary và Secondary với cùng tên. "
                    + ("Cùng kích thước, nhiều khả năng là bản sao trùng." if same_size
                       else "Khác kích thước, đây là hai file khác nhau chỉ trùng tên, không phải bản sao.")
                ),
                "action_type": "manual_guide",
                "guide": ("Library > Dọn ROM trùng: băm SHA-256 để chắc là bản sao, chọn giữ bản trên thẻ nào; bản bỏ đi vào "
                          "Quarantine, save/ảnh bìa đi theo bản giữ lại." if same_size else
                          "Hai file khác nhau: giữ cả hai, hoặc đổi tên một bản nếu muốn Leaf hiện riêng."),
                "file_path": r["absolute_path"],
                "safe": True
            })

    def _check_inventory_flags(self, inventory: Dict[str, Any], issues: List[Dict]):
        """Surfaces unsafe_paths and scan_errors reported by InventoryEngine."""
        unsafe_paths = inventory.get("unsafe_paths", [])
        for i, up in enumerate(unsafe_paths):
            issues.append({
                "id": f"UNSAFE_PATH_{i}",
                "severity": "critical",
                "category": "Security",
                "title": "Phát hiện đường dẫn ngoài vùng an toàn khi quét",
                "description": f"Đường dẫn '{up.get('path')}' bị chặn: {up.get('reason')}",
                "action_type": "info_only",
                "safe": True
            })

        scan_errors = inventory.get("scan_errors", [])
        for i, err in enumerate(scan_errors):
            issues.append({
                "id": f"SCAN_ERROR_{i}",
                "severity": "warning",
                "category": "Structure",
                "title": "Lỗi khi quét thư viện",
                "description": f"{err}",
                "action_type": "info_only",
                "safe": True
            })

    def _check_cue_file(self, cue_path: Path, parent_dir: Path, card_root: Optional[Path], issues: List[Dict], fixable_actions: List[Dict]):
        try:
            content = decode_playlist_bytes(cue_path.read_bytes())
        except Exception:
            return

        sys_id = None
        card_id = None
        # Best-effort tag derivation for unique issue IDs (D10); does not affect matching logic.
        try:
            parts_hint = str(cue_path)
        except Exception:
            parts_hint = ""

        for match in CUE_FILE_LINE_RE.finditer(content):
            target = match.group(1) if match.group(1) is not None else match.group(2)
            if not target:
                continue

            if self._is_dangerous_path(target, parent_dir, card_root):
                issues.append({
                    "id": f"TRAVERSAL_CUE_{cue_path.parent.name}_{cue_path.name}",
                    "severity": "critical",
                    "category": "Security",
                    "title": f"Đường dẫn nguy hiểm trong file CUE: {cue_path.name}",
                    "description": f"File CUE tham chiếu '{target}' có dấu hiệu path traversal vượt khỏi thư mục.",
                    "action_type": "quarantine_file",
                    "file_path": str(cue_path),
                    "safe": True
                })
                fixable_actions.append({
                    "type": "quarantine_file",
                    "path": str(cue_path),
                    "label": f"Chuyển file CUE nguy hiểm {cue_path.name} vào Quarantine"
                })
                return

            target_file = parent_dir / target
            if not target_file.exists():
                # Check case insensitivity match
                candidate = None
                try:
                    for sibling in parent_dir.iterdir():
                        if sibling.name.lower() == target.lower():
                            candidate = sibling.name
                            break
                except OSError:
                    candidate = None

                # Một ánh xạ rõ ràng: file CUE tên X.cue trỏ tới một file không có,
                # nhưng ngay cạnh nó có X.<đuôi đã khai báo> — tức chính tên của file
                # CUE. Tài liệu cho phép sửa .cue khi chỉ có một ánh xạ rõ ràng.
                # KIỂM CHỨNG TRÊN THẺ THẬT 2026-10-02: Vi-HM_BTN_patched.cue trỏ tới
                # "HM_BTN_patched.bin" còn file thật là "Vi-HM_BTN_patched.bin".
                if candidate is None:
                    self_named = cue_path.with_suffix(Path(target).suffix).name
                    if (parent_dir / self_named).is_file():
                        issues.append({
                            "id": f"CUE_SELFNAME_FIX_{cue_path.parent.name}_{cue_path.name}",
                            "severity": "warning",
                            "category": "CUE",
                            "title": f"CUE trỏ sai tên track: {cue_path.name}",
                            "description": (
                                f"'{cue_path.name}' khai báo '{target}' (không tồn tại) nhưng ngay "
                                f"cạnh nó có '{self_named}' — đúng bằng tên của chính file CUE. "
                                f"Đây là một ánh xạ rõ ràng nên có thể sửa an toàn; LoongMate chỉ "
                                f"đổi tên tham chiếu bên trong .cue, không chạm vào file dữ liệu."
                            ),
                            "action_type": "fix_cue_case",
                            "file_path": str(cue_path),
                            "old_str": target,
                            "new_str": self_named,
                            "safe": True
                        })
                        fixable_actions.append({
                            "type": "fix_cue",
                            "path": str(cue_path),
                            "old_str": target,
                            "new_str": self_named,
                            "label": f"Sửa {cue_path.name} trỏ sang {self_named}"
                        })
                        return

                if candidate:
                    issues.append({
                        "id": f"CASE_MISMATCH_CUE_{cue_path.parent.name}_{cue_path.name}",
                        "severity": "warning",
                        "category": "CUE",
                        "title": f"Sai chữ hoa/thường trong {cue_path.name}: {target} vs {candidate}",
                        "description": f"Linux phân biệt hoa thường. CUE ghi '{target}' nhưng file trên đĩa là '{candidate}'.",
                        "action_type": "fix_cue_case",
                        "file_path": str(cue_path),
                        "old_str": target,
                        "new_str": candidate,
                        "safe": True
                    })
                    fixable_actions.append({
                        "type": "fix_cue",
                        "path": str(cue_path),
                        "old_str": target,
                        "new_str": candidate,
                        "label": f"Sửa tên đĩa trong {cue_path.name} thành {candidate}"
                    })
                else:
                    issues.append({
                        "id": f"BROKEN_CUE_{cue_path.parent.name}_{cue_path.name}",
                        "severity": "critical",
                        "category": "CUE",
                        "title": f"File CUE bị hỏng: thiếu đĩa/track '{target}'",
                        "description": f"File '{target}' được khai báo trong '{cue_path.name}' nhưng không tồn tại trên thẻ.",
                        "action_type": "quarantine_file",
                        "file_path": str(cue_path),
                        "safe": True
                    })
                    # Chỉ cue hỏng thật mới được đề xuất cách ly. Đặt ngoài nhánh else
                    # thì cue chỉ sai hoa/thường cũng bị cách ly oan (lỗi 2026-10-02).
                    fixable_actions.append({
                        "type": "quarantine_file",
                        "path": str(cue_path),
                        "label": f"Chuyển file CUE hỏng {cue_path.name} vào Quarantine"
                    })

    def _check_m3u_file(self, m3u_path: Path, parent_dir: Path, card_root: Optional[Path], issues: List[Dict], fixable_actions: List[Dict]):
        try:
            lines = m3u_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return

        for line in lines:
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#"):
                continue

            if self._is_dangerous_path(trimmed, parent_dir, card_root):
                issues.append({
                    "id": f"TRAVERSAL_M3U_{m3u_path.parent.name}_{m3u_path.name}",
                    "severity": "critical",
                    "category": "Security",
                    "title": f"Nguy cơ Path Traversal trong playlist: {m3u_path.name}",
                    "description": f"Dòng '{trimmed}' tham chiếu ra ngoài thư mục an toàn.",
                    "action_type": "quarantine_file",
                    "file_path": str(m3u_path),
                    "safe": True
                })
                fixable_actions.append({
                    "type": "quarantine_file",
                    "path": str(m3u_path),
                    "label": f"Chuyển playlist nguy hiểm {m3u_path.name} vào Quarantine"
                })
                return

            disc_path = parent_dir / trimmed
            if not disc_path.exists():
                issues.append({
                    "id": f"BROKEN_M3U_{m3u_path.parent.name}_{m3u_path.name}",
                    "severity": "critical",
                    "category": "Playlist",
                    "title": f"Playlist M3U hỏng: thiếu đĩa '{trimmed}'",
                    "description": f"File '{m3u_path.name}' tham chiếu đến đĩa '{trimmed}' không tồn tại.",
                    "action_type": "quarantine_file",
                    "file_path": str(m3u_path),
                    "safe": True
                })
                fixable_actions.append({
                    "type": "quarantine_file",
                    "path": str(m3u_path),
                    "label": f"Chuyển playlist hỏng {m3u_path.name} vào Quarantine"
                })

    def _is_dangerous_path(self, target: str, parent_dir: Path, card_root: Optional[Path]) -> bool:
        """Fast string check (works even for non-existent targets) plus canonicalize-based
        check (catches symlinks and Windows drive-letter paths like C:\\...)."""
        if ".." in target or target.startswith("/") or target.startswith("\\"):
            return True
        if re.match(r"^[A-Za-z]:[\\/]", target):
            return True

        if card_root is not None:
            candidate = parent_dir / target
            if not is_safe_relative_path(card_root, candidate):
                return True

        return False

    def execute_fix(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a confirmed fix safely, moving to quarantine rather than deleting."""
        action_type = action.get("type")
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        allowed_roots = [r for r in [self.primary_path, self.secondary_path, self.userdata_path] if r]
        for key in ("path", "folder"):
            raw = action.get(key)
            if not raw:
                continue
            try:
                candidate = Path(raw)
            except (OSError, ValueError):
                return {"success": False, "message": f"Đường dẫn không hợp lệ: {raw}"}
            if not any(is_safe_relative_path(root, candidate) for root in allowed_roots):
                return {"success": False, "message": f"Đường dẫn ngoài vùng cho phép: {raw}"}

        try:
            if action_type == "create_dir":
                p = Path(action["path"])
                p.mkdir(parents=True, exist_ok=True)
                return {"success": True, "message": f"Đã tạo thư mục {p}"}

            elif action_type == "quarantine_file":
                src = Path(action["path"])
                if not src.exists():
                    return {"success": False, "message": "File không tồn tại"}

                src_card_root = self._resolve_card_root(str(src))
                crossed_card = False
                if src_card_root == self.secondary_path and self.secondary_path:
                    q_base = self.secondary_path / ".userdata" / "LoongMate" / "Quarantine"
                elif src_card_root == self.primary_path:
                    q_base = self.userdata_path / "Quarantine"
                else:
                    q_base = self.userdata_path / "Quarantine"
                    crossed_card = True

                # Block if the destination card is near-full (free space safety threshold).
                try:
                    dest_total, dest_used, dest_free = shutil.disk_usage(q_base.parent if q_base.parent.exists() else self.userdata_path)
                    dest_free_mb = dest_free / (1024 * 1024)
                    if dest_free_mb < 200:
                        return {
                            "success": False,
                            "message": f"Thẻ đích chỉ còn {round(dest_free_mb, 1)}MB trống (dưới 200MB). Cần giải phóng dung lượng trước khi quarantine."
                        }
                except OSError:
                    pass

                # Source read-only check: try a quick writable probe on the parent dir.
                if not os.access(src.parent, os.W_OK):
                    return {"success": False, "message": f"Thẻ chứa '{src}' đang Read-Only, không thể di chuyển file."}

                q_folder = q_base / timestamp
                q_folder.mkdir(parents=True, exist_ok=True)
                dst = q_folder / src.name
                shutil.move(str(src), str(dst))
                # Update quarantine index
                idx_file = self.userdata_path / "quarantine-index.json"
                index_data = []
                if idx_file.exists():
                    try:
                        index_data = json.loads(idx_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                index_data.append({
                    "timestamp": timestamp,
                    "original_path": str(src),
                    "quarantine_path": str(dst),
                    "size": dst.stat().st_size
                })
                idx_file.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")
                message = f"Đã chuyển an toàn sang {dst}"
                if crossed_card:
                    message += " (Lưu ý: đã chuyển chéo thẻ vì không xác định được thẻ nguồn)"
                return {"success": True, "message": message}

            elif action_type == "relink_art":
                src, dest = Path(action["path"]), Path(action["dest"])
                if not any(is_safe_relative_path(root, dest) for root in allowed_roots) or dest.parent.name == "" \
                        or "Images" not in dest.parts:
                    return {"success": False, "message": f"Đích không hợp lệ: {dest}"}
                if not src.is_file():
                    return {"success": False, "message": "Ảnh nguồn không còn"}
                if dest.exists():
                    return {"success": False, "message": f"Đã có {dest.name}: không ghi đè"}
                from engine.artwork import copy_file_hashed
                dest.parent.mkdir(parents=True, exist_ok=True)
                copy_file_hashed(src, dest)
                q = self.execute_fix({"type": "quarantine_file", "path": str(src)})
                return {"success": True, "message": f"Đã ghép ảnh bìa: {dest.name}"
                        + ("" if q.get("success") else f" (bản cũ chưa chuyển vào Quarantine: {q.get('message')})")}

            elif action_type == "create_m3u":
                folder = Path(action["folder"])
                m3u_file = folder / action["name"]
                m3u_file.write_text("\n".join(action["lines"]) + "\n", encoding="utf-8")
                return {"success": True, "message": f"Đã tạo playlist đa đĩa {m3u_file.name}"}

            elif action_type == "fix_cue":
                # Dùng chung với batchfix (ghi bytes, giữ CRLF, sao lưu .cue.bak-loongmate, từ chối khi
                # không thấy dòng cần sửa). Import trễ để tránh vòng import doctor <-> batchfix.
                from engine.batchfix import apply_cue_fix
                cue_file = Path(action["path"])
                r = apply_cue_fix(cue_file, [{"old": action["old_str"], "new": action["new_str"]}],
                                  [x for x in (self.primary_path, self.secondary_path) if x])
                if not r["ok"]:
                    return {"success": False, "message": r["reason"]}
                return {"success": True, "backup": r["backup"],
                        "message": f"Đã chuẩn hoá đường dẫn file CUE: {cue_file.name} (bản gốc: {Path(r['backup']).name})"}

            return {"success": False, "message": f"Loại hành động không hỗ trợ: {action_type}"}

        except Exception as e:
            return {"success": False, "message": f"Lỗi thực hiện: {str(e)}"}
