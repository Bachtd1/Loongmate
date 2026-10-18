"""Tải từ web (engine/web.py): hành vi thật, không Internet.

  * parse_page / decode_body: HTML dựng tay -> kiểm link, form, bảng mã (không cần mạng).
  * SSRF: Session với bộ phân giải GIẢ (host -> địa chỉ) và allow_private=False: loopback, RFC1918, link-local, CGNAT/Tailscale, ::1,
    ::ffff:127.0.0.1 phải bị chặn; địa chỉ công cộng qua được. Bước chuyển hướng cũng đi qua cùng kiểm tra (xem TestRedirectHop).
  * Luồng mạng: máy chủ HTTPS loopback tự ký (chứng thư dùng chung với test_homebrew_device) + Session(allow_private=True);
    thẻ giả + importer thật rồi nhìn cây Roms/.

Chứng minh test biết đỏ: làm hỏng tạm (a) kiểm is_global trong engine/web.py, (b) từ chối HTML trong homebrew.download_file,
(c) mẫu `{q}` của form -> các test tương ứng phải đỏ (xem docs/TAI-TU-WEB.md mục "Chạy test").
"""
import contextlib
import hashlib
import http.server
import io
import json
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine.libsource as LS  # noqa: E402
import engine.homebrew as HB  # noqa: E402
import engine.web as WB  # noqa: E402
import engine.cli as CLI  # noqa: E402
from test_homebrew_device import GOOD_CRT, GOOD_KEY, rnd, write_pem  # noqa: E402  (chứng thư thử dùng chung)

RealSession = WB.Session
PAGE_URL = "https://www.example.com/dir/page.html"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def links_of(html, url=PAGE_URL):
    return WB.parse_page(html, url)["links"]


# ======================================================================================
# phân tích trang
# ======================================================================================
class TestParse(unittest.TestCase):
    def test_title_whitespace_and_fallback_to_host(self):
        self.assertEqual(WB.parse_page("<title>  Trang   chủ\n ROM </title>", PAGE_URL)["title"], "Trang chủ ROM")
        self.assertEqual(WB.parse_page("<p>khong tieu de</p>", PAGE_URL)["title"], "www.example.com")
        self.assertEqual(len(WB.parse_page("<title>%s</title>" % ("x" * 500), PAGE_URL)["title"]), 120)

    def test_relative_resolution_and_base_href(self):
        ls = links_of('<a href="a.zip">A</a><a href="/b/c.html">B</a><a href="//cdn.example.org/d.zip">D</a>')
        # file (a.zip, d.zip) đứng trước link trang (theo hợp đồng sắp xếp mới), mỗi nhóm giữ thứ tự tài liệu
        self.assertEqual([x["url"] for x in ls], ["https://www.example.com/dir/a.zip", "https://cdn.example.org/d.zip",
                                                  "https://www.example.com/b/c.html"])
        ls = links_of('<base href="https://files.example.net/x/"><a href="a.zip">A</a><a href="../b.zip">B</a>')
        self.assertEqual([x["url"] for x in ls], ["https://files.example.net/x/a.zip", "https://files.example.net/b.zip"])
        # <base> không phải https -> bỏ qua, vẫn dùng URL trang
        ls = links_of('<base href="http://evil.test/"><a href="a.zip">A</a>')
        self.assertEqual(ls[0]["url"], "https://www.example.com/dir/a.zip")

    def test_dedup_keeps_first_position_and_takes_later_text(self):
        ls = links_of('<a href="/x">X</a><a href="/a"></a><a href="/a#frag">Hello</a><a href="/x">again</a>')
        self.assertEqual([(x["url"].rsplit("/", 1)[1], x["text"]) for x in ls], [("x", "X"), ("a", "Hello")])

    def test_text_fallbacks(self):
        ls = links_of('<a href="/f/My%20Game.gba"></a><a href="/t" title="Tiêu đề"></a><a href="/i"><img alt="Ảnh bìa"></a><a href="/"></a>')
        self.assertEqual([x["text"] for x in ls], ["My Game.gba", "Tiêu đề", "Ảnh bìa", "https://www.example.com/"])
        ls = links_of('<a href="/a">x\x00y\x07z   \n w</a>')
        self.assertEqual(ls[0]["text"], "x y z w")
        self.assertEqual(len(links_of('<a href="/a">%s</a>' % ("n" * 500))[0]["text"]), 120)

    def test_dropped_schemes_and_self_link(self):
        html = ('<a href="javascript:void(0)">j</a><a href="JavaScript:x">j</a><a href="mailto:a@b.c">m</a><a href="tel:123">t</a>'
                '<a href="data:text/html,hi">d</a><a href="">e</a><a href="#top">frag</a><a href="page.html#sec">self</a>'
                '<a href="ftp://x/y">ftp</a><a href="/ok">ok</a>')
        self.assertEqual([x["url"] for x in links_of(html)], ["https://www.example.com/ok"])

    def test_http_links_dropped_and_counted(self):
        r = WB.parse_page('<a href="http://a.test/x.zip">A</a><a href="http://a.test/x.zip">A2</a><a href="http://b.test/">B</a>'
                          '<a href="https://c.test/">C</a>', PAGE_URL)
        self.assertEqual([x["url"] for x in r["links"]], ["https://c.test/"])
        self.assertEqual(r["dropped_insecure"], 2)             # 2 địa chỉ khác nhau, không phải 3 lần xuất hiện

    def test_file_flag_by_extension_and_download_attribute(self):
        ls = {x["url"].rsplit("/", 1)[1]: x for x in links_of(
            '<a href="a.zip">1</a><a href="b.GBA">2</a><a href="c.7z">3</a><a href="d.rar">4</a><a href="e.html">5</a>'
            '<a href="f.php?id=3" download>6</a><a href="g.nes?x=1">7</a><a href="h">8</a><a href="i.EXE">9</a>')}
        self.assertEqual({k: v["file"] for k, v in ls.items()},
                         {"a.zip": True, "b.GBA": True, "c.7z": True, "d.rar": True, "e.html": False, "f.php?id=3": True,
                          "g.nes?x=1": True, "h": False, "i.EXE": False})
        self.assertEqual((ls["a.zip"]["ext"], ls["b.GBA"]["ext"], ls["g.nes?x=1"]["ext"]), (".zip", ".gba", ".nes"))
        self.assertEqual((ls["e.html"]["ext"], ls["f.php?id=3"]["ext"]), ("", ""))

    def test_rom_extensions_come_from_importer_catalog(self):
        cat = LS._importer().load_catalog()["ext"]
        self.assertIn(".gba", cat)
        for ext in list(cat)[:12]:
            self.assertTrue(links_of('<a href="/x%s">x</a>' % ext)[0]["file"], ext)

    def test_same_site(self):
        ls = {x["url"]: x["same_site"] for x in links_of(
            '<a href="https://example.com/a">1</a><a href="https://dl.example.com/b">2</a><a href="https://other.org/c">3</a>'
            '<a href="https://notexample.com/d">4</a><a href="https://www.example.com/e">5</a>')}
        self.assertEqual(list(ls.values()), [True, True, False, False, True])
        # trang ở subdomain, link lên tên gốc
        ls = links_of('<a href="https://example.com/a">1</a>', "https://files.example.com/")
        self.assertTrue(ls[0]["same_site"])

    def test_get_form_template_with_hidden_and_placeholder(self):
        r = WB.parse_page('<form action="/search?old=1&x=2" method="GET"><input type="hidden" name="cat" value="roms">'
                          '<input type="hidden" name="a b" value="x y"><input type="search" name="q" placeholder="Tìm game">'
                          '<input type="submit" value="Go"></form>'
                          '<form><input name="s" aria-label="Từ khoá"></form>'
                          '<form action="https://other.test/f"><input type="text" name="k"></form>', PAGE_URL)
        self.assertEqual(r["forms"], [
            {"label": "Tìm game", "template": "https://www.example.com/search?cat=roms&a+b=x+y&q={q}"},
            {"label": "Từ khoá", "template": "https://www.example.com/dir/page.html?s={q}"},
            {"label": "Tìm kiếm", "template": "https://other.test/f?k={q}"}])

    def test_unsuitable_forms_ignored(self):
        r = WB.parse_page(
            '<form method="post" action="/s"><input name="q"></form>'
            '<form><input name="a"><input name="b"></form>'
            '<form><input name="a"><input type="password" name="p"></form>'
            '<form><input name="a"><input type="file" name="f"></form>'
            '<form><input type="hidden" name="h" value="1"></form>'
            '<form action="http://x.test/s"><input name="q"></form>'
            '<form><input name=""></form>', PAGE_URL)
        self.assertEqual(r["forms"], [])

    def test_form_cap_and_script_text_ignored(self):
        r = WB.parse_page("".join('<form action="/s%d"><input name="q"></form>' % i for i in range(9)), PAGE_URL)
        self.assertEqual(len(r["forms"]), WB.MAX_FORMS)
        r = WB.parse_page('<a href="/a">A<script>var x = "</a>";</script>B</a>', PAGE_URL)
        self.assertEqual(r["links"][0]["text"], "AB")

    def test_max_links_truncation(self):
        n = WB.MAX_LINKS + 50
        r = WB.parse_page("".join('<a href="/p%d">p%d</a>' % (i, i) for i in range(n)), PAGE_URL)
        self.assertEqual((len(r["links"]), r["truncated"]), (WB.MAX_LINKS, True))
        r = WB.parse_page("".join('<a href="/p%d">p%d</a>' % (i, i) for i in range(WB.MAX_LINKS)), PAGE_URL)
        self.assertEqual((len(r["links"]), r["truncated"]), (WB.MAX_LINKS, False))

    def test_hostile_markup_does_not_crash(self):
        for html in ('<a href="https://[::1">x</a>', "<a href='/a'<b>", "<form><input", "<title><a href=/x>", "<<<>>>&&&;;", "\x00" * 50):
            WB.parse_page(html, PAGE_URL)


class TestZonesAndJunk(unittest.TestCase):
    """Heuristic chung: vùng điều hướng, link rác, gợi ý "tải", nút tải chết (JS). Không có mã riêng cho trang nào."""

    @staticmethod
    def zones(html):
        return {x["text"]: x["zone"] for x in links_of(html)}

    def test_nav_header_footer_aside_tags(self):
        z = self.zones('<header><a href="/1">h</a></header><nav><a href="/2">n</a></nav><footer><a href="/3">f</a></footer>'
                       '<aside><a href="/4">a</a></aside><main><a href="/5">m</a></main>')
        self.assertEqual(z, {"h": "nav", "n": "nav", "f": "nav", "a": "nav", "m": "main"})

    def test_role_and_class_id_tokens(self):
        z = self.zones('<div role="navigation"><a href="/1">r1</a></div><div role="Banner"><a href="/2">r2</a></div>'
                       '<div role="contentinfo"><a href="/3">r3</a></div><div role="complementary"><a href="/4">r4</a></div>'
                       '<div class="top-Menu_bar x"><a href="/5">c1</a></div><ul id="breadcrumbs"><li><a href="/6">c2</a></li></ul>'
                       '<div class="widget"><a href="/7">c3</a></div><div class="share-buttons"><a href="/8">c4</a></div>'
                       '<div class="content"><a href="/9">m1</a></div><div class="menus"><a href="/10">m2</a></div>'
                       '<div role="main"><a href="/11">m3</a></div>')
        self.assertEqual(z, {"r1": "nav", "r2": "nav", "r3": "nav", "r4": "nav", "c1": "nav", "c2": "nav", "c3": "nav", "c4": "nav",
                             "m1": "main", "m2": "main", "m3": "main"})

    def test_file_links_are_always_main(self):
        ls = {x["text"]: x for x in links_of('<footer><a href="/g.zip">z</a><a href="/p" download>d</a><a href="/q">q</a></footer>')}
        self.assertEqual((ls["z"]["zone"], ls["d"]["zone"], ls["q"]["zone"]), ("main", "main", "nav"))

    def test_pagination_inside_nav_is_main(self):
        z = self.zones('<nav class="pagination"><a href="/p2">next</a></nav><nav><a href="/m">menu</a>'
                       '<div class="pager"><a href="/p3">more</a></div><a href="/x" class="page-link">pl</a></nav>'
                       '<div id="page" class="site"><header><a href="/h">hdr</a></header><div class="paging"><a href="/n">nn</a></div></div>')
        self.assertEqual(z, {"next": "main", "menu": "nav", "more": "main", "pl": "main", "hdr": "nav", "nn": "main"})

    def test_malformed_html_does_not_leak_zone(self):
        html = ('<div><nav><ul><li><a href="/n1">n1</a><li><a href="/n2">n2</a><div class="menu"><span>x</nav>'
                '</span></p></li></b><div><a href="/m1">m1</a></div><p><a href="/m2">m2</a>')
        self.assertEqual(self.zones(html), {"n1": "nav", "n2": "nav", "m1": "main", "m2": "main"})
        # thẻ kết thúc lạc không đóng nhầm thẻ khác: </span> không làm mất vùng nav của <a> đang mở trong <nav>
        self.assertEqual(self.zones('<nav></span></div><a href="/a">a</a></nav><a href="/b">b</a>'), {"a": "nav", "b": "main"})

    def test_junk_by_rel_sponsored_and_hosts_with_count(self):
        r = WB.parse_page(
            '<a href="/ok">ok</a><a href="https://other.org/ad" rel="nofollow Sponsored">ad</a>'
            '<a href="https://www.facebook.com/share">fb</a><a href="https://m.facebook.com/x">mfb</a>'
            '<a href="https://t.co/abc">t</a><a href="https://sub.ads.doubleclick.net/c">dc</a>'
            '<a href="https://notfacebook.com/">keep</a><a href="https://other.org/ok" rel="nofollow">keep2</a>'
            '<a href="https://www.facebook.com/share">dup</a>', PAGE_URL)
        self.assertEqual([x["text"] for x in r["links"]], ["ok", "keep", "keep2"])
        self.assertEqual(r["hidden_junk"], 5)                      # 5 địa chỉ khác nhau (trùng không đếm hai lần)
        self.assertEqual(WB.parse_page('<a href="/a">a</a>', PAGE_URL)["hidden_junk"], 0)

    def test_same_site_link_on_junk_host_is_kept(self):
        ls = links_of('<a href="https://facebook.com/page/a">own</a><a href="https://www.facebook.com/b">own2</a>',
                      "https://www.facebook.com/home")
        self.assertEqual([x["text"] for x in ls], ["own", "own2"])
        r = WB.parse_page('<a href="https://x.com/s">s</a>', "https://x.com/")
        self.assertEqual((len(r["links"]), r["hidden_junk"]), (1, 0))

    def test_sponsored_only_junk_when_every_occurrence_is(self):
        r = WB.parse_page('<a href="/a" rel="sponsored">a</a><a href="/a">a2</a><a href="/b" rel="sponsored">b</a><a href="/b" rel="sponsored">b</a>',
                          PAGE_URL)
        self.assertEqual(([x["text"] for x in r["links"]], r["hidden_junk"]), (["a"], 1))

    def test_truncation_happens_after_junk_removal(self):
        html = "".join('<a href="https://facebook.com/%d">j</a>' % i for i in range(WB.MAX_LINKS + 10)) + \
               "".join('<a href="/p%d">p</a>' % i for i in range(WB.MAX_LINKS))
        r = WB.parse_page(html, PAGE_URL)
        self.assertEqual((len(r["links"]), r["truncated"], r["hidden_junk"]), (WB.MAX_LINKS, False, WB.MAX_LINKS + 10))

    def test_download_hint_and_ordering(self):
        r = WB.parse_page('<a href="/a">Home</a><a href="/b">Tải về ngay</a><a href="/c.zip">Plain</a><a href="/d" title="Mirror 2">x</a>'
                          '<a href="/e">TAI XUONG</a><a href="/f">Downloader</a><a href="/g.gba">Download</a><a href="/h">Download now</a>'
                          '<a href="/i.nes">Second file</a>', PAGE_URL)
        got = [(x["text"], x["file"], x["download_hint"]) for x in r["links"]]
        self.assertEqual(got, [("Plain", True, False), ("Download", True, False), ("Second file", True, False),
                               ("Tải về ngay", False, True), ("x", False, True), ("TAI XUONG", False, True), ("Download now", False, True),
                               ("Home", False, False), ("Downloader", False, False)])
        z = WB.parse_page('<a href="/a">tai ve</a><a href="/b">Get File</a>', PAGE_URL)["links"]
        self.assertEqual([x["download_hint"] for x in z], [True, True])

    def test_js_download_true_for_dead_controls(self):
        for html in ('<a href="#">Download</a>', '<a href="#dl">Tải về</a>', '<a href="javascript:go()">Download</a>', '<a>Download</a>',
                     '<a href="">Download</a>', '<a href="/real" onclick="count()">Download</a>', '<button>Download</button>',
                     '<input type="button" value="Tải xuống">', '<form method="post"><input type="submit" value="Download"></form>',
                     '<button onclick="x()">Get file</button><form><input name="q"><button>Download</button></form>'):
            self.assertTrue(WB.parse_page(html, PAGE_URL)["js_download"], html)

    def test_js_download_false_when_not_dead(self):
        for html in ('<a href="/game.zip">Download</a>', '<a href="#">Menu</a>', '<a href="/x">Download page</a>', "",
                     '<form><input name="q"><input type="submit" value="Download"></form>',        # form GET tìm kiếm ta trả về
                     '<button>Search</button>', '<script>var a="Download"</script><a href="/g.gba">Get</a>'):
            self.assertFalse(WB.parse_page(html, PAGE_URL)["js_download"], html)


class TestDecode(unittest.TestCase):
    def test_meta_charset_windows_1258(self):
        text = "Việt Nam"                                  # "Việt" viết kiểu cp1258: ê dựng sẵn + dấu nặng rời
        html = ('<html><head><meta charset="windows-1258"><title>%s</title></head></html>' % text).encode("cp1258")
        self.assertIn(text, WB.decode_body(html, "text/html"))

    def test_http_equiv_meta_and_header_wins(self):
        html = '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"><p>café</p>'.encode("latin-1")
        self.assertIn("café", WB.decode_body(html, "text/html"))
        self.assertIn("café", WB.decode_body('<meta charset="utf-8"><p>café</p>'.encode("latin-1"), "text/html; charset=ISO-8859-1"))

    def test_default_utf8_unknown_codec_and_bad_bytes(self):
        self.assertIn("Việt", WB.decode_body("<p>Việt</p>".encode("utf-8"), "text/html"))
        self.assertIn("Việt", WB.decode_body('<meta charset="no-such-codec"><p>Việt</p>'.encode("utf-8"), ""))
        self.assertIn("�", WB.decode_body(b"<p>\xff\xfe</p>", "text/html; charset=utf-8"))

    def test_meta_only_sniffed_in_first_4k(self):
        late = b"<p>" + b"x" * 5000 + b'</p><meta charset="windows-1258">' + "Việt".encode("cp1258")
        self.assertNotIn("Việt", WB.decode_body(late, ""))


# ======================================================================================
# SSRF
# ======================================================================================
def fake_resolver(table):
    def resolve(host, port, type=0, **kw):
        if host not in table:
            raise socket.gaierror(-2, "Name or service not known")
        out = []
        for ip in (table[host] if isinstance(table[host], list) else [table[host]]):
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            out.append((fam, socket.SOCK_STREAM, 6, "", (ip, port) if fam == socket.AF_INET else (ip, port, 0, 0)))
        return out
    return resolve


class TestSsrf(unittest.TestCase):
    PRIVATE = {"loop": "127.0.0.1", "rfc10": "10.0.0.5", "rfc192": "192.168.1.2", "rfc172": "172.16.0.9", "cgnat": "100.100.1.1",
               "linklocal": "169.254.1.1", "v6loop": "::1", "mapped": "::ffff:127.0.0.1", "zero": "0.0.0.0", "v6ula": "fd00::1",
               "v6ll": "fe80::1", "multi": "224.0.0.1"}

    def sess(self, table):
        return RealSession(resolver=fake_resolver(table), context=object())

    def test_private_addresses_rejected_by_name(self):
        s = self.sess({k + ".test": v for k, v in self.PRIVATE.items()})
        for k, ip in self.PRIVATE.items():
            with self.assertRaises(LS.LibError, msg=ip) as cm:
                s.check_url("https://%s.test/x" % k)
            self.assertEqual(cm.exception.kind, "invalid", ip)
            self.assertIn("mạng nội bộ", cm.exception.message, ip)

    def test_literal_ip_hosts_rejected_without_resolver(self):
        s = self.sess({})
        for u in ("https://127.0.0.1/", "https://10.0.0.5:8443/a", "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://169.254.169.254/latest",
                  "https://100.64.0.1/"):
            with self.assertRaises(LS.LibError, msg=u) as cm:
                s.check_url(u)
            self.assertIn("mạng nội bộ", cm.exception.message, u)

    def test_one_bad_address_among_many_blocks(self):
        s = self.sess({"mix.test": ["93.184.216.34", "10.0.0.1"]})
        with self.assertRaises(LS.LibError):
            s.check_url("https://mix.test/")

    def test_public_address_passes(self):
        s = self.sess({"ok.test": "93.184.216.34", "ok6.test": "2606:2800:220:1:248:1893:25c8:1946"})
        self.assertEqual(s.check_url("https://ok.test/a/b?q=1"), ("ok.test", 443, "/a/b?q=1"))
        self.assertEqual(s.check_url("https://ok6.test:8443"), ("ok6.test", 8443, "/"))
        self.assertEqual(s.check_url("https://8.8.8.8/x")[0], "8.8.8.8")

    def test_http_and_userinfo_and_junk_rejected(self):
        s = self.sess({"ok.test": "93.184.216.34"})
        with self.assertRaises(LS.LibError) as cm:
            s.check_url("http://ok.test/")
        self.assertIn("https://", cm.exception.message)
        for u in ("https://user:pw@ok.test/", "https://user@ok.test/", "https://ok.test:99999/", "https:///x", "ftp://ok.test/", "file:///etc/passwd"):
            with self.assertRaises(LS.LibError, msg=u):
                s.check_url(u)

    def test_unresolvable_is_unreachable_not_crash(self):
        with self.assertRaises(LS.LibError) as cm:
            self.sess({}).check_url("https://nope.test/")
        self.assertEqual(cm.exception.kind, "unreachable")

    def test_allow_private_is_the_only_way_to_loopback(self):
        s = RealSession(resolver=fake_resolver({}), context=object(), allow_private=True)
        self.assertEqual(s.check_url("https://127.0.0.1:8443/x"), ("127.0.0.1", 8443, "/x"))
        with self.assertRaises(LS.LibError):
            s.check_url("http://127.0.0.1:8443/x")                  # nhưng vẫn chỉ https

    def test_default_session_blocks_loopback_even_for_open_url(self):
        with self.assertRaises(LS.LibError) as cm:
            WB.open_url(tempfile.gettempdir(), "https://127.0.0.1:9/")
        self.assertIn("mạng nội bộ", cm.exception.message)

    def test_normalize_url(self):
        self.assertEqual(WB.normalize_url("  example.com/a b "), "https://example.com/a%20b")
        self.assertEqual(WB.normalize_url("localhost:8080/x"), "https://localhost:8080/x")
        self.assertEqual(WB.normalize_url("http://x.test/"), "http://x.test/")        # để check_url từ chối rõ ràng, không tự nâng cấp
        for bad in ("", "   ", "javascript:alert(1)", "file:///etc/passwd", "ftp://x/y", "data:text/html,x", "mailto:a@b.c"):
            with self.assertRaises(LS.LibError, msg=bad):
                WB.normalize_url(bad)


# ======================================================================================
# tên file
# ======================================================================================
class TestNames(unittest.TestCase):
    def test_safe_filename(self):
        for raw, want in [("../../evil.gba", "evil.gba"), ("..\\..\\evil.gba", "evil.gba"), ("a/b/c.zip", "c.zip"), ("", "download.bin"),
                          ("..", "download.bin"), ("/", "download.bin"), ("con.gba", "_con.gba"), ("a:b?.gba", "a -b_.gba"),
                          ("x\x00y\x1f.nes", "xy.nes"), (None, "download.bin"), ("Tên Game.GBA", "Tên Game.GBA")]:
            self.assertEqual(WB.safe_filename(raw), want, raw)
        self.assertLessEqual(len(WB.safe_filename("n" * 300 + ".gba")), 110)

    def test_content_disposition(self):
        d = WB._disposition_name
        self.assertEqual(d("attachment; filename=\"a b.gba\""), "a b.gba")
        self.assertEqual(d("attachment; filename=plain.nes"), "plain.nes")
        self.assertEqual(d("attachment; filename=\"old.gba\"; filename*=UTF-8''T%C3%AAn%20Game.gba"), "Tên Game.gba")
        self.assertEqual(d("attachment; filename*=UTF-8''new.gba; filename=\"old.gba\""), "new.gba")
        self.assertEqual(d("attachment; filename*=iso-8859-1''caf%E9.gba"), "café.gba")
        self.assertIsNone(d("inline"))
        self.assertIsNone(d(None))


# ======================================================================================
# máy chủ HTTPS giả
# ======================================================================================
class _Srv(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeSite:
    """pages/files theo đường dẫn. files hỗ trợ ETag mạnh + Range + If-Range. Có thể ghi đè từng đường dẫn (override), cắt kết nối
    giữa chừng (cut) hoặc đổi sang HTML khi tải đầy đủ (html_on_full). Ghi nhật ký từng request."""

    def __init__(self, workdir: Path):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(write_pem(workdir, "wc-%d.pem" % id(self), GOOD_CRT), write_pem(workdir, "wk-%d.pem" % id(self), GOOD_KEY))
        self.pages = {}          # path -> (status, headers, body)
        self.files = {}          # path -> (bytes, extra headers)
        self.override = {}       # path -> (status, headers, body, send_content_length)
        self.cut = {}
        self.html_on_full = set()
        self.log = []
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                self.request.settimeout(10)
                self.request.do_handshake()
                super().setup()

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None, cl=True):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if cl:
                    self.send_header("Content-Length", str(len(body)))
                else:
                    self.close_connection = True
                self.end_headers()
                if body and self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                site.log.append({"path": u.path, "query": u.query, "range": self.headers.get("Range"), "if_range": self.headers.get("If-Range"),
                                 "ua": self.headers.get("User-Agent"), "accept": self.headers.get("Accept"), "referer": self.headers.get("Referer")})
                if u.path in site.override:
                    st, hd, body, cl = site.override[u.path]
                    return self._send(st, body, hd, cl)
                if u.path in site.html_on_full and not self.headers.get("Range"):
                    return self._send(200, b"<html>countdown</html>", {"Content-Type": "text/html"})
                if u.path in site.pages:
                    st, hd, body = site.pages[u.path]
                    return self._send(st, body, hd)
                if u.path in site.files:
                    return self._file(u.path)
                self._send(404, b"nope", {"Content-Type": "text/plain"})

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                cl = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(cl)
                site.log.append({"path": u.path, "query": u.query, "method": "POST", "body": body,
                                 "ua": self.headers.get("User-Agent"), "referer": self.headers.get("Referer")})
                if u.path in site.pages:
                    st, hd, resp_body = site.pages[u.path]
                    return self._send(st, resp_body, hd)
                self._send(404, b"nope", {"Content-Type": "text/plain"})

            def _file(self, path):
                data, extra = site.files[path]
                size, et = len(data), '"%s"' % sha256(data)
                rng, ifr = self.headers.get("Range"), self.headers.get("If-Range")
                start, end, status = 0, size - 1, 200
                if rng and (ifr is None or ifr == et):
                    a, _, b = rng.split("=", 1)[1].partition("-")
                    if not a.isdigit() or int(a) >= size:
                        return self._send(416, b"", {"Content-Range": "bytes */%d" % size})
                    start, end, status = int(a), min(int(b), size - 1) if b.isdigit() else size - 1, 206
                body = data[start:end + 1]
                hdr = {"ETag": et, "Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"}
                hdr.update(extra)
                if status == 206:
                    hdr["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
                self.send_response(status)
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = site.cut.get(path)
                if cut is not None and cut < len(body):
                    site.cut.pop(path, None)
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
                return site.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

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

    def html(self, path, body, ctype="text/html; charset=utf-8", status=200, **hdr):
        self.pages[path] = (status, {"Content-Type": ctype, **hdr}, body.encode("utf-8") if isinstance(body, str) else body)

    def requests(self, prefix=""):
        return [r for r in self.log if r["path"].startswith(prefix)]


GBA_DATA = rnd(70000, 11)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm-web-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir()
        self.env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.tmp / "secrets", proxy=None)
        self.good = write_pem(self.tmp, "good.crt", GOOD_CRT)
        self.site = FakeSite(self.tmp)
        self.addCleanup(self.site.stop)
        self.site.files["/dl/game.gba"] = (GBA_DATA, {})
        self.site.files["/tool.exe"] = (rnd(2000, 5), {})
        self.site.files["/pack.7z"] = (rnd(2000, 6), {})

    def sess(self, **kw):
        kw.setdefault("min_interval", 0)
        kw.setdefault("sleep", lambda s: None)
        return RealSession(cafile=self.good, allow_private=True, **kw)

    def url(self, path):
        return self.site.base + path

    def roms(self):
        return sorted(p.relative_to(c).as_posix() for c in (self.sd1, self.sd2) if (c / "Roms").is_dir()
                      for p in (c / "Roms").rglob("*") if p.is_file())

    def only_job(self):
        jobs = LS.list_jobs(self.env)["jobs"]
        self.assertEqual(len(jobs), 1, jobs)
        return jobs[0]

    def parts(self):
        return list((self.sd2 / ".loongmate-import").rglob("*.part"))


# ======================================================================================
# mở địa chỉ
# ======================================================================================
class TestOpen(Base):
    def test_html_page_lists_links_counts_and_records_history(self):
        self.site.html("/index.html", '<title>Kho ROM</title><a href="/dl/game.gba">Game</a><a href="/sub/">Mục</a>'
                       '<a href="https://other.example.org/x">Ngoài</a><a href="http://insecure.test/y.zip">http</a>'
                       '<form action="/s"><input name="q" placeholder="Tìm"></form>')
        r = WB.open_url(self.ud, self.url("/index.html"), self.sess())
        self.assertEqual((r["success"], r["kind"], r["title"], r["url"], r["host"]), (True, "page", "Kho ROM", self.url("/index.html"), "127.0.0.1"))
        self.assertEqual([x["text"] for x in r["links"]], ["Game", "Mục", "Ngoài"])
        self.assertEqual(r["counts"], {"files": 1, "pages": 1, "external": 1, "nav": 0})
        self.assertEqual(r["dropped_insecure"], 1)
        self.assertEqual(r["forms"], [{"label": "Tìm", "template": self.url("/s") + "?q={q}"}])
        self.assertFalse(r["truncated"])
        self.assertEqual([h["url"] for h in WB.load_state(self.ud)["history"]], [self.url("/index.html")])
        req = self.site.requests("/index.html")[0]
        self.assertEqual(req["ua"], WB.USER_AGENT)
        self.assertTrue(req["accept"].startswith("text/html"))

    def test_scheme_less_input_gets_https(self):
        self.site.html("/p", "<title>P</title>")
        r = WB.open_url(self.ud, "127.0.0.1:%d/p" % self.site.port, self.sess())
        self.assertEqual(r["title"], "P")

    def test_binary_is_reported_as_file_without_reading_body(self):
        self.site.files["/get"] = (GBA_DATA, {"Content-Disposition": "attachment; filename=\"old.gba\"; filename*=UTF-8''T%C3%AAn%20Game.gba"})
        r = WB.open_url(self.ud, self.url("/get"), self.sess())
        self.assertEqual((r["kind"], r["name"], r["size"], r["content_type"]), ("file", "Tên Game.gba", len(GBA_DATA), "application/octet-stream"))
        self.assertNotIn("links", r)
        self.assertEqual(WB.load_state(self.ud)["history"], [])           # file không vào lịch sử trang

    def test_traversal_in_disposition_is_sanitized(self):
        for cd in ('attachment; filename="../../evil.gba"', 'attachment; filename="..\\..\\evil.gba"', "attachment; filename*=UTF-8''..%2F..%2Fevil.gba"):
            self.site.files["/t"] = (b"x" * 10, {"Content-Disposition": cd})
            r = WB.open_url(self.ud, self.url("/t"), self.sess())
            self.assertEqual(r["name"], "evil.gba", cd)

    def test_name_from_url_when_no_disposition(self):
        r = WB.open_url(self.ud, self.url("/dl/game.gba"), self.sess())
        self.assertEqual((r["kind"], r["name"]), ("file", "game.gba"))
        self.site.files["/dl/My%20Game.gba"] = (b"abc", {})
        self.site.files["/dl/My Game.gba"] = (b"abc", {})
        self.assertEqual(WB.open_url(self.ud, self.url("/dl/My%20Game.gba"), self.sess())["name"], "My Game.gba")

    def test_redirects_followed_and_final_url_reported(self):
        self.site.pages["/a"] = (301, {"Location": "/b"}, b"")
        self.site.pages["/b"] = (302, {"Location": self.url("/c/index.html")}, b"")
        self.site.html("/c/index.html", '<title>Cuối</title><a href="x.zip">x</a>')
        r = WB.open_url(self.ud, self.url("/a"), self.sess())
        self.assertEqual((r["url"], r["title"]), (self.url("/c/index.html"), "Cuối"))
        self.assertEqual(r["links"][0]["url"], self.url("/c/x.zip"))      # link tương đối tính theo URL CUỐI
        self.assertEqual(WB.load_state(self.ud)["history"][0]["url"], self.url("/c/index.html"))

    def test_redirect_loop_is_stopped(self):
        self.site.pages["/loop"] = (302, {"Location": "/loop"}, b"")
        with self.assertRaises(LS.LibError) as cm:
            WB.open_url(self.ud, self.url("/loop"), self.sess())
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertEqual(len(self.site.requests("/loop")), WB.MAX_REDIRECTS + 1)

    def test_http_errors_and_page_overflow(self):
        with self.assertRaises(LS.LibError) as cm:
            WB.open_url(self.ud, self.url("/missing"), self.sess())
        self.assertEqual(cm.exception.kind, "not_found")
        self.site.html("/big", "<a href='/a'>a</a>" + "x" * (WB.MAX_PAGE_BYTES + 1000))
        r = WB.open_url(self.ud, self.url("/big"), self.sess())
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["links"]), 1)

    def test_untrusted_certificate_is_refused(self):
        s = RealSession(cafile=None, context=ssl.create_default_context(), allow_private=True, min_interval=0)
        with self.assertRaises(LS.LibError) as cm:
            WB.open_url(self.ud, self.url("/x"), s)
        self.assertEqual(cm.exception.kind, "tls")

    def test_history_write_failure_does_not_fail_open(self):
        self.site.html("/p", "<title>P</title>")
        with mock.patch.object(WB, "_record_history", side_effect=OSError("disk full")):
            self.assertTrue(WB.open_url(self.ud, self.url("/p"), self.sess())["success"])



class TestOpenCounts(Base):
    def test_counts_nav_pages_external_files_and_hidden(self):
        self.site.html("/c", '<nav><a href="/n1">n</a><a href="https://other.org/n2">no</a><a href="/f.gba">fnav</a></nav>'
                       '<a href="/p1">p1</a><a href="/p2">p2</a><a href="https://other.org/e">e</a><a href="/g.zip">g</a>'
                       '<a href="https://twitter.com/s">tw</a><a href="#">Download</a>')
        r = WB.open_url(self.ud, self.url("/c"), self.sess())
        self.assertEqual(r["counts"], {"files": 2, "pages": 2, "external": 1, "nav": 2})
        self.assertEqual((r["hidden_junk"], r["js_download"]), (1, True))
        self.assertEqual(len(r["links"]), 7)
        self.assertEqual({x["text"]: x["zone"] for x in r["links"]}["fnav"], "main")
        self.site.html("/d", '<a href="/g.zip">Download</a>')
        r = WB.open_url(self.ud, self.url("/d"), self.sess())
        self.assertEqual((r["js_download"], r["counts"]["files"], r["hidden_junk"]), (False, 1, 0))


class TestRedirectHop(Base):
    """Mỗi bước chuyển hướng phải qua kiểm tra địa chỉ nội bộ. Máy chủ thử nằm ở loopback nên Session thử cho PHÉP riêng 127.0.0.1
    (ghi đè _check_public) và để mọi host khác đi qua kiểm tra THẬT (logic is_global thật, bộ phân giải giả)."""

    def sess2(self):
        class HopSess(RealSession):
            def _check_public(s, host, port):
                if host == "127.0.0.1":
                    return
                super()._check_public(host, port)
        return HopSess(cafile=self.good, resolver=fake_resolver({"evil.test": "127.0.0.1", "lan.test": "192.168.1.5", "pub.test": "93.184.216.34"}),
                       min_interval=0, sleep=lambda s: None)

    def test_redirect_to_internal_targets_is_blocked_before_connecting(self):
        for i, loc in enumerate(("https://10.0.0.1/x", "https://evil.test:%d/x" % self.site.port, "https://lan.test/x", "https://[::1]/x",
                                 "https://100.100.1.1/x")):
            self.site.pages["/r%d" % i] = (302, {"Location": loc}, b"")
            with self.assertRaises(LS.LibError, msg=loc) as cm:
                WB.open_url(self.ud, self.url("/r%d" % i), self.sess2())
            self.assertEqual(cm.exception.kind, "invalid", loc)
            self.assertIn("mạng nội bộ", cm.exception.message, loc)
        self.assertEqual(self.site.requests("/x"), [])

    def test_redirect_to_http_is_blocked(self):
        self.site.pages["/h"] = (302, {"Location": "http://pub.test/x"}, b"")
        with self.assertRaises(LS.LibError) as cm:
            WB.open_url(self.ud, self.url("/h"), self.sess2())
        self.assertIn("https://", cm.exception.message)

    def test_redirect_to_public_name_passes_the_check(self):
        s = self.sess2()
        self.assertEqual(s.check_url("https://pub.test/x"), ("pub.test", 443, "/x"))     # qua kiểm tra (chưa nối vì tên giả)


# ======================================================================================
# chuẩn bị / tải
# ======================================================================================
class TestPrepare(Base):
    def test_good_file_ok_with_card_and_system(self):
        pl = WB.prepare(self.env, [self.url("/dl/game.gba")], session=self.sess())
        self.assertTrue(pl["ok"], pl)
        f = pl["files"][0]
        self.assertEqual((f["name"], f["system"], f["size"], f["problem"], f["present"], f["host"]), ("game.gba", "GBA", len(GBA_DATA), None, False, "127.0.0.1"))
        self.assertEqual((f["max_bytes"], f["path"], f["url"], f["sha256"], f["hash"]), (WB.libsource._importer().DEFAULT_LIMITS.max_file_bytes, self.url("/dl/game.gba"), self.url("/dl/game.gba"), None, None))
        self.assertTrue(f["etag"])
        self.assertEqual((pl["card"]["selected"], pl["node"], pl["root"]), ("secondary", "web", "web"))
        self.assertEqual(pl["needed_bytes"], 2 * len(GBA_DATA) + LS._importer().DEFAULT_LIMITS.reserve_bytes)
        self.assertEqual(self.roms(), [])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_html_target_is_a_problem(self):
        self.site.html("/ad", "<html>đếm ngược 10s</html>")
        pl = WB.prepare(self.env, [self.url("/ad")], session=self.sess())
        self.assertFalse(pl["ok"])
        self.assertIn("trang web, không phải file", pl["files"][0]["problem"])

    def test_oversize_by_content_range_total(self):
        self.site.override["/huge.gba"] = (206, {"Content-Range": "bytes 0-0/3221225472", "Content-Type": "application/octet-stream"}, b"x", True)
        pl = WB.prepare(self.env, [self.url("/huge.gba")], session=self.sess())
        self.assertFalse(pl["ok"])
        self.assertIn("2 GiB", pl["files"][0]["problem"])

    def test_executable_unsupported_archive_empty_and_unknown_size(self):
        self.site.override["/empty.gba"] = (416, {"Content-Range": "bytes */0"}, b"", True)
        self.site.override["/nosize.gba"] = (200, {"Content-Type": "application/octet-stream"}, b"abc", False)
        pl = WB.prepare(self.env, [self.url(p) for p in ("/tool.exe", "/pack.7z", "/empty.gba", "/nosize.gba")], session=self.sess())
        probs = {f["name"]: f["problem"] for f in pl["files"]}
        self.assertIn("bị chặn", probs["tool.exe"])
        self.assertIn(".7z chưa hỗ trợ giải nén", probs["pack.7z"])
        self.assertEqual(probs["empty.gba"], "file rỗng")
        self.assertIn("không báo kích thước", probs["nosize.gba"])
        self.assertEqual(len(pl["files"]), 4)                              # file có vấn đề vẫn được liệt kê
        self.assertFalse(pl["ok"])

    def test_redirect_to_real_file_uses_final_url_and_name(self):
        self.site.pages["/go"] = (302, {"Location": "/dl/game.gba"}, b"")
        pl = WB.prepare(self.env, [self.url("/go")], session=self.sess())
        f = pl["files"][0]
        self.assertEqual((f["url"], f["name"]), (self.url("/dl/game.gba"), "game.gba"))

    def test_duplicate_names_get_unique_local_names(self):
        self.site.files["/other/game.gba"] = (rnd(3000, 9), {})
        pl = WB.prepare(self.env, [self.url("/dl/game.gba"), self.url("/other/game.gba")], session=self.sess())
        names = [f["local"] for f in pl["files"]]
        self.assertEqual(len({n.lower() for n in names}), 2, names)

    def test_present_flag_and_empty_list_and_network_errors_raise(self):
        (self.sd2 / "Roms" / "GBA").mkdir(parents=True)
        (self.sd2 / "Roms" / "GBA" / "game.gba").write_bytes(b"x")
        self.assertTrue(WB.prepare(self.env, [self.url("/dl/game.gba")], session=self.sess())["files"][0]["present"])
        with self.assertRaises(LS.LibError):
            WB.prepare(self.env, [], session=self.sess())
        with self.assertRaises(LS.LibError) as cm:
            WB.prepare(self.env, [self.url("/dl/game.gba")], session=RealSession(allow_private=False, min_interval=0))
        self.assertIn("mạng nội bộ", cm.exception.message)

    def test_missing_file_404_raises(self):
        with self.assertRaises(LS.LibError) as cm:
            WB.prepare(self.env, [self.url("/nope.gba")], session=self.sess())
        self.assertEqual(cm.exception.kind, "not_found")


class TestFetch(Base):
    def test_end_to_end_installs_into_system_folder(self):
        seen = {}
        real_install = LS._install

        def spy(env, job_dir, job, conflict):
            seen["files"] = [dict(f) for f in job["files"]]
            seen["source"] = job.get("source")
            return real_install(env, job_dir, job, conflict)
        events = []
        with mock.patch.object(LS, "_install", spy):
            r = WB.fetch(self.env, [self.url("/dl/game.gba")], progress=events.append, session=self.sess())
        self.assertTrue(r["success"], r)
        self.assertEqual((r["installed"], r["started"], r["state"]), (1, True, "done"))
        self.assertEqual(self.roms(), ["Roms/GBA/game.gba"])
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "game.gba").read_bytes(), GBA_DATA)
        self.assertEqual(seen["source"], "web")
        self.assertEqual(seen["files"][0]["sha256"], sha256(GBA_DATA))
        self.assertEqual((seen["files"][0]["url"], seen["files"][0]["max_bytes"] > 0), (self.url("/dl/game.gba"), True))
        self.assertTrue(r["job_removed"])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])
        self.assertEqual(self.parts(), [])
        self.assertTrue(any(e["phase"] == "download" for e in events))

    def test_executable_blocks_the_whole_fetch_before_any_download(self):
        r = WB.fetch(self.env, [self.url("/dl/game.gba"), self.url("/tool.exe")], session=self.sess())
        self.assertFalse(r["success"])
        self.assertFalse(r["started"])
        self.assertEqual(self.roms(), [])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])
        self.assertEqual([x for x in self.site.requests("/dl/game.gba") if not x["range"]], [])       # chưa tải thân file nào

    def test_html_served_at_download_time_is_removed_and_not_installed(self):
        self.site.html_on_full.add("/dl/game.gba")         # lúc hỏi (Range 0-0) là file thật, lúc tải đầy đủ lại là trang đếm ngược
        r = WB.fetch(self.env, [self.url("/dl/game.gba")], session=self.sess())
        self.assertFalse(r["success"])
        self.assertEqual(r["error_kind"], "not_file")
        self.assertIn("HTML", r["message"])
        self.assertFalse(r["resumable"])                    # tải tiếp chỉ gặp lại trang đó: job bị dọn, không để rác "Tải dở"
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])
        self.assertEqual(self.parts(), [])
        self.assertEqual(self.roms(), [])
        self.assertEqual(list((self.sd2 / ".loongmate-import").rglob("game.gba")), [])

    def test_interrupted_download_resumes_with_range_and_if_range(self):
        self.site.cut["/dl/game.gba"] = 25_000
        r = WB.fetch(self.env, [self.url("/dl/game.gba")], session=self.sess())
        self.assertFalse(r["success"])
        self.assertTrue(r["resumable"])
        job = self.only_job()
        self.assertEqual((job["node"], job["root"]), ("web", "web"))
        d = LS.find_job(self.env, job["job_id"])[1]
        self.assertEqual(LS._load_job(d)["source"], "web")
        have = self.parts()[0].stat().st_size
        self.assertTrue(0 < have < len(GBA_DATA))
        n0 = len(self.site.log)
        r2 = LS.resume(self.env, job["job_id"], session=self.sess())
        self.assertTrue(r2["success"], r2)
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "game.gba").read_bytes(), GBA_DATA)
        rq = [x for x in self.site.log[n0:] if x["path"] == "/dl/game.gba"]
        self.assertEqual(len(rq), 1)
        self.assertEqual((rq[0]["range"], rq[0]["if_range"]), ("bytes=%d-" % have, '"%s"' % sha256(GBA_DATA)))
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_resume_without_session_uses_web_session_class(self):
        self.site.cut["/dl/game.gba"] = 25_000
        WB.fetch(self.env, [self.url("/dl/game.gba")], session=self.sess())
        job = self.only_job()
        with mock.patch.object(WB, "Session", lambda *a, **k: self.sess()):
            r = LS.resume(self.env, job["job_id"])
        self.assertTrue(r["success"], r)

    def test_server_growing_past_declared_max_is_refused(self):
        self.site.cut["/dl/game.gba"] = 1000
        WB.fetch(self.env, [self.url("/dl/game.gba")], session=self.sess())
        job = self.only_job()
        d = LS.find_job(self.env, job["job_id"])[1]
        j = LS._load_job(d)
        j["files"][0]["max_bytes"] = 1000                    # giới hạn của job nhỏ hơn thực tế -> Content-Length của bản 200 bị từ chối
        for p in self.parts():
            p.unlink()
        LS._save_job(d, j)
        r = LS.resume(self.env, job["job_id"], session=self.sess())
        self.assertFalse(r["success"])
        self.assertEqual(r["error_kind"], "invalid")
        self.assertEqual(self.roms(), [])

    def test_cancel_removes_part(self):
        self.site.cut["/dl/game.gba"] = 1000
        WB.fetch(self.env, [self.url("/dl/game.gba")], session=self.sess())
        LS.cancel_job(self.env, self.only_job()["job_id"])
        self.assertEqual((LS.list_jobs(self.env)["jobs"], self.parts()), ([], []))


# ======================================================================================
# lịch sử / dấu trang / hộp thư
# ======================================================================================
class TestPatchRouting(Base):
    """Patch ROM: .ips/.bps/.ups (hoặc zip chỉ chứa patch) tải từ web đi vào kho patch, KHÔNG vào Roms/ (engine/patcher.py)."""

    def setUp(self):
        super().setUp()
        from test_patcher import BPS_PATCH, IPS_NORMAL, make_zip
        self.bps, self.ips = BPS_PATCH, IPS_NORMAL
        zp = self.tmp / "ips.zip"
        make_zip(zp, [("readme.txt", b"hi"), ("hack/fix.ips", IPS_NORMAL)])
        self.site.files["/p/fix.bps"] = (BPS_PATCH, {})
        self.site.files["/p/pack.zip"] = (zp.read_bytes(), {})
        mixed = self.tmp / "mixed.zip"
        make_zip(mixed, [("fix2.ips", IPS_NORMAL), ("game.gba", GBA_DATA)])
        self.site.files["/p/mixed.zip"] = (mixed.read_bytes(), {})
        self.site.files["/p/bad.ips"] = (b"this is not a patch", {})

    def store_files(self):
        d = self.ud / "patches"
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def test_prepare_marks_patch_as_system_patch_without_problem(self):
        pl = WB.prepare(self.env, [self.url("/p/fix.bps"), self.url("/p/pack.zip")], session=self.sess())
        self.assertTrue(pl["ok"], pl)
        self.assertEqual([(f["name"], f["system"], f["problem"]) for f in pl["files"]], [("fix.bps", "PATCH", None), ("pack.zip", "?", None)])

    def test_patch_extensions_are_file_links_on_a_page(self):
        ls = {x["url"].rsplit("/", 1)[1]: x for x in links_of('<a href="a.ips">1</a><a href="b.BPS">2</a><a href="c.ups">3</a>')}
        self.assertEqual([(k, v["file"], v["ext"]) for k, v in ls.items()], [("a.ips", True, ".ips"), ("b.BPS", True, ".bps"), ("c.ups", True, ".ups")])

    def test_bps_and_zip_of_ips_land_in_patch_store_not_in_roms(self):
        seen = []
        real_install = LS._install
        with mock.patch.object(LS, "_install", lambda *a, **k: seen.append(1) or real_install(*a, **k)):
            r = WB.fetch(self.env, [self.url("/p/fix.bps"), self.url("/p/pack.zip")], session=self.sess())
        self.assertTrue(r["success"], r)
        self.assertEqual(sorted(r["patches"]), ["fix.bps", "fix.ips"])
        self.assertEqual(self.store_files(), ["fix.bps", "fix.ips"])
        self.assertEqual((self.ud / "patches" / "fix.bps").read_bytes(), self.bps)
        self.assertEqual(self.roms(), [])
        self.assertEqual(seen, [])                                   # không còn gì cho importer -> không gọi _install
        self.assertEqual((r["installed"], r["state"], r["job_removed"]), (0, "done", True))
        self.assertIn("Đã lưu 2 patch — mở Library → Patch ROM để áp dụng.", r["message"])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])
        self.assertEqual(self.parts(), [])
        self.assertEqual(list((self.sd2 / ".loongmate-import").rglob("*.bps")), [])      # job đã dọn

    def test_patch_plus_rom_installs_rom_and_stores_patch(self):
        r = WB.fetch(self.env, [self.url("/p/fix.bps"), self.url("/dl/game.gba")], session=self.sess())
        self.assertTrue(r["success"], r)
        self.assertEqual((r["installed"], r["patches"]), (1, ["fix.bps"]))
        self.assertEqual(self.roms(), ["Roms/GBA/game.gba"])
        self.assertEqual(self.store_files(), ["fix.bps"])
        self.assertIn("Đã lưu 1 patch", r["message"])
        self.assertTrue(r["changed"])

    def test_zip_with_a_rom_inside_is_not_routed_as_patch(self):
        r = WB.fetch(self.env, [self.url("/p/mixed.zip")], session=self.sess())
        self.assertEqual(r["patches"], [])
        self.assertEqual(self.store_files(), [])
        self.assertNotIn("Đã lưu", r["message"])
        self.assertEqual(self.roms(), ["Roms/GBA/game.gba"])

    def test_corrupt_patch_is_reported_failed_and_nothing_stored(self):
        r = WB.fetch(self.env, [self.url("/p/bad.ips")], session=self.sess())
        self.assertFalse(r["success"])
        self.assertEqual((r["patches"], r["failed"], r["state"]), ([], 1, "done"))
        self.assertIn("bad.ips", r["message"])
        self.assertEqual(self.store_files(), [])
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_fetching_same_patch_twice_reuses_stored_file(self):
        for _ in range(2):
            self.assertTrue(WB.fetch(self.env, [self.url("/p/fix.bps")], session=self.sess())["success"])
        self.assertEqual(self.store_files(), ["fix.bps"])


class TestState(Base):
    def test_history_dedup_newest_first_and_cap(self):
        for i in range(WB.HISTORY_CAP + 10):
            WB._record_history(self.ud, "https://h.test/%d" % i, "T%d" % i)
        WB._record_history(self.ud, "https://h.test/55", "again")
        h = WB.load_state(self.ud)["history"]
        self.assertEqual(len(h), WB.HISTORY_CAP)
        self.assertEqual((h[0]["url"], h[0]["title"]), ("https://h.test/55", "again"))
        self.assertEqual([x["url"] for x in h].count("https://h.test/55"), 1)
        self.assertEqual(h[1]["url"], "https://h.test/59")
        self.assertNotIn("https://h.test/0", [x["url"] for x in h])
        self.assertTrue(h[0]["at"].endswith("Z"))

    def test_bookmarks_add_update_remove_and_cap(self):
        r = WB.add_bookmark(self.ud, "example.com/roms", "Kho ROM")
        self.assertEqual([(b["url"], b["title"]) for b in r["bookmarks"]], [("https://example.com/roms", "Kho ROM")])
        WB.add_bookmark(self.ud, "https://example.com/roms", "Đổi tên")
        WB.add_bookmark(self.ud, "https://b.test/")
        bm = WB.load_state(self.ud)["bookmarks"]
        self.assertEqual([(b["url"], b["title"]) for b in bm], [("https://b.test/", "https://b.test/"), ("https://example.com/roms", "Đổi tên")])
        self.assertTrue(WB.remove_bookmark(self.ud, "https://b.test/")["removed"])
        self.assertFalse(WB.remove_bookmark(self.ud, "https://b.test/")["removed"])
        self.assertEqual(len(WB.load_state(self.ud)["bookmarks"]), 1)
        with self.assertRaises(LS.LibError):
            WB.add_bookmark(self.ud, "http://insecure.test/")
        for i in range(WB.BOOKMARK_CAP - 1):
            WB.add_bookmark(self.ud, "https://c.test/%d" % i)
        with self.assertRaises(LS.LibError):
            WB.add_bookmark(self.ud, "https://c.test/overflow")
        WB.add_bookmark(self.ud, "https://c.test/0", "đổi tên khi đã đầy vẫn được")
        self.assertEqual(len(WB.load_state(self.ud)["bookmarks"]), WB.BOOKMARK_CAP)

    def test_clear_history_keeps_bookmarks(self):
        WB._record_history(self.ud, "https://h.test/", "H")
        WB.add_bookmark(self.ud, "https://b.test/")
        WB.clear_history(self.ud)
        st = WB.load_state(self.ud)
        self.assertEqual((st["history"], len(st["bookmarks"])), ([], 1))

    def test_corrupt_or_odd_file_is_tolerated(self):
        p = self.ud / WB.WEB_FILE
        for junk in ("{not json", "[]", '{"history": "x", "bookmarks": [1, {"url": 3}, {"url": "https://ok.test/", "title": null}]}', "", "\x00\x01"):
            p.write_text(junk, encoding="utf-8")
            st = WB.load_state(self.ud)
            self.assertEqual(st["history"], [], junk)
            WB.add_bookmark(self.ud, "https://new.test/")                  # ghi đè file hỏng bằng file tốt
            self.assertEqual(WB.load_state(self.ud)["bookmarks"][0]["url"], "https://new.test/")
            p.unlink()
        self.assertEqual(WB.load_state(self.tmp / "khong-co"), {"history": [], "bookmarks": []})

    def test_inbox_parsing(self):
        (self.sd1 / "Import").mkdir()
        (self.sd2 / "Import").mkdir()
        (self.sd1 / "Import" / "links.txt").write_bytes(
            "﻿# danh sách\n\nhttps://a.test/x.zip\n  https://b.test/y  \nhttp://insecure.test/z\nftp://f.test/\nkhong phai url\n"
            "https://a.test/x.zip\nhttps://sp.test/a b.gba\n# https://commented.test/\nhttps:///nohost\n".encode("utf-8"))
        (self.sd2 / "Import" / "links.txt").write_bytes(b"https://b.test/y\nhttps://c.test/\n\xff\xfehttps://bad.test/")
        got = WB.inbox(self.env.cards)
        self.assertEqual([(x["url"], x["card"]) for x in got], [("https://a.test/x.zip", "primary"), ("https://b.test/y", "primary"),
                                                                ("https://sp.test/a%20b.gba", "primary"), ("https://c.test/", "secondary")])

    def test_inbox_missing_dirs_cap_and_size_limit(self):
        self.assertEqual(WB.inbox({"primary": self.sd1, "secondary": None}), [])
        (self.sd1 / "Import").mkdir()
        (self.sd1 / "Import" / "links.txt").write_text("\n".join("https://x.test/%d" % i for i in range(80)), encoding="utf-8")
        self.assertEqual(len(WB.inbox(self.env.cards)), WB.INBOX_CAP)
        big = ("# " + "x" * 100 + "\n") * 1000 + "https://late.test/\n"      # > 64 KiB: phần cuối không được đọc
        (self.sd1 / "Import" / "links.txt").write_text(big, encoding="utf-8")
        self.assertEqual(WB.inbox(self.env.cards), [])

    def test_home_aggregates_and_filters_jobs(self):
        WB._record_history(self.ud, "https://h.test/", "H")
        WB.add_bookmark(self.ud, "https://b.test/", "B")
        (self.sd1 / "Import").mkdir()
        (self.sd1 / "Import" / "links.txt").write_text("https://i.test/\n", encoding="utf-8")
        self.site.cut["/dl/game.gba"] = 1000
        WB.fetch(self.env, [self.url("/dl/game.gba")], session=self.sess())
        h = WB.home(self.env)
        self.assertEqual((h["success"], len(h["history"]), len(h["bookmarks"]), [x["url"] for x in h["inbox"]]), (True, 1, 1, ["https://i.test/"]))
        self.assertEqual([j["node"] for j in h["jobs"]], ["web"])
        for i in range(30):
            WB._record_history(self.ud, "https://m.test/%d" % i, "m")
        self.assertEqual(len(WB.home(self.env)["history"]), WB.HOME_HISTORY)


# ======================================================================================
# CLI
# ======================================================================================
class TestCli(Base):
    def run_cli(self, *argv, stdin=""):
        base = ["--quiet", "--no-rescan", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--json"]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(WB, "Session", lambda *a, **k: self.sess()):
            code = CLI.main(base + list(argv))
        self.err = err.getvalue()
        return code, json.loads(out.getvalue())

    def test_home_open_bookmark_history(self):
        self.site.html("/p", '<title>CLI</title><a href="/dl/game.gba">g</a>')
        code, r = self.run_cli("web", "open", self.url("/p"))
        self.assertEqual((code, r["kind"], r["title"], r["counts"]["files"]), (0, "page", "CLI", 1))
        code, r = self.run_cli("web", "open", self.url("/dl/game.gba"))
        self.assertEqual((code, r["kind"], r["name"]), (0, "file", "game.gba"))
        code, r = self.run_cli("web", "bookmark", "add", "https://b.test/", "--title", "Dấu")
        self.assertEqual((code, r["bookmarks"][0]["title"]), (0, "Dấu"))
        code, r = self.run_cli("web", "home")
        self.assertEqual((code, [h["title"] for h in r["history"]], [b["url"] for b in r["bookmarks"]], r["inbox"], r["jobs"]),
                         (0, ["CLI"], ["https://b.test/"], [], []))
        self.assertEqual(self.run_cli("web", "bookmark", "remove", "https://b.test/")[1]["removed"], True)
        self.assertEqual(self.run_cli("web", "history", "clear")[0], 0)
        self.assertEqual(self.run_cli("web", "home")[1]["history"], [])

    def test_open_error_is_json_with_error_kind_and_exit_1(self):
        code, r = self.run_cli("web", "open", self.url("/missing"))
        self.assertEqual((code, r["success"], r["error_kind"]), (1, False, "not_found"))
        code, r = self.run_cli("web", "open", "http://x.test/")
        self.assertEqual((code, r["error_kind"]), (1, "invalid"))

    def test_fetch_needs_yes_dry_run_writes_nothing_then_installs(self):
        u = self.url("/dl/game.gba")
        code, r = self.run_cli("web", "fetch", u)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.roms(), [])
        code, r = self.run_cli("web", "fetch", u, "--dry-run")
        self.assertEqual((code, r["ok"], r["files"][0]["system"]), (0, True, "GBA"))
        self.assertEqual((self.roms(), LS.list_jobs(self.env)["jobs"]), ([], []))
        code, r = self.run_cli("web", "fetch", u, "--yes", "--progress")
        self.assertEqual((code, r["success"], r["installed"]), (0, True, 1))
        self.assertIn("LMPROGRESS download", self.err)
        self.assertEqual(self.roms(), ["Roms/GBA/game.gba"])

    def test_dry_run_reports_html_problem_with_exit_0(self):
        self.site.html("/ad", "<html></html>")
        code, r = self.run_cli("web", "fetch", self.url("/ad"), "--dry-run")
        self.assertEqual((code, r["ok"]), (0, False))
        self.assertTrue(r["files"][0]["problem"])

    def test_jobs_resume_cancel(self):
        self.site.cut["/dl/game.gba"] = 5000
        code, r = self.run_cli("web", "fetch", self.url("/dl/game.gba"), "--yes")
        self.assertEqual((code, r["resumable"]), (1, True))
        code, r = self.run_cli("web", "jobs")
        self.assertEqual((code, len(r["jobs"]), r["jobs"][0]["node"]), (0, 1, "web"))
        jid = r["jobs"][0]["job_id"]
        self.assertNotEqual(self.run_cli("web", "resume", jid)[0], 0)             # cần --yes
        code, r = self.run_cli("web", "resume", jid, "--yes")
        self.assertEqual((code, r["success"]), (0, True))
        self.assertEqual(self.roms(), ["Roms/GBA/game.gba"])
        self.site.cut["/dl/game.gba"] = 5000
        self.run_cli("web", "fetch", self.url("/dl/game.gba"), "--yes", "--conflict", "copy")
        jid = self.only_job()["job_id"]
        self.assertEqual(self.run_cli("web", "cancel", jid, "--yes")[0], 0)
        self.assertEqual(LS.list_jobs(self.env)["jobs"], [])

    def test_bare_web_and_bare_bookmark_are_usage_errors(self):
        out = io.StringIO()
        for argv in (["web"], ["web", "bookmark"], ["web", "history"]):
            with contextlib.redirect_stdout(out):
                code = CLI.main(["--quiet", "--primary", str(self.sd1), "--userdata", str(self.ud), "--json"] + argv)
            self.assertEqual(code, 2, argv)

    def test_static_download_attr_and_referer(self):
        # nút tải không phải <a> nhưng link nằm sẵn trong HTML (data-download-url): dùng được, tải kèm Referer của trang
        self.site.html("/download/game/1", '<button class="btn" data-download-url="%s">Download</button>' % self.url("/dl/game.gba"))
        r = WB.open_url(self.ud, self.url("/download/game/1"), self.sess())
        self.assertEqual(r["links"][0]["url"], self.url("/dl/game.gba"))
        self.assertTrue(r["links"][0]["file"])
        self.assertFalse(r["js_download"])
        self.assertEqual(r["links"][0]["referer"], self.url("/download/game/1"))

        plan = WB.prepare(self.env, [self.url("/download/game/1")], card_pref="primary", session=self.sess())
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["files"][0]["name"], "game.gba")
        self.assertEqual(plan["files"][0]["system"], "GBA")
        self.assertEqual(plan["files"][0]["referer"], self.url("/download/game/1"))

        out = WB.fetch(self.env, [self.url("/download/game/1")], card_pref="primary", session=self.sess())
        self.assertTrue(out["success"])
        self.assertEqual(self.roms(), ["Roms/GBA/game.gba"])
        dl = [x for x in self.site.requests("/dl/game.gba") if x.get("referer")]
        self.assertTrue(dl)
        self.assertEqual(dl[0]["referer"], self.url("/download/game/1"))

    def test_ajax_countdown_is_not_bypassed(self):
        # chính sách: KHÔNG gọi endpoint lấy token / vượt đếm ngược — chỉ cảnh báo, người dùng lấy link trên PC
        self.site.html("/download/game/2", (
            '<div id="download-container" data-ajax-url="%s/wp-admin/admin-ajax.php">'
            '<p id="countdown-text">7 seconds</p><a href="#" id="download-link">Download</a></div>') % self.site.base)
        self.site.pages["/wp-admin/admin-ajax.php"] = (200, {"Content-Type": "application/json"}, b'{"success": true}')
        r = WB.open_url(self.ud, self.url("/download/game/2"), self.sess())
        self.assertTrue(r["js_download"])
        self.assertEqual(r["counts"]["files"], 0)
        self.assertEqual([x for x in self.site.log if x.get("method") == "POST"], [])

    def test_resolved_link_error_is_reported_not_swallowed(self):
        self.site.html("/download/gone/1", '<button data-download-url="%s">Download</button>' % self.url("/dl/missing.gba"))
        with self.assertRaises(LS.LibError):           # 404 của link thật, không lặng lẽ thành "trang HTML"
            WB.prepare(self.env, [self.url("/download/gone/1")], card_pref="primary", session=self.sess())

    def test_resolver_loop_between_pages_is_bounded(self):
        self.site.html("/download/a/1", '<button data-download-url="%s">Download</button>' % self.url("/download/b/1"))
        self.site.html("/download/b/1", '<button data-download-url="%s">Download</button>' % self.url("/download/a/1"))
        plan = WB.prepare(self.env, [self.url("/download/a/1")], card_pref="primary", session=self.sess())
        self.assertFalse(plan["ok"])                    # dừng sau vài bước và báo là trang HTML, không đệ quy vô hạn
        self.assertLessEqual(len(self.site.requests("/download/a/1")) + len(self.site.requests("/download/b/1")), 4)

    def test_download_path_hint_and_ad_blocking(self):
        html = (
            '<a href="/download/game/1">Version 1</a>'
            '<a href="https://crowbarrinds.com/banner.js" rel="sponsored">Ad</a>'
            '<a href="https://ad-maven.com/pop">Pop</a>'
        )
        res = WB.parse_page(html, PAGE_URL)
        dl_links = [l for l in res["links"] if "/download/game/1" in l["url"]]
        self.assertTrue(dl_links[0]["download_hint"])
        self.assertEqual(res["hidden_junk"], 2)


if __name__ == "__main__":
    unittest.main()


# ======================================================================================
# ảnh bìa từ trang đã tải game
# ======================================================================================
def png(w=200, h=150):
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 64


def jpg(w=300, h=200):
    return b"\xff\xd8\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9 + b"\xff\xc0" + (17).to_bytes(2, "big") \
        + b"\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 64


class TestCover(Base):
    PAGE = ('<html><head><title>Hero Quest ROM - Free Download</title><meta property="og:image" content="/img/logo.png">'
            '<meta property="og:title" content="RomSite - best roms"></head><body><img src="/img/logo.png" alt="RomSite logo">'
            '<img src="/img/hq-box.png" alt="Hero Quest (USA) box art"><a href="/dl/hero-quest.gba">Download</a></body></html>')

    def setUp(self):
        super().setUp()
        self.site.files["/dl/hero-quest.gba"] = (GBA_DATA, {})
        self.site.files["/img/logo.png"] = (png(400, 400), {})
        self.site.files["/img/hq-box.png"] = (png(), {})
        self.site.html("/game/hero", self.PAGE)

    def fetch(self):
        return WB.fetch(self.env, [self.url("/dl/hero-quest.gba")], card_pref="primary", session=self.sess(),
                        from_page=self.url("/game/hero"))

    def test_attaches_the_image_matching_the_game_not_the_site_logo(self):
        r = self.fetch()
        self.assertTrue(r["success"], r)
        art = self.sd1 / "Images/GBA/hero-quest.png"
        self.assertEqual(art.read_bytes(), png())
        self.assertEqual(self.site.requests("/img/logo.png"), [], "không tải logo")
        self.assertIn("Ảnh bìa", r["message"])

    def test_og_image_used_only_when_og_title_matches_and_jpeg_kept_as_jpg(self):
        self.site.files["/img/og.jpg"] = (jpg(), {})
        self.site.html("/game/hero", '<meta property="og:title" content="Hero Quest - GBA">'
                       '<meta property="og:image" content="/img/og.jpg"><a href="/dl/hero-quest.gba">Download</a>')
        self.assertTrue(self.fetch()["success"])
        self.assertEqual((self.sd1 / "Images/GBA/hero-quest.jpg").read_bytes(), jpg())

    def test_no_matching_image_or_not_png_jpg_means_no_art(self):
        self.site.html("/game/hero", '<img src="/img/logo.png" alt="RomSite logo"><a href="/dl/hero-quest.gba">x</a>')
        r = self.fetch()
        self.assertTrue(r["success"])
        self.assertFalse((self.sd1 / "Images").exists())
        self.assertIn("không có ảnh khớp", r["results"][0]["cover_note"])
        self.site.files["/img/hq.webp"] = (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64, {})
        self.site.html("/game/hero", '<img src="/img/hq.webp" alt="Hero Quest"><a href="/dl/hero-quest.gba">x</a>')
        (self.sd1 / "Roms/GBA/hero-quest.gba").unlink()
        self.fetch()
        self.assertFalse((self.sd1 / "Images/GBA").exists() and any((self.sd1 / "Images/GBA").iterdir()))

    def test_existing_art_is_kept(self):
        self.w = self.sd1 / "Images/GBA/hero-quest.jpg"
        self.w.parent.mkdir(parents=True)
        self.w.write_bytes(b"MINE")
        r = self.fetch()
        self.assertEqual(self.w.read_bytes(), b"MINE")
        self.assertFalse((self.sd1 / "Images/GBA/hero-quest.png").exists())
        self.assertIn("đã có", r["results"][0]["cover_note"])

    def test_art_stem_and_image_sniffing(self):
        self.assertEqual(WB._art_stem("Sonic.md.zip"), "Sonic")
        self.assertEqual(WB._art_stem("1941.zip"), "1941")
        self.assertEqual(WB._art_stem("Game (Disc 1).cue"), "Game (Disc 1)")
        self.assertEqual(WB._image_kind(png(10, 20)), (".png", 10, 20))
        self.assertEqual(WB._image_kind(jpg(320, 240)), (".jpg", 320, 240))
        self.assertIsNone(WB._image_kind(b"<html>"))
