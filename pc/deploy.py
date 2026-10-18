#!/usr/bin/env python3
"""LoongMate - dựng gói cập nhật trên PC rồi đẩy lên Pocket 1 và cài có kiểm hash (chỉ dùng stdlib).

Thay quy trình tay: build.sh -> gói + manifest sha256 -> tar qua ssh -> `update.py verify` -> `apply`
-> so md5 từng file đã cài với gói -> in lệnh rollback.

  python pc/deploy.py --dry-run            # chỉ build + dựng + verify gói trên PC, không đụng máy
  python pc/deploy.py                      # triển khai thật vào <thẻ Leaf dò được>/Apps/mlp1
  python pc/deploy.py --apps-dir /userdata/lm-sandbox-c2/Apps --app-data /userdata/lm-sandbox-c2/data \\
                      --remote-dir /userdata/lm-sandbox-c2/updates      # thử trong sandbox

Gói có LoongMate.pak (kèm bin/lm-home native) và LoongMateAgent.pak; manifest ghi remove_paks=[LoongMateNative.pak] nên máy đã
cài app native riêng sẽ được gỡ nó (sao lưu, rollback trả lại). Cài xong gọi `cli.py rescan` để Leaf cập nhật danh sách Apps
(--no-rescan hoặc LOONGMATE_NO_RESCAN=1 để bỏ qua, vd. khi thử trong sandbox; rescan lỗi chỉ cảnh báo).

Đường dẫn trên máy mặc định được DÒ qua ssh (pc/device_paths.py): hai thẻ nhớ có thể đổi chỗ mount sau
mỗi lần khởi động, nên không ghi cứng /mnt/sdcard. --apps-dir/--app-data/--remote-dir ghi đè từng cái;
--sdcard chỉ định thẻ Leaf khi có hơn một thẻ. --dry-run cũng hỏi máy để in đích (--offline: không hỏi).

Từ chối chạy khi working tree git bẩn (trừ --allow-dirty). Version = `git rev-parse --short HEAD`.
Mã thoát: 0 ok | 1 lỗi (build/verify/apply/md5 lệch/không dò được thẻ Leaf) | 2 không kết nối được máy |
          3 sai cách dùng / nhiều thẻ Leaf (cần --sdcard).
"""
import argparse
import hashlib
import json
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import pull_saves  # noqa: E402  (dùng lại SshTransport: cùng khoá, cổng, kiểu báo lỗi)
import device_paths  # noqa: E402

PAKS = ("LoongMate.pak", "LoongMateAgent.pak")
REMOVE_PAKS = ("LoongMateNative.pak",)   # đã gộp vào LoongMate.pak (bin/lm-home); gỡ khỏi máy khi cập nhật
NATIVE_BIN = "LoongMate.pak/bin/lm-home"
DEFAULT_DEVICE_PYTHON = "/userdata/loongmate-runtime/python/bin/python3"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIX = (".pyc", ".partial")


class DeployError(Exception):
    pass


def sha256_file(p):
    return pull_saves.sha256_file(p)


def git(root, *args):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise DeployError("không tìm thấy lệnh 'git'")
    if p.returncode != 0:
        raise DeployError(f"git {' '.join(args)} lỗi: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout.decode("utf-8", "replace").strip()


def resolve_version(root, allow_dirty):
    dirty = git(root, "status", "--porcelain")
    if dirty and not allow_dirty:
        raise DeployError("working tree git đang bẩn; commit trước hoặc dùng --allow-dirty:\n" + dirty[:600])
    v = git(root, "rev-parse", "--short", "HEAD")
    return v + ("-dirty" if dirty else "")


def sources(root):
    """{tên pak: thư mục nguồn}. LoongMate.pak lấy từ kết quả build.sh (đã có bin/lm-home); pak kia chép nguyên từ pak/Apps/mlp1."""
    root = Path(root)
    src = {"LoongMate.pak": root / "pak" / "build" / "LoongMate.pak"}
    for n in PAKS[1:]:
        src[n] = root / "pak" / "Apps" / "mlp1" / n
    return src


def run_build(root):
    sh = shutil.which("sh")
    if not sh:
        raise DeployError("không tìm thấy 'sh' (cần Git Bash/WSL) để chạy pak/build.sh")
    p = subprocess.run([sh, "pak/build.sh"], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise DeployError("pak/build.sh lỗi:\n" + p.stdout.decode("utf-8", "replace")[-1500:])


def make_bundle(src_by_pak, out_dir, version, remove_paks=()):
    """Dựng <out_dir>/{manifest.json, <Pak>/...}. Bỏ __pycache__, *.pyc. remove_paks (nếu có) ghi vào manifest. Trả về manifest."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, src in src_by_pak.items():
        src = Path(src)
        if not src.is_dir():
            if name == "LoongMate.pak":
                raise DeployError(f"thiếu {src}")
            continue
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts) or p.name.endswith(SKIP_SUFFIX) or not p.is_file():
                continue
            dst = out / name / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
            files[f"{name}/{rel.as_posix()}"] = sha256_file(dst)
    manifest = {"schema": 1, "version": version, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": dict(sorted(files.items()))}
    if remove_paks:
        manifest["remove_paks"] = list(remove_paks)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_update_module(bundle):
    """Nạp chính engine/update.py nằm TRONG gói để verify bằng đúng mã sắp được cài."""
    p = Path(bundle) / "LoongMate.pak" / "engine" / "update.py"
    if not p.is_file():
        raise DeployError("gói thiếu LoongMate.pak/engine/update.py (build.sh chép engine/*.py vào pak)")
    # exec từ nguồn, KHÔNG import: import sẽ ghi __pycache__ vào gói và làm verify báo "file thừa".
    mod = types.ModuleType("update_in_bundle")
    mod.__file__ = str(p)
    exec(compile(p.read_text(encoding="utf-8"), str(p), "exec"), mod.__dict__)
    return mod


# --------------------------------------------------------------------------- máy

class Remote:
    """Bọc SshTransport: chạy lệnh, đẩy cả thư mục bằng tar qua ssh."""

    def __init__(self, transport):
        self.t = transport

    def run(self, cmd, timeout=300):
        return self.t._run(cmd, timeout=timeout)

    def run_json(self, cmd, timeout=600):
        """Chạy lệnh in JSON; mã thoát != 0 vẫn đọc JSON (update.py in kết quả lỗi rồi exit 1)."""
        try:
            p = subprocess.run(self.t._cmd(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise pull_saves.Unreachable("hết giờ chờ máy trả lời")
        if p.returncode == 255:
            self.t._fail(255, p.stderr)
        try:
            return json.loads(p.stdout.decode("utf-8")), p.returncode
        except ValueError:
            raise DeployError(f"lệnh trên máy không in JSON (mã {p.returncode}): "
                              f"{(p.stdout + p.stderr).decode('utf-8', 'replace')[:500]}")

    def upload_dir(self, local_dir, remote_dir):
        """tar qua ssh vào remote_dir (xoá bản cũ cùng tên trước). Mode 0755 cho launch.sh/bin, 0644 còn lại."""
        local = Path(local_dir)
        self.run(f"rm -rf {shlex.quote(remote_dir)} && mkdir -p {shlex.quote(remote_dir)}", timeout=60)
        errf = tempfile.TemporaryFile()
        proc = subprocess.Popen(self.t._cmd(f"tar -xf - -C {shlex.quote(remote_dir)}"), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errf)
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|") as tf:
                for p in sorted(local.rglob("*")):
                    rel = p.relative_to(local).as_posix()
                    ti = tarfile.TarInfo(rel)
                    ti.mtime = int(p.stat().st_mtime)
                    if p.is_dir():
                        ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                        tf.addfile(ti)
                    elif p.is_file():
                        parts = rel.split("/")
                        ex = len(parts) == 2 and parts[1] == "launch.sh" or len(parts) > 2 and parts[1] == "bin"
                        ti.mode, ti.size = (0o755 if ex else 0o644), p.stat().st_size
                        with open(p, "rb") as f:
                            tf.addfile(ti, f)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        rc = proc.wait()
        errf.seek(0)
        err = errf.read()
        errf.close()
        if rc != 0:
            self.t._fail(rc, err)

    def md5s(self, base_dir, relpaths):
        """{rel: md5} của file trên máy (chia lô để lệnh không quá dài)."""
        res = {}
        for i in range(0, len(relpaths), 40):
            batch = relpaths[i:i + 40]
            out = self.run(f"cd {shlex.quote(base_dir)} && md5sum -- " + " ".join(shlex.quote(r) for r in batch)).decode()
            for line in out.splitlines():
                h, _, name = line.partition("  ")
                res[name] = h
        return res


def md5_file(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run_rescan(remote, a):
    """Cho Leaf quét lại để bỏ mục app đã gỡ khỏi tab Apps. Không bao giờ ném lỗi / đổi kết quả cài: chỉ cảnh báo."""
    cli = posixpath.join(a.apps_dir, "LoongMate.pak", "engine", "cli.py")
    cmd = (f"{shlex.quote(a.device_python)} {shlex.quote(cli)} --userdata {shlex.quote(a.app_data)} --quiet --json rescan")
    try:
        r, rc = remote.run_json(cmd, timeout=120)
    except (DeployError, pull_saves.TransportError, pull_saves.Unreachable) as e:
        print(f"[deploy] CẢNH BÁO: không gọi được rescan ({e}). Hãy bấm tay System -> Rescan Library trên máy.")
        return False
    if rc != 0 or not r.get("ok"):
        print(f"[deploy] CẢNH BÁO: rescan không thành công ({json.dumps(r, ensure_ascii=False)[:300]}). "
              "Hãy bấm tay System -> Rescan Library trên máy.")
        return False
    print(f"[deploy] rescan OK: {r.get('message')}")
    return True


def deploy(remote, bundle, manifest, a):
    """Đẩy gói, verify + apply trên máy, so md5. Trả về (ok, rollback_cmd)."""
    ver = manifest["version"]
    rbundle = posixpath.join(a.remote_dir, ver)
    upd = f"{shlex.quote(a.device_python)} {shlex.quote(rbundle + '/LoongMate.pak/engine/update.py')}"
    opts = f"--apps-dir {shlex.quote(a.apps_dir)} --app-data {shlex.quote(a.app_data)}"
    rollback_cmd = f"{upd} rollback {opts} --yes"
    print(f"[deploy] đẩy gói {ver} ({len(manifest['files'])} file) -> {rbundle}")
    remote.upload_dir(bundle, rbundle)
    r, rc = remote.run_json(f"{upd} verify {shlex.quote(rbundle)} {opts}")
    if rc != 0 or not r.get("ok"):
        print("[deploy] LỖI: gói trên máy không qua verify (chưa cài gì):", json.dumps(r, ensure_ascii=False))
        return False, None
    print(f"[deploy] verify trên máy OK ({r['files']} file). Đang apply...")
    r, rc = remote.run_json(f"{upd} apply {shlex.quote(rbundle)} {opts} --yes")
    if rc != 0 or not r.get("ok"):
        print("[deploy] LỖI: apply thất bại, máy đã được trả về bản cũ:", json.dumps(r, ensure_ascii=False))
        return False, None
    print(f"[deploy] apply OK: {r['previous_version']} -> {r['version']}")
    local = {rel: md5_file(Path(bundle) / rel) for rel in manifest["files"]}
    got = remote.md5s(a.apps_dir, sorted(local))
    bad = [rel for rel, h in local.items() if got.get(rel) != h]
    noexec = remote.run(
        f"cd {shlex.quote(a.apps_dir)} && for p in {' '.join(sorted({r.split('/')[0] for r in local}))}; do "
        "[ -x \"$p/launch.sh\" ] || echo NOEXEC $p/launch.sh; "
        "for b in \"$p\"/bin/*; do [ -e \"$b\" ] && { [ -x \"$b\" ] || echo NOEXEC $b; }; done; done; true").decode().split()
    # pak đã gỡ phải biến mất khỏi apps-dir (và cả bản .old tạm), nếu không Leaf vẫn liệt kê nó trong tab Apps
    left = [n for n in manifest.get("remove_paks", [])
            if remote.run(f"cd {shlex.quote(a.apps_dir)} && ls -d {shlex.quote(n)} .{shlex.quote(n)}.old 2>/dev/null; true").decode().split()]
    if left:
        print(f"[deploy] CẢNH BÁO: pak đáng lẽ đã gỡ vẫn còn trong {a.apps_dir}: {left}")
    if bad or noexec or left:
        print(f"[deploy] CẢNH BÁO: {len(bad)} file lệch md5 {bad[:5]}; launch.sh không chạy được: {noexec}")
        print("[deploy] Rollback:", rollback_cmd.replace(upd, "ssh ... " + upd))
        return False, rollback_cmd
    print(f"[deploy] md5 khớp {len(local)}/{len(local)} file trên máy"
          + (f"; đã gỡ {', '.join(manifest['remove_paks'])}." if manifest.get("remove_paks") else "."))
    if a.no_rescan:
        print("[deploy] bỏ qua rescan (--no-rescan / LOONGMATE_NO_RESCAN=1): Leaf chưa cập nhật danh sách Apps.")
    else:
        run_rescan(remote, a)
    return True, rollback_cmd


def resolve_targets(transport, a):
    """Điền các đường dẫn trên máy mà người dùng không truyền (cờ truyền tay luôn thắng) từ thẻ Leaf dò được."""
    if a.apps_dir and a.app_data and a.remote_dir:
        return
    lp = device_paths.get(transport, a.sdcard)
    print(f"[deploy] Thẻ Leaf: {lp.sdcard} (userdata: {lp.userdata})")
    a.apps_dir = a.apps_dir or lp.apps_dir
    a.app_data = a.app_data or lp.app_data
    a.remote_dir = a.remote_dir or lp.remote_dir


def main(argv=None, transport=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Dựng + triển khai gói cập nhật LoongMate lên Pocket 1.")
    ap.add_argument("--dry-run", action="store_true", help="chỉ dựng và verify gói trên PC")
    ap.add_argument("--allow-dirty", action="store_true", help="cho phép working tree git bẩn (version thêm -dirty)")
    ap.add_argument("--skip-build", action="store_true", help="không chạy pak/build.sh (dùng pak/build có sẵn)")
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    ap.add_argument("--keep-bundle", type=Path, help="dựng gói vào thư mục này (mặc định: thư mục tạm, xoá sau)")
    ap.add_argument("--host", default=pull_saves.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=pull_saves.DEFAULT_PORT)
    ap.add_argument("--user", default=pull_saves.DEFAULT_USER)
    ap.add_argument("--key", type=Path, default=pull_saves.DEFAULT_KEY)
    ap.add_argument("--apps-dir", default=None, help="thư mục Apps trên máy (mặc định: <thẻ Leaf>/Apps/mlp1)")
    ap.add_argument("--app-data", default=None,
                    help="nơi lưu pak-backup và installed-version.json (mặc định: <USERDATA_PATH>/LoongMate)")
    ap.add_argument("--remote-dir", default=None,
                    help="nơi đặt gói trên máy trước khi cài (mặc định: <thẻ Leaf>/LoongMate-update)")
    ap.add_argument("--sdcard", default=None, help="thư mục gốc thẻ Leaf trên máy (khi có hơn một thẻ Leaf)")
    ap.add_argument("--offline", action="store_true", help="với --dry-run: không hỏi máy (không in đích dò được)")
    ap.add_argument("--device-python", default=DEFAULT_DEVICE_PYTHON)
    ap.add_argument("--no-rescan", action="store_true",
                    default=os.environ.get("LOONGMATE_NO_RESCAN") == "1",
                    help="không gọi Rescan Library của Leaf sau khi cài (dùng khi thử sandbox; cũng bật bằng LOONGMATE_NO_RESCAN=1)")
    a = ap.parse_args(argv)
    for flag, val in (("--apps-dir", a.apps_dir), ("--app-data", a.app_data), ("--remote-dir", a.remote_dir)):
        if val is None:
            continue
        if not val.startswith("/") or ":" in val or "'" in val:
            print(f"{flag} phải là đường dẫn tuyệt đối POSIX trên máy, nhận '{val}'. "
                  "(Git Bash tự đổi '/x' thành 'C:/...': đặt MSYS_NO_PATHCONV=1 hoặc chạy bằng PowerShell.)",
                  file=sys.stderr)
            return 3
    try:
        version = resolve_version(a.root, a.allow_dirty)
        if not a.skip_build:
            print("[deploy] chạy pak/build.sh ...")
            run_build(a.root)
        tmp = None
        if a.keep_bundle:
            bundle = a.keep_bundle
            if bundle.exists() and any(bundle.iterdir()):
                raise DeployError(f"--keep-bundle phải là thư mục trống: {bundle}")
        else:
            tmp = tempfile.TemporaryDirectory(prefix="lm-bundle-")
            bundle = Path(tmp.name)
        try:
            manifest = make_bundle(sources(a.root), bundle, version, REMOVE_PAKS)
            if NATIVE_BIN not in manifest["files"]:
                raise DeployError(f"gói thiếu {NATIVE_BIN}: chép native/build/lm-home vào pak/Apps/mlp1/LoongMate.pak/bin/ rồi build lại")
            v = load_update_module(bundle).verify_bundle(bundle)
            if not v["ok"]:
                raise DeployError("gói vừa dựng không qua verify: " + "; ".join(v["errors"][:5]))
            print(f"[deploy] gói {version}: {v['files']} file, pak: {', '.join(v['paks'])} - verify trên PC OK")
            if a.dry_run and a.offline:
                print("[deploy] --dry-run --offline: dừng tại đây, không đụng máy.")
                return 0
            t = transport or pull_saves.SshTransport(a.host, a.port, a.user, a.key)
            if a.dry_run:
                try:
                    resolve_targets(t, a)
                    print(f"[deploy] --dry-run: sẽ cài vào {a.apps_dir} (app-data {a.app_data}, gói tạm {a.remote_dir}). "
                          "Dừng tại đây, không đụng máy.")
                except (pull_saves.TransportError, device_paths.LeafCardError) as e:
                    print(f"[deploy] CẢNH BÁO: --dry-run không dò được đích trên máy: {e}")
                return 0
            resolve_targets(t, a)
            ok, rb = deploy(Remote(t), bundle, manifest, a)
            if rb:
                print("[deploy] Lệnh rollback (chạy trên máy):\n   " + rb)
            return 0 if ok else 1
        finally:
            if tmp:
                try:
                    tmp.cleanup()
                except OSError:
                    pass
    except pull_saves.Unreachable as e:
        print(f"MÁY KHÔNG TỚI ĐƯỢC: {e}", file=sys.stderr)
        return 2
    except device_paths.LeafCardAmbiguous as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 3
    except device_paths.LeafCardError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    except (DeployError, pull_saves.TransportError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
