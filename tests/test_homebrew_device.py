"""Homebrew Hub tải trực tiếp trên máy (engine/homebrew.py): hành vi thật qua mạng loopback, không Internet.

  * Máy chủ HTTPS giả (http.server + ssl) nói kiểu Homebrew Hub (/api/search phân trang) và raw GitHub (ETag mạnh, Range, If-Range, 206/200/416),
    GHI NHẬT KÝ từng request (User-Agent, Range, If-Range) và từng lượt bắt tay TLS, để chứng minh "không gửi byte HTTP nào cho máy
    chưa được xác minh" / "không chạm máy lạ" bằng log của SERVER chứ không bằng đọc code.
  * Ba chứng thư tự ký chỉ để thử (hạn 100 năm): GOOD (SAN localhost + 127.0.0.1, nằm trong cafile của Session), UNTRUSTED (cùng tên nhưng KHÔNG nằm
    trong cafile), WRONGNAME (nằm trong cafile nhưng SAN là other.test). Không dùng ở đâu khác.
  * Thẻ giả (thư mục tạm); cài thật bằng importer thật rồi nhìn cây thư mục Roms/. Dữ liệu: byte ngẫu nhiên đặt tên .gb (không ROM thật).

Chứng minh test biết đỏ: HOMEBREW_PATH=<bản homebrew.py cố tình làm hỏng> (tắt verify CA / bỏ chặn host-redirect / bỏ kiểm hash) -> các test
tương ứng phải đỏ. Xem docs/HOMEBREW.md mục "Chạy test".
"""
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
import random
import shutil
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

import engine.libsource as LS  # noqa: E402

_override = os.environ.get("HOMEBREW_PATH")
if _override:       # bản cố tình làm hỏng: libsource.run_job nạp `engine.homebrew` nên phải thay đúng chỗ đó
    _spec = importlib.util.spec_from_file_location("engine.homebrew", _override)
    HB = importlib.util.module_from_spec(_spec)
    sys.modules["engine.homebrew"] = HB
    _spec.loader.exec_module(HB)
    import engine
    engine.homebrew = HB
else:
    import engine.homebrew as HB  # noqa: E402
import engine.cli as CLI  # noqa: E402

RealSession = HB.Session      # test vá HB.Session để libsource.resume/CLI dùng phiên giả; sess() phải dùng bản thật

# ---------------------------------------------------------------- chứng thư thử (openssl, EC P-256, hạn 100 năm)
GOOD_CRT = '''-----BEGIN CERTIFICATE-----
MIIBkjCCATegAwIBAgIUdYeWx7oUS2kEV7aPiGUpevuOx0wwCgYIKoZIzj0EAwIw
DzENMAsGA1UEAwwEZ29vZDAgFw0yNjEwMDQwNDEyNThaGA8yMTI2MDkxMDA0MTI1
OFowDzENMAsGA1UEAwwEZ29vZDBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABAhB
NAqhwOk9P4f/Iouq5TKrgXnDK3gkfCXVnCdqE1FGHChAOPlFkQKtoX5oJDOu9N2k
ztEEx9xbnn+/r3y95xmjbzBtMB0GA1UdDgQWBBQjGSGJGToV86MeCB2/Gs9HNH4Z
hzAfBgNVHSMEGDAWgBQjGSGJGToV86MeCB2/Gs9HNH4ZhzAaBgNVHREEEzARggls
b2NhbGhvc3SHBH8AAAEwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNJADBG
AiEAlS9j5ZNPGA8DLym5ETOX5BXkIvYp11PHv/+XF/wA6WECIQC+kProFq75EaFB
o4V/+8EbfbgFBLiGwu6nBsRQ35SzBw==
-----END CERTIFICATE-----
'''
GOOD_KEY = '''-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgdTo3HFZp4mnYxIkh
EuXxyGH7IZYkR/WknHzKk2qFFzihRANCAAQIQTQKocDpPT+H/yKLquUyq4F5wyt4
JHwl1ZwnahNRRhwoQDj5RZECraF+aCQzrvTdpM7RBMfcW55/v698vecZ
-----END PRIVATE KEY-----
'''
UNTRUSTED_CRT = '''-----BEGIN CERTIFICATE-----
MIIBmjCCAUGgAwIBAgIUOg8SIqxjglugvYT9JQqFoobRcxAwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJdW50cnVzdGVkMCAXDTI2MTAwNDA0MTMwNFoYDzIxMjYwOTEw
MDQxMzA0WjAUMRIwEAYDVQQDDAl1bnRydXN0ZWQwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAARVPVy3FtILKc8UGqt9CaWaDRfdaEW+/6odGksLzxGZhNiwXkzdq6tm
eEBQqgpNrqxcP2YtFUP9Tw3R7M/qHlsoo28wbTAdBgNVHQ4EFgQUYZg4rzJxd8ag
P+RN1Vt7e4HG4PowHwYDVR0jBBgwFoAUYZg4rzJxd8agP+RN1Vt7e4HG4PowGgYD
VR0RBBMwEYIJbG9jYWxob3N0hwR/AAABMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZI
zj0EAwIDRwAwRAIgcLtWOcfT6Wb1Lbm6W8TJvedRFaoOdEhM1ErYhDi3DIQCIHtm
5o+WockGpNRHYmtUMBVqR1FnVBPjwjwkaKl2wcrr
-----END CERTIFICATE-----
'''
UNTRUSTED_KEY = '''-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg2Z6fVb68IxeheWUM
2IzBUGD7tf74ldGrqK9eOYeb64+hRANCAARVPVy3FtILKc8UGqt9CaWaDRfdaEW+
/6odGksLzxGZhNiwXkzdq6tmeEBQqgpNrqxcP2YtFUP9Tw3R7M/qHlso
-----END PRIVATE KEY-----
'''
WRONGNAME_CRT = '''-----BEGIN CERTIFICATE-----
MIIBlzCCATygAwIBAgIUSyxGGb38yRTXUkm+mexardoXLRQwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJd3JvbmduYW1lMCAXDTI2MTAwNDA0MTMwNFoYDzIxMjYwOTEw
MDQxMzA0WjAUMRIwEAYDVQQDDAl3cm9uZ25hbWUwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAAR1ZHK3cVz51beN2zOsVvOU8XNhxgFeNknrlw2Z8T0yqiysZ44ISKXd
PNlYZLJXV5eLK8zpEk/Nc2kdBxs86Qsto2owaDAdBgNVHQ4EFgQUCR/b+foAW24E
6NVh9b2QLcnig5EwHwYDVR0jBBgwFoAUCR/b+foAW24E6NVh9b2QLcnig5EwFQYD
VR0RBA4wDIIKb3RoZXIudGVzdDAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMC
A0kAMEYCIQCRe/wyJq7OZYD90gSQk/cMkTfqcBCSg3gDWbBOgBr/ZwIhAM6fEFPC
zoFmiv++MRQlZ2uDZ23fN3giIn3Cgxzk4+AO
-----END CERTIFICATE-----
'''
WRONGNAME_KEY = '''-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgnCX2VzFssRjPARbR
xDhYHN3ikScWhaPHbYIgTybTHlihRANCAAR1ZHK3cVz51beN2zOsVvOU8XNhxgFe
Nknrlw2Z8T0yqiysZ44ISKXdPNlYZLJXV5eLK8zpEk/Nc2kdBxs86Qst
-----END PRIVATE KEY-----
'''


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rnd(n: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(n)


def write_pem(d: Path, name: str, text: str) -> str:
    p = d / name
    p.write_text(text)
    return str(p)


# ======================================================================================
# máy chủ giả
# ======================================================================================
class _Srv(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeHub:
    """Một máy chủ HTTPS: /api/search (Hub) + /gbdev/database/master/entries/<slug>/<file> (raw GitHub) + các đường chuyển hướng."""

    def __init__(self, crt: str, key: str, workdir: Path):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(write_pem(workdir, "c-%d.pem" % id(self), crt), write_pem(workdir, "k-%d.pem" % id(self), key))
        self.api = {}                    # platform -> [entry thô]
        self.page_size = 2
        self.files = {}                  # "slug/file" -> bytes
        self.cut = {}                    # "slug/file" -> số byte gửi rồi cắt kết nối (một lần)
        self.log = []
        self.handshakes = 0
        self.api_fail = 0                # số lần đầu trả 503
        self.redirects = {}              # path -> Location
        self.require_ua = True
        self.lock = threading.RLock()
        hub = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                with hub.lock:
                    hub.handshakes += 1
                self.request.settimeout(10)
                self.request.do_handshake()
                super().setup()

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                rec = {"path": u.path, "query": u.query, "ua": self.headers.get("User-Agent"), "range": self.headers.get("Range"),
                       "if_range": self.headers.get("If-Range")}
                with hub.lock:
                    hub.log.append(rec)
                if hub.require_ua and not rec["ua"]:
                    return self._send(403, b"no UA")
                if u.path in hub.redirects:
                    return self._send(302, b"", {"Location": hub.redirects[u.path]})
                if u.path == "/api/search":
                    return self._search(urllib.parse.parse_qs(u.query))
                prefix = "/gbdev/database/master/entries/"
                if u.path.startswith(prefix):
                    return self._file(urllib.parse.unquote(u.path[len(prefix):]))
                self._send(404, b"nope")

            def _search(self, q):
                if hub.api_fail > 0:
                    hub.api_fail -= 1
                    return self._send(503, b"busy")
                items = hub.api.get(q["platform"][0], [])
                page = int(q["page"][0])
                chunk = items[(page - 1) * hub.page_size: page * hub.page_size]
                total = max(1, -(-len(items) // hub.page_size))
                body = json.dumps({"results": len(items), "page_total": total, "page_current": page, "entries": chunk}).encode()
                self._send(200, body, {"Content-Type": "application/json"})

            def _file(self, name):
                data = hub.files.get(name)
                if data is None:
                    return self._send(404, b"nope")
                size = len(data)
                et = '"%s"' % sha256(data)
                rng, ifr = self.headers.get("Range"), self.headers.get("If-Range")
                start, end, status = 0, size - 1, 200
                if rng and (ifr is None or ifr == et):
                    a, _, b = rng.split("=", 1)[1].partition("-")
                    if not a.isdigit() or int(a) >= size:
                        return self._send(416, b"", {"Content-Range": "bytes */%d" % size})
                    start, end, status = int(a), min(int(b), size - 1) if b.isdigit() else size - 1, 206
                body = data[start:end + 1]
                hdr = {"ETag": et, "Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"}
                if status == 206:
                    hdr["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
                self.send_response(status)
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = hub.cut.get(name)
                if cut is not None and cut < len(body):
                    hub.cut.pop(name, None)
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    try:
                        self.request.shutdown(2)
                    except OSError:
                        pass
                    return
                self.wfile.write(body)

        class S(_Srv):
            def get_request(s):
                sock, addr = s.socket.accept()
                return hub.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

            def handle_error(s, request, client_address):
                pass

        self.srv = S(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self):
        return "https://127.0.0.1:%d" % self.port

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def requests(self, prefix=""):
        return [r for r in self.log if r["path"].startswith(prefix)]


def entry(slug, title, license="MIT", filename=None, platform="GB", **extra):
    e = {"title": title, "slug": slug, "developer": "Dev " + slug, "typetag": "game", "platform": platform,
         "files": [{"default": True, "filename": filename or slug + ".gb", "playable": True}],
         "description": "Mo ta cua " + title, "baserepo": "https://github.com/gbdev/database"}
    if license is not None:
        e["license"] = license
    e.update(extra)
    return e


GB_A, GB_B, GB_C = rnd(60000, 1), rnd(300000, 2), rnd(20000, 3)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm-hb-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir()
        self.env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.tmp / "secrets", proxy=None)
        self.good = write_pem(self.tmp, "good.crt", GOOD_CRT)
        self.hub = self.start_hub(GOOD_CRT, GOOD_KEY)
        self.hub.files = {"alpha/alpha.gb": GB_A, "bravo/bravo.gb": GB_B, "charlie/charlie.gb": GB_C}
        self.hub.api = {"GB": [
            entry("alpha", "Alpha Quest"),
            entry("bravo", "Bravo: The Game", license="CC-BY-4.0",
                  files=[{"default": True, "filename": "bravo.gb", "playable": True, "hash": {"sha256": sha256(GB_B)}}]),
            entry("charlie", "Charlie", files=[{"default": True, "filename": "charlie.gb", "playable": True, "hash": {"sha256": sha256(b"khac")}}]),
            entry("nolic", "Khong giay phep", license=None),
            entry("weird", "Giay phep la", license="Some Custom License"),
            entry("nodl", "Tat tai xuong", **{"use-requirements": {"disable-downloads": True}}),
            entry("ccnover", "Thieu phien ban CC", license="CC-BY-SA"),
        ], "GBC": [], "GBA": [], "NES": []}
        for p in (mock.patch.object(HB, "API", self.hub.base + "/api"), mock.patch.object(HB, "RAW_HOST", self.hub.base)):
            p.start()
            self.addCleanup(p.stop)

    def start_hub(self, crt, key):
        h = FakeHub(crt, key, self.tmp)
        self.addCleanup(h.stop)
        return h

    def sess(self, hub=None, cafile=None, **kw):
        hub = hub or self.hub
        kw.setdefault("min_interval", 0)
        kw.setdefault("sleep", lambda s: None)
        return RealSession(allowed={"127.0.0.1:%d" % hub.port}, cafile=cafile or self.good, **kw)

    def sync(self, **kw):
        return HB.sync_catalog(self.ud, self.sess(**kw))

    def roms(self):
        return sorted(p.relative_to(c).as_posix() for c in (self.sd1, self.sd2) if (c / "Roms").is_dir()
                      for p in (c / "Roms").rglob("*") if p.is_file())


# ======================================================================================
# danh mục + giấy phép
# ======================================================================================
class TestCatalog(Base):
    def test_sync_keeps_only_clearly_licensed_and_paginates(self):
        r = self.sync()
        self.assertEqual(r["accepted"], 3)                               # alpha, bravo, charlie
        self.assertEqual(r["total_seen"], 7)
        self.assertEqual(r["pages"], 7)                                  # GB: 7 mục / 2 mỗi trang = 4; mỗi hệ rỗng vẫn tốn 1 trang
        out = HB.list_games(self.env, system="GB")
        self.assertEqual([e["slug"] for e in out["entries"]], ["alpha", "bravo", "charlie"])
        self.assertEqual({e["license_id"] for e in out["entries"]}, {"MIT", "CC-BY-4.0"})
        everything = HB.list_games(self.env, only="all")["entries"]
        for bad in ("nolic", "weird", "nodl", "ccnover"):
            self.assertNotIn(bad, [e["slug"] for e in everything], bad)

    def test_rejected_reason_is_shown_not_hidden_silently(self):
        self.sync()
        want = {"nolic": "no_license", "weird": "unrecognized_license", "nodl": "downloads_disabled", "ccnover": "unrecognized_license"}
        for slug, reason in want.items():
            r = HB.show_game(self.env, slug)
            self.assertFalse(r["listed"], slug)
            self.assertEqual(r["reason"], reason, slug)
        with self.assertRaises(LS.LibError) as cm:
            HB.show_game(self.env, "khong-co-that")
        self.assertEqual(cm.exception.kind, "not_found")

    def test_show_has_description_license_author_source(self):
        self.sync()
        r = HB.show_game(self.env, "alpha")
        self.assertTrue(r["listed"])
        self.assertEqual((r["license"], r["author"], r["description"]), ("MIT", "Dev alpha", "Mo ta cua Alpha Quest"))
        self.assertEqual(r["url"], self.hub.base + "/gbdev/database/master/entries/alpha/alpha.gb")

    def test_list_without_cache_says_sync(self):
        with self.assertRaises(LS.LibError) as cm:
            HB.list_games(self.env)
        self.assertEqual(cm.exception.kind, "not_found")
        self.assertIn("homebrew sync", cm.exception.message)

    def test_failed_sync_keeps_old_cache(self):
        self.sync()
        before = HB.catalog_path(self.ud).read_bytes()
        self.hub.api_fail = 99
        with self.assertRaises(LS.LibError):
            self.sync()
        self.assertEqual(HB.catalog_path(self.ud).read_bytes(), before)

    def test_search_5xx_retried_then_ok(self):
        self.hub.api_fail = 2
        self.assertEqual(self.sync()["accepted"], 3)

    def test_user_agent_sent_on_every_request(self):
        self.sync()
        self.assertTrue(self.hub.log)
        for r in self.hub.log:
            self.assertTrue((r["ua"] or "").startswith("LoongMate-homebrew/"), r)

    def test_requests_are_rate_limited_to_one_per_second(self):
        t = [0.0]
        s = HB.Session(allowed={"127.0.0.1:%d" % self.hub.port}, cafile=self.good, min_interval=1.0, clock=lambda: t[0],
                       sleep=lambda x: t.__setitem__(0, t[0] + x))
        HB.sync_catalog(self.ud, s)
        self.assertGreaterEqual(t[0], 6.0 - 1e-6)                         # 7 yêu cầu -> chờ ít nhất 6 giây giữa chúng

    def test_progress_reports_pages(self):
        evs = []
        HB.sync_catalog(self.ud, self.sess(), progress=evs.append)
        self.assertTrue(evs and all(e["phase"] == "catalog" and e["total"] >= e["done"] >= 1 for e in evs))


# ======================================================================================
# TLS: kiểm tra chứng thư đầy đủ
# ======================================================================================
class TestTls(Base):
    def test_context_is_fully_verifying(self):
        ctx = HB.make_context(self.good)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_no_ca_bundle_on_device_is_a_clear_error_not_a_downgrade(self):
        with mock.patch.object(HB, "find_cafile", lambda *a, **k: None), mock.patch.object(HB.os, "name", "posix"):
            with self.assertRaises(LS.LibError) as cm:
                HB.make_context(None)
        self.assertEqual(cm.exception.kind, "tls")
        self.assertIn("CA", cm.exception.message)

    def test_untrusted_certificate_blocked_before_any_http_byte(self):
        bad = self.start_hub(UNTRUSTED_CRT, UNTRUSTED_KEY)
        s = HB.Session(allowed={"127.0.0.1:%d" % bad.port}, cafile=self.good, min_interval=0, sleep=lambda x: None)
        with self.assertRaises(LS.LibError) as cm:
            s.get_bytes("https://127.0.0.1:%d/api/search?platform=GB&results=100&page=1" % bad.port, 1 << 20, retries=1)
        self.assertEqual(cm.exception.kind, "tls")
        self.assertEqual(bad.requests(), [])                              # máy chủ KHÔNG nhận được request HTTP nào

    def test_wrong_hostname_blocked(self):
        bad = self.start_hub(WRONGNAME_CRT, WRONGNAME_KEY)
        s = HB.Session(allowed={"127.0.0.1:%d" % bad.port}, cafile=write_pem(self.tmp, "wrong.crt", WRONGNAME_CRT), min_interval=0,
                       sleep=lambda x: None)
        with self.assertRaises(LS.LibError) as cm:
            s.get_bytes("https://127.0.0.1:%d/api/search?platform=GB&results=100&page=1" % bad.port, 1 << 20, retries=1)
        self.assertEqual(cm.exception.kind, "tls")
        self.assertEqual(bad.requests(), [])

    def test_untrusted_cert_never_installs_anything_through_fetch(self):
        self.sync()                                                       # danh mục lấy từ máy chủ tốt
        bad = self.start_hub(UNTRUSTED_CRT, UNTRUSTED_KEY)
        bad.files = dict(self.hub.files)
        self.sync_url_swap(bad)
        s = HB.Session(allowed={"127.0.0.1:%d" % bad.port}, cafile=self.good, min_interval=0, sleep=lambda x: None)
        with self.assertRaises(LS.LibError) as cm:
            HB.fetch(self.env, ["alpha"], session=s)
        self.assertEqual(cm.exception.kind, "tls")
        self.assertEqual(self.roms(), [])
        self.assertEqual(bad.requests(), [])

    def sync_url_swap(self, bad):
        """Đổi url trong cache sang máy chủ xấu (mô phỏng: DNS/đường truyền bị đánh tráo sau khi lấy danh mục)."""
        p = HB.catalog_path(self.ud)
        d = json.loads(p.read_text(encoding="utf-8"))
        for c in d["entries"]:
            c["url"] = c["url"].replace(self.hub.base, bad.base)
        p.write_text(json.dumps(d), encoding="utf-8")


# ======================================================================================
# host trắng + redirect
# ======================================================================================
class TestWhitelist(Base):
    def test_host_outside_whitelist_blocked_without_touching_it(self):
        other = self.start_hub(GOOD_CRT, GOOD_KEY)                         # chứng thư HỢP LỆ: chỉ danh sách trắng chặn được nó
        s = self.sess()
        for url in ("https://localhost:%d/x" % self.hub.port,                # tên thay vì IP: không có trong tập cho phép
                    "https://127.0.0.1:%d/x" % other.port,                  # đúng IP, SAI cổng
                    "http://127.0.0.1:%d/x" % self.hub.port,                # không phải https
                    "https://127.0.0.1:%d@evil.example/x" % self.hub.port,  # mẹo userinfo: máy thật là evil.example
                    "https://evil.example/x"):
            with self.assertRaises(LS.LibError, msg=url) as cm:
                s.get_bytes(url, 1000, retries=1)
            self.assertEqual(cm.exception.kind, "invalid", url)
        self.assertEqual(other.log, [])
        self.assertEqual(self.hub.log, [])

    def test_production_whitelist_is_exactly_two_hosts_on_443(self):
        s = HB.Session()
        self.assertEqual(s.allowed, {"hh3.gbdev.io", "raw.githubusercontent.com"})
        s.check_url("https://raw.githubusercontent.com/gbdev/database/master/x.gb")
        s.check_url("https://hh3.gbdev.io/api/search?platform=GB")
        for bad in ("https://raw.githubusercontent.com.evil.example/x", "https://github.com/x", "https://hh3.gbdev.io:8443/x",
                    "http://hh3.gbdev.io/x", "https://user@hh3.gbdev.io/x"):
            with self.assertRaises(LS.LibError, msg=bad):
                s.check_url(bad)

    def test_redirect_inside_whitelist_is_followed(self):
        self.hub.redirects["/moved"] = "/gbdev/database/master/entries/alpha/alpha.gb"
        self.assertEqual(self.sess().get_bytes(self.hub.base + "/moved", 1 << 20), GB_A)

    def test_redirect_to_foreign_host_blocked_and_foreign_host_untouched(self):
        evil = self.start_hub(GOOD_CRT, GOOD_KEY)                          # chứng thư hợp lệ + có file: chỉ whitelist chặn
        evil.files = {"alpha/alpha.gb": b"MALWARE" * 100}
        self.hub.redirects["/moved"] = "https://127.0.0.1:%d/gbdev/database/master/entries/alpha/alpha.gb" % evil.port
        with self.assertRaises(LS.LibError) as cm:
            self.sess().get_bytes(self.hub.base + "/moved", 1 << 20, retries=1)
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertEqual(evil.log, [])                                      # không hề nối tới host lạ
        self.assertEqual(len(self.hub.requests("/moved")), 1)

    def test_redirect_to_http_or_other_scheme_blocked(self):
        for loc in ("http://127.0.0.1:%d/x" % self.hub.port, "file:///etc/passwd", "ftp://localhost/x"):
            self.hub.redirects["/moved"] = loc
            with self.assertRaises(LS.LibError, msg=loc) as cm:
                self.sess().get_bytes(self.hub.base + "/moved", 1000, retries=1)
            self.assertEqual(cm.exception.kind, "invalid")

    def test_redirect_loop_stops(self):
        self.hub.redirects["/a"] = "/b"
        self.hub.redirects["/b"] = "/a"
        with self.assertRaises(LS.LibError) as cm:
            self.sess().get_bytes(self.hub.base + "/a", 1000, retries=1)
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertLessEqual(len(self.hub.log), HB.MAX_REDIRECTS + 1)

    def test_fetch_whose_file_redirects_to_foreign_host_installs_nothing(self):
        evil = self.start_hub(GOOD_CRT, GOOD_KEY)
        evil.files = {"alpha/alpha.gb": b"MALWARE" * 100}
        self.sync()
        self.hub.redirects["/gbdev/database/master/entries/alpha/alpha.gb"] = \
            "https://127.0.0.1:%d/gbdev/database/master/entries/alpha/alpha.gb" % evil.port
        with self.assertRaises(LS.LibError) as cm:
            HB.fetch(self.env, ["alpha"], session=self.sess())
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertEqual(self.roms(), [])
        self.assertEqual(evil.log, [])


# ======================================================================================
# tải + hash + cài
# ======================================================================================
class TestFetch(Base):
    def setUp(self):
        super().setUp()
        self.sync()

    def jobs(self):
        return LS.list_jobs(self.env)["jobs"]

    def test_fetch_installs_through_importer_and_records_license_outside_roms(self):
        before = len(self.hub.log)
        r = HB.fetch(self.env, ["alpha"], session=self.sess())
        self.assertTrue(r["success"], r)
        self.assertEqual((r["installed"], r["failed"], r["blocked"]), (1, 0, 0))
        dest = self.sd2 / "Roms" / "GB" / "Alpha Quest.gb"                  # thẻ phụ trước; đúng thư mục hệ của Leaf
        self.assertEqual(dest.read_bytes(), GB_A)
        self.assertEqual(self.roms(), ["Roms/GB/Alpha Quest.gb"])   # KHÔNG có .txt trong Roms/
        lic = (self.ud / "homebrew-licenses" / "alpha.txt").read_text(encoding="utf-8")
        self.assertIn("MIT", lic)
        self.assertIn(sha256(GB_A), lic)
        self.assertIn("Dev alpha", lic)
        self.assertEqual(self.jobs(), [])                                   # job dọn sạch
        self.assertGreater(len(self.hub.log), before)

    def test_catalog_hash_ok_installs_two_games(self):
        r = HB.fetch(self.env, ["alpha", "bravo"], session=self.sess())
        self.assertTrue(r["success"], r)
        self.assertEqual((self.sd2 / "Roms" / "GB" / "Bravo - The Game.gb").read_bytes(), GB_B)   # ':' -> ' -' cho FAT32
        self.assertTrue((self.ud / "homebrew-licenses" / "bravo.txt").is_file())

    def test_wrong_catalog_hash_is_not_installed(self):
        r = HB.fetch(self.env, ["charlie"], session=self.sess())
        self.assertFalse(r["success"])
        self.assertEqual(r["error_kind"], "hash")
        self.assertEqual(self.roms(), [])
        self.assertFalse((self.ud / "homebrew-licenses" / "charlie.txt").exists())
        self.assertEqual(self.jobs(), [])                                    # dữ liệu không đáng tin: dọn cả job
        self.assertEqual(r.get("installed", 0), 0)

    def test_one_bad_hash_blocks_the_whole_batch(self):
        r = HB.fetch(self.env, ["alpha", "charlie"], session=self.sess())
        self.assertFalse(r["success"])
        self.assertEqual(self.roms(), [])

    def test_prepare_refuses_rejected_or_unknown_slug_without_network(self):
        n = len(self.hub.log)
        for slug in ("nolic", "weird", "nodl", "ccnover", "khong-co"):
            with self.assertRaises(LS.LibError, msg=slug) as cm:
                HB.prepare(self.env, [slug], session=self.sess())
            self.assertEqual(cm.exception.kind, "invalid")
        self.assertEqual(len(self.hub.log), n)
        with self.assertRaises(LS.LibError):
            HB.prepare(self.env, ["alpha", "alpha"], session=self.sess())

    def test_prepare_reports_sizes_and_touches_nothing(self):
        pl = HB.prepare(self.env, ["alpha", "bravo"], session=self.sess())
        self.assertTrue(pl["ok"], pl)
        self.assertEqual({f["path"]: f["size"] for f in pl["files"]}, {"alpha": len(GB_A), "bravo": len(GB_B)})
        self.assertEqual(pl["card"]["selected"], "secondary")
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.roms(), [])
        self.assertFalse((self.sd2 / ".loongmate-import").exists())

    def test_oversize_file_is_a_problem_not_a_download(self):
        with mock.patch.object(HB, "MAX_ROM_BYTES", 1000):
            pl = HB.prepare(self.env, ["alpha"], session=self.sess())
        self.assertFalse(pl["ok"])
        self.assertTrue(pl["problems"])
        r = None
        with mock.patch.object(HB, "MAX_ROM_BYTES", 1000):
            r = HB.fetch(self.env, ["alpha"], session=self.sess())
        self.assertFalse(r["success"])
        self.assertEqual(self.roms(), [])
        self.assertEqual([x for x in self.hub.requests("/gbdev") if not x["range"]], [])   # không tải thân file nào

    def test_missing_file_on_server_is_reported_per_game(self):
        del self.hub.files["alpha/alpha.gb"]
        pl = HB.prepare(self.env, ["alpha"], session=self.sess())
        self.assertFalse(pl["ok"])
        self.assertIn("404", pl["problems"][0]["reason"])

    def test_already_installed_is_marked_present(self):
        HB.fetch(self.env, ["alpha"], session=self.sess())
        out = HB.list_games(self.env, system="GB")["entries"]
        self.assertEqual({e["slug"]: e["present"] for e in out}, {"alpha": True, "bravo": False, "charlie": False})

    def test_card_choice_never_silently_falls_back_to_primary(self):
        shutil.rmtree(self.sd2)                                              # thẻ phụ không gắn
        r = HB.fetch(self.env, ["alpha"], session=self.sess())
        self.assertFalse(r["success"])
        self.assertFalse(r["started"])
        self.assertTrue(r["needs_card_choice"])
        self.assertEqual(self.jobs(), [])
        r2 = HB.fetch(self.env, ["alpha"], card_pref="primary", session=self.sess())
        self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd1 / "Roms" / "GB" / "Alpha Quest.gb").read_bytes(), GB_A)

    def test_progress_events_reach_100_percent(self):
        evs = []
        HB.fetch(self.env, ["bravo"], progress=evs.append, session=self.sess())
        dl = [e for e in evs if e["phase"] == "download"]
        self.assertEqual(dl[-1]["done"], dl[-1]["total"])
        self.assertEqual(dl[-1]["total"], len(GB_B))
        self.assertEqual(evs[-1]["phase"], "install")


class TestResume(Base):
    def setUp(self):
        super().setUp()
        self.sync()

    def only_job(self):
        jobs = LS.list_jobs(self.env)["jobs"]
        self.assertEqual(len(jobs), 1, jobs)
        return jobs[0]

    def test_cut_connection_keeps_part_and_resume_uses_range_if_range(self):
        self.hub.cut["bravo/bravo.gb"] = 100_000
        r = HB.fetch(self.env, ["bravo"], session=self.sess())
        self.assertFalse(r["success"])
        self.assertTrue(r["resumable"])
        job = self.only_job()
        self.assertEqual(job["state"], "interrupted")
        parts = list((self.sd2 / ".loongmate-import" / "downloads").rglob("*.part"))
        self.assertEqual(len(parts), 1)
        have = parts[0].stat().st_size
        self.assertEqual(have, 100_000)
        self.assertEqual(self.roms(), [])
        n0 = len(self.hub.log)
        with mock.patch.object(HB, "Session", lambda *a, **k: self.sess()):  # đi đúng đường libsource.resume -> homebrew.download_file
            r2 = LS.resume(self.env, job["job_id"])
        self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd2 / "Roms" / "GB" / "Bravo - The Game.gb").read_bytes(), GB_B)
        rq = [x for x in self.hub.log[n0:] if x["path"].startswith("/gbdev")]
        self.assertEqual(len(rq), 1)
        self.assertEqual(rq[0]["range"], "bytes=%d-" % have)
        self.assertEqual(rq[0]["if_range"], '"%s"' % sha256(GB_B))
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_content_changed_on_server_restarts_instead_of_splicing(self):
        self.hub.cut["alpha/alpha.gb"] = 20_000
        r = HB.fetch(self.env, ["alpha"], session=self.sess())
        self.assertTrue(r["resumable"])
        job = self.only_job()
        new = rnd(len(GB_A), 99)                                             # cùng kích thước, nội dung khác -> ETag khác
        self.hub.files["alpha/alpha.gb"] = new
        with mock.patch.object(HB, "Session", lambda *a, **k: self.sess()):
            r2 = LS.resume(self.env, job["job_id"])
        self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd2 / "Roms" / "GB" / "Alpha Quest.gb").read_bytes(), new)   # KHÔNG phải GB_A[:20000] + new[20000:]

    def test_resume_hash_mismatch_after_resume_is_not_installed(self):
        self.hub.cut["charlie/charlie.gb"] = 5_000
        r = HB.fetch(self.env, ["charlie"], session=self.sess())
        self.assertTrue(r["resumable"])
        job = self.only_job()
        with mock.patch.object(HB, "Session", lambda *a, **k: self.sess()):
            r2 = LS.resume(self.env, job["job_id"])
        self.assertFalse(r2["success"])
        self.assertEqual(r2["error_kind"], "hash")
        self.assertEqual(self.roms(), [])

    def test_cancel_removes_part(self):
        self.hub.cut["alpha/alpha.gb"] = 1000
        HB.fetch(self.env, ["alpha"], session=self.sess())
        job = self.only_job()
        LS.cancel_job(self.env, job["job_id"])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])
        self.assertEqual(list((self.sd2 / ".loongmate-import").rglob("*.part")), [])

    def test_homebrew_jobs_are_marked_and_listed(self):
        self.hub.cut["alpha/alpha.gb"] = 1000
        HB.fetch(self.env, ["alpha"], session=self.sess())
        job = self.only_job()
        self.assertEqual((job["node"], job["root"]), ("homebrew", "hub"))
        self.assertEqual([j["job_id"] for j in HB.list_games(self.env)["jobs"]], [job["job_id"]])
        d = LS.find_job(self.env, job["job_id"])[1]
        self.assertEqual(LS._load_job(d)["source"], "homebrew")


# ======================================================================================
# CLI
# ======================================================================================
class TestCli(Base):
    def run_cli(self, *argv, stdin=""):
        base = ["--quiet", "--no-rescan", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--json"]
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
            code = CLI.main(base + list(argv))
        return code, json.loads(out.getvalue())

    def test_list_show_without_cache_then_with(self):
        code, r = self.run_cli("homebrew", "list")
        self.assertEqual(code, 1)
        self.assertEqual(r["error_kind"], "not_found")
        self.sync()
        code, r = self.run_cli("homebrew", "list")
        self.assertEqual((code, r["total"], [s["system"] for s in r["systems"]]), (0, 3, ["GB"]))
        code, r = self.run_cli("homebrew", "list", "--system", "gb", "--query", "bravo")
        self.assertEqual([e["slug"] for e in r["entries"]], ["bravo"])
        code, r = self.run_cli("homebrew", "show", "nolic")
        self.assertEqual((code, r["listed"], r["reason"]), (0, False, "no_license"))

    def test_fetch_needs_yes_in_non_interactive(self):
        self.sync()
        with mock.patch.object(HB, "Session", lambda *a, **k: self.sess()):
            code, r = self.run_cli("homebrew", "fetch", "alpha")
            self.assertNotEqual(code, 0)
            self.assertEqual(self.roms(), [])
            code, r = self.run_cli("homebrew", "fetch", "alpha", "--dry-run")
            self.assertEqual((code, r["ok"]), (0, True))
            self.assertEqual(self.roms(), [])
            code, r = self.run_cli("homebrew", "fetch", "alpha", "--yes")
            self.assertEqual((code, r["success"], r["installed"]), (0, True, 1))
        self.assertEqual((self.sd2 / "Roms" / "GB" / "Alpha Quest.gb").read_bytes(), GB_A)

    def test_sync_through_cli_writes_cache_only_in_app_data(self):
        with mock.patch.object(HB, "Session", lambda *a, **k: self.sess()):
            code, r = self.run_cli("homebrew", "sync")
        self.assertEqual((code, r["accepted"]), (0, 3))
        self.assertTrue(HB.catalog_path(self.ud).is_file())
        self.assertEqual(list(self.sd2.rglob("*")), [])
        self.assertEqual(self.roms(), [])


if __name__ == "__main__":
    unittest.main()
