"""Khoá khi thức dậy (chống bấm trong túi quần). Leaf chưa có khoá màn hình: để máy trong túi, nút nguồn bị cấn là máy
thức, sau đó các nút khác bị đè làm máy lướt menu, mở game... LoongMate Agent khoá lại mà KHÔNG sửa file nào của Leaf.

Cơ chế (đọc từ mã Jawaka, kiểm trên máy Leaf 0.13.0):
  * jawakad giữ độc quyền tay cầm thật (EVIOCGRAB) và chép phím sang một tay cầm ảo uinput cùng tên "Loong Gamepad"
    (Sysfs dưới /devices/virtual/). Launcher, RetroArch và mọi app đọc tay cầm ảo này. Agent giữ độc quyền tay cầm ảo thì
    không app nào nhận phím; Agent đọc phím để nhận tổ hợp mở khoá. Nút nguồn là thiết bị riêng do jawakad xử lý nên
    vẫn dùng được: bấm là ngủ, giữ 2 giây là tắt máy. Agent chết hay thoát giữa chừng thì kernel tự nhả (an toàn khi lỗi).
  * Biết máy vừa thức: CLOCK_BOOTTIME - CLOCK_MONOTONIC là tổng thời gian đã ngủ, tăng vọt sau mỗi lần suspend. Lần tắt
    màn do để lâu (standby, chưa ngủ hẳn) thì bấm nút nào Leaf cũng bật màn và ghi "standby: wake on input" vào log.
  * Không mở khoá trong RESLEEP_S giây: gửi {"type":"platform-action","action":"sleep"} cho jawakad (máy ngủ lại),
    vẫn giữ khoá cho lần thức sau.
Tay cầm thật không đánh thức được máy (kiểm power/wakeup trên máy): chỉ nút nguồn, cắm sạc và RTC.
Mặc định TẮT; bật/tắt bằng <app_data>/pocketlock.json ({"enabled": true})."""
import json
import os
import re
import select
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine import autoresume

CONFIG_NAME = "pocketlock.json"
EV_KEY = 1
BTN_SELECT = 314
BTN_START = 315
UNLOCK_HOLD_S = 1.0
RESLEEP_S = 10.0                 # thức mà không mở khoá trong ngần này giây thì ngủ lại
WAKE_GAP_S = 1.0                 # tổng thời gian ngủ tăng ít nhất ngần này mới tính là vừa thức
CONFIG_RECHECK_S = 5.0
EVIOCGRAB = 0x40044590           # _IOW('E', 0x90, int)
_EVENT = struct.Struct("llHHi")  # struct input_event (aarch64: 24 byte)
_STANDBY_WAKE = "standby: wake on input"


def enabled(app_data) -> bool:
    try:
        return bool(json.loads((Path(app_data) / CONFIG_NAME).read_text(encoding="utf-8")).get("enabled"))
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(app_data, on: bool) -> Dict[str, Any]:
    p = Path(app_data) / CONFIG_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial")
    tmp.write_text(json.dumps({"enabled": bool(on)}), encoding="utf-8")
    os.replace(tmp, p)
    return {"success": True, "enabled": bool(on),
            "message": ("BẬT" if on else "TẮT") + " khoá khi thức dậy (qua LoongMate Agent; Agent phải đang chạy)."}


def find_virtual_pad(proc_devices: str) -> Optional[str]:
    """/dev/input/eventN của tay cầm ảo jawakad tạo (cùng tên với tay cầm thật, khác ở Sysfs /devices/virtual/)."""
    for block in re.split(r"\n\s*\n", proc_devices):
        name = re.search(r'^N: Name="([^"]*)"', block, re.M)
        sysfs = re.search(r"^S: Sysfs=(\S+)", block, re.M)
        ev = re.search(r"^H: Handlers=.*?\b(event\d+)\b", block, re.M)
        if name and sysfs and ev and name.group(1) == "Loong Gamepad" and sysfs.group(1).startswith("/devices/virtual/"):
            return "/dev/input/" + ev.group(1)
    return None


def suspended_seconds() -> float:
    boot = getattr(time, "CLOCK_BOOTTIME", None)      # chỉ Linux; nơi khác coi như không bao giờ ngủ
    return time.clock_gettime(boot) - time.monotonic() if boot is not None else 0.0


class EvdevPad:
    """Tay cầm ảo thật trên máy. grab(True) = không app nào nhận phím."""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as f:
                path = find_virtual_pad(f.read())
            if path is None:
                raise OSError("không thấy tay cầm ảo của Leaf (Loong Gamepad, /devices/virtual)")
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    def grab(self, on: bool) -> None:
        import fcntl                                  # chỉ có trên Linux; import ở đây để test chạy được trên PC
        fcntl.ioctl(self.fd, EVIOCGRAB, 1 if on else 0)

    def read(self, timeout: float) -> List[Tuple[int, int, int]]:
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        try:
            data = os.read(self.fd, _EVENT.size * 64)
        except BlockingIOError:
            return []
        out = []
        for i in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
            _s, _us, typ, code, value = _EVENT.unpack_from(data, i)
            out.append((typ, code, value))
        return out

    def close(self) -> None:
        try:
            self.grab(False)
        except OSError:
            pass
        os.close(self.fd)


def _sleep_async(req: Dict[str, Any]) -> None:
    # jawakad chỉ trả lời khi máy đã thức lại: không chờ, không chặn vòng khoá.
    threading.Thread(target=autoresume.ipc_request, args=(req,), kwargs={"timeout": 2.0}, daemon=True).start()


class PocketLock:
    """step() chạy liên tục (~10 lần/giây) trong luồng riêng của Agent; mọi phụ thuộc inject được để test không cần máy."""

    def __init__(self, app_data, open_pad: Callable[[], Any] = EvdevPad,
                 request: Callable[[Dict[str, Any]], Any] = _sleep_async,
                 clock: Callable[[], float] = time.monotonic, suspended_s: Callable[[], float] = suspended_seconds,
                 log: Callable[[str], None] = print, is_enabled: Optional[Callable[[], bool]] = None):
        self.app_data = Path(app_data)
        self.open_pad, self.request, self.clock, self.suspended_s, self.log = open_pad, request, clock, suspended_s, log
        self.is_enabled = is_enabled or (lambda: enabled(self.app_data))
        self.pad = None
        self.last_gap: Optional[float] = None
        self.deadline: Optional[float] = None       # lúc cho máy ngủ lại nếu chưa mở khoá
        self.held: Dict[int, bool] = {}
        self.both_since: Optional[float] = None
        self.next_config = 0.0
        self._standby_wake = threading.Event()

    @property
    def locked(self) -> bool:
        return self.pad is not None

    def feed_line(self, line: str) -> None:
        """Dòng log của Leaf (gọi từ luồng chính của Agent)."""
        if _STANDBY_WAKE in line:
            self._standby_wake.set()

    def _woke(self) -> bool:
        gap = self.suspended_s()
        woke = self.last_gap is not None and gap - self.last_gap >= WAKE_GAP_S
        self.last_gap = gap
        if self._standby_wake.is_set():
            self._standby_wake.clear()
            woke = True
        return woke

    def _lock(self, now: float) -> None:
        if self.pad is None:
            try:
                pad = self.open_pad()
                pad.grab(True)
            except OSError as e:
                self.log(f"khoa khi thuc day: khong giu duoc tay cam ao ({e}); bo qua lan nay.")
                return
            self.pad = pad
            self.log("khoa khi thuc day: da khoa (giu Select + Start 1 giay de mo).")
        self.held.clear()
        self.both_since = None
        self.deadline = now + RESLEEP_S

    def _unlock(self, why: str) -> None:
        if self.pad is not None:
            for fn in (lambda: self.pad.grab(False), self.pad.close):
                try:
                    fn()
                except OSError:
                    pass
            self.pad = None
            self.log(f"khoa khi thuc day: mo khoa ({why}).")
        self.deadline = None
        self.held.clear()
        self.both_since = None

    def step(self, timeout: float = 0.0) -> None:
        now = self.clock()
        if self._woke() and self.is_enabled():
            self._lock(now)
            self.next_config = now + CONFIG_RECHECK_S
        if self.pad is None:
            return
        if now >= self.next_config:
            self.next_config = now + CONFIG_RECHECK_S
            if not self.is_enabled():
                self._unlock("da tat tinh nang")
                return
        try:
            events = self.pad.read(timeout)
        except OSError as e:
            self._unlock(f"loi doc tay cam: {e}")
            return
        now = self.clock()
        for typ, code, value in events:
            if typ == EV_KEY and code in (BTN_SELECT, BTN_START):
                self.held[code] = value != 0          # 1 nhấn, 2 giữ (auto-repeat), 0 nhả
        if self.held.get(BTN_SELECT) and self.held.get(BTN_START):
            if self.both_since is None:
                self.both_since = now
            elif now - self.both_since >= UNLOCK_HOLD_S:
                self._unlock("Select + Start")
                return
        else:
            self.both_since = None
        if self.deadline is not None and now >= self.deadline:
            self.log("khoa khi thuc day: khong mo khoa, cho may ngu lai.")
            self.request({"type": "platform-action", "action": "sleep"})
            self.deadline = now + RESLEEP_S           # Leaf hoãn ngủ (đang sạc...) thì thử lại; thức lại thì _lock đặt lại

    def stop(self) -> None:
        self._unlock("Agent dung")

    def run(self, stop: threading.Event) -> None:
        """Vòng lặp của luồng khoá. Mọi lỗi bất ngờ đều nhả tay cầm rồi thử lại, không bao giờ giữ phím khi đã lỗi."""
        on, recheck = False, 0.0
        try:
            while not stop.is_set():
                if self.pad is None and self.clock() >= recheck:
                    on, recheck = self.is_enabled(), self.clock() + CONFIG_RECHECK_S
                if self.pad is None and not on:
                    self.last_gap = None                      # tắt: không theo dõi; bật lại thì lấy mốc mới
                    stop.wait(CONFIG_RECHECK_S)
                    continue
                try:
                    self.step(timeout=0.1)
                except Exception as e:                # noqa: BLE001 - luồng nền: lỗi gì cũng phải nhả phím
                    self.log(f"khoa khi thuc day: loi ({e}); nha tay cam.")
                    self._unlock("loi")
                if self.pad is None:
                    stop.wait(0.1)
        finally:
            self.stop()
