"""Leaf thật sự đọc những thư mục / đuôi file nào — lấy từ chính danh mục của bản Leaf đang cài, không đoán.

Nguồn: <thẻ Leaf>/.system/leaf/platforms/<p>/defaults/systems.json (Leaf 0.13.0): mỗi hệ có
  "patterns"            tên thư mục Roms/<...> Leaf nhận (vd. ARCADE: ["ARCADE", "Arcade", "arcade"])
  "extensions", "archive_extensions", "playlist_extensions"   đuôi file Leaf liệt kê thành game
Kiểm trên máy 2026-10-17: Leaf 0.13.0 KHÔNG có hệ CPS1/CPS2/CPS3/FBNEO -> 71 game trong các thư mục đó không hiện;
.gbc trong Roms/GB không hiện (GB chỉ nhận .gb). File này được Leaf thay khi cập nhật, nên luôn khớp phiên bản đang chạy.
Không đọc được (máy không phải Leaf, bản Leaf cũ) -> None: LoongMate dùng danh mục riêng như trước."""
import json
from pathlib import Path
from typing import Any, Dict, Optional, Set

# Thư mục arcade LoongMate từng coi là hệ riêng; Leaf chạy chúng trong ARCADE (core mặc định FBNeo, có FBA 2012 CPS1/2/3)
ARCADE_FAMILY = ("CPS1", "CPS2", "CPS3", "FBNEO", "FBA", "FINALBURN")


def load(sd, platform: str = "mlp1") -> Optional[Dict[str, Any]]:
    """{"folders": {TÊN THƯ MỤC IN HOA: id hệ Leaf}, "exts": {id: {".zip", ...}}} hoặc None."""
    try:
        data = json.loads((Path(sd) / ".system" / "leaf" / "platforms" / platform / "defaults" / "systems.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    systems = data.get("systems", data) if isinstance(data, dict) else data
    items = systems.items() if isinstance(systems, dict) else ((s.get("id"), s) for s in systems if isinstance(s, dict))
    folders: Dict[str, str] = {}
    exts: Dict[str, Set[str]] = {}
    for sid, s in items:
        if not isinstance(s, dict) or not sid:
            continue
        for p in s.get("patterns") or [sid]:
            if isinstance(p, str) and p:
                folders.setdefault(p.upper(), sid)
        e: Set[str] = set()
        for key in ("extensions", "archive_extensions", "playlist_extensions"):
            for x in s.get(key) or []:
                if isinstance(x, str) and x:
                    e.add("." + x.lower().lstrip("."))
        exts[sid] = e
    return {"folders": folders, "exts": exts} if folders else None


def system_of(leaf: Optional[Dict[str, Any]], folder: str) -> Optional[str]:
    return leaf["folders"].get(folder.upper()) if leaf else None


def shows_file(leaf: Optional[Dict[str, Any]], folder: str, ext: str) -> Optional[bool]:
    """Leaf có liệt kê file đuôi `ext` trong Roms/<folder> không; None = không biết (không đọc được danh mục Leaf,
    hoặc hệ của Leaf không giới hạn đuôi)."""
    sid = system_of(leaf, folder)
    if not leaf or sid is None:
        return None if not leaf else False
    allowed = leaf["exts"].get(sid) or set()
    return None if not allowed else ext.lower() in allowed


def overlay_catalog(systems_map: Dict[str, Any], leaf: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Thư mục arcade mà bản Leaf này không đọc -> coi là tên cũ của ARCADE (luật LEGACY_SYSTEM_FOLDER báo, và
    "Sửa hàng loạt > Gộp thư mục" chuyển file có nhật ký hoàn tác). Không đụng gì khi không đọc được danh mục Leaf
    hoặc Leaf không có ARCADE."""
    if not leaf or system_of(leaf, "ARCADE") is None:
        return systems_map
    arcade = systems_map.get("ARCADE")
    for name in ARCADE_FAMILY:
        if system_of(leaf, name) is not None:
            continue                                    # bản Leaf này vẫn đọc thư mục đó: giữ nguyên
        base = dict(arcade) if arcade else {"id": "ARCADE", "folder": "ARCADE", "extensions": [".zip", ".7z"]}
        base["folder"] = "ARCADE"
        base["leaf_hidden_from"] = name
        systems_map[name] = base
    return systems_map
