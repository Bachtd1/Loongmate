"""
Tu dong sao luu khi thoat game (khong sua Leaf) + khoa tien trinh cho agent.

TIN HIEU (da doc tren may that 2026-10-02, umrk-leaf-session.log, 61 phien game):
  "... INFO life1: active launch committed launch_id=<id> source=.. kind=RETROARCH|EMULATOR"  -> game bat dau
  "... INFO life1: game.finish launch_id=<id> after writer barrier"                              -> game ket thuc
Ca 61/61 phien (RetroArch lan emulator rieng le: mupen64plus, flycast, drastic...)
deu co du cap. "after writer barrier" nghia la chinh Leaf da doi emulator ghi
xong save. Du phong khi file log khong ton tai: theo doi /proc/*/comm theo
EMULATOR_COMMS (chi la du phong; ten tien trinh doc tu thu muc bin cua tung
emulator tren may).
CHUA KIEM CHUNG khi dang co game chay that duoi agent: dung `agent.py --watch-debug`.

API:
    parse_event(line) -> (kind, launch_id) | None      kind: "start" | "finish"
    LogWatcher(path, from_end=True).poll() -> [Event]   theo doi duoi file (xoay vong/cat ngan/dong do)
    ProcWatcher(proc_root="/proc").poll() -> [Event]    du phong theo tien trinh
    PidLock(path).acquire() -> bool / .release() / .holder_pid()
        Khoa flock (tu nha khi tien trinh chet -> khong co khoa mo coi, khong
        nham PID tai su dung); pid ghi vao file chi de thong tin.
    AutoBackup(vault, min_interval=600, settle_s=10, log=print, on_result=None)
        .feed(events, now)     nhan su kien start/finish
        .tick(now) -> dict|None  chay sao luu khi den han; None neu chua/khong chay
        .game_active           True khi biet dang co game
      Quy tac: sau finish cho settle_s giay roi create_snapshot(label="auto").
      Debounce: khong chay 2 lan trong min_interval; neu finish den trong
      khoang debounce thi HOAN lai den het khoang (khong bo mat save moi),
      va khong chay trong luc game dang chay. DISK_LOW_SPACE chi canh bao 1
      lan cho den khi mot lan sao luu thanh cong.

GIOI HAN: SaveVault tu cat snapshot nhan "auto" rieng theo auto_retention (mac dinh 10, xem
SaveVault._prune_old_snapshots) nen khong day mat snapshot thu cong; AutoBackup khong tu xoa gi.
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

# comm toi da 15 ky tu; lay tu <emulators>/*/bin tren may that.
EMULATOR_COMMS = frozenset({"retroarch", "drastic64", "flycast", "mupen64plus", "yabasanshiro", "PPSSPPSDL"})

_START_RE = re.compile(r"INFO life1: active launch committed launch_id=(\S+)")
_FINISH_RE = re.compile(r"INFO life1: game\.finish launch_id=(\S+)")

# Neu khong thay finish sau chung nay thoi gian thi coi nhu co "dang choi" da cu.
GAME_ACTIVE_MAX_S = 8 * 3600


class Event(NamedTuple):
    kind: str            # "start" | "finish"
    launch_id: str
    source: str          # "log" | "proc"
    detail: str = ""


def parse_event(line: str) -> Optional[Tuple[str, str]]:
    m = _FINISH_RE.search(line)
    if m:
        return ("finish", m.group(1))
    m = _START_RE.search(line)
    if m:
        return ("start", m.group(1))
    return None


class LogWatcher:
    """Doc phan moi them vao log. Mac dinh bat dau tu cuoi file (khong phat lai lich su)."""

    MAX_READ = 256 * 1024

    def __init__(self, path: Any, from_end: bool = True, on_line: Optional[Callable[[str], None]] = None):
        self.path = Path(path)
        self.on_line = on_line          # moi dong moi (vd. engine/autoresume doc dong launch-game)
        self._ino: Optional[int] = None
        self._off = 0
        self._buf = b""
        self._seen_finish: set = set()
        try:
            st = self.path.stat()
            self._ino = st.st_ino
            self._off = st.st_size if from_end else 0
        except OSError:
            pass

    def exists(self) -> bool:
        return self.path.exists()

    def poll(self) -> List[Event]:
        try:
            st = self.path.stat()
        except OSError:
            return []
        if self._ino is not None and (st.st_ino != self._ino or st.st_size < self._off):
            # Xoay vong / cat ngan: doc lai tu dau file moi.
            self._off, self._buf = 0, b""
        self._ino = st.st_ino
        if st.st_size == self._off:
            return []
        events: List[Event] = []
        try:
            with open(self.path, "rb") as f:
                f.seek(self._off)
                chunk = f.read(self.MAX_READ)
        except OSError:
            return []
        self._off += len(chunk)
        data = self._buf + chunk
        *lines, self._buf = data.split(b"\n")   # dong cuoi chua co \n thi giu lai
        for raw in lines:
            line = raw.decode("utf-8", errors="replace")
            if self.on_line is not None:
                try:
                    self.on_line(line)
                except Exception:
                    pass                # loi cua nguoi nghe khong duoc lam hong viec theo doi game
            parsed = parse_event(line)
            if not parsed:
                continue
            kind, lid = parsed
            if kind == "finish":
                if lid in self._seen_finish:
                    continue
                self._seen_finish.add(lid)
            events.append(Event(kind, lid, "log", line.strip()[:200]))
        return events


class ProcWatcher:
    """Du phong: phat 'start' khi tu 0 -> co tien trinh emulator, 'finish' khi ve 0."""

    def __init__(self, proc_root: Any = "/proc", comms: frozenset = EMULATOR_COMMS):
        self.proc_root = Path(proc_root)
        self.comms = comms
        self._running: Dict[str, str] = {}

    def scan(self) -> Dict[str, str]:
        found: Dict[str, str] = {}
        try:
            entries = os.listdir(self.proc_root)
        except OSError:
            return found
        for name in entries:
            if not name.isdigit():
                continue
            try:
                with open(self.proc_root / name / "comm", "r", errors="replace") as f:
                    comm = f.read().strip()
            except OSError:
                continue
            if comm in self.comms:
                found[name] = comm
        return found

    def poll(self) -> List[Event]:
        now = self.scan()
        events: List[Event] = []
        if now and not self._running:
            events.append(Event("start", ",".join(sorted(now.values())), "proc", f"pids={sorted(now)}"))
        elif self._running and not now:
            events.append(Event("finish", ",".join(sorted(self._running.values())), "proc",
                                f"pids={sorted(self._running)}"))
        self._running = now
        return events

    @property
    def running(self) -> bool:
        return bool(self._running)


class PidLock:
    """Khoa exclusive khong chan. Giu den khi release() hoac tien trinh chet."""

    def __init__(self, path: Any):
        self.path = Path(path)
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            self._lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(os.getpid()).encode())
            os.fsync(fd)
        except OSError:
            pass
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            self._unlock(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def holder_pid(self) -> Optional[int]:
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _lock(fd: int) -> None:
        try:
            import fcntl
        except ImportError:  # Windows (chi dung khi phat trien/test tren PC)
            import msvcrt
            os.lseek(fd, 4096, os.SEEK_SET)   # khoa 1 byte xa vung ghi pid de doc pid van duoc
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fd: int) -> None:
        try:
            import fcntl
        except ImportError:
            import msvcrt
            os.lseek(fd, 4096, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        fcntl.flock(fd, fcntl.LOCK_UN)


class AutoBackup:
    def __init__(self, vault: Any, min_interval: float = 600, settle_s: float = 10,
                 log: Callable[[str], None] = print, on_result: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.vault = vault
        self.min_interval = min_interval
        self.settle_s = settle_s
        self.log = log
        self.on_result = on_result
        self._due: Optional[float] = None          # thoi diem du dinh chay
        self._last_run: Optional[float] = None
        self._active_since: Optional[float] = None
        self._disk_low_warned = False

    @property
    def pending(self) -> bool:
        return self._due is not None

    def game_active(self, now: float) -> bool:
        return self._active_since is not None and now - self._active_since < GAME_ACTIVE_MAX_S

    def feed(self, events: List[Event], now: float) -> None:
        for ev in events:
            if ev.kind == "start":
                self._active_since = now
            elif ev.kind == "finish":
                self._active_since = None
                self._due = now + self.settle_s
                self.log(f"Phat hien thoat game ({ev.source}, {ev.launch_id}); sao luu sau {self.settle_s:g}s.")

    def tick(self, now: float) -> Optional[Dict[str, Any]]:
        if self._due is None or now < self._due:
            return None
        if self.game_active(now):
            return None   # co game moi bat dau: de sau khi no thoat
        if self._last_run is not None and now - self._last_run < self.min_interval:
            # Debounce: hoan den het khoang chu khong bo.
            self._due = self._last_run + self.min_interval
            return None
        self._due = None
        self._last_run = now
        res = self.vault.create_snapshot(label="auto")
        if res.get("success"):
            self._disk_low_warned = False
            self.log(f"Sao luu tu dong xong: {res.get('snapshot_id')} ({res.get('files_count')} file).")
        elif res.get("error") == "DISK_LOW_SPACE":
            if not self._disk_low_warned:
                self._disk_low_warned = True
                self.log(f"CANH BAO: khong du dung luong de sao luu tu dong: {res.get('message')} "
                         f"(se khong nhac lai cho den khi sao luu duoc.)")
        else:
            self.log(f"Sao luu tu dong THAT BAI: {res.get('error')} {res.get('message')}")
        if self.on_result:
            self.on_result(res)
        return res
