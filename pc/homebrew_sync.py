#!/usr/bin/env python3
"""LoongMate - thu thap homebrew CO GIAY PHEP ro rang tu Homebrew Hub (gbdev) vao thu vien ROM (chi dung stdlib).

  python pc/homebrew_sync.py --dest D:\\roms --dry-run                 # chi liet ke, khong tai ROM
  python pc/homebrew_sync.py --dest /srv/roms --systems gb,gbc --limit 20
  python pc/homebrew_sync.py --dest /srv/roms --libretro-homebrew-dat   # them: doi chieu SHA-1 voi DAT homebrew cua libretro

Nguon: danh muc qua API https://hh3.gbdev.io/api/search (kiem tra that: moi trang toi da 100 muc, `results=100`),
file ROM tai thang tu raw GitHub cua kho goc (gbdev/database, gbadev-org/games, nesdev-org/homebrew-db) - KHONG clone kho (~1,5 GB).
Chi nhan muc co giay phep thuoc danh sach trang (SPDX/CC) cho game; thieu giay phep, chuoi la, hoac
`use-requirements.disable-downloads` = true -> LOAI, khong suy dien. Xem docs/HOMEBREW.md.

Le phep voi may chu: <= 1 yeu cau/giay, cache JSON tren dia (24 gio), chi tai file moi/doi, co --dry-run.
Ghi nguyen tu (.partial -> rename); khong ghi de file khong do cong cu tao (danh dau trong homebrew-catalog.json).

Ma thoat: 0 ok | 1 co loi tai/ghi | 2 sai cach dung hoac khong lay duoc danh muc / catalog hong.
"""
import argparse
import collections
import hashlib
import itertools
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # logic dung chung nam trong engine/homebrew.py (cung ban voi may Pocket 1)
from engine.homebrew import (  # noqa: E402,F401  (cac ten nay la API cong khai cua module nay: test + nguoi goi import tu day)
    API, EXT_OUT, EXT_SYSTEM, MAX_ROM_BYTES, MIN_INTERVAL, PAGE_SIZE, RAW_HOST, REASON_TEXT, SYSTEMS,
    classify_entry, fat32_name, hash_mismatch, license_verdict, normalize_license, pick_rom, raw_url)
from engine.homebrew import leaf_folders as _leaf_folders, license_text as _license_text  # noqa: E402

USER_AGENT = "LoongMate-homebrew-sync/1 (+personal use)"  # urllib mac dinh bi HTTP 403
DAT_BASE = RAW_HOST + "/libretro/libretro-database/master/metadat/homebrew/"
CACHE_TTL = 24 * 3600
CATALOG_NAME = "homebrew-catalog.json"
LIBRETRO_DAT = {"GB": "Nintendo - Game Boy.dat", "GBC": "Nintendo - Game Boy.dat",
                "GBA": "Nintendo - Game Boy Advance.dat", "NES": "Nintendo - Nintendo Entertainment System.dat"}


class FetchError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


# ---------------------------------------------------------------- mang
class HttpFetcher:
    """urllib + User-Agent tuong minh + <= 1 yeu cau/giay + thu lai 429/5xx/loi mang."""

    def __init__(self, min_interval=MIN_INTERVAL, clock=time.monotonic, sleep=time.sleep, retries=3):
        self.min_interval, self.clock, self.sleep, self.retries = min_interval, clock, sleep, retries
        self._last = None

    def _throttle(self):
        if self._last is not None:
            wait = self._last + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def __call__(self, url, max_bytes=MAX_ROM_BYTES):
        last = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise FetchError("file lon hon %d byte, bo qua" % max_bytes)
                return data
            except urllib.error.HTTPError as e:
                last = FetchError("HTTP %d" % e.code, e.code)
                if e.code != 429 and e.code < 500:
                    raise last
            except (urllib.error.URLError, OSError) as e:
                last = FetchError("loi mang: %s" % e)
            if attempt + 1 < self.retries:
                self.sleep(2 ** (attempt + 1))
        raise last


def leaf_folders():
    return _leaf_folders(ROOT)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def parse_libretro_dat(text):
    """sha1 (chu thuong) -> ten game trong DAT clrmamepro cua libretro."""
    out, game = {}, None
    for line in text.splitlines():
        m = re.match(r'^\s*name "(.*)"\s*$', line)
        if m:
            game = m.group(1)
            continue
        m = re.search(r"\brom \(.*?\bsha1 ([0-9A-Fa-f]{40})", line)
        if m and game:
            out[m.group(1).lower()] = game
    return out


# ---------------------------------------------------------------- dong bo
class Syncer:
    def __init__(self, dest, fetch, systems=SYSTEMS, dry_run=False, limit=None, cache_dir=None, refresh=False,
                 force=False, libretro=False, out=print, now=time.time):
        self.dest = Path(dest)
        self.fetch, self.systems, self.dry_run, self.limit = fetch, tuple(systems), dry_run, limit
        self.cache_dir = Path(cache_dir) if cache_dir else self.dest / ".homebrew-cache"
        self.refresh, self.force, self.libretro, self.out, self.now = refresh, force, libretro, out, now
        folders = leaf_folders()
        self.folder = {s: folders.get(s, s) for s in SYSTEMS}
        self.stats = {s: {"accepted": 0, "rejected": collections.Counter(), "errors": 0, "downloaded": 0,
                          "unchanged": 0, "skipped": collections.Counter(), "libretro": 0} for s in SYSTEMS}
        self.rejected_notes = collections.defaultdict(list)  # ly do -> [slug (chi tiet)]
        self.error_notes = []
        self.records = {}
        self.lr = {}

    # --- cache JSON/DAT
    def _cached(self, name, url):
        p = self.cache_dir / name
        if not self.refresh:
            try:
                if self.now() - p.stat().st_mtime < CACHE_TTL:
                    return p.read_bytes()
            except OSError:
                pass
        data = self.fetch(url)
        try:
            atomic_write(p, data)
        except OSError:
            pass
        return data

    def list_entries(self):
        """slug -> (entry, he_truy_van). Dung API search, phan trang theo page_total."""
        found = {}
        for system in self.systems:
            page = 1
            while True:
                url = "%s/search?%s" % (API, urllib.parse.urlencode(
                    {"platform": system, "results": PAGE_SIZE, "page": page}))
                raw = self._cached("search-%s-p%d.json" % (system, page), url)
                try:
                    d = json.loads(raw)
                    entries, total = d["entries"], int(d.get("page_total", 1))
                except (ValueError, KeyError, TypeError):
                    try:
                        (self.cache_dir / ("search-%s-p%d.json" % (system, page))).unlink()
                    except OSError:
                        pass
                    raise FetchError("API tra ve du lieu khong hieu duoc: %s" % url)
                for e in entries:
                    if isinstance(e, dict) and isinstance(e.get("slug"), str):
                        found.setdefault(e["slug"], (e, system))
                if page >= total or not entries:
                    break
                page += 1
        return found

    # --- catalog / manifest
    def load_catalog(self):
        p = self.dest / CATALOG_NAME
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.records = {r["slug"]: r for r in data["entries"]}
        except (ValueError, KeyError, TypeError) as e:
            raise FetchError("%s hong (%s). Khong doan: sua/xoa tay roi chay lai." % (p, e))

    def save_catalog(self):
        doc = {"generator": USER_AGENT, "updated": self._iso(),
               "sources": ["https://hh3.gbdev.io/", "https://github.com/gbdev/database",
                           "https://github.com/gbadev-org/games", "https://github.com/nesdev-org/homebrew-db"],
               "entries": sorted(self.records.values(), key=lambda r: r["file"].lower())}
        atomic_write(self.dest / CATALOG_NAME, (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))

    def _iso(self):
        return datetime.fromtimestamp(self.now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- loc
    def classify(self, entry, queried):
        """-> (cand, None) hoac (None, (he, ly_do, chi_tiet))."""
        return classify_entry(entry, queried)

    def choose_relpath(self, c, taken):
        ext = os.path.splitext(c["rom"]["filename"])[1].lower()
        ext = EXT_OUT.get(ext, ext)
        folder = self.folder[c["system"]]
        stem = fat32_name(c["title"])
        rel = "%s/%s%s" % (folder, stem, ext)
        if taken.get(rel.lower(), c["slug"]) != c["slug"]:  # cung ten, khac game -> them slug
            rel = "%s/%s%s" % (folder, fat32_name("%s [%s]" % (c["title"], c["slug"]), 120), ext)
        return rel

    # --- chay
    def run(self):
        if not self.dry_run:
            self.dest.mkdir(parents=True, exist_ok=True)
        self.load_catalog()
        found = self.list_entries()
        cands = []
        for slug in sorted(found):
            entry, queried = found[slug]
            cand, rej = self.classify(entry, queried)
            if rej:
                system, reason, detail = rej
                if system in self.systems:
                    self.stats[system]["rejected"][reason] += 1
                    self.rejected_notes[reason].append("%s (%s)%s" % (slug, system, ": " + detail if detail else ""))
            elif cand["system"] in self.systems:
                cands.append(cand)
        cands.sort(key=lambda c: (c["system"], c["slug"]))
        if self.limit is not None:  # xen ke giua cac he de --limit nho van thu duoc ca bon he
            by = [[c for c in cands if c["system"] == s] for s in SYSTEMS]
            cands = [c for row in itertools.zip_longest(*by) for c in row if c][:self.limit]
        if self.libretro and not self.dry_run:
            self.load_libretro()
        taken = {r["file"].lower(): r["slug"] for r in self.records.values()}
        for c in cands:
            self.stats[c["system"]]["accepted"] += 1
            try:
                self.process(c, taken)
            except (FetchError, OSError) as e:
                self.stats[c["system"]]["errors"] += 1
                self.error_notes.append("%s: %s" % (c["slug"], e))
                self.out("  [LOI] %s: %s" % (c["slug"], e))
        return 1 if any(s["errors"] for s in self.stats.values()) else 0

    def load_libretro(self):
        for name in sorted({LIBRETRO_DAT[s] for s in self.systems}):
            try:
                raw = self._cached("libretro-" + name, DAT_BASE + urllib.parse.quote(name))
                self.lr.update(parse_libretro_dat(raw.decode("utf-8", "replace")))
            except FetchError as e:
                self.out("  [CANH BAO] khong tai duoc DAT libretro %s: %s (bo qua doi chieu)" % (name, e))

    def process(self, c, taken):
        st = self.stats[c["system"]]
        want = c["rom"].get("hash")
        rec = self.records.get(c["slug"])
        rel = rec["file"] if rec else self.choose_relpath(c, taken)
        path = self.dest / rel
        if rec and path.exists():
            if sha256_of(path) != rec["sha256"]:
                st["skipped"]["file da bi sua tay (khong ghi de)"] += 1
                self.out("  [BO QUA] %s: file da bi sua tay" % rel)
                return
            if not self.force and rec.get("source_url") == c["url"] and not (
                    want and hash_mismatch(path.read_bytes(), want)):
                st["unchanged"] += 1
                return
        elif not rec and path.exists():
            st["skipped"]["da co file cung ten khong do cong cu tao (khong ghi de)"] += 1
            self.out("  [BO QUA] %s: da co file cung ten khong do cong cu tao" % rel)
            return
        if self.dry_run:
            st["downloaded"] += 1
            self.out("  [SE TAI] %s  (%s)  <- %s" % (rel, c["license_id"], c["url"]))
            return
        data = self.fetch(c["url"])
        bad = hash_mismatch(data, want) if want else None
        if bad:
            st["rejected"]["hash_mismatch"] += 1
            self.rejected_notes["hash_mismatch"].append("%s (%s): %s" % (c["slug"], c["system"], bad))
            self.out("  [LOAI] %s: hash catalog sai (%s)" % (c["slug"], bad))
            return
        sha256 = hashlib.sha256(data).hexdigest()
        atomic_write(path, data)
        record = {"slug": c["slug"], "title": c["title"], "system": c["system"], "file": rel, "sha256": sha256,
                  "size": len(data), "license": c["license"], "license_assets": c["license_assets"],
                  "author": c["author"], "source_url": c["url"], "repository": c["repository"],
                  "typetag": c["typetag"], "downloaded_at": self._iso()}
        lr = self.lr.get(hashlib.sha1(data).hexdigest())
        if lr:
            record["libretro_name"] = lr
            st["libretro"] += 1
        self.records[c["slug"]] = record
        taken[rel.lower()] = c["slug"]
        self.save_catalog()  # ngay sau moi file: mat dien giua chung van con dau vet
        st["downloaded"] += 1
        self.out("  [OK] %s  (%s)" % (rel, c["license_id"]))
        atomic_write(path.with_name(path.stem + ".license.txt"), self.license_text(record).encode("utf-8"))

    @staticmethod
    def license_text(r):
        return _license_text(r)

    def report(self):
        o = self.out
        o("")
        o("=== BAO CAO%s ===" % (" (DRY-RUN: khong tai, khong ghi ROM)" if self.dry_run else ""))
        tot = collections.Counter()
        for s in self.systems:
            st = self.stats[s]
            rej = sum(st["rejected"].values())
            o("%-4s nhan %4d | loai %4d | loi tai %d | %s %d, khong doi %d, bo qua %d%s" % (
                s, st["accepted"], rej, st["errors"], "se tai" if self.dry_run else "da tai", st["downloaded"],
                st["unchanged"], sum(st["skipped"].values()),
                ", khop libretro %d" % st["libretro"] if self.libretro else ""))
            for reason, n in st["rejected"].most_common():
                o("       loai - %s: %d" % (REASON_TEXT.get(reason, reason), n))
            for reason, n in st["skipped"].items():
                o("       bo qua - %s: %d" % (reason, n))
            tot.update({"accepted": st["accepted"], "rejected": rej, "errors": st["errors"]})
        o("TONG nhan %d | loai %d | loi tai %d" % (tot["accepted"], tot["rejected"], tot["errors"]))
        for reason in ("unrecognized_license", "hash_mismatch", "downloads_disabled", "unsafe_entry"):
            if self.rejected_notes.get(reason):
                o("%s:" % REASON_TEXT[reason])
                for n in self.rejected_notes[reason]:
                    o("  - " + n)
        if self.error_notes:
            o("Loi tai:")
            for n in self.error_notes:
                o("  - " + n)


def main(argv=None, fetch=None, out=print):
    ap = argparse.ArgumentParser(description="Thu thap homebrew co giay phep ro rang tu Homebrew Hub vao thu vien ROM.")
    ap.add_argument("--dest", required=True, help="thu muc thu vien (vd. mot library.roots cua PocketBridge)")
    ap.add_argument("--systems", default=",".join(s.lower() for s in SYSTEMS), help="gb,gbc,gba,nes (mac dinh: ca bon)")
    ap.add_argument("--dry-run", action="store_true", help="chi liet ke + bao cao; khong tai ROM, khong ghi vao dest (tru cache)")
    ap.add_argument("--limit", type=int, help="chi xu ly N muc dau (sau khi loc giay phep)")
    ap.add_argument("--refresh", action="store_true", help="bo qua cache JSON 24 gio")
    ap.add_argument("--force", action="store_true", help="tai lai ca file da co (van khong ghi de file sua tay/la)")
    ap.add_argument("--cache-dir", help="mac dinh <dest>/.homebrew-cache")
    ap.add_argument("--libretro-homebrew-dat", action="store_true",
                    help="doi chieu SHA-1 voi DAT homebrew cua libretro-database (chi de thong tin)")
    a = ap.parse_args(argv)
    systems = [s.strip().upper() for s in a.systems.split(",") if s.strip()]
    bad = [s for s in systems if s not in SYSTEMS]
    if bad or not systems or (a.limit is not None and a.limit < 0):
        out("Tham so sai: --systems chi nhan %s; --limit >= 0." % ",".join(s.lower() for s in SYSTEMS))
        return 2
    sy = Syncer(a.dest, fetch or HttpFetcher(), systems, a.dry_run, a.limit, a.cache_dir, a.refresh, a.force,
                a.libretro_homebrew_dat, out)
    try:
        code = sy.run()
    except FetchError as e:
        out("LOI: %s" % e)
        return 2
    sy.report()
    return code


if __name__ == "__main__":
    sys.exit(main())
