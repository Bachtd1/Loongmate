"""Leaf Hot Tabs: đọc trạng thái, bật/tắt "tự tiếp tục khi mở từ Games/Favorites", gọi bộ cài.

Hot Tabs là bản `jawaka-launcher` đã patch (repo leaf-hottabs). Module này KHÔNG tự thay binary:
việc cài/gỡ do `<thẻ>/hottabs/hottabs.sh` làm (một nguồn sự thật cho thao tác thay launcher, có sao lưu +
kiểm md5); ở đây chỉ gọi nó bằng argv cố định (không qua shell).

Chỉ dùng stdlib; `status()` không ghi gì.

Định dạng các file đọc (đã đối chiếu trên máy thật 2026-10-03):
  <sd>/.umrk/mlp1/release.json                    {"release_id": "v0.11.0", "version": "0.11.0", ...}
  <sd>/.system/leaf/releases/<id>/provenance/components.json   {"components":[{"name":"launcher","commit":...}, ...]}
  <sd>/hottabs/hottabs.manifest                   key=value: leaf_version, launcher_commit, stock.jawaka-launcher, patched.jawaka-launcher
  <userdata>/hot-tabs/no-resume-from-lists        file cờ rỗng: có = TẮT resume từ Games/Favorites
  <userdata>/hot-tabs/backup/<leaf_version>/jawaka-launcher   bản gốc sao lưu
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

PLATFORM = "mlp1"
LAUNCHER = "jawaka-launcher"
FLAG_NAME = "no-resume-from-lists"
INSTALLER_TIMEOUT_S = 120

RESTART_NOTE = "Khởi động lại máy để có hiệu lực"


def userdata_for(sd, env_userdata: Optional[str] = None) -> Path:
    """Userdata CỦA LEAF (không phải của LoongMate): $USERDATA_PATH nếu nó nằm trong thẻ, không thì <sd>/.userdata/mlp1."""
    sd = Path(sd)
    if env_userdata:
        p = Path(env_userdata)
        try:
            p.resolve().relative_to(sd.resolve())
            return p
        except ValueError:
            pass
    return sd / ".userdata" / PLATFORM


def flag_path(userdata) -> Path:
    return Path(userdata) / "hot-tabs" / FLAG_NAME


def _md5(path: Path) -> Optional[str]:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_manifest(path: Path) -> dict:
    """key=value mỗi dòng (cùng cách `mf()` của hottabs.sh: dòng đầu tiên cho mỗi khoá). {} nếu không đọc được."""
    out: dict = {}
    try:
        for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = ln.partition("=")
            if sep and k.strip() and k.strip() not in out:
                out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def leaf_provenance(sd) -> tuple:
    """(release_id, launcher_commit); phần nào không đọc được là None."""
    rel = _read_json(Path(sd) / ".umrk" / PLATFORM / "release.json")
    rid = rel.get("release_id") if isinstance(rel, dict) else None
    if not isinstance(rid, str) or not rid or "/" in rid or "\\" in rid or rid.startswith("."):
        return None, None
    comps = _read_json(Path(sd) / ".system" / "leaf" / "releases" / rid / "provenance" / "components.json")
    commit = None
    items = comps.get("components") if isinstance(comps, dict) else comps
    for c in items if isinstance(items, list) else []:
        if isinstance(c, dict) and c.get("name") == "launcher" and isinstance(c.get("commit"), str):
            commit = c["commit"]
            break
    return rid, commit


def installer_path(sd) -> Optional[str]:
    p = Path(sd) / "hottabs" / "hottabs.sh"
    return str(p) if p.is_file() else None


def status(sd, userdata=None) -> dict:
    """Trạng thái Hot Tabs. Chỉ đọc.

    installed: "patched" md5 launcher = bản patch của gói | "stock" = bản gốc của gói |
               "unknown" có launcher nhưng md5 không khớp gói (hoặc không có gói để so) |
               "absent" không có file launcher (không phải Leaf mlp1 / thẻ sai)."""
    sd = Path(sd)
    ud = Path(userdata) if userdata else userdata_for(sd)
    rid, commit = leaf_provenance(sd)
    mf = read_manifest(sd / "hottabs" / "hottabs.manifest")
    launcher = sd / ".system" / "leaf" / "platforms" / PLATFORM / "launcher" / "bin" / LAUNCHER
    md5 = _md5(launcher)
    stock, patched = mf.get("stock." + LAUNCHER), mf.get("patched." + LAUNCHER)
    if md5 is None:
        installed = "absent"
    elif md5 == patched and patched:
        installed = "patched"
    elif md5 == stock and stock:
        installed = "stock"
    else:
        installed = "unknown"
    bver = mf.get("leaf_version")
    matches = bool(mf.get("launcher_commit")) and mf.get("launcher_commit") == commit
    bdir = ud / "hot-tabs" / "backup"
    backup = bool(bver) and (bdir / bver / LAUNCHER).is_file()
    resume = not flag_path(ud).exists()
    if installed == "absent":
        msg = "Không thấy jawaka-launcher của Leaf trên thẻ này."
    elif installed == "patched":
        msg = "Đang dùng bản Hot Tabs." + ("" if resume else " Tự tiếp tục từ Games/Favorites đang TẮT.")
    elif installed == "stock":
        msg = ("Đang dùng bản gốc của Leaf." if mf else "Đang dùng bản gốc.") + \
              ("" if matches else " Bộ cài trên thẻ không khớp phiên bản Leaf này.")
    elif not mf:
        msg = "Không có bộ cài (hottabs/hottabs.manifest) trên thẻ nên không nhận diện được launcher."
    elif not matches:
        msg = "Launcher không khớp bộ cài: bộ cài làm cho Leaf khác, cần build lại."
    else:
        msg = "Launcher không khớp bản gốc lẫn bản Hot Tabs của bộ cài; bộ cài sẽ từ chối ghi đè."
    return {"installed": installed, "leaf_version": rid, "launcher_commit": commit, "bundle_version": bver,
            "bundle_matches": matches, "resume_from_lists": resume, "backup_present": backup,
            "installer_path": installer_path(sd), "message": msg}


def set_resume(userdata, on: bool) -> dict:
    """on=True: xoá file cờ (resume từ Games/Favorites BẬT). on=False: tạo file cờ rỗng (TẮT).
    Tạo bằng tên tạm rồi os.replace nên launcher không bao giờ thấy file dở; xoá một file thì vốn nguyên tử."""
    fp = flag_path(userdata)
    existed = fp.exists()
    if on:
        try:
            fp.unlink()
        except FileNotFoundError:
            pass
    else:
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_name(fp.name + ".tmp")
        tmp.write_bytes(b"")
        os.replace(tmp, fp)
    return {"resume_from_lists": on, "changed": existed == on}


def installer_argv(sd, action: str, dry_run: bool) -> list:
    """argv CỐ ĐỊNH cho hottabs.sh; thẻ gắn noexec nên luôn `sh <script>`."""
    if action not in ("install", "uninstall", "status"):
        raise ValueError(action)
    script = installer_path(sd)
    if script is None:
        raise FileNotFoundError("Không có hottabs/hottabs.sh trên thẻ")
    argv = ["sh", script, "--sd", str(sd)]
    if dry_run:
        argv.append("--dry-run")
    return argv + [action]


def run_installer(sd, action: str, dry_run: bool) -> dict:
    """Chạy hottabs.sh; không ném lỗi. {"ok", "returncode", "output", "dry_run"}."""
    try:
        argv = installer_argv(sd, action, dry_run)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "returncode": None, "output": str(e), "dry_run": dry_run}
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=INSTALLER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "returncode": None, "output": f"Không chạy được hottabs.sh: {e}", "dry_run": dry_run}
    out = p.stdout.decode("utf-8", errors="replace").strip()
    return {"ok": p.returncode == 0, "returncode": p.returncode, "output": out, "dry_run": dry_run}
