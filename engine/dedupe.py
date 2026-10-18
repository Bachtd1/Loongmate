"""
Dedupe / safe ROM deletion for LoongMate.

Why this exists: Leaf and the OS give the user no way to delete a ROM, and the
primary card is ~97% full. This module lets LoongMate do it, but a deletion is
never a bare os.remove:

  * only files inside Roms/ of the primary/secondary card (plus, for an explicit
    single-game request, that game's cover art in Images/) can be touched;
  * a duplicate is only removed after SHA-256 proves a kept copy has identical
    content, re-hashed immediately before the delete (the file may have changed
    since the scan); the last copy of a content is never removed;
  * multi-file games (.cue/.bin, .m3u + discs, .gdi, .ccd) are never auto-deduped
    because deleting one referenced file silently breaks the game;
  * saves/states are never deleted;
  * every deletion is written (fsync) to an append-only ledger BEFORE the file
    is removed, so a power cut still leaves a trail.

Hashing is expensive on a handheld, so only files that share a size with another
file are hashed (optionally capped by a total byte budget).
"""

import glob
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.doctor import CUE_FILE_LINE_RE, decode_playlist_bytes
from engine.inventory import compute_sha256, is_safe_relative_path

LEDGER_NAME = "deletion-ledger.jsonl"
PLAYLIST_EXTS = {".cue", ".m3u"}
MULTIFILE_EXTS = {".cue", ".m3u", ".gdi", ".ccd"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
MB = 1024 * 1024


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _key(p) -> str:
    """Case-insensitive path key: playlists written on Windows often differ in case."""
    return os.path.normpath(str(p)).lower()


def _read_refs(pl: Path) -> List[Path]:
    """Paths a .cue/.m3u refers to, relative to the playlist's folder (unresolved)."""
    try:
        text = decode_playlist_bytes(pl.read_bytes())
    except OSError:
        return []
    if pl.suffix.lower() == ".cue":
        targets = [a if a is not None else b for a, b in CUE_FILE_LINE_RE.findall(text)]
    else:
        targets = [ln.strip() for ln in text.splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
    return [pl.parent / t.replace("\\", "/") for t in targets if t]


def _expand(pl: Path, index: Dict[str, Path], _seen=None):
    """Every file a playlist needs, recursively (.m3u -> .cue -> .bin).
    Returns (found paths, missing reference strings)."""
    seen = _seen if _seen is not None else {_key(pl)}
    found: List[Path] = []
    missing: List[str] = []
    for ref in _read_refs(pl):
        k = _key(ref)
        real = index.get(k)
        if real is None:
            missing.append(str(ref.name))
            continue
        if k in seen:
            continue
        seen.add(k)
        found.append(real)
        if real.suffix.lower() in PLAYLIST_EXTS:
            f2, m2 = _expand(real, index, seen)
            found.extend(f2)
            missing.extend(m2)
    return found, missing


def _save_stems(card: Dict[str, Any]) -> set:
    stems = set()
    for sv in card.get("saves", []) + card.get("states", []):
        s = sv.get("stem", "").lower()
        stems.add(s)
        stems.add(re.sub(r"\.state.*$", "", s))
    return stems


def _free_mb(root: Path, cache: Dict[str, int]) -> int:
    k = str(root)
    if k not in cache:
        try:
            cache[k] = shutil.disk_usage(root).free // MB
        except OSError:
            cache[k] = 0
    return cache[k]


def _referencing_playlist(fp: Path) -> Optional[Path]:
    """A .cue/.m3u in the same folder that refers to fp, if any (last line of defence)."""
    k = _key(fp)
    try:
        for pl in fp.parent.iterdir():
            if pl.suffix.lower() in PLAYLIST_EXTS and k in {_key(r) for r in _read_refs(pl)}:
                return pl
    except OSError:
        pass
    return None


def _ledger_append(app_data: Path, entry: Dict[str, Any]) -> None:
    """Append one JSON line and fsync. Raises OSError if it cannot be made durable."""
    app_data.mkdir(parents=True, exist_ok=True)
    with open(app_data / LEDGER_NAME, "ab") as f:
        f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# find duplicates
# --------------------------------------------------------------------------
def find_duplicates(inventory: Dict[str, Any], catalog: Dict[str, Any],
                    primary_path, secondary_path,
                    max_hash_bytes: Optional[int] = None, prefer_card: str = "auto",
                    compare: bool = False, cache: Optional[Any] = None) -> Dict[str, Any]:
    """Finds byte-identical single-file ROMs (same system, any card) via SHA-256.

    prefer_card: 'auto' | 'primary' | 'secondary' -- which card's copy to keep when nothing
    more important decides (a playlist reference or a save/state always wins).
    compare=True also returns the reclaimable bytes per card for each choice.

    `catalog` is the doctor's systems map (UPPER folder/id/alias -> system info).

    cache: optional engine.hashcache.HashCache. It is used ONLY to find candidates faster (a file whose
    path/size/mtime still match a trusted entry is not re-read). The report gains cache_hits/cache_misses.
    A wrong cache can at worst make this propose a wrong pair: execute_deletion never reads the cache
    and re-hashes both the removed and the kept file, so such a proposal is refused there.
    """
    if prefer_card not in ("auto", "primary", "secondary"):
        raise ValueError("prefer_card phải là 'auto', 'primary' hoặc 'secondary'")
    cards = {}
    for cid in ("primary", "secondary"):
        c = inventory.get(cid)
        if c:
            cards[cid] = c
    roots = {"primary": Path(primary_path) if primary_path else None,
             "secondary": Path(secondary_path) if secondary_path else None}

    roms = [r for c in cards.values() for r in c["roms"]]
    index = {_key(r["absolute_path"]): Path(r["absolute_path"]) for r in roms}
    size_of = {_key(r["absolute_path"]): r["size"] for r in roms}

    # Files that belong to a multi-file game are protected from auto-dedupe.
    protected = set()
    for r in roms:
        ext, p = r["extension"], Path(r["absolute_path"])
        if ext in PLAYLIST_EXTS:
            protected.update(_key(f) for f in _expand(p, index)[0])
        elif ext == ".gdi":  # CHƯA KIỂM CHỨNG: .gdi nằm trong thư mục riêng; bảo vệ cả thư mục cho chắc
            protected.update(k for k, f in index.items() if f.parent == p.parent)
        elif ext == ".ccd":
            protected.update(k for k, f in index.items()
                             if f.parent == p.parent and f.stem.lower() == p.stem.lower())

    def canon(sys_name: str) -> str:
        info = catalog.get(sys_name.upper())
        return info["folder"].upper() if info and info.get("folder") else sys_name.upper()

    eligible = []
    for r in roms:
        info = catalog.get(r["system"].upper())
        exts = [e.lower() for e in (info or {}).get("extensions", [])]
        if (not info or not exts or info.get("extension_check", True) is False
                or r["extension"] not in exts or r["extension"] in MULTIFILE_EXTS or r["size"] <= 0):
            continue
        eligible.append(r)

    by_size: Dict[int, List[Dict]] = {}
    for r in eligible:
        by_size.setdefault(r["size"], []).append(r)

    hashed_files = hashed_bytes = skipped_budget = cache_hits = cache_misses = 0
    hash_errors: List[Dict[str, str]] = []
    by_hash: Dict[tuple, List[Dict]] = {}
    for size in sorted(by_size):
        cand = by_size[size]
        if len(cand) < 2:
            continue
        if max_hash_bytes is not None and hashed_bytes + size * len(cand) > max_hash_bytes:
            skipped_budget += len(cand)
            continue
        for r in cand:
            try:
                if cache is not None:
                    before = cache.hits
                    h = cache.hash(r["absolute_path"], compute_sha256)
                    if cache.hits > before:
                        cache_hits += 1
                    else:
                        cache_misses += 1
                else:
                    h = compute_sha256(Path(r["absolute_path"]))
            except OSError as e:
                hash_errors.append({"path": r["absolute_path"], "reason": str(e)})
                continue
            hashed_files += 1
            hashed_bytes += size
            by_hash.setdefault((canon(r["system"]), h), []).append(r)

    if cache is not None:
        cache.flush()

    save_stems = {cid: _save_stems(c) for cid, c in cards.items()}
    art = {(cid, canon(i["system"]), i["stem"].lower())
           for cid, c in cards.items() for i in c.get("images", [])}
    free_cache: Dict[str, int] = {}
    card_names = {"primary": "SD1 (Primary)", "secondary": "SD2 (Secondary)"}

    def describe(r: Dict) -> Dict[str, Any]:
        cid = r["card_id"]
        root = roots.get(cid)
        stem = r["stem"].lower()
        d = {"path": r["absolute_path"], "card_id": cid, "system": r["system"],
             "name": r["name"], "size": r["size"],
             "has_save": stem in save_stems.get(cid, ()),
             "has_art": (cid, canon(r["system"]), stem) in art,
             # referenced by a .cue/.m3u: may be KEPT, never removed (removing breaks the playlist)
             "referenced": _key(r["absolute_path"]) in protected,
             "standard_folder": r["system"].upper() == canon(r["system"])
                                and catalog.get(r["system"].upper()) is not None,
             "free_mb": _free_mb(root, free_cache) if root else 0}
        return d

    art_note = (" Lưu ý: ảnh bìa của game nằm ở thẻ kia. "
                "# CHƯA KIỂM CHỨNG: Leaf có tìm ảnh bìa chéo thẻ không.")

    def build(pref: str):
        # Ordered criteria; each returns a sortable value (smaller = better).
        # Save always comes first; the user's card choice slots in right after it.
        criteria = [
            ("save", lambda d: not d["has_save"],
             lambda k, o: "bản này có save/state đi kèm (xoá bản kia thì save của bạn không bị mồ côi)"),
            ("art", lambda d: not d["has_art"],
             lambda k, o: "bản này có ảnh bìa khớp trong Images/"),
            ("std", lambda d: not d["standard_folder"],
             lambda k, o: f"bản này nằm trong thư mục hệ máy chuẩn Roms/{k['system']} (bản kia dùng tên legacy Roms/{o['system']})"),
            ("free", lambda d: -d["free_mb"],
             lambda k, o: f"bản kia nằm trên thẻ đang chật hơn ({o['free_mb']} MB trống so với {k['free_mb']} MB) nên xoá bớt ở đó"),
            ("len", lambda d: len(d["path"]),
             lambda k, o: "các tiêu chí khác ngang nhau, giữ đường dẫn ngắn hơn"),
        ]
        if pref != "auto":
            criteria.insert(1, ("card", lambda d: d["card_id"] != pref,
                                lambda k, o: f"bạn chọn giữ bản ở thẻ {card_names[pref]}"))
        out_groups = []
        for (_, sha), members in sorted(by_hash.items(), key=lambda kv: kv[0]):
            if len(members) < 2:
                continue
            ds = sorted((describe(m) for m in members), key=lambda d: d["path"])
            refd = [d for d in ds if d["referenced"]]
            pool = refd or ds
            keep = min(pool, key=lambda d: tuple(c[1](d) for c in criteria) + (d["path"],))
            also_kept = [d for d in refd if d is not keep]
            rest = [d for d in ds if d is not keep and not d["referenced"]]
            # Bản có save/state đi kèm KHÔNG BAO GIỜ bị xoá, kể cả khi bản giữ bị ép
            # (vì được playlist tham chiếu). GẶP THẬT 2026-10-02: Chrono Trigger trên SD1
            # có save, bản SD2 nằm trong .m3u nên bị ép làm bản giữ, và bản đang chơi bị
            # đề xuất xoá. Chạy qua .m3u thì RetroArch đặt tên save theo playlist, nên save
            # của bản .chd cũ rất có thể không được nhận lại — tức mất tiến trình chơi.
            # Thà không thu hồi được game đó còn hơn.
            saved = [d for d in rest if d["has_save"]]
            if saved:
                also_kept = also_kept + saved
                rest = [d for d in rest if not d["has_save"]]
            if not rest:
                continue
            wrong_card = pref != "auto" and keep["card_id"] != pref
            if refd:
                reason = ("bản này đang được playlist (.cue/.m3u) tham chiếu — xoá nó sẽ làm hỏng game"
                          + (f"; giữ bản ở {card_names[keep['card_id']]} dù bạn chọn {card_names[pref]}" if wrong_card else ""))
            else:
                reason = "các bản hoàn toàn ngang nhau, giữ theo thứ tự đường dẫn"
                for name, val, text in criteria:
                    other = next((o for o in rest if val(o) != val(keep)), None)
                    if other is None:
                        continue
                    reason = text(keep, other)
                    if name == "save" and wrong_card:
                        reason = (f"giữ bản ở {card_names[keep['card_id']]} dù bạn chọn {card_names[pref]}, "
                                  "vì bản này có save đi kèm")
                    elif name == "card" and not keep["has_art"] and any(o["has_art"] for o in rest):
                        reason += "." + art_note
                    break
            out_groups.append({"sha256": sha, "size": keep["size"], "keep": keep, "keep_reason": reason,
                               "also_kept": also_kept, "remove": rest, "multifile": False})
        return out_groups

    def summarize(gs):
        by = {"primary": 0, "secondary": 0}
        for g in gs:
            for d in g["remove"]:
                by[d["card_id"]] += d["size"]
        return {"reclaimable_bytes": sum(by.values()), "by_card": by,
                "files": sum(len(g["remove"]) for g in gs)}

    groups = build(prefer_card)
    summary = summarize(groups)

    # Multi-file games: only REPORT suspected duplicates (same system + name, NOT hashed).
    # Never proposed for deletion.
    referenced_by_playlist = {k for r in roms if r["extension"] in PLAYLIST_EXTS
                              for k in (_key(f) for f in _expand(Path(r["absolute_path"]), index)[0])}
    games: Dict[tuple, List[tuple]] = {}
    for r in roms:
        if r["extension"] not in MULTIFILE_EXTS or _key(r["absolute_path"]) in referenced_by_playlist:
            continue
        parts = _expand(Path(r["absolute_path"]), index)[0] if r["extension"] in PLAYLIST_EXTS else []
        games.setdefault((canon(r["system"]), r["name"].lower()), []).append(
            (r, r["size"] + sum(size_of.get(_key(f), 0) for f in parts)))
    skipped_multifile = [
        {"system": s, "name": members[0][0]["name"], "size": max(t for _, t in members),
         "same_size": len({t for _, t in members}) == 1,
         "paths": [x["absolute_path"] for x, _ in members],
         "reason": "Game nhiều file (.cue/.bin, .m3u + đĩa...): chỉ báo nghi trùng theo tên, "
                   "chưa băm. Không tự đề xuất xoá vì xoá lẻ một file sẽ làm hỏng game; "
                   "dùng 'rom delete <file>' để xoá có kiểm soát."}
        for (s, _n), members in sorted(games.items()) if len(members) > 1
    ]

    rep = {"groups": groups, "hashed_files": hashed_files, "hashed_bytes": hashed_bytes,
           "skipped_multifile": skipped_multifile, "reclaimable_bytes": summary["reclaimable_bytes"],
           "reclaimable_by_card": summary["by_card"], "prefer_card": prefer_card,
           "skipped_budget_files": skipped_budget, "hash_errors": hash_errors,
           "cache_hits": cache_hits, "cache_misses": cache_misses}
    if compare:
        rep["compare"] = {p: summarize(build(p)) for p in ("auto", "primary", "secondary")}
    return rep


# --------------------------------------------------------------------------
# plan deleting ONE game on request
# --------------------------------------------------------------------------
def plan_rom_deletion(path, primary_path, secondary_path, include_artwork: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "files": [], "artwork": [], "saves_kept": [],
                           "bytes": 0, "warnings": [], "message": ""}
    p = Path(path)
    card = None
    for root in (primary_path, secondary_path):
        if root and is_safe_relative_path(Path(root) / "Roms", p):
            card = Path(root).resolve()
            break
    if card is None or not p.is_file():
        out["message"] = ("Từ chối: đường dẫn phải là file có thật nằm trong Roms/ của thẻ Primary hoặc Secondary. "
                          "Hãy truyền đường dẫn đầy đủ tới file ROM.")
        return out
    rp = p.resolve()
    rel = rp.relative_to(card / "Roms")
    if len(rel.parts) < 2:
        out["message"] = "Từ chối: ROM phải nằm trong một thư mục hệ máy, dạng Roms/<HỆ>/<file>."
        return out
    ext = rp.suffix.lower()
    if ext in (".gdi", ".ccd"):
        out["message"] = (f"Chưa hỗ trợ xoá game dạng '{ext}' (không biết chắc những file nào thuộc game). "
                          "Hãy xoá thủ công cả thư mục game.")
        return out

    sys_name = rel.parts[0]
    sysdir = card / "Roms" / sys_name
    index = {_key(f): f for f in sysdir.rglob("*") if f.is_file()}

    files = {_key(rp): rp}
    if ext in PLAYLIST_EXTS:
        found, missing = _expand(rp, index)
        files.update({_key(f): f for f in found})
        for m in missing:
            out["warnings"].append(f"'{rp.name}' tham chiếu '{m}' nhưng file này không có trên thẻ.")

    # Anything still referenced by a playlist we are NOT deleting must stay.
    playlists = {k: f for k, f in index.items() if f.suffix.lower() in PLAYLIST_EXTS}
    direct = {k: {_key(r) for r in _read_refs(f)} for k, f in playlists.items()}
    while True:
        held = set().union(*[direct[k] for k in playlists if k not in files]) if playlists else set()
        blocked = [k for k in files if k in held]
        if not blocked:
            break
        for k in blocked:
            out["warnings"].append(
                f"Giữ lại '{files[k].name}' vì vẫn được playlist khác tham chiếu (xoá nó sẽ làm hỏng game kia).")
            del files[k]

    if not files:
        out["message"] = ("Không có file nào được xoá: file này thuộc game nhiều file hoặc đang được playlist khác tham chiếu. "
                          "Hãy chọn file .cue/.m3u của game để xoá trọn bộ.")
        return out

    out["files"] = sorted(str(f) for f in files.values())
    out["bytes"] = sum(f.stat().st_size for f in files.values())

    stems = {f.stem.lower() for f in files.values()}
    if include_artwork:
        others = [f for r in (primary_path, secondary_path) if r
                  for f in (Path(r) / "Roms" / sys_name).rglob("*")
                  if f.is_file() and f.stem.lower() == rp.stem.lower() and _key(f) not in files]
        art_dir = card / "Images" / sys_name
        art = [a for a in (art_dir.iterdir() if art_dir.is_dir() else [])
               if a.is_file() and a.suffix.lower() in IMAGE_EXTS and a.stem.lower() == rp.stem.lower()]
        if art and others:
            out["warnings"].append(
                f"Giữ ảnh bìa vì vẫn còn bản khác cùng tên '{others[0]}' đang dùng nó.")
        else:
            out["artwork"] = sorted(str(a) for a in art)
            out["bytes"] += sum(a.stat().st_size for a in art)

    for r in (primary_path, secondary_path):
        if not r:
            continue
        for d in ("Saves", "States"):
            base = Path(r) / d
            if not base.is_dir():
                continue
            for f in base.rglob("*"):
                s = f.stem.lower()
                if f.is_file() and (s in stems or re.sub(r"\.state.*$", "", s) in stems):
                    out["saves_kept"].append(str(f))
    out["saves_kept"].sort()
    if out["saves_kept"]:
        out["warnings"].append(
            f"Game này có {len(out['saves_kept'])} save/state đi kèm. LoongMate KHÔNG xoá chúng; "
            "chúng sẽ thành mồ côi nếu bạn không chơi lại game này.")

    out["ok"] = True
    out["message"] = f"Sẽ xoá {len(out['files'])} file ROM + {len(out['artwork'])} ảnh bìa ({out['bytes'] / MB:.1f} MB)."
    return out


# --------------------------------------------------------------------------
# execute
# --------------------------------------------------------------------------
def _update_quarantine_index(app_data: Path, entries: List[Dict[str, Any]], drop_paths=()) -> None:
    idx = app_data / "quarantine-index.json"
    data: List[Dict[str, Any]] = []
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
    drop = set(drop_paths)
    data = [e for e in data if e.get("quarantine_path") not in drop] + entries
    tmp = idx.with_name(idx.name + ".partial")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, idx)


def execute_deletion(files, app_data, reason, kept_path=None, expected_sha256=None,
                     primary_path=None, secondary_path=None, artwork=None,
                     quarantine: bool = False, copy_artwork: bool = False) -> Dict[str, Any]:
    """Deletes (or, with quarantine=True, moves to Quarantine) the given files after verification.

    reason: 'duplicate' (needs kept_path + expected_sha256) or 'user_request'.
    SAFETY: every hash here (removed copy AND kept copy) is computed fresh from disk with compute_sha256 --
    never from engine.hashcache -- so a stale or lying cache cannot cause a wrong deletion.
    `artwork` (user_request only) may hold cover images from Images/ of a card.
    One refused file never stops the others.
    copy_artwork=True (reason='duplicate' only): before deleting, make sure the KEPT copy's card has the
    cover art (copied from the other card with SHA-256 verification, see engine.artwork). The outcome
    is reported in res["artwork"]; a failure here never blocks or forces the deletion.
    """
    app_data = Path(app_data)
    res: Dict[str, Any] = {"deleted": [], "refused": [], "bytes_freed": 0,
                           "mode": "quarantine" if quarantine else "delete",
                           "ledger_path": str(app_data / LEDGER_NAME)}
    cards = [Path(r).resolve() for r in (primary_path, secondary_path) if r]
    primary_root = Path(primary_path).resolve() if primary_path else None
    todo = [(f, "rom") for f in files] + [(a, "artwork") for a in (artwork or [])]

    def refuse_all(why: str):
        res["refused"] = [{"path": str(f), "reason": why} for f, _ in todo]
        return res

    if reason not in ("duplicate", "user_request"):
        return refuse_all(f"Lý do xoá không hợp lệ: '{reason}'.")
    if not cards:
        return refuse_all("Thiếu đường dẫn thẻ (primary_path), không xác định được vùng Roms/ an toàn.")
    if artwork and reason != "user_request":
        return refuse_all("Chỉ được xoá ảnh bìa khi người dùng yêu cầu xoá game (reason='user_request').")

    keeper = None
    if reason == "duplicate":
        if not kept_path or not expected_sha256:
            return refuse_all("Xoá bản trùng cần cả kept_path và expected_sha256.")
        keeper = Path(kept_path)
        if not any(is_safe_relative_path(c / "Roms", keeper) for c in cards) or not keeper.is_file():
            return refuse_all(f"Bản giữ lại '{kept_path}' không còn trong Roms/ — không xoá gì để khỏi mất bản cuối.")
        try:
            st0 = keeper.stat()
            if compute_sha256(keeper) != expected_sha256:
                return refuse_all(f"Bản giữ lại '{kept_path}' đã thay đổi nội dung từ lúc quét. Chạy lại 'dedupe'.")
        except OSError as e:
            return refuse_all(f"Không đọc được bản giữ lại: {e}")

    if copy_artwork and keeper is not None:
        from engine.artwork import ensure_art_on_card
        kcard = next((c for c in cards if is_safe_relative_path(c / "Roms", keeper)), None)
        res["artwork"] = (ensure_art_on_card(keeper, kcard, primary_path, secondary_path, app_data)
                          if kcard is not None else None)

    ts = time.strftime("%Y%m%d_%H%M%S")
    q_index: List[Dict[str, Any]] = []
    seen = set()
    for f, kind in todo:
        sp = str(f)
        fp = Path(f)
        try:
            if sp in seen:
                continue
            seen.add(sp)
            if kind == "rom":
                card = next((c for c in cards if is_safe_relative_path(c / "Roms", fp)), None)
            else:
                card = next((c for c in cards if is_safe_relative_path(c / "Images", fp)
                             and fp.suffix.lower() in IMAGE_EXTS), None)
            if card is None:
                res["refused"].append({"path": sp, "reason": "Nằm ngoài Roms/ (hoặc Images/ cho ảnh bìa) của thẻ — từ chối."})
                continue
            if fp.is_symlink() or not fp.is_file():
                res["refused"].append({"path": sp, "reason": "Không phải file thường (không tồn tại hoặc là symlink)."})
                continue
            size = fp.stat().st_size
            sha = compute_sha256(fp)
            if keeper is not None:
                if fp.resolve() == keeper.resolve():
                    res["refused"].append({"path": sp, "reason": "Chính là bản giữ lại — không bao giờ xoá bản cuối."})
                    continue
                pl = _referencing_playlist(fp)
                if pl is not None:
                    res["refused"].append({"path": sp, "reason": f"Đang được playlist '{pl.name}' tham chiếu — xoá sẽ làm hỏng game."})
                    continue
                # Chốt chặn cuối: bản trùng có save/state đi kèm không bao giờ bị xoá,
                # phòng khi kế hoạch được dựng sai ở đâu đó (xem find_duplicates).
                companion = next((p for sub in ("Saves", "States")
                                  for p in (card / sub).rglob(glob.escape(fp.stem) + ".*")
                                  if p.is_file()), None) if (card / "Saves").exists() or (card / "States").exists() else None
                if companion is not None:
                    res["refused"].append({"path": sp, "reason": f"Có save/state đi kèm ({companion.name}) — "
                                                                  "xoá bản này có thể làm mất tiến trình chơi."})
                    continue
                if sha != expected_sha256:
                    res["refused"].append({"path": sp, "reason": "Nội dung không khớp SHA-256 đã xác minh (file đã đổi sau khi quét)."})
                    continue
                try:
                    st1 = keeper.stat()
                except OSError:
                    res["refused"].append({"path": sp, "reason": "Bản giữ lại đã biến mất — không xoá."})
                    continue
                if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
                    res["refused"].append({"path": sp, "reason": "Bản giữ lại đã bị sửa trong lúc chạy — không xoá."})
                    continue

            dst = None
            if quarantine:
                qbase = (app_data / "Quarantine") if card == primary_root else (card / ".userdata" / "LoongMate" / "Quarantine")
                dst = qbase / ts / fp.resolve().relative_to(card)
                if dst.exists():
                    res["refused"].append({"path": sp, "reason": f"Đích Quarantine đã tồn tại: {dst}"})
                    continue

            # Ledger FIRST (fsync), then act.
            entry = {"deleted_at": _now_iso(), "path": sp, "size": size, "sha256": sha,
                     "reason": reason, "kept_path": str(kept_path) if keeper is not None else None,
                     "action": "quarantine" if quarantine else "delete", "kind": kind,
                     "status": "intent"}
            if dst is not None:
                entry["quarantined_to"] = str(dst)
            try:
                _ledger_append(app_data, entry)
            except OSError as e:
                res["refused"].append({"path": sp, "reason": f"Không ghi được sổ xoá ({e}) — không xoá khi chưa có dấu vết."})
                continue
            try:
                if dst is not None:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(sp, str(dst))
                    q_index.append({"timestamp": ts, "original_path": sp, "quarantine_path": str(dst), "size": size})
                else:
                    os.remove(sp)
            except OSError as e:
                try:
                    _ledger_append(app_data, {**entry, "status": "failed", "error": str(e)})
                except OSError:
                    pass
                res["refused"].append({"path": sp, "reason": f"Thao tác thất bại: {e}"})
                continue
            res["deleted"].append(sp)
            if not quarantine:
                res["bytes_freed"] += size
        except OSError as e:
            res["refused"].append({"path": sp, "reason": f"Lỗi I/O: {e}"})

    if q_index:
        try:
            _update_quarantine_index(app_data, q_index)
        except OSError:
            pass
    return res


# --------------------------------------------------------------------------
# quarantine list / purge
# --------------------------------------------------------------------------
def _quarantine_roots(app_data, secondary_path) -> List[Path]:
    roots = [Path(app_data) / "Quarantine"]
    if secondary_path:
        roots.append(Path(secondary_path) / ".userdata" / "LoongMate" / "Quarantine")
    return roots


def list_quarantine(app_data, secondary_path=None, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    batches = []
    for root in _quarantine_roots(app_data, secondary_path):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            fs = [x for x in child.rglob("*") if x.is_file()] if child.is_dir() else [child]
            try:
                age = (now - time.mktime(time.strptime(child.name, "%Y%m%d_%H%M%S"))) / 86400
            except ValueError:
                age = None
            batches.append({"name": child.name, "path": str(child), "root": str(root), "age_days": age,
                            "files": [{"path": str(x), "size": x.stat().st_size} for x in fs]})
    for b in batches:
        b["bytes"] = sum(f["size"] for f in b["files"])
    return {"batches": batches, "total_files": sum(len(b["files"]) for b in batches),
            "total_bytes": sum(b["bytes"] for b in batches)}


def purge_quarantine(app_data, secondary_path=None, older_than_days: Optional[float] = None,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """Permanently deletes quarantined files (ledgered first). With older_than_days, batches
    whose age is unknown are skipped rather than guessed."""
    app_data = Path(app_data)
    res: Dict[str, Any] = {"deleted": [], "refused": [], "bytes_freed": 0,
                           "ledger_path": str(app_data / LEDGER_NAME)}
    roots = _quarantine_roots(app_data, secondary_path)
    dirs_to_prune = set()
    for b in list_quarantine(app_data, secondary_path, now)["batches"]:
        if older_than_days is not None and (b["age_days"] is None or b["age_days"] < older_than_days):
            continue
        root = Path(b["root"])
        dirs_to_prune.add(Path(b["path"]))
        for f in b["files"]:
            fp = Path(f["path"])
            try:
                if fp.is_symlink() or not is_safe_relative_path(root, fp):
                    res["refused"].append({"path": f["path"], "reason": "Nằm ngoài thư mục Quarantine — từ chối."})
                    continue
                entry = {"deleted_at": _now_iso(), "path": f["path"], "size": f["size"],
                         "sha256": compute_sha256(fp), "reason": "quarantine_purge", "kept_path": None,
                         "action": "delete", "kind": "quarantine", "status": "intent"}
                try:
                    _ledger_append(app_data, entry)
                except OSError as e:
                    res["refused"].append({"path": f["path"], "reason": f"Không ghi được sổ xoá ({e})."})
                    continue
                os.remove(fp)
                res["deleted"].append(f["path"])
                res["bytes_freed"] += f["size"]
            except OSError as e:
                res["refused"].append({"path": f["path"], "reason": f"Lỗi I/O: {e}"})
    for d in sorted(dirs_to_prune, key=lambda x: -len(x.parts)):
        for sub in sorted((x for x in d.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)) if d.is_dir() else []:
            try:
                sub.rmdir()
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass
    if res["deleted"]:
        try:
            _update_quarantine_index(app_data, [], drop_paths=res["deleted"])
        except OSError:
            pass
    return res
