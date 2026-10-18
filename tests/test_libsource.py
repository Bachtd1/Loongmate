"""Thư viện riêng (engine/libsource.py): hành vi thật qua mạng loopback.

  * Server TLS giả (http.server + ssl) nói đúng giao thức agent PocketBridge: library/index/file với Range, If-Range, ETag
    "sha256:<hex>", 416, pair/exchange một lần (409 already_exchanged). Nó GHI NHẬT KÝ từng request (có Authorization hay
    không) và từng lượt bắt tay TLS, để chứng minh "không gửi token cho máy lạ" bằng log của SERVER chứ không bằng đọc code.
  * SOCKS5 giả ghi lại ATYP + tên đích.
  * Thẻ giả (thư mục tạm); cài thật bằng importer thật rồi nhìn cây thư mục Roms/.
  * Dữ liệu: byte ngẫu nhiên đặt tên .gba/.sfc (không ROM thương mại).

Chứng minh test biết đỏ: LOONGMATE_LIBSOURCE_PATH=<bản libsource.py cố tình làm hỏng> (xem docs/THU-VIEN-RIENG.md mục "Chạy test").
Tên host dùng trong test chỉ đi qua SOCKS giả; không có kết nối ra ngoài loopback.
"""
import base64
import hashlib
import http.server
import importlib.util
import json
import os
import random
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_override = os.environ.get("LOONGMATE_LIBSOURCE_PATH")
if _override:
    _spec = importlib.util.spec_from_file_location("libsource_under_test", _override)
    LS = importlib.util.module_from_spec(_spec)
    sys.modules["libsource_under_test"] = LS
    _spec.loader.exec_module(LS)
else:
    import engine.libsource as LS
import engine.importer as IM

# Chứng thư/khoá TỰ KÝ chỉ để thử (sinh bằng openssl, hạn 100 năm). Không dùng ở đâu khác.
TEST_KEY = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgAGfHb0qX87ucrvtP
Ma4ZPGZRjEJWArbwAheCO9OutNmhRANCAATFiaYTfeHwLwZJXX211w87M1nA0Ncj
PCGt3TYXkNWrCVCfpQ0S2Akpb93VIVrrXUawFjHCMQ64AAH+r63qHrax
-----END PRIVATE KEY-----
"""
TEST_CERT = """-----BEGIN CERTIFICATE-----
MIIBiDCCAS+gAwIBAgIUdOAeTDraqAzwuEdObv5xKIkOBZAwCgYIKoZIzj0EAwIw
GTEXMBUGA1UEAwwObG9vbmdtYXRlLXRlc3QwIBcNMjYxMDA0MDIyNTEzWhgPMjEy
NjA5MTAwMjI1MTNaMBkxFzAVBgNVBAMMDmxvb25nbWF0ZS10ZXN0MFkwEwYHKoZI
zj0CAQYIKoZIzj0DAQcDQgAExYmmE33h8C8GSV19tdcPOzNZwNDXIzwhrd02F5DV
qwlQn6UNEtgJKW/d1SFa611GsBYxwjEOuAAB/q+t6h62saNTMFEwHQYDVR0OBBYE
FM4nDC0gwwpLXT1Sl6kJWAduw6tcMB8GA1UdIwQYMBaAFM4nDC0gwwpLXT1Sl6kJ
WAduw6tcMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDRwAwRAIgJjaPDSwH
cvj09/8fwjmZWnbwp9qhW+2c7BUhuKanF+cCIEfsOfm26fOOEFvSGeFRCiX+Obqe
l5EcAs30yjbaoqBO
-----END CERTIFICATE-----
"""
BEARER = "pb_" + "L" * 43
BOOT = "pb_" + "B" * 43
LONG = "pb_" + "C" * 43
KNOWN_HINTS = {"GBA", "SNES", "PSX", "GENESIS", "NES"}
MB = 1024 * 1024


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rnd(n: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(n)


# ======================================================================================
# agent giả
# ======================================================================================
class _Srv(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeAgent:
    def __init__(self):
        d = Path(tempfile.mkdtemp(prefix="lm-cert-"))
        (d / "c.pem").write_text(TEST_CERT)
        (d / "k.pem").write_text(TEST_KEY)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(d / "c.pem"), str(d / "k.pem"))
        shutil.rmtree(d, ignore_errors=True)
        self.der = ssl.PEM_cert_to_DER_cert(TEST_CERT)
        self.fingerprint = "sha256:" + hashlib.sha256(self.der).hexdigest()
        self.files = {}                    # path -> bytes
        self.nohash = set()                # path chưa băm xong: index sha256=null, không ETag
        self.etag_lie = {}                 # path -> hex ETag nói dối
        self.ignore_range = False
        self.cut = {}                      # path -> số byte gửi rồi cắt kết nối (một lần)
        self.tokens = {BEARER: ["library:read"]}
        self.bootstrap = {BOOT: {"used": False, "scopes": ["library:read"]}}
        self.log = []
        self.handshakes = 0
        self.page_limit = None             # ép trang nhỏ để thử phân trang
        self.index_extra = []              # entry thô chèn thêm vào index (thử dữ liệu độc)
        self.big_index_pad = 0
        self.lock = threading.RLock()
        agent = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                with agent.lock:
                    agent.handshakes += 1          # đếm lượt kết nối TLS tới server (kể cả khi client bỏ ngang sau bắt tay)
                self.request.settimeout(10)
                self.request.do_handshake()
                super().setup()

            def log_message(self, *a):
                pass

            def _json(self, code, obj, extra=None):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def _err(self, code, name, msg="x"):
                self._json(code, {"error": {"code": name, "message": msg}})

            def _entry(self, rec, **kw):
                rec.update(kw)
                with agent.lock:
                    agent.log.append(rec)

            def _auth(self):
                h = self.headers.get("Authorization", "")
                tok = h[7:] if h.startswith("Bearer ") else None
                return tok

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                tok = self._auth()
                rec = {"method": "POST", "path": self.path, "auth": bool(tok)}
                if self.path != "/api/v1/pair/exchange":
                    self._entry(rec, status=404)
                    return self._err(404, "not_found")
                b = agent.bootstrap.get(tok)
                if b is None:
                    self._entry(rec, status=401)
                    return self._err(401, "unauthorized")
                with agent.lock:
                    if b["used"]:
                        self._entry(rec, status=409)
                        return self._err(409, "already_exchanged", "đã đổi")
                    b["used"] = True
                    agent.tokens[LONG] = b["scopes"]
                json.loads(body)
                self._entry(rec, status=200)
                self._json(200, {"token": LONG, "token_id": "pocket1", "scopes": b["scopes"], "expires_at": None})

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                tok = self._auth()
                rec = {"method": "GET", "path": u.path, "auth": bool(tok), "range": self.headers.get("Range"),
                       "if_range": self.headers.get("If-Range")}
                if tok not in agent.tokens:
                    self._entry(rec, status=401)
                    return self._err(401, "unauthorized")
                if "library:read" not in agent.tokens[tok]:
                    self._entry(rec, status=403)
                    return self._err(403, "scope_denied")
                if u.path == "/api/v1/library":
                    self._entry(rec, status=200)
                    return self._json(200, {"roots": [{"id": "roms", "name": "ROM của tôi", "files": len(agent.files), "bytes": sum(map(len, agent.files.values())),
                                                       "indexed_at": "2026-10-04T09:30:00Z", "indexing": False}]})
                if u.path == "/api/v1/library/roms/index":
                    return self._index(rec, q)
                if u.path == "/api/v1/library/roms/file":
                    return self._file(rec, q)
                self._entry(rec, status=404)
                self._err(404, "not_found")

            def _etag(self, p):
                if p in agent.nohash:
                    return None
                return agent.etag_lie.get(p) or sha(agent.files[p])

            def _index(self, rec, q):
                limit = int(q.get("limit", ["100"])[0])
                if agent.page_limit:
                    limit = min(limit, agent.page_limit)
                cur = q.get("cursor", [""])[0]
                last = base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)).decode() if cur else None
                paths = sorted(agent.files, key=lambda s: s.encode())
                if last is not None:
                    paths = [p for p in paths if p.encode() > last.encode()]
                page = paths[:limit]
                ents = []
                for p in page:
                    top = p.split("/")[0].upper() if "/" in p else None
                    ents.append({"path": p, "size": len(agent.files[p]), "mtime": "2026-01-01T00:00:00Z", "sha256": None if p in agent.nohash else sha(agent.files[p]),
                                 "system_hint": top if top in KNOWN_HINTS else None})
                ents += agent.index_extra if not last else []
                more = len(paths) > limit
                nxt = base64.urlsafe_b64encode(page[-1].encode()).decode().rstrip("=") if page else cur
                self._entry(rec, status=200)
                body = {"entries": ents, "next_cursor": nxt, "has_more": more}
                if agent.big_index_pad:
                    body["pad"] = "x" * agent.big_index_pad
                self._json(200, body)

            def _file(self, rec, q):
                p = q.get("path", [""])[0]
                if p not in agent.files:
                    self._entry(rec, status=404)
                    return self._err(404, "not_found")
                data = agent.files[p]
                size = len(data)
                et = self._etag(p)
                rng = self.headers.get("Range")
                ifr = self.headers.get("If-Range")
                start, end, status = 0, size - 1, 200
                if rng and not agent.ignore_range:
                    ok_cond = True
                    if ifr is not None:
                        ok_cond = et is not None and ifr == f'"sha256:{et}"'
                    if ok_cond:
                        spec = rng.split("=", 1)[1]
                        a, _, b = spec.partition("-")
                        if "," in spec or not a.isdigit() or int(a) >= size:
                            self._entry(rec, status=416)
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        start = int(a)
                        end = min(int(b), size - 1) if b.isdigit() else size - 1
                        status = 206
                body = data[start:end + 1]
                self._entry(rec, status=status)
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                if et:
                    self.send_header("ETag", f'"sha256:{et}"')
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = agent.cut.get(p)
                if cut is not None and cut < len(body):
                    agent.cut.pop(p, None)
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    try:
                        self.request.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                self.wfile.write(body)

        class S(_Srv):
            def get_request(s):
                sock, addr = s.socket.accept()
                return agent.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

            def handle_error(s, request, client_address):
                pass

        self.srv = S(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def requests(self, path_prefix=""):
        return [r for r in self.log if r["path"].startswith(path_prefix)]


class FakeSocks:
    """SOCKS5 không xác thực. resolve: tên -> (host thật). Ghi lại (atyp, đích, cổng)."""

    def __init__(self, resolve):
        self.resolve, self.log = resolve, []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            c.settimeout(10)
            ver, n = c.recv(2)
            c.recv(n)
            c.sendall(b"\x05\x00")
            h = c.recv(4)
            atyp = h[3]
            if atyp == 1:
                host = socket.inet_ntoa(c.recv(4))
            elif atyp == 3:
                ln = c.recv(1)[0]
                host = c.recv(ln).decode()
            else:
                host = socket.inet_ntop(socket.AF_INET6, c.recv(16))
            port = int.from_bytes(c.recv(2), "big")
            self.log.append({"atyp": atyp, "host": host, "port": port})
            real = self.resolve.get(host)
            if real is None:
                c.sendall(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            up = socket.create_connection((real, port), timeout=10)
            c.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            c.settimeout(None)
            up.settimeout(None)

            def pump(a, b):
                try:
                    while True:
                        d = a.recv(65536)
                        if not d:
                            break
                        b.sendall(d)
                except OSError:
                    pass
                finally:
                    for s in (a, b):
                        try:
                            s.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
            threading.Thread(target=pump, args=(up, c), daemon=True).start()
            pump(c, up)
        except Exception:
            try:
                c.close()
            except OSError:
                pass

    def stop(self):
        self.srv.close()


# ======================================================================================
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm-lib-"))
        self.sd1, self.sd2 = self.tmp / "sd1", self.tmp / "sd2"
        for d in (self.sd1, self.sd2, self.tmp / "ud"):
            d.mkdir()
        self.ud = self.tmp / "ud" / "LoongMate"
        self.secrets = self.tmp / "secrets"
        self.agent = FakeAgent()
        self.addCleanup(self.agent.stop)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.secrets, proxy=None)
        self.gba = rnd(40000, 1)
        self.sfc = rnd(300000, 2)
        self.agent.files = {"GBA/Homebrew One.gba": self.gba, "SNES/Demo Two.sfc": self.sfc, "readme.txt": b"hi", "GBA/notes.md": b"x",
                            "GBA/LICENSE.license": b"x", "tools/run.sh": b"#!/bin/sh\n", "misc/cover.png": b"\x89PNG"}
        self.agent.nohash = {"readme.txt", "GBA/notes.md", "GBA/LICENSE.license", "tools/run.sh", "misc/cover.png"}

    def bundle(self, kind="bearer", fp=None, host="127.0.0.1", port=None, tok=None, nid="pi"):
        return {"schema": 1, "node": {"id": nid, "name": "Pi nhà", "host": host, "port": port or self.agent.port,
                                      "fingerprint": fp or self.agent.fingerprint},
                "credential": {"type": kind, "token": tok or (BEARER if kind == "bearer" else BOOT)}}

    def write_bundle(self, b, name="b.json"):
        d = self.sd1 / "PocketBridge-import"
        d.mkdir(exist_ok=True)
        p = d / name
        p.write_text(json.dumps(b))
        return p

    def pair(self, **kw):
        p = self.write_bundle(self.bundle(**kw))
        r = LS.pair(self.env, p)
        self.assertTrue(r["success"], r)
        return r

    def wait_handshake(self, n=1):
        import time
        t0 = time.time()
        while self.agent.handshakes < n and time.time() - t0 < 5:
            time.sleep(0.02)
        self.assertGreaterEqual(self.agent.handshakes, n, "client phải tới được bước bắt tay TLS (để so fingerprint)")
        time.sleep(0.3)                  # chờ: nếu client có gửi HTTP thì giờ đã kịp vào log

    def tree(self, root):
        return sorted(str(p.relative_to(root)).replace("\\", "/") for p in Path(root).rglob("*") if p.is_file())

    def roms(self, card=None):
        return [t for t in self.tree(card or self.sd2) if t.startswith("Roms/")]


# ======================================================================================
class PairingTests(Base):
    def test_bearer_pair_saves_token_and_removes_bundle(self):
        p = self.write_bundle(self.bundle())
        r = LS.pair(self.env, p)
        self.assertTrue(r["success"] and r["bundle_removed"], r)
        self.assertFalse(p.exists())
        self.assertEqual(LS.read_token(self.env, "pi"), BEARER)
        n = LS.load_nodes(self.env)[0]
        self.assertEqual((n["id"], n["port"], n["fingerprint"]), ("pi", self.agent.port, self.agent.fingerprint))
        self.assertNotIn(BEARER, (self.ud / LS.NODES_FILE).read_text())          # token KHÔNG nằm trong file nodes

    def test_bootstrap_exchange_once_then_bundle_removed(self):
        p = self.write_bundle(self.bundle("bootstrap"))
        r = LS.pair(self.env, p)
        self.assertTrue(r["success"] and r["bundle_removed"], r)
        self.assertFalse(p.exists(), "bundle phải bị xoá sau khi đổi token thành công")
        self.assertEqual(LS.read_token(self.env, "pi"), LONG)                      # lưu token DÀI HẠN, không phải bootstrap
        self.assertNotIn(BOOT, "".join(f.read_text() for f in self.secrets.iterdir()))
        ex = [x for x in self.agent.log if x["method"] == "POST"]
        self.assertEqual(len(ex), 1)

    def test_already_exchanged_409_reported_and_nothing_saved(self):
        self.agent.bootstrap[BOOT]["used"] = True
        p = self.write_bundle(self.bundle("bootstrap"))
        r = LS.pair(self.env, p)
        self.assertFalse(r["success"])
        self.assertEqual(r.get("code"), "already_exchanged")
        self.assertIn("pairing MỚI", r["message"])
        self.assertTrue(p.exists(), "file giữ nguyên như client PocketBridge")
        self.assertEqual(LS.load_nodes(self.env), [])
        self.assertFalse(self.secrets.exists() and any(self.secrets.iterdir()))

    def test_exchange_without_library_scope_rejected(self):
        self.agent.bootstrap[BOOT]["scopes"] = ["metrics:read"]
        p = self.write_bundle(self.bundle("bootstrap"))
        r = LS.pair(self.env, p)
        self.assertFalse(r["success"])
        self.assertIn("library:read", r["message"])
        self.assertEqual(LS.load_nodes(self.env), [])
        self.assertFalse(self.secrets.exists() and any(self.secrets.iterdir()), "không lưu token thiếu quyền")
        self.assertTrue(p.exists())

    def test_bearer_without_library_scope_rejected(self):
        self.agent.tokens[BEARER] = ["metrics:read"]
        r = LS.pair(self.env, self.write_bundle(self.bundle()))
        self.assertFalse(r["success"])
        self.assertEqual(LS.load_nodes(self.env), [])

    def test_wrong_fingerprint_sends_no_token_and_keeps_bundle(self):
        bad = "sha256:" + "0" * 64
        p = self.write_bundle(self.bundle("bootstrap", fp=bad))
        r = LS.pair(self.env, p)
        self.assertFalse(r["success"])
        self.assertIn("KHÁC", r["message"])
        self.assertTrue(p.exists())
        # BẰNG CHỨNG TỪ SERVER: có bắt tay TLS, nhưng KHÔNG có request HTTP nào (nên không có Authorization nào tới server)
        self.wait_handshake()
        self.assertEqual(self.agent.log, [], "server không được nhận bất kỳ request nào")

    def test_pinned_node_wrong_fingerprint_blocks_browse_without_token_on_wire(self):
        self.pair()
        nodes = LS.load_nodes(self.env)
        nodes[0]["fingerprint"] = "sha256:" + "1" * 64
        LS.save_nodes(self.env, nodes)
        before, hs = len(self.agent.log), self.agent.handshakes
        with self.assertRaises(LS.LibError) as cm:
            LS.list_roots(self.env, "pi")
        self.assertEqual(cm.exception.kind, "pin")
        self.wait_handshake(hs + 1)
        self.assertEqual(len(self.agent.log), before, "không request nào tới server khi fingerprint lệch")
        # và lỗi pin không bị che bằng cache cũ
        with self.assertRaises(LS.LibError) as cm:
            LS.browse(self.env, "pi", "roms")
        self.assertEqual(cm.exception.kind, "pin")

    def test_bad_bundles_rejected_and_kept(self):
        for mut in (lambda b: b["credential"].update(token="pb_short"),
                    lambda b: b["node"].update(fingerprint="sha256:ABC"),
                    lambda b: b["node"].update(port=0),
                    lambda b: b.update(schema=2),
                    lambda b: b.update(extra=1),
                    lambda b: b["node"].update(id="../x"),
                    lambda b: b["credential"].update(type="other")):
            b = self.bundle()
            mut(b)
            p = self.write_bundle(b)
            r = LS.pair(self.env, p)
            self.assertFalse(r["success"], b)
            self.assertTrue(p.exists())
            p.unlink()
        self.assertEqual(self.agent.log, [])

    def test_find_bundles_lists_both_dirs_without_following_symlink(self):
        self.write_bundle(self.bundle())
        (self.sd2 / "Import").mkdir()
        (self.sd2 / "Import" / "x.json").write_text("{not json")
        (self.sd2 / "Import" / ".hidden.json").write_text("{}")
        found = {(b["card"], b["dir"], b["file"]): b["valid"] for b in LS.find_bundles(self.env)}
        self.assertEqual(found, {("primary", "PocketBridge-import", "b.json"): True, ("secondary", "Import", "x.json"): False})

    @unittest.skipUnless(os.name == "posix", "quyền 0600 chỉ có nghĩa trên POSIX")
    def test_token_file_0600_and_dir_0700(self):
        self.pair()
        f = self.secrets / "pi.token"
        self.assertEqual(oct(os.stat(f).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(self.secrets).st_mode & 0o777), "0o700")
        os.chmod(f, 0o644)
        with self.assertRaises(LS.LibError):             # quyền bị nới -> từ chối dùng
            LS.read_token(self.env, "pi")

    @unittest.skipUnless(os.name == "posix", "quyền 0600 chỉ có nghĩa trên POSIX")
    def test_secret_dir_that_cannot_hold_permissions_is_refused(self):
        real_stat = os.stat

        def fake_stat(p, *a, **k):
            st = real_stat(p, *a, **k)
            if str(p).endswith(".partial") or str(p) == str(self.secrets):
                class S:
                    st_mode = 0o100777 if str(p).endswith(".partial") else 0o040777
                return S()
            return st
        with mock.patch("os.stat", fake_stat):
            r = LS.pair(self.env, self.write_bundle(self.bundle()))
        self.assertFalse(r["success"])
        self.assertIn("0600", r["message"])
        self.assertFalse((self.secrets / "pi.token").exists())
        self.assertEqual(LS.load_nodes(self.env), [])


class RouteTests(Base):
    def test_choose_route_rules(self):
        up = lambda h, p: True
        down = lambda h, p: False
        no = lambda h: False
        yes = lambda h: True
        P = ("127.0.0.1", 1055)
        for h in ("100.64.0.1", "100.127.255.254", "fd7a:115c:a1e0::1", "box.tail1234.ts.net", "BOX.TS.NET"):
            self.assertEqual(LS.choose_route(h, P, yes, up), P, h)
        for h in ("100.63.255.255", "100.128.0.1", "192.168.1.5", "example.com", "fd7a:115c:a1e1::1", "8.8.8.8"):
            self.assertIsNone(LS.choose_route(h, P, yes, up), h)
        self.assertEqual(LS.choose_route("mypi", P, no, up), P, "tên không phân giải được -> proxy")
        self.assertEqual(LS.choose_route("my.pi.lan", P, no, up), P)
        self.assertIsNone(LS.choose_route("mypi", P, yes, up), "phân giải được -> đi thẳng")
        self.assertIsNone(LS.choose_route("100.64.0.1", P, no, down), "cổng proxy đóng -> đi thẳng")
        self.assertIsNone(LS.choose_route("100.64.0.1", None, no, up), "proxy=none")

    def test_magicdns_name_goes_through_socks_with_atyp3(self):
        socks = FakeSocks({"pi.tail1.ts.net": "127.0.0.1"})
        self.addCleanup(socks.stop)
        env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.secrets, proxy=("127.0.0.1", socks.port))
        b = self.bundle(host="pi.tail1.ts.net")
        p = self.write_bundle(b)
        r = LS.pair(env, p)
        self.assertTrue(r["success"], r)
        roots = LS.list_roots(env, "pi")["roots"]
        self.assertEqual(roots[0]["id"], "roms")
        self.assertTrue(socks.log)
        self.assertTrue(all(x["atyp"] == 3 and x["host"] == "pi.tail1.ts.net" and x["port"] == self.agent.port for x in socks.log), socks.log)

    def test_tailnet_ip_uses_atyp1_and_loopback_goes_direct(self):
        socks = FakeSocks({"100.64.9.9": "127.0.0.1"})
        self.addCleanup(socks.stop)
        env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.secrets, proxy=("127.0.0.1", socks.port))
        self.assertTrue(LS.pair(env, self.write_bundle(self.bundle(host="100.64.9.9")))["success"])
        LS.list_roots(env, "pi")
        self.assertTrue(socks.log and all(x["atyp"] == 1 and x["host"] == "100.64.9.9" for x in socks.log), socks.log)
        socks.log.clear()
        n = LS.load_nodes(env)
        n[0]["host"] = "127.0.0.1"
        LS.save_nodes(env, n)
        LS.list_roots(env, "pi")
        self.assertEqual(socks.log, [], "127.0.0.1 phải đi thẳng, không qua proxy")

    def test_socks_refusal_is_reported(self):
        socks = FakeSocks({})
        self.addCleanup(socks.stop)
        env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.secrets, proxy=("127.0.0.1", socks.port))
        LS.save_nodes(env, [{"id": "pi", "name": "x", "host": "gone.ts.net", "port": 4268, "fingerprint": self.agent.fingerprint, "fallback_hosts": []}])
        LS.write_token(env, "pi", BEARER)
        with self.assertRaises(LS.LibError) as cm:
            LS.list_roots(env, "pi")
        self.assertEqual(cm.exception.kind, "unreachable")
        self.assertIn("REP 4", cm.exception.message)

    def test_fallback_host_used_when_first_unreachable(self):
        self.pair()
        n = LS.load_nodes(self.env)
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port_host = "127.0.0.1"
        n[0]["host"], n[0]["fallback_hosts"] = "127.0.0.2", [dead_port_host]
        LS.save_nodes(self.env, n)
        # 127.0.0.2:port không có ai nghe (server bind 127.0.0.1) -> thử host dự phòng
        self.assertEqual(LS.list_roots(self.env, "pi")["roots"][0]["id"], "roms")


class ValidationTests(Base):
    def test_response_size_limit_and_bad_schema(self):
        self.pair()
        self.agent.big_index_pad = LS.MAX_INDEX_JSON + 10
        with self.assertRaises(LS.LibError) as cm:
            LS.browse(self.env, "pi", "roms")
        self.assertEqual(cm.exception.kind, "too_large")

    def test_index_entry_with_unsafe_path_is_dropped_not_trusted(self):
        self.pair()
        self.agent.index_extra = [{"path": "../../etc/x.gba", "size": 1, "mtime": "t", "sha256": None, "system_hint": None},
                                  {"path": "/abs/y.gba", "size": 1, "mtime": "t", "sha256": None, "system_hint": None},
                                  {"path": "GBA/.hidden.gba", "size": 1, "mtime": "t", "sha256": None, "system_hint": "GBA"},
                                  {"path": "GBA/a\\b.gba", "size": 1, "mtime": "t", "sha256": None, "system_hint": "GBA"},
                                  {"path": "GBA/‮evil.gba", "size": 1, "mtime": "t", "sha256": None, "system_hint": "GBA"}]
        r = LS.browse(self.env, "pi", "roms", system="GBA")
        self.assertEqual([e["path"] for e in r["entries"]], ["GBA/Homebrew One.gba"])

    def test_missing_ssl_module_gives_clear_error(self):
        self.pair()
        with mock.patch.object(LS, "ssl", None):
            with self.assertRaises(LS.LibError) as cm:
                LS.list_roots(self.env, "pi")
        self.assertEqual(cm.exception.kind, "tls")
        self.assertIn("ssl", cm.exception.message)

    def test_malformed_index_rejected(self):
        self.pair()
        self.agent.index_extra = [{"path": "GBA/x.gba", "size": "big", "mtime": "t", "sha256": None, "system_hint": None}]
        with self.assertRaises(LS.LibError) as cm:
            LS.browse(self.env, "pi", "roms")
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_local_name_is_fat_safe_and_deterministic(self):
        self.assertEqual(LS.local_name('GBA/a:b?c.gba'), "GBA/a_b_c.gba")
        self.assertEqual(LS.local_name("GBA/x. "), "GBA/x")
        long = "GBA/" + "é" * 150 + ".gba"
        n = LS.local_name(long)
        self.assertTrue(n.endswith(".gba") and all(len(c.encode()) <= 200 for c in n.split("/")))
        self.assertEqual(LS.local_name(long), n)


class BrowseTests(Base):
    def setUp(self):
        super().setUp()
        self.pair()

    def test_filter_systems_and_hide_txt_md_license(self):
        r = LS.browse(self.env, "pi", "roms", only="all")
        names = {e["path"] for e in r["entries"]}
        self.assertEqual(names, {"GBA/Homebrew One.gba", "SNES/Demo Two.sfc"})   # run.sh: không đuôi hệ máy, không hint -> ẩn
        self.assertNotIn("GBA/notes.md", names)
        self.assertNotIn("GBA/LICENSE.license", names)
        self.assertNotIn("readme.txt", names)
        self.assertEqual({s["system"]: s["count"] for s in r["systems"]}, {"GBA": 1, "SNES": 1})

    def test_hint_without_known_ext_still_shown_but_txt_never(self):
        self.agent.files["GBA/patch.ips"] = b"PATCH"
        self.agent.files["GBA/readme.txt"] = b"x"
        r = LS.browse(self.env, "pi", "roms", system="GBA")
        names = {e["name"] for e in r["entries"]}
        self.assertIn("patch.ips", names)
        self.assertNotIn("readme.txt", names)

    def test_pagination_collects_every_entry(self):
        for i in range(25):
            self.agent.files[f"GBA/Game {i:02d}.gba"] = rnd(10, i)
        self.agent.page_limit = 7
        r = LS.browse(self.env, "pi", "roms", system="GBA")
        self.assertEqual(r["shown"], 26)
        pages = [x for x in self.agent.requests("/api/v1/library/roms/index")]
        self.assertGreaterEqual(len(pages), 5)

    def test_stale_cache_when_server_down_and_marked(self):
        r1 = LS.browse(self.env, "pi", "roms", system="GBA")
        self.assertFalse(r1["stale"])
        self.agent.stop()
        r2 = LS.browse(self.env, "pi", "roms", system="GBA")
        self.assertTrue(r2["stale"] and r2["from_cache"])
        self.assertTrue(r2["error"])
        self.assertEqual(r2["entries"], r1["entries"])
        r3 = LS.browse(self.env, "pi", "roms", system="GBA", refresh=False)     # đọc cache chủ động: from_cache nhưng KHÔNG "cũ do lỗi"
        self.assertTrue(r3["from_cache"] and not r3["stale"])

    def test_no_cache_and_server_down_raises(self):
        self.agent.stop()
        with self.assertRaises(LS.LibError):
            LS.browse(self.env, "pi", "roms")

    def test_query_filter(self):
        r = LS.browse(self.env, "pi", "roms", query="demo")
        self.assertEqual([e["name"] for e in r["entries"]], ["Demo Two.sfc"])

    def test_browse_writes_only_app_data_cache(self):
        before = (self.tree(self.sd1), self.tree(self.sd2), self.tree(self.secrets))
        LS.browse(self.env, "pi", "roms")
        LS.list_roots(self.env, "pi")
        self.assertEqual((self.tree(self.sd1), self.tree(self.sd2), self.tree(self.secrets)), before)


class FetchTests(Base):
    def setUp(self):
        super().setUp()
        self.pair()

    def fetch(self, paths, **kw):
        return LS.fetch(self.env, "pi", "roms", paths, **kw)

    def test_happy_path_installs_into_roms_and_cleans_job(self):
        events = []
        r = self.fetch(["GBA/Homebrew One.gba"], progress=events.append)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["installed"], 1)
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "Homebrew One.gba").read_bytes(), self.gba)
        self.assertFalse((self.sd2 / ".loongmate-import" / "downloads").exists() and any((self.sd2 / ".loongmate-import" / "downloads").iterdir()),
                         "job + .part phải được dọn sau khi xong")
        self.assertTrue(events and events[-1]["done"] == events[-1]["total"])
        self.assertEqual([e["done"] for e in events], sorted(e["done"] for e in events), "tiến độ không lùi")
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_multiple_files_across_systems(self):
        r = self.fetch(["GBA/Homebrew One.gba", "SNES/Demo Two.sfc"])
        self.assertTrue(r["success"], r)
        self.assertEqual(self.roms(), ["Roms/GBA/Homebrew One.gba", "Roms/SNES/Demo Two.sfc"])

    def test_default_card_is_secondary_and_primary_untouched(self):
        self.fetch(["GBA/Homebrew One.gba"])
        self.assertFalse((self.sd1 / "Roms").exists())

    def test_no_hash_yet_means_no_download_no_install(self):
        self.agent.nohash.add("GBA/Homebrew One.gba")
        n0 = len(self.agent.requests("/api/v1/library/roms/file"))
        r = self.fetch(["GBA/Homebrew One.gba"])
        self.assertFalse(r["success"] or r["started"])
        self.assertIn("server chưa băm xong", r["message"])
        for req in self.agent.requests("/api/v1/library/roms/file")[n0:]:
            self.assertEqual(req["range"], "bytes=0-0", "chỉ được thăm dò 1 byte, không tải file")
        self.assertEqual(self.roms(), [])
        self.assertFalse((self.sd2 / ".loongmate-import").exists())

    def test_wrong_sha_not_installed_and_part_removed(self):
        self.agent.etag_lie["GBA/Homebrew One.gba"] = sha(b"something else")      # server nói hash X nhưng gửi byte khác
        r = self.fetch(["GBA/Homebrew One.gba"])
        self.assertFalse(r["success"])
        self.assertEqual(r["error_kind"], "hash")
        self.assertIn("SHA-256 không khớp", r["message"])
        self.assertEqual(self.roms(), [], "không được cài khi hash sai")
        left = [t for t in self.tree(self.sd2) if ".loongmate-import" in t]
        self.assertEqual(left, [], f"không được còn .part/job: {left}")

    def test_cut_mid_download_then_resume_with_range_and_ifrange(self):
        self.agent.files["SNES/Demo Two.sfc"] = self.sfc
        self.agent.cut["SNES/Demo Two.sfc"] = 123457
        r = self.fetch(["SNES/Demo Two.sfc"])
        self.assertFalse(r["success"])
        self.assertTrue(r["resumable"], r)
        self.assertEqual(r["state"], "interrupted")
        self.assertEqual(self.roms(), [])
        parts = [p for p in (self.sd2 / ".loongmate-import" / "downloads").rglob("*.part")]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].stat().st_size, 123457)
        self.assertEqual(parts[0].read_bytes(), self.sfc[:123457])
        jobs = LS.list_jobs(self.env)["jobs"]
        self.assertEqual((len(jobs), jobs[0]["state"], jobs[0]["bytes_done"]), (1, "interrupted", 123457))
        n0 = len(self.agent.requests("/api/v1/library/roms/file"))
        r2 = LS.resume(self.env, jobs[0]["job_id"])
        self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), self.sfc)
        req = self.agent.requests("/api/v1/library/roms/file")[n0:]
        self.assertEqual(len(req), 1)
        self.assertEqual(req[0]["range"], "bytes=123457-")
        self.assertEqual(req[0]["if_range"], f'"sha256:{sha(self.sfc)}"')
        self.assertEqual(req[0]["status"], 206)
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_resume_after_cards_swapped_installs_on_card_holding_the_job(self):
        # Pocket 1: hai thẻ có thể đổi mountpoint sau khởi động -> thẻ phụ cũ thành "primary". Job (và .part) nằm trên thẻ đó.
        self.agent.cut["SNES/Demo Two.sfc"] = 123457
        r = self.fetch(["SNES/Demo Two.sfc"])
        self.assertTrue(r["resumable"], r)
        job_id = LS.list_jobs(self.env)["jobs"][0]["job_id"]
        self.env = LS.Env(self.ud, {"primary": self.sd2, "secondary": self.sd1}, self.secrets, proxy=None)
        r2 = LS.resume(self.env, job_id)
        self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), self.sfc)
        self.assertFalse((self.sd1 / "Roms").exists(), "không được cài sang thẻ kia chỉ vì nhãn primary/secondary đã đổi")
        self.assertIn("đổi vị trí", r2["message"])

    def test_resume_gets_200_when_etag_changed_restarts_from_zero_never_appends(self):
        self.agent.cut["SNES/Demo Two.sfc"] = 100000
        self.fetch(["SNES/Demo Two.sfc"])
        new = rnd(len(self.sfc), 99)                      # file đổi nội dung trên server (cùng cỡ: nối nhầm sẽ ra file hỏng im lặng)
        self.agent.files["SNES/Demo Two.sfc"] = new
        job = LS.list_jobs(self.env)["jobs"][0]
        n0 = len(self.agent.requests("/api/v1/library/roms/file"))
        r = LS.resume(self.env, job["job_id"])
        req = self.agent.requests("/api/v1/library/roms/file")[n0:]
        self.assertEqual([x["status"] for x in req], [200], "If-Range lệch -> server trả 200 toàn bộ")
        self.assertTrue(r["success"], r)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), new, "phải là bản MỚI nguyên vẹn, không nối vào .part cũ")

    def test_resume_200_without_etag_keeps_recorded_hash_and_restarts(self):
        self.agent.cut["SNES/Demo Two.sfc"] = 100000
        self.fetch(["SNES/Demo Two.sfc"])
        self.agent.ignore_range = True                     # server (mất hash) trả 200 cho mọi Range
        job = LS.list_jobs(self.env)["jobs"][0]
        r = LS.resume(self.env, job["job_id"])
        self.assertTrue(r["success"], r)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), self.sfc)

    def test_resume_416_when_part_already_complete(self):
        # .part đã đủ byte nhưng job chưa kịp đổi tên: resume không được tải lại, chỉ kiểm hash rồi cài
        self.agent.cut["SNES/Demo Two.sfc"] = 50000
        self.fetch(["SNES/Demo Two.sfc"])
        part = next((self.sd2 / ".loongmate-import" / "downloads").rglob("*.part"))
        part.write_bytes(self.sfc)
        job = LS.list_jobs(self.env)["jobs"][0]
        n0 = len(self.agent.requests("/api/v1/library/roms/file"))
        r = LS.resume(self.env, job["job_id"])
        self.assertTrue(r["success"], r)
        self.assertEqual(len(self.agent.requests("/api/v1/library/roms/file")) - n0, 0)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), self.sfc)

    def test_416_when_server_file_shrank_restarts(self):
        self.agent.cut["SNES/Demo Two.sfc"] = 200000
        self.fetch(["SNES/Demo Two.sfc"])
        small = rnd(150000, 5)
        self.agent.files["SNES/Demo Two.sfc"] = small
        job = LS.list_jobs(self.env)["jobs"][0]
        r = LS.resume(self.env, job["job_id"])
        self.assertTrue(r["success"], r)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), small)

    def test_part_longer_than_server_file_is_discarded(self):
        self.agent.cut["SNES/Demo Two.sfc"] = 1000
        self.fetch(["SNES/Demo Two.sfc"])
        part = next((self.sd2 / ".loongmate-import" / "downloads").rglob("*.part"))
        part.write_bytes(b"z" * (len(self.sfc) + 10))
        r = LS.resume(self.env, LS.list_jobs(self.env)["jobs"][0]["job_id"])
        self.assertTrue(r["success"], r)
        self.assertEqual((self.sd2 / "Roms" / "SNES" / "Demo Two.sfc").read_bytes(), self.sfc)

    def test_cancel_removes_job_and_part(self):
        self.agent.cut["SNES/Demo Two.sfc"] = 1000
        self.fetch(["SNES/Demo Two.sfc"])
        jid = LS.list_jobs(self.env)["jobs"][0]["job_id"]
        out = LS.cancel_job(self.env, jid)
        self.assertTrue(out["gone"])
        self.assertEqual([t for t in self.tree(self.sd2) if "downloads" in t], [])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_secondary_without_space_asks_instead_of_using_primary(self):
        real_free = IM._free
        with mock.patch.object(IM, "_free", lambda root: 10 * MB if Path(root) == self.sd2 else real_free(root)):
            pl = LS.prepare(self.env, "pi", "roms", ["GBA/Homebrew One.gba"])
            self.assertTrue(pl["needs_card_choice"])
            self.assertFalse(pl["ok"])
            r = self.fetch(["GBA/Homebrew One.gba"])
            self.assertFalse(r["started"])
            self.assertTrue(r["needs_card_choice"])
            self.assertFalse((self.sd1 / ".loongmate-import").exists(), "không được tự dùng thẻ Leaf")
            self.assertEqual(self.roms(self.sd1), [])
            r2 = self.fetch(["GBA/Homebrew One.gba"], card_pref="primary")
            self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd1 / "Roms" / "GBA" / "Homebrew One.gba").read_bytes(), self.gba)
        self.assertFalse((self.sd2 / "Roms").exists())

    def test_card_fills_up_before_install_keeps_files_and_resume_installs(self):
        real_free = IM._free
        dl = self.sd2 / ".loongmate-import" / "downloads"

        def free(root):                                    # thẻ phụ còn rộng khi tải, nhưng "đầy" ngay khi file tải xong nằm trên thẻ
            if Path(root) == self.sd2 and any(p.suffix == ".gba" for p in dl.rglob("*")):
                return 10 * MB
            return real_free(root)
        with mock.patch.object(IM, "_free", free):
            r = self.fetch(["GBA/Homebrew One.gba"])
            self.assertFalse(r["success"], r)
            self.assertTrue(r["resumable"] and r["state"] == "installing", r)
            self.assertEqual(self.roms(), [])
            self.assertTrue(any(p.suffix == ".gba" for p in dl.rglob("*")), "file đã tải phải được giữ")
        n0 = len(self.agent.requests("/api/v1/library/roms/file"))
        r2 = LS.resume(self.env, LS.list_jobs(self.env)["jobs"][0]["job_id"])
        self.assertTrue(r2["success"], r2)
        self.assertEqual(len(self.agent.requests("/api/v1/library/roms/file")), n0, "đã tải xong: resume chỉ cài, không tải lại")
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "Homebrew One.gba").read_bytes(), self.gba)

    def test_needed_space_counts_download_plus_staging(self):
        pl = LS.prepare(self.env, "pi", "roms", ["SNES/Demo Two.sfc"])
        self.assertEqual(pl["needed_bytes"], 2 * len(self.sfc) + IM.DEFAULT_LIMITS.reserve_bytes)

    def test_unsupported_and_exec_and_empty_files_refused_before_download(self):
        self.agent.files["GBA/pack.7z"] = b"7z"
        self.agent.files["GBA/empty.gba"] = b""
        for p in ("tools/run.sh", "GBA/pack.7z", "GBA/empty.gba"):
            self.agent.nohash.discard(p)
        pl = LS.prepare(self.env, "pi", "roms", ["tools/run.sh", "GBA/pack.7z", "GBA/empty.gba"])
        self.assertFalse(pl["ok"])
        self.assertEqual(len(pl["problems"]), 3, pl["problems"])
        self.assertEqual(self.roms(), [])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_list_changed_since_browse_is_refused(self):
        LS.browse(self.env, "pi", "roms")
        self.agent.files["GBA/Homebrew One.gba"] = rnd(40000, 77)
        pl = LS.prepare(self.env, "pi", "roms", ["GBA/Homebrew One.gba"])
        self.assertFalse(pl["ok"])
        self.assertIn("đã đổi", pl["message"])

    def test_scope_revoked_between_browse_and_download(self):
        self.agent.tokens.pop(BEARER)
        with self.assertRaises(LS.LibError) as cm:
            self.fetch(["GBA/Homebrew One.gba"])
        self.assertEqual(cm.exception.kind, "auth")

    def test_present_flag_after_install(self):
        self.assertTrue(self.fetch(["GBA/Homebrew One.gba"])["success"])
        r = LS.browse(self.env, "pi", "roms", system="GBA")
        self.assertEqual([(e["name"], e["present"]) for e in r["entries"]], [("Homebrew One.gba", True)])
        r = LS.browse(self.env, "pi", "roms", system="SNES")
        self.assertEqual([(e["name"], e["present"]) for e in r["entries"]], [("Demo Two.sfc", False)])
        pl = LS.prepare(self.env, "pi", "roms", ["GBA/Homebrew One.gba", "SNES/Demo Two.sfc"])
        self.assertEqual([(f["name"], f["present"]) for f in pl["files"]], [("Homebrew One.gba", True), ("Demo Two.sfc", False)])

    def test_installing_same_game_twice_reports_already_present(self):
        self.assertTrue(self.fetch(["GBA/Homebrew One.gba"])["success"])
        r = self.fetch(["GBA/Homebrew One.gba"])
        self.assertTrue(r["success"], r)
        self.assertEqual((r["installed"], r["already"]), (0, 1))

    def test_duplicate_after_fat_cleaning_refused(self):
        self.agent.files["GBA/a:b.gba"] = rnd(10, 1)
        self.agent.files["GBA/a_b.gba"] = rnd(10, 2)
        for p in ("GBA/a:b.gba", "GBA/a_b.gba"):
            self.agent.nohash.discard(p)
        pl = LS.prepare(self.env, "pi", "roms", ["GBA/a:b.gba", "GBA/a_b.gba"])
        self.assertFalse(pl["ok"])

    def test_multi_file_disc_installs_as_one_game(self):
        cue = b'FILE "Disc One.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        binb = rnd(2352 * 40, 8)
        self.agent.files["PSX/Disc One/Disc One.cue"] = cue
        self.agent.files["PSX/Disc One/Disc One.bin"] = binb
        r = self.fetch(["PSX/Disc One/Disc One.cue", "PSX/Disc One/Disc One.bin"])
        self.assertTrue(r["success"], r)
        self.assertEqual(self.roms(), ["Roms/PSX/Disc One.bin", "Roms/PSX/Disc One.cue"])   # một game, cả bộ, đúng hệ
        self.assertEqual(r["installed"], 1)

    def test_ambiguous_extension_resolved_by_system_hint(self):
        self.agent.files["GENESIS/Hb Game.bin"] = rnd(70000, 31)
        self.agent.nohash.discard("GENESIS/Hb Game.bin")
        r = self.fetch(["GENESIS/Hb Game.bin"])
        self.assertTrue(r["success"], r)
        self.assertEqual(self.roms(), ["Roms/GENESIS/Hb Game.bin"])

    def test_ambiguous_without_hint_is_not_guessed(self):
        self.agent.files["Hb Game.bin"] = rnd(70000, 32)
        r = self.fetch(["Hb Game.bin"])
        self.assertFalse(r["success"], r)
        self.assertEqual(self.roms(), [])
        self.assertEqual(r["skipped"] + r["blocked"] + r["failed"], 1)


class CliTests(Base):
    """cli.py library ... --json: chạy main() thật, stdout phải là JSON; lệnh chỉ-đọc không ghi gì lên thẻ/secret."""

    def run_cli(self, *argv, stdin_tty=False):
        import contextlib
        import io
        from engine import cli_impl
        out, err = io.StringIO(), io.StringIO()
        env = {"LOONGMATE_SECRETS_DIR": str(self.secrets), "LOONGMATE_LIB_PROXY": "none", "LOONGMATE_NO_RESCAN": "1"}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.object(sys.stdin, "isatty", lambda: stdin_tty):
                code = cli_impl.main(["--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--quiet",
                                      "--json", "--no-rescan", *argv])
        return code, out.getvalue(), err.getvalue()

    def jrun(self, *argv, **kw):
        code, out, err = self.run_cli(*argv, **kw)
        try:
            return code, json.loads(out), err
        except ValueError:
            self.fail(f"stdout không phải JSON: {out!r} / stderr {err!r}")

    def test_pair_via_cli_then_readonly_commands_write_nothing(self):
        p = self.write_bundle(self.bundle("bootstrap"))
        code, j, _ = self.jrun("library", "pair", str(p), "--yes")
        self.assertEqual(code, 0, j)
        self.assertTrue(j["paired"] and j["bundle_removed"])
        self.assertFalse(p.exists())
        snap = lambda: (self.tree(self.sd1), self.tree(self.sd2), self.tree(self.secrets),
                        sorted(t for t in self.tree(self.ud) if not t.startswith("library-cache/")),
                        (self.ud / LS.NODES_FILE).read_bytes())
        before = snap()
        for argv in (("nodes",), ("overview",), ("bundles",), ("roots", "pi"), ("list", "pi", "roms"), ("list", "pi", "roms", "--system", "GBA"),
                     ("list", "pi", "roms", "--offline", "--all"), ("jobs",), ("fetch", "pi", "roms", "GBA/Homebrew One.gba", "--dry-run")):
            code, j, _ = self.jrun("library", *argv)
            self.assertEqual(code, 0, (argv, j))
            self.assertTrue(j["success"], (argv, j))
        self.assertEqual(snap(), before, "lệnh chỉ-đọc không được ghi thẻ/secret/nodes (chỉ cache index trong app_data)")
        self.assertEqual([t for t in self.tree(self.ud) if t.startswith("library-cache/")], ["library-cache/pi__roms.json"])

    def test_json_shapes(self):
        self.pair()
        _, ov, _ = self.jrun("library", "overview")
        self.assertEqual((len(ov["nodes"]), ov["jobs"], ov["bundles"]), (1, [], []))
        _, nodes, _ = self.jrun("library", "nodes")
        self.assertEqual([(n["id"], n["has_token"]) for n in nodes["nodes"]], [("pi", True)])
        self.assertNotIn(BEARER, json.dumps(nodes))
        _, roots, _ = self.jrun("library", "roots", "pi")
        self.assertEqual(roots["roots"][0]["id"], "roms")
        _, lst, _ = self.jrun("library", "list", "pi", "roms")
        self.assertEqual({s["system"] for s in lst["systems"]}, {"GBA", "SNES"})
        self.assertNotIn("entries", lst)
        _, lst, _ = self.jrun("library", "list", "pi", "roms", "--system", "snes")
        self.assertEqual([e["path"] for e in lst["entries"]], ["SNES/Demo Two.sfc"])
        e = lst["entries"][0]
        self.assertEqual(set(e), {"path", "name", "size", "mtime", "sha256", "system", "installable", "problem", "present"})

    def test_fetch_requires_yes_when_not_a_tty(self):
        self.pair()
        n0 = len(self.agent.requests("/api/v1/library/roms/file"))
        code, j, _ = self.jrun("library", "fetch", "pi", "roms", "GBA/Homebrew One.gba")
        self.assertEqual(code, 1)
        self.assertFalse(j["success"])
        self.assertEqual(len(self.agent.requests("/api/v1/library/roms/file")), n0)
        self.assertEqual(self.roms(), [])

    def test_fetch_yes_progress_lines_and_json_result(self):
        self.pair()
        code, j, err = self.jrun("library", "fetch", "pi", "roms", "SNES/Demo Two.sfc", "--yes", "--progress")
        self.assertEqual(code, 0, j)
        self.assertEqual(j["installed"], 1)
        self.assertEqual(self.roms(), ["Roms/SNES/Demo Two.sfc"])
        lines = [l for l in err.splitlines() if l.startswith("LMPROGRESS ")]
        self.assertGreaterEqual(len(lines), 2)
        done = [int(l.split()[2]) for l in lines]
        self.assertEqual(done, sorted(done))
        self.assertEqual(int(lines[-1].split()[3]), len(self.sfc))

    def test_cut_then_jobs_resume_cancel_via_cli(self):
        self.pair()
        self.agent.cut["SNES/Demo Two.sfc"] = 70000
        code, j, _ = self.jrun("library", "fetch", "pi", "roms", "SNES/Demo Two.sfc", "--yes")
        self.assertEqual(code, 1)
        self.assertTrue(j["resumable"])
        _, jobs, _ = self.jrun("library", "jobs")
        jb = jobs["jobs"][0]
        self.assertEqual((jb["state"], jb["bytes_done"], jb["bytes_total"], jb["percent"]), ("interrupted", 70000, len(self.sfc), 70000 * 100 // len(self.sfc)))
        code, j, _ = self.jrun("library", "resume", jb["job_id"], "--yes")
        self.assertEqual(code, 0, j)
        self.assertEqual(self.roms(), ["Roms/SNES/Demo Two.sfc"])
        self.agent.cut["GBA/Homebrew One.gba"] = 100
        self.jrun("library", "fetch", "pi", "roms", "GBA/Homebrew One.gba", "--yes")
        jid = self.jrun("library", "jobs")[1]["jobs"][0]["job_id"]
        code, j, _ = self.jrun("library", "cancel", jid, "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.jrun("library", "jobs")[1]["jobs"], [])

    def test_unknown_node_and_errors_are_json_with_kind(self):
        code, j, _ = self.jrun("library", "roots", "nope")
        self.assertEqual((code, j["success"], j["error_kind"]), (1, False, "not_found"))

    def test_unpair_removes_token_node_and_cache(self):
        self.pair()
        self.jrun("library", "list", "pi", "roms")
        code, j, _ = self.jrun("library", "unpair", "pi", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(LS.load_nodes(self.env), [])
        self.assertEqual(self.tree(self.secrets), [])
        self.assertEqual([t for t in self.tree(self.ud) if t.startswith("library-cache/")], [])


class UiFlowTests(Base):
    """Màn Thư viện riêng của giao diện Python (không cần SDL): bấm phím như người dùng."""

    def setUp(self):
        super().setUp()
        from engine import ui_screens as ui
        from engine.inventory import InventoryEngine
        from engine.probe import PlatformProbe
        self.ui = ui
        pp = mock.patch.dict(os.environ, {ui.NO_RESCAN_ENV: "1", "LOONGMATE_SECRETS_DIR": str(self.secrets), "LOONGMATE_LIB_PROXY": "none"})
        pp.start()
        self.addCleanup(pp.stop)
        probe = PlatformProbe(str(self.sd1), str(self.sd2), str(self.ud))
        self.app = ui.App(probe, None, None, InventoryEngine(probe.primary_path, probe.secondary_path), None, self.ud, None)

    def goto(self, scr, text):
        idx = [i for i, it in enumerate(scr.items) if text in it.label]
        self.assertTrue(idx, [it.label for it in scr.items])
        while scr.sel != idx[0]:
            self.app.on_action("DOWN" if scr.sel < idx[0] else "UP")

    def press(self, *acts):
        for a in acts:
            self.app.on_action(a)
            self.app.wait_task()

    def test_pair_from_bundle_screen_confirm_defaults_to_cancel(self):
        p = self.write_bundle(self.bundle("bootstrap"))
        self.app.open_lib_nodes()
        nodes = self.app.cur()
        self.assertEqual(nodes.title, "THƯ VIỆN RIÊNG")
        self.goto(nodes, "Ghép đôi thư viện")
        self.press("A")
        scr = self.app.cur()
        self.assertEqual(scr.title, "GHÉP ĐÔI THƯ VIỆN")
        self.goto(scr, "b.json")
        self.press("A")
        self.assertEqual(self.app.dialog.choice, 0)
        self.press("A")                                        # Huỷ
        self.assertTrue(p.exists())
        self.assertEqual(LS.load_nodes(self.env), [])
        self.press("A", "RIGHT", "A")
        self.assertFalse(p.exists())
        self.assertEqual(LS.read_token(self.env, "pi"), LONG)
        self.assertIn("Đã ghép đôi", "\n".join(self.app.dialog.lines))

    def test_browse_mark_confirm_and_install_stays_put(self):
        self.pair()
        self.app.open_lib_nodes()
        nodes = self.app.cur()
        self.goto(nodes, "Pi nhà")
        self.press("A")
        roots = self.app.cur()
        self.assertEqual(roots.title, "THƯ VIỆN — thư mục")
        self.press("A")
        systems = self.app.cur()
        self.assertEqual(systems.title, "THƯ VIỆN — hệ máy")
        self.assertEqual([it.label for it in systems.items], ["GBA", "SNES"])
        self.goto(systems, "GBA")
        self.press("A")
        games = self.app.cur()
        self.assertFalse(any("CŨ" in h for h in games.header), games.header)     # đọc cache chủ động sau lần đọc server thành công: không phải danh sách cũ
        depth = len(self.app.stack)
        self.goto(games, "[ ] Homebrew One.gba")
        self.app.on_action("X")
        self.goto(games, "Tải & cài 1")
        self.press("A")                                        # kiểm với server -> hộp xác nhận
        self.assertEqual(self.app.dialog.choice, 0, "mặc định Huỷ")
        self.assertTrue(any("Homebrew One.gba" in l for l in self.app.dialog.lines))
        self.press("A")                                        # Huỷ
        self.assertEqual(self.roms(), [])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])
        self.goto(games, "Tải & cài 1")
        self.press("A", "RIGHT", "A")
        self.assertEqual(self.roms(), ["Roms/GBA/Homebrew One.gba"])
        self.assertEqual(len(self.app.stack), depth)
        self.assertIs(self.app.cur(), games)
        self.assertIn("Kết quả tải & cài", self.app.dialog.title)
        it = next(i for i in games.items if getattr(i, "lib_path", None))
        self.assertTrue(it.tag.startswith("đã có"), it.tag)
        self.assertTrue(it.label.startswith("[ ]"), "dấu chọn của game đã cài phải được bỏ")

    def test_sort_and_filter_keep_marks(self):
        self.pair()
        self.agent.files["GBA/Zed.gba"] = rnd(90000, 3)
        self.agent.files["GBA/Alpha.gba"] = rnd(1000, 4)
        LS.browse(self.env, "pi", "roms")                      # như khi mở màn hệ máy: nạp index + cache
        self.app.open_lib_games("pi", "roms", "GBA")
        self.app.wait_task()
        games = self.app.cur()
        names = lambda: [i.label[4:] for i in games.items if getattr(i, "lib_path", None)]
        self.assertEqual(names(), ["Alpha.gba", "Homebrew One.gba", "Zed.gba"])
        self.goto(games, "Zed.gba")
        self.app.on_action("X")
        self.app.on_action("Y")                                # sắp xếp theo dung lượng
        self.assertEqual(names(), ["Zed.gba", "Homebrew One.gba", "Alpha.gba"])
        self.assertIn("[x] Zed.gba", [i.label for i in games.items])
        self.goto(games, "Lọc:")
        self.app.on_action("A")                                # Chưa có trên máy
        self.assertEqual(len(names()), 3)
        self.app.on_action("A")                                # Đã có trên máy
        self.assertEqual(names(), [])
        self.assertIn("Đã chọn 1 file", " ".join(games.header))

    def test_stale_banner_when_server_down(self):
        self.pair()
        LS.browse(self.env, "pi", "roms")
        self.agent.stop()
        self.app.open_lib_systems("pi", "roms")
        self.app.wait_task()
        systems = self.app.cur()
        self.assertTrue(any("DANH SÁCH CŨ" in h for h in systems.header), systems.header)

    def test_error_shows_message_not_traceback(self):
        self.pair()
        n = LS.load_nodes(self.env)
        n[0]["fingerprint"] = "sha256:" + "2" * 64
        LS.save_nodes(self.env, n)
        self.app.open_lib_roots("pi")
        self.app.wait_task()
        self.assertEqual(self.app.dialog.title, "Thư viện riêng")
        self.assertIn("KHÁC", self.app.dialog.lines[0])

    def test_interrupted_job_listed_and_resumable_from_ui(self):
        self.pair()
        self.agent.cut["SNES/Demo Two.sfc"] = 90000
        r = LS.fetch(self.env, "pi", "roms", ["SNES/Demo Two.sfc"])
        self.assertTrue(r["resumable"])
        self.app.open_lib_nodes()
        nodes = self.app.cur()
        self.goto(nodes, "Tải dở")
        self.assertTrue(nodes.cur_item().tag.endswith("%"))
        self.press("A")
        self.assertEqual(self.app.dialog.choice, 0)
        self.press("A", "A", "RIGHT", "A")
        self.assertEqual(self.roms(), ["Roms/SNES/Demo Two.sfc"])
        self.assertFalse([i for i in nodes.items if "Tải dở" in i.label])


if __name__ == "__main__":
    unittest.main()
