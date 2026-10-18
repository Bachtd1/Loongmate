"""
LoongMate Agent: tien trinh nen cho Leaf service (pak LoongMateAgent.pak).
Moi 5 phut ghi 1 mau pin/nhiet (telemetry.py) va tu sao luu khi thoat game
(autobackup.py). Chi dung stdlib; chay bang `python3 -m engine.agent ...`
voi PYTHONPATH tro toi thu muc chua goi `engine` (LoongMate.pak).

Che do:
    --run                  vong lap foreground (Leaf service goi che do nay)
    --watch-debug          chi IN su kien phat hien duoc (log + tien trinh),
                           khong sao luu that; de nguoi dung choi thu 1 game
    (khong co co)          in trang thai agent roi thoat (bam app o tab Apps)
Tuy chon: --userdata DIR, --primary DIR, --secondary DIR, --leaf-log FILE,
    --duration GIAY (tu thoat sau N giay; de kiem chung), --interval 300,
    --poll 5, --min-interval 600, --settle 10.

Luon dung khoa <userdata>/agent.lock: khong bao gio chay hai ban (hai ban
tranh tai nguyen se lam service fail lap -> backoff -> breaker mo).
"""

import argparse
import datetime
import json
import os
import hashlib
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import autoresume, leafconfig, pocketlock, telemetry
from .autobackup import GAME_ACTIVE_MAX_S, AutoBackup, Event, LogWatcher, PidLock, ProcWatcher, pid_lock_held
from .probe import PlatformProbe
from .savevault import SaveVault

LEAF_LOG_NAME = "umrk-leaf-session.log"
STATE_NAME = "agent_state.json"
LOCK_NAME = "agent.lock"
EXIT_LOCKED = 3
EXIT_RELOAD = 75            # tu thoat sau khi cap nhat; GIA DINH (chua kiem chung) supervisor Leaf restart voi ma != 0
VERSION_NAME = "installed-version.json"
MAINT_EVERY_S = 30          # khoang giua hai lan xet viec nen (reload / verify / prewarm)
VERSION_STABLE_S = 10       # installed-version.json phai khong doi it nhat chung nay truoc khi reload
RELOAD_GUARD_S = 600        # da reload sang dung version nay trong chung nay -> khong reload nua
VERIFY_EVERY_S = 7 * 86400
VERIFY_MIN_PCT = 50
PREWARM_EVERY_S = 86400
PREWARM_IDLE_S = 600        # cho yen sau lan choi game cuoi
PREWARM_RETRY_S = 600       # luot dang do (bi ngat) thi doi chung nay moi quet/bam tiep
MAX_BAD_OBJECTS = 20
CONFIG_SNAPSHOT_EVERY_S = 86400   # sao luu cau hinh Leaf/RetroArch moi ngay (bo qua neu khong doi); Leaf doi phien ban thi lam ngay
_OBJ_RE = re.compile(r"^[0-9a-f]{64}$")
LOG_KEEP_DAYS = 14
_LOG_RE = re.compile(r"^agent_(\d{8})\.log$")


class _Stop(BaseException):
    pass


def _leaf_log_path(args: argparse.Namespace, probe: PlatformProbe) -> Path:
    if args.leaf_log:
        return Path(args.leaf_log)
    logs = os.environ.get("LOGS_PATH")
    if logs:
        return Path(logs) / LEAF_LOG_NAME
    base = probe.primary_path or Path("/mnt/sdcard")
    return base / ".userdata" / (os.environ.get("PLATFORM") or "mlp1") / "logs" / LEAF_LOG_NAME


def make_logger(app_data: Path):
    logdir = app_data / "logs"

    def log(msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        try:
            logdir.mkdir(parents=True, exist_ok=True)
            with open(logdir / f"agent_{time.strftime('%Y%m%d')}.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return log


def prune_logs(app_data: Path, keep_days: int = LOG_KEEP_DAYS, today: Optional[datetime.date] = None) -> List[str]:
    """Xoay vong log: giu `keep_days` ngay lich gan nhat (hom nay + keep_days-1 ngay truoc), xoa cac file
    agent_YYYYMMDD.log cu hon. Dua vao NGAY TRONG TEN FILE (mtime tren the FAT32 chi chinh xac 2 giay va
    co the lech). File khac ten/ten ngay khong hop le thi khong dong den. Tra ve ten file da xoa."""
    logdir = app_data / "logs"
    removed: List[str] = []
    cutoff = (today or datetime.date.today()) - datetime.timedelta(days=keep_days - 1)
    try:
        names = sorted(os.listdir(logdir))
    except OSError:
        return removed
    for name in names:
        m = _LOG_RE.match(name)
        if not m:
            continue
        try:
            day = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                (logdir / name).unlink()
                removed.append(name)
            except OSError:
                pass
    return removed


def _read_state(app_data: Path) -> Dict[str, Any]:
    try:
        state = json.loads((app_data / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(app_data: Path, patch: Dict[str, Any]) -> None:
    path = app_data / STATE_NAME
    state = _read_state(app_data)
    state.update(patch)
    tmp = path.with_name(path.name + ".partial")
    try:
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass

# --------------------------------------------------------------------------- viec nen (dot D)

class _Stopped(Exception):
    """Ngat viec nen vi game bat dau / rut sac / nhan tin hieu dung."""


class GameState:
    """Theo doi 'dang co game' tu cac su kien start/finish (cung quy tac AutoBackup.game_active)."""

    def __init__(self):
        self._since: Optional[float] = None

    def feed(self, events: List[Event]) -> None:
        for ev in events:
            if ev.kind == "start":
                self._since = time.monotonic()
            elif ev.kind == "finish":
                self._since = None

    def active(self) -> bool:
        return self._since is not None and time.monotonic() - self._since < GAME_ACTIVE_MAX_S


def read_installed_version(app_data: Path):
    """(version | None, mtime | None) cua <app_data>/installed-version.json; hong/thieu -> (None, None)."""
    path = Path(app_data) / VERSION_NAME
    try:
        mtime = path.stat().st_mtime
        v = json.loads(path.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None, None
    return (v if isinstance(v, str) and v else None), mtime


def hash_file_interruptible(path: Path, should_stop: Callable[[], bool], chunk: int = 1 << 18) -> str:
    """SHA-256 (cung ket qua compute_sha256) nhung hoi should_stop() sau moi chunk; nem _Stopped neu phai ngat."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if should_stop():
                raise _Stopped()
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def _referenced_objects(vault_dir: Path) -> set:
    """sha256 duoc cac snapshot layout 'objects' tham chieu (cung cach SaveVault._gc_objects). Chi doc."""
    ref = set()
    snaps = vault_dir / "snapshots"
    try:
        dirs = list(snaps.iterdir())
    except OSError:
        return ref
    for d in dirs:
        if not d.is_dir() or d.name.endswith(".partial"):
            continue
        try:
            data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("layout") != "objects":
            continue
        for info in (data.get("files") or {}).values():
            sha = info.get("sha256") if isinstance(info, dict) else None
            if isinstance(sha, str) and _OBJ_RE.match(sha):
                ref.add(sha)
    return ref


def verify_vault_objects(vault_dir: Path, should_stop: Callable[[], bool], sleep: Callable[[float], None] = time.sleep,
                         cursor: Optional[Dict[str, Any]] = None, wall: Callable[[], float] = time.time,
                         clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Bam lai moi object trong <vault_dir>/objects va so voi ten file (= sha256). CHI DOC: khong sua/xoa gi.

    SaveVault.verify_snapshot khong dung duoc o day: no bam theo tung snapshot (object chung bi bam lap nhieu lan),
    khong ngat giua chung duoc va dem ca file legacy. Ham nay di theo object, ngat duoc, tiep tuc duoc qua `cursor`.
    Tra ve {"ts","ok","checked","bad_objects"(<=20),"missing_objects","partial","cursor"}; cursor=None khi xong.
    missing_objects = object duoc snapshot tham chieu nhung khong con tren dia (tinh lai moi lan goi)."""
    vault_dir = Path(vault_dir)
    objects = vault_dir / "objects"
    cur = cursor or {}
    after = cur.get("after") if isinstance(cur.get("after"), str) else ""
    checked = int(cur.get("checked") or 0)
    bad = [b for b in (cur.get("bad") or []) if isinstance(b, str)][:MAX_BAD_OBJECTS]
    bad_n = max(int(cur.get("bad_n") or 0), len(bad))

    names = []
    try:
        shards = sorted(os.listdir(objects))
    except OSError:
        shards = []
    for sh in shards:
        try:
            for n in os.listdir(objects / sh):
                if _OBJ_RE.match(n):
                    names.append(n)
        except OSError:
            continue
    names.sort()

    partial = False
    for n in names:
        if n <= after:
            continue
        if should_stop():
            partial = True
            break
        t0 = clock()
        try:
            digest = hash_file_interruptible(objects / n[:2] / n, should_stop)
        except _Stopped:
            partial = True
            break
        except OSError:
            digest = None
        checked += 1
        after = n
        if digest != n:
            bad_n += 1
            if len(bad) < MAX_BAD_OBJECTS:
                bad.append(n)
        sleep(min(0.25, max(0.005, clock() - t0)))     # ~50% chu ky lam viec: nhuong IO cho game/he thong

    missing = sum(1 for sha in _referenced_objects(vault_dir) if not (objects / sha[:2] / sha).exists())
    return {"ts": int(wall()), "ok": bad_n == 0 and missing == 0, "checked": checked, "bad_objects": bad,
            "missing_objects": missing, "partial": partial,
            "cursor": {"after": after, "checked": checked, "bad": bad, "bad_n": bad_n} if partial else None}


class _CandidateCollector:
    """Gia lam HashCache de lay DANH SACH ung vien ma dedupe.find_duplicates se bam (cung tieu chi, goi API cong khai):
    khong doc file, chi gom duong dan; hash tra ve la gia tri gia (ket qua find_duplicates bi bo)."""

    def __init__(self, inner, should_stop: Callable[[], bool]):
        self.inner, self.should_stop, self.paths, self.hits, self.misses = inner, should_stop, [], 0, 0

    def hash(self, path, compute):
        if self.should_stop():
            raise _Stopped()
        self.paths.append(str(path))
        self.misses += 1
        return self.inner.get(path) or "0" * 64

    def flush(self):
        pass


def prewarm_hashes(cache, inventory: Dict[str, Any], catalog: Dict[str, Any], primary, secondary,
                   should_stop: Callable[[], bool], sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Bam (qua `cache`) cac ROM ma dedupe.find_duplicates se bam. Dung ngay khi should_stop(). Tra ve
    {"hashed","remaining","done"}. File loi doc (OSError) bi bo qua, khong tinh vao remaining."""
    from . import dedupe
    col = _CandidateCollector(cache, should_stop)
    try:
        dedupe.find_duplicates(inventory, catalog, primary, secondary, cache=col)
    except _Stopped:
        return {"hashed": 0, "remaining": None, "done": False}
    seen, todo = set(), []
    for p in col.paths:
        if p not in seen:
            seen.add(p)
            if cache.get(p) is None:
                todo.append(p)
    hashed, processed, stopped = 0, 0, False
    for p in todo:
        if should_stop():
            stopped = True
            break
        t0 = clock()
        try:
            cache.hash(p, lambda path: hash_file_interruptible(path, should_stop))
            hashed += 1
        except _Stopped:
            stopped = True
            break
        except OSError:
            pass
        processed += 1
        if processed % 10 == 0:
            cache.flush()
        sleep(min(1.0, (clock() - t0) * 0.5))
    cache.flush()
    remaining = len(todo) - processed
    return {"hashed": hashed, "remaining": remaining, "done": not stopped and remaining == 0}


class Maintenance:
    """Viec nen chi chay khi KHONG co game: tu nap lai sau cap nhat, kiem tra toan ven SaveVault hang tuan,
    bam truoc ROM khi dang sac. Moi thu inject duoc (dong ho, ngu, pin, game) de test khong cho that.

    game_busy(): tra True neu dang co game (nen tu poll detector). backup_pending(): AutoBackup dang cho/sap chay.
    battery(): dict kieu PlatformProbe.get_battery_info. scan(): (inventory, catalog) cho prewarm.
    extra_stop(): True khi nhan tin hieu dung."""

    def __init__(self, app_data: Path, game_busy: Callable[[], bool], battery: Callable[[], Dict[str, Any]],
                 log: Callable[[str], None] = print, vault_dir: Optional[Path] = None,
                 backup_pending: Callable[[], bool] = lambda: False, scan: Optional[Callable[[], Any]] = None,
                 paths: Any = (None, None), extra_stop: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, nice: Optional[Callable[[int], Any]] = None,
                 stop_check_s: float = 0.5):
        self.app_data, self.game_busy, self.battery, self.log = Path(app_data), game_busy, battery, log
        self.vault_dir = Path(vault_dir) if vault_dir else None
        self.backup_pending, self.scan, self.paths, self.extra_stop = backup_pending, scan, paths, extra_stop
        self.clock, self.wall, self.sleep, self.nice, self.stop_check_s = clock, wall, sleep, nice, stop_check_s
        self.start_version, _ = read_installed_version(self.app_data)
        self._last_game = clock()        # luc khoi dong coi nhu vua co game (chua biet) -> prewarm doi PREWARM_IDLE_S
        self._next_tick: Optional[float] = None
        self._next_prewarm = 0.0
        self._noted: Dict[str, float] = {}
        self._next_cfg_try = 0.0
        self._niced = False

    # ---- tien ich
    def note_game(self) -> None:
        self._last_game = self.clock()

    def _note(self, key: str, msg: str, every: float = 3600) -> None:
        t = self.clock()
        if key not in self._noted or t - self._noted[key] >= every:
            self._noted[key] = t
            self.log(msg)

    def _busy(self) -> Optional[str]:
        if self.game_busy():
            self.note_game()
            return "dang co game"
        if self.backup_pending():
            return "sao luu tu dong dang cho/chay"
        return None

    def _battery(self) -> Dict[str, Any]:
        try:
            return self.battery() or {}
        except Exception:
            return {}

    def _charging(self) -> bool:
        return str(self._battery().get("status") or "").lower() in ("charging", "full")

    def _stopper(self, extra: Callable[[], bool] = lambda: False) -> Callable[[], bool]:
        """should_stop() rate-limit moi stop_check_s (poll log/pin khong can moi chunk); da True thi giu True."""
        st = {"t": None, "v": False}

        def stop() -> bool:
            if st["v"]:
                return True
            t = self.clock()
            if st["t"] is not None and t - st["t"] < self.stop_check_s:
                return False
            st["t"] = t
            if self.extra_stop() or self.game_busy() or extra():
                st["v"] = True
                self.note_game()
            return st["v"]
        return stop

    def _heavy_start(self) -> None:
        if not self._niced and self.nice:
            self._niced = True
            try:
                self.nice(10)
            except (OSError, ValueError):
                pass

    # ---- tick
    def tick(self) -> Optional[str]:
        """Goi thuong xuyen; tu gioi han moi MAINT_EVERY_S. Tra ve "reload" khi agent nen thoat ma EXIT_RELOAD."""
        t = self.clock()
        if self._next_tick is not None and t < self._next_tick:
            return None
        self._next_tick = t + MAINT_EVERY_S
        if self._check_reload():
            return "reload"
        if self._busy() is None:
            if not self._maybe_config_snapshot() and not self._maybe_verify():
                self._maybe_prewarm()
        return None

    def _maybe_config_snapshot(self) -> bool:
        """Sao luu cau hinh Leaf/RetroArch (engine/leafconfig.py) moi ngay; khi phien ban Leaf khac lan truoc thi chup ngay ban
        "sau cap nhat" va ghi state leaf_updated {from, to, before_id, after_id} de giao dien goi y xem khac biet."""
        sd = self.paths[0] if self.paths else None
        if not sd or self.clock() < self._next_cfg_try:
            return False
        plat = os.environ.get("PLATFORM") or "mlp1"
        st = _read_state(self.app_data).get("leafconfig")
        st = st if isinstance(st, dict) else {}
        ver = leafconfig.leaf_version(sd, plat)
        updated = bool(st.get("leaf_version")) and bool(ver) and st.get("leaf_version") != ver
        if not updated and 0 <= self.wall() - (st.get("ts") or 0) < CONFIG_SNAPSHOT_EVERY_S:
            return False
        label = f"sau cập nhật Leaf {st.get('leaf_version')} -> {ver}" if updated else "tự động"
        try:
            r = leafconfig.snapshot(self.app_data, sd, plat, label=label, force=updated)
        except (OSError, leafconfig.ConfigError) as e:
            self._note("leafcfg-err", f"Sao luu cau hinh Leaf loi: {e}", 6 * 3600)
            r = {"success": False}
        if not r.get("success"):
            self._next_cfg_try = self.clock() + 3600      # khong thay cau hinh / loi: khong ghi state, mot gio sau thu lai
            return False
        patch: Dict[str, Any] = {"leafconfig": {"ts": int(self.wall()), "leaf_version": ver or st.get("leaf_version"),
                                                "last_id": r.get("id") or st.get("last_id")}}
        if updated:
            patch["leaf_updated"] = {"from": st.get("leaf_version"), "to": ver, "ts": int(self.wall()),
                                     "before_id": st.get("last_id"), "after_id": r.get("id")}
            self.log(f"Leaf doi phien ban {st.get('leaf_version')} -> {ver}: da chup cau hinh sau cap nhat ({r.get('id')}), "
                     f"ban truoc la {st.get('last_id')}.")
        elif r.get("created"):
            self.log(f"Sao luu cau hinh Leaf: {r.get('message')}")
        _write_state(self.app_data, patch)
        return bool(r.get("created"))

    def _check_reload(self) -> bool:
        cur, mtime = read_installed_version(self.app_data)
        if cur is None or cur == self.start_version or mtime is None:
            return False
        if self.wall() - mtime < VERSION_STABLE_S:
            return False           # file moi ghi / chua on dinh
        why = self._busy()
        if why:
            self._note("reload-wait", f"Co ban cap nhat {self.start_version} -> {cur} nhung chua nap lai: {why}.", 600)
            return False
        prev = _read_state(self.app_data).get("reload")
        if isinstance(prev, dict) and prev.get("to") == cur and 0 <= self.wall() - (prev.get("ts") or 0) < RELOAD_GUARD_S:
            self._note("reload-loop", f"CANH BAO: da nap lai sang {cur} luc {prev.get('ts')} (< {RELOAD_GUARD_S}s truoc) "
                                      f"ma version van khac luc khoi dong ({self.start_version}); KHONG thoat nua de tranh vong lap.", 600)
            return False
        self.log(f"Phat hien cap nhat {self.start_version} -> {cur}, khong co game/sao luu: thoat ma {EXIT_RELOAD} de supervisor nap ban moi.")
        _write_state(self.app_data, {"reload": {"from": self.start_version, "to": cur, "ts": int(self.wall())}})
        return True

    def _maybe_verify(self) -> bool:
        if self.vault_dir is None:
            return False
        st = _read_state(self.app_data)
        last = st.get("vault_verify")
        if isinstance(last, dict) and not last.get("partial") and \
                0 <= self.wall() - (last.get("ts") or 0) < VERIFY_EVERY_S:
            return False
        bat = self._battery()
        pct = bat.get("percent") if bat.get("available") else None
        if not (self._charging() or (isinstance(pct, (int, float)) and pct >= VERIFY_MIN_PCT)):
            self._note("verify-bat", f"Den han kiem tra SaveVault nhung pin {pct}% va khong sac: hoan lai.")
            return False
        cursor = st.get("vault_verify_cursor") if isinstance(last, dict) and last.get("partial") else None
        self._heavy_start()
        self.log("Kiem tra toan ven SaveVault: bat dau" + (" (tiep tuc)" if cursor else ""))
        t0 = self.clock()
        res = verify_vault_objects(self.vault_dir, self._stopper(), self.sleep, cursor, self.wall, self.clock)
        cur = res.pop("cursor")
        _write_state(self.app_data, {"vault_verify": res, "vault_verify_cursor": cur})
        self.log(f"Kiem tra SaveVault {'DANG DO (se tiep tuc)' if res['partial'] else 'xong'} sau {self.clock() - t0:.1f}s: "
                 f"{res['checked']} object, hong={len(res['bad_objects'])} thieu={res['missing_objects']} ok={res['ok']}")
        return True

    def _maybe_prewarm(self) -> bool:
        if self.scan is None or not self._charging():
            return False
        if self.clock() < self._next_prewarm or self.clock() - self._last_game < PREWARM_IDLE_S:
            return False
        last = _read_state(self.app_data).get("prewarm")
        if isinstance(last, dict) and last.get("done") and 0 <= self.wall() - (last.get("ts") or 0) < PREWARM_EVERY_S:
            return False
        from .hashcache import HashCache
        self._heavy_start()
        self.log("Bam truoc ROM (dang sac, khong game): bat dau")
        t0 = self.clock()
        stop = self._stopper(lambda: not self._charging())
        cache = HashCache(self.app_data)
        try:
            inv, catalog = self.scan()
            if stop():
                res = {"hashed": 0, "remaining": last.get("remaining", 0) if isinstance(last, dict) else 0, "done": False}
            else:
                res = prewarm_hashes(cache, inv, catalog, self.paths[0], self.paths[1], stop, self.sleep, self.clock)
        except Exception as e:
            self.log(f"Loi bam truoc ROM (bo qua luot nay): {e}")
            res = {"hashed": 0, "remaining": 0, "done": False}
        finally:
            cache.close()
        if res["remaining"] is None:         # bi ngat truoc khi biet con bao nhieu: giu con so lan truoc
            res["remaining"] = last.get("remaining", 0) if isinstance(last, dict) else 0
        if not res["done"]:
            self._next_prewarm = self.clock() + PREWARM_RETRY_S
        _write_state(self.app_data, {"prewarm": {"ts": int(self.wall()), **res}})
        self.log(f"Bam truoc ROM {'xong' if res['done'] else 'dung giua chung'} sau {self.clock() - t0:.1f}s: "
                 f"da bam {res['hashed']}, con {res['remaining']}")
        return True


def _default_scan(probe: PlatformProbe) -> Callable[[], Any]:
    def scan():
        from .doctor import LibraryDoctor
        from .inventory import InventoryEngine, find_systems_catalog
        inv = InventoryEngine(probe.primary_path, probe.secondary_path).scan_all()
        cat = LibraryDoctor(probe.primary_path, probe.secondary_path, find_systems_catalog(),
                            probe.userdata_path).systems_catalog
        return inv, cat
    return scan


class Detector:
    """Uu tien tin hieu log cua Leaf; neu file log khong ton tai thi theo doi tien trinh."""

    def __init__(self, leaf_log: Path, on_line=None):
        self.log = LogWatcher(leaf_log, from_end=True, on_line=on_line)
        self.proc = ProcWatcher()

    @property
    def source(self) -> str:
        return "log" if self.log.exists() else "proc"

    def poll(self) -> List[Event]:
        return self.log.poll() if self.log.exists() else self.proc.poll()


def lock_held(app_data) -> Optional[bool]:
    """True/False = agent đang giữ khoá hay không; None = chưa từng có file khoá. CHỈ ĐỌC (xem autobackup.pid_lock_held)."""
    return pid_lock_held(Path(app_data) / LOCK_NAME)


def cmd_status(app_data: Path, leaf_log: Path) -> int:
    if lock_held(app_data):
        print(f"Agent: dang chay (pid {PidLock(app_data / LOCK_NAME).holder_pid() or '?'}).")
    else:
        print("Agent: KHONG chay (hay bat service 'LoongMate Agent' trong man Services: Enable + Start).")
    rows = telemetry.load(app_data, since_ts=time.time() - 7 * 86400)
    if rows:
        last = rows[-1]
        print(f"Mau gan nhat: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last['ts']))} "
              f"pin={last['battery_pct']}% ({last['status']}) soc={last['soc_c']}C gpu={last['gpu_c']}C")
        s = telemetry.summarize(rows)
        print(f"7 ngay: {s['samples']} mau, hao pin {s['drain_pct_per_hour']} %/gio, "
              f">70C: {s['over_70_events']} lan")
    else:
        print("Chua co mau telemetry nao trong 7 ngay qua.")
    try:
        st = json.loads((app_data / STATE_NAME).read_text(encoding="utf-8"))
        print(f"Sao luu tu dong gan nhat: {st.get('last_backup')}")
    except (OSError, ValueError):
        print("Sao luu tu dong gan nhat: chua co.")
    print(f"Tin hieu thoat game: {'log Leaf' if leaf_log.exists() else 'tien trinh (log Leaf khong thay)'} [{leaf_log}]")
    return 0


class _DebugVault:
    def create_snapshot(self, destination=None, label="manual"):
        print("  -> (debug) SE GOI create_snapshot(label='auto') luc nay; khong sao luu that.", flush=True)
        return {"success": True, "snapshot_id": "DEBUG-NOT-CREATED", "files_count": 0}


def cmd_watch_debug(args: argparse.Namespace, leaf_log: Path) -> int:
    start = time.monotonic()
    wall = lambda: time.strftime("%H:%M:%S")
    log = LogWatcher(leaf_log, from_end=True)
    proc = ProcWatcher()
    print(f"[watch-debug] log={leaf_log} ton_tai={log.exists()} poll={args.poll}s duration={args.duration}s", flush=True)
    print(f"[watch-debug] tien trinh theo doi: {sorted(proc.comms)}", flush=True)
    dbg = AutoBackup(_DebugVault(), min_interval=args.min_interval, settle_s=args.settle,
                     log=lambda m: print(f"[{wall()}] {m}", flush=True))
    counts = {"log": 0, "proc": 0}
    try:
        while True:
            now = time.monotonic()
            le, pe = log.poll(), proc.poll()
            for ev in le + pe:
                counts[ev.source] += 1
                print(f"[{wall()}] {ev.source.upper():4} {ev.kind:6} id={ev.launch_id} {ev.detail}", flush=True)
            dbg.feed(le if log.exists() else pe, now)
            dbg.tick(now)
            if args.duration and now - start >= args.duration:
                break
            time.sleep(args.poll)
    except KeyboardInterrupt:
        pass
    print(f"[watch-debug] xong. Su kien: log={counts['log']} proc={counts['proc']}", flush=True)
    return 0


def cmd_run(args: argparse.Namespace, probe: PlatformProbe, app_data: Path, leaf_log: Path) -> int:
    log = make_logger(app_data)
    lock = PidLock(app_data / LOCK_NAME)
    waited = 0.0
    while not lock.acquire():
        if waited >= args.wait_lock:
            log(f"Agent khac dang chay (pid {lock.holder_pid() or '?'}); thoat de khong chay hai ban.")
            # Che do service (--wait-lock): thoat 0 de supervisor khong coi la loi -> khong backoff/breaker.
            return 0 if args.wait_lock else EXIT_LOCKED
        time.sleep(1)
        waited += 1

    removed_logs = prune_logs(app_data)
    if removed_logs:
        log(f"Da xoa log agent cu (> {LOG_KEEP_DAYS} ngay): {len(removed_logs)} file")

    busy = {"v": False}

    def on_signal(signum, frame):
        if busy["v"]:
            busy["flag"] = True      # dang sao luu: de vong lap dung sau khi xong
        else:
            raise _Stop()
    busy["flag"] = False
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass

    reload_now = False
    try:
        vault = None
        if probe.primary_path is not None and probe.userdata_path is not None:
            # Snapshot nhan "auto" duoc cat rieng theo auto_retention (mac dinh 10) nen khong day mat
            # snapshot thu cong cua nguoi dung.
            vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)
        else:
            log("Khong xac dinh duoc the Primary/userdata: chi ghi telemetry, TAT sao luu tu dong.")
        backup = AutoBackup(vault, min_interval=args.min_interval, settle_s=args.settle, log=log,
                            on_result=lambda r: _write_state(app_data, {"last_backup": {
                                "at": time.strftime("%Y-%m-%d %H:%M:%S"), "success": bool(r.get("success")),
                                "id": r.get("snapshot_id"), "error": r.get("error")}})) if vault else None
        resumer = autoresume.AutoResume(app_data, log=log)
        # Khoa khi thuc day chay o luong rieng: phai giu tay cam trong ~0.1s sau khi may thuc, vong chinh poll 1-5s.
        pocket = pocketlock.PocketLock(app_data, log=log)
        pocket_stop = threading.Event()
        threading.Thread(target=pocket.run, args=(pocket_stop,), name="pocketlock", daemon=True).start()

        def on_leaf_line(line: str) -> None:
            resumer.feed_line(line)
            pocket.feed_line(line)
        detector = Detector(leaf_log, on_line=on_leaf_line)
        game = GameState()
        ar_check = {"t": 0.0, "on": False}

        def resume_on() -> bool:            # doc co bat/tat toi da moi 30s (file nho tren the)
            if time.monotonic() - ar_check["t"] > 30:
                ar_check.update(t=time.monotonic(),
                                on=autoresume.enabled(app_data) or pocketlock.enabled(app_data))
            return ar_check["on"]

        def game_busy() -> bool:
            """Poll detector (dung cho viec nen giua chung): su kien duoc chuyen cho backup va GameState."""
            evs = detector.poll()
            if evs:
                for ev in evs:
                    log(f"su kien {ev.source}: {ev.kind} {ev.launch_id}")
                game.feed(evs)
                maint.note_game()
                if backup is not None:
                    backup.feed(evs, time.monotonic())
            return game.active()

        maint = Maintenance(
            app_data, game_busy, probe.get_battery_info, log=log,
            vault_dir=vault.vault_dir if vault else None,
            backup_pending=lambda: backup is not None and backup.pending,
            scan=_default_scan(probe) if vault else None, paths=(probe.primary_path, probe.secondary_path),
            extra_stop=lambda: busy["flag"], nice=getattr(os, "nice", None))
        _write_state(app_data, {"pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S")})
        log(f"Agent khoi dong pid={os.getpid()} userdata={app_data} tin_hieu={detector.source} log={leaf_log} "
            f"mau/{args.interval:g}s poll={args.poll:g}s")

        deadline = time.monotonic() + args.duration if args.duration else None
        next_sample = 0.0
        next_prune = 0.0
        while not busy["flag"]:
            now = time.monotonic()
            if now >= next_sample:
                try:
                    row = telemetry.sample(probe)
                    telemetry.append(app_data, row)
                    if args.verbose:
                        log(f"telemetry {row}")
                except Exception as e:
                    log(f"Loi ghi telemetry (bo qua mau nay): {e}")
                next_sample = now + args.interval
            if now >= next_prune:
                try:
                    removed = telemetry.prune(app_data)
                    if removed:
                        log(f"Da xoa telemetry cu: {removed}")
                except Exception as e:
                    log(f"Loi prune telemetry: {e}")
                prune_logs(app_data)
                next_prune = now + 86400
            events = detector.poll()
            for ev in events:
                log(f"su kien {ev.source}: {ev.kind} {ev.launch_id}")
            if events:
                game.feed(events)
                maint.note_game()
            if backup is not None:
                backup.feed(events, now)
                busy["v"] = True
                try:
                    backup.tick(now)
                finally:
                    busy["v"] = False
            busy["v"] = True        # viec nen: SIGTERM chi dat co, vong lap ngat nhanh qua extra_stop
            try:
                if maint.tick() == "reload":
                    reload_now = True
                    break
            finally:
                busy["v"] = False
            try:
                res = resumer.tick()
                if res:                         # cho man "Tu tiep tuc game" biet lan gan nhat co chay that khong
                    _write_state(app_data, {"autoresume_last": {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                                "outcome": res.get("outcome"), "rom": res.get("rom"),
                                                                "slot": res.get("slot")}})
            except Exception as e:
                log(f"Loi tu tiep tuc (bo qua): {e}")
                resumer.pending = None
            if deadline is not None and now >= deadline:
                break
            sleep_for = min(args.poll, max(0.5, next_sample - time.monotonic()))
            if resumer.pending:
                sleep_for = 0.3                 # vua mo game: cho RetroArch san sang roi nap state ngay
            elif resume_on():
                sleep_for = min(sleep_for, 1.0)     # bat tu tiep tuc: doc log moi giay (chi stat file) de bat kip luc mo game
            time.sleep(sleep_for)
    except _Stop:
        log("Nhan tin hieu dung; thoat.")
    finally:
        if "pocket_stop" in locals():
            pocket_stop.set()           # luong khoa nha tay cam ao truoc khi Agent thoat
        lock.release()
    log("Agent da dung." if not reload_now else f"Agent thoat ma {EXIT_RELOAD} de nap lai sau cap nhat.")
    return EXIT_RELOAD if reload_now else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LoongMate Agent (telemetry + tu dong sao luu khi thoat game)")
    p.add_argument("--run", action="store_true", help="Chay vong lap foreground (che do service)")
    p.add_argument("--watch-debug", action="store_true", help="In su kien thoat game phat hien duoc, khong sao luu that")
    p.add_argument("--userdata")
    p.add_argument("--primary")
    p.add_argument("--secondary")
    p.add_argument("--leaf-log", help="Duong dan umrk-leaf-session.log (mac dinh $LOGS_PATH hoac .userdata/mlp1/logs)")
    p.add_argument("--duration", type=float, default=0, help="Tu thoat sau N giay (0 = chay mai)")
    p.add_argument("--interval", type=float, default=300, help="Giay giua hai mau telemetry")
    p.add_argument("--poll", type=float, default=5, help="Giay giua hai lan kiem tra thoat game (>=5 de tiet kiem pin)")
    p.add_argument("--min-interval", type=float, default=600, help="Debounce giua hai lan sao luu tu dong")
    p.add_argument("--settle", type=float, default=10, help="Giay cho emulator ghi xong save truoc khi sao luu")
    p.add_argument("--wait-lock", type=float, default=0,
                   help="Neu khoa dang bi giu, doi toi da N giay (ban cu dang tat) roi thoat 0; 0 = thoat ngay ma 3")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    probe = PlatformProbe(args.primary, args.secondary, args.userdata)
    if probe.userdata_path is None:
        print("Khong xac dinh duoc thu muc userdata.", file=sys.stderr)
        return 2
    app_data = probe.userdata_path
    app_data.mkdir(parents=True, exist_ok=True)
    leaf_log = _leaf_log_path(args, probe)
    if args.watch_debug:
        return cmd_watch_debug(args, leaf_log)
    if args.run:
        return cmd_run(args, probe, app_data, leaf_log)
    return cmd_status(app_data, leaf_log)


if __name__ == "__main__":
    sys.exit(main())
