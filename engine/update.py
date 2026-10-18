#!/usr/bin/env python3
"""LoongMate - tự cập nhật có kiểm hash (chạy trên máy, chỉ dùng stdlib).

Gói cập nhật là một thư mục:
    <bundle>/manifest.json   {"schema":1,"version":"...","created_at":"...",
                              "files":{"LoongMate.pak/engine/cli.py":"<sha256>", ...}}
    <bundle>/LoongMate.pak/...   (và LoongMateAgent.pak/..., LoongMateNative.pak/... nếu có)
    "remove_paks": ["LoongMateNative.pak"]   (khoá tuỳ chọn trong manifest: pak cần GỠ khỏi máy, vd. app đã gộp vào LoongMate.pak)

API ổn định (giao diện/CLI ghép vào sau):
    verify_bundle(bundle_dir)                 -> dict  (không ném lỗi; xem "ok" và "errors")
    apply_update(bundle_dir, apps_dir, app_data) -> dict
    rollback(apps_dir, app_data)              -> dict
    status(app_data)                          -> dict

Nguyên tắc an toàn:
  * Chỉ đụng tới: <apps_dir>/{3 pak} (+ thư mục tạm/.old cạnh chúng, CÙNG thẻ nên đổi tên là nguyên tử),
    <app_data>/pak-backup/ và <app_data>/installed-version.json. Không bao giờ chạm Roms/Saves/States/.userdata.
  * Gói chỉ được chứa đường dẫn trong 3 pak; mọi file phải khớp sha256; không có file thừa, không symlink.
  * Lỗi ở bất kỳ bước nào sau khi bắt đầu đổi -> trả lại bản cũ (kết quả có "rolled_back": true).
  * remove_paks: chỉ tên trong PAKS, không trùng pak có trong gói, không được là LoongMate.pak (chính engine đang chạy).
    Pak bị gỡ cũng được sao lưu vào pak-backup/<ts>/, đổi tên thành .<tên>.old trong cùng lượt đổi (lỗi -> trả lại nguyên vẹn),
    xoá .old khi thành công; rollback() chép nó trở lại từ bản sao lưu.
  * Quyền thực thi: launch.sh và bin/* của mỗi pak (gói đi qua tar từ Windows nên không tin mode trong gói).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path, PurePosixPath

PAKS = ("LoongMate.pak", "LoongMateAgent.pak", "LoongMateNative.pak")
KEEP_BACKUPS = 3
UPDATE_DIR_NAME = "LoongMate-update"   # <SDCARD>/LoongMate-update/<tên gói>/manifest.json
KEEP_BUNDLES = 2
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEFAULT_APPS_DIR = os.path.join(os.environ.get("SDCARD_PATH", "/mnt/sdcard"), "Apps", "mlp1")
DEFAULT_APP_DATA = os.path.join(os.environ.get("USERDATA_PATH", "/mnt/sdcard/.userdata/mlp1"), "LoongMate")


class UpdateError(Exception):
    pass


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def _is_exec_path(rel):
    """rel dạng 'LoongMate.pak/launch.sh' hoặc 'LoongMate.pak/bin/xyz'."""
    parts = PurePosixPath(rel).parts
    return (len(parts) == 2 and parts[1] == "launch.sh") or (len(parts) >= 3 and parts[1] == "bin")


def _check_rel(rel):
    """Trả về chuỗi lỗi nếu đường dẫn trong manifest không an toàn, ngược lại None."""
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel or rel.startswith("/"):
        return f"đường dẫn không hợp lệ: {rel!r}"
    parts = PurePosixPath(rel).parts
    if any(p in ("", ".", "..") for p in parts) or ".." in rel.split("/"):
        return f"đường dẫn thoát/không chuẩn: {rel!r}"
    if len(parts) < 2 or parts[0] not in PAKS:
        return f"đường dẫn ngoài 3 pak cho phép ({', '.join(PAKS)}): {rel!r}"
    return None


def _load_manifest(bundle):
    mf = Path(bundle) / "manifest.json"
    if not mf.is_file():
        raise UpdateError("không có manifest.json trong gói")
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise UpdateError(f"manifest.json hỏng: {e}")
    if not isinstance(m, dict) or m.get("schema") != 1:
        raise UpdateError("manifest.json: schema phải là 1")
    if not isinstance(m.get("version"), str) or not VERSION_RE.match(m["version"]):
        raise UpdateError("manifest.json: 'version' thiếu hoặc có ký tự không hợp lệ")
    if not isinstance(m.get("files"), dict) or not m["files"]:
        raise UpdateError("manifest.json: 'files' trống")
    return m


def verify_bundle(bundle_dir):
    """Mọi file khớp sha256, không file thừa ngoài manifest, đường dẫn chỉ trong 3 pak."""
    bundle = Path(bundle_dir)
    res = {"ok": False, "version": None, "files": 0, "paks": [], "remove_paks": [], "errors": []}
    try:
        m = _load_manifest(bundle)
    except UpdateError as e:
        res["errors"].append(str(e))
        return res
    res["version"], res["files"] = m["version"], len(m["files"])
    errs = res["errors"]
    declared = set()
    for rel, want in sorted(m["files"].items()):
        bad = _check_rel(rel)
        if bad:
            errs.append(bad)
            continue
        if not (isinstance(want, str) and HASH_RE.match(want)):
            errs.append(f"{rel}: sha256 không hợp lệ")
            continue
        declared.add(rel)
        p = bundle.joinpath(*PurePosixPath(rel).parts)
        if any(q.is_symlink() for q in [p, *p.parents] if q != bundle and bundle in q.parents):
            errs.append(f"{rel}: là symlink / nằm trong symlink")
        elif not p.is_file():
            errs.append(f"{rel}: thiếu trong gói")
        elif sha256_file(p) != want:
            errs.append(f"{rel}: sai sha256")
    for root, dirs, files in os.walk(bundle):
        for name in dirs + files:
            p = Path(root) / name
            if p.is_symlink():
                errs.append(f"{p.relative_to(bundle).as_posix()}: symlink không được phép")
        for name in files:
            p = Path(root) / name
            rel = p.relative_to(bundle).as_posix()
            if rel != "manifest.json" and rel not in declared and not p.is_symlink():
                errs.append(f"{rel}: file thừa, không có trong manifest")
    res["paks"] = sorted({PurePosixPath(r).parts[0] for r in declared})
    rm = m.get("remove_paks", [])
    if not isinstance(rm, list) or not all(isinstance(x, str) for x in rm):
        errs.append("manifest.json: 'remove_paks' phải là danh sách tên pak")
    else:
        for x in rm:
            if x not in PAKS:
                errs.append(f"remove_paks: tên pak lạ {x!r} (chỉ cho phép {', '.join(PAKS)})")
            elif x == "LoongMate.pak":
                errs.append("remove_paks: không được gỡ LoongMate.pak (chính engine đang chạy)")
            elif x in res["paks"]:
                errs.append(f"remove_paks: {x} vừa nằm trong gói vừa bị gỡ")
        if len(set(rm)) != len(rm):
            errs.append("remove_paks: có tên lặp")
        res["remove_paks"] = sorted(set(x for x in rm if x in PAKS))
    res["ok"] = not errs
    return res


# --------------------------------------------------------------------------- tiện ích cây thư mục

def _tree_hashes(root):
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(root).as_posix()] = sha256_file(p)
    return out


def _fix_modes(pak_dir, pak_name):
    for p in Path(pak_dir).rglob("*"):
        if p.is_file() and not p.is_symlink():
            rel = f"{pak_name}/{p.relative_to(pak_dir).as_posix()}"
            os.chmod(p, 0o755 if _is_exec_path(rel) else 0o644)


def _rmtree(p):
    p = Path(p)
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.exists():
        shutil.rmtree(p)


def _write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _new_dir(apps_dir, name):
    return Path(apps_dir) / f".{name}.new"


def _old_dir(apps_dir, name):
    return Path(apps_dir) / f".{name}.old"


# --------------------------------------------------------------------------- đổi pak (dùng chung apply/rollback)

def _swap_one(apps_dir, name):
    """.<name>.new -> <name>; bản cũ (nếu có) -> .<name>.old. Cùng thẻ nên os.rename là nguyên tử.
    Tách riêng để test chèn lỗi giữa chừng."""
    real, new, old = Path(apps_dir) / name, _new_dir(apps_dir, name), _old_dir(apps_dir, name)
    _rmtree(old)
    if real.exists():
        os.rename(real, old)
    os.rename(new, real)


def _undo(apps_dir, names, absent=()):
    """Trả mọi pak về như trước khi đổi: có .old thì .old -> tên thật; pak vốn chưa có (absent) thì bỏ bản mới.
    Chỉ tin .old vì _swap_all đã xoá .old cũ trước khi bắt đầu (nên .old nào còn là của lượt này)."""
    for n in reversed(list(names)):
        real, old = Path(apps_dir) / n, _old_dir(apps_dir, n)
        if old.exists():
            _rmtree(real)
            os.rename(old, real)
        elif n in absent:
            _rmtree(real)


def _remove_one(apps_dir, name):
    """<name> -> .<name>.old (gỡ pak khỏi danh sách Apps nhưng còn đường lùi). Tách riêng để test chèn lỗi."""
    os.rename(Path(apps_dir) / name, _old_dir(apps_dir, name))


def _swap_all(apps_dir, names, absent=(), remove=()):
    """Đổi lần lượt, rồi gỡ các pak trong `remove`; lỗi ở bước nào thì trả lại MỌI pak (kể cả pak đang đổi dở).
    Ném lại lỗi."""
    for n in [*names, *remove]:
        _rmtree(_old_dir(apps_dir, n))
    try:
        for n in names:
            _swap_one(apps_dir, n)
        for n in remove:
            _remove_one(apps_dir, n)
    except BaseException:
        _undo(apps_dir, [*names, *remove], absent)
        raise


def _stage(src, apps_dir, name, expect=None):
    """Chép src -> .<name>.new, chỉnh quyền, băm lại. expect: {rel: sha} (None thì so với chính src)."""
    new = _new_dir(apps_dir, name)
    _rmtree(new)
    shutil.copytree(src, new, symlinks=True)
    _fix_modes(new, name)
    got = _tree_hashes(new)
    want = expect if expect is not None else _tree_hashes(src)
    if got != want:
        diff = sorted(set(got) ^ set(want) | {k for k in got if k in want and got[k] != want[k]})
        raise UpdateError(f"{name}: bản chép sang thẻ lệch hash/thiếu file ({', '.join(diff[:5])})")
    return new


def _cleanup_tmp(apps_dir, names):
    for n in names:
        _rmtree(_new_dir(apps_dir, n))


def _prune_backups(app_data):
    root = Path(app_data) / "pak-backup"
    if root.is_dir():
        dirs = sorted(d for d in root.iterdir() if d.is_dir())
        for d in dirs[:-KEEP_BACKUPS]:
            shutil.rmtree(d, ignore_errors=True)


def _backup_dirs(app_data):
    root = Path(app_data) / "pak-backup"
    return sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []


def _prune_bundles(bundle, apps_dir, keep=KEEP_BUNDLES):
    """Dọn gói cũ sau khi cài thành công. CHỈ chạy khi `bundle` nằm trực tiếp trong
    <SDCARD>/LoongMate-update/ (SDCARD = cha của cha apps_dir, vì apps_dir = <SDCARD>/Apps/mlp1); gói ở nơi khác
    thì không đụng gì. Chỉ xoá thư mục con thật (không symlink) của LoongMate-update có manifest.json hợp lệ.
    Giữ gói vừa cài + gói mới nhất còn lại (theo created_at) cho đủ `keep`. Lỗi bất kỳ -> không xoá thêm, không ném."""
    try:
        root = (Path(apps_dir).resolve().parent.parent / UPDATE_DIR_NAME)
        b = Path(bundle)
        if b.is_symlink() or b.resolve().parent != root.resolve() or not root.is_dir() or root.is_symlink():
            return []
        rows = []
        for d in root.iterdir():
            if d.is_symlink() or not d.is_dir():
                continue
            try:
                m = _load_manifest(d)
            except UpdateError:
                continue
            ca = m.get("created_at")
            rows.append((ca if isinstance(ca, str) else "", d.name, d))
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        mine = b.resolve()
        keepers = [r for r in rows if r[2].resolve() == mine]
        for r in rows:
            if len(keepers) >= keep:
                break
            if r not in keepers:
                keepers.append(r)
        removed = []
        for r in rows:
            if r in keepers:
                continue
            try:
                shutil.rmtree(r[2])
                removed.append(r[1])
            except OSError:
                pass
        return sorted(removed)
    except OSError:
        return []


# --------------------------------------------------------------------------- apply / rollback

def apply_update(bundle_dir, apps_dir, app_data):
    bundle, apps, data = Path(bundle_dir), Path(apps_dir), Path(app_data)
    v = verify_bundle(bundle)
    if not v["ok"]:
        return {"ok": False, "stage": "verify", "error": "gói không qua kiểm tra", "errors": v["errors"],
                "rolled_back": False}
    m = _load_manifest(bundle)
    names = [n for n in PAKS if n in v["paks"]]
    remove = [n for n in v["remove_paks"] if (apps / n).is_dir()]   # pak đã vắng sẵn thì không còn gì để gỡ
    if not apps.is_dir():
        return {"ok": False, "stage": "prepare", "error": f"không có thư mục Apps: {apps}", "rolled_back": False}
    prev = _read_json(data / "installed-version.json")
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = data / "pak-backup" / ts
    n = 1
    while bdir.exists():
        n += 1
        bdir = data / "pak-backup" / f"{ts}-{n}"
    swapped, present = False, []
    absent = [x for x in names if not (apps / x).is_dir()]
    try:
        # 1) sao lưu bản đang chạy
        bdir.mkdir(parents=True)
        present = [x for x in names if (apps / x).is_dir()]
        for x in [*present, *remove]:
            shutil.copytree(apps / x, bdir / x, symlinks=True)
        _write_json_atomic(bdir / "meta.json", {
            "schema": 1, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "paks": [*present, *remove],
            "removed_paks": remove,
            "absent_before": [x for x in names if x not in present],
            "version_before": (prev or {}).get("version"), "update_to": m["version"]})
        # 2) chép bản mới vào thư mục tạm cùng thẻ, băm lại
        by_pak = {}
        for rel, h in m["files"].items():
            pk, sub = rel.split("/", 1)
            by_pak.setdefault(pk, {})[sub] = h
        for x in names:
            _stage(bundle / x, apps, x, expect=by_pak[x])
        # 3) đổi tên
        swapped = True
        _swap_all(apps, names, absent, remove)
        # 4) ghi phiên bản; lỗi thì vẫn hoàn tác được vì .old còn
        _write_json_atomic(data / "installed-version.json", {
            "schema": 1, "version": m["version"], "bundle_created_at": m.get("created_at"),
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "paks": names,
            "removed_paks": remove, "previous_version": (prev or {}).get("version"), "backup": bdir.name})
    except BaseException as e:
        if swapped:
            _undo(apps, [*names, *remove], absent)
        _cleanup_tmp(apps, names)
        if not isinstance(e, Exception):  # Ctrl+C: đã trả bản cũ, giờ để nó đi tiếp
            raise
        shutil.rmtree(bdir, ignore_errors=True)
        return {"ok": False, "stage": "swap" if swapped else "stage", "error": f"{type(e).__name__}: {e}",
                "rolled_back": True}
    for x in [*names, *remove]:
        _rmtree(_old_dir(apps, x))
    _prune_backups(data)
    pruned = _prune_bundles(bundle, apps)
    return {"ok": True, "version": m["version"], "previous_version": (prev or {}).get("version"),
            "paks": names, "removed_paks": remove, "backup": bdir.name, "files": len(m["files"]),
            "pruned_bundles": pruned}


def rollback(apps_dir, app_data):
    """Khôi phục từ bản sao lưu mới nhất trong <app_data>/pak-backup/. Giữ nguyên các bản sao lưu."""
    apps, data = Path(apps_dir), Path(app_data)
    backups = _backup_dirs(data)
    if not backups:
        return {"ok": False, "error": "không có bản sao lưu nào trong pak-backup", "rolled_back": False}
    b = backups[-1]
    meta = _read_json(b / "meta.json") or {}
    names = [x for x in PAKS if (b / x).is_dir()]
    absent = [x for x in meta.get("absent_before", []) if x in PAKS and (apps / x).is_dir()]
    if not names and not absent:
        return {"ok": False, "error": f"bản sao lưu {b.name} rỗng", "rolled_back": False}
    cur = _read_json(data / "installed-version.json")
    missing = [x for x in names if not (apps / x).is_dir()]   # pak đã bị gỡ: lỗi thì bỏ bản vừa chép lại
    swapped = False
    try:
        for x in names:
            _stage(b / x, apps, x)
        swapped = True
        _swap_all(apps, names, missing)
        # pak chưa từng có trước bản cập nhật: đổi thành .old để còn đường lùi nếu bước sau lỗi
        for x in absent:
            _rmtree(_old_dir(apps, x))
            os.rename(apps / x, _old_dir(apps, x))
        _write_json_atomic(data / "installed-version.json", {
            "schema": 1, "version": meta.get("version_before"), "rolled_back_from": (cur or {}).get("version"),
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "paks": names, "restored_backup": b.name})
    except BaseException as e:
        if swapped:
            _undo(apps, names + absent, missing)
        _cleanup_tmp(apps, names)
        if not isinstance(e, Exception):
            raise
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "rolled_back": True}
    for x in names + absent:
        _rmtree(_old_dir(apps, x))
    return {"ok": True, "version": meta.get("version_before"), "from_version": (cur or {}).get("version"),
            "paks": names, "removed_paks": absent,
            "restored_removed_paks": [x for x in meta.get("removed_paks", []) if x in names], "backup": b.name}


def status(app_data):
    data = Path(app_data)
    backups = []
    for b in _backup_dirs(data):
        meta = _read_json(b / "meta.json") or {}
        backups.append({"name": b.name, "version_before": meta.get("version_before"),
                        "update_to": meta.get("update_to"), "paks": meta.get("paks", [])})
    return {"installed": _read_json(data / "installed-version.json"), "backups": backups}


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="LoongMate: cập nhật pak có kiểm hash (chạy trên máy).")
    ap.add_argument("cmd", choices=["verify", "apply", "rollback", "status"])
    ap.add_argument("bundle", nargs="?", help="thư mục gói (verify/apply)")
    ap.add_argument("--apps-dir", default=DEFAULT_APPS_DIR, help=f"mặc định {DEFAULT_APPS_DIR}")
    ap.add_argument("--app-data", default=DEFAULT_APP_DATA, help=f"mặc định {DEFAULT_APP_DATA}")
    ap.add_argument("--yes", action="store_true", help="không hỏi xác nhận (apply/rollback)")
    a = ap.parse_args(argv)
    if a.cmd in ("verify", "apply") and not a.bundle:
        print(f"'{a.cmd}' cần đường dẫn gói", file=sys.stderr)
        return 3
    if a.cmd in ("apply", "rollback") and not a.yes:
        if not sys.stdin.isatty():
            print("Cần xác nhận tương tác hoặc --yes.", file=sys.stderr)
            return 3
        if input(f"Gõ 'yes' để {a.cmd} vào {a.apps_dir}: ").strip() != "yes":
            print("Đã huỷ.", file=sys.stderr)
            return 3
    if a.cmd == "verify":
        r = verify_bundle(a.bundle)
    elif a.cmd == "apply":
        r = apply_update(a.bundle, a.apps_dir, a.app_data)
    elif a.cmd == "rollback":
        r = rollback(a.apps_dir, a.app_data)
    else:
        r = status(a.app_data)
    try:                                          # chạy như script cứu hộ (rollback) thì có thể không có gói engine
        from engine import i18n
        r_out = i18n.translate_obj(r)
    except Exception:
        r_out = r
    print(json.dumps(r_out, ensure_ascii=False, indent=2))
    if a.cmd not in ("verify", "apply", "rollback"):
        return 0
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
