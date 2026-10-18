"""
LoongMate - Homebrew Hub: logic dung chung + tai TRUC TIEP tren Pocket 1 (khong qua PC).

Hai nguoi dung:
  * pc/homebrew_sync.py (PC/Mac/Pi): import lai cac ham loc giay phep / phan loai / hash / ten file o day (khong doi hanh vi).
  * LoongMate tren may (CLI `homebrew ...`, man Library > Homebrew): `sync_catalog` -> `list_games` -> `prepare`/`fetch`.

Chi muc co GIAY PHEP RO RANG moi duoc hien/tai (whitelist SPDX/CC; thieu/la/`disable-downloads` -> loai, khong suy dien).

An toan mang (kiem tra that trong tests/test_homebrew_device.py):
  * HTTPS voi KIEM TRA CHUNG THU DAY DU (CERT_REQUIRED + check_hostname) bang bo CA cua may (`cafile`). Khong co bo CA thi bao loi ro,
    KHONG BAO GIO tat kiem tra.
  * Chi nhan host trong danh sach trang (hh3.gbdev.io, raw.githubusercontent.com), cong 443. Redirect duoc theo tay (toi da 3 buoc)
    va tung buoc deu phai nam trong danh sach trang va https; ra host la -> chan.
  * Gioi han kich thuoc; <= 1 yeu cau/giay; User-Agent tuong minh (Hub tra 403 khi thieu).
  * Hash trong catalog (md5/sha1/sha256) neu co: sai -> xoa ban tai, KHONG cai.

Tai: dung lai co che job ben cua engine/libsource.py (cung thu muc job tren the dich, .part, tien do %, chon the, cai bang importer).
Job danh dau `source: "homebrew"`; libsource.run_job giao cho `download_file`/`after_install` o day. Khong co duong cai thu hai.
Giay phep: ghi vao <app_data>/homebrew-licenses/<slug>.txt (NGOAI Roms/ de Leaf khong hien nhu game).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import time
import unicodedata
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine import libsource
from engine.libsource import LibError

try:
    import ssl
except ImportError:                       # Python khong co OpenSSL: bao ro khi can noi
    ssl = None  # type: ignore[assignment]

API = "https://hh3.gbdev.io/api"
API_HOST = "hh3.gbdev.io"
RAW_HOST_NAME = "raw.githubusercontent.com"
RAW_HOST = "https://" + RAW_HOST_NAME
ALLOWED_HOSTS = (API_HOST, RAW_HOST_NAME)
USER_AGENT = "LoongMate-homebrew/1 (+personal use)"   # Hub tra HTTP 403 neu thieu User-Agent
PAGE_SIZE = 100
MIN_INTERVAL = 1.0
MAX_ROM_BYTES = 128 * 1024 * 1024
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 3
SYSTEMS = ("GB", "GBC", "GBA", "NES")
EXT_SYSTEM = {".gb": "GB", ".gbc": "GBC", ".cgb": "GBC", ".gba": "GBA", ".nes": "NES"}
EXT_OUT = {".cgb": ".gbc"}  # Leaf chi biet .gbc
CACHE_DIR = "homebrew-cache"
CATALOG_FILE = "catalog.json"
LICENSE_DIR = "homebrew-licenses"
CATALOG_SCHEMA = 1
JOB_SOURCE = "homebrew"
JOB_NODE, JOB_ROOT = "homebrew", "hub"
CAFILE_CANDIDATES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem", "/etc/pki/tls/certs/ca-bundle.crt")

REASON_TEXT = {
    "no_license": "khong co giay phep",
    "unrecognized_license": "giay phep la / khong nhan dien duoc",
    "downloads_disabled": "tac gia tat tai xuong (use-requirements.disable-downloads)",
    "no_rom_file": "khong co file ROM truc tiep (chi .zip/.7z/...)",
    "unsafe_entry": "slug/ten file/kho nguon khong an toan",
    "hash_mismatch": "hash trong catalog khong khop file tai ve",
}


# ---------------------------------------------------------------- giay phep
_FIXED = {"MIT": "MIT", "ZLIB": "Zlib", "0BSD": "0BSD", "UNLICENSE": "Unlicense", "CC0-1.0": "CC0-1.0",
          "MPL-2.0": "MPL-2.0", "APACHE-2.0": "Apache-2.0", "BSD-2-CLAUSE": "BSD-2-Clause",
          "BSD-3-CLAUSE": "BSD-3-Clause", "PUBLIC DOMAIN": "public-domain"}
_GPL = re.compile(r"(GPL-(?:2\.0|3\.0)|LGPL-(?:2\.0|2\.1|3\.0))(-ONLY|-OR-LATER)?")
_CC = re.compile(r"CC-BY(?:-SA|-NC|-NC-SA|-NC-ND|-ND)?-[1-4]\.\d")


def normalize_license(value):
    """Chuoi giay phep -> ma chuan neu NHAN DIEN DUOC, nguoc lai None (khong suy dien)."""
    if not isinstance(value, str):
        return None
    s = re.sub(r"\s+", " ", value).strip()
    m = re.fullmatch(r"(?i)(CC-BY[A-Z-]*) (\d\.\d)", s)  # 'CC-BY-SA 4.0' -> 'CC-BY-SA-4.0'
    if m:
        s = m.group(1) + "-" + m.group(2)
    s = re.sub(r"(?i)^public[ -]domain$", "PUBLIC DOMAIN", s)
    u = s.upper()
    if u in _FIXED:
        return _FIXED[u]
    if _GPL.fullmatch(u) or _CC.fullmatch(u):
        return u
    return None


def license_verdict(entry):
    """-> ('ok', game_id, assets_id|None, raw_game, raw_assets|None) hoac (ly_do, chi_tiet)."""
    game = entry.get("gameLicense", entry.get("license"))
    assets = entry.get("assetsLicense", entry.get("assetLicense"))
    if game is None or (isinstance(game, str) and not game.strip()):
        return ("no_license", "")
    g = normalize_license(game)
    if g is None:
        return ("unrecognized_license", str(game))
    a = None
    if assets is not None and not (isinstance(assets, str) and not assets.strip()):
        a = normalize_license(assets)
        if a is None:
            return ("unrecognized_license", "tai nguyen: %s" % (assets,))
    return ("ok", g, a, game, assets)


# ---------------------------------------------------------------- ten file, hash, url
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def fat32_name(text, maxlen=100):
    """Ten an toan cho FAT32/exFAT (khong ky tu cam, ten thiet bi, dau cham/cach cuoi)."""
    s = unicodedata.normalize("NFC", str(text))
    s = s.replace(":", " -")
    s = re.sub(r'[<>"/\\|?*\x00-\x1f\x7f]', "_", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip(".")
    s = s[:maxlen].rstrip(". ")
    if not s:
        s = "game"
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s


def _hash_specs(spec) -> Dict[str, str]:
    """Hash catalog (chuoi hex hoac {algo: hex}) -> {algo: hex thuong}; bo qua thu khong kiem duoc."""
    if isinstance(spec, str):
        spec = {{32: "md5", 40: "sha1", 64: "sha256"}.get(len(spec.strip()), "?"): spec}
    if not isinstance(spec, dict):
        return {}
    return {a: w.strip().lower() for a, w in spec.items() if a in ("md5", "sha1", "sha256") and isinstance(w, str)}


def hash_mismatch(data, spec):
    """None neu khop (hoac catalog khong co hash kiem duoc); nguoc lai chuoi mo ta."""
    for algo, want in _hash_specs(spec).items():
        got = hashlib.new(algo, data).hexdigest()
        if got != want:
            return "%s: catalog %s, thuc te %s" % (algo, want, got)
    return None


def file_hash_mismatch(path, spec):
    """Nhu hash_mismatch nhung doc tep theo khoi (khong nap ca ROM vao RAM)."""
    specs = _hash_specs(spec)
    if not specs:
        return None
    hs = {a: hashlib.new(a) for a in specs}
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            for h in hs.values():
                h.update(chunk)
    for algo, want in specs.items():
        got = hs[algo].hexdigest()
        if got != want:
            return "%s: catalog %s, thuc te %s" % (algo, want, got)
    return None


def raw_url(entry, filename):
    """URL raw GitHub cua file trong kho goc, hoac None neu bat thuong."""
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", str(entry.get("baserepo", "")))
    slug = str(entry.get("slug", ""))
    parts = str(filename).split("/")
    if (not m or not re.fullmatch(r"[\w.-]+", slug) or slug in (".", "..")
            or any(p in ("", ".", "..") or "\\" in p for p in parts)):
        return None
    return "%s/%s/%s/master/entries/%s/%s" % (RAW_HOST, m.group(1), m.group(2), slug,
                                            urllib.parse.quote("/".join(parts), safe="/"))


def pick_rom(entry):
    """File ROM chinh: uu tien default, roi playable; chi nhan duoi .gb/.gbc/.cgb/.gba/.nes."""
    files = [f for f in (entry.get("files") or []) if isinstance(f, dict) and isinstance(f.get("filename"), str)
             and os.path.splitext(f["filename"])[1].lower() in EXT_SYSTEM]
    files.sort(key=lambda f: (not f.get("default"), not f.get("playable")))  # sort on dinh: giu thu tu goc
    return files[0] if files else None


def leaf_folders(root=None):
    """id he -> ten thu muc chuan cua Leaf, doc tu systems.json (de ten khong bi ghi cung). root=None: tim nhu engine."""
    try:
        if root is not None:
            p = Path(root) / "systems.json"
        else:
            from engine.inventory import find_systems_catalog
            p = find_systems_catalog()
        data = json.loads(p.read_text(encoding="utf-8"))
        return {s["id"]: s["folder"] for s in data["systems"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def classify_entry(entry, queried):
    """-> (cand, None) hoac (None, (he, ly_do, chi_tiet)). cand co them mo ta/the/ngay de hien tren man hinh."""
    rom = pick_rom(entry)
    system = EXT_SYSTEM[os.path.splitext(rom["filename"])[1].lower()] if rom else queried
    v = license_verdict(entry)
    if v[0] != "ok":
        return None, (system, v[0], v[1])
    ur = entry.get("use-requirements")
    if isinstance(ur, dict) and ur.get("disable-downloads") is True:
        return None, (system, "downloads_disabled", "")
    if not rom:
        return None, (system, "no_rom_file", "")
    url = raw_url(entry, rom["filename"])
    if not url:
        return None, (system, "unsafe_entry", str(entry.get("slug")))
    dev = entry.get("developer")
    author = ", ".join(map(str, dev)) if isinstance(dev, list) else str(dev or "")
    desc = entry.get("description")
    tags = entry.get("tags")
    return {"slug": entry["slug"], "title": str(entry.get("title") or entry["slug"]), "system": system,
            "rom": rom, "url": url, "author": author, "license": v[3], "license_id": v[1],
            "license_assets": v[4], "repository": entry.get("repository") or entry.get("website") or "",
            "typetag": entry.get("typetag") or "",
            "description": re.sub(r"\s+", " ", desc).strip()[:500] if isinstance(desc, str) else "",
            "tags": [t for t in tags if isinstance(t, str)][:8] if isinstance(tags, list) else [],
            "website": next((str(entry[k]) for k in ("gameWebsite", "devWebsite", "website") if isinstance(entry.get(k), str) and entry.get(k)), ""),
            "added": str(entry.get("firstadded_date") or entry.get("date") or "")}, None


def license_text(r):
    lines = ["Tieu de:      %s" % r["title"], "Tac gia:      %s" % r["author"],
             "Giay phep:    %s" % r["license"]]
    if r.get("license_assets"):
        lines.append("Giay phep tai nguyen: %s" % r["license_assets"])
    lines += ["Slug:         %s" % r["slug"], "Nguon file:   %s" % r["source_url"]]
    if r.get("repository"):
        lines.append("Trang/kho:    %s" % r["repository"])
    lines += ["Danh muc:     Homebrew Hub (https://hh3.gbdev.io/)", "SHA-256:      %s" % r["sha256"],
              "Tai ngay:     %s" % r["downloaded_at"], "",
              "Tu dung ca nhan; ton trong dieu khoan giay phep tren (ghi cong, khong thuong mai, khong sua doi...).", ""]
    return "\n".join(lines)


# ====================================================================================
# HTTPS: kiem tra chung thu day du, host trang, redirect co kiem soat
# ====================================================================================
def find_cafile(environ=None, isfile=os.path.isfile) -> Optional[str]:
    env = os.environ if environ is None else environ
    v = (env.get("LOONGMATE_CAFILE") or "").strip()
    if v:
        if not isfile(v):
            raise LibError("tls", "LOONGMATE_CAFILE trỏ tới file không tồn tại: %s" % v)
        return v
    cands = list(CAFILE_CANDIDATES)
    if ssl is not None:
        vp = ssl.get_default_verify_paths()
        cands = [vp.cafile, vp.openssl_cafile] + cands
    for c in cands:
        if c and isfile(c):
            return c
    return None


def make_context(cafile: Optional[str] = None):
    """SSLContext KIEM TRA DAY DU (CERT_REQUIRED + check_hostname). Khong co bo CA tren may posix -> loi, khong ha cap."""
    if ssl is None:
        raise LibError("tls", "Python trên máy không có module ssl (OpenSSL): không tải được từ Internet.")
    cafile = cafile or find_cafile()
    if cafile is None:
        if os.name == "nt":
            ctx = ssl.create_default_context()      # kho chung thu cua Windows (chi khi chay tren PC)
        else:
            raise LibError("tls", "Máy không có bộ chứng thư CA (đã tìm %s): không xác minh được máy chủ nên KHÔNG tải. "
                                  "Đặt LOONGMATE_CAFILE=<file .pem> nếu bộ CA nằm chỗ khác." % ", ".join(CAFILE_CANDIDATES))
    else:
        try:
            ctx = ssl.create_default_context(cafile=cafile)
        except (ssl.SSLError, OSError) as e:
            raise LibError("tls", "Không nạp được bộ chứng thư CA %s: %s" % (cafile, e))
    if hasattr(ctx, "post_handshake_auth"):
        ctx.post_handshake_auth = True
    try:
        ctx.set_alpn_protocols(["http/1.1"])
    except (AttributeError, NotImplementedError):
        pass
    return ctx


class Session:
    """Nguoi tai HTTPS. `allowed`: tap host (hoac 'host:port' khi thu nghiem). Moi yeu cau/redirect deu bi kiem tra."""

    def __init__(self, allowed=ALLOWED_HOSTS, cafile: Optional[str] = None, context=None,
                 min_interval: float = MIN_INTERVAL, clock=time.monotonic, sleep=time.sleep, timeout: float = 30.0,
                 max_redirects: int = MAX_REDIRECTS, user_agent: str = USER_AGENT):
        self.max_redirects, self.user_agent = max_redirects, user_agent    # engine/web.py dung lai Session voi gia tri khac
        self.allowed = {a.lower() for a in allowed}
        self.cafile, self._ctx = cafile, context
        self.min_interval, self.clock, self.sleep, self.timeout = min_interval, clock, sleep, timeout
        self._last: Optional[float] = None

    def close(self) -> None:          # tuong thich libsource.Client (run_job goi c.close())
        return None

    def context(self):
        if self._ctx is None:
            self._ctx = make_context(self.cafile)
        return self._ctx

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self._last + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def check_url(self, url: str) -> Tuple[str, int, str]:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        try:
            port = u.port
        except ValueError:
            port = -1
        netloc = ("%s:%s" % (host, port)) if port else host
        if u.scheme != "https" or not host or u.username or u.password or not (
                (host in self.allowed and port in (None, 443)) or netloc in self.allowed):
            raise LibError("invalid", "Chặn: %s không nằm trong danh sách máy chủ được phép (%s) hoặc không phải https."
                           % (u.hostname or url[:60], ", ".join(sorted(self.allowed))))
        target = u.path or "/"
        if u.query:
            target += "?" + u.query
        return host, port or 443, target

    @contextmanager
    def request(self, url: str, headers: Optional[Dict[str, str]] = None):
        """GET co kiem soat. Tra response CHUA doc body (dong ket noi khi thoat khoi `with`). Loi tang mang -> LibError."""
        ctx = self.context()
        hdrs = {"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Encoding": "identity"}
        hdrs.update(headers or {})
        conn = None
        try:
            for hop in range(self.max_redirects + 1):
                host, port, target = self.check_url(url)
                self._throttle()
                conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
                try:
                    conn.request("GET", target, headers=hdrs)
                    resp = conn.getresponse()
                except LibError:
                    raise
                except Exception as e:
                    raise _net_error(e, host)
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.getheader("Location")
                    conn.close()
                    conn = None
                    if not loc or hop >= self.max_redirects:
                        raise LibError("invalid", "Chặn: chuyển hướng sai hoặc quá %d bước." % self.max_redirects)
                    url = urllib.parse.urljoin(url, loc)     # buoc ke tiep phai qua check_url
                    continue
                yield resp
                return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def error_for(resp) -> LibError:
        st = resp.status
        if st == 404:
            return LibError("not_found", "Máy chủ trả 404: file không còn trên Homebrew Hub/GitHub.", st)
        if st == 429:
            return LibError("rate_limited", "Máy chủ báo quá nhiều yêu cầu (429). Thử lại sau ít giây.", st)
        if st == 403:
            return LibError("server", "Máy chủ từ chối (HTTP 403).", st)
        if st >= 500:
            return LibError("server", "Máy chủ báo lỗi (HTTP %d)." % st, st)
        return LibError("bad_response", "Máy chủ trả HTTP %d." % st, st)

    def get_bytes(self, url: str, limit: int, retries: int = 3) -> bytes:
        last: Optional[LibError] = None
        for attempt in range(retries):
            try:
                with self.request(url) as resp:
                    if resp.status != 200:
                        raise self.error_for(resp)
                    cl = resp.getheader("Content-Length")
                    if cl and cl.isdigit() and int(cl) > limit:
                        raise LibError("too_large", "Phản hồi vượt giới hạn %d byte." % limit)
                    try:
                        data = resp.read(limit + 1)
                    except (http.client.HTTPException, OSError) as e:
                        raise _net_error(e, "")
                    if len(data) > limit:
                        raise LibError("too_large", "Phản hồi vượt giới hạn %d byte." % limit)
                    return data
            except LibError as e:
                last = e
                if e.kind not in ("rate_limited", "server", "network", "timeout"):
                    raise
            if attempt + 1 < retries:
                self.sleep(2 ** (attempt + 1))
        assert last is not None
        raise last

    def probe(self, url: str) -> Dict[str, Any]:
        """Kich thuoc + ETag cua file ma KHONG tai no (Range 0-0; may chu bo qua Range thi lay Content-Length)."""
        with self.request(url, {"Range": "bytes=0-0"}) as resp:
            if resp.status == 206:
                cr = libsource.parse_content_range(resp.getheader("Content-Range"))
                if not cr or cr[0] != 0:
                    raise LibError("bad_response", "Content-Range của 206 sai khuôn", 206)
                size = cr[2]
            elif resp.status == 200:
                cl = resp.getheader("Content-Length")
                if not (cl and cl.isdigit()):
                    raise LibError("bad_response", "Máy chủ không báo Content-Length", 200)
                size = int(cl)
            elif resp.status == 416:
                size = 0
            else:
                raise self.error_for(resp)
            return {"size": size, "etag": resp.getheader("ETag")}


def _net_error(e: BaseException, host: str) -> LibError:
    import socket
    if ssl is not None and isinstance(e, ssl.SSLCertVerificationError):
        return LibError("tls", "Chứng thư của %s KHÔNG được tin cậy (%s). Dừng lại, không tải." % (host or "máy chủ", getattr(e, "verify_message", e)))
    if ssl is not None and isinstance(e, ssl.SSLError):
        return LibError("tls", "Bắt tay TLS với %s thất bại (%s)." % (host or "máy chủ", e))
    if isinstance(e, socket.timeout):
        return LibError("timeout", "Hết thời gian chờ %s. Kiểm tra Wi-Fi." % (host or "máy chủ"))
    if isinstance(e, socket.gaierror):
        return LibError("unreachable", "Không phân giải được tên %s. Kiểm tra máy đã nối Wi-Fi/Internet chưa." % host)
    if isinstance(e, (http.client.HTTPException, OSError)):
        return LibError("network", "Mất kết nối tới %s (%s)." % (host or "máy chủ", e))
    raise e


# ====================================================================================
# danh muc (cache trong app_data)
# ====================================================================================
def _iso(now=time.time) -> str:
    return datetime.fromtimestamp(now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def catalog_path(app_data) -> Path:
    return Path(app_data) / CACHE_DIR / CATALOG_FILE


def load_cache(app_data) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(catalog_path(app_data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == CATALOG_SCHEMA and isinstance(d.get("entries"), list) else None


def sync_catalog(app_data, session: Session, systems=SYSTEMS, progress: Optional[Callable] = None,
                 now=time.time) -> Dict[str, Any]:
    """Tai TOAN BO danh muc qua API (<= 1 yeu cau/giay), loc giay phep, ghi cache nguyen tu. Loi giua chung -> giu cache cu."""
    t0 = time.monotonic()
    found: Dict[str, Tuple[Dict[str, Any], str]] = {}
    pages = 0
    systems = tuple(systems)
    for si, system in enumerate(systems):
        page, total = 1, 1
        while True:
            url = "%s/search?%s" % (API, urllib.parse.urlencode({"platform": system, "results": PAGE_SIZE, "page": page}))
            raw = session.get_bytes(url, MAX_PAGE_BYTES)
            try:
                d = json.loads(raw)
                entries, total = d["entries"], int(d.get("page_total", 1))
            except (ValueError, KeyError, TypeError):
                raise LibError("bad_response", "API trả về dữ liệu không hiểu được: %s" % url)
            if not isinstance(entries, list):
                raise LibError("bad_response", "API trả về 'entries' không phải mảng: %s" % url)
            pages += 1
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("slug"), str):
                    found.setdefault(e["slug"], (e, system))
            if progress:
                progress({"phase": "catalog", "done": page, "total": max(total, page), "index": si + 1, "count": len(systems),
                          "name": "%s trang %d/%d" % (system, page, max(total, page))})
            if page >= total or not entries:
                break
            page += 1
    accepted, rejected = [], []
    counts: Dict[str, Dict[str, int]] = {s: {"accepted": 0} for s in systems}
    for slug in sorted(found):
        entry, queried = found[slug]
        cand, rej = classify_entry(entry, queried)
        if rej:
            system, reason, detail = rej
            if system in counts:
                counts[system][reason] = counts[system].get(reason, 0) + 1
                rejected.append({"slug": slug, "title": str(entry.get("title") or slug)[:80], "system": system, "reason": reason,
                                 "detail": str(detail)[:80]})
        elif cand["system"] in counts:
            cand["rom"] = {k: cand["rom"][k] for k in ("filename", "hash") if k in cand["rom"]}
            accepted.append(cand)
            counts[cand["system"]]["accepted"] += 1
    accepted.sort(key=lambda c: (c["system"], c["title"].lower(), c["slug"]))
    doc = {"schema": CATALOG_SCHEMA, "fetched_at": _iso(now), "fetched_epoch": now(), "source": API, "total_seen": len(found),
           "counts": counts, "entries": accepted, "rejected": rejected}
    libsource._write_json_atomic(catalog_path(app_data), doc)
    return {"success": True, "fetched_at": doc["fetched_at"], "pages": pages, "total_seen": len(found), "accepted": len(accepted),
            "rejected": len(rejected), "counts": counts, "elapsed_s": round(time.monotonic() - t0, 1),
            "message": "Đã cập nhật danh mục: %d game có giấy phép rõ ràng (loại %d mục không rõ giấy phép/không tải được) từ %d trang."
                       % (len(accepted), len(rejected), pages)}


def _need_cache(env) -> Dict[str, Any]:
    c = load_cache(env.app_data)
    if c is None:
        raise LibError("not_found", "Chưa có danh mục Homebrew trên máy. Chạy `homebrew sync` (cần Internet) hoặc chọn Cập nhật danh mục.")
    return c


def target_name(c: Dict[str, Any]) -> str:
    ext = os.path.splitext(c["rom"]["filename"])[1].lower()
    return fat32_name(c["title"]) + EXT_OUT.get(ext, ext)


def _folder(c: Dict[str, Any], folders: Dict[str, str]) -> str:
    return folders.get(c["system"], c["system"])


def _present(env, cands: List[Dict[str, Any]], folders) -> Dict[str, bool]:
    importer = libsource._importer()
    have = libsource._present_names(env, importer, None, {_folder(c, folders) for c in cands})
    out = {}
    for c in cands:
        names = have.get(_folder(c, folders), ())
        alt = fat32_name("%s [%s]" % (c["title"], c["slug"]), 120) + os.path.splitext(target_name(c))[1]
        out[c["slug"]] = target_name(c).lower() in names or alt.lower() in names
    return out


def _public(c: Dict[str, Any], folder: str, present: bool) -> Dict[str, Any]:
    return {"slug": c["slug"], "title": c["title"], "author": c["author"], "license": c["license"], "license_id": c["license_id"],
            "license_assets": c.get("license_assets"), "system": c["system"], "folder": folder, "present": present,
            "description": c.get("description", ""), "repository": c.get("repository", ""), "website": c.get("website", ""),
            "tags": c.get("tags", []), "added": c.get("added", ""), "typetag": c.get("typetag", ""),
            "file": c["rom"]["filename"], "url": c["url"]}


def list_games(env, system: Optional[str] = None, query: Optional[str] = None, only: Optional[str] = None,
               now=time.time) -> Dict[str, Any]:
    """Doc cache (khong mang). Co system/query/only='all' thi tra `entries`; khong thi chi tom tat theo he."""
    cache = _need_cache(env)
    folders = leaf_folders()
    ents = cache["entries"]
    systems = [{"system": s, "folder": folders.get(s, s), "count": sum(1 for c in ents if c["system"] == s)} for s in SYSTEMS]
    systems = [s for s in systems if s["count"]]
    out: Dict[str, Any] = {"success": True, "fetched_at": cache.get("fetched_at"), "age_s": int(now() - cache.get("fetched_epoch", now())),
                           "total": len(ents), "total_seen": cache.get("total_seen", 0), "rejected": len(cache.get("rejected", [])),
                           "systems": systems, "jobs": [j for j in libsource.list_jobs(env)["jobs"] if j.get("node") == JOB_NODE]}
    if system or query or only == "all":
        shown = ents
        if system:
            su = system.upper()
            shown = [c for c in shown if c["system"] == su or folders.get(c["system"], "").upper() == su]
        if query:
            q = query.lower()
            shown = [c for c in shown if q in c["title"].lower() or q in c["author"].lower() or q in c["slug"]]
        shown = sorted(shown, key=lambda c: (c["title"].lower(), c["slug"]))
        pres = _present(env, shown, folders)
        out["entries"] = [_public(c, _folder(c, folders), pres[c["slug"]]) for c in shown]
        out["shown"] = len(shown)
    return out


def show_game(env, slug: str) -> Dict[str, Any]:
    cache = _need_cache(env)
    folders = leaf_folders()
    for c in cache["entries"]:
        if c["slug"] == slug:
            lp = license_path(env.app_data, slug)
            d = _public(c, _folder(c, folders), _present(env, [c], folders)[slug])
            d.update(success=True, listed=True, license_file=str(lp) if lp.is_file() else None, fetched_at=cache.get("fetched_at"))
            return d
    for r in cache.get("rejected", []):
        if r["slug"] == slug:
            return {"success": True, "listed": False, "slug": slug, "title": r["title"], "system": r["system"], "reason": r["reason"],
                    "message": "'%s' không được hiện: %s%s." % (slug, REASON_TEXT.get(r["reason"], r["reason"]),
                                                               " (%s)" % r["detail"] if r["detail"] else "")}
    raise LibError("not_found", "Không có '%s' trong danh mục (cập nhật lúc %s)." % (slug, cache.get("fetched_at")))


def license_path(app_data, slug: str) -> Path:
    if not re.fullmatch(r"[\w.-]+", slug) or slug in (".", ".."):
        raise LibError("invalid", "slug không an toàn: %r" % slug)
    return Path(app_data) / LICENSE_DIR / (slug + ".txt")


# ====================================================================================
# chuan bi / tai / cai
# ====================================================================================
def prepare(env, slugs: List[str], card_pref: str = "auto", session: Optional[Session] = None) -> Dict[str, Any]:
    """Hoi may chu kich thuoc tung file (Range 0-0), kiem gioi han, chon the dich nhu importer. Khong tao gi tren the."""
    imp = libsource._importer()
    if not slugs:
        raise LibError("invalid", "Chưa chọn game nào.")
    if len(set(slugs)) != len(slugs):
        raise LibError("invalid", "Danh sách có slug trùng.")
    cache = _need_cache(env)
    by_slug = {c["slug"]: c for c in cache["entries"]}
    rejected = {r["slug"]: r for r in cache.get("rejected", [])}
    folders = leaf_folders()
    sess = session or Session()
    files, problems, used = [], [], set()
    for slug in slugs:
        c = by_slug.get(slug)
        if c is None:
            r = rejected.get(slug)
            raise LibError("invalid", "'%s' không có trong danh mục game có giấy phép rõ ràng%s." % (
                slug, (": " + REASON_TEXT.get(r["reason"], r["reason"])) if r else ""))
        local = target_name(c)
        if local.lower() in used:
            local = fat32_name("%s [%s]" % (c["title"], c["slug"]), 120) + os.path.splitext(local)[1]
        used.add(local.lower())
        f = {"path": slug, "name": local, "local": local, "system": _folder(c, folders), "problem": None, "title": c["title"],
             "url": c["url"], "hash": c["rom"].get("hash"), "license": c["license"], "license_id": c["license_id"],
             "license_assets": c.get("license_assets"), "author": c["author"], "repository": c.get("repository", ""),
             "etag": None, "sha256": None}
        try:
            pr = sess.probe(c["url"])
        except LibError as e:
            if e.kind in ("tls", "unreachable", "timeout", "network", "invalid"):
                raise
            f.update(size=0, problem=e.message)
            files.append(f)
            problems.append({"path": slug, "reason": e.message})
            continue
        f.update(size=pr["size"], etag=pr["etag"])
        reason = None
        if pr["size"] == 0:
            reason = "file rỗng"
        elif pr["size"] > MAX_ROM_BYTES:
            reason = "file lớn hơn %d MB, bỏ qua" % (MAX_ROM_BYTES // (1024 * 1024))
        if reason:
            f["problem"] = reason
            problems.append({"path": slug, "reason": reason})
        files.append(f)
    pres = _present(env, [by_slug[f["path"]] for f in files], folders)
    for f in files:
        f["present"] = pres[f["path"]]
    total = sum(f["size"] for f in files)
    needed = 2 * total + imp.DEFAULT_LIMITS.reserve_bytes
    card = imp.choose_card(env.cards, needed, card_pref)
    ok = not problems and card["selected"] is not None
    msg = "; ".join("%s: %s" % (p["path"], p["reason"]) for p in problems[:3]) if problems else card["reason"]
    return {"success": True, "ok": ok, "message": msg, "node": JOB_NODE, "root": JOB_ROOT, "files": files, "problems": problems,
            "total_bytes": total, "needed_bytes": needed, "card": card, "needs_card_choice": card["needs_choice"]}


def fetch(env, slugs: List[str], card_pref: str = "auto", progress=None, conflict: str = "keep",
          session: Optional[Session] = None) -> Dict[str, Any]:
    pl = prepare(env, slugs, card_pref, session)
    if not pl["ok"]:
        return {"success": False, "started": False, "message": pl["message"], "needs_card_choice": pl["needs_card_choice"], "plan": pl}
    d, job = libsource.create_job(env, pl, pl["card"]["selected"])
    job["source"] = JOB_SOURCE
    for jf, pf in zip(job["files"], pl["files"]):
        jf.update({k: pf[k] for k in ("url", "hash", "etag", "title", "license", "license_id", "license_assets", "author", "repository")})
    libsource._save_job(d, job)
    libsource._ledger(env.app_data, "job_created", job=job["job_id"], node=JOB_NODE, root=JOB_ROOT, files=len(slugs), bytes=pl["total_bytes"])
    out = libsource.run_job(env, d, progress, conflict, session=session)
    out["started"] = True
    return out


def download_file(sess: Session, job_dir: Path, job: Dict[str, Any], f: Dict[str, Any], idx: int, base_done: int, total: int, prog) -> None:
    """Tai + kiem hash MOT file (cung hop dong voi libsource._download_file). Mat ket noi: LibError(network...) va GIU .part.
    Hash catalog sai: xoa .part, LibError(hash)."""
    final = job_dir / "files" / f["local"]
    part = final.with_name(final.name + ".part")
    final.parent.mkdir(parents=True, exist_ok=True)
    nfiles = len(job["files"])

    def emit(done_in_file: int, phase: str = "download", force: bool = False):
        prog({"phase": phase, "job_id": job["job_id"], "index": idx + 1, "count": nfiles, "name": f["name"],
              "done": base_done + done_in_file, "total": total}, force)

    def finish(digest: str):
        bad = file_hash_mismatch(part, f.get("hash"))
        if bad:
            part.unlink(missing_ok=True)
            raise LibError("hash", "Hash trong danh mục không khớp file tải về (%s). Đã xoá bản tải, KHÔNG cài." % bad)
        f["sha256"] = digest
        emit(f["size"], force=True)
        os.replace(part, final)
        libsource._fsync_dir(final.parent)
        f["verified"] = True

    if final.is_file():                                   # da doi ten nhung chua kip ghi job.json: kiem lai, dung tin
        if f.get("sha256") and final.stat().st_size == f["size"] and libsource._sha256_file(final) == f["sha256"] \
                and not file_hash_mismatch(final, f.get("hash")):
            f["verified"] = True
            return
        final.unlink()
    for _ in range(3):
        try:
            have = part.stat().st_size
        except OSError:
            have = 0
        if have > f["size"] or (have and not f.get("etag")):   # khong co ETag thi khong the noi an toan: tai lai tu dau
            part.unlink(missing_ok=True)
            have = 0
        if have and have == f["size"]:
            emit(f["size"], "verify", True)
            finish(libsource._sha256_file(part))
            return
        headers = {}
        if f.get("referer"):
            headers["Referer"] = f["referer"]
        if have:
            headers["Range"] = "bytes=%d-" % have
            if f.get("etag"):
                headers["If-Range"] = f["etag"]
        with sess.request(f["url"], headers) as resp:
            st = resp.status
            if st == 416:
                part.unlink(missing_ok=True)
                continue
            if st not in (200, 206):
                raise sess.error_for(resp)
            if (resp.getheader("Content-Type") or "").strip().lower().startswith("text/html"):
                part.unlink(missing_ok=True)                  # ROM khong bao gio la HTML: trang quang cao/dem nguoc/loi
                raise LibError("not_file", "Máy chủ trả về trang web (HTML) chứ không phải file — có thể là trang quảng cáo/đếm ngược. Đã bỏ bản tải.")
            et = resp.getheader("ETag")
            h = hashlib.sha256()
            if st == 206:
                cr = libsource.parse_content_range(resp.getheader("Content-Range"))
                if not have or not cr or cr[0] != have or cr[2] != f["size"] or (et and f.get("etag") and et != f["etag"]):
                    part.unlink(missing_ok=True)          # tra loi khong khop dieu da xin: khong dam noi
                    continue
                mode = "ab"
                with open(part, "rb") as old:
                    for chunk in iter(lambda: old.read(1 << 20), b""):
                        h.update(chunk)
            else:                                         # 200 = TOAN BO file -> bo .part cu, KHONG noi
                cl = resp.getheader("Content-Length")
                if cl and cl.isdigit():
                    ncl = int(cl)
                    if ncl == 0 or ncl > (f.get("max_bytes") or MAX_ROM_BYTES):
                        raise LibError("invalid", "Kích thước file trên máy chủ không hợp lệ (%d byte)." % ncl)
                    f["size"] = ncl
                f["etag"] = et
                have, mode = 0, "wb"
            want, got = f["size"] - have, 0
            with open(part, mode) as out:
                while got < want:
                    try:
                        chunk = resp.read(min(libsource.CHUNK, want - got))
                    except (http.client.HTTPException, OSError) as e:
                        raise LibError("network", "Mất kết nối giữa chừng (%s); đã có %d / %d byte — chạy resume để tiếp tục"
                                       % (e, have + got, f["size"]), sent=True)
                    if not chunk:
                        break
                    try:
                        out.write(chunk)
                    except OSError as e:
                        raise LibError("storage", "Không ghi được file tải (%s); .part giữ lại để tiếp tục" % e, sent=True)
                    h.update(chunk)
                    got += len(chunk)
                    emit(have + got)
                try:
                    out.flush()
                    os.fsync(out.fileno())
                except OSError as e:
                    raise LibError("storage", "Không ghi được file tải (%s); .part giữ lại để tiếp tục" % e, sent=True)
            if got != want:
                raise LibError("network", "Máy chủ đóng kết nối sớm: có %d / %d byte — chạy resume để tiếp tục" % (have + got, f["size"]), sent=True)
            try:
                extra = resp.read(1)
            except (http.client.HTTPException, OSError):
                extra = b""
            if extra:
                part.unlink(missing_ok=True)
                raise LibError("bad_response", "Máy chủ gửi nhiều byte hơn kích thước đã khai — bỏ file tải")
        finish(h.hexdigest())
        return
    raise LibError("network", "Không tiếp tục được file (máy chủ liên tục trả lời không khớp) — thử lại sau")


def after_install(env, job: Dict[str, Any], inst: Dict[str, Any]) -> None:
    """Sau khi importer cai xong: ghi giay phep cua tung game da cai vao <app_data>/homebrew-licenses/<slug>.txt (ngoai Roms/)."""
    done = {str(r.get("name", "")).lower() for r in inst.get("results", []) or [] if r.get("status") in ("installed", "already_present")}
    for f in job["files"]:       # importer bao ten game KHONG co duoi (vd. "Conways Game of Life")
        if not ({f["name"].lower(), os.path.splitext(f["name"])[0].lower()} & done) or not f.get("sha256"):
            continue
        rec = {"slug": f["path"], "title": f.get("title") or f["path"], "author": f.get("author", ""), "license": f.get("license", ""),
               "license_assets": f.get("license_assets"), "source_url": f.get("url", ""), "repository": f.get("repository", ""),
               "sha256": f["sha256"], "downloaded_at": _iso()}
        try:
            p = license_path(env.app_data, f["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".partial")
            tmp.write_text(license_text(rec), encoding="utf-8")
            os.replace(tmp, p)
        except (OSError, LibError):
            pass                                           # khong ghi duoc ghi chu giay phep khong lam hong viec cai
