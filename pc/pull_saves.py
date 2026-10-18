#!/usr/bin/env python3
"""LoongMate - PC tự kéo SaveVault từ Pocket 1 về qua SSH/Tailscale (chỉ dùng stdlib).

Máy KHÔNG có sftp/scp nên PC là bên kéo: chạy lệnh qua `ssh`, đọc stdout (luồng tar).
Định dạng vault (xem engine/savevault.py): content-addressed
    SaveVault/objects/<hash[:2]>/<hash>            (tên file = SHA-256 nội dung)
    SaveVault/snapshots/<id>/manifest.json         (committed: true, layout: "objects")
Quy tắc: object trước, manifest SAU CÙNG (giống commit marker của engine). Mọi object tải về
đều được băm lại; lệch -> xoá và báo lỗi. Không bao giờ xoá gì trên máy.

Sau mỗi lần kéo thành công + verify đạt, ghi <thư mục LoongMate trên máy>/offsite.json (mốc bản sao
ngoài máy cho giao diện). Kéo lỗi thì KHÔNG ghi.

Đường dẫn vault trên máy mặc định được DÒ qua ssh (pc/device_paths.py): <USERDATA_PATH của thẻ Leaf>/
LoongMate/SaveVault. --device-vault ghi đè; --sdcard chỉ định thẻ khi máy có hơn một thẻ Leaf.

Mã thoát: 0 ok | 1 có lỗi dữ liệu (hash lệch, thiếu object, verify hỏng) HOẶC máy không có thẻ Leaf dò được |
          2 không kết nối được máy | 3 sai cách dùng / người dùng không xác nhận / nhiều thẻ Leaf (cần --sdcard).
"""
import argparse
import hashlib
import io
import json
import logging
import os
import re
import posixpath
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import device_paths  # noqa: E402  (dò thẻ Leaf: thẻ có thể đổi chỗ mount sau mỗi lần boot)

# Lịch chạy bằng pythonw.exe (không có console): mỗi lần gọi ssh.exe không được bật cửa sổ.
_NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

DEFAULT_HOST = "pocket1"
DEFAULT_PORT = 2222
DEFAULT_USER = "sshadmin"
DEFAULT_KEY = Path.home() / ".ssh" / "pocket1_loongmate"
DEFAULT_DEST = Path.home() / "Pocket1Backup" / "SaveVault"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SNAP_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BATCH_MAX_FILES = 40
log = logging.getLogger("pull_saves")


class TransportError(Exception):
    """Lỗi chung khi nói chuyện với máy."""


class Unreachable(TransportError):
    """Không tới được máy (offline/ngủ/sai host/hết giờ)."""


# --------------------------------------------------------------------------- vận chuyển

class SshTransport:
    """Nói chuyện với vault trên máy qua ssh. Chỉ push() mới ghi lên máy (khi người dùng
    gọi --push-snapshot); mọi thứ còn lại chỉ đọc."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, user=DEFAULT_USER, key=DEFAULT_KEY,
                 vault=None, connect_timeout=15, sdcard=None):
        # vault=None: dò thẻ Leaf lúc dùng lần đầu (xem thuộc tính vault bên dưới)
        self._vault = vault.rstrip("/") if vault else None
        self.sdcard = sdcard
        self.host = host
        self.base = ["ssh", "-i", str(key), "-p", str(port),
                     "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                     "-o", f"ConnectTimeout={connect_timeout}",
                     "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                     f"{user}@{host}"]

    @property
    def vault(self):
        if self._vault is None:
            lp = device_paths.get(self, self.sdcard)
            self._vault = lp.vault
            log.info("Thẻ Leaf: %s (vault trên máy: %s)", lp.sdcard, self._vault)
        return self._vault

    def _cmd(self, remote):
        return self.base + [remote]

    def _fail(self, rc, err):
        err = err.decode("utf-8", "replace").strip() if isinstance(err, bytes) else str(err).strip()
        if rc == 255:
            raise Unreachable(f"Không kết nối được tới '{self.host}' qua ssh: {err or 'không rõ lý do'}")
        raise TransportError(f"Lệnh trên máy lỗi (mã {rc}): {err}")

    def _run(self, remote, timeout=90):
        try:
            p = subprocess.run(self._cmd(remote), **_NOWIN, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Unreachable(f"Hết {timeout}s mà máy '{self.host}' không trả lời (đang ngủ hoặc mất mạng?).")
        except FileNotFoundError:
            raise TransportError("Không tìm thấy lệnh 'ssh' trên PC (cần OpenSSH client).")
        if p.returncode != 0:
            self._fail(p.returncode, p.stderr)
        return p.stdout

    def list_objects(self):
        out = self._run(f"if [ -d '{self.vault}/objects' ]; then cd '{self.vault}' && find objects -type f -exec stat -c '%s %n' {{}} +; fi").decode()
        res = {}
        for line in out.splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            name = parts[1].rsplit("/", 1)[-1]
            if HASH_RE.match(name) and parts[1] == f"objects/{name[:2]}/{name}":
                res[name] = int(parts[0])
        return res

    def list_snapshot_ids(self):
        out = self._run(f"if [ -d '{self.vault}/snapshots' ]; then cd '{self.vault}' && ls snapshots; fi").decode()
        return sorted(s for s in out.split() if SNAP_RE.match(s))

    def fetch(self, relpaths):
        """Sinh (relpath, file-like) từ MỘT luồng tar. Phải đọc hết từng file trước khi lấy file kế."""
        if not relpaths:
            return
        remote = f"cd '{self.vault}' && tar -cf - " + " ".join(relpaths)
        errf = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(self._cmd(remote), **_NOWIN, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=errf)
        except FileNotFoundError:
            errf.close()
            raise TransportError("Không tìm thấy lệnh 'ssh' trên PC (cần OpenSSH client).")
        try:
            try:
                with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
                    for m in tf:
                        if m.isfile():
                            yield m.name, tf.extractfile(m)
            except tarfile.TarError as e:
                proc.kill()
                proc.wait()
                errf.seek(0)
                raise TransportError(f"Luồng tar từ máy hỏng/cụt: {e} {errf.read().decode('utf-8', 'replace')[:300]}")
            proc.stdout.read()  # xả phần đệm cuối
            rc = proc.wait()
            if rc != 0:
                errf.seek(0)
                self._fail(rc, errf.read())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            errf.close()

    def write_offsite(self, data):
        """Ghi nguyên tử <thư mục LoongMate>/offsite.json trên máy (cạnh thư mục SaveVault):
        đẩy vào offsite.json.partial -> kiểm SHA-256 bản .partial -> mv -> đọc lại so từng byte.
        Bài học: luồng rỗng vào ssh làm file đích 0 byte, nên không bao giờ mv khi chưa kiểm hash."""
        d = posixpath.dirname(self.vault)
        final, part = f"{d}/offsite.json", f"{d}/offsite.json.partial"
        try:
            p = subprocess.run(self._cmd(f"mkdir -p '{d}' && cat > '{part}'"), **_NOWIN, input=data,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        except subprocess.TimeoutExpired:
            raise Unreachable(f"Hết 90s mà máy '{self.host}' không trả lời khi ghi offsite.json.")
        except FileNotFoundError:
            raise TransportError("Không tìm thấy lệnh 'ssh' trên PC (cần OpenSSH client).")
        if p.returncode != 0:
            self._fail(p.returncode, p.stderr)
        want = hashlib.sha256(data).hexdigest()
        got = self._run(f"sha256sum '{part}'").decode().split()[:1]
        if got != [want]:
            self._run(f"rm -f '{part}'")
            raise TransportError("offsite.json.partial trên máy không khớp SHA-256 (file đích KHÔNG bị đổi)")
        self._run(f"mv -f '{part}' '{final}'")
        if self._run(f"cat '{final}'") != data:
            raise TransportError("Đọc lại offsite.json trên máy không khớp bản đã ghi")

    def push(self, members):
        """Đẩy [(relpath, Path|bytes)] lên vault: tar -> thư mục .incoming -> kiểm SHA-256 từng object
        (tên = hash) -> mv vào đúng chỗ. Có object lệch hash thì không mv gì cả."""
        if not members:
            return
        v = self.vault
        self._run(f"rm -rf '{v}/.incoming' && mkdir -p '{v}/.incoming'")
        errf = tempfile.TemporaryFile()
        proc = subprocess.Popen(self._cmd(f"tar -xf - -C '{v}/.incoming'"), **_NOWIN, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errf)
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|") as tf:
                for rel, src in members:
                    ti = tarfile.TarInfo(rel)
                    ti.mode, ti.mtime = 0o644, int(time.time())
                    if isinstance(src, (bytes, bytearray)):
                        ti.size = len(src)
                        tf.addfile(ti, io.BytesIO(bytes(src)))
                    else:
                        ti.size = Path(src).stat().st_size
                        with open(src, "rb") as f:
                            tf.addfile(ti, f)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # lý do thật nằm ở mã thoát + stderr của ssh bên dưới
        rc = proc.wait()
        errf.seek(0)
        err = errf.read()
        errf.close()
        if rc != 0:
            self._fail(rc, err)
        script = (
            f"cd '{v}/.incoming' && bad=0; "
            "for f in $(find objects -type f); do h=${f##*/}; set -- $(sha256sum $f); "
            "[ $1 = $h ] || { echo BAD $f; bad=1; }; done; "
            "[ $bad = 0 ] || exit 9; "
            f"cd '{v}' && for f in $(cd .incoming && find . -type f); do f=${{f#./}}; "
            "mkdir -p $(dirname $f); mv .incoming/$f $f || exit 8; done; "
            "rm -rf .incoming"
        )
        out = self._run(script, timeout=300)
        if b"BAD" in out:
            raise TransportError("Máy báo object lệch hash sau khi ghi: " + out.decode(errors="replace")[:300])


# --------------------------------------------------------------------------- tiện ích local

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def obj_path(dest, h):
    return Path(dest) / "objects" / h[:2] / h


def obj_ok_quick(dest, h, size):
    p = obj_path(dest, h)
    return p.is_file() and p.stat().st_size == size


def parse_manifest(data):
    """-> (manifest dict, {hash: size}) hoặc ném ValueError nếu không dùng được."""
    m = json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data)
    if m.get("committed") is not True:
        raise ValueError("chưa committed")
    if m.get("layout") != "objects":
        raise ValueError(f"layout '{m.get('layout')}' không được hỗ trợ (chỉ 'objects')")
    refs = {}
    for rel, info in (m.get("files") or {}).items():
        h, size = info.get("sha256"), info.get("size")
        if not (isinstance(h, str) and HASH_RE.match(h) and isinstance(size, int)):
            raise ValueError(f"mục '{rel}' thiếu sha256/size hợp lệ")
        if refs.setdefault(h, size) != size:
            raise ValueError(f"cùng hash nhưng size khác nhau ({rel})")
    return m, refs


def local_snapshots(dest):
    """{id: (manifest, refs)} của các snapshot committed hợp lệ trên PC."""
    res = {}
    sdir = Path(dest) / "snapshots"
    if not sdir.is_dir():
        return res
    for d in sorted(sdir.iterdir()):
        mf = d / "manifest.json"
        if d.is_dir() and SNAP_RE.match(d.name) and mf.is_file():
            try:
                res[d.name] = parse_manifest(mf.read_bytes())
            except (ValueError, OSError):
                pass
    return res


def write_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_stream(stream, final, expected_hash, expected_size):
    """Ghi stream -> final.partial, băm khi ghi; đúng hash+size mới os.replace.
    Trả về None nếu ok, chuỗi lỗi nếu hỏng (file .partial đã bị xoá)."""
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(final.name + ".partial")
    h, n = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as f:
            while True:
                b = stream.read(1 << 20)
                if not b:
                    break
                h.update(b)
                n += len(b)
                f.write(b)
            f.flush()
            os.fsync(f.fileno())
        if n != expected_size:
            raise ValueError(f"size {n} != {expected_size}")
        if h.hexdigest() != expected_hash:
            raise ValueError(f"hash lệch (nhận {h.hexdigest()[:12]}...)")
        os.replace(tmp, final)
        return None
    except (ValueError, OSError) as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return str(e)


# --------------------------------------------------------------------------- kéo

def _batches(items):
    for i in range(0, len(items), BATCH_MAX_FILES):
        yield items[i:i + BATCH_MAX_FILES]


def pull(transport, dest):
    """Kéo incremental. Trả về dict thống kê; st['errors'] là danh sách lỗi dữ liệu."""
    dest = Path(dest)
    st = {"snapshots_new": 0, "snapshots_skipped": [], "objects_downloaded": 0, "bytes_downloaded": 0,
          "objects_bad": 0, "errors": [], "warnings": []}

    # Liệt kê máy TRƯỚC khi tạo bất cứ thư mục nào: máy offline thì ném Unreachable ở đây và
    # không để lại thư mục rỗng trên PC.
    dev_objs = transport.list_objects()
    dev_ids = transport.list_snapshot_ids()
    (dest / "objects").mkdir(parents=True, exist_ok=True)
    (dest / "snapshots").mkdir(parents=True, exist_ok=True)
    for p in dest.rglob("*.partial"):  # rác của lần chạy bị ngắt
        p.unlink(missing_ok=True)
    local = local_snapshots(dest)

    # 1) manifest của snapshot mới: đọc vào RAM, CHƯA ghi đĩa
    paths = {f"snapshots/{s}/manifest.json": s for s in dev_ids if s not in local}
    pending = {}  # id -> (raw bytes, refs)
    for batch in _batches(list(paths)):
        seen = set()
        for rel, fobj in transport.fetch(batch):
            if rel not in paths:
                continue
            seen.add(rel)
            sid, raw = paths[rel], fobj.read()
            try:
                pending[sid] = (raw, parse_manifest(raw)[1])
            except (ValueError, json.JSONDecodeError) as e:
                st["snapshots_skipped"].append((sid, str(e)))
        for rel in batch:
            if rel not in seen:
                st["snapshots_skipped"].append((paths[rel], "không đọc được manifest"))

    # 2) object cần tải: mọi object snapshot (mới + cũ trên PC) tham chiếu mà PC chưa có đúng size
    wanted = {}
    for _, refs in pending.values():
        wanted.update(refs)
    for _, refs in local.values():
        wanted.update(refs)
    todo = []
    for h, size in sorted(wanted.items()):
        if obj_ok_quick(dest, h, size):
            continue
        obj_path(dest, h).unlink(missing_ok=True)  # sai size -> coi như hỏng
        if dev_objs.get(h) != size:
            st["warnings"].append(f"Máy không có object {h[:12]}... (size {size}); không thể tải")
            continue
        todo.append((h, size))
    log.info("Máy: %d object, %d snapshot. Cần tải %d object.", len(dev_objs), len(dev_ids), len(todo))

    # 3) tải theo lô, băm lại từng object
    for batch in _batches(todo):
        want = {f"objects/{h[:2]}/{h}": (h, s) for h, s in batch}
        got = set()
        for rel, fobj in transport.fetch(list(want)):
            if rel not in want:
                continue
            h, size = want[rel]
            got.add(rel)
            err = save_stream(fobj, obj_path(dest, h), h, size)
            if err:
                st["objects_bad"] += 1
                st["errors"].append(f"Object {h[:12]}... tải về bị hỏng và đã xoá: {err}")
                log.error(st["errors"][-1])
            else:
                st["objects_downloaded"] += 1
                st["bytes_downloaded"] += size
        for rel, (h, _) in want.items():
            if rel not in got:
                st["errors"].append(f"Máy không gửi object {h[:12]}...")
                log.error(st["errors"][-1])

    # 4) manifest SAU CÙNG, chỉ khi mọi object nó tham chiếu đã có đúng size
    for sid, (raw, refs) in sorted(pending.items()):
        missing = [h for h, s in refs.items() if not obj_ok_quick(dest, h, s)]
        if missing:
            st["snapshots_skipped"].append((sid, f"thiếu {len(missing)} object"))
            st["errors"].append(f"Snapshot {sid} chưa ghi: thiếu {len(missing)} object")
            log.error(st["errors"][-1])
            continue
        write_atomic(dest / "snapshots" / sid / "manifest.json", raw)
        st["snapshots_new"] += 1
    return st


# --------------------------------------------------------------------------- verify / prune

def verify(dest):
    """Băm lại toàn bộ object + kiểm mọi snapshot. Object hỏng bị xoá để lần pull sau tải lại."""
    dest = Path(dest)
    res = {"objects": 0, "bad_objects": [], "snapshots": 0, "bad_snapshots": {}}
    good = set()
    odir = dest / "objects"
    if odir.is_dir():
        for p in sorted(odir.rglob("*")):
            if not p.is_file() or p.name.endswith(".partial"):
                continue
            res["objects"] += 1
            if p.parent.name == p.name[:2] and sha256_file(p) == p.name:
                good.add(p.name)
            else:
                res["bad_objects"].append(p.name)
                p.unlink()
    for sid, (_, refs) in local_snapshots(dest).items():
        res["snapshots"] += 1
        bad = [h for h, s in refs.items() if h not in good or obj_path(dest, h).stat().st_size != s]
        if bad:
            res["bad_snapshots"][sid] = len(bad)
    res["ok"] = not res["bad_objects"] and not res["bad_snapshots"]
    return res


def prune(dest, keep):
    """Giữ `keep` snapshot committed mới nhất; xoá snapshot cũ và object không ai tham chiếu.
    Chỉ tác động trên PC."""
    dest = Path(dest)
    ids = sorted(local_snapshots(dest))
    removed = ids[:-keep] if keep < len(ids) else []
    for sid in removed:
        shutil.rmtree(dest / "snapshots" / sid, ignore_errors=True)
    referenced = set()
    for _, refs in local_snapshots(dest).values():
        referenced.update(refs)
    gc = 0
    odir = dest / "objects"
    if odir.is_dir():
        for shard in list(odir.iterdir()):
            if not shard.is_dir():
                continue
            for o in list(shard.iterdir()):
                if o.name not in referenced and not o.name.endswith(".partial"):
                    o.unlink()
                    gc += 1
            if not any(shard.iterdir()):
                shard.rmdir()
    return {"snapshots_removed": removed, "objects_gc": gc}


# --------------------------------------------------------------------------- mốc "bản sao ngoài máy"

def record_offsite(transport, dest):
    """Sau một lần pull thành công: băm lại toàn bộ bản trên PC, đạt thì ghi offsite.json lên máy
    (giao diện trên máy đọc file này để cảnh báo khi bản sao ngoài máy đã cũ).
    Trả về record đã ghi; None nếu PC chưa có snapshot nào (không có gì để gọi là bản sao);
    ném ValueError nếu verify không đạt."""
    v = verify(dest)
    if not v["ok"]:
        raise ValueError("Không ghi offsite.json: verify trên PC không đạt (%d object hỏng, %d snapshot hỏng)"
                         % (len(v["bad_objects"]), len(v["bad_snapshots"])))
    ids = sorted(local_snapshots(dest))
    if not ids:
        log.warning("Không ghi offsite.json: PC chưa có snapshot nào (chưa có bản sao ngoài máy).")
        return None
    now = time.time()
    rec = {"schema": 1, "last_pull_ts": int(now),
           "last_pull_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
           "pc_host": socket.gethostname(), "snapshot_ids": ids,
           "objects": v["objects"], "verified": True}
    transport.write_offsite(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    return rec


# --------------------------------------------------------------------------- đẩy ngược

def push_snapshot(transport, dest, sid):
    """Đẩy snapshot từ PC về vault trên máy: object (đã băm kiểm) trước, manifest SAU CÙNG.
    Ném ValueError/TransportError nếu không an toàn."""
    dest = Path(dest)
    if not SNAP_RE.match(sid):
        raise ValueError(f"ID snapshot không hợp lệ: {sid}")
    snaps = local_snapshots(dest)
    if sid not in snaps:
        raise ValueError(f"PC không có snapshot committed '{sid}'")
    raw = (dest / "snapshots" / sid / "manifest.json").read_bytes()
    refs = snaps[sid][1]
    for h, size in refs.items():
        if not obj_ok_quick(dest, h, size) or sha256_file(obj_path(dest, h)) != h:
            raise ValueError(f"Object {h[:12]}... trên PC thiếu hoặc hỏng; chạy --verify rồi kéo lại trước khi đẩy")
    if sid in transport.list_snapshot_ids():
        raise ValueError(f"Máy đã có snapshot '{sid}'; không ghi đè")
    dev = transport.list_objects()
    todo = [(h, s) for h, s in sorted(refs.items()) if dev.get(h) != s]
    for batch in _batches(todo):
        transport.push([(f"objects/{h[:2]}/{h}", obj_path(dest, h)) for h, _ in batch])
    transport.push([(f"snapshots/{sid}/manifest.json", raw)])  # commit marker cuối cùng
    back = dict((rel, f.read()) for rel, f in transport.fetch([f"snapshots/{sid}/manifest.json"]))
    if back.get(f"snapshots/{sid}/manifest.json") != raw:
        raise TransportError("Đọc lại manifest trên máy không khớp bản đã đẩy")
    return {"objects_pushed": len(todo), "objects_already": len(refs) - len(todo),
            "bytes_pushed": sum(s for _, s in todo)}


# --------------------------------------------------------------------------- CLI

def setup_logging(logfile):
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    try:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError as e:
        print(f"Cảnh báo: không ghi được log {logfile}: {e}", file=sys.stderr)


def main(argv=None, transport=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Kéo SaveVault từ Pocket 1 về PC qua ssh/Tailscale.")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="thư mục SaveVault trên PC")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--key", type=Path, default=DEFAULT_KEY)
    ap.add_argument("--device-vault", default=None,
                    help="đường dẫn SaveVault trên máy (mặc định: dò thẻ Leaf, <USERDATA_PATH>/LoongMate/SaveVault)")
    ap.add_argument("--sdcard", default=None, help="thư mục gốc thẻ Leaf trên máy (khi máy có hơn một thẻ Leaf)")
    ap.add_argument("--verify", action="store_true", help="chỉ băm lại toàn bộ bản trên PC (không cần mạng)")
    ap.add_argument("--keep", "--prune", dest="prune", type=int, metavar="N",
                    help="sau khi kéo THÀNH CÔNG (không lỗi): giữ N snapshot mới nhất trên PC, GC object mồ côi")
    ap.add_argument("--push-snapshot", metavar="ID", help="đẩy snapshot từ PC ngược về vault trên máy")
    ap.add_argument("--yes", action="store_true", help="bỏ qua bước xác nhận của --push-snapshot")
    ap.add_argument("--log", type=Path, help="file log (mặc định: pull.log cạnh thư mục SaveVault)")
    a = ap.parse_args(argv)
    if a.prune is not None and a.prune < 1:
        print("--keep/--prune phải >= 1", file=sys.stderr)
        return 3
    setup_logging(a.log or a.dest.parent / "pull.log")

    if a.verify:
        r = verify(a.dest)
        log.info("Verify: %d object, %d snapshot.", r["objects"], r["snapshots"])
        if not r["ok"]:
            for h in r["bad_objects"]:
                log.error("Object hỏng (đã xoá, kéo lại để sửa): %s", h)
            for s, n in r["bad_snapshots"].items():
                log.error("Snapshot %s thiếu/hỏng %d object", s, n)
            return 1
        log.info("VERIFY OK: mọi object khớp SHA-256.")
        return 0

    t = transport or SshTransport(a.host, a.port, a.user, a.key, a.device_vault, sdcard=a.sdcard)
    try:
        if a.push_snapshot:
            log.warning("Sắp GHI snapshot '%s' vào vault trên máy: %s", a.push_snapshot, t.vault)
            if not a.yes:
                if not sys.stdin.isatty():
                    log.error("Cần xác nhận tương tác hoặc --yes.")
                    return 3
                if input("Gõ 'yes' để tiếp tục: ").strip() != "yes":
                    log.error("Đã huỷ.")
                    return 3
            r = push_snapshot(t, a.dest, a.push_snapshot)
            log.info("Đã đẩy: %d object mới (%d đã có), manifest ghi sau cùng. "
                     "Giờ dùng LoongMate Restore trên máy.", r["objects_pushed"], r["objects_already"])
            return 0

        t0 = time.time()
        st = pull(t, a.dest)
        log.info("Xong sau %.1fs: %d snapshot mới, %d object tải (%.1f MB), %d object hỏng.",
                 time.time() - t0, st["snapshots_new"], st["objects_downloaded"],
                 st["bytes_downloaded"] / 1e6, st["objects_bad"])
        for w in st["warnings"]:
            log.warning(w)
        for s, why in st["snapshots_skipped"]:
            log.warning("Snapshot %s bỏ qua: %s", s, why)
        if a.prune and not st["errors"]:
            log.info("Prune: %s", prune(a.dest, a.prune))
        elif a.prune:
            log.warning("Có lỗi nên KHÔNG prune.")
        if st["errors"]:
            log.warning("Có lỗi nên KHÔNG ghi offsite.json (mốc bản sao ngoài máy giữ nguyên).")
            return 1
        rec = record_offsite(t, a.dest)
        if rec:
            log.info("Đã ghi offsite.json lên máy: %d snapshot, %d object, đã xác minh.",
                     len(rec["snapshot_ids"]), rec["objects"])
            return 0
        return 0
    except Unreachable as e:
        log.error("MÁY KHÔNG TỚI ĐƯỢC: %s", e)
        return 2
    except device_paths.LeafCardAmbiguous as e:
        log.error("%s", e)
        return 3
    except device_paths.LeafCardError as e:
        log.error("Lỗi: %s", e)
        return 1
    except (TransportError, ValueError) as e:
        log.error("Lỗi: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
