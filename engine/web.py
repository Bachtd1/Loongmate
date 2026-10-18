"""
LoongMate - Tải từ web: duyệt một trang web BẤT KỲ (chỉ xem danh sách link + ô tìm kiếm) rồi tải file người dùng chọn.

Đây là công cụ chung (kiểu trình quản lý tải / trình duyệt chữ), KHÔNG có mã riêng cho trang nào, KHÔNG chạy JavaScript, KHÔNG cookie,
KHÔNG vượt quảng cáo/đếm ngược/captcha, KHÔNG giả danh trình duyệt, KHÔNG gửi form POST.

An toàn:
  * Chỉ https, kiểm tra chứng thư đầy đủ (dùng lại homebrew.Session: make_context/find_cafile). Mỗi bước chuyển hướng đều qua check_url.
  * Chặn địa chỉ nội bộ (loopback, RFC1918, link-local, CGNAT/Tailscale...): một trang web không được khiến máy gọi vào LAN/tailnet (SSRF).
  * Link trả về trang HTML thay vì file (trang quảng cáo/đếm ngược) -> từ chối, không cài.
  * File tải về đi qua đúng đường của Nhập game (importer): chặn file chạy được, zip bomb, giới hạn kích thước. Job bền, tiếp tục được.

Job đánh dấu `source: "web"`; libsource.run_job giao cho homebrew.download_file (tải tiếp Range/If-Range, sha256).
"""
from __future__ import annotations

import codecs
import http.client
import ipaddress
import json
import os
import re
import socket
import time
import unicodedata
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine import homebrew, libsource, patcher
from engine.libsource import LibError

JOB_NODE, JOB_ROOT, JOB_SOURCE = "web", "web", "web"
USER_AGENT = "Mozilla/5.0 (compatible; LoongMate/1; +Pocket 1 handheld)"   # trung thực: không giả trình duyệt cụ thể
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 6
MIN_INTERVAL = 0.5
MAX_LINKS = 400
MAX_FORMS = 5
HISTORY_CAP = 50
BOOKMARK_CAP = 100
HOME_HISTORY = 20
INBOX_CAP = 50
INBOX_MAX_BYTES = 64 * 1024
WEB_FILE = "web.json"
WEB_SCHEMA = 1
ARCHIVE_FILE_EXTS = {".zip", ".7z", ".rar"}
PAGE_TYPES = ("text/html", "application/xhtml+xml")


def _max_file_bytes() -> int:
    return libsource._importer().DEFAULT_LIMITS.max_file_bytes


# ====================================================================================
# HTTPS: chỉ https, chặn địa chỉ nội bộ
# ====================================================================================
def _check_ip(host: str, addr: str) -> None:
    """Địa chỉ phải là địa chỉ công cộng toàn cầu; còn lại (loopback, riêng tư, link-local, CGNAT 100.64/10 gồm Tailscale,
    unspecified, multicast, reserved) -> chặn. IPv4-mapped IPv6 xét theo IPv4 bên trong."""
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:        # is_global đúng cho 224.0.0.0/4 nên phải loại multicast riêng
        raise LibError("invalid", "Chặn: %s trỏ vào mạng nội bộ (%s) — LoongMate không mở địa chỉ nội bộ từ trang web." % (host, addr))


class Session(homebrew.Session):
    """homebrew.Session cho mọi máy chủ https công khai. `allow_private=True` CHỈ để thử nghiệm (máy chủ loopback)."""

    def __init__(self, cafile=None, context=None, resolver=socket.getaddrinfo, allow_private: bool = False, **kw):
        kw.setdefault("min_interval", MIN_INTERVAL)
        kw.setdefault("max_redirects", MAX_REDIRECTS)
        kw.setdefault("user_agent", USER_AGENT)
        super().__init__(allowed=(), cafile=cafile, context=context, **kw)
        self.resolver, self.allow_private = resolver, allow_private
        self.last_url: Optional[str] = None        # URL của yêu cầu gần nhất = URL cuối sau chuyển hướng

    def _check_public(self, host: str, port: int) -> None:
        # Còn một rủi ro chấp nhận: lúc nối socket, http.client phân giải tên lần nữa (DNS rebinding) — không chặn được ở tầng này.
        try:
            ipaddress.ip_address(host)
            literal = True
        except ValueError:
            literal = False
        if literal:
            _check_ip(host, host)
            return
        try:
            infos = self.resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise homebrew._net_error(e, host)
        if not infos:
            raise homebrew._net_error(socket.gaierror("no address"), host)
        for info in infos:
            _check_ip(host, str(info[4][0]))

    def check_url(self, url: str) -> Tuple[str, int, str]:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        try:
            port = u.port
        except ValueError:
            port = -1
        if u.scheme != "https":
            raise LibError("invalid", "Chỉ hỗ trợ địa chỉ https:// (trang http không mã hoá, dễ bị chèn quảng cáo/mã độc).")
        if not host or port == -1 or u.username or u.password:
            raise LibError("invalid", "Địa chỉ không hợp lệ: %s" % url[:80])
        if not self.allow_private:
            self._check_public(host, port or 443)
        self.last_url = url
        target = u.path or "/"
        if u.query:
            target += "?" + u.query
        return host, port or 443, target

    @staticmethod
    def error_for(resp) -> LibError:
        st = resp.status
        if st == 404:
            return LibError("not_found", "Máy chủ trả 404: trang hoặc file không còn tồn tại.", st)
        if st in (401, 403):
            return LibError("server", "Máy chủ từ chối (HTTP %d): cần đăng nhập hoặc không cho truy cập kiểu này." % st, st)
        return homebrew.Session.error_for(resp)


# ====================================================================================
# URL, tên file
# ====================================================================================
_BAD_SCHEMES = re.compile(r"^(javascript|data|mailto|tel|sms|file|ftp|blob|about):", re.I)
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean_url(url: str) -> str:
    """Bỏ tab/xuống dòng, mã hoá phần không an toàn (dấu cách, Unicode...) để request không bị http.client từ chối."""
    url = re.sub(r"[\t\r\n]", "", url).strip()
    return urllib.parse.quote(url, safe="%/:?#[]@!$&'()*+,;=~-._")


def normalize_url(text: str) -> str:
    s = (text or "").strip()
    if not s:
        raise LibError("invalid", "Chưa nhập địa chỉ trang web.")
    if "://" in s:
        scheme = s.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise LibError("invalid", "Chỉ hỗ trợ địa chỉ https:// (không hỗ trợ %s://)." % scheme)
    elif _BAD_SCHEMES.match(s):
        raise LibError("invalid", "Chỉ hỗ trợ địa chỉ https:// (không hỗ trợ %s)." % s.split(":", 1)[0])
    else:
        s = "https://" + s
    return _clean_url(s)


def _site(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _same_site(a: str, b: str) -> bool:
    a, b = _site(a), _site(b)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def _url_ext(url: str) -> str:
    return os.path.splitext(urllib.parse.unquote(urllib.parse.urlsplit(url).path))[1].lower()


def safe_filename(name: Optional[str]) -> str:
    """Chỉ lấy tên cuối (không / \\ ..), bỏ ký tự điều khiển, giữ đuôi, tên đi qua fat32_name. Rỗng -> download.bin."""
    s = _CTRL.sub("", str(name or "")).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if s in ("", ".", ".."):
        return "download.bin"
    stem, ext = os.path.splitext(s)
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:10]
    ext = "." + ext if ext else ""
    if not stem.strip("."):
        return "download.bin"
    return homebrew.fat32_name(stem) + ext


def _disposition_name(value: Optional[str]) -> Optional[str]:
    """filename*=UTF-8''... được ưu tiên hơn filename=..."""
    if not value:
        return None
    m = re.search(r"(?i)(?:^|;)\s*filename\*\s*=\s*([\w.-]+)'[^']*'([^;]+)", value)
    if m:
        try:
            enc = codecs.lookup(m.group(1)).name
        except LookupError:
            enc = "utf-8"
        return urllib.parse.unquote(m.group(2).strip(), encoding=enc, errors="replace")
    m = re.search(r'(?i)(?:^|;)\s*filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]+))', value)
    if m:
        return re.sub(r"\\(.)", r"\1", m.group(1)) if m.group(1) is not None else m.group(2).strip()
    return None


def _name_for(final_url: str, disposition: Optional[str]) -> str:
    raw = _disposition_name(disposition) or urllib.parse.unquote(urllib.parse.urlsplit(final_url).path.rsplit("/", 1)[-1])
    return safe_filename(raw)


# ====================================================================================
# phân tích trang
# ====================================================================================
_file_exts_cache: Optional[set] = None


def file_exts() -> set:
    """Đuôi coi là "file tải": mọi đuôi ROM/đĩa importer biết (systems.json) + .zip/.7z/.rar + patch .ips/.bps/.ups."""
    global _file_exts_cache
    if _file_exts_cache is None:
        _file_exts_cache = set(libsource._importer().load_catalog()["ext"]) | ARCHIVE_FILE_EXTS | set(patcher.PATCH_EXTS)
    return _file_exts_cache


def _collapse(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", _CTRL.sub(" ", text)).strip()[:limit].strip()


# Máy chủ mạng xã hội / quảng cáo: link tới đó từ một trang khác là rác (nút chia sẻ, banner), không phải nơi tải file.
JUNK_HOSTS = frozenset((
    "facebook.com", "fb.com", "twitter.com", "x.com", "t.co", "pinterest.com", "linkedin.com", "reddit.com", "tumblr.com",
    "vk.com", "wa.me", "whatsapp.com", "t.me", "telegram.me", "instagram.com", "tiktok.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "adservice.google.com", "amazon-adsystem.com", "taboola.com",
    "outbrain.com", "popads.net", "propellerads.com", "adsterra.com", "exoclick.com", "juicyads.com", "mgid.com",
    "crowbarrinds.com", "ad-maven.com", "popcash.net", "yllix.com", "revenuehits.com", "clickadu.com", "hilltopads.com",
    "monetag.com", "adsterratools.com"))
_VOID_TAGS = frozenset("area base br col embed hr img input link meta source track wbr".split())
_NAV_TAGS = frozenset(("nav", "header", "footer", "aside"))
_NAV_ROLES = frozenset(("navigation", "banner", "contentinfo", "complementary"))
_NAV_TOKENS = frozenset(("nav", "navbar", "navigation", "menu", "menubar", "footer", "header", "sidebar", "breadcrumb",
                         "breadcrumbs", "widget", "share", "social"))
_DOWNLOAD_RE = re.compile(r"\b(download|downloads|tải về|tải xuống|tai ve|tai xuong|get file|mirror)\b", re.I)
_DL_PATH_RE = re.compile(r"/(download|downloads|tai-ve|tai-xuong)(/|$)", re.I)


def _is_junk_host(host: str) -> bool:
    return any(host == j or host.endswith("." + j) for j in JUNK_HOSTS)


def _dl_text(*parts: str) -> bool:
    return any(_DOWNLOAD_RE.search(unicodedata.normalize("NFC", x)) for x in parts if x)


def _dl_hint(text: str, title: str, url: str = "") -> bool:
    if any(_DOWNLOAD_RE.search(unicodedata.normalize("NFC", x)) for x in (text, title) if x):
        return True
    if url:
        path = urllib.parse.urlsplit(url).path
        return bool(_DL_PATH_RE.search(path))
    return False


def _dead_href(href: str) -> bool:
    h = href.strip().lower()
    return h == "" or h.startswith("#") or h.startswith("javascript:")


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self.in_title = False
        self.skip = 0                                    # trong <script>/<style>
        self.base: Optional[str] = None
        self.anchors: List[Dict[str, Any]] = []
        self.cur: Optional[Dict[str, Any]] = None
        self.forms: List[Dict[str, Any]] = []
        self.form: Optional[Dict[str, Any]] = None
        self.stack: List[Tuple[str, bool, bool]] = []     # (thẻ, là vùng điều hướng, là phân trang) của các thẻ đang mở
        self.all_a: List[Dict[str, Any]] = []             # mọi <a> (kể cả không href) để dò nút tải "chết"
        self.buttons: List[Dict[str, Any]] = []
        self.btn: Optional[Dict[str, Any]] = None

    def _zone(self, own_pag: bool) -> str:
        """"nav" nếu có tổ tiên là vùng điều hướng, trừ khi có phân trang nằm trong (hoặc là chính) vùng đó."""
        nav_at = next((i for i, e in enumerate(self.stack) if e[1]), None)
        if nav_at is None:
            return "main"
        if own_pag or any(e[2] for e in self.stack[nav_at:]):
            return "main"
        return "nav"

    def _push(self, tag: str, a: Dict[str, str]) -> None:
        toks = [t for t in re.split(r"[\s\-_]+", (a.get("class", "") + " " + a.get("id", "")).lower()) if t]
        roles = a.get("role", "").lower().split()
        nav = tag in _NAV_TAGS or any(r in _NAV_ROLES for r in roles) or any(t in _NAV_TOKENS for t in toks)
        # class "page"/"page-wrapper" trên <body>/<html> là lớp của khung giao diện, không phải phân trang
        pag = tag not in ("html", "body") and any(t.startswith("pag") for t in toks)
        self.stack.append((tag, nav, pag))

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag == "input":
            typ = (a.get("type") or "text").strip().lower()
            if typ in ("button", "submit"):
                self.buttons.append({"text": [a.get("value", "")], "onclick": bool(a.get("onclick", "").strip()), "form": self.form})
        if tag == "title" and not self.title_parts:
            self.in_title = True
        elif tag == "base" and self.base is None and a.get("href"):
            self.base = a["href"]
        elif tag == "a":
            toks = [t for t in re.split(r"[\s\-_]+", (a.get("class", "") + " " + a.get("id", "")).lower()) if t]
            self.cur = {"href": a.get("href", ""), "title": a.get("title", ""), "download": "download" in a, "text": [],
                        "rel": a.get("rel", "").lower().split(), "onclick": bool(a.get("onclick", "").strip()),
                        "zone": self._zone(any(t.startswith("pag") for t in toks))}
            self.all_a.append(self.cur)
            if a.get("href", "").strip():
                self.anchors.append(self.cur)
        elif tag == "button":
            self.btn = {"text": [], "onclick": bool(a.get("onclick", "").strip()), "form": self.form}
            self.buttons.append(self.btn)
        elif tag == "img" and self.cur is not None:
            self.cur["text"].append(" " + a.get("alt", "") + " ")
        elif tag == "form" and self.form is None:
            self.form = {"method": (a.get("method") or "get").strip().lower(), "action": a.get("action", ""), "hidden": [],
                         "text": [], "bad": False}
        elif tag == "input" and self.form is not None:
            typ = (a.get("type") or "text").strip().lower()
            if typ in ("password", "file"):
                self.form["bad"] = True
            elif typ == "hidden":
                if a.get("name"):
                    self.form["hidden"].append((a["name"], a.get("value", "")))
            elif typ in ("text", "search"):
                self.form["text"].append(a)
        if tag not in _VOID_TAGS:
            self._push(tag, a)

    def handle_startendtag(self, tag, attrs):
        if tag in ("img", "input", "base"):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        for i in range(len(self.stack) - 1, -1, -1):      # HTML hỏng: đóng xuống thẻ mở gần nhất cùng tên; không có thì bỏ qua
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag == "title":
            self.in_title = False
        elif tag == "a":
            self.cur = None
        elif tag == "button":
            self.btn = None
        elif tag == "form" and self.form is not None:
            self.forms.append(self.form)
            self.form = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title_parts.append(data)
        else:
            if self.cur is not None:
                self.cur["text"].append(data)
            if self.btn is not None:
                self.btn["text"].append(data)

    def close(self):
        super().close()
        if self.form is not None:
            self.forms.append(self.form)
            self.form = None


def _https_join(base: str, ref: str) -> Optional[str]:
    ref = re.sub(r"[\t\r\n]", "", ref).strip()
    if not ref or _BAD_SCHEMES.match(ref):
        return None
    try:
        return _clean_url(urllib.parse.urldefrag(urllib.parse.urljoin(base, ref))[0])
    except ValueError:                                   # vd. "https://[::1" (IPv6 hỏng): link rác, bỏ qua
        return None


def parse_page(html_text: str, base_url: str) -> Dict[str, Any]:
    p = _PageParser()
    p.feed(html_text)
    p.close()
    page_url = urllib.parse.urldefrag(base_url)[0]
    page_host = (urllib.parse.urlsplit(page_url).hostname or "").lower()
    base = page_url
    if p.base:
        b = _https_join(page_url, p.base)
        if b and b.startswith("https://"):
            base = b
    title = _collapse("".join(p.title_parts), 120) or page_host

    links: Dict[str, Dict[str, Any]] = {}
    insecure = set()
    exts = file_exts()
    for an in p.anchors:
        url = _https_join(base, an["href"])
        if not url:
            continue
        if url.lower().startswith("http://"):
            insecure.add(url)
            continue
        if not url.lower().startswith("https://") or url == page_url:
            continue
        text = _collapse("".join(an["text"]), 120)
        if not text:
            text = _collapse(an["title"], 120)
        old = links.get(url)
        if old is None:
            links[url] = {"text": text, "title": an["title"], "download": an["download"], "sponsored": "sponsored" in an["rel"],
                          "zone": an["zone"]}
        else:
            old["text"] = old["text"] or text
            old["title"] = old["title"] or an["title"]
            old["download"] = old["download"] or an["download"]
            old["sponsored"] = old["sponsored"] and "sponsored" in an["rel"]    # chỉ rác nếu MỌI lần xuất hiện đều là quảng cáo
            if an["zone"] == "main":
                old["zone"] = "main"                                             # có lúc nằm ở nội dung chính -> coi là nội dung chính
    out_links = []
    hidden_junk = 0
    for url, d in links.items():
        ext = _url_ext(url)
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        same_site = _same_site(host, page_host)
        if d["sponsored"] or (not same_site and _is_junk_host(host)):
            hidden_junk += 1
            continue
        is_file = ext in exts or d["download"]
        text = d["text"] or _collapse(urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]), 120) or url[:120]
        out_links.append({"text": text, "url": url, "file": is_file, "ext": ext if is_file and ext in exts else "",
                          "same_site": same_site, "zone": "main" if is_file else d["zone"],
                          "download_hint": not is_file and _dl_hint(d["text"], d["title"], url)})
    out_links.sort(key=lambda x: (not x["file"], not x["download_hint"]))          # ổn định: file, rồi link "tải", rồi còn lại
    truncated = len(out_links) > MAX_LINKS

    js_download = any(_dl_text(_collapse("".join(an["text"]), 120), an["title"]) and (_dead_href(an["href"]) or an["onclick"])
                      for an in p.all_a)

    forms = []
    kept_forms = set()
    for f in p.forms:
        if f["method"] != "get" or f["bad"] or len(f["text"]) != 1 or not f["text"][0].get("name"):
            continue
        action = _https_join(base, f["action"]) if f["action"].strip() else page_url
        if not action or not action.lower().startswith("https://"):
            continue
        inp = f["text"][0]
        s = urllib.parse.urlsplit(action)
        query = urllib.parse.urlencode(f["hidden"]) + ("&" if f["hidden"] else "") + urllib.parse.quote_plus(inp["name"]) + "={q}"
        label = _collapse(inp.get("placeholder") or inp.get("aria-label") or "", 60) or "Tìm kiếm"
        forms.append({"label": label, "template": urllib.parse.urlunsplit((s.scheme, s.netloc, s.path, query, ""))})
        kept_forms.add(id(f))
        if len(forms) >= MAX_FORMS:
            break
    # nút "tải" không có đích thật (JS/đếm ngược): không nằm trong form GET mà ta trả về, hoặc có onclick
    js_download = js_download or any(
        _dl_text(_collapse("".join(b["text"]), 120)) and (b["onclick"] or b["form"] is None or id(b["form"]) not in kept_forms)
        for b in p.buttons)
    return {"title": title, "links": out_links[:MAX_LINKS], "forms": forms, "truncated": truncated, "dropped_insecure": len(insecure),
            "hidden_junk": hidden_junk, "js_download": js_download}


def decode_body(data: bytes, content_type: str = "") -> str:
    """Bảng mã: header Content-Type, rồi <meta> trong 4 KiB đầu, rồi utf-8. Mã lạ -> utf-8; lỗi byte -> thay thế."""
    m = re.search(r"(?i)charset\s*=\s*[\"']?([\w.:-]+)", content_type or "")
    if not m:
        m = re.search(r"(?i)<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", data[:4096].decode("latin-1"))
    enc = "utf-8"
    if m:
        try:
            enc = codecs.lookup(m.group(1)).name
        except LookupError:
            pass
    return data.decode(enc, errors="replace")


# ====================================================================================
# lịch sử / dấu trang / hộp thư links.txt
# ====================================================================================
def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _web_path(app_data) -> Path:
    return Path(app_data) / WEB_FILE


def load_state(app_data) -> Dict[str, List[Dict[str, str]]]:
    """Thiếu/hỏng file -> trạng thái trống (không bao giờ ném lỗi)."""
    out: Dict[str, List[Dict[str, str]]] = {"history": [], "bookmarks": []}
    try:
        d = json.loads(_web_path(app_data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(d, dict):
        return out
    for key in out:
        for e in d.get(key) or []:
            if isinstance(e, dict) and isinstance(e.get("url"), str) and e["url"]:
                out[key].append({"url": e["url"], "title": str(e.get("title") or ""), "at": str(e.get("at") or "")})
    return out


def _save_state(app_data, st: Dict[str, List[Dict[str, str]]]) -> None:
    libsource._write_json_atomic(_web_path(app_data), {"schema": WEB_SCHEMA, "history": st["history"][:HISTORY_CAP],
                                                       "bookmarks": st["bookmarks"][:BOOKMARK_CAP]})


def _record_history(app_data, url: str, title: str) -> None:
    st = load_state(app_data)
    st["history"] = [{"url": url, "title": title, "at": _iso()}] + [h for h in st["history"] if h["url"] != url]
    _save_state(app_data, st)


def add_bookmark(app_data, url: str, title: str = "") -> Dict[str, Any]:
    url = normalize_url(url)
    if not url.startswith("https://"):
        raise LibError("invalid", "Chỉ lưu được địa chỉ https://.")
    st = load_state(app_data)
    others = [b for b in st["bookmarks"] if b["url"] != url]
    if len(others) >= BOOKMARK_CAP:
        raise LibError("invalid", "Đã có %d dấu trang — xoá bớt trước khi thêm." % BOOKMARK_CAP)
    st["bookmarks"] = [{"url": url, "title": _collapse(title, 120) or url[:120], "at": _iso()}] + others
    _save_state(app_data, st)
    return {"success": True, "message": "Đã lưu dấu trang.", "bookmarks": st["bookmarks"]}


def remove_bookmark(app_data, url: str) -> Dict[str, Any]:
    url = normalize_url(url)
    st = load_state(app_data)
    keep = [b for b in st["bookmarks"] if b["url"] != url]
    gone = len(keep) != len(st["bookmarks"])
    if gone:
        st["bookmarks"] = keep
        _save_state(app_data, st)
    return {"success": True, "removed": gone, "message": "Đã xoá dấu trang." if gone else "Không có dấu trang này.",
            "bookmarks": st["bookmarks"]}


def clear_history(app_data) -> Dict[str, Any]:
    st = load_state(app_data)
    st["history"] = []
    _save_state(app_data, st)
    return {"success": True, "message": "Đã xoá lịch sử duyệt web."}


def inbox(cards) -> List[Dict[str, str]]:
    """Link người dùng tự thả vào <thẻ>/Import/links.txt (mỗi dòng một địa chỉ https, # là chú thích). Chỉ đọc."""
    imp = libsource._importer()
    out, seen = [], set()
    for cid in ("primary", "secondary"):
        root = cards.get(cid)
        if not root:
            continue
        try:
            with open(Path(root) / imp.IMPORT_DIR / "links.txt", "rb") as f:
                text = f.read(INBOX_MAX_BYTES).decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or not line.lower().startswith("https://"):
                continue
            url = _clean_url(line)
            if url in seen or not urllib.parse.urlsplit(url).hostname:
                continue
            seen.add(url)
            out.append({"url": url, "card": cid})
            if len(out) >= INBOX_CAP:
                return out
    return out


def home(env) -> Dict[str, Any]:
    st = load_state(env.app_data)
    return {"success": True, "history": st["history"][:HOME_HISTORY], "bookmarks": st["bookmarks"], "inbox": inbox(env.cards),
            "jobs": [j for j in libsource.list_jobs(env)["jobs"] if j.get("node") == JOB_NODE]}


# ====================================================================================
# mở một địa chỉ
# ====================================================================================
def _ctype(resp) -> str:
    return (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()


def resolve_download(sess: Session, page_url: str, html_text: str) -> Optional[Dict[str, Any]]:
    """Link tải TĨNH có sẵn trong HTML qua thuộc tính data-download-url / data-direct-url / data-file-url (nút tải
    không dùng thẻ <a>). KHÔNG gọi endpoint AJAX, không vượt đếm ngược/quảng cáo/captcha: trang cần chạy JavaScript
    để lộ link thì open_url báo js_download và người dùng lấy link trên PC (Import/links.txt). `sess` để dành, chưa dùng."""
    del sess
    m = re.search(r'data-(?:download|direct|file)-url=["\'](https://[^"\']+)["\']', html_text, re.I)
    if not m:
        return None
    return {"url": m.group(1).replace("&amp;", "&").replace("&#038;", "&"), "text": "", "referer": page_url}


def open_url(app_data, url: str, session: Optional[Session] = None) -> Dict[str, Any]:
    """Trang HTML -> phân tích link/form. Mọi thứ khác -> báo là file (KHÔNG đọc body). Lỗi mạng/TLS/chặn -> LibError."""
    sess = session or Session()
    url = normalize_url(url)
    with sess.request(url, {"Accept": "text/html,*/*", "Cache-Control": "no-cache"}) as resp:
        final = sess.last_url or url
        host = (urllib.parse.urlsplit(final).hostname or "").lower()
        if resp.status != 200:
            raise sess.error_for(resp)
        ctype = _ctype(resp)
        if ctype not in PAGE_TYPES:
            cl = resp.getheader("Content-Length")
            return {"success": True, "kind": "file", "url": final, "host": host,
                    "name": _name_for(final, resp.getheader("Content-Disposition")),
                    "size": int(cl) if cl and cl.isdigit() else None, "content_type": ctype}
        if (resp.getheader("Content-Encoding") or "identity").strip().lower() != "identity":
            raise LibError("bad_response", "Máy chủ trả dữ liệu nén mà LoongMate không yêu cầu — không đọc được trang này.")
        try:
            data = resp.read(MAX_PAGE_BYTES + 1)
        except (http.client.HTTPException, OSError) as e:
            raise homebrew._net_error(e, host)
        content_type = resp.getheader("Content-Type") or ""
    over = len(data) > MAX_PAGE_BYTES
    decoded = decode_body(data[:MAX_PAGE_BYTES], content_type)
    resolved = resolve_download(sess, final, decoded)
    page = parse_page(decoded, final)
    if resolved and resolved.get("url"):
        page["js_download"] = False
        res_url = resolved["url"]
        exts = file_exts()
        res_ext = _url_ext(res_url)
        entry = {
            "text": resolved.get("text") or _collapse(urllib.parse.unquote(urllib.parse.urlsplit(res_url).path.rsplit("/", 1)[-1]), 120),
            "url": res_url,
            "file": True,      # nút tải trỏ tới link này; prepare() vẫn kiểm lại và chặn nếu nó trả trang HTML
            "ext": res_ext if res_ext in exts else "",
            "same_site": _same_site((urllib.parse.urlsplit(res_url).hostname or "").lower(), host),
            "zone": "main",
            "download_hint": False,
            "referer": resolved.get("referer") or final
        }
        res_name = _name_for(res_url, "")
        filtered = [l for l in page["links"] if l["url"] != res_url and not (res_name and res_ext and _name_for(l["url"], "") == res_name and l["url"] != res_url)]
        page["links"] = [entry] + filtered
    links = page["links"]
    files = sum(1 for x in links if x["file"])
    same = sum(1 for x in links if not x["file"] and x["zone"] == "main" and x["same_site"])
    ext_n = sum(1 for x in links if not x["file"] and x["zone"] == "main" and not x["same_site"])
    nav = sum(1 for x in links if not x["file"] and x["zone"] == "nav")
    try:
        _record_history(app_data, final, page["title"])
    except OSError:
        pass                                           # không ghi được lịch sử không làm hỏng việc mở trang
    return {"success": True, "kind": "page", "url": final, "host": host, "title": page["title"], "links": links, "forms": page["forms"],
            "truncated": page["truncated"] or over, "dropped_insecure": page["dropped_insecure"],
            "hidden_junk": page["hidden_junk"], "js_download": page["js_download"],
            "counts": {"files": files, "pages": same, "external": ext_n, "nav": nav}}


# ====================================================================================
# chuẩn bị / tải
# ====================================================================================
def _probe(sess: Session, url: str, referer: Optional[str] = None, _depth: int = 0) -> Dict[str, Any]:
    """Range 0-0: URL cuối, kiểu nội dung, tên, ETag, kích thước (None nếu máy chủ không báo) — không tải file."""
    headers = {"Range": "bytes=0-0"}
    if referer:
        headers["Referer"] = referer
    with sess.request(url, headers) as resp:
        final = sess.last_url or url
        st, size = resp.status, None
        ctype = _ctype(resp)
        if st == 200 and ctype in PAGE_TYPES and _depth < 2:      # giới hạn: trang A -> B -> A không được đệ quy vô hạn
            try:
                data = resp.read(65536)
            except OSError as e:
                raise homebrew._net_error(e, (urllib.parse.urlsplit(final).hostname or "").lower())
            resolved = resolve_download(sess, final, decode_body(data, resp.getheader("Content-Type") or ""))
            if resolved and resolved.get("url") and resolved["url"] != url:
                # lỗi của link thật (404, hết hạn, bị chặn) báo nguyên văn, không lặng lẽ coi như "trang HTML"
                pr = _probe(sess, resolved["url"], referer=resolved.get("referer") or final, _depth=_depth + 1)
                pr["referer"] = resolved.get("referer") or final
                return pr
        if st == 206:
            cr = libsource.parse_content_range(resp.getheader("Content-Range"))
            size = cr[2] if cr else None
        elif st == 200:
            cl = resp.getheader("Content-Length")
            size = int(cl) if cl and cl.isdigit() else None
        elif st == 416:
            size = 0
        else:
            raise sess.error_for(resp)
        return {"url": final, "size": size, "etag": resp.getheader("ETag"), "content_type": ctype,
                "name": _name_for(final, resp.getheader("Content-Disposition")), "referer": referer}


def _problem(name: str, content_type: str, size: Optional[int]) -> Optional[str]:
    imp = libsource._importer()
    ext = os.path.splitext(name)[1].lower()
    if content_type in PAGE_TYPES:
        return "Liên kết này mở ra trang web, không phải file (thường là trang quảng cáo/đếm ngược). Hãy mở nó như một trang để tìm link tải thật."
    if ext in imp.EXEC_EXTS:
        return "file thực thi/script — bị chặn"
    if ext in imp.UNSUPPORTED_ARCHIVE_EXTS:
        return "%s chưa hỗ trợ giải nén trên máy: tải trên PC rồi giải nén" % ext
    if size is None:
        return "máy chủ không báo kích thước file — không tải được an toàn"
    if size == 0:
        return "file rỗng"
    if size > _max_file_bytes():
        return "file lớn hơn 2 GiB (giới hạn FAT32/Nhập game)"
    return None


def prepare(env, urls: List[str], card_pref: str = "auto", session: Optional[Session] = None) -> Dict[str, Any]:
    """Hỏi máy chủ từng file (Range 0-0), kiểm giới hạn, chọn thẻ đích như importer. Không tạo gì trên thẻ."""
    imp, catalog = libsource._catalog()
    if not urls:
        raise LibError("invalid", "Chưa chọn link nào.")
    sess = session or Session()
    files, problems, used = [], [], set()
    for raw in urls:
        pr = _probe(sess, normalize_url(raw))      # lỗi mạng/TLS/chặn/404...: ném (khác Homebrew: URL do người dùng đưa, báo thẳng)
        name = pr["name"]
        stem, ext = os.path.splitext(name)
        n = 1
        while name.lower() in used:
            n += 1
            name = "%s [%d]%s" % (stem, n, ext)
        used.add(name.lower())
        reason = _problem(name, pr["content_type"], pr["size"])
        cls = libsource.classify({"path": name, "size": pr["size"] or 0, "mtime": 0, "sha256": ""}, imp, catalog)
        system = cls["system"] if cls else "?"
        if ext.lower() in patcher.PATCH_EXTS:         # patch ROM (engine/patcher.py): không phải vấn đề; run_job đưa vào kho patch thay vì cài
            system = "PATCH"
        f = {"path": pr["url"], "name": name, "local": name, "system": system, "problem": reason, "url": pr["url"],
             "host": (urllib.parse.urlsplit(pr["url"]).hostname or "").lower(), "hash": None, "etag": pr["etag"], "sha256": None,
             "size": pr["size"] or 0, "max_bytes": _max_file_bytes(), "content_type": pr["content_type"], "present": False,
             "referer": pr.get("referer")}
        files.append(f)
        if reason:
            problems.append({"path": pr["url"], "reason": reason})
    have = libsource._present_names(env, imp, catalog, {f["system"] for f in files})
    for f in files:
        f["present"] = f["name"].lower() in have.get(f["system"], ())
    total = sum(f["size"] for f in files)
    needed = 2 * total + imp.DEFAULT_LIMITS.reserve_bytes
    card = imp.choose_card(env.cards, needed, card_pref)
    ok = not problems and card["selected"] is not None
    msg = "; ".join("%s: %s" % (p["path"][:60], p["reason"]) for p in problems[:3]) if problems else card["reason"]
    return {"success": True, "ok": ok, "message": msg, "node": JOB_NODE, "root": JOB_ROOT, "files": files, "problems": problems,
            "total_bytes": total, "needed_bytes": needed, "card": card, "needs_card_choice": card["needs_choice"]}


# ====================================================================================
# ảnh bìa từ chính trang đã tải game
# ====================================================================================
# Leaf đọc ảnh bìa ở <thẻ của game>/Images/<thư mục hệ>/<tên ROM>.png|.jpg|.jpeg (Jawaka internal/discovery/art_path.h),
# "Sonic.md.zip" -> "Sonic". Ảnh chỉ lấy từ trang chứa link tải, và chỉ khi tên game khớp chữ thay thế (alt)/tên file ảnh,
# hoặc og:title khi dùng og:image -> không gán logo của trang. Không chắc thì bỏ qua (game vẫn cài như thường).
COVER_MAX_BYTES = 3 * 1024 * 1024
COVER_MIN_PX = 64
COVER_MATCH = 0.6           # tỉ lệ từ của tên game phải xuất hiện trong mô tả ảnh
_ART_EXTS = (".png", ".jpg", ".jpeg")
_NOISE_WORDS = frozenset("usa europe japan world eu us jp en fr de es it rom roms download free rev version the a of "
                         "and cover box art boxart front image jpg jpeg png webp gif".split())
_ARCHIVE_EXTS = (".zip", ".7z")


def _name_tokens(text: str) -> set:
    t = unicodedata.normalize("NFKD", urllib.parse.unquote(text or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)
    return {w for w in re.split(r"[^0-9a-z]+", t) if w and w not in _NOISE_WORDS and (len(w) > 1 or w.isdigit())}


def _match_score(game: set, other: str) -> float:
    if not game:
        return 0.0
    return len(game & _name_tokens(other)) / len(game)


def _art_stem(filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    if ext.lower() in _ARCHIVE_EXTS:
        inner, iext = os.path.splitext(stem)
        if iext and 1 < len(iext) <= 6 and iext[1:].isalnum() and not iext[1:].isdigit():
            stem = inner
    return stem


def _image_kind(data: bytes) -> Optional[Tuple[str, int, int]]:
    """(đuôi, rộng, cao) cho PNG/JPEG thật (đọc header); None nếu không phải."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        return ".png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return ".jpg", int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


class _ImageParser(HTMLParser):
    MAX_IMGS = 300

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: Dict[str, str] = {}
        self.imgs: List[Dict[str, str]] = []
        self.title: List[str] = []
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:image", "og:image:secure_url", "twitter:image", "og:title", "twitter:title") and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "img" and len(self.imgs) < self.MAX_IMGS:
            src = a.get("src") or a.get("data-src") or a.get("data-lazy-src") or ""
            if src.strip():
                self.imgs.append({"src": src.strip(), "alt": a.get("alt", ""), "title": a.get("title", "")})

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self._skip:
            self.title.append(data)


def cover_candidates(html_text: str, page_url: str, game_name: str) -> List[Tuple[float, str]]:
    """[(điểm, url https)] giảm dần, chỉ ảnh khớp tên game >= COVER_MATCH."""
    p = _ImageParser()
    try:
        p.feed(html_text)
        p.close()
    except Exception:
        pass
    game = _name_tokens(_art_stem(game_name))
    scored: Dict[str, float] = {}

    def add(ref: str, score: float) -> None:
        u = _https_join(page_url, ref)
        if u and score >= COVER_MATCH and score > scored.get(u, -1):
            scored[u] = score

    for im in p.imgs:
        base = urllib.parse.urlsplit(im["src"]).path.rsplit("/", 1)[-1]
        add(im["src"], max(_match_score(game, im["alt"]), _match_score(game, im["title"]), _match_score(game, base)))
    og = p.meta.get("og:image") or p.meta.get("og:image:secure_url") or p.meta.get("twitter:image")
    if og:
        title = p.meta.get("og:title") or p.meta.get("twitter:title") or " ".join(p.title)
        add(og, min(1.0, _match_score(game, title)) - 0.01)        # img có alt khớp được ưu tiên hơn og:image
    return sorted(((sc, u) for u, sc in scored.items()), key=lambda t: -t[0])


def _rom_of(result: Dict[str, Any]) -> Optional[Path]:
    dests = [Path(d) for d in result.get("dest") or []]
    for pref in (".m3u", ".cue", ".chd", ".gdi"):
        for d in dests:
            if d.suffix.lower() == pref:
                return d
    return next((d for d in dests if d.suffix.lower() != ".bin"), dests[0] if dests else None)


def attach_covers(results: List[Dict[str, Any]], page_url: str, session: Optional[Session] = None) -> List[str]:
    """Gắn ảnh bìa cho game vừa cài từ trang `page_url`. Ghi ra kết quả từng game: "cover" (đường dẫn) hoặc "cover_note"."""
    todo = [r for r in results if r.get("status") == "installed" and _rom_of(r) is not None]
    if not todo or not page_url:
        return []
    sess = session or Session()
    notes: List[str] = []
    try:
        with sess.request(page_url, {"Accept": "text/html,*/*"}) as resp:
            if resp.status != 200 or _ctype(resp) not in PAGE_TYPES:
                return [f"Không đọc lại được trang để lấy ảnh bìa (HTTP {resp.status})."]
            html_text = decode_body(resp.read(MAX_PAGE_BYTES), resp.getheader("Content-Type") or "")
    except (LibError, OSError, http.client.HTTPException) as e:
        return [f"Không đọc lại được trang để lấy ảnh bìa: {e}"]
    for r in todo:
        rom = _rom_of(r)
        parts = rom.parts
        if "Roms" not in parts or parts.index("Roms") + 2 >= len(parts):
            continue                                  # cần <thẻ>/Roms/<hệ>/.../<file>
        i = parts.index("Roms")
        card, sysdir = Path(*parts[:i]), parts[i + 1]
        art_dir = card / "Images" / sysdir
        stem = _art_stem(rom.name)
        try:
            existing = {n.lower() for n in os.listdir(art_dir)} if art_dir.is_dir() else set()
        except OSError:
            existing = set()
        if any((stem + e).lower() in existing for e in _ART_EXTS):
            r["cover_note"] = "đã có ảnh bìa — giữ nguyên"
            continue
        cands = cover_candidates(html_text, page_url, rom.name)
        if not cands:
            r["cover_note"] = "trang không có ảnh khớp tên game — bỏ qua (dùng Scrape Artwork của Leaf nếu cần)"
            continue
        for _score, url in cands[:3]:
            try:
                with sess.request(url, {"Accept": "image/png,image/jpeg,image/*"}) as resp:
                    if resp.status != 200:
                        continue
                    data = resp.read(COVER_MAX_BYTES + 1)
            except (LibError, OSError, http.client.HTTPException):
                continue
            kind = _image_kind(data) if len(data) <= COVER_MAX_BYTES else None
            if not kind or kind[1] < COVER_MIN_PX or kind[2] < COVER_MIN_PX:
                continue
            dest = art_dir / (stem + kind[0])
            try:
                art_dir.mkdir(parents=True, exist_ok=True)
                tmp = art_dir / f".{stem}.cover.partial"
                with open(tmp, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, dest)
            except OSError as e:
                r["cover_note"] = f"không ghi được ảnh bìa: {e}"
                break
            r["cover"] = str(dest)
            notes.append(f"Ảnh bìa: {dest.name} ({kind[1]}x{kind[2]}, từ {urllib.parse.urlsplit(url).hostname})")
            break
        else:
            r["cover_note"] = "ảnh trên trang không phải PNG/JPG hợp lệ (Leaf chỉ đọc PNG/JPG) — bỏ qua"
    return notes


def fetch(env, urls: List[str], card_pref: str = "auto", progress=None, conflict: str = "keep",
          session: Optional[Session] = None, from_page: Optional[str] = None) -> Dict[str, Any]:
    """from_page: trang chứa link tải; cài xong thì gắn ảnh bìa khớp đúng game từ trang đó (trước khi Leaf quét lại)."""
    pl = prepare(env, urls, card_pref, session)
    if not pl["ok"]:
        return {"success": False, "started": False, "message": pl["message"], "needs_card_choice": pl["needs_card_choice"], "plan": pl}
    d, job = libsource.create_job(env, pl, pl["card"]["selected"])
    job["source"] = JOB_SOURCE
    for jf, pf in zip(job["files"], pl["files"]):
        jf.update({k: pf[k] for k in ("url", "hash", "etag", "max_bytes", "host", "referer") if k in pf})
    libsource._save_job(d, job)
    libsource._ledger(env.app_data, "job_created", job=job["job_id"], node=JOB_NODE, root=JOB_ROOT, files=len(urls), bytes=pl["total_bytes"])
    out = libsource.run_job(env, d, progress, conflict, session=session)
    out["started"] = True
    if from_page and out.get("installed"):
        try:
            covers = attach_covers(out.get("results") or [], normalize_url(from_page), session)
        except (LibError, ValueError) as e:
            covers = [f"Bỏ qua ảnh bìa: {e}"]
        if covers:
            out["covers"] = covers
            out["message"] = (out.get("message") or "") + (" " if out.get("message") else "") + " ".join(covers)
    return out
