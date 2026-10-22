"""Leaf thật sự đọc những thư mục / đuôi file nào — lấy từ chính danh mục của bản Leaf đang cài, không đoán.

Nguồn: <thẻ Leaf>/.system/leaf/platforms/<p>/defaults/systems.json (Leaf 0.13.0): mỗi hệ có
  "patterns"            tên thư mục Roms/<...> Leaf nhận (vd. ARCADE: ["ARCADE", "Arcade", "arcade"])
  "extensions", "archive_extensions", "playlist_extensions"   đuôi file Leaf liệt kê thành game
Kiểm trên máy 2026-10-17: Leaf 0.13.0 KHÔNG có hệ CPS1/CPS2/CPS3/FBNEO -> 71 game trong các thư mục đó không hiện;
.gbc trong Roms/GB không hiện (GB chỉ nhận .gb). File này được Leaf thay khi cập nhật, nên luôn khớp phiên bản đang chạy.
Hệ do pak cài thêm (Pak Rat, vd J2ME.pak): <thẻ Leaf>/Apps/<p>/*.pak/pak.json, mục "provides.systems" cùng dạng trên; Leaf
ghép vào danh mục (catalog/effective.c), hệ gốc thắng khi trùng thư mục. Kiểm trên máy 2026-10-19: J2ME.pak 0.1.0 nhận
Roms/J2ME, đuôi .jar/.kjx; game để ở Roms/JAVA/Java/ thì Leaf không thấy.
Không đọc được (máy không phải Leaf, bản Leaf cũ) -> None: LoongMate dùng danh mục riêng như trước."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Thư mục arcade LoongMate từng coi là hệ riêng; Leaf chạy chúng trong ARCADE (core mặc định FBNeo, có FBA 2012 CPS1/2/3)
ARCADE_FAMILY = ("CPS1", "CPS2", "CPS3", "FBNEO", "FBA", "FINALBURN")


def _add_systems(items, folders: Dict[str, str], exts: Dict[str, Set[str]], names: Dict[str, str]) -> List[str]:
    added = []
    for sid, s in items:
        if not isinstance(s, dict) or not isinstance(sid, str) or not sid or sid in exts:
            continue                                    # hệ đã có (hệ gốc thắng hệ của pak cùng id)
        e: Set[str] = set()
        for key in ("extensions", "archive_extensions", "playlist_extensions"):
            for x in s.get(key) or []:
                if isinstance(x, str) and x:
                    e.add("." + x.lower().lstrip("."))
        exts[sid] = e
        names[sid] = s.get("name") if isinstance(s.get("name"), str) else sid
        for p in s.get("patterns") or [sid]:
            if isinstance(p, str) and p:
                folders.setdefault(p.upper(), sid)      # thư mục đã thuộc hệ khác thì giữ hệ đó
        added.append(sid)
    return added


def pak_manifests(sd, platform: str = "mlp1") -> List[Tuple[str, Dict[str, Any]]]:
    """(tên .pak, pak.json) của mọi app trong <thẻ Leaf>/Apps/<platform>/; file hỏng/không phải dict bị bỏ qua."""
    out = []
    try:
        paks = sorted(p for p in (Path(sd) / "Apps" / platform).iterdir() if p.name.endswith(".pak"))
    except OSError:
        return out
    for p in paks:
        try:
            m = json.loads((p / "pak.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict):
            out.append((p.name, m))
    return out


def load(sd, platform: str = "mlp1") -> Optional[Dict[str, Any]]:
    """{"folders": {TÊN THƯ MỤC IN HOA: id hệ}, "exts": {id: {".zip", ...}}, "names": {id: tên},
    "paks": {id hệ do pak thêm: tên .pak}} hoặc None (không đọc được danh mục gốc của Leaf)."""
    try:
        data = json.loads((Path(sd) / ".system" / "leaf" / "platforms" / platform / "defaults" / "systems.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    systems = data.get("systems", data) if isinstance(data, dict) else data
    items = systems.items() if isinstance(systems, dict) else ((s.get("id"), s) for s in systems if isinstance(s, dict))
    folders: Dict[str, str] = {}
    exts: Dict[str, Set[str]] = {}
    names: Dict[str, str] = {}
    _add_systems(items, folders, exts, names)
    if not folders:
        return None
    paks: Dict[str, str] = {}
    for pak, m in pak_manifests(sd, platform):
        provides = m.get("provides")
        sysl = provides.get("systems") if isinstance(provides, dict) else None
        if isinstance(sysl, list):
            for sid in _add_systems(((s.get("id"), s) for s in sysl if isinstance(s, dict)), folders, exts, names):
                paks[sid] = pak
    return {"folders": folders, "exts": exts, "names": names, "paks": paks}


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


def pak_catalog_entries(leaf: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Hệ do pak thêm, theo dạng mục của systems.json của LoongMate (id, name, folder, extensions có dấu chấm)."""
    out = []
    for sid, pak in ((leaf or {}).get("paks") or {}).items():
        folder = next((f for f, s in leaf["folders"].items() if s == sid), sid)
        out.append({"id": sid, "name": leaf["names"].get(sid, sid), "folder": folder,
                    "extensions": sorted(leaf["exts"].get(sid) or []), "leaf_pak": pak})
    return out


def overlay_catalog(systems_map: Dict[str, Any], leaf: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Thư mục arcade mà bản Leaf này không đọc -> coi là tên cũ của ARCADE (luật LEGACY_SYSTEM_FOLDER báo, và
    "Sửa hàng loạt > Gộp thư mục" chuyển file có nhật ký hoàn tác). Không đụng gì khi không đọc được danh mục Leaf
    hoặc Leaf không có ARCADE."""
    for entry in pak_catalog_entries(leaf):              # hệ do pak thêm (J2ME...): thư mục hợp lệ, không phải "lạ"
        systems_map.setdefault(entry["folder"].upper(), entry)
        systems_map.setdefault(entry["id"].upper(), entry)
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
