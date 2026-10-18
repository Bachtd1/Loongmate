#!/usr/bin/env python3
"""LoongMate - đẩy file ROM/.zip/thư mục game từ PC vào <thẻ đích>/Import/ trên Pocket 1 (chỉ dùng stdlib).

CHỈ đẩy nội dung bạn có quyền sử dụng (bản dump của chính bạn, homebrew). Script không tải gì từ internet.

  python pc/import_push.py Game.gba Pack.zip "Disc Game/"                 # đẩy vào Import/ của thẻ PHỤ
  python pc/import_push.py Game.zip --card primary                         # vào thẻ Leaf (chỉ khi bạn chủ động chọn)
  python pc/import_push.py Game.zip --apply                                # đẩy rồi chạy `import scan` + `import plan` trên máy
  python pc/import_push.py Game.zip --apply --yes                          # ... và cài thật (--choice/--conflict chuyển xuống máy)

Cách làm an toàn:
  * Thẻ được DÒ trên máy (pc/device_paths.py: thẻ Leaf = thẻ có env.sh; thẻ phụ = thẻ khác đang mount trong danh sách ứng viên). Hai thẻ có thể đổi chỗ sau mỗi lần khởi động nên không ghi cứng.
  * Mặc định thẻ PHỤ. Không có thẻ phụ -> báo lỗi, KHÔNG tự chuyển sang thẻ Leaf (dùng --card primary để chọn).
  * File đến một thư mục tạm ẩn <thẻ>/Import/.incoming-<giờ>/ (tar qua ssh), so SHA-256 từng file với bản trên PC;
    chỉ khi KHỚP hết mới chuyển ra Import/. Lệch -> xoá thư mục tạm, báo lỗi, Import/ không có gì mới.
  * Không ghi đè: trùng tên với file/thư mục đã có trong Import/ -> từ chối cả lượt.
  * (bài học deploy) không bao giờ ghép luồng stdin của ssh với `< /dev/null`.

Mã thoát: 0 ok | 1 lỗi dữ liệu (SHA-256 lệch, không đủ chỗ, trùng tên, lệnh trên máy lỗi) | 2 không kết nối được máy |
          3 sai cách dùng / không có thẻ phụ / nhiều thẻ Leaf.
"""
import argparse
import hashlib
import json
import os
import posixpath
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pull_saves  # noqa: E402
import device_paths  # noqa: E402

DEFAULT_DEVICE_PYTHON = "/userdata/loongmate-runtime/python/bin/python3"
RESERVE = 128 * 1024 * 1024
HASH_PY = ("import hashlib,sys\n"
           "for p in sys.argv[1:]:\n"
           "    h=hashlib.sha256()\n"
           "    with open(p,'rb') as f:\n"
           "        for b in iter(lambda:f.read(1<<20),b''): h.update(b)\n"
           "    print(h.hexdigest()+'  '+p)\n")


class PushError(Exception):
    pass


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def collect(paths):
    """[(đường dẫn local, tên trong tar)] cho file; thư mục thì đi đệ quy (không theo symlink). Từ chối symlink/lạ."""
    out, tops = [], []
    for raw in paths:
        p = Path(raw)
        if p.is_symlink() or not p.exists():
            raise PushError(f"'{raw}' không tồn tại hoặc là symlink")
        if p.is_file():
            out.append((p, p.name))
            tops.append(p.name)
        elif p.is_dir():
            base = p.resolve()
            top = base.name
            tops.append(top)
            for dp, dn, fn in os.walk(base):
                for d in list(dn):
                    if (Path(dp) / d).is_symlink():
                        raise PushError(f"'{Path(dp) / d}' là symlink")
                for f in fn:
                    fp = Path(dp) / f
                    if fp.is_symlink() or not fp.is_file():
                        raise PushError(f"'{fp}' không phải file thường")
                    out.append((fp, f"{top}/{fp.relative_to(base).as_posix()}"))
        else:
            raise PushError(f"'{raw}' không phải file/thư mục thường")
    for t in tops:
        if t.startswith(".") or "/" in t or "\\" in t or "'" in t or "\n" in t:
            raise PushError(f"tên '{t}' không an toàn để đẩy (bắt đầu bằng '.', chứa dấu gạch/nháy/xuống dòng)")
    if len(set(t.lower() for t in tops)) != len(tops):
        raise PushError("hai mục cùng tên (không phân biệt hoa/thường)")
    if not out:
        raise PushError("không có file nào để đẩy")
    return out, tops


class SshRemote:
    """Mọi thao tác trên máy qua ssh (argv cố định, không ghép dữ liệu từ máy vào lệnh khác ngoài đường dẫn đã kiểm)."""

    def __init__(self, transport):
        self.t = transport

    def run(self, cmd, timeout=120):
        return self.t._run(cmd, timeout=timeout)

    def leaf(self, sdcard=None):
        return device_paths.get(self.t, sdcard)

    def secondary(self, leaf):
        """Thẻ phụ = thẻ NHỎ KHÁC đang mount trong danh sách ứng viên của device_paths (không phải thẻ Leaf).
        KHÔNG nạp env.sh để lấy UMRK_SECONDARY_SDCARD_PATH: env.sh chỉ điền mặc định '/media/sdcard1' nên khi hai thẻ đã
        đổi chỗ (thẻ Leaf ở /media/sdcard1) nó trả CHÍNH thẻ Leaf (đã kiểm trên máy thật 2026-10-04); giá trị đúng do tiến trình
        khởi động của Leaf đặt vào môi trường của Leaf (jawakad có UMRK_SECONDARY_SDCARD_PATH=/mnt/sdcard) mà ssh không thừa hưởng."""
        cand = " ".join(shlex.quote(c) for c in device_paths.CANDIDATES)
        script = (f"for c in {cand}; do real=$(cd \"$c\" 2>/dev/null && pwd -P) || continue; "
                  "grep -q \" $real \" /proc/mounts && echo \"MNT:$real\"; done; true")
        mounted = []
        for line in self.run(script).decode("utf-8", "replace").splitlines():
            if line.startswith("MNT:"):
                m = line[4:].rstrip("/")
                if not device_paths._SAFE.match(m):
                    raise PushError(f"đường dẫn thẻ lạ, không dám dùng: {m!r}")
                if m != leaf.sdcard and m not in mounted:
                    mounted.append(m)
        if len(mounted) > 1:
            raise PushError("Thấy nhiều hơn một thẻ ngoài thẻ Leaf: " + ", ".join(mounted) + " — không đoán thẻ phụ là thẻ nào.")
        return mounted[0] if mounted else None

    def free_bytes(self, d):
        out = self.run(f"df -Pk {shlex.quote(d)} | tail -n 1").decode().split()
        try:
            return int(out[3]) * 1024
        except (IndexError, ValueError):
            raise PushError(f"không đọc được dung lượng trống của {d}: {out}")

    def mkdir(self, d):
        self.run(f"mkdir -p {shlex.quote(d)}")

    def listdir(self, d):
        out = self.run(f"[ -d {shlex.quote(d)} ] && ls -A {shlex.quote(d)}; true").decode("utf-8", "replace")
        return [x for x in out.split("\n") if x]

    def upload(self, files, dest_dir):
        errf = tempfile.TemporaryFile()
        proc = subprocess.Popen(self.t._cmd(f"tar -xf - -C {shlex.quote(dest_dir)}"), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errf, **pull_saves._NOWIN)
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|") as tf:
                for local, arc in files:
                    ti = tarfile.TarInfo(arc)
                    ti.mtime, ti.size, ti.mode = int(local.stat().st_mtime), local.stat().st_size, 0o644
                    with open(local, "rb") as f:
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

    def sha256s(self, base, names, python):
        res = {}
        for i in range(0, len(names), 40):
            batch = names[i:i + 40]
            cmd = f"cd {shlex.quote(base)} && {shlex.quote(python)} -c {shlex.quote(HASH_PY)} " + " ".join(shlex.quote(n) for n in batch)
            for line in self.run(cmd, timeout=600).decode("utf-8", "replace").splitlines():
                h, _, name = line.partition("  ")
                res[name] = h
        return res

    def promote(self, incoming, dest, tops):
        moves = " && ".join(f"mv -- {shlex.quote(t)} {shlex.quote(dest + '/' + t)}" for t in tops)
        self.run(f"cd {shlex.quote(incoming)} && {moves} && cd / && rmdir {shlex.quote(incoming)}")

    def rmtree(self, d):
        if not posixpath.basename(d).startswith(".incoming-"):
            raise PushError(f"từ chối xoá '{d}' (không phải thư mục tạm .incoming-*)")
        self.run(f"rm -rf -- {shlex.quote(d)}")

    def cli(self, engine_dir, python, leaf, secondary, args, timeout=900):
        cmd = [python, posixpath.join(engine_dir, "cli.py"), "--primary", leaf.sdcard]
        if secondary:
            cmd += ["--secondary", secondary]
        cmd += ["--userdata", leaf.app_data, "--quiet", "--json", "import"] + list(args)
        try:
            p = subprocess.run(self.t._cmd(" ".join(shlex.quote(c) for c in cmd)), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, **pull_saves._NOWIN)
        except subprocess.TimeoutExpired:
            raise pull_saves.Unreachable("hết giờ chờ máy trả lời")
        if p.returncode == 255:
            self.t._fail(255, p.stderr)
        try:
            return json.loads(p.stdout.decode("utf-8")), p.returncode
        except ValueError:
            raise PushError(f"lệnh trên máy không in JSON (mã {p.returncode}): {(p.stdout + p.stderr).decode('utf-8', 'replace')[:600]}")


def push(remote, files, tops, card, sdcard=None, python=DEFAULT_DEVICE_PYTHON, say=print, secondary=None):
    """Đẩy + xác minh. Trả (thẻ đích, thư mục Import/, leaf, thẻ phụ). Ném PushError khi không an toàn."""
    leaf = remote.leaf(sdcard)
    sec = secondary or remote.secondary(leaf)       # --secondary-sdcard: chỉ định tay (thử sandbox), bỏ qua dò
    if card == "secondary":
        if not sec:
            raise PushError("Không có thẻ phụ đang gắn. "
                            "Không tự chuyển sang thẻ Leaf: chạy lại với --card primary nếu bạn muốn dùng thẻ Leaf.")
        root = sec
    else:
        root = leaf.sdcard
    say(f"[import_push] thẻ đích: {root} ({'thẻ phụ' if card == 'secondary' else 'thẻ Leaf'}); thẻ Leaf: {leaf.sdcard}")
    dest = posixpath.join(root, "Import")
    total = sum(p.stat().st_size for p, _ in files)
    remote.mkdir(dest)
    free = remote.free_bytes(dest)
    if free < total + RESERVE:
        raise PushError(f"không đủ chỗ trên {root}: trống {free // 2**20} MB, cần {(total + RESERVE) // 2**20} MB")
    exist = {x.lower() for x in remote.listdir(dest)}
    clash = [t for t in tops if t.lower() in exist]
    if clash:
        raise PushError(f"Import/ đã có: {', '.join(clash)} — không ghi đè (đổi tên hoặc xoá bản cũ trên máy)")
    incoming = posixpath.join(dest, ".incoming-" + time.strftime("%Y%m%d-%H%M%S"))
    remote.mkdir(incoming)
    try:
        say(f"[import_push] đẩy {len(files)} file ({total / 2**20:.1f} MB) -> {incoming}")
        t0 = time.monotonic()
        remote.upload(files, incoming)
        say(f"[import_push] đã gửi trong {time.monotonic() - t0:.1f}s; so SHA-256 từng file...")
        got = remote.sha256s(incoming, [a for _, a in files], python)
        bad = [a for p, a in files if got.get(a) != sha256_file(p)]
        if bad:
            raise PushError(f"SHA-256 trên máy lệch với PC ở {len(bad)} file: {bad[:5]} — đã xoá thư mục tạm, Import/ không thay đổi")
        remote.promote(incoming, dest, tops)
    except BaseException:
        try:
            remote.rmtree(incoming)
        except Exception:
            pass
        raise
    say(f"[import_push] OK: {len(files)} file, SHA-256 khớp, đã vào {dest}")
    return root, dest, leaf, sec


def pushed_units(units, dest, tops):
    """Chỉ các unit đến từ ĐÚNG những file/thư mục vừa đẩy (không đụng thứ khác đang nằm sẵn trong Import/)."""
    want = {t.lower() for t in tops}

    base = dest.replace("\\", "/").rstrip("/")

    def hit(path):
        d, _, name = path.replace("\\", "/").rpartition("/")
        return d == base and name.lower() in want
    return [u for u in units if hit(u["source"]) or any(hit(o) for o in u.get("origin", []))]


def apply_on_device(remote, leaf, sec, card, args, dest, tops, say=print):
    engine = args.engine_dir or posixpath.join(leaf.apps_dir, "LoongMate.pak", "engine")
    py = args.device_python
    scan, rc = remote.cli(engine, py, leaf, sec, ["scan"])
    mine = pushed_units(scan.get("units", []), dest, tops)
    say(f"[import_push] import scan: {len(mine)} mục từ file vừa đẩy (Import/ có tổng {len(scan.get('units', []))} mục; các mục khác KHÔNG bị đụng)")
    for u in mine:
        say(f"   [{u['status']}] {u['name']} -> {u.get('system') or ('?' + '/'.join(u.get('candidates', [])))}"
            + "".join(f"\n       ! {r}" for r in u.get("reasons", [])))
    if not mine:
        say("[import_push] không có mục nào nhận diện được từ file vừa đẩy.")
        return 1
    plan_file = posixpath.join(leaf.app_data, "jobs", "pc-import.plan.json")
    remote.mkdir(posixpath.dirname(plan_file))
    plan_args = ["plan", "--card", card, "--plan-out", plan_file]
    for u in mine:
        plan_args += ["--select", u["id"]]
    for c in args.choice or []:
        plan_args += ["--choice", c]
    plan, rc = remote.cli(engine, py, leaf, sec, plan_args)
    say(f"[import_push] import plan: {plan.get('card', {}).get('reason')}")
    for u in plan.get("units", []):
        say(f"   [{u.get('action')}] {u['name']} ({u.get('system') or '?'})")
    if not plan.get("ok"):
        say("[import_push] kế hoạch chưa chọn được thẻ đích — không cài.")
        return 1
    if not args.yes:
        say("[import_push] chưa cài (thiếu --yes). Xem kế hoạch ở trên; chạy lại với --apply --yes để cài thật.")
        return 0
    ap = ["apply", "--plan", plan_file, "--yes"]
    for c in args.choice or []:
        ap += ["--choice", c]
    for c in args.conflict or []:
        ap += ["--conflict", c]
    res, rc = remote.cli(engine, py, leaf, sec, ap)
    say(f"[import_push] import apply: {res.get('message')}")
    for r in res.get("results", []):
        say(f"   [{r['status']}] {r['name']}: {r['message']}")
    return 0 if res.get("success") else 1


def main(argv=None, transport=None, remote=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Đẩy file game của bạn từ PC vào <thẻ>/Import/ trên Pocket 1 (có so SHA-256).")
    ap.add_argument("files", nargs="+", help="file ROM / .zip / thư mục game")
    ap.add_argument("--card", choices=["secondary", "primary"], default="secondary",
                    help="secondary (mặc định) = thẻ phụ; primary = thẻ Leaf (chỉ khi bạn chủ động chọn)")
    ap.add_argument("--apply", action="store_true", help="sau khi đẩy: chạy import scan + plan trên máy và in kết quả")
    ap.add_argument("--yes", action="store_true", help="với --apply: cài thật (không có --yes thì chỉ xem kế hoạch)")
    ap.add_argument("--choice", action="append", metavar="ID=HỆ", help="với --apply: chọn hệ cho mục mơ hồ")
    ap.add_argument("--conflict", action="append", metavar="ID=ACTION", help="với --apply --yes: keep|replace|copy|cancel")
    ap.add_argument("--host", default=pull_saves.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=pull_saves.DEFAULT_PORT)
    ap.add_argument("--user", default=pull_saves.DEFAULT_USER)
    ap.add_argument("--key", type=Path, default=pull_saves.DEFAULT_KEY)
    ap.add_argument("--sdcard", default=None, help="thư mục gốc thẻ Leaf trên máy (khi có hơn một thẻ Leaf)")
    ap.add_argument("--secondary-sdcard", default=None, help="thư mục gốc thẻ phụ do bạn chỉ định tay (bỏ qua việc dò; dùng khi thử sandbox)")
    ap.add_argument("--device-python", default=DEFAULT_DEVICE_PYTHON)
    ap.add_argument("--engine-dir", default=None, help="thư mục engine/ trên máy (mặc định: <thẻ Leaf>/Apps/mlp1/LoongMate.pak/engine)")
    a = ap.parse_args(argv)
    if (a.yes or a.choice or a.conflict) and not a.apply:
        print("--yes/--choice/--conflict chỉ dùng cùng --apply", file=sys.stderr)
        return 3
    for flag, val in (("--sdcard", a.sdcard), ("--secondary-sdcard", a.secondary_sdcard)):
        if val is not None and not device_paths._SAFE.match(val):
            print(f"{flag} phải là đường dẫn tuyệt đối POSIX sạch trên máy, nhận '{val}'. (Git Bash đổi '/x' thành 'C:/...': đặt MSYS_NO_PATHCONV=1.)", file=sys.stderr)
            return 3
    try:
        files, tops = collect(a.files)
    except PushError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 3
    try:
        rem = remote or SshRemote(transport or pull_saves.SshTransport(a.host, a.port, a.user, a.key))
        root, dest, leaf, sec = push(rem, files, tops, a.card, a.sdcard, a.device_python, secondary=a.secondary_sdcard)
        if a.apply:
            return apply_on_device(rem, leaf, sec, "auto" if a.card == "secondary" else "primary", a, dest, tops)
        return 0
    except pull_saves.Unreachable as e:
        print(f"MÁY KHÔNG TỚI ĐƯỢC: {e}", file=sys.stderr)
        return 2
    except device_paths.LeafCardAmbiguous as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 3
    except PushError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 3 if "thẻ phụ" in str(e) else 1
    except (device_paths.LeafCardError, pull_saves.TransportError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
