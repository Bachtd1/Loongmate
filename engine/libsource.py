"""
LoongMate — Thư viện riêng (Library Sources, giai đoạn B, phía Pocket 1).

Duyệt + tải + cài game từ một agent PocketBridge do CHÍNH BẠN dựng (Pi/PC/Mac) và khai báo thư mục ROM của bạn trong
`library.roots`. LoongMate không có danh mục ROM nào, không tải gì từ internet: nó chỉ nói chuyện với node bạn đã ghép đôi.

Truyền tải (giữ cùng hành vi client C++ PocketBridge: client/src/transport/{Socks5,Route,AgentClient}.cpp)
  * Tailscale chạy userspace trên Pocket 1: app chỉ vào tailnet qua SOCKS5 127.0.0.1:1055 (CONNECT, không xác thực;
    ATYP=3 cho tên MagicDNS vì resolver hệ thống không phân giải MagicDNS). Tự dùng SOCKS5 khi host thuộc
    100.64.0.0/10, fd7a:115c:a1e0::/48, đuôi .ts.net, hoặc không phân giải được — VÀ cổng proxy đang mở.
  * TLS tự ký, GHIM fingerprint "sha256:" + hex(SHA-256(DER lá)). Không dùng CA. Fingerprint được so NGAY sau bắt tay,
    TRƯỚC khi gửi byte HTTP nào (nên không bao giờ gửi Authorization cho một máy lạ). Lệch -> chặn.
  * Token dài hạn: /userdata/LoongMate/secrets/<id>.token (0600, thư mục 0700; /userdata là ext4 — thẻ FAT32 không giữ
    quyền nên bị từ chối thay vì lưu bừa). Nodes: <app_data>/library-nodes.json.

Tải
  * Job bền: <thẻ đích>/.loongmate-import/downloads/<job>/{job.json, files/<đường dẫn>.part}. Resume bằng
    Range + If-Range (ETag "sha256:<hex>"): 206 -> nối; 200 -> tải lại TỪ ĐẦU (không bao giờ nối vào .part cũ);
    416 -> kiểm .part đã đủ chưa.
  * SHA-256 tải về PHẢI khớp hash của server (ETag/index). Server chưa băm xong -> không tải, không cài.
  * Cài bằng ĐÚNG importer (engine/importer.py): build_plan + apply_plan. Không có đường cài thứ hai.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import time
import unicodedata
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import ssl
except ImportError:                       # Python không có OpenSSL: báo rõ khi cần nối, không làm vỡ cả CLI
    ssl = None  # type: ignore[assignment]
_SSL_ERROR = ssl.SSLError if ssl is not None else OSError

CLIENT_NAME = "Pocket 1"
CLIENT_VERSION = "1.0.0"
API = "/api/v1"
SCOPE = "library:read"
DEFAULT_PROXY = ("127.0.0.1", 1055)

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ROOT_RE = re.compile(r"^[a-z0-9-]{1,32}$")
TOKEN_RE = re.compile(r"^pb_[A-Za-z0-9_-]{43}$")
FP_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]*$")
ETAG_RE = re.compile(r'^"sha256:([0-9a-f]{64})"$')

MAX_BUNDLE_BYTES = 16 * 1024
MAX_ROOTS_JSON = 64 * 1024
MAX_INDEX_JSON = 2 * 1024 * 1024
MAX_SMALL_JSON = 16 * 1024
MAX_INDEX_ENTRIES = 120_000          # agent giới hạn 100 000/root
MAX_INDEX_PAGES = 600
CONNECT_TIMEOUT = 10.0
IO_TIMEOUT = 30.0
CHUNK = 256 * 1024
PROGRESS_EVERY_S = 0.25
CACHE_DIR = "library-cache"
NODES_FILE = "library-nodes.json"
LEDGER_FILE = "library-ledger.jsonl"
DL_DIR = "downloads"                 # <thẻ>/.loongmate-import/downloads/<job>/
JOB_SCHEMA = 1
HIDE_EXTS = {".txt", ".md", ".license"}   # không bao giờ hiện, kể cả khi có system_hint
FAT_BAD = set('<>:"|?*\\')


class LibError(Exception):
    """kind: pin | unreachable | timeout | tls | auth | scope | not_found | rate_limited | bad_response |
    too_large | network | server | storage | invalid. `sent`: đã gửi xong byte của request chưa (biết có thể đã có tác dụng)."""

    def __init__(self, kind: str, message: str, status: Optional[int] = None, sent: bool = False, code: str = "",
                 observed: str = ""):
        super().__init__(message)
        self.kind, self.message, self.status, self.sent, self.code, self.observed = kind, message, status, sent, code, observed


# ======================================================================================
# tiện ích
# ======================================================================================
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_json_atomic(path: Path, data: Any, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp), flags, mode if mode is not None else 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _ledger(app_data, event: str, **kw) -> None:
    try:
        p = Path(app_data) / LEDGER_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.write((json.dumps({"at": _now_iso(), "event": event, **kw}, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError:
        pass


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if 0 < i < len(name) - 1 else ""


def safe_remote_path(p: Any) -> bool:
    """Đường dẫn server trả về là DỮ LIỆU KHÔNG TIN CẬY: nó sẽ thành tên file trên thẻ. Cùng luật như agent kiểm đầu vào."""
    if not isinstance(p, str) or not p or len(p.encode("utf-8", "surrogatepass")) > 1024:
        return False
    if p.startswith("/") or "\\" in p or p.endswith("/"):
        return False
    parts = p.split("/")
    if len(parts) > 32:
        return False
    for c in parts:
        if not c or c in (".", "..") or c.startswith("."):
            return False
    for ch in p:
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            return False
    try:
        p.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def local_name(rel: str) -> str:
    """Đường dẫn tương đối server -> đường dẫn tương đối AN TOÀN cho FAT32 (từng thành phần). Tất định."""
    out = []
    for c in rel.split("/"):
        c = "".join("_" if (ch in FAT_BAD or ord(ch) < 32) else ch for ch in c).rstrip(" .")
        if not c:
            c = "_"
        b = c.encode("utf-8")
        if len(b) > 200:                              # giữ đuôi (đuôi quyết định nhận diện)
            ext = _ext(c)
            keep = 200 - len(ext.encode("utf-8"))
            c = c[:max(1, keep // 4)] + "~" + hashlib.sha1(b).hexdigest()[:6] + ext
        out.append(c)
    return "/".join(out)


# ======================================================================================
# kiểm theo schema (schemas/api/library_*.schema.json, pair_exchange_response, pairing_bundle)
# ======================================================================================
def validate_bundle(text: str) -> Dict[str, Any]:
    """schemas/pairing_bundle.schema.json (additionalProperties=false như client C++). Ném LibError(invalid)."""
    def bad(msg):
        raise LibError("invalid", f"bundle không hợp lệ: {msg}")
    if len(text.encode("utf-8", "replace")) > MAX_BUNDLE_BYTES:
        bad(f"quá lớn (> {MAX_BUNDLE_BYTES} byte)")
    try:
        d = json.loads(text)
    except ValueError as e:
        bad(f"không phải JSON ({e})")
    if not isinstance(d, dict) or set(d) != {"schema", "node", "credential"}:
        bad('cần đúng 3 khoá "schema", "node", "credential"')
    if d["schema"] != 1 or isinstance(d["schema"], bool):
        bad("schema phải là 1")
    n, c = d["node"], d["credential"]
    if not isinstance(n, dict) or set(n) != {"id", "name", "host", "port", "fingerprint"}:
        bad('node cần đúng các khoá id, name, host, port, fingerprint')
    if not isinstance(c, dict) or set(c) != {"type", "token"}:
        bad('credential cần đúng các khoá type, token')
    if not (isinstance(n["id"], str) and ID_RE.match(n["id"])):
        bad("node.id phải khớp ^[a-z0-9][a-z0-9_-]{0,31}$")
    if not (isinstance(n["name"], str) and n["name"].strip()):
        bad("node.name rỗng")
    if not (isinstance(n["host"], str) and n["host"].strip() and len(n["host"]) <= 253 and re.match(r"^[A-Za-z0-9.:_-]+$", n["host"])):
        bad("node.host sai khuôn")
    if not (_is_int(n["port"]) and 1 <= n["port"] <= 65535):
        bad("node.port phải là số nguyên 1..65535")
    if not (isinstance(n["fingerprint"], str) and FP_RE.match(n["fingerprint"])):
        bad("node.fingerprint phải là sha256:<64 hex thường>")
    if c["type"] not in ("bootstrap", "bearer"):
        bad('credential.type phải là "bootstrap" hoặc "bearer"')
    if not (isinstance(c["token"], str) and TOKEN_RE.match(c["token"])):
        bad("credential.token sai khuôn (pb_ + 43 ký tự [A-Za-z0-9_-])")
    return d


def validate_roots(d: Any) -> List[Dict[str, Any]]:
    def bad(m):
        raise LibError("bad_response", f"/library trả dữ liệu sai schema: {m}")
    if not isinstance(d, dict) or not isinstance(d.get("roots"), list) or len(d["roots"]) > 16:
        bad("thiếu mảng roots (≤ 16)")
    out = []
    for r in d["roots"]:
        if not isinstance(r, dict):
            bad("root không phải object")
        if not (isinstance(r.get("id"), str) and ROOT_RE.match(r["id"])):
            bad("root.id")
        if not (isinstance(r.get("name"), str) and 1 <= len(r["name"]) <= 64):
            bad("root.name")
        if not (_is_int(r.get("files")) and 0 <= r["files"] <= 100000):
            bad("root.files")
        if not (_is_int(r.get("bytes")) and r["bytes"] >= 0):
            bad("root.bytes")
        if not (r.get("indexed_at") is None or isinstance(r.get("indexed_at"), str)):
            bad("root.indexed_at")
        if not isinstance(r.get("indexing"), bool):
            bad("root.indexing")
        out.append({k: r[k] for k in ("id", "name", "files", "bytes", "indexed_at", "indexing")})
    return out


def validate_index_page(d: Any) -> Tuple[List[Dict[str, Any]], str, bool, int]:
    """-> (entries hợp lệ, next_cursor, has_more, số mục bị bỏ vì đường dẫn không an toàn)."""
    def bad(m):
        raise LibError("bad_response", f"/index trả dữ liệu sai schema: {m}")
    if not isinstance(d, dict):
        bad("không phải object")
    ents, nc, more = d.get("entries"), d.get("next_cursor"), d.get("has_more")
    if not isinstance(ents, list) or len(ents) > 500:
        bad("entries phải là mảng ≤ 500")
    if not (isinstance(nc, str) and CURSOR_RE.match(nc) and len(nc) <= 2048):
        bad("next_cursor")
    if not isinstance(more, bool):
        bad("has_more")
    out, dropped = [], 0
    for e in ents:
        if not isinstance(e, dict):
            bad("entry không phải object")
        p, size, mt, sha, hint = e.get("path"), e.get("size"), e.get("mtime"), e.get("sha256"), e.get("system_hint")
        if not isinstance(p, str) or not _is_int(size) or size < 0 or not isinstance(mt, str):
            bad("entry thiếu/sai kiểu path|size|mtime")
        if not (sha is None or (isinstance(sha, str) and HEX64_RE.match(sha))):
            bad("entry.sha256")
        if not (hint is None or (isinstance(hint, str) and re.match(r"^[A-Za-z0-9_-]{1,32}$", hint))):
            bad("entry.system_hint")
        if not safe_remote_path(p):
            dropped += 1                     # không tin: bỏ riêng mục này
            continue
        out.append({"path": p, "size": size, "mtime": mt, "sha256": sha, "system_hint": hint})
    return out, nc, more, dropped


def validate_exchange(d: Any) -> Dict[str, Any]:
    if not isinstance(d, dict) or not (isinstance(d.get("token"), str) and TOKEN_RE.match(d["token"])):
        raise LibError("bad_response", "Agent trả 200 nhưng thiếu/sai \"token\".")
    sc = d.get("scopes")
    if not (isinstance(sc, list) and all(isinstance(x, str) for x in sc)):
        raise LibError("bad_response", "Agent trả 200 nhưng thiếu/sai \"scopes\".")
    return {"token": d["token"], "token_id": d.get("token_id") if isinstance(d.get("token_id"), str) else "",
            "scopes": sc, "expires_at": d.get("expires_at") if isinstance(d.get("expires_at"), str) else None}


# ======================================================================================
# định tuyến + SOCKS5 + TLS ghim
# ======================================================================================
def is_tailnet_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        if ip.version == 4:
            return ip in ipaddress.ip_network("100.64.0.0/10")
        return ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")
    except ValueError:
        pass
    return len(host) > 7 and host.lower().endswith(".ts.net")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _tcp_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_setting() -> Optional[Tuple[str, int]]:
    """LOONGMATE_LIB_PROXY: 'none' = không bao giờ dùng proxy; 'host:port' = proxy khác (thử nghiệm). Mặc định 127.0.0.1:1055."""
    v = os.environ.get("LOONGMATE_LIB_PROXY", "").strip()
    if v.lower() == "none":
        return None
    if v:
        h, _, p = v.rpartition(":")
        if h and p.isdigit() and 1 <= int(p) <= 65535:
            return (h, int(p))
    return DEFAULT_PROXY


def choose_route(host: str, proxy: Optional[Tuple[str, int]] = "auto",   # type: ignore[assignment]
                 resolves: Callable[[str], bool] = _resolves,
                 port_open: Callable[[str, int], bool] = _tcp_open) -> Optional[Tuple[str, int]]:
    """None = đi thẳng; (host, port) = qua SOCKS5. Giống Route.cpp: ứng viên proxy khi host thuộc tailnet hoặc không
    phân giải được, và chỉ khi cổng proxy đang mở (máy có tun thật vẫn chạy)."""
    if proxy == "auto":
        proxy = proxy_setting()
    if proxy is None:
        return None
    candidate = is_tailnet_host(host) or (not _is_ip(host) and not resolves(host))
    if not candidate:
        return None
    return proxy if port_open(proxy[0], proxy[1]) else None


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise LibError("unreachable", "Proxy Tailscale đóng kết nối giữa chừng")
        buf += chunk
    return buf


_SOCKS_REP = {1: "lỗi chung", 2: "bị từ chối theo quy tắc", 3: "mạng đích không tới được",
              4: "máy tắt, ngoài tailnet hoặc không phân giải được tên", 5: "máy đích từ chối kết nối: agent chưa chạy hoặc sai cổng",
              6: "hết hạn chờ khi tới máy đích", 7: "proxy không hỗ trợ CONNECT", 8: "proxy không hỗ trợ kiểu địa chỉ đã gửi"}


def socks5_connect(proxy: Tuple[str, int], host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> socket.socket:
    """SOCKS5 CONNECT không xác thực. ATYP 1/4 cho IP, 3 cho tên (MagicDNS do Tailscale phân giải)."""
    label = f"{proxy[0]}:{proxy[1]}"
    if not host or len(host.encode()) > 255 or not 1 <= port <= 65535:
        raise LibError("invalid", "Địa chỉ đích không hợp lệ cho proxy SOCKS5")
    try:
        s = socket.create_connection(proxy, timeout=timeout)
    except socket.timeout:
        raise LibError("timeout", f"Hết thời gian chờ nối tới proxy Tailscale {label}")
    except OSError as e:
        raise LibError("unreachable", f"Proxy Tailscale không chạy: không nối được {label} ({e}). Kiểm tra Tailscale đã bật.")
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        sel = _recv_exact(s, 2)
        if sel[0] != 5:
            raise LibError("unreachable", f"Cổng {label} không phải proxy SOCKS5")
        if sel[1] != 0:
            raise LibError("unreachable", f"Proxy {label} đòi xác thực; chỉ hỗ trợ không xác thực")
        try:
            ip = ipaddress.ip_address(host)
            addr = (b"\x01" + ip.packed) if ip.version == 4 else (b"\x04" + ip.packed)
        except ValueError:
            hb = host.encode("idna") if not host.isascii() else host.encode()
            addr = b"\x03" + bytes([len(hb)]) + hb
        s.sendall(b"\x05\x01\x00" + addr + port.to_bytes(2, "big"))
        h = _recv_exact(s, 4)
        if h[0] != 5:
            raise LibError("unreachable", f"Proxy {label} trả lời sai khuôn SOCKS5")
        if h[1] != 0:
            raise LibError("unreachable", f"Tailscale không tới được máy đích: {_SOCKS_REP.get(h[1], 'mã lạ %d' % h[1])} (SOCKS REP {h[1]})")
        if h[3] == 1:
            _recv_exact(s, 4 + 2)
        elif h[3] == 4:
            _recv_exact(s, 16 + 2)
        elif h[3] == 3:
            _recv_exact(s, _recv_exact(s, 1)[0] + 2)
        else:
            raise LibError("unreachable", f"Proxy {label} trả kiểu địa chỉ lạ {h[3]}")
        return s
    except socket.timeout:
        s.close()
        raise LibError("timeout", f"Hết thời gian chờ proxy Tailscale {label}")
    except OSError as e:
        s.close()
        raise LibError("unreachable", f"Lỗi mạng khi nói chuyện với proxy Tailscale {label}: {e}")
    except BaseException:
        s.close()
        raise


def fingerprint_of_der(der: bytes) -> str:
    return "sha256:" + hashlib.sha256(der).hexdigest()


class _PinnedConn(http.client.HTTPConnection):
    """HTTP trên TLS đã ghim fingerprint. connect() so fingerprint NGAY sau bắt tay: tới lúc http.client gửi byte đầu tiên
    (kể cả Authorization) thì máy đích đã được xác minh. Không xác minh CA/hostname — tin cậy hoàn toàn nằm ở ghim."""

    def __init__(self, host: str, port: int, fingerprint: str, proxy: Optional[Tuple[str, int]]):
        super().__init__(host, port, timeout=IO_TIMEOUT)
        self.fingerprint = fingerprint
        self.proxy = proxy
        self.observed = ""

    def connect(self):
        try:
            if self.proxy:
                raw = socks5_connect(self.proxy, self.host, self.port)
            else:
                raw = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except socket.timeout:
            raise LibError("timeout", f"Hết thời gian chờ nối tới {self.host}:{self.port}")
        except OSError as e:
            raise LibError("unreachable", f"Không nối được {self.host}:{self.port} ({e})")
        if ssl is None:
            raw.close()
            raise LibError("tls", "Python trên máy không có module ssl (OpenSSL): không thể nối TLS tới thư viện riêng")
        try:
            raw.settimeout(CONNECT_TIMEOUT)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            tls = ctx.wrap_socket(raw, server_hostname=None if _is_ip(self.host) else self.host)
        except socket.timeout:
            raw.close()
            raise LibError("timeout", "Hết thời gian chờ bắt tay TLS")
        except (_SSL_ERROR, OSError) as e:
            raw.close()
            raise LibError("tls", f"Bắt tay TLS thất bại ({e})")
        der = tls.getpeercert(binary_form=True) or b""
        got = fingerprint_of_der(der)
        self.observed = got
        if not hmac.compare_digest(got, self.fingerprint):
            tls.close()
            raise LibError("pin", "Fingerprint của máy đích KHÁC bản đã ghim: dừng, KHÔNG gửi token. "
                                  "Kiểm tra lại máy đích hoặc ghép đôi lại.", observed=got)
        tls.settimeout(IO_TIMEOUT)
        self.sock = tls


def parse_content_range(v: Optional[str]) -> Optional[Tuple[Optional[int], Optional[int], int]]:
    """'bytes a-b/total' -> (a, b, total); 'bytes */total' -> (None, None, total); sai khuôn -> None."""
    if not v:
        return None
    m = re.match(r"^bytes (?:(\d+)-(\d+)|\*)/(\d+)$", v.strip())
    if not m:
        return None
    a = int(m.group(1)) if m.group(1) is not None else None
    b = int(m.group(2)) if m.group(2) is not None else None
    return a, b, int(m.group(3))


def etag_sha(v: Optional[str]) -> Optional[str]:
    m = ETAG_RE.match(v.strip()) if v else None
    return m.group(1) if m else None


class Client:
    """Một phiên tới một node (kết nối giữ sống, dùng lại giữa các request)."""

    def __init__(self, node: Dict[str, Any], token: Optional[str], proxy="auto"):
        self.node, self.token, self._proxy_setting = node, token, proxy
        self.hosts = [node["host"]] + [h for h in node.get("fallback_hosts", []) if h != node["host"]]
        self._conn: Optional[_PinnedConn] = None
        self._used = False

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _connect(self) -> _PinnedConn:
        if self._conn is not None:
            return self._conn
        last: Optional[LibError] = None
        for h in self.hosts:
            c = _PinnedConn(h, int(self.node["port"]), self.node["fingerprint"], choose_route(h, self._proxy_setting))
            try:
                c.connect()
            except LibError as e:
                c.close()
                if e.kind == "pin":
                    raise                         # lệch ghim = tín hiệu an ninh: không thử host khác
                last = e
                continue
            self._conn, self._used = c, False
            return c
        assert last is not None
        if len(self.hosts) > 1:
            last.message += f" (đã thử {len(self.hosts)} host)"
            last.args = (last.message,)
        raise last

    def request(self, method: str, target: str, headers: Optional[Dict[str, str]] = None, body: Optional[bytes] = None,
                auth: Optional[str] = "default") -> http.client.HTTPResponse:
        """Gửi request, trả response CHƯA đọc body. Lỗi tầng mạng -> LibError. Thử lại một lần nếu kết nối giữ sống đã chết."""
        tok = self.token if auth == "default" else auth
        hdrs = {"Accept": "application/json, application/octet-stream;q=0.9", "User-Agent": f"LoongMate/{CLIENT_VERSION}"}
        if tok:
            hdrs["Authorization"] = "Bearer " + tok
        hdrs.update(headers or {})
        for attempt in (0, 1):
            conn = self._connect()
            reused = self._used
            sent = False
            try:
                conn.request(method, target, body=body, headers=hdrs)
                sent = True
                resp = conn.getresponse()
                self._used = True
                return resp
            except LibError:
                raise
            except (http.client.HTTPException, ConnectionError, socket.timeout, _SSL_ERROR, OSError) as e:
                self.close()
                if reused and attempt == 0 and isinstance(e, (http.client.RemoteDisconnected, ConnectionError,
                                                            http.client.BadStatusLine, http.client.CannotSendRequest,
                                                            http.client.ResponseNotReady, _SSL_ERROR, BrokenPipeError)):
                    continue                      # kết nối giữ sống bị server đóng: mở lại một lần
                if isinstance(e, socket.timeout):
                    raise LibError("timeout", "Hết thời gian chờ máy đích trả lời", sent=sent)
                raise LibError("network", f"Mất kết nối tới máy đích ({e})", sent=sent)
        raise LibError("network", "Mất kết nối tới máy đích")

    @staticmethod
    def _read_limited(resp: http.client.HTTPResponse, limit: int) -> bytes:
        cl = resp.getheader("Content-Length")
        if cl and cl.isdigit() and int(cl) > limit:
            raise LibError("too_large", f"Response vượt giới hạn {limit} byte")
        try:
            data = resp.read(limit + 1)
        except (http.client.HTTPException, OSError) as e:
            raise LibError("network", f"Mất kết nối khi đọc response ({e})", sent=True)
        if len(data) > limit:
            raise LibError("too_large", f"Response vượt giới hạn {limit} byte")
        return data

    def error_from(self, resp: http.client.HTTPResponse) -> LibError:
        """Dựng LibError từ response lỗi (đọc tối đa 16 KiB body JSON)."""
        code = msg = ""
        try:
            raw = self._read_limited(resp, MAX_SMALL_JSON)
            e = json.loads(raw.decode("utf-8", "replace")).get("error", {})
            code, msg = str(e.get("code", ""))[:64], str(e.get("message", ""))[:300]
        except (LibError, ValueError, AttributeError):
            self.close()
        st = resp.status
        if st == 401:
            return LibError("auth", "Token sai, hết hạn hoặc đã bị thu hồi (401). Hãy ghép đôi lại bằng bundle mới.", st, True, code)
        if st == 403:
            return LibError("scope", "Máy đích từ chối (403): token thiếu quyền library:read hoặc file vượt giới hạn của thư viện." + (f" ({msg})" if msg else ""), st, True, code)
        if st == 404:
            return LibError("not_found", "Không tìm thấy (404): thư viện/file không còn trên máy đích." , st, True, code)
        if st == 429:
            return LibError("rate_limited", "Máy đích báo quá tải/quá nhiều lượt (429). Thử lại sau ít giây.", st, True, code)
        if st >= 500:
            return LibError("server", f"Máy đích báo lỗi (HTTP {st}{' ' + code if code else ''}){': ' + msg if msg else ''}", st, True, code)
        return LibError("bad_response", f"Máy đích trả HTTP {st}{' ' + code if code else ''}{': ' + msg if msg else ''}", st, True, code)

    def get_json(self, target: str, limit: int) -> Any:
        resp = self.request("GET", target)
        if resp.status != 200:
            raise self.error_from(resp)
        raw = self._read_limited(resp, limit)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise LibError("bad_response", "Máy đích trả JSON hỏng", resp.status, True)

    # ---- API
    def roots(self) -> List[Dict[str, Any]]:
        return validate_roots(self.get_json(f"{API}/library", MAX_ROOTS_JSON))

    def index_pages(self, root: str):
        """Sinh (entries, dropped) từng trang. Chống vòng lặp con trỏ."""
        if not ROOT_RE.match(root):
            raise LibError("invalid", "id thư viện sai khuôn")
        cursor, seen = "", set()
        for _ in range(MAX_INDEX_PAGES):
            q = "limit=500" + (f"&cursor={urllib.parse.quote(cursor, safe='')}" if cursor else "")
            ents, nxt, more, dropped = validate_index_page(self.get_json(f"{API}/library/{root}/index?{q}", MAX_INDEX_JSON))
            yield ents, dropped
            if not more:
                return
            if not nxt or nxt == cursor or nxt in seen:
                raise LibError("bad_response", "Con trỏ phân trang không tiến — dừng để khỏi lặp vô hạn")
            seen.add(nxt)
            cursor = nxt
        raise LibError("bad_response", "Index vượt số trang tối đa")

    def file_target(self, root: str, path: str) -> str:
        if not ROOT_RE.match(root) or not safe_remote_path(path):
            raise LibError("invalid", "id thư viện/đường dẫn file không hợp lệ")
        return f"{API}/library/{root}/file?path={urllib.parse.quote(path, safe='/')}"

    def probe_file(self, root: str, path: str) -> Dict[str, Any]:
        """Một request Range 0-0: cho (size, sha256|None) hiện tại của server mà KHÔNG tải file."""
        resp = self.request("GET", self.file_target(root, path), {"Range": "bytes=0-0"})
        if resp.status == 206:
            cr = parse_content_range(resp.getheader("Content-Range"))
            if not cr or cr[0] != 0:
                self.close()
                raise LibError("bad_response", "Content-Range của 206 sai khuôn", 206, True)
            size = cr[2]
            self._read_limited(resp, 16)
        elif resp.status == 416:
            cr = parse_content_range(resp.getheader("Content-Range"))
            self.close()
            if not cr or cr[2] != 0:
                raise LibError("bad_response", "416 không kèm Content-Range hợp lệ", 416, True)
            size = 0
        elif resp.status == 200:           # server bỏ qua Range: lấy kích thước từ header, KHÔNG đọc body
            cl = resp.getheader("Content-Length")
            self.close()
            if not (cl and cl.isdigit()):
                raise LibError("bad_response", "200 không có Content-Length", 200, True)
            size = int(cl)
        else:
            raise self.error_from(resp)
        return {"size": size, "sha256": etag_sha(resp.getheader("ETag"))}

    def exchange(self, bootstrap: str, request_id: str) -> Dict[str, Any]:
        body = json.dumps({"request_id": request_id, "client": {"name": CLIENT_NAME, "version": CLIENT_VERSION}}).encode()
        resp = self.request("POST", f"{API}/pair/exchange", {"Content-Type": "application/json"}, body, auth=bootstrap)
        if resp.status == 200:
            raw = self._read_limited(resp, MAX_SMALL_JSON)
            try:
                return validate_exchange(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                raise LibError("bad_response", "Agent trả 200 nhưng không phải JSON", 200, True)
        e = self.error_from(resp)
        if resp.status == 409 and e.code == "already_exchanged":
            e.kind = "already_exchanged"
            e.message = "Bundle này ĐÃ được đổi trước đó (409 already_exchanged), không thử lại."
            e.args = (e.message,)
        raise e


# ======================================================================================
# môi trường + lưu trữ node/token
# ======================================================================================
class Env:
    """app_data: <USERDATA>/LoongMate; cards: {'primary': Path|None, 'secondary': Path|None}; secrets_dir: ext4 (0700)."""

    def __init__(self, app_data, cards: Dict[str, Any], secrets_dir=None, proxy="auto"):
        self.app_data = Path(app_data)
        self.cards = {k: (Path(v) if v else None) for k, v in cards.items()}
        self.secrets_dir = Path(secrets_dir) if secrets_dir else default_secrets_dir()
        self.proxy = proxy

    def client(self, node_id: str) -> Client:
        node = get_node(self, node_id)
        return Client(node, read_token(self, node_id), self.proxy)


def default_secrets_dir() -> Path:
    v = os.environ.get("LOONGMATE_SECRETS_DIR", "").strip()
    return Path(v) if v else Path("/userdata/LoongMate/secrets")


def _posix() -> bool:
    return os.name == "posix"


def write_token(env: Env, node_id: str, token: str) -> None:
    """Ghi token 0600 trong thư mục 0700. KIỂM LẠI quyền thật sau khi ghi: filesystem không giữ quyền (FAT32) -> xoá + từ chối."""
    if not ID_RE.match(node_id) or not TOKEN_RE.match(token):
        raise LibError("invalid", "id node/token sai khuôn")
    d = env.secrets_dir
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _posix():
            os.chmod(d, 0o700)
        p = d / f"{node_id}.token"
        tmp = d / f".{node_id}.token.partial"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(token.encode("ascii"))
            f.flush()
            os.fsync(f.fileno())
        if _posix():
            os.chmod(tmp, 0o600)
            mode = stat.S_IMODE(os.stat(tmp).st_mode)
            dmode = stat.S_IMODE(os.stat(d).st_mode)
            if mode != 0o600 or dmode != 0o700:
                tmp.unlink()
                raise LibError("storage", f"Nơi lưu {d} không giữ được quyền 0600/0700 (hiện {oct(mode)}/{oct(dmode)}; thẻ FAT32?). "
                                          "Không lưu token ở đó. Cần thư mục trên /userdata (ext4).")
        os.replace(tmp, p)
        _fsync_dir(d)
    except OSError as e:
        raise LibError("storage", f"Không lưu được token tại {d}: {e}")


def read_token(env: Env, node_id: str) -> str:
    if not ID_RE.match(node_id):
        raise LibError("invalid", "id node sai khuôn")
    p = env.secrets_dir / f"{node_id}.token"
    try:
        if _posix() and stat.S_IMODE(os.stat(p).st_mode) & 0o077:
            raise LibError("storage", f"{p} có quyền quá rộng (cần 0600). Ghép đôi lại.")
        t = p.read_text(encoding="ascii").strip()
    except OSError:
        raise LibError("auth", f"Chưa có token cho node '{node_id}' (chưa ghép đôi, hoặc /userdata chưa gắn).")
    if not TOKEN_RE.match(t):
        raise LibError("auth", f"Token của node '{node_id}' hỏng. Ghép đôi lại.")
    return t


def remove_token(env: Env, node_id: str) -> None:
    try:
        (env.secrets_dir / f"{node_id}.token").unlink()
    except OSError:
        pass


def _nodes_path(env: Env) -> Path:
    return env.app_data / NODES_FILE


def load_nodes(env: Env) -> List[Dict[str, Any]]:
    try:
        d = json.loads(_nodes_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for n in d.get("nodes", []) if isinstance(d, dict) else []:
        if (isinstance(n, dict) and isinstance(n.get("id"), str) and ID_RE.match(n["id"]) and isinstance(n.get("host"), str)
                and _is_int(n.get("port")) and isinstance(n.get("fingerprint"), str) and FP_RE.match(n["fingerprint"])):
            n.setdefault("name", n["id"])
            n["fallback_hosts"] = [h for h in n.get("fallback_hosts", []) if isinstance(h, str) and h]
            out.append(n)
    return out


def save_nodes(env: Env, nodes: List[Dict[str, Any]]) -> None:
    _write_json_atomic(_nodes_path(env), {"schema": 1, "nodes": nodes})


def get_node(env: Env, node_id: str) -> Dict[str, Any]:
    for n in load_nodes(env):
        if n["id"] == node_id:
            return n
    raise LibError("not_found", f"Chưa có thư viện riêng '{node_id}'. Ghép đôi bằng: library pair <bundle.json>")


def list_nodes(env: Env) -> Dict[str, Any]:
    nodes = []
    for n in load_nodes(env):
        tok = (env.secrets_dir / f"{n['id']}.token").is_file()
        nodes.append({"id": n["id"], "name": n["name"], "host": n["host"], "port": n["port"], "fingerprint": n["fingerprint"],
                      "fallback_hosts": n["fallback_hosts"], "paired_at": n.get("paired_at"), "scopes": n.get("scopes", []),
                      "has_token": tok})
    return {"success": True, "nodes": nodes}


def unpair(env: Env, node_id: str) -> Dict[str, Any]:
    nodes = load_nodes(env)
    if not any(n["id"] == node_id for n in nodes):
        raise LibError("not_found", f"Chưa có thư viện riêng '{node_id}'.")
    save_nodes(env, [n for n in nodes if n["id"] != node_id])
    remove_token(env, node_id)
    for p in (env.app_data / CACHE_DIR).glob(f"{node_id}__*.json") if (env.app_data / CACHE_DIR).is_dir() else []:
        try:
            p.unlink()
        except OSError:
            pass
    _ledger(env.app_data, "unpair", node=node_id)
    return {"success": True, "message": f"Đã gỡ '{node_id}' (token và cache đã xoá). Token trên agent vẫn còn: thu hồi bằng `token revoke` trên máy agent."}


# ======================================================================================
# ghép đôi
# ======================================================================================
def find_bundles(env: Env) -> List[Dict[str, Any]]:
    """File *.json (không ẩn, không symlink) trong <thẻ>/PocketBridge-import/ và <thẻ>/Import/ của cả hai thẻ."""
    out = []
    for cid in ("primary", "secondary"):
        c = env.cards.get(cid)
        if not c:
            continue
        for sub in ("PocketBridge-import", "Import"):
            d = c / sub
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                p = d / n
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if n.startswith(".") or not n.lower().endswith(".json") or not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BUNDLE_BYTES:
                    continue
                info: Dict[str, Any] = {"path": str(p), "card": cid, "dir": sub, "file": n}
                try:
                    b = validate_bundle(p.read_text(encoding="utf-8"))
                    info.update(valid=True, node_id=b["node"]["id"], node_name=b["node"]["name"], host=b["node"]["host"],
                                type=b["credential"]["type"])
                except (LibError, OSError, UnicodeDecodeError) as e:
                    info.update(valid=False, reason=getattr(e, "message", str(e)))
                out.append(info)
    return out


def pair(env: Env, bundle_path) -> Dict[str, Any]:
    """Nhập bundle: kiểm schema -> ghim fingerprint -> (bootstrap) đổi lấy token dài hạn -> kiểm library:read -> lưu token (0600)
    + node -> XOÁ bundle. Bundle chỉ bị xoá SAU KHI token + node đã nằm an toàn trên đĩa; mọi lỗi khác giữ nguyên file."""
    bp = Path(bundle_path)
    res: Dict[str, Any] = {"success": False, "paired": False, "bundle_removed": False, "node_id": None, "message": ""}
    try:
        st = os.lstat(bp)
        if not stat.S_ISREG(st.st_mode):
            raise LibError("invalid", "bundle không phải file thường (symlink?) — file KHÔNG bị xoá")
        b = validate_bundle(bp.read_text(encoding="utf-8"))
    except OSError as e:
        res["message"] = f"Không đọc được bundle: {e}"
        return res
    except UnicodeDecodeError:
        res["message"] = "bundle không phải UTF-8 — file KHÔNG bị xoá"
        return res
    except LibError as e:
        res["message"] = e.message + " — file KHÔNG bị xoá"
        return res
    n, cred = b["node"], b["credential"]
    node = {"id": n["id"], "name": n["name"], "host": n["host"], "port": n["port"], "fingerprint": n["fingerprint"],
            "fallback_hosts": []}
    res["node_id"] = node["id"]
    old = next((x for x in load_nodes(env) if x["id"] == node["id"]), None)
    if old:                                   # giữ phần người dùng đã chỉnh (host dự phòng)
        node["fallback_hosts"] = old.get("fallback_hosts", [])
    c = Client(node, None, env.proxy)
    scopes: List[str] = []
    try:
        if cred["type"] == "bootstrap":
            try:
                ex = c.exchange(cred["token"], str(uuid.uuid4()))
            except LibError as e:
                if e.kind == "pin":
                    res["message"] = e.message + " — file giữ nguyên, token chưa được lưu"
                elif e.kind in ("unreachable", "timeout", "tls") and not e.sent:
                    res["message"] = f"Không tới được máy đích ({e.message}). Chưa gửi gì: có thể thử nhập lại sau. File giữ nguyên."
                elif e.kind == "already_exchanged":
                    res["message"] = (e.message + " Hãy tạo pairing MỚI trên máy agent bằng `pocketbridge-agent pair` rồi chép bundle mới. "
                                      "File giữ nguyên, token chưa được lưu.")
                    res["code"] = "already_exchanged"
                elif e.kind == "auth":
                    res["message"] = ("Mã bootstrap sai hoặc đã hết hạn (401). Nếu bundle vừa được tạo, agent đang chạy cần tới ~5 giây để nạp mã: "
                                      "đợi rồi thử lại. Nếu vẫn lỗi, tạo pairing MỚI bằng `pocketbridge-agent pair`. File giữ nguyên.")
                elif e.sent and e.kind in ("network", "timeout"):
                    res["message"] = (f"Mất kết nối SAU KHI đã gửi yêu cầu đổi token ({e.message}). Không rõ agent đã cấp token chưa và agent "
                                      "không trả token lần hai. Hãy tạo pairing MỚI trên máy agent. File giữ nguyên.")
                else:
                    res["message"] = f"Agent từ chối đổi token: {e.message}. File giữ nguyên, token chưa được lưu."
                res["error_kind"] = e.kind
                return res
            token, scopes = ex["token"], ex["scopes"]
            if SCOPE not in scopes:
                res["message"] = (f"Token dài hạn KHÔNG có quyền {SCOPE} (có: {', '.join(scopes) or 'không'}). Không lưu. "
                                  f"Token đã cấp (id '{ex['token_id']}') nằm vô chủ trên agent: thu hồi bằng `token revoke --id {ex['token_id']}`, "
                                  "rồi tạo lại bằng `pair --scopes library:read`.")
                res["error_kind"] = "scope"
                return res
        else:                                  # bearer: dùng thẳng; kiểm quyền bằng một lần gọi /library
            token = cred["token"]
            c.token = token
            try:
                c.roots()
            except LibError as e:
                res["message"] = ("Token trong bundle không dùng được: " if e.kind in ("auth", "scope") else "Không kiểm được token: ") + e.message + " File giữ nguyên."
                res["error_kind"] = e.kind
                return res
            scopes = [SCOPE]
        write_token(env, node["id"], token)
    except LibError as e:
        res["message"] = e.message + " File giữ nguyên."
        res["error_kind"] = e.kind
        return res
    finally:
        c.close()
    node.update(paired_at=_now_iso(), scopes=scopes)
    nodes = [x for x in load_nodes(env) if x["id"] != node["id"]] + [node]
    try:
        save_nodes(env, nodes)
    except OSError as e:
        remove_token(env, node["id"])
        res["message"] = f"Không lưu được danh sách node: {e}. File giữ nguyên."
        return res
    res.update(success=True, paired=True, fingerprint_changed=bool(old and old["fingerprint"] != node["fingerprint"]))
    try:                                       # chỉ xoá SAU KHI node + token đã nằm an toàn trên đĩa
        bp.unlink()
    except OSError as e:
        res["message"] = f"Đã ghép đôi '{node['name']}' nhưng KHÔNG xoá được file bundle ({e}): hãy xoá tay vì file còn chứa mã."
    else:
        res["bundle_removed"] = not bp.exists()
        res["message"] = (f"Đã ghép đôi '{node['name']}' ({node['host']}:{node['port']})"
                          + (" — fingerprint đã GHI ĐÈ bản cũ" if res.get("fingerprint_changed") else "")
                          + (" — đã đổi mã bootstrap lấy token dài hạn (mã bootstrap không được lưu)" if cred["type"] == "bootstrap" else "")
                          + (" và đã xoá file bundle." if res["bundle_removed"] else "; file bundle VẪN CÒN: hãy xoá tay vì file còn chứa mã."))
    _ledger(env.app_data, "pair", node=node["id"], type=cred["type"], bundle_removed=res["bundle_removed"])
    return res


# ======================================================================================
# duyệt
# ======================================================================================
def _catalog():
    from engine import importer
    return importer, importer.load_catalog()


def _archive():
    from engine import archive
    return archive


def classify(entry: Dict[str, Any], importer, catalog) -> Optional[Dict[str, Any]]:
    """None = không hiện. Hiện khi đuôi thuộc systems.json hoặc có system_hint; luôn ẩn .txt/.md/.license (và file meta của importer)."""
    name = entry["path"].rsplit("/", 1)[-1]
    ext = _ext(name)
    if ext in HIDE_EXTS or ext in importer.META_EXTS:
        return None
    hint = entry.get("system_hint")
    known = catalog["ext"]
    all_exts = set(known) | importer.ARCHIVE_EXTS
    if not hint and ext not in all_exts:
        return None
    if hint:
        system = hint
    else:
        cands = known.get(ext, [])
        system = (catalog["by_id"][cands[0]].get("folder") or cands[0]) if len(cands) == 1 else "?"
    problem = None
    if ext in importer.EXEC_EXTS:
        problem = "file thực thi/script — importer chặn"
    elif ext in importer.UNSUPPORTED_ARCHIVE_EXTS and _archive().download_problem(name):
        problem = _archive().download_problem(name)
    elif entry["size"] == 0:
        problem = "file rỗng"
    elif entry["size"] > importer.DEFAULT_LIMITS.max_file_bytes:
        problem = "file lớn hơn 2 GiB (giới hạn FAT32/importer)"
    return {"path": entry["path"], "name": name, "size": entry["size"], "mtime": entry["mtime"], "sha256": entry["sha256"],
            "system": system, "installable": problem is None, "problem": problem}


def hint_from_path(path: str, catalog) -> Optional[str]:
    """Cùng luật server: thư mục CẤP 1 khớp (không phân biệt hoa/thường) một `folder` của systems.json -> tên chuẩn; ngược lại None."""
    if "/" not in path:
        return None
    top = path.split("/", 1)[0].lower()
    for sid, sysd in catalog["by_id"].items():
        folder = sysd.get("folder") or sid
        if folder.lower() == top:
            return folder
    return None


def _cache_path(env: Env, node_id: str, root: str) -> Path:
    return env.app_data / CACHE_DIR / f"{node_id}__{root}.json"


def _load_cache(env: Env, node_id: str, root: str) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_cache_path(env, node_id, root).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("schema") == 1 and isinstance(d.get("entries"), list) else None
    except (OSError, ValueError):
        return None


def list_roots(env: Env, node_id: str) -> Dict[str, Any]:
    c = env.client(node_id)
    try:
        return {"success": True, "node": node_id, "roots": c.roots()}
    finally:
        c.close()


def browse(env: Env, node_id: str, root: str, system: Optional[str] = None, query: Optional[str] = None,
           refresh: bool = True, only: Optional[str] = None) -> Dict[str, Any]:
    """Danh sách game của một thư viện. Tải index (phân trang) rồi cache trong app_data; mất mạng -> dùng cache và ĐÁNH DẤU CŨ.
    Chỉ ghi cache vào app_data (không đụng thẻ/secret). refresh=False: chỉ đọc cache."""
    get_node(env, node_id)
    if not ROOT_RE.match(root):
        raise LibError("invalid", "id thư viện sai khuôn")
    importer, catalog = _catalog()
    cache = _load_cache(env, node_id, root)
    stale, error, from_cache = False, None, False
    raw: List[Dict[str, Any]]
    fetched_at: Optional[str]
    if refresh:
        try:
            c = env.client(node_id)
            raw, dropped, total_seen = [], 0, 0
            try:
                for ents, dr in c.index_pages(root):
                    dropped += dr
                    total_seen += len(ents) + dr
                    if total_seen > MAX_INDEX_ENTRIES:
                        raise LibError("bad_response", "Index vượt giới hạn số mục")
                    raw += [e for e in ents if classify(e, importer, catalog)]      # chỉ giữ mục hiện được: cache nhỏ
            finally:
                c.close()
            fetched_at = _now_iso()
            try:
                _write_json_atomic(_cache_path(env, node_id, root), {"schema": 1, "node": node_id, "root": root, "fetched_at": fetched_at,
                                                                   "fetched_epoch": time.time(), "total_seen": total_seen, "dropped": dropped,
                                                                   "entries": raw})
            except OSError:
                pass                                      # không ghi được cache không làm hỏng việc duyệt
        except LibError as e:
            if cache is None or e.kind in ("pin", "auth", "scope", "invalid"):
                raise                                     # lỗi an ninh/cấu hình: không che bằng cache
            stale, error, from_cache = True, e.message, True
            raw, fetched_at = cache["entries"], cache.get("fetched_at")
    else:
        if cache is None:
            raise LibError("not_found", "Chưa có cache cho thư viện này (chưa duyệt lần nào).")
        from_cache = True                      # đọc chủ động từ cache (không thử mạng): không phải "cũ do lỗi"
        raw, fetched_at = cache["entries"], cache.get("fetched_at")
    rows = [r for r in (classify(e, importer, catalog) for e in raw) if r]
    systems: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s = systems.setdefault(r["system"], {"system": r["system"], "count": 0, "bytes": 0})
        s["count"] += 1
        s["bytes"] += r["size"]
    syslist = sorted(systems.values(), key=lambda x: (x["system"] == "?", x["system"].lower()))
    shown = rows
    if system:
        shown = [r for r in shown if r["system"].lower() == system.lower()]
    if query:
        q = query.lower()
        shown = [r for r in shown if q in r["path"].lower()]
    shown.sort(key=lambda r: r["path"].lower())
    out: Dict[str, Any] = {"success": True, "node": node_id, "root": root, "stale": stale, "from_cache": from_cache,
                           "fetched_at": fetched_at, "error": error, "total": len(rows), "systems": syslist}
    if system or query or only == "all":
        have = _present_names(env, importer, catalog, {r["system"] for r in shown})
        for r in shown:
            r["present"] = r["name"].lower() in have.get(r["system"], ())
        out["entries"] = shown
        out["shown"] = len(shown)
    return out


def _present_names(env: Env, importer, catalog, systems) -> Dict[str, set]:
    """Tên file (chữ thường) đã có trong <thẻ>/Roms/<hệ>/ (đệ quy, cả hai thẻ), để đánh dấu "đã có" và khỏi tải lại. Chỉ đọc."""
    out: Dict[str, set] = {}
    for sysname in systems:
        if sysname == "?":
            continue
        names = set()
        for c in env.cards.values():
            if not c:
                continue
            d = Path(c) / "Roms" / sysname
            if d.is_dir():
                for _dp, _dn, fn in os.walk(d):
                    names.update(n.lower() for n in fn)
        out[sysname] = names
    return out


# ======================================================================================
# tải: job bền
# ======================================================================================
def _importer():
    from engine import importer
    return importer


def _dl_root(card_root: Path) -> Path:
    return Path(card_root) / ".loongmate-import" / DL_DIR


def _load_job(p: Path) -> Optional[Dict[str, Any]]:
    try:
        j = json.loads((p / "job.json").read_text(encoding="utf-8"))
        return j if isinstance(j, dict) and j.get("schema") == JOB_SCHEMA and isinstance(j.get("files"), list) else None
    except (OSError, ValueError):
        return None


def _save_job(p: Path, job: Dict[str, Any]) -> None:
    _write_json_atomic(p / "job.json", job)


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def _file_state(job_dir: Path, f: Dict[str, Any]) -> Tuple[str, int]:
    final = job_dir / "files" / f["local"]
    part = final.with_name(final.name + ".part")
    try:
        if f.get("verified") and final.is_file():
            return "done", final.stat().st_size
    except OSError:
        pass
    try:
        return "partial", part.stat().st_size
    except OSError:
        return "pending", 0


def _job_summary(job_dir: Path, job: Dict[str, Any], card: str) -> Dict[str, Any]:
    done = 0
    nfiles_done = 0
    for f in job["files"]:
        st, n = _file_state(job_dir, f)
        done += n
        nfiles_done += st == "done"
    imp = _importer()
    alive = bool(job.get("pid")) and imp._pid_alive(job.get("pid"), job.get("boot_id", ""))
    total = sum(f["size"] for f in job["files"])
    return {"job_id": job["job_id"], "card": card, "path": str(job_dir), "node": job["node"], "root": job["root"],
            "state": job["state"], "created_at": job.get("created_at"), "running": alive, "resumable": job["state"] != "done",
            "files": len(job["files"]), "files_done": nfiles_done, "bytes_done": done, "bytes_total": total,
            "percent": int(done * 100 / total) if total else 0, "names": [f["name"] for f in job["files"]][:8],
            "message": job.get("message", "")}


def list_jobs(env: Env) -> Dict[str, Any]:
    out = []
    for cid in ("primary", "secondary"):
        c = env.cards.get(cid)
        if not c:
            continue
        base = _dl_root(c)
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for n in names:
            d = base / n
            if not d.is_dir():
                continue
            j = _load_job(d)
            if j is None:
                out.append({"job_id": n, "card": cid, "path": str(d), "state": "unreadable", "running": False, "resumable": False,
                            "files": 0, "files_done": 0, "bytes_done": 0, "bytes_total": 0, "percent": 0, "names": [], "message": "job.json hỏng/thiếu — dùng library cancel để dọn"})
            else:
                out.append(_job_summary(d, j, cid))
    return {"success": True, "jobs": out}


def find_job(env: Env, job_id: str) -> Tuple[str, Path]:
    for cid in ("primary", "secondary"):
        c = env.cards.get(cid)
        if c and re.match(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$", job_id) and (_dl_root(c) / job_id).is_dir():
            return cid, _dl_root(c) / job_id
    raise LibError("not_found", f"Không có job tải '{job_id}'.")


def cancel_job(env: Env, job_id: str) -> Dict[str, Any]:
    cid, d = find_job(env, job_id)
    j = _load_job(d)
    if j and _importer()._pid_alive(j.get("pid"), j.get("boot_id", "")) and j.get("pid") != os.getpid():
        raise LibError("invalid", "Job đang chạy ở tiến trình khác — không huỷ được lúc này.")
    _rmtree(d)
    _ledger(env.app_data, "job_cancelled", job=job_id, card=cid)
    return {"success": True, "message": f"Đã huỷ job {job_id} và xoá file tải dở.", "job_id": job_id, "gone": not d.exists()}


def prepare(env: Env, node_id: str, root: str, paths: List[str], card_pref: str = "auto") -> Dict[str, Any]:
    """CHỈ ĐỌC (trừ yêu cầu mạng): hỏi server kích thước + hash hiện tại của từng file (Range 0-0), kiểm giới hạn, chọn thẻ đích
    như importer (thẻ phụ trước, hỏi khi phải dùng thẻ Leaf, đủ chỗ). Không tạo gì trên thẻ."""
    imp, catalog = _catalog()
    if not paths:
        raise LibError("invalid", "Chưa chọn file nào.")
    if len(set(paths)) != len(paths):
        raise LibError("invalid", "Danh sách file có đường dẫn trùng.")
    cache = _load_cache(env, node_id, root) or {"entries": []}
    cached = {e["path"]: e for e in cache["entries"]}
    c = env.client(node_id)
    files, problems = [], []
    try:
        for p in paths:
            if not safe_remote_path(p):
                raise LibError("invalid", f"Đường dẫn không hợp lệ: {p!r}")
            cls = classify({"path": p, "size": cached.get(p, {}).get("size", 1), "mtime": "", "sha256": None,
                            "system_hint": cached[p].get("system_hint") if p in cached else hint_from_path(p, catalog)}, imp, catalog)
            f = {"path": p, "name": p.rsplit("/", 1)[-1], "local": local_name(p), "system": cls["system"] if cls else "?", "problem": None}
            try:
                pr = c.probe_file(root, p)
            except LibError as e:
                if e.kind in ("pin", "auth", "scope", "unreachable", "timeout", "network", "tls"):
                    raise
                f.update(size=0, sha256=None, problem=e.message)
                files.append(f)
                problems.append({"path": p, "reason": e.message})
                continue
            f.update(size=pr["size"], sha256=pr["sha256"])
            ext = _ext(f["name"])
            reason = None
            if ext in imp.EXEC_EXTS:
                reason = "file thực thi/script — importer chặn"
            elif ext in imp.UNSUPPORTED_ARCHIVE_EXTS and _archive().download_problem(f["name"]):
                reason = _archive().download_problem(f["name"])
            elif pr["size"] == 0:
                reason = "file rỗng"
            elif pr["size"] > imp.DEFAULT_LIMITS.max_file_bytes:
                reason = "file lớn hơn 2 GiB (giới hạn FAT32/importer)"
            elif pr["sha256"] is None:
                reason = "server chưa băm xong, thử lại sau (chưa có SHA-256 để kiểm file tải về)"
            elif cached.get(p) and cached[p].get("sha256") and cached[p]["sha256"] != pr["sha256"]:
                reason = "file đã đổi trên server kể từ lần duyệt gần nhất: làm mới danh sách rồi chọn lại"
            if reason:
                f["problem"] = reason
                problems.append({"path": p, "reason": reason})
            files.append(f)
    finally:
        c.close()
    have = _present_names(env, imp, catalog, {f["system"] for f in files})
    for f in files:                                  # cảnh báo mềm: đã có file cùng tên trong Roms/ (importer vẫn là người quyết: trùng nội dung thì bỏ qua)
        f["present"] = f["name"].lower() in have.get(f["system"], ())
    lm = {local: [f["path"] for f in files if f["local"] == local] for local in {f["local"] for f in files}}
    for local, ps in lm.items():
        if len(ps) > 1:
            problems.append({"path": ps[0], "reason": f"hai file trùng tên sau khi làm sạch cho FAT32: {ps}"})
    total = sum(f["size"] for f in files)
    needed = 2 * total + imp.DEFAULT_LIMITS.reserve_bytes          # file tải + staging của importer + dự phòng
    card = imp.choose_card(env.cards, needed, card_pref)
    ok = not problems and card["selected"] is not None
    msg = "; ".join(f"{p['path']}: {p['reason']}" for p in problems[:3]) if problems else card["reason"]
    return {"success": True, "ok": ok, "message": msg, "node": node_id, "root": root, "files": files, "problems": problems,
            "total_bytes": total, "needed_bytes": needed, "card": card, "needs_card_choice": card["needs_choice"]}


Progress = Callable[[Dict[str, Any]], None]


class _Throttle:
    def __init__(self, cb: Optional[Progress]):
        self.cb, self.t = cb, 0.0

    def __call__(self, ev: Dict[str, Any], force: bool = False):
        if not self.cb:
            return
        now = time.monotonic()
        if force or now - self.t >= PROGRESS_EVERY_S:
            self.t = now
            self.cb(ev)


def _stream_part(c: Client, job: Dict[str, Any], f: Dict[str, Any], part: Path, have: int, emit) -> Optional[str]:
    """Một lượt yêu cầu + ghi .part. Trả sha256 (hex) của TOÀN BỘ .part khi đủ byte; 'retry' khi phải làm lại từ đầu (đã xoá .part);
    'check' khi .part vốn đã đủ (416 khớp): người gọi tự băm. Mất kết nối: ném LibError(network) và GIỮ .part."""
    headers = {"Accept-Encoding": "identity"}
    if have > 0:
        headers["Range"] = f"bytes={have}-"
        headers["If-Range"] = f'"sha256:{f["sha256"]}"'
    resp = c.request("GET", c.file_target(job["root"], f["path"]), headers)
    st = resp.status
    if st == 416:                                         # khoảng không thoả: .part đã đủ so với kích thước hiện tại chưa?
        cr = parse_content_range(resp.getheader("Content-Range"))
        c.close()
        if have > 0 and cr and cr[2] == have:
            f["size"] = have
            return "check"
        part.unlink(missing_ok=True)
        return "retry"
    if st not in (200, 206):
        raise c.error_from(resp)
    et = etag_sha(resp.getheader("ETag"))
    h = hashlib.sha256()
    if st == 206:
        cr = parse_content_range(resp.getheader("Content-Range"))
        if have == 0 or not cr or cr[0] != have or cr[1] != f["size"] - 1 or cr[2] != f["size"] or (et and et != f["sha256"]):
            c.close()                                     # trả lời không khớp điều đã xin: không dám nối
            part.unlink(missing_ok=True)
            return "retry"
        mode = "ab"
        with open(part, "rb") as old:                     # nạp lại phần đã có vào hash
            for chunk in iter(lambda: old.read(1024 * 1024), b""):
                h.update(chunk)
    else:                                                 # 200 = TOÀN BỘ file -> bỏ .part cũ, KHÔNG nối
        cl = resp.getheader("Content-Length")
        if et and et != f["sha256"]:
            f["sha256"], f["changed_on_server"] = et, True    # server công bố hash mới => file đã đổi
        if cl and cl.isdigit() and int(cl) != f["size"]:
            ncl = int(cl)
            if ncl == 0 or ncl > _importer().DEFAULT_LIMITS.max_file_bytes:
                c.close()
                raise LibError("invalid", "kích thước mới của file trên server không hợp lệ")
            f["size"], f["changed_on_server"] = ncl, True
        have, mode = 0, "wb"
    want, got = f["size"] - have, 0
    with open(part, mode) as out:
        while got < want:
            try:
                chunk = resp.read(min(CHUNK, want - got))
            except (http.client.HTTPException, OSError) as e:
                c.close()
                raise LibError("network", f"Mất kết nối giữa chừng ({e}); đã có {have + got} / {f['size']} byte — chạy resume để tiếp tục", sent=True)
            if not chunk:
                break
            try:
                out.write(chunk)
            except OSError as e:
                c.close()
                raise LibError("storage", f"Không ghi được file tải ({e}); .part giữ lại để tiếp tục", sent=True)
            h.update(chunk)
            got += len(chunk)
            emit(have + got)
        try:
            out.flush()
            os.fsync(out.fileno())
        except OSError as e:
            c.close()
            raise LibError("storage", f"Không ghi được file tải ({e}); .part giữ lại để tiếp tục", sent=True)
    if got != want:
        c.close()
        raise LibError("network", f"Server đóng kết nối sớm: có {have + got} / {f['size']} byte — chạy resume để tiếp tục", sent=True)
    try:
        extra = resp.read(1)
    except (http.client.HTTPException, OSError):
        extra = b""
        c.close()
    if extra:
        c.close()
        part.unlink(missing_ok=True)
        raise LibError("bad_response", "Server gửi nhiều byte hơn kích thước đã khai — bỏ file tải")
    return h.hexdigest()


def _download_file(c: Client, job_dir: Path, job: Dict[str, Any], f: Dict[str, Any], idx: int, base_done: int,
                   total: int, prog: _Throttle) -> None:
    """Tải + kiểm hash MỘT file. Thành công: file nằm ở files/<local>, f['verified']=True. Mất kết nối: ném LibError(network/timeout)
    và GIỮ .part để resume. Hash sai: xoá .part, ném LibError(hash)."""
    final = job_dir / "files" / f["local"]
    part = final.with_name(final.name + ".part")
    final.parent.mkdir(parents=True, exist_ok=True)
    nfiles = len(job["files"])

    def emit(done_in_file: int, phase: str = "download", force: bool = False):
        prog({"phase": phase, "job_id": job["job_id"], "index": idx + 1, "count": nfiles, "name": f["name"],
              "done": base_done + done_in_file, "total": total}, force)

    if final.is_file():                                   # đã đổi tên nhưng chưa kịp ghi job.json: băm lại, đừng tin
        if final.stat().st_size == f["size"] and _sha256_file(final) == f["sha256"]:
            f["verified"] = True
            return
        final.unlink()
    for _ in range(3):
        try:
            have = part.stat().st_size
        except OSError:
            have = 0
        if have > f["size"]:
            part.unlink()
            have = 0
        digest: Optional[str] = "check" if have == f["size"] else None
        if digest is None:
            digest = _stream_part(c, job, f, part, have, emit)
            if digest == "retry":
                continue
        if digest == "check":
            emit(f["size"], "verify", True)
            digest = _sha256_file(part)
        if digest != f["sha256"]:
            part.unlink(missing_ok=True)
            raise LibError("hash", f"SHA-256 không khớp: nhận {digest}, server công bố {f['sha256']}. Đã xoá bản tải, KHÔNG cài.")
        emit(f["size"], force=True)
        os.replace(part, final)
        _fsync_dir(final.parent)
        f["verified"] = True
        return
    raise LibError("network", "Không tiếp tục được file (server liên tục trả lời không khớp) — thử lại sau")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_job(env: Env, plan: Dict[str, Any], card_id: str) -> Tuple[Path, Dict[str, Any]]:
    card_root = env.cards.get(card_id)
    if not card_root:
        raise LibError("invalid", "Thẻ đích không có")
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = _dl_root(card_root) / jid
    job = {"schema": JOB_SCHEMA, "job_id": jid, "node": plan["node"], "root": plan["root"], "card": card_id,
           "created_at": _now_iso(), "state": "downloading", "message": "",
           "files": [{"path": f["path"], "name": f["name"], "local": f["local"], "size": f["size"], "sha256": f["sha256"],
                      "system": f["system"], "verified": False} for f in plan["files"]]}
    try:
        (d / "files").mkdir(parents=True, exist_ok=True)
        _save_job(d, job)
    except OSError as e:
        raise LibError("storage", f"Không tạo được thư mục tải trên thẻ đích: {e}")
    return d, job


def _rebind_card(env: Env, job_dir: Path, job: Dict[str, Any]) -> Optional[str]:
    """Thư mục job nằm trên chính thẻ đích (<thẻ>/.loongmate-import/downloads/<job>). Hai thẻ có thể đổi mountpoint/vai trò
    sau khi khởi động lại, nên vai trò ghi trong job ("primary"/"secondary") có thể đã trỏ sang thẻ KHÁC: luôn lấy lại vai trò
    theo thẻ đang chứa job_dir. Trả mô tả nếu phải đổi, None nếu không."""
    jd = os.path.realpath(job_dir)
    for cid, root in env.cards.items():
        if root and os.path.dirname(jd) == os.path.realpath(_dl_root(root)):
            if cid != job.get("card"):
                old = job.get("card")
                job["card"] = cid
                return f"Hai thẻ đã đổi vị trí từ lần trước ({old} -> {cid}): vẫn cài vào đúng thẻ chứa file tải ({root})."
            return None
    return None


def run_job(env: Env, job_dir: Path, progress: Optional[Progress] = None, conflict: str = "keep", session=None) -> Dict[str, Any]:
    """Tải (tiếp) mọi file chưa xong, kiểm hash, rồi cài bằng importer. Mất mạng -> job còn nguyên để resume.
    Job có source == "homebrew" (engine/homebrew.py) dùng đúng luồng này nhưng tải qua HTTPS công khai (`session`) thay vì node đã ghép đôi."""
    imp = _importer()
    job = _load_job(job_dir)
    if job is None:
        raise LibError("invalid", f"{job_dir}: job.json hỏng/thiếu — dùng library cancel để dọn.")
    if job.get("pid") and job["pid"] != os.getpid() and imp._pid_alive(job["pid"], job.get("boot_id", "")):
        raise LibError("invalid", "Job này đang chạy ở tiến trình khác.")
    swapped = _rebind_card(env, job_dir, job)
    prog = _Throttle(progress)
    res: Dict[str, Any] = {"success": False, "job_id": job["job_id"], "card": {"selected": job["card"], "root": str(env.cards.get(job["card"]))},
                           "resumable": False, "state": job["state"], "message": "", "installed": 0, "already": 0, "blocked": 0,
                           "failed": 0, "skipped": 0, "results": [], "changed": False, "downloaded_bytes": 0}
    if swapped:
        res["card_swapped"] = swapped
    job.update(pid=os.getpid(), boot_id=imp._boot_id(), state="downloading")
    _save_job(job_dir, job)
    total = sum(f["size"] for f in job["files"])
    c = None
    hb = job.get("source") == "homebrew"
    try:
        if hb:
            from engine import homebrew
            c, dl = (session or homebrew.Session()), homebrew.download_file
        elif job.get("source") == "web":                 # Tải từ web (engine/web.py): cùng download_file, Session riêng (mọi địa chỉ https công khai)
            from engine import homebrew, web
            c, dl = (session or web.Session()), homebrew.download_file
        else:
            c, dl = env.client(job["node"]), _download_file
        base = 0
        for i, f in enumerate(job["files"]):
            if not f.get("verified"):
                dl(c, job_dir, job, f, i, base, total, prog)
                _save_job(job_dir, job)
            base += f["size"]
        prog({"phase": "install", "job_id": job["job_id"], "index": len(job["files"]), "count": len(job["files"]), "name": "",
              "done": total, "total": total}, True)
        job["state"] = "installing"
        all_files = list(job["files"])
        patches, patch_failed = [], []
        if job.get("source") == "web":                   # Patch ROM (engine/patcher.py): .ips/.bps/.ups (hoặc zip chỉ có patch) vào kho patch, KHÔNG qua importer
            from engine import patcher
            patches, patch_failed = patcher.route_web_job(env, job_dir, job)    # bỏ chúng khỏi job["files"]
        _save_job(job_dir, job)
        if job["files"]:
            inst = _install(env, job_dir, job, conflict)
        else:                                            # toàn patch: không còn gì cho importer
            inst = {"success": not patch_failed, "message": "", "results": [], "installed": 0, "already": 0, "blocked": 0, "failed": 0,
                    "skipped": 0, "changed": False}
        if hb:
            homebrew.after_install(env, job, inst)
        res.update({k: inst.get(k) for k in ("installed", "already", "blocked", "failed", "skipped", "results", "changed")})
        res["message"] = inst["message"]
        res["success"] = bool(inst.get("success"))
        res["install"] = {"card": inst.get("card"), "job_id": inst.get("job_id")}
        if job.get("source") == "web":
            res["patches"] = patches
            res["results"] = list(res["results"] or []) + [{"status": "patch", "name": n, "message": "Đã lưu vào kho patch"} for n in patches]                 + [{"status": "failed", "name": x["name"], "message": x["message"]} for x in patch_failed]
            res["failed"] = (res["failed"] or 0) + len(patch_failed)
            if patch_failed:
                res["success"] = False
            if patches:
                pm = "Đã lưu %d patch — mở Library → Patch ROM để áp dụng." % len(patches)
                res["message"] = (res["message"] + " " + pm).strip()
            if patch_failed:
                res["message"] = (res["message"] + " Patch lỗi: " + "; ".join("%s: %s" % (x["name"], x["message"]) for x in patch_failed)).strip()
        if inst.get("retry_install"):                    # chưa cài được vì lý do tạm (thẻ đầy...): giữ file để thử lại
            job.update(state="installing", pid=None, message=inst["message"])
            _save_job(job_dir, job)
            res.update(resumable=True, state="installing")
            return res
        res["state"] = "done"
        res["downloaded_bytes"] = total
        _rmtree(job_dir)
        res["job_removed"] = not job_dir.exists()
        _ledger(env.app_data, "job_done", job=job["job_id"], node=job["node"], root=job["root"], files=[f["path"] for f in all_files],
                installed=res["installed"], already=res["already"], blocked=res["blocked"], failed=res["failed"])
        return res
    except LibError as e:
        res["error_kind"], res["message"] = e.kind, e.message
        if e.kind in ("hash", "not_file"):               # dữ liệu không đáng tin / máy chủ trả trang HTML thay file: tải tiếp vô ích -> dọn cả job, không cài
            _rmtree(job_dir)
            res.update(state="failed", job_removed=not job_dir.exists(), resumable=False)
            _ledger(env.app_data, "job_hash_mismatch", job=job["job_id"], node=job["node"])
        else:                                            # mất mạng/hết chỗ/token...: giữ job + .part để resume
            job.update(state="interrupted", pid=None, message=e.message)
            try:
                _save_job(job_dir, job)
            except OSError:
                pass
            res.update(resumable=True, state="interrupted")
        return res
    except BaseException:
        try:
            job.update(state="interrupted", pid=None)
            _save_job(job_dir, job)
        except OSError:
            pass
        raise
    finally:
        if c:
            c.close()


def _install(env: Env, job_dir: Path, job: Dict[str, Any], conflict: str) -> Dict[str, Any]:
    """Giao file đã tải + kiểm cho importer (build_plan -> apply_plan). Nguồn ngoài Import/ nên importer không chuyển/xoá."""
    imp = _importer()
    from engine.hashcache import HashCache
    out: Dict[str, Any] = {"success": False, "message": "", "results": [], "installed": 0, "already": 0, "blocked": 0, "failed": 0,
                           "skipped": 0, "changed": False}
    files_dir = job_dir / "files"
    paths: List[Path] = []
    # Archive .7z/.rar/.tar.*... (engine/archive.py): giải vào job (cùng thẻ, cùng kiểm tra như Nhập game, tự thử mật khẩu gợi ý) rồi giao
    # thư mục giải ra cho importer. Không giải được (thiếu mật khẩu...) -> chuyển archive vào <thẻ>/Import/ để Nhập game > GIẢI NÉN xử lý.
    from engine import archive
    unpacked: Dict[str, str] = {}                         # thư mục giải ra -> hệ máy của archive (gợi ý chọn hệ)
    held: List[Dict[str, Any]] = []
    for f in job["files"]:
        p = files_dir / f["local"]
        if not p.is_file() or not archive.can_unpack_name(p.name):
            continue
        r = archive.unpack(p, auto_password=True, remember_file=Path(env.app_data) / archive.REMEMBERED,
                           out_dir=job_dir / "unpacked")
        if r.get("success"):
            unpacked[r["dest"]] = f["system"]
            continue
        try:
            imp_dir = imp.import_dir(env.cards[job["card"]])
            imp_dir.mkdir(exist_ok=True)
            target = archive._unique(imp_dir / p.name)
            os.replace(p, target)                         # job và Import/ cùng thẻ
            where = f"đã chuyển vào {imp_dir.name}/{target.name} — mở Library → Nhập game → GIẢI NÉN"
            if r.get("needs_password"):
                where += " để chọn hoặc gõ mật khẩu"
        except OSError as e:
            where = f"không chuyển được vào Import/ ({e})"
        held.append({"status": "failed", "name": f["name"], "message": f"Chưa giải nén được: {r.get('message')}; {where}."})
    # Nhiều file CÙNG thư mục (không phải gốc files/) mà có playlist (.cue/.m3u/.gdi) và không có .zip -> giao CẢ THƯ MỤC cho importer
    # (một game nhiều file, giữ layout tương đối). Còn lại giao từng file một.
    groups: Dict[Path, List[Path]] = {}
    for f in job["files"]:
        p = files_dir / f["local"]
        if archive.can_unpack_name(p.name):               # đã giải ở trên (hoặc đã chuyển vào Import/)
            continue
        groups.setdefault(p.parent, []).append(p)
    for parent, ps in groups.items():
        if (parent != files_dir and len(ps) > 1 and any(imp._ext(p.name) in imp.PLAYLIST_EXTS for p in ps)
                and not any(imp._ext(p.name) == ".zip" for p in ps)):
            paths.append(parent)
        else:
            paths += ps
    paths += [Path(d) for d in unpacked]

    def hints_of(origins) -> set:                         # hệ máy (system_hint/đuôi) của các file tải thuộc một unit
        hs = set()
        for o in origins:
            o = str(o)
            for d, sysname in unpacked.items():
                if o == d or o.startswith(d.rstrip("/\\") + os.sep):
                    hs.add(sysname)
            for f in job["files"]:
                ap = str(files_dir / f["local"])
                if ap == o or ap.startswith(o.rstrip("/\\") + os.sep):
                    hs.add(f["system"])
        hs.discard("?")
        return hs
    card_id = job["card"]
    cards = env.cards
    hc = None
    try:
        hc = HashCache(env.app_data)
    except Exception:
        hc = None
    try:
        if not paths:                                    # chỉ có archive và đều chưa giải được: đã chuyển vào Import/
            out.update(results=held, failed=len(held), message=" ".join(x["message"] for x in held))
            return out
        plan = imp.build_plan([str(p) for p in paths], cards, env.app_data, card_pref=card_id, hashcache=hc)
        if not plan["ok"]:
            out.update(message=f"Chưa cài được: {plan['message']} Các file đã tải được giữ lại — giải phóng chỗ rồi chạy resume.",
                       retry_install=True)
            return out
        choices: Dict[str, str] = {}
        for u in plan["units"]:
            if u["status"] != "needs_choice":
                continue
            hints = hints_of(u.get("origin", []) or [u.get("source", "")])
            if len(hints) == 1 and next(iter(hints)) in u["candidates"]:
                choices[u["id"]] = next(iter(hints))
            else:
                folder_of = {imp.load_catalog()["by_id"][cid].get("folder") or cid: cid for cid in u["candidates"]}
                for h in list(hints):
                    if h in folder_of:
                        choices[u["id"]] = folder_of[h]
        if choices:
            plan = imp.build_plan([str(p) for p in paths], cards, env.app_data, card_pref=card_id, hashcache=hc, choices=choices)
        conflicts = {u["id"]: conflict for u in plan["units"] if u.get("action") == "conflict"}
        apply_plan = {"card": plan["card"], "units": plan["units"], "scan_roots": plan["scan_roots"]}
        r = imp.apply_plan(apply_plan, cards, env.app_data, choices=choices, conflicts=conflicts, bios_ok=(), delete_source=False,
                           hashcache=hc)
        for x in r["results"]:
            if x.get("status") == "skipped" and "BIOS" in (x.get("message") or ""):
                x["message"] += " (LoongMate không tự xác nhận BIOS từ thư viện riêng: dùng Nhập game)"
        out.update({k: r.get(k) for k in ("results", "installed", "already", "blocked", "failed", "skipped", "changed", "card", "job_id", "message")})
        out["success"] = bool(r.get("success")) and not held
        if held:
            out["results"] = list(out.get("results") or []) + held
            out["failed"] = (out.get("failed") or 0) + len(held)
            out["message"] = ((out.get("message") or "") + " " + " ".join(x["message"] for x in held)).strip()
        if not r.get("installed") and not r.get("already") and "không đủ chỗ" in (r.get("message") or ""):
            out["retry_install"] = True               # thẻ đích hết chỗ lúc cài: giữ file đã tải để thử lại khi đã giải phóng chỗ
        return out
    finally:
        if hc:
            hc.close()


def fetch(env: Env, node_id: str, root: str, paths: List[str], card_pref: str = "auto", progress: Optional[Progress] = None,
          conflict: str = "keep") -> Dict[str, Any]:
    """Chuẩn bị + tạo job + tải + kiểm + cài. Trả kết quả của run_job, hoặc {success False, needs_card_choice...} nếu chưa làm gì."""
    pl = prepare(env, node_id, root, paths, card_pref)
    if not pl["ok"]:
        return {"success": False, "started": False, "message": pl["message"], "needs_card_choice": pl["needs_card_choice"], "plan": pl}
    d, job = create_job(env, pl, pl["card"]["selected"])
    _ledger(env.app_data, "job_created", job=job["job_id"], node=node_id, root=root, files=len(paths), bytes=pl["total_bytes"])
    out = run_job(env, d, progress, conflict)
    out["started"] = True
    return out


def resume(env: Env, job_id: str, progress: Optional[Progress] = None, conflict: str = "keep", session=None) -> Dict[str, Any]:
    cid, d = find_job(env, job_id)
    out = run_job(env, d, progress, conflict, session=session)
    out["started"] = True
    if out.get("card_swapped"):
        out["message"] = (out["message"] + " " if out.get("message") else "") + out["card_swapped"]
    return out
