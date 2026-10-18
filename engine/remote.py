"""
Remote backup transports for LoongMate SaveVault.

Two transports (LocalFolderTransport for a second SD card/USB folder, SftpTransport
for PC/NAS/Raspberry Pi over SSH) share one sync algorithm, RemoteVault, which is
naturally incremental thanks to SaveVault's content-addressed object store: only
objects the remote doesn't already have are uploaded, and the snapshot manifest.json
is always written LAST as the commit marker — "Snapshot manifest phải ghi sau cùng;
snapshot thiếu commit marker được coi là incomplete" (tài liệu nghiên cứu, mục "Bảo
mật và dữ liệu").

SftpTransport only shells out to the system `ssh`/`sftp`/`ssh-keyscan`/`ssh-keygen`
binaries via argv arrays (never shell=True, never string-concatenated commands) and
never accepts a password: host-key pinning is mandatory and BatchMode=yes +
PasswordAuthentication=no guarantee a broken/missing auth setup fails fast instead of
hanging on a password prompt.

# CHƯA KIỂM CHỨNG TRÊN MÁY THẬT: SftpTransport chỉ được kiểm bằng runner giả (fake
# runner) mô phỏng `sftp -b`/`ssh-keyscan`/`ssh-keygen` trong bộ test; chưa có lần
# nào chạy áp vào một server SFTP/SSH thật hay chạy trên chính Leaf/Pocket 1. Việc
# Leaf có sẵn binary `sftp`/`ssh-keyscan` hay không cũng CHƯA kiểm chứng — đó là lý
# do probe_sftp_capability() tồn tại và đưa ra hành động cụ thể khi thiếu.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .savevault import SaveVault

# Transport-relative paths are restricted to this character set, mirroring the
# local-side canonicalize-and-check-root rule from inventory.is_safe_relative_path:
# a snapshot_id or object hash coming from anywhere hostile must never be able to
# reference a path outside the remote root.
_SAFE_REL_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

_SHA256_FP_RE = re.compile(r"(SHA256:[A-Za-z0-9+/=]+)")


def _safe_rel(rel: str) -> str:
    """Validates a transport-relative path. Raises ValueError on anything unsafe.
    Rules: only [A-Za-z0-9._/-], no '..' path segment, must not start with '/'.
    Called by every RemoteTransport operation on both transports.
    """
    if not rel or not _SAFE_REL_RE.match(rel):
        raise ValueError(f"Đường dẫn remote không hợp lệ: '{rel}' (chỉ cho phép chữ/số/._/- )")
    if rel.startswith("/"):
        raise ValueError(f"Đường dẫn remote không hợp lệ: '{rel}' (không được bắt đầu bằng '/')")
    if ".." in rel.split("/"):
        raise ValueError(f"Đường dẫn remote không hợp lệ: '{rel}' (không được chứa '..')")
    return rel


class RemoteTransport:
    """Abstract minimal remote filesystem interface used by RemoteVault."""

    def exists(self, rel: str) -> bool:
        raise NotImplementedError

    def list_dir(self, rel: str) -> List[str]:
        """Returns entry names directly under rel, or [] if rel does not exist."""
        raise NotImplementedError

    def makedirs(self, rel: str) -> None:
        raise NotImplementedError

    def put_atomic(self, local: Path, rel: str) -> None:
        """Uploads local -> rel via rel+'.partial' then rename to rel."""
        raise NotImplementedError

    def get(self, rel: str, local: Path) -> None:
        raise NotImplementedError

    def size(self, rel: str) -> Optional[int]:
        """Returns the remote file's size in bytes, or None if it cannot be determined."""
        raise NotImplementedError

    def remove(self, rel: str) -> None:
        raise NotImplementedError


class LocalFolderTransport(RemoteTransport):
    """Backend for a second SD card, USB drive, or any locally-mounted folder."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, rel: str) -> Path:
        return self.root / _safe_rel(rel)

    def exists(self, rel: str) -> bool:
        return self._path(rel).exists()

    def list_dir(self, rel: str) -> List[str]:
        p = self._path(rel)
        if not p.is_dir():
            return []
        return sorted(e.name for e in p.iterdir())

    def makedirs(self, rel: str) -> None:
        self._path(rel).mkdir(parents=True, exist_ok=True)

    def put_atomic(self, local: Path, rel: str) -> None:
        dst = self._path(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.parent / f"{dst.name}.partial"
        with open(local, "rb") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        os.replace(tmp, dst)

    def get(self, rel: str, local: Path) -> None:
        src = self._path(rel)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.parent / f"{local.name}.partial"
        with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        os.replace(tmp, local)

    def size(self, rel: str) -> Optional[int]:
        try:
            return self._path(rel).stat().st_size
        except OSError:
            return None

    def remove(self, rel: str) -> None:
        p = self._path(rel)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass


def probe_sftp_capability() -> Dict[str, Any]:
    """Checks whether the `ssh`/`sftp` binaries are available on PATH of THIS
    (the calling) machine. Does not check the remote side.
    """
    ssh = shutil.which("ssh")
    sftp = shutil.which("sftp")
    available = bool(ssh and sftp)
    if available:
        reason = "Có đủ lệnh ssh và sftp trên PATH."
    else:
        missing = [name for name, path in (("ssh", ssh), ("sftp", sftp)) if not path]
        reason = (
            f"Thiếu lệnh {', '.join(missing)} trên PATH. Thiết bị không có lệnh `sftp`. "
            f"Kiểm bằng `command -v sftp` qua SSH; nếu thiếu, dùng endpoint loại `local` "
            f"(thẻ SD2/USB) hoặc để PC kéo về (xem TAILSCALE.md mục 7)."
        )
    return {"available": available, "ssh": ssh, "sftp": sftp, "reason": reason}


def fetch_host_fingerprint(host: str, port: int = 22, runner=None) -> Dict[str, Any]:
    """Runs ssh-keyscan against host:port and computes SHA256 fingerprints of the
    returned host keys via ssh-keygen. Does not write anything; pin_host_key does.
    """
    run = runner or subprocess.run
    try:
        keyscan = run(["ssh-keyscan", "-p", str(port), host], capture_output=True, timeout=15, text=True)
    except Exception as e:
        return {"success": False, "message": f"Không chạy được ssh-keyscan: {e}"}

    raw_keys = (keyscan.stdout or "")
    if keyscan.returncode != 0 or not raw_keys.strip():
        return {
            "success": False,
            "message": (
                f"ssh-keyscan không lấy được host key từ {host}:{port}. "
                f"{(keyscan.stderr or '').strip()}"
            )
        }

    try:
        keygen = run(["ssh-keygen", "-lf", "-"], input=raw_keys, capture_output=True, timeout=10, text=True)
    except Exception as e:
        return {"success": False, "message": f"Không chạy được ssh-keygen: {e}"}
    if keygen.returncode != 0:
        return {
            "success": False,
            "message": f"ssh-keygen không tính được fingerprint: {(keygen.stderr or '').strip()}"
        }

    fingerprints = [m.group(1) for m in (_SHA256_FP_RE.search(line) for line in (keygen.stdout or "").splitlines()) if m]

    return {
        "success": True,
        "raw_keys": raw_keys,
        "fingerprints": fingerprints,
        "fingerprint": fingerprints[0] if fingerprints else None
    }


def _parse_known_hosts_entries(text: str) -> Dict[str, List[str]]:
    """Parses a known_hosts-format file into {host: ["keytype base64key", ...]}."""
    entries: Dict[str, List[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        host_field, keytype, key_b64 = parts[0], parts[1], parts[2]
        for h in host_field.split(","):
            entries.setdefault(h, []).append(f"{keytype} {key_b64}")
    return entries


def pin_host_key(
    host: str,
    port: int = 22,
    known_hosts: Optional[Path] = None,
    expected_sha256: Optional[str] = None,
    runner=None
) -> Dict[str, Any]:
    """Fetches host:port's current host key and appends it to known_hosts.

    Refuses to write (and reports why) when:
      - expected_sha256 is given and does not match the freshly-fetched fingerprint;
      - known_hosts already has a DIFFERENT key on file for this host (the key
        changed since it was first pinned — treated as a possible MITM/reinstall,
        per the acceptance matrix: "SFTP sai host key -> Từ chối kết nối và giải
        thích fingerprint thay đổi").
    """
    if known_hosts is None:
        return {"success": False, "message": "Cần đường dẫn 'known_hosts' để ghim host key."}
    known_hosts = Path(known_hosts)

    fp_result = fetch_host_fingerprint(host, port=port, runner=runner)
    if not fp_result.get("success"):
        return {"success": False, "message": fp_result["message"]}

    new_fp = fp_result.get("fingerprint")
    if expected_sha256 and new_fp != expected_sha256:
        return {
            "success": False,
            "message": (
                f"Fingerprint host key không khớp: mong đợi {expected_sha256}, nhận được {new_fp}. "
                f"Từ chối ghi known-hosts."
            ),
            "fingerprint": new_fp
        }

    existing_text = ""
    if known_hosts.exists():
        try:
            existing_text = known_hosts.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            existing_text = ""

    existing_entries = _parse_known_hosts_entries(existing_text)
    new_keys_for_host = set()
    host_field = host
    for line in fp_result["raw_keys"].splitlines():
        parts = line.split()
        if len(parts) >= 3:
            # ssh-keyscan writes the host field as "[host]:port" for any non-default
            # port (OpenSSH known_hosts convention); use whatever it actually wrote
            # as the lookup key instead of assuming "host" is the literal field, or
            # a pinned key on a non-22 port would never be found on a later lookup
            # and a changed key there would silently go undetected.
            host_field = parts[0]
            for h in host_field.split(","):
                new_keys_for_host.add(f"{parts[1]} {parts[2]}")

    old_keys_for_host = set(existing_entries.get(host_field, []))
    if old_keys_for_host and old_keys_for_host != new_keys_for_host:
        return {
            "success": False,
            "message": (
                f"Host key của '{host}' đã THAY ĐỔI so với known-hosts hiện có. Fingerprint mới: "
                f"{new_fp}. Từ chối kết nối — đây có thể là tấn công man-in-the-middle hoặc server đã "
                f"được cài lại. Chỉ ghi đè thủ công nếu bạn chắc chắn đã đổi server/key hợp lệ."
            ),
            "fingerprint": new_fp
        }

    combined = existing_text
    if combined and not combined.endswith("\n"):
        combined += "\n"
    combined += fp_result["raw_keys"]
    if not combined.endswith("\n"):
        combined += "\n"

    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    tmp = known_hosts.parent / f"{known_hosts.name}.partial"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(combined)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, known_hosts)

    return {
        "success": True,
        "fingerprint": new_fp,
        "message": f"Đã ghim host key cho {host} (fingerprint {new_fp})."
    }


class SftpTransport(RemoteTransport):
    """Talks to a PC/NAS/Raspberry Pi via the system `sftp` binary in batch mode.

    Never uses shell=True and never builds a command by string-concatenating a
    filename; every invocation is an argv list, and remote paths/filenames go into
    an `sftp -b <batchfile>` batch file instead of `-b -` string concatenation.
    Every invocation pins the host key and disables password auth so a missing/
    wrong key fails fast instead of hanging on a prompt:
      -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known_hosts>
      -o BatchMode=yes -o ConnectTimeout=10
      -o PasswordAuthentication=no -o NumberOfPasswordPrompts=0
    `sftp` takes `-P <port>` (capital P); `ssh`/`ssh-keyscan` take `-p` (lowercase) —
    intentionally different flags, easy to mix up.
    """

    def __init__(
        self,
        host: str,
        user: str,
        remote_path: str,
        port: int = 22,
        key_path: Optional[str] = None,
        known_hosts: Optional[Path] = None,
        runner=None,
        timeout: int = 60
    ):
        if known_hosts is None:
            raise ValueError(
                "SftpTransport cần đường dẫn 'known_hosts' để ghim host key trước khi kết nối. "
                "Chạy 'endpoint pin --name <tên>' trước, rồi truyền known_hosts vào đây."
            )
        self.host = host
        self.user = user
        self.remote_path = str(remote_path).rstrip("/") or "."
        self.port = port
        self.key_path = str(key_path) if key_path else None
        self.known_hosts = Path(known_hosts)
        self.runner = runner
        self.timeout = timeout
        # A real capability check only matters when we are really going to shell out;
        # with an injected runner (tests), the fake runner stands in for ssh/sftp
        # entirely, so the host machine's own PATH is irrelevant.
        self._capability = probe_sftp_capability() if runner is None else {"available": True, "reason": None}

    def _remote_full(self, rel: str) -> str:
        rel = _safe_rel(rel)
        return f"{self.remote_path}/{rel}" if rel else self.remote_path

    def _base_argv(self, batchfile: str) -> List[str]:
        argv = [
            "sftp", "-b", batchfile,
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "PasswordAuthentication=no",
            "-o", "NumberOfPasswordPrompts=0",
            "-P", str(self.port),
        ]
        if self.key_path:
            argv += ["-i", self.key_path]
        argv.append(f"{self.user}@{self.host}")
        return argv

    def _run_batch(self, lines: List[str]):
        if self.runner is None and not self._capability["available"]:
            raise RuntimeError(self._capability["reason"])

        fd, path = tempfile.mkstemp(prefix="loongmate_sftp_", suffix=".batch")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            argv = self._base_argv(path)
            run = self.runner or subprocess.run
            try:
                return run(argv, capture_output=True, timeout=self.timeout, text=True)
            except subprocess.TimeoutExpired as e:
                raise IOError(f"Lệnh sftp vượt quá timeout {self.timeout}s: {argv}") from e
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def exists(self, rel: str) -> bool:
        full = self._remote_full(rel)
        res = self._run_batch([f'ls "{full}"'])
        return res.returncode == 0

    def list_dir(self, rel: str) -> List[str]:
        full = self._remote_full(rel)
        res = self._run_batch([f'ls -1 "{full}"'])
        if res.returncode != 0:
            return []
        names = []
        for line in (res.stdout or "").splitlines():
            name = line.strip()
            if not name or name in (".", ".."):
                continue
            names.append(Path(name).name)
        return names

    def makedirs(self, rel: str) -> None:
        rel = _safe_rel(rel)
        parts = rel.split("/")
        lines = []
        accum: List[str] = []
        for part in parts:
            accum.append(part)
            sub = "/".join(accum)
            # "-mkdir" (leading '-') tells sftp batch mode to ignore an error on this
            # single line (e.g. "already exists") instead of aborting the whole batch.
            lines.append(f'-mkdir "{self._remote_full(sub)}"')
        self._run_batch(lines)

    def put_atomic(self, local: Path, rel: str) -> None:
        full = self._remote_full(rel)
        partial = f"{full}.partial"
        res = self._run_batch([f'put "{local}" "{partial}"', f'rename "{partial}" "{full}"'])
        if res.returncode != 0:
            raise IOError(
                f"Đẩy file lên remote thất bại (rel={rel}, exit={res.returncode}): "
                f"{(res.stderr or res.stdout or '').strip()}"
            )

    def get(self, rel: str, local: Path) -> None:
        full = self._remote_full(rel)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.parent / f"{local.name}.partial"
        res = self._run_batch([f'get "{full}" "{tmp}"'])
        if res.returncode != 0:
            raise IOError(
                f"Tải file từ remote thất bại (rel={rel}, exit={res.returncode}): "
                f"{(res.stderr or res.stdout or '').strip()}"
            )
        os.replace(tmp, local)

    def size(self, rel: str) -> Optional[int]:
        full = self._remote_full(rel)
        res = self._run_batch([f'ls -l "{full}"'])
        if res.returncode != 0:
            return None
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            # Standard `ls -l` layout: perms links owner group size month day time name
            if len(parts) >= 5 and parts[0][0] in "-dl":
                try:
                    return int(parts[4])
                except ValueError:
                    continue
        return None

    def remove(self, rel: str) -> None:
        """Removes a remote file, or a directory that directly contains only files
        (one level deep — enough for snapshots/<id>/ which only holds manifest.json;
        the shared objects/ tree is never passed here)."""
        full = self._remote_full(rel)
        entries = self.list_dir(rel)
        lines = []
        if entries:
            for e in entries:
                lines.append(f'-rm "{full}/{e}"')
            lines.append(f'-rmdir "{full}"')
        else:
            lines.append(f'-rm "{full}"')
        self._run_batch(lines)


class RemoteVault:
    """Sync logic shared by every transport: incremental push via the object store,
    manifest-last commit, and pull/prune. Contains no transport-specific code.
    """

    def __init__(self, vault: SaveVault, transport: RemoteTransport):
        self.vault = vault
        self.transport = transport

    def test_endpoint(self) -> Dict[str, Any]:
        try:
            self.transport.makedirs("snapshots")
            self.transport.makedirs("objects")
            return {"success": True, "message": "Kết nối endpoint OK (đã tạo/xác nhận thư mục snapshots, objects)."}
        except Exception as e:
            return {"success": False, "message": f"Không kết nối được endpoint: {e}"}

    def push_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        """Pushes snapshot_id to the remote. Order (per tài liệu "Luồng backup"):
        (1) read local manifest; (2) list_dir each needed objects/<prefix> shard to
        see what the remote already has; (3) put_atomic only the missing objects;
        (4) verify size on the remote if the transport can report it, aborting
        WITHOUT writing a manifest on mismatch; (5) push manifest.json LAST as the
        commit marker. Any exception before step 5 leaves the remote with no
        manifest for this snapshot, so list_remote_snapshots() will not see it —
        "Rút mạng giữa backup không tạo snapshot 'thành công' giả".
        """
        empty_counts = {"objects_uploaded": 0, "objects_skipped": 0, "bytes_uploaded": 0}

        snap_path = self.vault._resolve_snapshot(snapshot_id)
        if snap_path is None:
            return {"success": False, "snapshot_id": snapshot_id, **empty_counts,
                     "message": f"ID snapshot không hợp lệ: '{snapshot_id}'. Vui lòng chọn lại từ danh sách snapshot."}

        mf_path = snap_path / "manifest.json"
        if not mf_path.exists():
            return {"success": False, "snapshot_id": snapshot_id, **empty_counts,
                     "message": f"Không tìm thấy snapshot {snapshot_id}"}

        manifest = json.loads(mf_path.read_text(encoding="utf-8"))
        if manifest.get("layout") != "objects":
            return {
                "success": False, "snapshot_id": snapshot_id, **empty_counts,
                "message": (
                    "Snapshot định dạng cũ (không dùng object store) chưa hỗ trợ đẩy lên remote. "
                    "Hãy tạo snapshot mới bằng lệnh 'backup' rồi push snapshot đó."
                )
            }

        by_prefix: Dict[str, Dict[str, int]] = {}
        for info in manifest.get("files", {}).values():
            sha = info["sha256"]
            by_prefix.setdefault(sha[:2], {})[sha] = info["size"]

        uploaded = 0
        skipped = 0
        bytes_uploaded = 0

        try:
            self.transport.makedirs("objects")
            for prefix, shas in by_prefix.items():
                remote_dir = f"objects/{prefix}"
                existing = set(self.transport.list_dir(remote_dir))
                missing = {sha: size for sha, size in shas.items() if sha not in existing}
                skipped += len(shas) - len(missing)
                if not missing:
                    continue
                self.transport.makedirs(remote_dir)
                for sha, size in missing.items():
                    rel = f"{remote_dir}/{sha}"
                    local_obj = self.vault._object_path(sha)
                    self.transport.put_atomic(local_obj, rel)
                    remote_size = self.transport.size(rel)
                    if remote_size is not None and remote_size != size:
                        return {
                            "success": False, "snapshot_id": snapshot_id,
                            "objects_uploaded": uploaded, "objects_skipped": skipped,
                            "bytes_uploaded": bytes_uploaded,
                            "message": (
                                f"Kích thước object sau khi tải lên remote không khớp (hash={sha}, "
                                f"kỳ vọng {size}, thực tế {remote_size}). Không commit manifest; "
                                f"snapshot KHÔNG được coi là thành công trên remote."
                            )
                        }
                    uploaded += 1
                    bytes_uploaded += size

            self.transport.makedirs(f"snapshots/{snapshot_id}")
            self.transport.put_atomic(mf_path, f"snapshots/{snapshot_id}/manifest.json")

            return {
                "success": True, "snapshot_id": snapshot_id,
                "objects_uploaded": uploaded, "objects_skipped": skipped,
                "bytes_uploaded": bytes_uploaded,
                "message": f"Đã đẩy snapshot {snapshot_id} lên remote ({uploaded} object mới, {skipped} đã có sẵn)."
            }
        except Exception as e:
            return {
                "success": False, "snapshot_id": snapshot_id,
                "objects_uploaded": uploaded, "objects_skipped": skipped,
                "bytes_uploaded": bytes_uploaded,
                "message": (
                    f"Đẩy snapshot thất bại giữa chừng: {e}. Vì manifest.json chưa được ghi lên remote, "
                    f"snapshot KHÔNG được coi là 'thành công' (list_remote_snapshots sẽ không thấy nó)."
                )
            }

    def list_remote_snapshots(self) -> List[Dict[str, Any]]:
        """Lists only remote snapshots that have a manifest.json AND committed=true."""
        try:
            ids = self.transport.list_dir("snapshots")
        except Exception:
            return []

        results = []
        for sid in ids:
            mf_rel = f"snapshots/{sid}/manifest.json"
            try:
                if not self.transport.exists(mf_rel):
                    continue
                tmp_dir = Path(tempfile.mkdtemp(prefix="loongmate_remote_mf_"))
                try:
                    local_mf = tmp_dir / "manifest.json"
                    self.transport.get(mf_rel, local_mf)
                    data = json.loads(local_mf.read_text(encoding="utf-8"))
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                if not data.get("committed"):
                    continue
                results.append({
                    "id": data.get("snapshot_id", sid),
                    "created_at": data.get("created_at"),
                    "total_files": data.get("total_files", 0),
                    "total_size_bytes": data.get("total_size_bytes", 0)
                })
            except Exception:
                continue

        results.sort(key=lambda s: s["id"], reverse=True)
        return results

    def pull_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        """Downloads snapshot_id from the remote into the local vault's snapshots_dir,
        fetching only objects not already present locally (dedup, same object store).
        """
        rel_mf = f"snapshots/{snapshot_id}/manifest.json"
        if not self.transport.exists(rel_mf):
            return {"success": False, "snapshot_id": snapshot_id,
                     "message": f"Không tìm thấy snapshot '{snapshot_id}' trên remote."}

        local_snap_dir = self.vault.snapshots_dir / snapshot_id
        temp_dir = local_snap_dir.parent / f"{snapshot_id}.partial"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            local_mf_tmp = temp_dir / "manifest.json.download"
            self.transport.get(rel_mf, local_mf_tmp)
            manifest = json.loads(local_mf_tmp.read_text(encoding="utf-8"))
            local_mf_tmp.unlink(missing_ok=True)

            if not manifest.get("committed"):
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {
                    "success": False, "snapshot_id": snapshot_id,
                    "message": "Snapshot trên remote chưa commit (thiếu 'committed': true), không tải về."
                }

            downloaded = 0
            bytes_downloaded = 0
            for info in manifest.get("files", {}).values():
                sha = info["sha256"]
                local_obj = self.vault._object_path(sha)
                if local_obj.exists():
                    continue
                rel = f"objects/{sha[:2]}/{sha}"
                obj_tmp = temp_dir / f"obj_{sha}.download"
                self.transport.get(rel, obj_tmp)
                local_obj.parent.mkdir(parents=True, exist_ok=True)
                staged = local_obj.parent / f"{local_obj.name}.partial"
                shutil.move(str(obj_tmp), str(staged))
                os.replace(staged, local_obj)
                downloaded += 1
                bytes_downloaded += info["size"]

            manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            tmp_mf = temp_dir / "manifest.json"
            with open(tmp_mf, "wb") as f:
                f.write(manifest_bytes)
                f.flush()
                os.fsync(f.fileno())

            if local_snap_dir.exists():
                shutil.rmtree(local_snap_dir)
            temp_dir.rename(local_snap_dir)

            return {
                "success": True, "snapshot_id": snapshot_id,
                "objects_downloaded": downloaded, "bytes_downloaded": bytes_downloaded,
                "message": f"Đã tải snapshot {snapshot_id} về vault cục bộ."
            }
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "snapshot_id": snapshot_id, "message": f"Tải snapshot thất bại: {e}"}

    def prune_remote(self, keep: int) -> Dict[str, Any]:
        """Keeps only the `keep` newest committed remote snapshots. Only removes
        snapshot directories (manifest.json); the shared objects/ store is untouched
        (no remote object garbage-collection in this MVP — out of the specified scope).
        """
        snaps = self.list_remote_snapshots()
        removed = []
        for old in snaps[keep:]:
            try:
                self.transport.remove(f"snapshots/{old['id']}")
                removed.append(old["id"])
            except Exception:
                continue
        return {"success": True, "removed": removed, "kept": min(len(snaps), keep)}
