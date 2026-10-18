"""Tự tiếp tục chỗ đang chơi khi mở game từ Games/Favorites — thay cho bản vá launcher của Leaf Hot Tabs, KHÔNG sửa file nào
của Leaf, nên không phải build lại mỗi khi Leaf cập nhật.

Cơ chế (đọc từ mã Jawaka, thử trên máy Leaf 0.13.0):
  * Mọi lần mở game, launcher gửi `launch-game` cho jawakad; daemon ghi log
        "INFO launch-game requested system=<s> rom=<path> core=<id> resume=<none|switcher-latest>".
    Tab Recents (và bản vá Hot Tabs) gửi resume=switcher-latest: Leaf TỰ nạp state, ta bỏ qua. Games/Favorites gửi resume=none.
  * Leaf resume bằng lệnh nạp state qua mạng SAU khi RetroArch chạy (main.c: "Resume via the post-launch network load-state
    command"). Ta làm đúng như vậy qua socket một-lần của daemon:
        {"type":"retroarch-session"}  -> active, command_ok, rom_path, core_config_folder
        {"type":"retroarch-action","action":"load-state","value":<slot>}
    Khung gói: 4 byte độ dài big-endian + JSON (internal/ipc/ipc.c). Socket chỉ root mở được (Agent chạy root).
  * Chọn state như Leaf (internal/retroarch/states.c jw_ra_find_resume_state_for_core): slot 99 (state của bộ chuyển game)
    nếu có, không thì file state mới nhất của game trong States/<core_config_folder>/ (.state, .stateN, .state.auto).
Không đăng ký LIFE-1 (`lifecycle.game: notify`) vì service tham gia vòng đời game làm bộ chuyển game phải thoát hẳn RetroArch
(docs/launch-exit-barriers.md, "Content switching") — chậm hơn.

Chỉ RetroArch (emulator độc lập không có retroarch-session -> bỏ qua). Không có state -> game chạy mới như thường.
Mặc định TẮT; bật/tắt bằng file cấu hình <app_data>/autoresume.json ({"enabled": true})."""
import json
import os
import re
import socket
import struct
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

SWITCHER_SLOT = 99
AUTO_SLOT = -1
WAIT_S = 25.0                   # từ lúc Leaf chạy game: quá lâu mà RetroArch chưa sẵn sàng thì thôi (không nạp giữa lúc đang chơi)
ARM_MAX_S = 180.0               # launch-game được xếp hàng khi có tiến trình foreground; quá lâu không thấy game chạy thì bỏ
DEFAULT_SOCKET = "/tmp/jawaka-runtime/jawakad.sock"
CONFIG_NAME = "autoresume.json"
_LAUNCH_RE = re.compile(r"INFO launch-game requested system=(\S+) rom=(.+?) core=(\S+) resume=(\S+)\s*$")
_COMMIT_RE = re.compile(r"INFO life1: active launch committed launch_id=\S+ .*?kind=(\S+)")
_SUFFIX_RE = re.compile(r"^\.state(\d*|\.auto)$")


def socket_path() -> str:
    return os.environ.get("UMRK_DAEMON_SOCKET") or DEFAULT_SOCKET


def ipc_request(obj: Dict[str, Any], path: Optional[str] = None, timeout: float = 2.0) -> Dict[str, Any]:
    """Một yêu cầu tới jawakad; lỗi kết nối/khung -> {"type": "error", "message": ...}."""
    body = json.dumps(obj).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path or socket_path())
            s.sendall(struct.pack(">I", len(body)) + body)
            hdr = b""
            while len(hdr) < 4:
                b = s.recv(4 - len(hdr))
                if not b:
                    raise OSError("daemon đóng kết nối")
                hdr += b
            n = struct.unpack(">I", hdr)[0]
            if n > 1 << 20:
                raise OSError("gói trả về quá lớn")
            data = b""
            while len(data) < n:
                b = s.recv(n - len(data))
                if not b:
                    raise OSError("daemon đóng kết nối")
                data += b
        r = json.loads(data.decode("utf-8"))
        return r if isinstance(r, dict) else {"type": "error", "message": "gói trả về không phải object"}
    except (OSError, ValueError, AttributeError) as e:
        return {"type": "error", "message": str(e)}


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
            "message": ("BẬT" if on else "TẮT") + " tự tiếp tục khi mở từ Games/Favorites (qua LoongMate Agent; Agent phải đang chạy)."}


def find_resume_slot(states_dir, core_folder: str, rom_path: str) -> Optional[Tuple[int, str]]:
    """(slot, đường dẫn) của state Leaf sẽ chọn khi resume; None nếu game chưa có state với core này."""
    if not core_folder or "/" in core_folder or "\\" in core_folder or core_folder in (".", ".."):
        return None
    core_dir = Path(states_dir) / core_folder
    base = os.path.basename(rom_path)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    p99 = core_dir / f"{stem}.state{SWITCHER_SLOT}"
    if p99.is_file():
        return SWITCHER_SLOT, str(p99)
    best: Optional[Tuple[float, int, str]] = None
    try:
        names = os.listdir(core_dir)
    except OSError:
        return None
    for name in names:
        if name.startswith(".") or not name.startswith(stem):
            continue
        m = _SUFFIX_RE.match(name[len(stem):])
        if not m:
            continue
        p = core_dir / name
        try:
            st = p.stat()
        except OSError:
            continue
        if not p.is_file():
            continue
        g = m.group(1)
        slot = AUTO_SLOT if g == ".auto" else int(g) if g else 0
        if best is None or st.st_mtime > best[0]:
            best = (st.st_mtime, slot, str(p))
    return (best[1], best[2]) if best else None


def _states_dir_for(rom_path: str, active_game: Optional[Dict[str, Any]]) -> Optional[str]:
    sp = (active_game or {}).get("states_path")
    if isinstance(sp, str) and sp:
        return sp
    i = rom_path.find("/Roms/")
    return rom_path[:i] + "/States" if i > 0 else None


def read_active_game(path: str = "/tmp/jawaka-runtime/active-game.json") -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


class AutoResume:
    """feed_line(dòng log) ghi nhận lần mở game; tick() chạy thường xuyên (vài trăm ms khi `pending`) để nạp state khi
    RetroArch sẵn sàng. Mọi phụ thuộc inject được để test không cần máy."""

    def __init__(self, app_data, request: Callable[[Dict[str, Any]], Dict[str, Any]] = ipc_request,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print,
                 active_game: Callable[[], Optional[Dict[str, Any]]] = read_active_game,
                 is_enabled: Optional[Callable[[], bool]] = None):
        self.app_data = Path(app_data)
        self.request, self.clock, self.log, self.active_game = request, clock, log, active_game
        self.is_enabled = is_enabled or (lambda: enabled(self.app_data))
        self.pending: Optional[Dict[str, Any]] = None
        self.last: Optional[Dict[str, Any]] = None       # kết quả lần gần nhất (cho trạng thái/kiểm thử)

    def feed_line(self, line: str) -> None:
        c = _COMMIT_RE.search(line)
        if c and self.pending and self.pending.get("started") is None:
            if c.group(1) == "RETROARCH":
                self.pending["started"] = self.clock()      # game thật sự chạy: bắt đầu đếm WAIT_S từ đây
            else:
                self.pending = None                         # emulator độc lập: không có retroarch-session
            return
        m = _LAUNCH_RE.search(line)
        if not m:
            return
        system, rom, _core, resume = m.groups()
        if resume != "none":
            self.pending = None                           # Leaf tự resume (Recents / launcher đã vá)
            return
        if self.is_enabled():
            # Lệnh có thể nằm chờ (launcher/app foreground chưa thoát): chỉ đếm WAIT_S khi Leaf báo game đã chạy.
            self.pending = {"system": system, "rom": rom, "armed": self.clock(), "started": None}

    def _done(self, outcome: str, msg: str, **extra) -> Dict[str, Any]:
        self.last = {"outcome": outcome, "rom": self.pending["rom"] if self.pending else None, **extra}
        self.pending = None
        self.log(f"tu tiep tuc: {msg}")
        return self.last

    def tick(self) -> Optional[Dict[str, Any]]:
        p = self.pending
        if not p:
            return None
        if p["started"] is None:
            if self.clock() - p["armed"] > ARM_MAX_S:
                return self._done("not_started", f"{p['rom']} khong thay chay sau {ARM_MAX_S:g}s; bo qua.")
            return None
        if self.clock() - p["started"] > WAIT_S:
            return self._done("timeout", f"het {WAIT_S:g}s ma RetroArch chua san sang cho {p['rom']}; bo qua (game chay moi).")
        s = self.request({"type": "retroarch-session"})
        if s.get("type") != "retroarch-session" or not s.get("active") or not s.get("command_ok"):
            return None                                   # chưa chạy / chưa nhận lệnh: thử lại lần sau
        if s.get("rom_path") != p["rom"]:
            return self._done("other_game", f"dang chay game khac ({s.get('rom_path')}); bo qua.")
        states = _states_dir_for(p["rom"], self.active_game())
        found = find_resume_slot(states, s.get("core_config_folder") or "", p["rom"]) if states else None
        if not found:
            return self._done("no_state", f"{os.path.basename(p['rom'])} chua co state voi core nay; chay moi.")
        slot, path = found
        r = self.request({"type": "retroarch-action", "action": "load-state", "value": slot})
        if r.get("type") == "ok":
            return self._done("loaded", f"da nap state slot {slot} ({path}).", slot=slot, path=path)
        return None                                       # RetroArch chưa nạp được (đang khởi động core): thử lại
