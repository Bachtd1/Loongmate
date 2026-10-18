"""
Các màn hình và máy trạng thái của giao diện SDL LoongMate (không đụng SDL ở đây:
mọi lệnh vẽ đi qua đối tượng `g` kiểu Gfx do engine/ui_sdl.py cấp).

Nguyên tắc: mọi con số/kết quả hiện ra là NGUYÊN VĂN những gì engine trả về;
tác vụ phá huỷ luôn qua hộp xác nhận mặc định "Huỷ"; tác vụ dài chạy trong
thread nền (thread chỉ gọi engine, KHÔNG gọi SDL) để vòng lặp chính vẫn vẽ.
"""

import json
import os
import sys
import time
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from pathlib import Path

from engine import agent as agent_mod
from engine import artwork as artwork_mod
from engine import batchfix as batchfix_mod
from engine import bios as bios_mod
from engine import dedupe as dedupe_mod
from engine import hashcache as hashcache_mod
from engine import hottabs as hottabs_mod
from engine import importer as importer_mod
from engine import jawaka
from engine import homebrew as homebrew_mod
from engine import libsource as libsource_mod
from engine import nodes as nodes_mod
from engine import telemetry as telemetry_mod
from engine import transfer as transfer_mod
from engine.autobackup import PidLock
from engine.config import ConfigStore, get_nodes_settings, load_audit_cache, save_audit_cache, set_node_favorite

try:   # engine/update.py do agent khác viết; thiếu thì các màn Cập nhật báo thẳng chứ không vỡ cả UI
    from engine import update as update_mod
except ImportError:
    update_mod = None

WIN_W, WIN_H = 960, 720
BG = (0x12, 0x15, 0x16)
PANEL = (0x1C, 0x22, 0x24)
TEXT = (0xF0, 0xF3, 0xF4)
MUTED = (0x8D, 0x99, 0xAE)
FOCUS = (0x27, 0xAE, 0x60)
HILITE = (0x1F, 0x4D, 0x36)
STATUS_COLORS = {"green": (0x2E, 0xCC, 0x71), "yellow": (0xF3, 0x9C, 0x12),
                 "red": (0xE7, 0x4C, 0x3C), "unknown": (0x8D, 0x99, 0xAE)}
SEV_COLOR = {"critical": "red", "warning": "yellow", "info": "unknown"}
SEV_NAME = {"critical": "NGHIÊM TRỌNG", "warning": "CẢNH BÁO", "info": "GHI CHÚ"}
RESCAN_REMINDER = "Vào System > Rescan Library trên máy để Leaf cập nhật danh sách game."
NO_RESCAN_ENV = "LOONGMATE_NO_RESCAN"   # =1: UI không bao giờ gọi jawakad (dùng khi thử nghiệm)
AGENT_HINT = "Bật service LoongMate Agent trong màn Services của Leaf: Enable rồi Start."
CHUNK_GB = (1, 2, 3, 5)                 # mục tiêu dung lượng trống cho gợi ý chuyển hàng loạt
MB = 1024 * 1024

# CHƯA KIỂM CHỨNG cho X và Y: log trên máy thật chỉ xác nhận A=1, B=0 (bố cục Nintendo).
# Ghi đè được bằng LOONGMATE_BTN_A/B/X/Y.
def _btn_env(name: str, default: int) -> int:
    try:
        return int(os.environ["LOONGMATE_BTN_" + name])
    except (KeyError, ValueError):
        return default


# Bảng nút joystick của Pocket 1 — lấy đúng từ toolkit Catastrophe của Leaf
# (catastrophe.h, CAT__MLP1_BTN_*, dùng SDL joystick API): A=1 (BTN_EAST), B=0
# (BTN_SOUTH), X=2, Y=3, L1=4, R1=5, SELECT=8, START=9, MENU=10. D-pad là hat.
# Đã đối chiếu log trên máy thật 2026-10-02 (A=1, B=0, Menu=10).
BTN_MAP = {
    _btn_env("A", 1): "A", _btn_env("B", 0): "B", _btn_env("X", 2): "X", _btn_env("Y", 3): "Y",
    _btn_env("L1", 4): "LEFT", _btn_env("R1", 5): "RIGHT",   # vai = lật trang
    _btn_env("START", 9): "A", _btn_env("MENU", 10): "B",     # Start như A, Menu như B (quay lại)
}
KEY_ACTION = {
    0x40000050: "LEFT", 0x4000004F: "RIGHT", 0x40000052: "UP", 0x40000051: "DOWN",
    13: "A", 27: "B", ord("j"): "A", ord("k"): "B", ord("u"): "X", ord("i"): "Y", ord("q"): "QUIT",
    ord("w"): "UP", ord("a"): "LEFT", ord("s"): "DOWN", ord("d"): "RIGHT",
}
HAT_BITS = ((0x01, "UP", "LÊN"), (0x04, "DOWN", "XUỐNG"), (0x08, "LEFT", "TRÁI"), (0x02, "RIGHT", "PHẢI"))
AXIS_THRESHOLD = 16000


def fit_middle(s: str, maxw: int, width, tail_chars: int = 14) -> str:
    """Cắt GIỮA nhãn quá dài ("Bomberman Ma…(Blue)"): giữ đuôi (≤ tail_chars ký tự) vì đuôi thường là phần phân
    biệt các bản (Disc 2, (Blue)/(Red), (USA)/(Japan)). width(text) -> px. Không vừa nổi thì cắt cuối."""
    if width(s) <= maxw:
        return s
    for t in range(min(tail_chars, len(s) - 7), 3, -1):     # cột hẹp: co đuôi dần (14 -> 4) trước khi bỏ cuộc
        tail = s[-t:]
        for h in range(len(s) - t - 1, 5, -1):
            cand = s[:h] + "…" + tail
            if width(cand) <= maxw:
                return cand
    while s and width(s + "…") > maxw:
        s = s[:-1]
    return s + "…"


def mb(n) -> str:
    return f"{n / MB:.1f} MB" if n >= MB else f"{n / 1024:.1f} KB"


from engine.status import (   # noqa: F401  (re-export: chỗ khác vẫn gọi ui.offsite_status / ui.OFFSITE_*)
    OFFSITE_NAME, OFFSITE_STALE_DAYS, OFFSITE_OLD_DAYS, OFFSITE_MISSING, _COLOR_RANK, _COLOR_PREFIX, offsite_status)


# ---------------------------------------------------------------- trạng thái do agent ghi (agent_state.json)
VERIFY_STALE_DAYS = 14   # kiểm toàn vẹn cũ hơn: vàng


def read_agent_state(app_data) -> Dict[str, Any]:
    """Đọc <app_data>/agent_state.json; thiếu/hỏng/không phải đối tượng thì {} (không bịa). CHỈ ĐỌC."""
    if not app_data:
        return {}
    try:
        d = json.loads((Path(app_data) / agent_mod.STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def vault_verify_status(state, now: Optional[float] = None) -> Dict[str, Any]:
    """Chấm kết quả state['vault_verify'] do agent ghi.
    level: none (chưa kiểm/dữ liệu không dùng được -> KHÔNG cảnh báo) | ok | stale | bad.
    color: green/yellow/red/unknown.  line: dòng sẵn để hiện (rỗng khi none)."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"level": "none", "color": "unknown", "line": "", "bad": [], "missing": 0,
                           "checked": None, "partial": False, "ts": None, "age_days": None, "count": 0}
    v = state.get("vault_verify") if isinstance(state, dict) else None
    if not isinstance(v, dict) or not isinstance(v.get("ok"), bool):
        return out
    bad = [str(x) for x in v["bad_objects"]] if isinstance(v.get("bad_objects"), list) else []
    miss = _num(v.get("missing_objects"))
    miss = int(miss) if miss is not None and miss > 0 else 0
    ts = _num(v.get("ts"))
    age = int(max(0.0, now - ts) / 86400) if ts is not None else None
    checked = _num(v.get("checked"))
    out.update(bad=bad, missing=miss, partial=v.get("partial") is True, ts=ts, age_days=age,
               checked=int(checked) if checked is not None else None, count=len(bad) + miss)
    if not v["ok"]:
        out.update(level="bad", color="red",
                   line=f"!! Kiểm tra toàn vẹn: {out['count']} object hỏng/thiếu — xem SaveVault")
    elif age is not None and age > VERIFY_STALE_DAYS:
        out.update(level="stale", color="yellow", line=f"! Kiểm tra toàn vẹn đã {age} ngày chưa làm lại")
    else:
        when = "hôm nay" if not age else f"{age} ngày trước"
        out.update(level="ok", color="green", line=f"+ Toàn vẹn kho: đã kiểm ({when})" if ts is not None else "+ Toàn vẹn kho: đã kiểm")
    return out


def prewarm_line(state) -> str:
    """Dòng trạng thái băm trước của agent; rỗng nếu agent chưa ghi trường này."""
    p = state.get("prewarm") if isinstance(state, dict) else None
    if not isinstance(p, dict) or not isinstance(p.get("done"), bool):
        return ""
    if p["done"]:
        h = _num(p.get("hashed"))
        return "+ Băm trước: xong" + (f" ({int(h)} file)" if h is not None else "")
    r = _num(p.get("remaining"))
    return "Băm trước: đang làm" + (f", còn {int(r)} file" if r is not None else "")


def _fmt_ts(ts) -> str:
    try:
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return "không rõ"


UPDATE_DIR_NAME = "LoongMate-update"  # <thẻ chính>/LoongMate-update/<tên gói>/manifest.json
APPS_DIR_ENV = "LOONGMATE_APPS_DIR"    # ghi đè thư mục Apps (dùng khi thử trên sandbox)
RESTART_NOTE = "Thoát và mở lại LoongMate để dùng bản mới"
RESTART_NOTE_ROLLBACK = "Thoát và mở lại LoongMate để dùng bản đã khôi phục"


def find_update_bundles(primary) -> List[Path]:
    """Các thư mục gói có manifest.json trong <thẻ chính>/LoongMate-update/ (chỉ đọc, sắp theo tên)."""
    if not primary:
        return []
    try:
        return sorted(m.parent for m in (Path(primary) / UPDATE_DIR_NAME).glob("*/manifest.json") if m.is_file())
    except OSError:
        return []


def bundle_version(d: Path) -> Optional[str]:
    try:
        v = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8")).get("version")
        return v if isinstance(v, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def default_apps_dir(primary) -> Optional[Path]:
    env = os.environ.get(APPS_DIR_ENV)
    if env:
        return Path(env)
    return Path(primary) / "Apps" / "mlp1" if primary else None


def hash_cache_note(rep) -> str:
    """' Dùng lại N hash đã lưu (băm mới M).' khi dedupe báo cache_hits/cache_misses; rỗng nếu engine chưa báo."""
    if rep.get("cache_hits") is None or rep.get("cache_misses") is None:
        return ""
    return f" Dùng lại {rep['cache_hits']} hash đã lưu, băm mới {rep['cache_misses']} file."


def worse_color(a: str, b: str) -> str:
    return a if _COLOR_RANK.get(a, 1) >= _COLOR_RANK.get(b, 1) else b


def line_color(s: str):
    """Tiền tố dòng giải thích: '!!' đỏ, '!' vàng, '+' xanh; còn lại chữ thường."""
    if s.startswith("!!"):
        return STATUS_COLORS["red"]
    if s.startswith("!"):
        return STATUS_COLORS["yellow"]
    if s.startswith("+"):
        return STATUS_COLORS["green"]
    return TEXT


# ---------------------------------------------------------------- xoá ROM theo lô (dùng chung với cli `rom delete`)
def _rk(p) -> str:
    """Khoá so sánh đường dẫn: đã resolve (plan_rom_deletion trả đường dẫn đã resolve)."""
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(os.path.abspath(str(p)))


def plan_rom_batch(paths, primary_path, secondary_path, include_artwork: bool = True) -> List[Dict[str, Any]]:
    """Lập kế hoạch xoá từng game bằng dedupe.plan_rom_deletion rồi GỘP trùng. Trả danh sách
    {"path", "plan", "merged_into"} theo thứ tự đầu vào:
    - đường dẫn lặp lại: chỉ giữ lần đầu;
    - file đã nằm trong kế hoạch của game đứng trước (vd chọn cả .cue lẫn .bin của nó) chỉ bị xoá MỘT lần;
      mục nào bị gộp hết thì merged_into = đường dẫn game giữ file, không tính là lỗi."""
    entries, seen = [], set()
    for p in paths:
        k = _rk(p)
        if k in seen:
            continue
        seen.add(k)
        entries.append({"path": str(p), "plan": dedupe_mod.plan_rom_deletion(
            p, primary_path, secondary_path, include_artwork=include_artwork), "merged_into": None, "_k": k})
    claimed: Dict[str, str] = {}
    for e in entries:
        pl = e["plan"]
        if not pl["ok"]:
            continue
        dropped, owner = 0, None
        for field in ("files", "artwork"):
            keep = []
            for f in pl[field]:
                fk = _rk(f)
                if fk in claimed:
                    owner = owner or claimed[fk]
                    try:
                        dropped += os.path.getsize(f)
                    except OSError:
                        pass
                else:
                    claimed[fk] = e["path"]
                    keep.append(f)
            pl[field] = keep
        if dropped:
            pl["bytes"] -= dropped
            pl["message"] = (f"Sẽ xoá {len(pl['files'])} file ROM + {len(pl['artwork'])} ảnh bìa ({pl['bytes'] / MB:.1f} MB)."
                             " (Phần trùng với game khác trong lô đã được gộp.)")
        if not pl["files"]:
            e["merged_into"] = owner
    for e in entries:   # mục không lập được kế hoạch nhưng là file của game khác trong lô (vd .bin của .cue đã chọn)
        if not e["plan"]["ok"] and e["_k"] in claimed:
            e["merged_into"] = claimed[e["_k"]]
        del e["_k"]
    return entries


def rom_batch_game_rows(entries) -> List[Dict[str, Any]]:
    """Dòng 'games' (chưa xoá gì) cho từng mục: game không lập được kế hoạch ghi lý do vào refused."""
    rows = []
    for e in entries:
        row = {"path": e["path"], "plan": e["plan"], "deleted": [], "refused": [], "bytes_freed": 0}
        if e["merged_into"]:
            row["merged_into"] = e["merged_into"]
        elif not e["plan"]["ok"]:
            row["refused"] = [{"path": e["path"], "reason": e["plan"]["message"]}]
        rows.append(row)
    return rows


def execute_rom_batch(entries, userdata_path, primary_path, secondary_path) -> Dict[str, Any]:
    """Xoá từng game bằng dedupe.execute_deletion (mỗi game một lệnh, một game hỏng không chặn game khác).
    Trả {"games", "deleted", "refused", "bytes_freed", "ledger_path"}."""
    games, out = rom_batch_game_rows(entries), {"deleted": [], "refused": [], "bytes_freed": 0, "ledger_path": None}
    for e, row in zip(entries, games):
        if not e["merged_into"] and e["plan"]["ok"]:
            res = dedupe_mod.execute_deletion(e["plan"]["files"], userdata_path, "user_request",
                                              primary_path=primary_path, secondary_path=secondary_path,
                                              artwork=e["plan"]["artwork"])
            row.update(deleted=res["deleted"], refused=res["refused"], bytes_freed=res["bytes_freed"])
            out["ledger_path"] = res.get("ledger_path") or out["ledger_path"]
        out["deleted"] += row["deleted"]
        out["refused"] += row["refused"]
        out["bytes_freed"] += row["bytes_freed"]
    out["games"] = games
    return out


ROM_TOP = "*"                       # khoá "hệ" của danh sách Game lớn nhất (mọi hệ); không trùng tên thư mục hệ nào
ROM_TOP_N = 50
ROM_SORT_LABEL = {"name": "Tên", "size": "Dung lượng (lớn trước)"}
ROM_SAVE_CYCLE = ("all", "with", "without")
ROM_SAVE_LABEL = {"all": "tất cả", "with": "có save", "without": "không save"}


def rom_save_stems(inv) -> set:
    """stem (chữ thường) của mọi save ở BẤT KỲ thẻ nào — cùng định nghĩa has_save với `cli roms`."""
    return {sv.get("stem", "").lower() for cid in ("primary", "secondary")
            for sv in ((inv or {}).get(cid) or {}).get("saves", [])}


def rom_view_rows(rows, save_stems, save: str = "all", sort: str = "name", top: Optional[int] = None) -> List[dict]:
    """Lọc (save: all|with|without) -> (top: chỉ N file lớn nhất sau khi lọc) -> sắp xếp (name|size). Không đổi `rows`."""
    out = list(rows)
    if save != "all":
        out = [r for r in out if (r["stem"].lower() in save_stems) == (save == "with")]
    if top is not None:
        out = sorted(out, key=lambda r: (-r["size"], r["name"].lower(), r["card_id"]))[:top]
    if sort == "size":
        out.sort(key=lambda r: (-r["size"], r["name"].lower(), r["card_id"]))
    else:
        out.sort(key=lambda r: (r["name"].lower(), r["card_id"]))
    return out


def prune_inventory(inv, deleted_paths) -> int:
    """Bỏ các file đã xoá khỏi `roms` và `images` của từng thẻ trong kết quả scan_all (tại chỗ) và
    cập nhật total_roms/total_images. Khớp theo đường dẫn đã resolve (chỉ resolve những file trùng tên).
    Trả số mục đã bỏ."""
    if not inv or not deleted_paths:
        return 0
    gone = {_rk(p) for p in deleted_paths}
    names = {os.path.basename(str(p)).lower() for p in deleted_paths}
    removed = 0
    for cid in ("primary", "secondary"):
        card = inv.get(cid)
        if not card:
            continue
        for sect in ("roms", "images"):
            old = card.get(sect) or []
            card[sect] = [r for r in old if not (os.path.basename(r["absolute_path"]).lower() in names
                                                 and _rk(r["absolute_path"]) in gone)]
            removed += len(old) - len(card[sect])
    for sect in ("roms", "images"):
        inv["total_" + sect] = sum(len((inv.get(c) or {}).get(sect) or []) for c in ("primary", "secondary"))
    return removed


# ---------------------------------------------------------------- widgets
class Item:
    def __init__(self, label, tag="", tagc="unknown", explain=(), a=None, x=None, y=None, head=False):
        self.label, self.tag, self.tagc, self.explain = label, tag, tagc, list(explain)
        self.a, self.x, self.y, self.head = a, x, y, head   # a/x/y = (nhãn tác dụng, hàm)


class Task:
    def __init__(self, label: str, fn: Callable, done: Callable):
        self.label, self.fn, self.done = label, fn, done
        self.t0 = time.monotonic()
        self.progress = None            # {'ev': {...}} do tác vụ nền cập nhật (tiến độ theo byte); None = không có
        self.result, self.err, self.finished = None, None, False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.result = self.fn()
        except Exception as e:  # trả lý do thật cho người dùng, không nuốt
            self.err = f"{type(e).__name__}: {e}"
        finally:
            self.finished = True


class Dialog:
    def __init__(self, title, lines, on_confirm=None, confirm_label="Xác nhận"):
        self.title, self.lines = title, [str(x) for x in lines]
        self.on_confirm, self.confirm_label = on_confirm, confirm_label
        self.choice, self.scroll = 0, 0     # choice 0 = Huỷ (mặc định an toàn)

    def render(self, g):
        bw, bh = 880, 640
        x, y = (WIN_W - bw) // 2, (WIN_H - bh) // 2
        g.fill(x, y, bw, bh, PANEL)
        g.outline(x, y, bw, bh, FOCUS, 3)
        g.text(self.title, "title", TEXT, x + 20, y + 14)
        wrapped: List[Tuple[str, Tuple[int, int, int]]] = []
        for ln in self.lines:
            for w in (g.wrap(ln, "body", bw - 40) if ln else [""]):
                wrapped.append((w, line_color(ln)))
        rows = (bh - 60 - 100) // 25   # chừa chỗ cho dòng "(dòng a-b/n)" khỏi đè dòng cuối
        self.scroll = max(0, min(self.scroll, max(0, len(wrapped) - rows)))
        for i, (w, c) in enumerate(wrapped[self.scroll:self.scroll + rows]):
            g.text(w, "body", c, x + 20, y + 56 + i * 25)
        more = len(wrapped) - self.scroll - rows
        if more > 0 or self.scroll > 0:
            g.text(f"(dòng {self.scroll + 1}-{min(len(wrapped), self.scroll + rows)}/{len(wrapped)} — Lên/Xuống để cuộn)",
                   "small", MUTED, x + 20, y + bh - 98)
        if self.on_confirm:
            for i, lab in enumerate(("Huỷ", self.confirm_label)):
                bx = x + 20 + i * 220
                g.fill(bx, y + bh - 66, 200, 40, HILITE if i == self.choice else BG)
                g.outline(bx, y + bh - 66, 200, 40, FOCUS if i == self.choice else MUTED, 2)
                g.text(lab, "body", TEXT, bx + 14, y + bh - 58)
            g.text("[Trái/Phải] chọn  [A] bấm nút đang chọn  [B] huỷ", "small", MUTED, x + 460, y + bh - 56)
        else:
            g.text("[A/B/X/Y hoặc nút bất kỳ] đóng", "small", MUTED, x + 20, y + bh - 56)

    def footer(self):
        return "[Trái/Phải] chọn  [A] bấm nút đang chọn  [B] huỷ" if self.on_confirm else "[A/B/X/Y] đóng"

    def on_action(self, act, app):
        if act == "UP":
            self.scroll = max(0, self.scroll - 3)
        elif act == "DOWN":
            self.scroll += 3
        elif self.on_confirm:
            if act in ("LEFT", "RIGHT"):
                self.choice = 1 if act == "RIGHT" else 0
            elif act == "B":
                app.dialog = None
            elif act == "A":
                cb, ok = self.on_confirm, self.choice == 1
                app.dialog = None
                if ok:
                    cb()
        elif act in ("A", "B", "X", "Y"):
            app.dialog = None


STATUS_VI = {"green": "Ổn", "yellow": "Cần chú ý", "red": "Có vấn đề", "unknown": "Chưa đọc được"}


class ListScreen:
    """Danh sách chọn được + khung giải thích bên phải + chân màn hình theo mục đang chọn."""
    # 36 px: dòng 30 px làm dấu nặng (ạ, ọ, ụ) và đuôi chữ g chạm viền ô đang chọn.
    ROW = 36

    def __init__(self, app, title, build):
        self.app, self.title, self.build = app, title, build
        self.sel = self.top = 0
        self.header: List[str] = []
        self.items: List[Item] = []
        self.refresh()

    def refresh(self):
        self.header, self.items = self.build()
        self.sel = min(self.sel, max(0, len(self.items) - 1))
        if self.items and self.items[self.sel].head:
            self._step(1)

    def _step(self, d):
        i, n = self.sel, len(self.items)
        for _ in range(n):
            i += d
            if i < 0 or i >= n:
                return
            if not self.items[i].head:
                self.sel = i
                return

    def cur_item(self):
        return self.items[self.sel] if self.items else None

    def footer(self):
        it, parts = self.cur_item(), []
        if it:
            for key, act in (("A", it.a), ("X", it.x), ("Y", it.y)):
                if act:
                    parts.append(f"[{key}] {act[0]}")
        return "  ".join(parts + ["[Lên/Xuống] chọn  [Trái/Phải] lật trang  [B] Quay lại"])

    def on_action(self, act):
        it = self.cur_item()
        rows = self._rows()
        if act == "UP":
            self._step(-1)
        elif act == "DOWN":
            self._step(1)
        elif act in ("LEFT", "RIGHT"):
            d = 1 if act == "RIGHT" else -1
            self.sel = max(0, min(len(self.items) - 1, self.sel + d * rows))
            if self.items and self.items[self.sel].head:
                self._step(1 if d > 0 else -1)
        elif act == "B":
            self.app.back()
        elif it and act in ("A", "X", "Y"):
            fn = {"A": it.a, "X": it.x, "Y": it.y}[act]
            if fn:
                fn[1]()

    def _layout(self):
        return 54 + 22 * len(self.header) + 6, WIN_H - 44

    def _rows(self):
        top, bottom = self._layout()
        return max(1, (bottom - top) // self.ROW)

    def render(self, g):
        g.text(self.title, "title", TEXT, 20, 12)
        y = 52
        for h in self.header:
            g.text(g.fit(h, "small", WIN_W - 40), "small", line_color(h) if h.startswith("!") else MUTED, 20, y)
            y += 22
        top, bottom = self._layout()
        rows = self._rows()
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + rows:
            self.top = self.sel - rows + 1
        for i in range(self.top, min(len(self.items), self.top + rows)):
            it, ry = self.items[i], top + (i - self.top) * self.ROW
            if it.head:
                g.text(g.fit(it.label, "small", 460), "small", MUTED, 24, ry + 6)
                continue
            if i == self.sel:
                g.fill(20, ry, 480, self.ROW - 2, HILITE)
                g.outline(20, ry, 480, self.ROW - 2, FOCUS, 2)
            tw = g.width(it.tag, "small") if it.tag else 0
            g.text(fit_middle(it.label, 480 - tw - 28, lambda t: g.width(t, "body")), "body", TEXT, 28, ry + 3)
            if it.tag:
                g.text(it.tag, "small", STATUS_COLORS.get(it.tagc, MUTED), 492 - tw, ry + 6)
        if len(self.items) > rows:   # góc phải trên: dải dưới danh sách sát dòng chân màn hình nên để ở đó là đè
            cnt = f"{self.sel + 1}/{len(self.items)}"
            g.text(cnt, "small", MUTED, WIN_W - 20 - g.width(cnt, "small"), 22)
        px, pw = 516, WIN_W - 516 - 20
        g.fill(px, top, pw, bottom - top, PANEL)
        g.outline(px, top, pw, bottom - top, MUTED, 1)
        it = self.cur_item()
        if not it:
            g.text("(Không có mục nào)", "small", MUTED, px + 12, top + 10)
            return
        ty, limit = top + 10, bottom - 24
        g.text(g.fit("Giải thích", "small", pw - 20), "small", MUTED, px + 12, ty)
        ty += 24
        for ln in it.explain:
            for w in (g.wrap(ln, "small", pw - 24) if ln else [""]):
                if ty > limit:
                    g.text("…", "small", MUTED, px + 12, ty)
                    return
                g.text(w, "small", line_color(ln), px + 12, ty)
                ty += 21


# ---------------------------------------------------------------- lịch sử pin & nhiệt
def bucketize(rows, t0, span, n, getter, agg):
    """Chia các mẫu trong [t0, t0+span] thành n cột đều nhau; cột không có mẫu nào là None
    (KHÔNG nội suy, KHÔNG điền số)."""
    vals: List[List[float]] = [[] for _ in range(n)]
    for r in rows:
        v = getter(r)
        if v is None or r["ts"] < t0 or r["ts"] > t0 + span:
            continue
        vals[min(n - 1, int((r["ts"] - t0) * n / span))].append(v)
    return [agg(v) if v else None for v in vals]


def peak_temp(r):
    """Nhiệt cao nhất trong SoC/GPU/pin của một mẫu (cùng cách tính với telemetry.summarize)."""
    return max((r[k] for k in ("soc_c", "gpu_c", "batt_c") if r.get(k) is not None), default=None)


def _avg(v):
    return sum(v) / len(v)


def temp_color(c):
    return STATUS_COLORS["red"] if c > 70 else STATUS_COLORS["yellow"] if c >= 60 else STATUS_COLORS["green"]


def batt_color(p):
    return STATUS_COLORS["red"] if p <= 15 else STATUS_COLORS["yellow"] if p <= 30 else STATUS_COLORS["green"]


def summary_lines(s) -> List[str]:
    """Tóm tắt từ telemetry.summarize, NGUYÊN VĂN số liệu của engine."""
    def t(label, d):
        return f"{label} {d['max']}°C (TB {d['avg']}°C)" if d else f"{label}: không đọc được"
    out = [f"{s['samples']} mẫu, từ {time.strftime('%d/%m %H:%M', time.localtime(s['first_ts']))} "
           f"đến {time.strftime('%d/%m %H:%M', time.localtime(s['last_ts']))}"]
    if s["drain_pct_per_hour"] is None:
        out.append("Hao pin: chưa đủ dữ liệu (cần ít nhất 0,5 giờ xả liên tục, mẫu cách nhau không quá 30 phút).")
    else:
        out.append(f"Hao pin: {s['drain_pct_per_hour']} %/giờ (tính trên {s['discharge_hours']} giờ xả liên tục)")
    tp = s["temp"]
    out.append("Cao nhất — " + t("SoC", tp["soc"]) + " • " + t("GPU", tp["gpu"]) + " • " + t("Pin", tp["batt"]))
    out.append(f"Vượt 70°C: {s['over_70_events']} lần ({s['over_70_samples']} mẫu)")
    return out


class HistoryScreen:
    """Biểu đồ cột vẽ bằng fill: pin (%) và nhiệt cao nhất (°C) theo 24 giờ / 7 ngày."""
    RANGES = (("24 giờ qua", 24 * 3600, 48, "%H:%M", 4), ("7 ngày qua", 7 * 86400, 84, "%d/%m", 7))
    PX, PW, PH = 76, 860, 130

    def __init__(self, app, rows, now):
        self.app, self.rows, self.now, self.rng = app, rows, now, 0

    def refresh(self):
        pass

    def footer(self):
        return "[Trái/Phải] đổi khoảng thời gian  [B] Quay lại"

    def on_action(self, act):
        if act in ("LEFT", "RIGHT", "UP", "DOWN"):
            self.rng = 1 - self.rng
        elif act == "B":
            self.app.back()

    def _chart(self, g, y, title, vals, vmax, color_of, unit_ticks):
        g.text(title, "small", MUTED, 20, y - 40)
        g.fill(self.PX, y, self.PW, self.PH, PANEL)
        for frac, lab in unit_ticks:
            gy = y + self.PH - int(self.PH * frac)
            g.fill(self.PX, min(gy, y + self.PH - 1), self.PW, 1, (0x2E, 0x38, 0x3B))
            g.text(lab, "small", MUTED, 20, gy - 10)
        n = len(vals)
        for i, v in enumerate(vals):
            if v is None:
                continue
            x0, x1 = self.PX + i * self.PW // n, self.PX + (i + 1) * self.PW // n
            h = max(2, int(self.PH * min(v, vmax) / vmax))
            g.fill(x0, y + self.PH - h, max(1, x1 - x0 - 1), h, color_of(v))

    def render(self, g):
        g.text("LỊCH SỬ PIN & NHIỆT", "title", TEXT, 20, 12)
        if not self.rows:
            for i, ln in enumerate(("Chưa có dữ liệu — " + AGENT_HINT[0].lower() + AGENT_HINT[1:],
                                    "Agent ghi một mẫu pin/nhiệt mỗi 5 phút vào thư mục telemetry của LoongMate.",
                                    "LoongMate không vẽ số giả: biểu đồ chỉ có sau khi agent đã chạy.")):
                g.text(ln, "body", STATUS_COLORS["yellow"] if i == 0 else MUTED, 20, 80 + i * 32)
            return
        for i, (name, *_r) in enumerate(self.RANGES):
            tx = 20 + i * 170
            g.fill(tx, 48, 160, 32, HILITE if i == self.rng else BG)
            g.outline(tx, 48, 160, 32, FOCUS if i == self.rng else MUTED, 2)
            g.text(name, "body", TEXT, tx + 14, 52)
        name, span, n, fmt, nt = self.RANGES[self.rng]
        t0 = self.now - span
        rows = [r for r in self.rows if r["ts"] >= t0]
        g.text(f"Đọc lúc {time.strftime('%d/%m %H:%M', time.localtime(self.now))}", "small", MUTED, WIN_W - 20 - 150, 56)
        if not rows:
            last = self.rows[-1]["ts"]
            g.text(f"Không có mẫu nào trong {name.lower()}. Mẫu gần nhất: {time.strftime('%d/%m %H:%M', time.localtime(last))}.",
                   "body", STATUS_COLORS["yellow"], 20, 110)
            g.text(AGENT_HINT, "small", MUTED, 20, 146)
            return
        y1, y2 = 134, 332
        self._chart(g, y1, "Pin (%) — trung bình mỗi cột", bucketize(rows, t0, span, n, lambda r: r["battery_pct"], _avg),
                    100, batt_color, ((0, "0"), (0.5, "50"), (1.0, "100")))
        self._chart(g, y2, "Nhiệt độ cao nhất (°C) trong SoC/GPU/pin — cao nhất mỗi cột",
                    bucketize(rows, t0, span, n, peak_temp, max), 100, temp_color, ((0, "0"), (0.5, "50"), (1.0, "100")))
        for y in (y1, y2):
            for k in range(nt + 1):
                lab = time.strftime(fmt, time.localtime(t0 + span * k / nt))
                lx = self.PX + self.PW * k // nt
                lx = min(lx - g.width(lab, "small") // 2 if 0 < k < nt else (lx if k == 0 else lx - g.width(lab, "small")), WIN_W - 20)
                g.text(lab, "small", MUTED, max(20, lx), y + self.PH + 4)
        ty = y2 + self.PH + 36
        for ln in summary_lines(telemetry_mod.summarize(rows)):
            g.text(g.fit(ln, "small", WIN_W - 40), "small", TEXT, 20, ty)
            ty += 24
        g.text("Cột trống = không có mẫu (máy ngủ/tắt hoặc agent không chạy).", "small", MUTED, 20, ty + 4)
        g.text("Màu nhiệt: xanh <60 / vàng 60-70 / đỏ >70°C.  Màu pin: xanh >30 / vàng 16-30 / đỏ ≤15%.", "small", MUTED, 20, ty + 28)


class ButtonTest:
    """Màn dò số hiệu nút. Thoát bằng cách bấm CÙNG MỘT nút 3 lần liên tiếp."""

    def __init__(self, app):
        self.app, self.last, self.same = app, None, 0

    def refresh(self):
        pass

    def footer(self):
        return "Bấm cùng MỘT nút 3 lần liên tiếp để thoát"

    def feed(self, kind, value):
        if kind == "hat" and value == 0:
            return
        if kind == "axis" and value[1] == 0:
            return
        if kind == "button":
            act = BTN_MAP.get(value)
            desc = f"nút joystick số {value}"
            gan = f"đang gán: {act}" if act else "CHƯA gán action nào"
        elif kind == "hat":
            names = [n for bit, _a, n in HAT_BITS if value & bit]
            desc, gan = f"D-pad (hat) value={value}", "hướng " + "+".join(names)
        elif kind == "axis":
            desc, gan = f"trục số {value[0]} hướng {value[1]:+d}", "D-pad dạng trục (quy ước: trục chẵn ngang, trục lẻ dọc)"
        else:
            act = KEY_ACTION.get(value)
            desc, gan = f"phím sym={value}", (f"đang gán: {act}" if act else "CHƯA gán")
        key = (kind, value)
        self.same = self.same + 1 if key == self.last else 1
        self.last = key
        self.shown = (desc, gan)
        print(f"[LoongMate][kiểm tra nút] {desc} — {gan}", file=sys.stderr, flush=True)
        if self.same >= 3:
            self.app.back()

    def on_action(self, act):
        pass

    def render(self, g):
        g.text("KIỂM TRA NÚT", "title", TEXT, 20, 12)
        g.text("Bấm bất kỳ nút/hướng nào trên máy; số hiệu hiện ở đây và được ghi vào log.", "body", MUTED, 20, 60)
        if self.last is None:
            g.text("Chưa bấm gì...", "title", MUTED, 40, 200)
        else:
            desc, gan = self.shown
            g.text(desc, "title", STATUS_COLORS["green"], 40, 200)
            g.text(gan, "body", TEXT, 40, 250)
            g.text(f"Lần bấm liên tiếp giống nhau: {self.same}/3", "body", MUTED, 40, 300)
        for i, ln in enumerate(("Mục đích: dò số hiệu nút thật của Loong Gamepad để gán A/B/X/Y đúng.",
                                "Đặt biến LOONGMATE_BTN_A/B/X/Y nếu mapping mặc định sai.",
                                "Thoát: bấm CÙNG MỘT nút 3 lần liên tiếp (kể cả nút B).")):
            g.text(ln, "small", MUTED, 40, 420 + i * 24)


class HomeScreen:
    def __init__(self, app):
        self.app, self.row, self.col = app, 0, 0

    def refresh(self):
        self.app.refresh_home()

    def footer(self):
        return "[D-pad] di chuyển  [A] chi tiết  [X] Sao lưu  [Y] Gói hỗ trợ  [B] Thoát"

    def on_action(self, act):
        app = self.app
        if act == "UP":
            self.row = 0
        elif act == "DOWN":
            self.row = 1
        elif act == "LEFT":
            self.col = 0
        elif act == "RIGHT":
            self.col = 1
        elif act == "A":
            idx = self.row * 2 + self.col
            (app.open_device, app.open_cards, app.open_savevault, app.open_library)[idx]()
        elif act == "B":
            app.exit_reason = "hành động B trên Home"
            app.running = False
        elif act == "X":
            app.do_backup()
        elif act == "Y":
            app.ask_bundle()

    def cards(self):
        app, d = self.app, self.app.home_data
        dev, st = d["readiness"]["device"], d["readiness"]["storage"]
        c1 = ("1) MÁY", [battery_line(dev["battery"]), thermal_line(dev["thermal"])] +
              [f"! {w}" for w in dev.get("warnings", [])[:2]], dev["status"])
        c2 = ("2) THẺ NHỚ", storage_lines(st) + [f"! {w}" for w in st.get("warnings", [])[:1]], st["status"])
        rec, snaps = d["recovery"], d["snapshots"]
        if app.vault is None:
            c3 = ("3) SAVE", [app.engine_error or "SaveVault không khả dụng."], "unknown")
        elif rec:
            c3 = ("3) SAVE", ["Phát hiện phục hồi dở dang!", "Hành động: mở SaveVault (A) để rollback."], "red")
        elif not snaps:
            c3 = ("3) SAVE", ["Chưa có snapshot nào.", "Nhấn X để sao lưu ngay."], "yellow")
        else:
            c3 = ("3) SAVE", [f"{len(snaps)} snapshot", f"Mới nhất: {snaps[0]['id']}", f"Lúc: {snaps[0]['created_at']}"], "green")
        if app.vault is not None:   # bản sao ngoài máy: dòng riêng + viền thẻ lấy mức xấu hơn
            off = offsite_status(app.app_data)
            c3 = (c3[0], c3[1] + [off["line"]], worse_color(c3[2], off["color"]))
            vv = vault_verify_status(read_agent_state(app.app_data))
            if vv["level"] != "none":   # chưa kiểm bao giờ -> không thêm gì (agent mới bật)
                c3 = (c3[0], c3[1] + [vv["line"]], worse_color(c3[2], vv["color"]))
        cache = app.report_summary()
        if cache is None:
            c4 = ("4) LIBRARY", ["Chưa quét lần nào.", "Nhấn A rồi chọn Quét lại."], "unknown")
        else:
            sev = cache.get("severity_counts", {})
            cr, wa, inf = sev.get("critical", 0), sev.get("warning", 0), sev.get("info", 0)
            c4 = ("4) LIBRARY", [f"{cache.get('total_issues', 0)} vấn đề (NT {cr} / CB {wa} / GC {inf})",
                                  f"Số liệu lúc: {cache.get('generated_at', 'không rõ')}"],
                  "red" if cr else ("yellow" if wa else "green"))
        pw = prewarm_line(read_agent_state(app.app_data))
        if pw:
            c4 = (c4[0], c4[1] + [pw], c4[2])
        return [c1, c2, c3, c4]

    def render(self, g):
        if not self.app.home_data:
            self.app.refresh_home()
        g.text("LoongMate — Home (Readiness)", "title", TEXT, 20, 14)
        g.text("A: mở chi tiết • B: thoát", "small", MUTED, WIN_W - 20 - g.width("A: mở chi tiết • B: thoát", "small"), 22)
        margin, hh, fh = 20, 60, 60
        cw = (WIN_W - 3 * margin) // 2
        ch = (WIN_H - hh - fh - 3 * margin) // 2
        for idx, (title, lines, status) in enumerate(self.cards()):
            r, c = idx // 2, idx % 2
            x, y = margin + c * (cw + margin), hh + margin + r * (ch + margin)
            focused = r == self.row and c == self.col
            g.fill(x, y, cw, ch, PANEL)
            g.outline(x, y, cw, ch, FOCUS if focused else STATUS_COLORS.get(status, STATUS_COLORS["unknown"]), 5 if focused else 2)
            g.text(title, "title", TEXT, x + 16, y + 10)
            ty = y + 48
            for line in lines:
                for w in g.wrap(line, "body", cw - 32):
                    if ty + 22 > y + ch - 8:
                        break
                    g.text(w, "body", line_color(line) if line.startswith("!") else MUTED, x + 16, ty)
                    ty += 22


def battery_line(bat):
    if bat.get("available") and bat.get("percent") is not None:
        return f"Pin: {bat['percent']}% ({'đang sạc' if bat.get('is_charging') else 'không sạc'})"
    return "Pin: không đọc được"


def thermal_line(th):
    if th.get("available") and th.get("current_celsius") is not None:
        return f"Nhiệt độ: {th['current_celsius']}°C ({th.get('trend', '')})"
    return "Nhiệt độ: không đọc được"


def storage_lines(st):
    out = []
    p = st.get("primary") or {}
    out.append(f"SD1: {p['free_mb']}MB trống/{p['total_mb']}MB | Ghi: {'Có' if p['writable'] else 'KHÔNG'} | FS: {p['filesystem']}"
               if p.get("mounted") else "SD1: không xác định được")
    s = st.get("secondary")
    if s and s.get("mounted"):
        fs = "chưa xác minh" if s.get("fs_detected") is False else s["filesystem"] + (" (FAT32 OK)" if s.get("is_fat32") else " (CẦN FAT32)")
        out.append(f"SD2: {s['free_mb']}MB trống/{s['total_mb']}MB | FS: {fs}")
    else:
        out.append("SD2: chưa gắn thẻ khe 2")
    return out


DIAG_INFO = {
    "userdata_rw_integrity": ("Ghi/đọc dữ liệu LoongMate",
                              "Ghi một file tạm 32 KB vào thư mục dữ liệu của LoongMate, đọc lại, so sánh rồi xoá.",
                              "Nếu FAIL: thẻ có thể đầy, bị khoá ghi hoặc hỏng. SaveVault sẽ không lưu được — kiểm tra mục Thẻ nhớ."),
    "leaf_directory_structure": ("Cấu trúc thư mục Leaf",
                                 "Kiểm tra các thư mục chuẩn (Roms, Saves, States, BIOS...) có mặt trên thẻ chính.",
                                 "Nếu FAIL: mở Library > Quét lại, mục 'Thiếu thư mục chuẩn' có sửa an toàn tạo lại thư mục."),
    "secondary_sd_presence": ("Thẻ SD2",
                              "Kiểm tra thẻ nhớ khe 2 (nếu có khai báo) còn truy cập được.",
                              "Nếu FAIL: rút thẻ ra cắm lại, rồi chạy lại kiểm tra."),
    "mount_and_space": ("Mount và dung lượng",
                        "Kiểm tra filesystem, chế độ read-only và dung lượng trống của từng thẻ.",
                        "Nếu FAIL: xem mục Thẻ nhớ để biết thẻ nào lỗi (đầy, read-only, sai FAT32)."),
    "thermal_battery_sample": ("Mẫu nhiệt độ và pin", "Lấy một mẫu tức thời nhiệt độ, pin, trạng thái sạc.",
                               "Nếu không đọc được: máy không lộ cảm biến qua sysfs; LoongMate không đoán số."),
    "platform_identity": ("Nhận dạng thiết bị", "Đọc model và phiên bản hệ điều hành thật nếu có.",
                          "Không đọc được thì để trống, không bịa."),
}


def diag_extra(t) -> List[str]:
    out = []
    if t.get("present_dirs") is not None:
        out.append("Thư mục tìm thấy: " + (", ".join(t["present_dirs"]) or "(không có)"))
    for cid, m in (t.get("mounts") or {}).items():
        out.append(f"Thẻ {cid}: " + ", ".join(f"{k}={v}" for k, v in m.items()))
    for k in ("path", "model", "os_release", "leaf_version", "model_reason", "reason"):
        if t.get(k) not in (None, ""):
            out.append(f"{k}: {t[k]}")
    if "latency_ms" in t:
        out.append(f"Độ trễ ghi/đọc: {t['latency_ms']} ms")
    return out


# ---------------------------------------------------------------- App
class App:
    def __init__(self, probe, vault, doctor, inv_engine, health, app_data, engine_error):
        self.probe, self.vault, self.doctor = probe, vault, doctor
        self.inv_engine, self.health, self.app_data = inv_engine, health, app_data
        self.engine_error = engine_error
        self.running, self.exit_reason = True, "chưa rõ"
        self.dialog: Optional[Dialog] = None
        self.task: Optional[Task] = None
        self.home_data: Dict[str, Any] = {}
        self.report: Optional[Dict[str, Any]] = None      # báo cáo audit ĐẦY ĐỦ của phiên (cache đĩa chỉ có tóm tắt)
        self.inv: Optional[Dict[str, Any]] = None         # kết quả scan_all của phiên
        self.dup_cache: Dict[str, Dict[str, Any]] = {}    # theo prefer_card
        self.dup_compare: Optional[Dict[str, Any]] = None
        self.dup_art: Dict[str, bool] = {}                # theo prefer_card: có chép ảnh bìa sang thẻ giữ không
        self.rom_marks: Dict[str, set] = {}               # màn Xoá ROM: hệ máy (hoặc ROM_TOP) -> tập đường dẫn file đã đánh dấu
        self.rom_view: Dict[str, Dict[str, str]] = {}     # màn Xoá ROM: hệ máy (hoặc ROM_TOP) -> {"sort","save"} đang dùng
        self.import_data: Optional[Dict[str, Any]] = None     # màn Nhập game: kết quả importer.scan của phiên
        self.import_marks: set = set()                        # id unit đã đánh dấu (X)
        self.import_choices: Dict[str, str] = {}              # id unit -> hệ người dùng chọn (mục mơ hồ)
        self.import_plan: Optional[Dict[str, Any]] = None     # kế hoạch đang xem
        self.import_acts: Dict[str, str] = {}                 # id unit -> keep|replace|copy|cancel (trùng tên khác nội dung)
        self.import_bios: set = set()                         # id BIOS đã xác nhận riêng
        self.import_delete = False                            # "Dọn file đã nhập": xoá thay vì chuyển vào Import/.done/
        self.lib_data: Dict[Any, Dict[str, Any]] = {}         # Thư viện riêng: (node, root) -> kết quả browse (tóm tắt hệ máy)
        self.lib_games: Dict[Any, Dict[str, Any]] = {}        # (node, root, hệ) -> browse kèm entries
        self.lib_marks: Dict[Any, set] = {}                   # (node, root, hệ) -> tập path đã đánh dấu (X)
        self.lib_view: Dict[Any, Dict[str, str]] = {}         # (node, root, hệ) -> {"sort","flt"}
        self.game_idx: Optional[Dict[str, Any]] = None    # màn Khôi phục save một game: kết quả vault.game_index()
        self.dup_art_need: Dict[str, int] = {}            # số nhóm mà thẻ giữ chưa có ảnh nhưng thẻ kia có
        self.nodes_data: Optional[Dict[str, Any]] = None  # kết quả nodes.list_nodes của phiên
        self.node_res: Dict[str, Dict[str, Any]] = {}     # kết quả ping/kiểm cổng gần nhất theo node
        self.home = HomeScreen(self)
        self.stack: List[Any] = [self.home]
        self._last_unknown: Tuple[Optional[int], float] = (None, 0.0)
        self._last_hat, self._axis_dir = 0, {}

    # ---- tiện ích
    def cur(self):
        return self.stack[-1]

    def push(self, s):
        self.stack.append(s)

    def back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            if hasattr(self.cur(), "refresh") and not isinstance(self.cur(), ButtonTest):
                self.cur().refresh()

    def info(self, title, lines):
        self.dialog = Dialog(title, lines)

    def confirm(self, title, lines, cb, label="Xác nhận"):
        self.dialog = Dialog(title, lines, cb, label)

    def need(self, *objs) -> bool:
        if any(o is None for o in objs):
            self.info("Lỗi", [self.engine_error or "Engine không khả dụng."])
            return False
        return True

    def run(self, label, fn, done):
        self.task = Task(label, fn, done)

    def tick(self):
        t = self.task
        if t is not None and t.finished:
            self.task = None
            if t.err:
                self.info("Lỗi", [f"{t.label} thất bại:", t.err])
            else:
                t.done(t.result)

    def wait_task(self):
        while self.task is not None:
            time.sleep(0.05)
            self.tick()

    def finish_change(self, title, lines, changed):
        """Hiện kết quả một thao tác ĐỔI thư viện. Có thay đổi thì tự rescan Leaf (thread nền);
        không tự rescan được thì giữ lời nhắc bấm tay kèm lý do thật."""
        if not changed:
            self.info(title, lines)
            return
        if os.environ.get(NO_RESCAN_ENV) == "1":
            self.info(title, lines + [RESCAN_REMINDER, f"(Tự rescan đang tắt bởi {NO_RESCAN_ENV}=1)"])
            return

        def work():
            av = jawaka.rescan_available()
            return av, (jawaka.request_rescan() if av["available"] else None)

        def done(res):
            av, r = res
            if r is None:
                extra = [RESCAN_REMINDER, f"(Không tự rescan được: {av['reason']})"]
            elif r["ok"]:
                extra = [f"+ Đã cập nhật thư viện Leaf: {r['message']} ({r['waited_s']:g}s)"]
            else:
                extra = [f"!! Tự cập nhật thư viện Leaf thất bại: {r['message']}", RESCAN_REMINDER]
            self.info(title, lines + extra)
            self.refresh_cur()
        self.run("Đang cập nhật thư viện Leaf", work, done)

    def refresh_home(self):
        readiness = self.probe.get_readiness_summary()
        snaps, rec = [], None
        if self.vault is not None:
            try:
                snaps, rec = self.vault.list_snapshots(), self.vault.check_crash_recovery()
            except Exception:
                pass
        self.home_data = {"readiness": readiness, "snapshots": snaps, "recovery": rec}

    def report_summary(self):
        return load_audit_cache(self.app_data) if self.app_data else None

    def refresh_cur(self):
        self.refresh_home()
        for s in self.stack:
            if isinstance(s, ListScreen):
                s.refresh()

    # ---- nhập liệu
    def on_input(self, kind, value):
        """kind: button|hat|axis|key. value: số nút | bitmask hat | (trục, hướng) | sym."""
        if self.task is not None:
            if kind == "key" and KEY_ACTION.get(value) == "QUIT":
                self.exit_reason, self.running = "phím QUIT", False
            return
        if isinstance(self.cur(), ButtonTest):
            self.cur().feed(kind, value)
            return
        if kind == "button":
            act = BTN_MAP.get(value)
            if act is None:
                self._unknown_button(value)
                return
            self._last_unknown = (None, 0.0)
            self.on_action(act)
        elif kind == "hat":
            if value != self._last_hat:
                for bit, a, _n in HAT_BITS:
                    if value & bit:
                        self.on_action(a)
                self._last_hat = value
        elif kind == "axis":
            ax, d = value
            if d != 0 and d != self._axis_dir.get(ax, 0):
                self.on_action(("RIGHT" if d > 0 else "LEFT") if ax % 2 == 0 else ("DOWN" if d > 0 else "UP"))
            self._axis_dir[ax] = d
        elif kind == "key":
            act = KEY_ACTION.get(value)
            if act:
                self.on_action(act)

    def _unknown_button(self, n):
        # Số nút chưa gán: nếu là hộp xác nhận thì coi như Huỷ; ngược lại hiện số nút,
        # và bấm cùng nút đó 2 lần trong 2 giây là thoát (đường thoát dự phòng).
        print(f"[LoongMate] Nút gamepad lạ (chưa gán action): {n}", file=sys.stderr, flush=True)
        if self.dialog is not None and self.dialog.on_confirm:
            self.dialog = None
            return
        now = time.monotonic()
        if self._last_unknown[0] == n and now - self._last_unknown[1] < 2.0:
            self.exit_reason, self.running = f"bấm nút lạ {n} hai lần", False
            return
        self._last_unknown = (n, now)
        self.info(f"Nút số {n} chưa được gán", [f"Bạn vừa bấm nút gamepad số {n}.",
                                                 "Bấm NÚT NÀY thêm 1 lần nữa (trong 2 giây) để THOÁT.",
                                                 "Bấm nút khác để đóng thông báo này.",
                                                 "Dò nút thật: Máy > Kiểm tra nút."])

    def on_action(self, act):
        if act == "QUIT":
            self.exit_reason, self.running = "phím QUIT", False
            return
        if self.task is not None:
            return
        if self.dialog is not None:
            self.dialog.on_action(act, self)
            return
        self.cur().on_action(act)

    # ---- vẽ
    def render(self, g):
        g.clear(BG)
        self.cur().render(g)
        g.text(g.fit(self.dialog.footer() if self.dialog else self.cur().footer(), "small", WIN_W - 40),
               "small", MUTED, 20, WIN_H - 30)
        if self.dialog is not None:
            self.dialog.render(g)
        if self.task is not None:
            x, y, w, h = 130, 300, 700, 120
            g.fill(x, y, w, h, PANEL)
            g.outline(x, y, w, h, FOCUS, 3)
            ev = (self.task.progress or {}).get("ev")
            if ev and ev.get("total"):
                pct = min(100, int(ev["done"] * 100 / ev["total"]))
                what = {"install": "đang cài", "verify": "kiểm hash", "catalog": "danh mục"}.get(ev["phase"], f"file {ev['index']}/{ev['count']}")
                g.text(f"{self.task.label}... {pct}% ({int(time.monotonic() - self.task.t0)}s)", "title", TEXT, x + 24, y + 14)
                g.fill(x + 24, y + 56, w - 48, 14, BG)
                g.fill(x + 24, y + 56, int((w - 48) * pct / 100), 14, FOCUS)
                amount = f"trang {ev['done']}/{ev['total']}" if ev["phase"] == "catalog" else f"{mb(ev['done'])} / {mb(ev['total'])}"
                g.text(g.fit(f"{amount} — {what}: {ev.get('name', '')}", "small", w - 48), "small", MUTED, x + 24, y + 78)
                g.text("Mất mạng/tắt máy: tải tiếp được ở Thư viện riêng.", "small", MUTED, x + 24, y + 98)
            else:
                g.text(f"{self.task.label}... ({int(time.monotonic() - self.task.t0)}s)", "title", TEXT, x + 24, y + 22)
                g.text("Vui lòng chờ, đừng tắt máy. Tác vụ dài có thể mất vài phút.", "small", MUTED, x + 24, y + 70)

    # ---- mở màn hình
    def open_device(self):
        self.push(ListScreen(self, "MÁY — sức khoẻ thiết bị", self._build_device))

    def open_cards(self):
        self.push(ListScreen(self, "THẺ NHỚ", self._build_cards))

    def open_savevault(self):
        if self.need(self.vault):
            self.push(ListScreen(self, "SAVEVAULT — sao lưu & khôi phục", self._build_savevault))

    def open_library(self):
        if self.need(self.doctor):
            self.push(ListScreen(self, "LIBRARY — kiểm toán & dọn thư viện", self._build_library))

    # ---- MÁY
    def _build_device(self):
        r = self.probe.get_readiness_summary()
        dev = r["device"]
        bat, th = dev["battery"], dev["thermal"]
        items = [Item("Thông số", head=True)]
        bex = ["Pin của máy."]
        if bat.get("available"):
            bex += [f"Dung lượng: {bat['percent']}%", f"Trạng thái: {bat.get('status')}", f"Nguồn đọc: {bat.get('device')}"]
            if bat["percent"] <= 25 and not bat.get("is_charging"):
                bex.append("! Pin thấp: cắm sạc trước khi sao lưu hoặc khôi phục (mất điện giữa chừng có thể làm dở dang).")
        else:
            bex += ["!! Không đọc được pin: " + str(bat.get("reason")), "LoongMate không đoán số khi không đọc được."]
        bc = "unknown" if not bat.get("available") else ("red" if bat["percent"] <= 15 and not bat.get("is_charging") else "yellow" if bat["percent"] <= 25 and not bat.get("is_charging") else "green")
        items.append(Item("Pin", battery_line(bat).replace("Pin: ", ""), bc, bex))
        tex = ["Nhiệt độ trung bình các vùng nhiệt."]
        if th.get("available"):
            tex += [f"Hiện tại: {th['current_celsius']}°C", f"Cao nhất: {th['max_celsius']}°C", f"Xu hướng: {th['trend']}"]
            if th["current_celsius"] >= 60:
                tex.append("! Máy đang nóng: ngừng chơi vài phút, tránh sạc khi chơi nặng.")
        else:
            tex += ["!! Không đọc được: " + str(th.get("reason"))]
        tc = "unknown" if not th.get("available") else ("red" if th["current_celsius"] >= 70 else "yellow" if th["current_celsius"] >= 60 else "green")
        items.append(Item("Nhiệt độ", thermal_line(th).replace("Nhiệt độ: ", ""), tc, tex))
        header = [f"Tình trạng chung: {STATUS_VI.get(dev['status'], dev['status'])}"] + [f"! {w}" for w in dev.get("warnings", [])]
        diag = None
        if self.health is not None:
            try:
                diag = self.health.run_diagnostics(storage=r["storage"])
            except Exception as e:
                header.append(f"!! Chẩn đoán thất bại: {e}")
        if diag:
            items.append(Item(f"Kiểm tra tự động ({diag['timestamp']})", head=True))
            for t in diag["tests"]:
                name, meaning, adv = DIAG_INFO.get(t["test"], (t["test"], t.get("description", ""), ""))
                st = "SKIP" if t.get("skipped") else ("PASS" if t.get("passed", True) else "FAIL")
                ex = [meaning, t.get("description", "")] + diag_extra(t)
                if st == "FAIL":
                    ex += ["!! KẾT QUẢ: FAIL", adv]
                elif st == "SKIP":
                    ex += ["! Bỏ qua: " + str(t.get("reason", ""))]
                else:
                    ex += ["+ KẾT QUẢ: PASS"]
                items.append(Item(name, st, {"PASS": "green", "FAIL": "red", "SKIP": "yellow"}[st], ex))
        items.append(Item("Công cụ", head=True))
        items.append(Item("Xuất gói hỗ trợ", "ZIP", "unknown", [
            "Tạo file loongmate_support_<thời gian>.zip gồm: summary.json, checks.txt, mounts.txt, environment.txt, library_issues.json, logs.txt.",
            "+ Đã lọc mật khẩu Wi-Fi, token, khoá SSH khỏi mọi file.",
            "+ Privacy Mode bật: tên game được ẩn danh ở mọi file trong gói.",
            "Gói chỉ nằm trên thẻ; LoongMate không tự gửi đi đâu.",
            "Cần quét thư viện (~15 giây) để có danh sách vấn đề đầy đủ."], a=("Xuất gói", self.ask_bundle)))
        items.append(Item("Lịch sử pin & nhiệt", "24 giờ / 7 ngày", "unknown", [
            "Biểu đồ pin (%) và nhiệt độ cao nhất (°C) theo 24 giờ và 7 ngày, kèm tóm tắt hao pin và số lần vượt 70°C.",
            "Dữ liệu do LoongMate Agent ghi mỗi 5 phút; chưa có agent thì chưa có biểu đồ (không vẽ số giả).",
            "Chỉ đọc."], a=("Xem", self.open_history)))
        items.append(Item("Sức khoẻ pin", "ước lượng", "unknown", [
            "Dung lượng đầy hiện tại so với dung lượng thiết kế, % sức khoẻ ước lượng, mức 'health' do driver báo và xu hướng mAh mỗi 30 ngày.",
            "Đây là ƯỚC LƯỢNG từ mẫu telemetry, không phải số đo chính hãng; màn ghi rõ nguồn của dung lượng thiết kế.",
            "Cần LoongMate Agent đã ghi mẫu một thời gian. Chỉ đọc."], a=("Xem", self.open_battery_health)))
        items.append(Item("Cập nhật LoongMate", "gói trên thẻ", "unknown", [
            "Xem phiên bản đang chạy, tìm gói cập nhật trong LoongMate-update/ trên thẻ, kiểm tra toàn vẹn rồi áp dụng; có thể quay về bản trước.",
            "Áp dụng luôn qua hộp xác nhận (mặc định Huỷ). Cần thoát và mở lại LoongMate để dùng bản mới."], a=("Mở", self.open_update)))
        items.append(Item("Leaf Hot Tabs", "", "unknown", [
            "Bản jawaka-launcher đã patch: mở game từ Games/Favorites thì tự tiếp tục chỗ đang chơi dở như tab Recents.",
            "Xem đang dùng bản Hot Tabs hay bản gốc, bật/tắt tính năng (hiệu lực ngay), gỡ hoặc cài lại (có hộp xác nhận, mặc định Huỷ)."],
            a=("Mở", self.open_hottabs)))
        items.append(Item("Trạng thái agent", "", "unknown", [
            "LoongMate Agent có đang chạy không, lần sao lưu tự động gần nhất, mẫu pin/nhiệt gần nhất.",
            "Chỉ đọc.", AGENT_HINT], a=("Xem", self.open_agent)))
        items.append(Item("Thiết bị trong mạng", "Tailscale", "unknown", [
            "Các máy trong tailnet (PC, Mac, Pi, NAS): online/offline, IP; chọn máy để ping, kiểm cổng SSH/SMB/Sunshine/RDP, đánh dấu yêu thích, Wake-on-LAN.",
            "Cần Tailscale đã cài và chạy ngoài LoongMate; LoongMate chỉ đọc trạng thái.",
            "Tác vụ mạng chạy nền, có thể mất vài giây."], a=("Mở", self.open_nodes)))
        items.append(Item("Kiểm tra nút", "", "unknown", [
            "Hiện số hiệu nút joystick/hat/trục bạn vừa bấm và action đang gán cho nó.",
            "Dùng để dò mapping thật của Loong Gamepad (nút 10 chưa biết là gì; X/Y chưa kiểm chứng).",
            "Mọi lần bấm được ghi vào log.",
            "Thoát: bấm CÙNG một nút 3 lần liên tiếp."], a=("Mở", lambda: self.push(ButtonTest(self)))))
        return header, items

    # ---- THẺ NHỚ
    def _build_cards(self):
        st = self.probe.get_readiness_summary()["storage"]
        items = []
        for label, c, is_sec in (("SD1 (Primary)", st.get("primary"), False), ("SD2 (Secondary)", st.get("secondary"), True)):
            if not c or not c.get("mounted"):
                items.append(Item(label, "không có", "unknown", [("Chưa gắn thẻ ở khe 2." if is_sec else "!! Không xác định được thẻ chính.")]))
                continue
            fs = "chưa xác minh" if c.get("fs_detected") is False else c["filesystem"]
            ex = [f"Đường dẫn: {c['path']}", f"Filesystem: {fs}", f"Tổng: {c['total_mb']} MB", f"Trống: {c['free_mb']} MB ({100 - c['percent_used']:.0f}%)",
                  f"Read-only mount: {'CÓ' if c['read_only_mount'] else 'không'}", f"Ghi được: {'có' if c['writable'] else 'KHÔNG'}"]
            color = "green"
            if not c["writable"]:
                color = "red"
                ex.append("!! Thẻ không ghi được: kiểm tra khoá cơ học/rút cắm lại. Không thể sao lưu hay xoá ROM.")
            if c["free_mb"] < 200:
                color = "red"
                ex.append("!! Gần đầy (<200 MB): dùng Library > Dọn ROM trùng hoặc Xoá ROM để giải phóng.")
            elif c["free_mb"] < 1000:
                color = "yellow" if color == "green" else color
                ex.append("! Còn dưới 1 GB: dùng Library > Dọn ROM trùng để giải phóng dung lượng.")
            if c.get("fs_detected") is False:
                color = "unknown" if color == "green" else color
                ex.append("! Chưa xác minh được filesystem (không đọc được /proc/mounts); LoongMate không kết luận đúng/sai.")
            elif is_sec and not c.get("is_fat32"):
                color = "red"
                ex.append("!! Leaf yêu cầu thẻ khe 2 là FAT32. Sao lưu rồi format FAT32 trên máy tính — LoongMate KHÔNG format thẻ (sẽ xoá hết dữ liệu).")
            items.append(Item(label, f"{c['free_mb']:.0f} MB trống", color, ex))
        items.append(Item("Công cụ", head=True))
        items.append(Item("Chuyển game sang thẻ kia", "SHA-256", "yellow", [
            "Chuyển trọn bộ game (ROM, track, playlist, ảnh bìa) giữa SD1 và SD2 để giải phóng thẻ đang chật.",
            "Gợi ý hàng loạt theo mục tiêu dung lượng trống (1/2/3/5 GB), hoặc chọn từng game.",
            "+ Chép > xác minh SHA-256 từng file > chỉ khi cả game đủ mới xoá nguồn; lỗi thì hoàn tác cả game.",
            "! Save/state được CHÉP sang nhưng GIỮ LẠI ở thẻ cũ."], a=("Mở", self.open_transfer)))
        return ["Mỗi thẻ: đường dẫn, filesystem, dung lượng, quyền ghi."] + [f"! {w}" for w in st.get("warnings", [])], items

    # ---- SAVEVAULT
    def _build_savevault(self):
        snaps = self.vault.list_snapshots()
        rec = self.vault.check_crash_recovery()
        off = offsite_status(self.app_data)
        header = [f"{len(snaps)} snapshot (mới nhất trên cùng).", off["line"]]
        _vv = vault_verify_status(read_agent_state(self.app_data))
        if _vv["level"] in ("bad", "stale"):
            header.append(_vv["line"])
        items = []
        if rec:
            header.append("!! Phiên khôi phục trước bị gián đoạn — nên Rollback ngay.")
            items.append(Item("!! Rollback sau mất điện", "CẦN XỬ LÝ", "red", [
                "!! " + str(rec.get("message", "")), f"Đã lên kế hoạch {rec['planned_count']} file, mới khôi phục {rec['restored_count']}.",
                "Rollback đưa các file đã ghi dở về trạng thái trước khi khôi phục (từ pre-restore snapshot)." if rec.get("can_rollback")
                else "!! KHÔNG tìm thấy pre-restore snapshot — không rollback tự động được; kiểm tra dữ liệu thủ công."],
                a=("Rollback", lambda: self.ask_rollback(rec)) if rec.get("can_rollback") else None))
        items.append(Item("Khôi phục save một game", "", "unknown", [
            "Chọn MỘT game rồi chọn phiên bản save/state cũ của nó (từ các snapshot) để đưa về.",
            "Chỉ file save/state của game đó bị ghi; game khác không bị đụng.",
            "Có xem trước và hộp xác nhận (mặc định Huỷ); LoongMate tạo pre-restore snapshot trước khi ghi."],
            a=("Chọn game", self.open_game_saves)))
        items.append(Item("Sao lưu ngay", "", "unknown", [
            "Tạo snapshot mới của Saves/States (cả thẻ SD2 nếu có) và save của emulator standalone.",
            "Tăng tiến: file không đổi không bị chép lại; chỉ file đã đổi mới được băm.",
            "Ghi .partial > fsync > đổi tên; chỉ hợp lệ khi manifest được ghi cuối cùng.",
            "Không sửa gì ở Saves/States hiện tại."], a=("Sao lưu", self.do_backup)))
        tag = {"missing": "chưa có", "invalid": "lỗi file", "future": "giờ lệch"}.get(
            off["level"], "hôm nay" if off["age_days"] == 0 else f"{off['age_days']} ngày")
        oex = [off["line"], "Bản sao thứ hai nằm trên PC (không cùng thẻ với save): thẻ hỏng/mất thì vẫn còn.",
               f"Cảnh báo vàng khi cũ hơn {OFFSITE_STALE_DAYS} ngày, đỏ khi cũ hơn {OFFSITE_OLD_DAYS} ngày."]
        if off["last_pull_at"]:
            oex += [f"Lần kéo gần nhất: {off['last_pull_at']}" + (f" bởi {off['pc_host']}" if off["pc_host"] else ""),
                    f"Số snapshot đã kéo: {len(off['snapshot_ids'])}   Số đối tượng: {off['objects']}",
                    "+ Đã xác minh sau khi kéo." if off["verified"] else "!! Lần kéo này CHƯA được xác minh."]
        if off["level"] in ("missing", "invalid", "stale", "old", "future"):
            oex.append("Cài/kiểm tra lịch kéo tự động trên PC: pc/install_schedule.ps1")
        items.append(Item("Bản sao ngoài máy", tag, off["color"], oex))
        vv = vault_verify_status(read_agent_state(self.app_data))
        vtag = {"none": "chưa kiểm", "ok": "ổn", "stale": "cũ", "bad": f"{vv['count']} hỏng/thiếu"}[vv["level"]]
        vex = ["Agent định kỳ băm lại các object trong kho SaveVault để phát hiện thẻ nhớ hỏng ngầm trước khi bạn cần khôi phục.",
               f"Cảnh báo vàng khi lần kiểm cũ hơn {VERIFY_STALE_DAYS} ngày; đỏ khi có object hỏng/thiếu."]
        if vv["level"] == "none":
            vex.append("Chưa có kết quả kiểm tra (agent chưa chạy lần kiểm nào) — không phải lỗi.")
        else:
            vex.append(vv["line"])
            if vv["ts"] is not None:
                vex.append(f"Lần kiểm gần nhất: {_fmt_ts(vv['ts'])}")
            if vv["checked"] is not None:
                vex.append(f"Số object đã kiểm: {vv['checked']}")
            if vv["partial"]:
                vex.append("! Lần kiểm này chỉ một phần kho (chưa duyệt hết).")
            if vv["missing"]:
                vex.append(f"!! Thiếu {vv['missing']} object (manifest nhắc tới nhưng không còn trong kho).")
            if vv["bad"]:
                vex.append(f"!! Object hỏng ({len(vv['bad'])}):")
                vex += ["!!   " + b for b in vv["bad"][:12]]
                if len(vv["bad"]) > 12:
                    vex.append(f"!!   … và {len(vv['bad']) - 12} object nữa")
            if vv["level"] == "bad":
                vex.append("Nên kéo bản sao ngoài máy về hoặc sao lưu lại ngay.")
        items.append(Item("Toàn vẹn kho", vtag, vv["color"], vex))
        items.append(Item("Snapshot", head=True))
        for s in snaps:
            mbs = s.get("total_size_bytes", 0)
            sid = s["id"]
            items.append(Item(sid, f"{s.get('total_files', '?')} file", "green", [
                f"ID: {sid}", f"Tạo lúc: {s['created_at']}", f"Số file: {s.get('total_files', '?')}", f"Dung lượng: {mb(mbs)}",
                "A — Xem trước khôi phục: so sánh với Saves hiện tại, KHÔNG ghi gì.",
                "X — Xác minh: băm lại toàn bộ snapshot để chứng minh đọc lại được.",
                "Y — Khôi phục: ghi đè Saves bằng snapshot này (có hộp xác nhận)."],
                a=("Xem trước", lambda sid=sid: self.do_preview(sid)),
                x=("Xác minh", lambda sid=sid: self.do_verify(sid)),
                y=("Khôi phục", lambda sid=sid: self.ask_restore(sid))))
        if not snaps:
            items.append(Item("(chưa có snapshot — chọn Sao lưu ngay)", head=True))
        return header, items

    # ---- khôi phục save của MỘT game (SaveVault.game_index / plan_game_restore / restore_game_files)
    def open_game_saves(self):
        if not self.need(self.vault):
            return

        def done(idx):
            self.game_idx = idx
            self.push(ListScreen(self, "KHÔI PHỤC SAVE — chọn game", self._build_games))
        self.run("Đang đọc save và snapshot", self.vault.game_index, done)

    def _build_games(self):
        games = sorted((self.game_idx or {}).get("games", {}).values(), key=lambda g: (g["name"].lower(), g["key"]))
        header = [f"{len(games)} game có save/state hiện tại hoặc trong snapshot.",
                  "Chọn game để xem các phiên bản save đã sao lưu."]
        items = []
        for g in games:
            v = len(g["snapshots"])
            ex = [f"Game: {g['name']}", f"Emulator/core: {g['core'] or '-'}",
                  f"File hiện tại: {len(g['current'])}" + (f" ({mb(sum(f['size'] for f in g['current']))})" if g["current"] else ""),
                  f"Phiên bản trong snapshot: {v}", ""]
            ex += [f"• {f['rel_path']}" for f in g["current"][:8]]
            if len(g["current"]) > 8:
                ex.append(f"… và {len(g['current']) - 8} file nữa")
            if not v:
                ex.append("!! Chưa có snapshot nào chứa save của game này — chọn Sao lưu ngay trước.")
            items.append(Item(g["name"], f"{v} bản" if v else "chưa có bản", "green" if v else "unknown", ex,
                              a=("Xem phiên bản", lambda k=g["key"]: self.open_game_versions(k))))
        if not items:
            items.append(Item("(chưa có save nào)", head=True))
        return header, items

    def open_game_versions(self, key):
        g = (self.game_idx or {}).get("games", {}).get(key)
        if g is None or not g["snapshots"]:
            self.info("Chưa có phiên bản", ["Chưa có snapshot nào chứa save của game này.", "Chọn Sao lưu ngay ở màn SaveVault trước."])
            return
        self.push(ListScreen(self, f"KHÔI PHỤC SAVE — {g['name']}", lambda: self._build_game_versions(key)))

    def _build_game_versions(self, key):
        idx = self.game_idx
        g, cur_files = idx["games"][key], idx["current"].get("files", {})
        header = [f"{g['name']}: các phiên bản save/state trong snapshot (mới nhất trên cùng).",
                  "A — xem trước rồi khôi phục. Chỉ file của game này được ghi."]
        items = []
        for s in idx["snapshots"]:
            infos = g["snapshots"].get(s["id"])
            if not infos:
                continue
            same = [cur_files.get(rel) is not None and cur_files[rel]["sha256"] == info["sha256"] for rel, info in infos.items()]
            ex = [f"Snapshot: {s['id']}", f"Nhãn: {s['label'] or '-'}", f"Tạo lúc: {s['created_at']}",
                  f"{len(infos)} file, {mb(sum(i['size'] for i in infos.values()))}", ""]
            for rel in sorted(infos):
                cur = cur_files.get(rel)
                state = ("chưa có file hiện tại" if cur is None else
                         "GIỐNG bản hiện tại" if cur["sha256"] == infos[rel]["sha256"] else "khác bản hiện tại")
                ex.append(f"• {rel.split('/')[-1]} — {mb(infos[rel]['size'])} — {state}")
            tag = "giống hiện tại" if all(same) else mb(sum(i["size"] for i in infos.values()))
            items.append(Item(f"{s['created_at']}  {s['label']}", tag, "green" if all(same) else "unknown", ex,
                              a=("Xem trước & khôi phục", lambda sid=s["id"]: self.ask_game_restore(key, sid))))
        return header, items

    def ask_game_restore(self, key, sid):
        def work():
            idx = self.vault.game_index()   # tính lại: file hiện tại có thể đã đổi từ lúc mở danh sách
            g = idx["games"].get(key)
            return idx, (self.vault.plan_game_restore(g, sid, idx["current"]) if g else None)

        def done(res):
            idx, planned = res
            self.game_idx = idx
            if planned is None:
                self.info("Không thể khôi phục", ["!! Snapshot này không còn file nào của game."])
                return
            plan, to_write = planned
            name = idx["games"][key]["name"]
            if not to_write:
                self.info("Không cần khôi phục", [f"+ Mọi file của {name} đã giống hệt bản trong snapshot {sid}."])
                return
            tags = {"create": "+ [TẠO MỚI]", "overwrite": "[GHI ĐÈ]", "unchanged": "[GIỮ NGUYÊN]"}
            lines = [f"!! Khôi phục CHỈ file save/state của {name} từ snapshot {sid}.",
                     "• LoongMate tự tạo pre-restore snapshot trước khi ghi (đường lùi, chứa cả bản đang chơi của game).",
                     "• Game khác không bị đụng.", ""]
            for f in plan:
                lines.append(f"{tags[f['action']]} {f['rel']}  ({mb(f['size'])}"
                             + (f", đang có {mb(f['current_size'])}" if f["current_size"] is not None else "") + ")")
                if f["newer_than_snapshot"]:
                    lines.append("! bản đang chơi MỚI HƠN snapshot — sẽ được cất vào SaveVault/conflicts/")
            self.confirm("Xác nhận khôi phục save", lines, lambda: self.do_game_restore(key, sid, to_write), "Khôi phục")
        self.run("Đang so sánh với save hiện tại", work, done)

    def do_game_restore(self, key, sid, to_write):
        name = ((self.game_idx or {}).get("games", {}).get(key) or {}).get("name", key)

        def done(r):
            if not r.get("success"):
                self.info("Khôi phục thất bại", ["!! " + str(r.get("message", "không rõ nguyên nhân")),
                                                 "Nếu đã ghi dở, mở lại SaveVault: sẽ có mục Rollback."])
                return
            lines = [f"+ Đã khôi phục {len(r['restored'])} file của {name}" + (" (đã băm lại, khớp snapshot)." if r["verified"] else "."),
                     f"Đường lùi: snapshot {r['pre_restore_id']} (nhãn pre_restore).",
                     "Muốn quay lại: mở lại Khôi phục save một game, chọn game này rồi chọn snapshot đó."]
            if r["failed"]:
                lines.append(f"!! {len(r['failed'])} file KHÔNG khớp sau khi ghi: " + ", ".join(r["failed"]))
            if r["preserved_conflicts"]:
                lines.append(f"Bản đang chơi (mới hơn) được cất ở: {r['conflicts_dir']}")
            if r["rejected_paths"] or r["skipped_readonly"]:
                lines.append(f"!! Bị bỏ qua: {r['rejected_paths'] + r['skipped_readonly']}")
            self.game_idx = None
            while len(self.stack) > 2:   # về màn SaveVault: danh sách game/phiên bản đã cũ
                self.stack.pop()
            self.info("Kết quả khôi phục save", lines)
            self.refresh_cur()
        self.run("Đang khôi phục save", lambda: self.vault.restore_game_files(sid, to_write), done)

    def do_backup(self):
        if not self.need(self.vault):
            return

        def done(snap):
            if snap.get("success"):
                lines = ["+ Sao lưu thành công.", f"Snapshot: {snap['snapshot_id']}", f"Số file: {snap['files_count']}",
                         f"Dung lượng: {mb(snap['total_bytes'])}", f"Vị trí: {snap.get('path')}"]
            else:
                lines = ["!! Sao lưu thất bại:", str(snap.get("message", "không rõ nguyên nhân"))]
            self.info("Sao lưu SaveVault", lines)
            self.refresh_cur()
        self.run("Đang sao lưu", self.vault.create_snapshot, done)

    def do_verify(self, sid):
        def done(r):
            if r.get("ok"):
                lines = [f"+ Snapshot {sid} HỢP LỆ.", f"Đã kiểm tra {r.get('checked', 0)} file."]
            else:
                lines = [f"!! Snapshot {sid} KHÔNG hợp lệ.", f"Đã kiểm tra: {r.get('checked', 0)} file"]
                for k in ("error", "message", "missing", "size_mismatch", "hash_mismatch"):
                    if r.get(k):
                        lines.append(f"{k}: {r[k]}")
            self.info("Xác minh snapshot", lines)
        self.run("Đang xác minh", lambda: self.vault.verify_snapshot(sid), done)

    @staticmethod
    def _preview_lines(p, sid):
        if p.get("error"):
            return None, [f"!! {p.get('message', p['error'])}"]
        conf = [c["rel_path"] if isinstance(c, dict) else str(c) for c in p.get("conflicts", [])]
        lines = [f"Snapshot {sid}", f"Thêm mới: {len(p['to_add'])} file", f"Cập nhật: {len(p['to_update'])} file",
                 f"Xung đột (giữ cả hai): {len(conf)} file", f"Không đổi: {len(p['unchanged'])} file", ""]
        lines += [f"+ [THÊM] {f}" for f in p["to_add"]] + [f"[CẬP NHẬT] {f}" for f in p["to_update"]]
        lines += [f"! [XUNG ĐỘT] {f}" for f in conf]
        return (len(p["to_add"]), len(p["to_update"]), len(conf), len(p["unchanged"])), lines

    def do_preview(self, sid):
        def done(p):
            _n, lines = self._preview_lines(p, sid)
            self.info("Xem trước khôi phục (chưa ghi gì)", lines)
        self.run("Đang so sánh", lambda: self.vault.preview_restore(sid), done)

    def ask_restore(self, sid):
        def after_preview(p):
            n, lines = self._preview_lines(p, sid)
            if n is None:
                self.info("Không thể khôi phục", lines)
                return
            intro = ["!! KHÔI PHỤC sẽ ghi đè Saves/States đang chơi bằng nội dung snapshot.",
                     "• LoongMate TỰ TẠO pre-restore snapshot trước khi ghi (đường lùi).",
                     "• File xung đột: bản đang chơi được CẤT vào SaveVault/conflicts/, không bị mất.",
                     f"• So với Saves hiện tại: thêm {n[0]}, cập nhật {n[1]}, xung đột (giữ cả hai) {n[2]}, không đổi {n[3]}.",
                     "• Engine khôi phục toàn bộ file của snapshot nên file 'không đổi' cũng có thể được ghi lại cùng nội dung.",
                     "• Bản đang chơi không có trong snapshot sẽ được giữ nguyên.", ""]
            self.confirm("Xác nhận khôi phục", intro + lines[1:], lambda: self.do_restore(sid), "Khôi phục")
        self.run("Đang tính kế hoạch", lambda: self.vault.preview_restore(sid), after_preview)

    def do_restore(self, sid):
        def done(r):
            if r.get("success"):
                lines = ["+ Khôi phục xong.", f"Số file đã khôi phục: {r.get('restored_files_count')}",
                         f"Pre-restore snapshot (đường lùi): {r.get('pre_restore_id')}",
                         f"Xung đột được bảo toàn: {r.get('preserved_conflicts')}" + (f" (trong {r.get('conflicts_dir')})" if r.get("preserved_conflicts") else ""),
                         f"Path bị từ chối: {r.get('rejected_paths')}", f"Bỏ qua do read-only/gần đầy: {r.get('skipped_readonly')}"]
            else:
                lines = ["!! Khôi phục thất bại:", str(r.get("message", "không rõ")),
                         "Nếu đã ghi dở, mở lại SaveVault: sẽ có mục Rollback."]
            self.info("Kết quả khôi phục", lines)
            self.refresh_cur()
        self.run("Đang khôi phục", lambda: self.vault.restore_snapshot(sid), done)

    def ask_rollback(self, rec):
        self.confirm("Xác nhận rollback", [
            "!! Rollback sẽ đưa Saves/States về bản pre-restore snapshot " + str(rec.get("journal", {}).get("pre_restore_id")) + ".",
            f"Phiên khôi phục dở dang: đã ghi {rec['restored_count']}/{rec['planned_count']} file.",
            "Nội dung ghi dở sau lần khôi phục đó sẽ bị thay bằng bản trước khi khôi phục."], self.do_rollback, "Rollback")

    def do_rollback(self):
        def done(r):
            self.info("Kết quả rollback", [("+ " if r.get("success") else "!! ") + str(r.get("message", r))])
            self.refresh_cur()
        self.run("Đang rollback", self.vault.rollback_from_journal, done)

    # ---- gói hỗ trợ
    def ask_bundle(self):
        if not self.need(self.health, self.doctor, self.inv_engine):
            return
        self.confirm("Xuất gói hỗ trợ", [
            "Sẽ quét thư viện (~15 giây) rồi tạo file ZIP chẩn đoán trên thẻ.",
            "Gói gồm: summary.json, checks.txt, mounts.txt, environment.txt, library_issues.json, logs.txt.",
            "+ Mật khẩu, token, khoá SSH đã được lọc khỏi mọi file.", "+ Privacy Mode: tên game bị ẩn danh.",
            "Không tự gửi đi đâu; bạn tự chép file ra."], self.do_bundle, "Xuất gói")

    def do_bundle(self):
        def work():
            readiness = self.probe.get_readiness_summary()
            self.inv = self.inv_engine.scan_all()
            report = self.doctor.audit(self.inv, readiness["storage"])
            save_audit_cache(self.app_data, report)
            self.report = report
            return self.health.export_support_bundle(readiness, report, privacy_mode=True)

        def done(path):
            self.info("Gói hỗ trợ", ["+ Đã xuất gói hỗ trợ tại:", str(path)])
            self.refresh_cur()
        self.run("Đang xuất gói hỗ trợ", work, done)

    # ---- LIBRARY
    def _build_library(self):
        cache = self.report_summary()
        rep = self.report
        header = []
        if cache:
            sev = cache.get("severity_counts", {})
            header.append(f"Số liệu lúc {cache.get('generated_at', 'không rõ')}: {cache.get('total_issues', 0)} vấn đề "
                          f"(NT {sev.get('critical', 0)} / CB {sev.get('warning', 0)} / GC {sev.get('info', 0)})")
        else:
            header.append("! Chưa quét lần nào.")
        if rep is None:
            header.append("Cache chỉ giữ tóm tắt: chọn Quét lại để xem danh sách vấn đề chi tiết.")
        fix = (rep or {}).get("fixable_actions", [])
        items = [
            Item("Quét lại thư viện", "~15 giây", "unknown", [
                "Quét ROM, ảnh bìa, save, BIOS trên cả hai thẻ (chỉ đọc), chạy các luật kiểm toán, rồi lưu bản tóm tắt.",
                "Không sửa hay xoá gì.", "Sau khi quét, mục Danh sách vấn đề sẽ có chi tiết."], a=("Quét", self.do_scan)),
            Item("Danh sách vấn đề", f"{rep['total_issues']}" if rep else "cần quét", "unknown" if not rep else "yellow", [
                "Từng vấn đề kèm mô tả và việc cần làm, nhóm theo mức độ." if rep else "Chưa có danh sách chi tiết trong phiên này — hãy Quét lại trước."],
                a=("Mở", self.open_issues) if rep else None),
            Item("Áp dụng sửa an toàn", f"{len(fix)} việc" if rep else "cần quét", "unknown", [
                "Chỉ gồm các sửa an toàn do Library Doctor đề xuất (vd tạo thư mục thiếu, sửa .cue có ánh xạ rõ ràng).",
                "Bạn sẽ thấy danh sách từng việc rồi mới xác nhận; kết quả hiện từng việc một.",
                "Không xoá file." if fix else ("Không có sửa an toàn nào." if rep else "Cần quét trước.")],
                a=("Xem & áp dụng", self.ask_fixes) if fix else None),
            Item("Sửa hàng loạt", "xem trước", "unknown", [
                "Quét thư viện rồi gom các sửa một chạm: tạo thư mục chuẩn, sửa .cue, tạo .m3u cho game nhiều đĩa, gộp thư mục tên cũ.",
                "Bạn xem từng bước (rủi ro, hoàn tác được không) rồi mới xác nhận; lỗi từng bước không chặn các bước khác.",
                "Mọi bước ghi vào nhật ký để hoàn tác (mục bên dưới)."], a=("Quét & xem kế hoạch", self.open_batchfix)),
            self._undo_item(),
            Item("Kiểm tra BIOS", "md5", "unknown", [
                "Bảng hệ máy x file BIOS: ok / sai hash / thiếu / chưa có hash nguồn. Chỉ đọc.",
                bios_mod.NO_DOWNLOAD], a=("Kiểm tra", self.open_bios)),
            Item("Dọn ROM trùng", "SHA-256", "yellow", [
                "Tìm ROM giống hệt nhau từng byte (băm SHA-256) giữa/ trong hai thẻ để giải phóng dung lượng.",
                "Bước 1 chọn giữ bản ở thẻ nào, bước 2 xem từng nhóm, bước 3 xác nhận mới xoá.",
                "! Có thể mất vài phút trên máy thật (đã đo ~260 giây).",
                "+ Bản có save không bao giờ bị xoá; game có playlist chỉ giữ bản trong playlist."], a=("Bắt đầu", self.open_dedupe)),
            Item("Nhập game", "Import/", "unknown", [
                "Nhập game bạn TỰ ĐƯA VÀO máy: thư mục Import/ ở gốc mỗi thẻ (file ROM, .zip, thư mục). Chỉ nhập nội dung bạn có quyền sử dụng.",
                "Tự nhận diện hệ máy bằng header (không đoán): mơ hồ thì hỏi bạn. Cài vào thẻ PHỤ trước; thẻ Leaf chỉ khi bạn đồng ý.",
                "Giải nén an toàn trong thư mục tạm rồi mới chuyển vào Roms/; mất điện giữa chừng không để lại file nửa vời.",
                "! .7z/.rar chưa hỗ trợ trên máy: giải nén trên PC."], a=("Mở", self.open_import)),
            Item("Thư viện riêng", "PocketBridge", "unknown", [
                "Duyệt + tải + cài game từ agent PocketBridge do CHÍNH BẠN dựng (Pi/PC/Mac) và đã ghép đôi. LoongMate không có danh mục ROM nào.",
                "Kết nối qua Tailscale (SOCKS5 userspace), TLS ghim fingerprint; token lưu ở /userdata (0600).",
                "Tải có tiếp tục được (Range + If-Range), kiểm SHA-256 theo server, rồi cài bằng trình Nhập game.",
                "Ghép đôi: chép bundle vào <thẻ Leaf>/PocketBridge-import/ rồi chọn Ghép đôi thư viện."], a=("Mở", self.open_lib_nodes)),
            Item("Homebrew", "tải trực tiếp", "unknown", [
                "Tải game homebrew hợp pháp trực tiếp từ Homebrew Hub qua Wi-Fi (không cần PC): chọn rồi cài như một app.",
                "CHỈ hiện game có giấy phép rõ ràng (MIT, CC, GPL...); mục không giấy phép bị ẩn, không đoán.",
                "HTTPS kiểm chứng thư đầy đủ, chỉ máy chủ Homebrew Hub/GitHub; hash trong danh mục sai thì không cài.",
                "Tải có tiếp tục được (Range + If-Range), cài bằng trình Nhập game; ghi chú giấy phép lưu ngoài Roms/."], a=("Mở", self.open_hb)),
            Item("Xoá ROM", "", "red", [
                "Chọn một hoặc nhiều game để xoá: duyệt hệ máy > game, bấm X để đánh dấu nhiều game rồi xoá cả lô.",
                "Xem kế hoạch đầy đủ (mọi file, ảnh bìa, MB, save được GIỮ LẠI) trước khi xác nhận.",
                "Xoá VĨNH VIỄN, có ghi sổ xoá."], a=("Duyệt", self.open_rom_systems)),
        ]
        return header, items

    def do_scan(self):
        if not self.need(self.doctor, self.inv_engine):
            return

        def work():
            self.inv = self.inv_engine.scan_all()
            report = self.doctor.audit(self.inv, self.probe.get_readiness_summary()["storage"])
            save_audit_cache(self.app_data, report)
            return report

        def done(rep):
            self.report = rep
            sev = rep.get("severity_counts", {})
            self.info("Library Doctor", [f"+ Đã quét xong lúc {time.strftime('%H:%M:%S')}", f"Tổng: {rep.get('total_issues', 0)} vấn đề",
                                         f"Nghiêm trọng: {sev.get('critical', 0)}  Cảnh báo: {sev.get('warning', 0)}  Ghi chú: {sev.get('info', 0)}",
                                         f"Sửa an toàn khả dụng: {len(rep.get('fixable_actions', []))} việc"])
            self.refresh_cur()
        self.run("Đang quét thư viện", work, done)

    def open_issues(self):
        def build():
            issues = self.report["issues"] if self.report else []
            items = []
            for sev in ("critical", "warning", "info"):
                grp = [i for i in issues if i.get("severity") == sev]
                if not grp:
                    continue
                items.append(Item(f"{SEV_NAME[sev]} ({len(grp)})", head=True))
                for i in grp:
                    ex = [str(i.get("title")), f"Mức độ: {SEV_NAME[sev]}", f"Mã lỗi: {i.get('code', '?')}", "", str(i.get("description", ""))]
                    if i.get("guide"):
                        ex += ["", "Cần làm: " + str(i["guide"])]
                    items.append(Item(f"[{i.get('category')}] {i.get('title')}", {"critical": "NT", "warning": "CB", "info": "GC"}[sev], SEV_COLOR[sev], ex))
            return [f"{len(issues)} vấn đề từ lần quét gần nhất trong phiên này."], items
        self.push(ListScreen(self, "DANH SÁCH VẤN ĐỀ", build))

    def ask_fixes(self):
        fix = self.report["fixable_actions"]
        self.confirm("Áp dụng sửa an toàn", [f"Sẽ thực hiện {len(fix)} việc sau, lần lượt:"] + [f"• {a['label']}" for a in fix] +
                     ["", "Mỗi việc chỉ ghi trong vùng cho phép; kết quả từng việc sẽ hiện sau đó."], self.do_fixes, "Áp dụng")

    def do_fixes(self):
        actions = list(self.report["fixable_actions"])

        def work():
            return [(a["label"], self.doctor.execute_fix(a)) for a in actions]

        def done(res):
            lines = [("+ " if r.get("success") else "!! ") + f"{lab}: {'Thành công' if r.get('success') else 'Thất bại'} ({r.get('message')})" for lab, r in res]
            lines += ["", "Hãy Quét lại để cập nhật danh sách vấn đề."]
            self.report = None
            self.info("Kết quả áp dụng sửa", lines)
            self.refresh_cur()
        self.run("Đang áp dụng sửa", work, done)

    # ---- DỌN ROM TRÙNG
    PREFER = (("auto", "Tự động (khuyến nghị)", "Giữ bản có save > có ảnh bìa > thư mục hệ máy chuẩn > thẻ đang trống hơn > đường dẫn ngắn."),
              ("primary", "Giữ bản ở SD1", "Ưu tiên giữ bản trên SD1 và xoá bản trùng ở SD2 (sau tiêu chí save)."),
              ("secondary", "Giữ bản ở SD2", "Ưu tiên giữ bản trên SD2 và xoá bản trùng ở SD1 — giải phóng SD1 đang chật."))

    def open_dedupe(self):
        if not self.need(self.doctor, self.inv_engine):
            return

        def build():
            items, cmp = [], self.dup_compare
            for key, name, why in self.PREFER:
                rep = self.dup_cache.get(key)
                by = (rep["reclaimable_by_card"] if rep else (cmp[key]["by_card"] if cmp else None))
                ex = [why, "Bản có save/state không bao giờ bị xoá; bản đang trong playlist luôn được giữ — bất kể lựa chọn."]
                if by is not None:
                    ex += ["", f"Thu hồi trên SD1: {mb(by['primary'])}", f"Thu hồi trên SD2: {mb(by['secondary'])}"]
                    tag = f"SD1 {mb(by['primary'])} / SD2 {mb(by['secondary'])}"
                else:
                    ex += ["", "Chưa tính. Bấm A để băm các file nghi trùng (có thể vài phút)."]
                    tag = "chưa tính"
                ex.append("Kết quả đã tính được nhớ trong phiên." if rep else "! Đổi lựa chọn khác sẽ phải băm lại.")
                items.append(Item(name, tag, "green" if rep else "unknown", ex, a=("Chọn & tính" if not rep else "Xem nhóm", lambda k=key: self.dedupe_go(k))))
            hdr = ["Bước 1/3: chọn giữ bản ở thẻ nào."]
            _p = read_agent_state(self.app_data).get("prewarm")
            if isinstance(_p, dict) and _p.get("done") is True:
                hdr.append("+ Agent đã băm trước các file nghi trùng: lần quét sẽ nhanh.")
            if self.dup_cache:
                r = next(iter(self.dup_cache.values()))
                hdr.append(f"Đã băm {r['hashed_files']} file ({mb(r['hashed_bytes'])}).{hash_cache_note(r)}")
            return hdr, items
        self.push(ListScreen(self, "DỌN ROM TRÙNG", build))

    def dedupe_go(self, prefer):
        if prefer in self.dup_cache:
            self.open_dup_groups(prefer)
            return

        def work():
            self.inv = self.inv_engine.scan_all()
            cache = hashcache_mod.HashCache(self.app_data)   # chỉ để tìm ứng viên nhanh; xoá luôn băm lại
            try:
                rep = dedupe_mod.find_duplicates(self.inv, self.doctor.systems_catalog, self.probe.primary_path,
                                                 self.probe.secondary_path, None, prefer_card=prefer, compare=True, cache=cache)
            finally:
                cache.close()
            return rep, self._art_groups_needing_copy(rep["groups"])

        def done(res):
            rep, need = res
            self.dup_cache[prefer] = rep
            self.dup_art_need[prefer] = need
            self.dup_art[prefer] = need > 0     # mặc định BẬT khi có nhóm giữ ở thẻ chưa có ảnh mà thẻ kia có
            self.dup_compare = rep.get("compare")
            self.refresh_cur()
            self.open_dup_groups(prefer)
        self.run("Đang băm tìm ROM trùng", work, done)

    def _art_groups_needing_copy(self, groups) -> int:
        """Số nhóm mà thẻ chứa bản GIỮ chưa có ảnh bìa nhưng thẻ kia có (chỉ đọc)."""
        p, s = self.probe.primary_path, self.probe.secondary_path
        n = 0
        for g in groups:
            loc = artwork_mod.locate_rom(g["keep"]["path"], p, s)
            if loc is None:
                continue
            card, system, _rel = loc
            stem = Path(g["keep"]["path"]).stem
            if artwork_mod.find_art_in_card(card, system, stem) is not None:
                continue
            others = [Path(r).resolve() for r in (p, s) if r and Path(r).resolve() != card]
            if any(artwork_mod.find_art_in_card(o, system, stem) is not None for o in others):
                n += 1
        return n

    def open_dup_groups(self, prefer):
        rep = self.dup_cache[prefer]
        groups = rep["groups"]
        n_files = sum(len(g["remove"]) for g in groups)
        by = rep["reclaimable_by_card"]

        def build():
            hdr = [f"Bước 2/3: {len(groups)} nhóm trùng • xoá {n_files} file = {mb(rep['reclaimable_bytes'])} (SD1 {mb(by['primary'])}, SD2 {mb(by['secondary'])})"]
            if hash_cache_note(rep):
                hdr.append(hash_cache_note(rep).strip())
            hdr += [f"! Bỏ qua {rep['skipped_budget_files']} file vượt giới hạn băm."] if rep.get("skipped_budget_files") else []
            hdr += [f"! Không băm được {e['path']}: {e['reason']}" for e in rep.get("hash_errors", [])[:2]]
            items = []
            if groups:
                items.append(Item("Xoá TẤT CẢ các bản trùng", f"{n_files} file", "red", [
                    f"Sẽ xoá {n_files} file, thu hồi {mb(rep['reclaimable_bytes'])}.", f"SD1: {mb(by['primary'])}   SD2: {mb(by['secondary'])}",
                    "Bước 3 là hộp xác nhận. Mỗi file được băm lại ngay trước khi xoá."], a=("Tiếp: xác nhận", lambda: self.ask_dedupe(prefer))))
                need, on = self.dup_art_need.get(prefer, 0), self.dup_art.get(prefer, False)
                items.append(Item("Chép ảnh bìa sang thẻ giữ", "BẬT" if on else "TẮT", "green" if on else "unknown", [
                    "Trước khi xoá, nếu thẻ chứa bản GIỮ chưa có ảnh bìa mà thẻ kia có, ảnh được chép sang (xác minh SHA-256, không ghi đè ảnh có sẵn).",
                    f"Hiện có {need} nhóm cần chép ảnh." if need else "Hiện không nhóm nào cần chép ảnh (thẻ giữ đã có ảnh, hoặc không thẻ nào có).",
                    "Mặc định BẬT khi có nhóm cần chép. Lỗi chép ảnh không chặn việc xoá.",
                    "# CHƯA KIỂM CHỨNG: Leaf có tự tìm ảnh bìa chéo thẻ hay không; chép thừa một ảnh nhỏ còn hơn mất ảnh.",
                    "A — bật/tắt."], a=("Bật/tắt", lambda: self.toggle_dup_art(prefer))))
            items.append(Item("Các nhóm trùng", head=True))
            for g in groups:
                k = g["keep"]
                ex = [f"Game: {k['name']} ({k['system']}) — {mb(g['size'])}", "", "GIỮ:", k["path"], "Lý do: " + g["keep_reason"]]
                ex += [f"GIỮ (cũng cần): {o['path']}" for o in g.get("also_kept", [])]
                ex += ["", "XOÁ:"]
                for r in g["remove"]:
                    ex.append(f"{r['path']} [{r['card_id']}]" + (" !! CÓ SAVE" if r["has_save"] else ""))
                ex += ["", "+ Bản có save không bao giờ bị xoá; game có playlist chỉ giữ bản trong playlist."]
                items.append(Item(k["name"], f"-{mb(g['size'] * len(g['remove']))}", "yellow", ex))
            if rep.get("skipped_multifile"):
                items.append(Item("Game nhiều file nghi trùng (KHÔNG đề xuất xoá)", head=True))
                for m in rep["skipped_multifile"]:
                    items.append(Item(m["name"], "chỉ báo", "unknown", [m["reason"]] + m["paths"]))
            if not groups:
                items.append(Item("(Không có ROM trùng nào để dọn)", head=True))
            return hdr, items
        self.push(ListScreen(self, "ROM TRÙNG — các nhóm", build))

    def toggle_dup_art(self, prefer):
        self.dup_art[prefer] = not self.dup_art.get(prefer, False)
        self.cur().refresh()

    def ask_dedupe(self, prefer):
        rep = self.dup_cache[prefer]
        groups = rep["groups"]
        n = sum(len(g["remove"]) for g in groups)
        by = rep["reclaimable_by_card"]
        self.confirm("Bước 3/3 — Xác nhận xoá ROM trùng", [
            f"!! XOÁ VĨNH VIỄN {n} file trong {len(groups)} nhóm.", f"Thu hồi: {mb(rep['reclaimable_bytes'])} (SD1 {mb(by['primary'])}, SD2 {mb(by['secondary'])}).",
            "Không dùng Quarantine: dung lượng được giải phóng ngay.",
            (f"Ảnh bìa: sẽ chép sang thẻ giữ trước khi xoá ({self.dup_art_need.get(prefer, 0)} nhóm cần)."
             if self.dup_art.get(prefer) else "Ảnh bìa: KHÔNG chép (đã tắt)."),
            "Trước khi xoá, mỗi file được băm lại và so với bản giữ; file nào đã đổi sẽ bị TỪ CHỐI.",
            f"Mọi lần xoá được ghi vào sổ: {self.probe.userdata_path / dedupe_mod.LEDGER_NAME}",
            "Save/state không bao giờ bị xoá."], lambda: self.do_dedupe(prefer), "XOÁ")

    def do_dedupe(self, prefer):
        groups = self.dup_cache[prefer]["groups"]
        art = bool(self.dup_art.get(prefer))

        def work():
            out = []
            for g in groups:   # y hệt cli._cmd_dedupe
                out.append(dedupe_mod.execute_deletion(
                    [r["path"] for r in g["remove"]], self.probe.userdata_path, "duplicate",
                    kept_path=g["keep"]["path"], expected_sha256=g["sha256"],
                    primary_path=self.probe.primary_path, secondary_path=self.probe.secondary_path, quarantine=False,
                    copy_artwork=art))
            return out

        def done(results):
            self.dup_cache.clear()
            self.dup_compare = None
            self.inv = None
            n = sum(len(r["deleted"]) for r in results)
            freed = sum(r["bytes_freed"] for r in results)
            lines = [("+ " if n else "") + f"Đã xoá {n} file, giải phóng {mb(freed)}."]
            for r in results:
                lines += [f"!! TỪ CHỐI {x['path']}: {x['reason']}" for x in r["refused"]]
            arts = [r["artwork"] for r in results if r.get("artwork")]
            if art:
                cnt: Dict[str, int] = {}
                for a in arts:
                    cnt[a["status"]] = cnt.get(a["status"], 0) + 1
                lines.append("Ảnh bìa: " + (", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) or "không có nhóm nào xử lý ảnh")
                             + "  (copied = đã chép sang thẻ giữ)")
                lines += [f"! Ảnh bìa {a['status']}: {a['message']}" for a in arts if a["status"] in ("error", "refused")]
            lines += [f"Sổ xoá: {self.probe.userdata_path / dedupe_mod.LEDGER_NAME}"]
            while len(self.stack) > 2:   # về menu Library
                self.stack.pop()
            self.finish_change("Kết quả dọn ROM trùng", lines, n > 0)
            self.refresh_cur()
        self.run("Đang xoá ROM trùng", work, done)

    # ---- XOÁ ROM
    def _with_inv(self, then, label="Đang quét ROM"):
        if self.inv is not None:
            then()
            return

        def done(inv):
            self.inv = inv
            then()
        self.run(label, self.inv_engine.scan_all, done)

    def open_rom_systems(self):
        if not self.need(self.inv_engine):
            return
        self._with_inv(lambda: self.push(ListScreen(self, "XOÁ ROM — chọn hệ máy", self._build_systems)))

    def _roms(self):
        out = []
        for cid in ("primary", "secondary"):
            c = (self.inv or {}).get(cid)
            if c:
                out += c["roms"]
        return out

    def _build_systems(self):
        by: Dict[str, List[dict]] = {}
        for r in self._roms():
            by.setdefault(r["system"], []).append(r)
        items = []
        for s in sorted(by, key=str.lower):
            rs = by[s]
            n1 = sum(1 for r in rs if r["card_id"] == "primary")
            items.append(Item(s, f"{len(rs)} file", "unknown", [
                f"Hệ máy: {s}", f"SD1: {n1} file   SD2: {len(rs) - n1} file", f"Tổng dung lượng: {mb(sum(r['size'] for r in rs))}",
                "A — duyệt danh sách game của hệ này (gộp cả hai thẻ)."], a=("Duyệt", lambda s=s: self.open_rom_games(s))))
        header = [f"{len(items)} hệ máy. Chọn hệ rồi chọn game để xoá."]
        if items:
            items.insert(0, Item("Game lớn nhất (mọi hệ)", f"{min(ROM_TOP_N, len(self._roms()))} game", "unknown", [
                f"{ROM_TOP_N} game nặng nhất trên cả hai thẻ, mọi hệ máy.",
                "Dùng để dọn nhanh khi thẻ gần đầy.",
                "A — duyệt (chọn nhiều bằng X, xoá cả lô, lọc có/không save)."], a=("Duyệt", lambda: self.open_rom_games(ROM_TOP))))
        return header, items

    def _rom_scope(self, scope):
        """Mọi file ROM thuộc phạm vi (chưa lọc): một hệ máy, hoặc tất cả khi scope == ROM_TOP."""
        rs = self._roms()
        return rs if scope == ROM_TOP else [r for r in rs if r["system"] == scope]

    def _rom_view(self, scope) -> Dict[str, str]:
        return self.rom_view.setdefault(scope, {"sort": "size" if scope == ROM_TOP else "name", "save": "all"})

    def open_rom_games(self, system):
        top = system == ROM_TOP
        marks = self.rom_marks.setdefault(system, set())
        view = self._rom_view(system)
        holder: List[Any] = []

        def build():
            every = self._rom_scope(system)
            # game đã mất thì bỏ đánh dấu; KHÔNG theo bộ lọc (lọc/sắp xếp không được làm mất dấu chọn)
            marks.intersection_update(r["absolute_path"] for r in every)
            stems = rom_save_stems(self.inv)
            rs = rom_view_rows(every, stems, view["save"], view["sort"], ROM_TOP_N if top else None)
            msize = sum(r["size"] for r in every if r["absolute_path"] in marks)
            shown = {r["absolute_path"] for r in rs}
            if top:
                header = [f"{len(rs)} game nặng nhất (gộp SD1 + SD2, mọi hệ)." if rs else "Không có game nào khớp."]
            else:
                header = [f"Hệ {system}: {len(every)} file (gộp SD1 + SD2)." if every else f"Hệ {system}: Không còn game."]
            header.append(f"Sắp xếp: {ROM_SORT_LABEL[view['sort']]} (Y đổi).   Lọc: {ROM_SAVE_LABEL[view['save']]} (A trên mục Lọc).")
            if marks:
                hidden = len(marks - shown)
                header.append(f"Đã chọn {len(marks)} game ({mb(msize)})" + (f", {hidden} đang bị bộ lọc ẩn" if hidden else "")
                              + ". X chọn/bỏ chọn, mục đầu danh sách để xoá cả lô.")
            nxt_sort = ROM_SORT_LABEL["name" if view["sort"] == "size" else "size"]
            y = (f"Sắp xếp theo {nxt_sort}", lambda: self.rom_toggle_sort(holder[0], system))
            items = []
            if marks:
                items.append(Item(f"Xoá {len(marks)} game đã chọn", mb(msize), "red", [
                    f"Đã chọn {len(marks)} game, file đã chọn nặng {mb(msize)} (chưa tính file đi kèm).",
                    "A — xem kế hoạch xoá cả lô (chưa xoá gì).", "X — bỏ chọn tất cả."],
                    a=(f"Xoá {len(marks)} game đã chọn", lambda: self.plan_rom_batch(system)),
                    x=("Bỏ chọn tất cả", lambda: self.rom_clear_marks(system)), y=y))
            nxt_save = ROM_SAVE_LABEL[ROM_SAVE_CYCLE[(ROM_SAVE_CYCLE.index(view["save"]) + 1) % len(ROM_SAVE_CYCLE)]]
            flt = Item(f"Lọc: {ROM_SAVE_LABEL[view['save']]}", f"{len(rs)} game", "unknown", [
                "Lọc theo save: tất cả / có save / không save.",
                "Có save = có file save cùng tên game (không tính state), ở thẻ nào cũng được.",
                "Dấu chọn (X) được giữ nguyên khi đổi lọc hay đổi sắp xếp.",
                f"A — đổi sang: {nxt_save}.", "Y — đổi cách sắp xếp (Tên / Dung lượng)."],
                a=(f"Lọc: {nxt_save}", lambda: self.rom_cycle_filter(holder[0], system)), y=y)
            flt.rom_ctl = "filter"
            items.append(flt)
            for r in rs:
                card = "SD1" if r["card_id"] == "primary" else "SD2"
                on = r["absolute_path"] in marks
                it = Item(("[x] " if on else "[ ] ") + r["name"], (f"{r['system']} " if top else "") + f"{card} {mb(r['size'])}",
                          "green" if on else "unknown", [
                    f"Thẻ: {card}", f"Hệ: {r['system']}", f"File: {r['absolute_path']}", f"Dung lượng: {mb(r['size'])}",
                    "Save cùng tên: " + ("có" if r["stem"].lower() in stems else "không"), "",
                    "Game nhiều file (.cue/.m3u): chọn file .cue/.m3u để xoá trọn bộ.",
                    "A — xem kế hoạch xoá game này (chưa xoá gì).", "X — chọn / bỏ chọn để xoá nhiều game cùng lúc.",
                    "Y — đổi cách sắp xếp."],
                    a=("Xoá game này", lambda p=r["absolute_path"]: self.plan_rom(p)),
                    x=("Bỏ chọn" if on else "Chọn", lambda p=r["absolute_path"]: self.rom_toggle(holder[0], system, p)), y=y)
                it.rom_path = r["absolute_path"]
                items.append(it)
            if not rs:
                items.append(Item("Không có game nào khớp" if every else "Không còn game", "", "unknown",
                                  ["Không có file ROM nào khớp bộ lọc hiện tại." if every else "Hệ này không còn file ROM nào.",
                                   "B — quay lại danh sách hệ máy."], y=y))
            return header, items
        scr = ListScreen(self, "XOÁ ROM — Game lớn nhất (mọi hệ)" if top else f"XOÁ ROM — {system}", build)
        scr.rom_system = system
        # Mở màn: con trỏ vào game đầu tiên, không phải mục Lọc (bấm A ngay thì mở game chứ không đổi lọc).
        scr.sel = next((i for i, it in enumerate(scr.items) if getattr(it, "rom_path", None)), scr.sel)
        holder.append(scr)
        self.push(scr)

    @staticmethod
    def _rom_refresh_keep(scr, keep_path=None, keep_ctl=None):
        """Dựng lại màn rồi đặt con trỏ về đúng game (hoặc mục Lọc) đang chọn trước đó."""
        scr.refresh()
        for i, it in enumerate(scr.items):
            if (keep_path is not None and getattr(it, "rom_path", None) == keep_path) or \
                    (keep_ctl is not None and getattr(it, "rom_ctl", None) == keep_ctl):
                scr.sel = i
                return

    def rom_toggle_sort(self, scr, system):
        view, cur = self._rom_view(system), scr.cur_item()
        view["sort"] = "name" if view["sort"] == "size" else "size"
        self._rom_refresh_keep(scr, getattr(cur, "rom_path", None), getattr(cur, "rom_ctl", None))

    def rom_cycle_filter(self, scr, system):
        view = self._rom_view(system)
        view["save"] = ROM_SAVE_CYCLE[(ROM_SAVE_CYCLE.index(view["save"]) + 1) % len(ROM_SAVE_CYCLE)]
        self._rom_refresh_keep(scr, keep_ctl="filter")

    def rom_toggle(self, scr, system, path):
        marks = self.rom_marks.setdefault(system, set())
        marks.symmetric_difference_update({path})
        scr.refresh()
        for i, it in enumerate(scr.items):   # mục "Xoá N game" hiện/ẩn làm danh sách lệch dòng: giữ con trỏ trên đúng game
            if getattr(it, "rom_path", None) == path:
                scr.sel = i

    def rom_clear_marks(self, system):
        self.rom_marks.get(system, set()).clear()
        scr = self.cur()
        if isinstance(scr, ListScreen):
            scr.sel = 0
            scr.refresh()

    def plan_rom(self, path):
        def done(entries):
            e = entries[0]
            plan, lines = e["plan"], [f"! {w}" for w in e["plan"]["warnings"]]
            if not plan["ok"]:
                self.info("Không thể xoá game này", [f"!! {plan['message']}"] + lines)
                return
            body = ["!! XOÁ VĨNH VIỄN các file sau:"] + [f"• {f}" for f in plan["files"] + plan["artwork"]]
            body += ["", f"Tổng: {len(plan['files'])} file ROM + {len(plan['artwork'])} ảnh bìa = {mb(plan['bytes'])}", ""]
            body += (["+ Save/state được GIỮ LẠI (không xoá):"] + [f"+ {f}" for f in plan["saves_kept"]]) if plan["saves_kept"] else ["Không có save/state đi kèm."]
            self.confirm("Kế hoạch xoá ROM", lines + body, lambda: self.do_rom_delete(entries), "XOÁ")
        self.run("Đang lập kế hoạch", lambda: plan_rom_batch([path], self.probe.primary_path, self.probe.secondary_path), done)

    def plan_rom_batch(self, system):
        paths = [r["absolute_path"] for r in self._rom_scope(system) if r["absolute_path"] in self.rom_marks.get(system, ())]
        paths.sort(key=lambda p: os.path.basename(p).lower())

        def done(entries):
            ok = [e for e in entries if e["plan"]["ok"] and not e["merged_into"]]
            bad = [f"!! Bỏ qua '{os.path.basename(e['path'])}': {e['plan']['message']}"
                   for e in entries if not e["plan"]["ok"] and not e["merged_into"]]
            if not ok:
                self.info("Không thể xoá các game đã chọn", bad or ["Không có game nào lập được kế hoạch."])
                return
            nf = na = nbytes = 0
            kept: List[str] = []
            body = [f"!! XOÁ VĨNH VIỄN {len(ok)} game:"]
            for e in ok:
                pl = e["plan"]
                nf, na, nbytes = nf + len(pl["files"]), na + len(pl["artwork"]), nbytes + pl["bytes"]
                kept += [k for k in pl["saves_kept"] if k not in kept]
                body.append(f"{os.path.basename(e['path'])}  ({len(pl['files'])} file, {mb(pl['bytes'])})")
                body += [f"• {f}" for f in pl["files"] + pl["artwork"]]
                body += [f"! {w}" for w in pl["warnings"]]
            body += ["", f"Tổng: {nf} file ROM + {na} ảnh bìa = {mb(nbytes)}", ""]
            body += (["+ Save/state được GIỮ LẠI (không xoá):"] + [f"+ {f}" for f in kept]) if kept else ["Không có save/state đi kèm."]
            self.confirm(f"Kế hoạch xoá {len(ok)} game", bad + body, lambda: self.do_rom_delete(entries), "XOÁ")
        self.run("Đang lập kế hoạch", lambda: plan_rom_batch(paths, self.probe.primary_path, self.probe.secondary_path), done)

    def do_rom_delete(self, entries):
        def work():   # y hệt cli._cmd_rom
            return execute_rom_batch(entries, self.probe.userdata_path, self.probe.primary_path, self.probe.secondary_path)

        def done(res):
            n_ok = sum(1 for g in res["games"] if g["deleted"])
            lines = [("+ " if res["deleted"] else "") + f"Đã xoá {n_ok} game ({len(res['deleted'])} file), giải phóng {mb(res['bytes_freed'])}."]
            lines += [f"!! TỪ CHỐI {x['path']}: {x['reason']}" for x in res["refused"]]
            lines.append(f"Sổ xoá: {res.get('ledger_path')}")
            self.after_rom_delete(res["deleted"])
            self.finish_change("Kết quả xoá ROM", lines, bool(res["deleted"]))
            self.refresh_cur()
        self.run("Đang xoá ROM", work, done)

    def after_rom_delete(self, deleted):
        """Ở yên đúng màn game của hệ đó: cập nhật self.inv tại chỗ (không quét lại), chỉ xoá kết quả dò trùng
        cũ (không còn đúng), đặt con trỏ vào game kế tiếp. Đánh dấu của game đã xoá tự rụng khi màn dựng lại."""
        scr = self.cur()
        gone = {_rk(p) for p in deleted}
        nxt, hit = None, False
        if getattr(scr, "rom_system", None) is not None and gone:
            for it in scr.items:
                rp = getattr(it, "rom_path", None)
                if rp is None:
                    continue
                if _rk(rp) in gone:
                    hit = True
                elif hit:
                    nxt = rp
                    break
        prune_inventory(self.inv, deleted)
        self.dup_cache.clear()
        self.dup_compare = None
        if getattr(scr, "rom_system", None) is not None:
            scr.refresh()
            if hit:
                scr.sel = len(scr.items) - 1   # không còn game nào phía sau: về mục cuối
                for i, it in enumerate(scr.items):
                    if nxt is not None and getattr(it, "rom_path", None) == nxt:
                        scr.sel = i
                scr.sel = max(0, scr.sel)

    # ---- NHẬP GAME (Local Importer, engine/importer.py)
    IMPORT_NEXT = {"keep": "replace", "replace": "copy", "copy": "cancel", "cancel": "keep"}
    IMPORT_ACT_VI = {"keep": "giữ bản cũ, không cài", "replace": "thay (bản cũ vào .loongmate-import/trash/)",
                     "copy": "cài thêm bản sao", "cancel": "huỷ mục này"}
    IMPORT_STATUS_TAG = {"blocked": ("bị chặn", "red"), "unsupported": ("chưa hỗ trợ", "yellow"), "empty": ("trống", "unknown")}

    def _import_cards(self):
        return {"primary": self.probe.primary_path, "secondary": self.probe.secondary_path}

    def open_import(self):
        self.import_marks, self.import_choices = set(), {}
        self.run("Đang quét thư mục Import/", lambda: importer_mod.scan(None, self._import_cards()), self._import_scanned)

    def _import_scanned(self, res, push=True):
        self.import_data = res
        ids = {u["id"] for u in res["units"]}
        self.import_marks &= ids
        self.import_choices = {k: v for k, v in self.import_choices.items() if k in ids}
        if push:
            self.push(ListScreen(self, "NHẬP GAME", self._build_import))
        else:
            self.refresh_cur()

    def import_rescan(self):
        self.run("Đang quét lại Import/", lambda: importer_mod.scan(None, self._import_cards()),
                 lambda r: self._import_scanned(r, push=False))

    def import_mkdir(self):
        def work():
            return [(cid, importer_mod.ensure_import_dir(c)) for cid, c in self._import_cards().items() if c]

        def done(rows):
            lines = [("+ " if r["ok"] else "!! ") + r["message"] for _, r in rows]
            lines += ["", "Chép file ROM/.zip vào thư mục trên (qua máy tính, USB hoặc pc/import_push.py), rồi chọn Quét lại."]
            self.info("Thư mục nhập", lines)
            self.import_rescan()
        self.run("Đang tạo thư mục Import/", work, done)

    def import_toggle_delete(self):
        self.import_delete = not self.import_delete
        self.refresh_cur()

    def _import_unit_tag(self, u):
        if u["id"] in self.import_choices:
            return self.import_choices[u["id"]], "green"
        if u["status"] in self.IMPORT_STATUS_TAG:
            return self.IMPORT_STATUS_TAG[u["status"]]
        if u["kind"] == "bios":
            return "BIOS", "yellow"
        if u["status"] == "needs_choice":
            return "chọn hệ", "yellow"
        return (u["system"] or "?"), "green"

    def _import_unit_lines(self, u):
        out = [f"Nguồn: {u['source']}", f"Dung lượng: {mb(u['bytes'])}"]
        if u["status"] == "ready" and u["system"]:
            out.append(f"Hệ: {u['system']} (thư mục Roms/{u['folder']}/)")
        if u["status"] == "needs_choice":
            out.append("Hệ ứng viên: " + ", ".join(u["candidates"]) + " — A để chọn (không tự đoán).")
        if u["kind"] == "bios":
            out.append(f"BIOS khớp md5: sẽ vào {u['bios']['dest']} (xác nhận riêng ở bước kế hoạch).")
        if u["evidence"]:
            out.append(f"Nhận diện: {u['evidence']}")
        if u["kind"] == "multi":
            out.append("Game nhiều file: cài cả bộ, giữ layout tương đối: " + ", ".join(f["dest_rel"] for f in u["files"][:6]))
        out += [f"!! {r}" for r in u["reasons"]]
        out += [f"! {w}" for w in u["warnings"]]
        return out

    def _import_markable(self, u):
        return u["status"] in ("ready", "needs_choice")

    def _build_import(self):
        d = self.import_data or {"units": [], "import_dirs": [], "errors": [], "skipped": []}
        units = d["units"]
        header = [f"{len(units)} mục trong Import/ (SD1 + SD2). Chỉ nhập nội dung bạn có quyền sử dụng." if units else "Không có mục nào."]
        for x in d["import_dirs"]:
            header.append(f"{'SD1' if x['card'] == 'primary' else 'SD2'}: {x['path']} " + ("" if x["exists"] else "(chưa có — chọn Mở thư mục nhập)"))
        header += [f"! {e['path']}: {e['message']}" for e in d["errors"]]
        marks = self.import_marks
        items = []
        if marks:
            nbytes = sum(u["bytes"] for u in units if u["id"] in marks)
            items.append(Item(f"Nhập {len(marks)} mục đã chọn", mb(nbytes), "green", [
                "A — chọn thẻ đích, kiểm trùng và xem kế hoạch (chưa ghi gì).", "X — bỏ chọn tất cả."],
                a=(f"Xem kế hoạch {len(marks)} mục", lambda: self.import_make_plan(sorted(marks))),
                x=("Bỏ chọn tất cả", self.import_clear_marks)))
        for u in units:
            tag, color = self._import_unit_tag(u)
            on = u["id"] in marks
            fn_a = (("Chọn hệ máy", lambda u=u: self.import_choose(u)) if u["status"] == "needs_choice"
                    else (("Nhập mục này", lambda u=u: self.import_make_plan([u["id"]])) if self._import_markable(u) or u["kind"] == "bios" else None))
            it = Item(("[x] " if on else "[ ] ") + u["name"], tag, color, self._import_unit_lines(u), a=fn_a,
                      x=(("Bỏ chọn" if on else "Chọn"), lambda u=u: self.import_toggle(u)) if self._import_markable(u) or u["kind"] == "bios" else None)
            it.import_id = u["id"]
            items.append(it)
        if not units:
            items.append(Item("Chưa có gì trong Import/", "", "unknown", [
                "Thư mục Import/ trống (hoặc chưa có). Chọn Mở thư mục nhập để tạo, rồi chép file vào."]))
        items.append(Item("Quét lại Import/", f"{len(units)} mục", "unknown", ["Đọc lại Import/ của cả hai thẻ (chỉ đọc)."], a=("Quét lại", self.import_rescan)))
        items.append(Item("Mở thư mục nhập", "tạo", "unknown", [
            "Tạo <thẻ>/Import/ ở gốc mỗi thẻ nếu chưa có và cho biết đường dẫn.",
            "Chép file ROM, .zip hoặc thư mục game vào đó (qua máy tính, USB hoặc pc/import_push.py).",
            "LoongMate không tải ROM từ internet: chỉ nhập file bạn tự đưa vào."], a=("Tạo & hiện đường dẫn", self.import_mkdir)))
        items.append(Item("Dọn file đã nhập", "XOÁ" if self.import_delete else ".done/", "red" if self.import_delete else "unknown", [
            "Sau khi cài thành công, file gốc trong Import/ được " + ("XOÁ (đang BẬT)." if self.import_delete else "chuyển vào Import/.done/ (mặc định)."),
            "File chưa cài được (bị chặn, huỷ, lỗi) luôn ở nguyên chỗ.", "A — bật/tắt xoá thay vì chuyển."], a=("Bật/tắt xoá", self.import_toggle_delete)))
        return header, items

    def import_toggle(self, u):
        self.import_marks.symmetric_difference_update({u["id"]})
        scr = self.cur()
        scr.refresh()
        for i, it in enumerate(scr.items):
            if getattr(it, "import_id", None) == u["id"]:
                scr.sel = i

    def import_clear_marks(self):
        self.import_marks.clear()
        scr = self.cur()
        scr.sel = 0
        scr.refresh()

    def import_choose(self, u):
        def build():
            items = []
            for c in u["candidates"]:
                items.append(Item(c, "chọn", "green", [f"Cài vào Roms/{c}/.", u["evidence"]], a=(f"Chọn {c}", lambda c=c: self.import_set_choice(u, c))))
            return [f"'{u['name']}': {u['evidence']}", "Chọn hệ máy đúng. Chọn sai thì game nằm nhầm thư mục."], items
        self.push(ListScreen(self, "CHỌN HỆ MÁY", build))

    def import_set_choice(self, u, system):
        self.import_choices[u["id"]] = system
        self.import_marks.add(u["id"])
        self.back()

    def import_make_plan(self, ids, pref="auto"):
        todo = [u for u in (self.import_data or {}).get("units", []) if u["id"] in ids]
        unresolved = [u["name"] for u in todo if u["status"] == "needs_choice" and u["id"] not in self.import_choices]
        if unresolved:
            self.info("Chưa chọn hệ máy", ["!! Chọn hệ máy (A trên mục đó) trước khi lập kế hoạch:"] + [f"• {n}" for n in unresolved[:12]])
            return
        choices = {k: v for k, v in self.import_choices.items() if k in ids}

        def work():
            hc = hashcache_mod.HashCache(self.app_data) if self.app_data else None
            try:
                return importer_mod.build_plan(None, self._import_cards(), self.app_data, card_pref=pref, select=list(ids),
                                               hashcache=hc, choices=choices)
            finally:
                if hc:
                    hc.close()

        def done(plan):
            if plan["needs_card_choice"]:
                lines = ["!! " + plan["card"]["reason"], "", "Dùng thẻ Leaf (thẻ chính) thì game mới nằm cùng thẻ với hệ thống; thẻ này thường gần đầy.",
                         "LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."]
                prim = [o for o in plan["card"]["options"] if o["card"] == "primary"][0]
                if prim["usable"]:
                    self.confirm("Thẻ phụ không dùng được", lines, lambda: self.import_make_plan(ids, "primary"), "Dùng thẻ Leaf")
                else:
                    self.info("Không có thẻ nào dùng được", lines)
                return
            if not plan["ok"]:
                self.info("Không lập được kế hoạch", ["!! " + plan["card"]["reason"]])
                return
            self.import_plan, self.import_acts, self.import_bios = plan, {}, set()
            self.push(ListScreen(self, "KẾ HOẠCH NHẬP", self._build_import_plan))
        self.run("Đang kiểm tra và lập kế hoạch", work, done)

    def _import_action_row(self, u):
        a = u.get("action")
        if a == "install":
            return "cài", "green", ["Sẽ cài: " + ", ".join(u.get("dest_paths", [])[:4])]
        if a == "present":
            return "đã có", "unknown", ["Cùng SHA-256 đã có trong thư viện — không cài lại."]
        if a == "conflict":
            act = self.import_acts.get(u["id"], "keep")
            return f"trùng tên: {act}", "yellow", ["!! Đã có file cùng tên nhưng KHÁC nội dung:"] + ["• " + x for x in u["conflict"]["paths"][:4]] + [
                f"Hành động: {self.IMPORT_ACT_VI[act]}.", "A — đổi hành động (giữ / thay / bản sao / huỷ)."]
        if a in ("bios_confirm", "bios_present", "bios_conflict"):
            if a == "bios_present":
                return "BIOS đã có", "unknown", ["BIOS này đã có đúng nội dung."]
            if a == "bios_conflict":
                return "BIOS khác", "red", ["!! " + x for x in u["reasons"]]
            on = u["id"] in self.import_bios
            return "BIOS: " + ("nhập" if on else "bỏ qua"), "green" if on else "yellow", [
                f"BIOS khớp md5 -> {u['bios']['dest']}.", "A — xác nhận / bỏ qua BIOS này (xác nhận riêng, không bao giờ ghi đè BIOS có sẵn)."]
        if a == "choose_system":
            return "chưa chọn hệ", "yellow", ["Quay lại và chọn hệ máy."]
        return (u.get("status") or "?"), "red", ["!! " + x for x in u.get("reasons", [])]

    def _import_will_do(self):
        n = 0
        for u in (self.import_plan or {}).get("units", []):
            a = u.get("action")
            if a == "install" or (a == "conflict" and self.import_acts.get(u["id"], "keep") in ("replace", "copy")) or \
                    (a == "bios_confirm" and u["id"] in self.import_bios):
                n += 1
        return n

    def _build_import_plan(self):
        plan = self.import_plan
        card = plan["card"]
        header = ["Thẻ đích: " + card["reason"], f"Cần ~{mb(plan['needed_bytes'])} (gồm giải nén, staging, dự phòng)."]
        n = self._import_will_do()
        items = [Item(f"THỰC HIỆN ({n} việc)", "", "green" if n else "unknown", [
            "A — xác nhận cuối (mặc định Huỷ) rồi cài: giải nén vào thư mục tạm trên thẻ đích, kiểm SHA-256, rồi chuyển vào Roms/.",
            "Nguồn trong Import/ chỉ được chuyển/xoá sau khi cài thành công."], a=("Thực hiện", self.import_confirm) if n else None)]
        for u in plan["units"]:
            tag, color, lines = self._import_action_row(u)
            lines = [f"Hệ: {u['system'] or '?'}  ·  {mb(u['bytes'])}"] + lines + [f"! {w}" for w in u["warnings"]]
            fn = None
            if u.get("action") == "conflict":
                fn = ("Đổi hành động", lambda u=u: self.import_cycle(u))
            elif u.get("action") == "bios_confirm":
                fn = ("Xác nhận/bỏ BIOS", lambda u=u: self.import_toggle_bios(u))
            it = Item(u["name"], tag, color, lines, a=fn)
            it.import_id = u["id"]
            items.append(it)
        return header, items

    def import_cycle(self, u):
        self.import_acts[u["id"]] = self.IMPORT_NEXT[self.import_acts.get(u["id"], "keep")]
        self.cur().refresh()

    def import_toggle_bios(self, u):
        self.import_bios.symmetric_difference_update({u["id"]})
        self.cur().refresh()

    def import_confirm(self):
        plan = self.import_plan
        lines = [f"Thẻ đích: {plan['card']['root']}", ""]
        for u in plan["units"]:
            a = u.get("action")
            if a == "install":
                lines.append(f"+ Cài {u['name']} -> {u['system']} ({mb(u['bytes'])})")
            elif a == "conflict" and self.import_acts.get(u["id"], "keep") in ("replace", "copy"):
                lines.append(f"! {u['name']}: {self.IMPORT_ACT_VI[self.import_acts[u['id']]]}")
            elif a == "bios_confirm" and u["id"] in self.import_bios:
                lines.append(f"+ BIOS {u['bios']['name']} -> BIOS/")
        lines += ["", "File gốc: " + ("sẽ bị XOÁ sau khi cài thành công." if self.import_delete else "chuyển vào Import/.done/ sau khi cài thành công.")]
        self.confirm("Xác nhận nhập game", lines, self.do_import_apply, "Nhập")

    def do_import_apply(self):
        plan = self.import_plan

        def work():
            hc = hashcache_mod.HashCache(self.app_data) if self.app_data else None
            try:
                res = importer_mod.apply_plan(plan, self._import_cards(), self.app_data, choices=dict(self.import_choices),
                                              conflicts=dict(self.import_acts), bios_ok=set(self.import_bios),
                                              delete_source=self.import_delete, hashcache=hc)
            finally:
                if hc:
                    hc.close()
            return res, importer_mod.scan(None, self._import_cards())

        def done(out):
            res, fresh = out
            lines = [("+ " if res["installed"] else "") + res["message"]]
            for r in res["results"]:
                mark = "+" if r["status"] in ("installed", "already_present") else "!!" if r["status"] in ("blocked", "failed") else "!"
                lines.append(f"{mark} {r['name']}: {r['status']} — {r['message']}")
            lines += [f"Nguồn {c['action']}: {c['path']}" for c in res.get("source_cleanup", [])]
            self.import_marks -= {r["id"] for r in res["results"] if r["status"] in ("installed", "already_present")}
            self.back()                                     # đóng màn kế hoạch, về danh sách (đứng yên tại chỗ)
            self._import_scanned(fresh, push=False)
            self.finish_change("Kết quả nhập game", lines, bool(res["changed"]))
        self.run("Đang nhập game", work, done)

    # ---- THƯ VIỆN RIÊNG (engine/libsource.py): agent PocketBridge của BẠN; tải + kiểm SHA-256 + cài bằng importer
    LIB_FILTER_CYCLE = ("all", "new", "have")
    LIB_FILTER_LABEL = {"all": "tất cả", "new": "chưa có trên máy", "have": "đã có trên máy"}
    LIB_STATE_VI = {"downloading": "đang tải dở", "interrupted": "tải dở", "installing": "chờ cài", "unreadable": "hỏng"}
    LIB_HINT = {"pin": "Kiểm tra lại máy đích, hoặc ghép đôi lại bằng bundle mới.",
                "unreachable": "Kiểm tra Tailscale đang chạy trên máy này và máy đích đang bật.",
                "timeout": "Kiểm tra mạng (Wi-Fi/Tailscale) rồi thử lại.",
                "auth": "Ghép đôi lại bằng bundle mới (pocketbridge-agent pair --scopes library:read)."}

    def _lib_env(self):
        return libsource_mod.Env(self.app_data, self._import_cards())

    def _lib_task(self, label, fn, done, progress=None):
        """Chạy fn nền; LibError -> hộp lỗi tiếng Việt kèm gợi ý (không nuốt lý do)."""
        def work():
            try:
                return {"ok": True, "v": fn()}
            except libsource_mod.LibError as e:
                return {"ok": False, "e": e}

        def after(r):
            if r["ok"]:
                done(r["v"])
            else:
                e = r["e"]
                self.info("Thư viện riêng", ["!! " + e.message] + ([self.LIB_HINT[e.kind]] if e.kind in self.LIB_HINT else []))
        self.run(label, work, after)
        self.task.progress = progress

    def open_lib_nodes(self):
        if self.need(self.app_data):
            self.push(ListScreen(self, "THƯ VIỆN RIÊNG", self._build_lib_nodes))

    def _build_lib_nodes(self):
        env = self._lib_env()
        nodes, jobs = libsource_mod.list_nodes(env)["nodes"], libsource_mod.list_jobs(env)["jobs"]
        header = ["Chỉ nói chuyện với máy BẠN đã ghép đôi (agent PocketBridge của bạn). LoongMate không có danh mục ROM nào.",
                  f"{len(nodes)} thư viện đã ghép đôi."]
        items = []
        for n in nodes:
            items.append(Item(n["name"], f"{n['host']}:{n['port']}", "green" if n["has_token"] else "red", [
                f"Mã: {n['id']}", f"Máy đích: {n['host']}:{n['port']}", f"Fingerprint ghim: {n['fingerprint'][:23]}…",
                "Token: " + ("đã lưu (/userdata, quyền 0600)" if n["has_token"] else "!! THIẾU — ghép đôi lại"),
                "A — mở thư viện.   X — gỡ ghép đôi (xoá token + cache trên máy này)."],
                a=("Mở", lambda i=n["id"]: self.open_lib_roots(i)) if n["has_token"] else None,
                x=("Gỡ ghép đôi", lambda i=n["id"], nm=n["name"]: self.confirm(
                    "Gỡ ghép đôi", [f"Gỡ '{nm}' khỏi máy này? Token và cache danh sách bị xoá.",
                                    "Token trên agent vẫn còn: thu hồi bằng `token revoke` trên máy agent."], lambda: self.lib_unpair(i), "Gỡ"))))
        for j in jobs:
            nm = ", ".join(j["names"][:2]) + ("…" if len(j["names"]) > 2 else "")
            items.append(Item(f"Tải dở: {nm}", f"{j['percent']}%", "yellow", [
                f"Job {j['job_id']} ({self.LIB_STATE_VI.get(j['state'], j['state'])}) trên thẻ {'SD1' if j['card'] == 'primary' else 'SD2'}",
                f"{j['files_done']}/{j['files']} file xong, {mb(j['bytes_done'])}/{mb(j['bytes_total'])}.",
                j["message"] or "Mất mạng/ngủ máy giữa chừng: tiếp tục được (Range + If-Range), file đã tải được giữ.",
                "A — tiếp tục tải rồi cài.   X — huỷ job và xoá file tải dở."],
                a=("Tiếp tục", lambda jj=j: self.lib_resume(jj)) if j["state"] != "unreadable" else None,
                x=("Huỷ job", lambda jj=j: self.confirm("Huỷ job tải", [f"Xoá job {jj['job_id']} và mọi file tải dở?"],
                                                         lambda: self.lib_cancel(jj["job_id"]), "Huỷ job"))))
        bundles = libsource_mod.find_bundles(env)
        items.append(Item("Ghép đôi thư viện", f"{len(bundles)} bundle", "unknown", [
            "Nhập bundle ghép đôi (.json) do `pocketbridge-agent pair --scopes library:read` tạo ra.",
            "Chép bundle vào <thẻ Leaf>/PocketBridge-import/ (hoặc Import/) trước.",
            "Ghim fingerprint của bundle, đổi mã một lần lấy token dài hạn, lưu token (0600) rồi XOÁ bundle."],
            a=("Mở", self.open_lib_pair)))
        return header, items

    def lib_unpair(self, node_id):
        try:
            r = libsource_mod.unpair(self._lib_env(), node_id)
            self.info("Gỡ ghép đôi", ["+ " + r["message"]])
        except libsource_mod.LibError as e:
            self.info("Gỡ ghép đôi", ["!! " + e.message])
        self.refresh_cur()

    def lib_cancel(self, job_id):
        try:
            self.info("Huỷ job", ["+ " + libsource_mod.cancel_job(self._lib_env(), job_id)["message"]])
        except libsource_mod.LibError as e:
            self.info("Huỷ job", ["!! " + e.message])
        self.refresh_cur()

    def open_lib_pair(self):
        self.push(ListScreen(self, "GHÉP ĐÔI THƯ VIỆN", self._build_lib_pair))

    def _build_lib_pair(self):
        bundles = libsource_mod.find_bundles(self._lib_env())
        items = []
        for b in bundles:
            card = "SD1" if b["card"] == "primary" else "SD2"
            if b["valid"]:
                items.append(Item(b["file"], card, "green", [
                    f"Thư viện: {b['node_name']} ({b['node_id']})", f"Máy đích: {b['host']}", f"Kiểu mã: {b['type']}", f"File: {b['path']}",
                    "A — ghép đôi (xác nhận trước). Bundle bị xoá sau khi ghép đôi thành công."],
                    a=("Ghép đôi", lambda b=b: self.lib_ask_pair(b))))
            else:
                items.append(Item(b["file"], "không hợp lệ", "red", [f"File: {b['path']}", "!! " + b["reason"], "File không bị đụng tới."]))
        if not items:
            items.append(Item("Không có bundle nào", "", "unknown", [
                "Chép file bundle (.json) vào <thẻ Leaf>/PocketBridge-import/ hoặc <thẻ>/Import/, rồi mở lại màn này.",
                "Tạo bundle trên máy agent: pocketbridge-agent pair --id pocket1 --scopes library:read"]))
        return [f"{len(bundles)} bundle trong PocketBridge-import/ và Import/ của cả hai thẻ."], items

    def lib_ask_pair(self, b):
        self.confirm("Ghép đôi thư viện", [
            f"Ghép đôi với '{b['node_name']}' ({b['host']})?", "",
            "Mã trong bundle chỉ dùng được MỘT lần: đổi lấy token dài hạn, lưu ở /userdata (quyền 0600).",
            "Fingerprint trong bundle được ghim: máy lạ không nhận được token.",
            "Bundle bị XOÁ sau khi ghép đôi thành công."], lambda: self.lib_do_pair(b), "Ghép đôi")

    def lib_do_pair(self, b):
        def done(r):
            self.info("Ghép đôi thư viện", [("+ " if r["success"] else "!! ") + r["message"]])
            self.refresh_cur()
        self._lib_task("Đang ghép đôi", lambda: libsource_mod.pair(self._lib_env(), b["path"]), done)

    def open_lib_roots(self, node_id):
        def done(r):
            self.push(ListScreen(self, "THƯ VIỆN — thư mục", lambda: self._build_lib_roots(node_id, r["roots"])))
        self._lib_task("Đang hỏi thư viện", lambda: libsource_mod.list_roots(self._lib_env(), node_id), done)

    def _build_lib_roots(self, node_id, roots):
        items = [Item(r["name"], f"{r['files']} file", "yellow" if r["indexing"] else "unknown", [
            f"Mã thư mục: {r['id']}", f"{r['files']} file, {mb(r['bytes'])}",
            "Server đang quét lại danh sách." if r["indexing"] else f"Quét lúc: {r['indexed_at']}",
            "A — duyệt theo hệ máy."], a=("Duyệt", lambda i=r["id"]: self.open_lib_systems(node_id, i))) for r in roots]
        if not items:
            items.append(Item("Agent chưa khai báo thư viện", "", "unknown", ["Khai báo library.roots trong config của agent (xem docs/THU-VIEN-RIENG.md)."]))
        return [f"{len(roots)} thư mục thư viện."], items

    def open_lib_systems(self, node_id, root):
        def done(res):
            self.lib_data[(node_id, root)] = res
            self.push(ListScreen(self, "THƯ VIỆN — hệ máy", lambda: self._build_lib_systems(node_id, root)))
        self._lib_task("Đang đọc danh sách thư viện", lambda: libsource_mod.browse(self._lib_env(), node_id, root), done)

    def _build_lib_systems(self, node_id, root):
        res = self.lib_data[(node_id, root)]
        header = [f"{res['total']} game trong {len(res['systems'])} hệ máy (chỉ hiện file có đuôi hệ máy trong systems.json hoặc có hint)."]
        if res["stale"]:
            header.append(f"! DANH SÁCH CŨ (lấy lúc {res['fetched_at']}): không nối được server — {res['error'] or 'chỉ đọc cache'}")
        items = [Item(s["system"] if s["system"] != "?" else "Chưa rõ hệ", f"{s['count']} file", "unknown", [
            f"Hệ: {s['system']}", f"{s['count']} file, {mb(s['bytes'])}",
            ("Chưa rõ hệ: đuôi mơ hồ (.bin/.zip...) và thư mục cấp 1 không phải tên hệ — importer nhận diện bằng header, không được thì báo, không đoán."
             if s["system"] == "?" else "A — duyệt danh sách game.")],
            a=("Duyệt", lambda sy=s["system"]: self.open_lib_games(node_id, root, sy))) for s in res["systems"]]
        if not items:
            items.append(Item("Không có game nào", "", "unknown", ["Thư viện không có file nào có đuôi thuộc hệ máy nào."]))
        return header, items

    def _lib_view(self, key):
        return self.lib_view.setdefault(key, {"sort": "name", "flt": "all"})

    def open_lib_games(self, node_id, root, system):
        key = (node_id, root, system)

        def done(res):
            self.lib_games[key] = res
            self._push_lib_games(key)
        self._lib_task("Đang đọc danh sách game",
                       lambda: libsource_mod.browse(self._lib_env(), node_id, root, system=system, refresh=False), done)

    def _push_lib_games(self, key):
        node_id, root, system = key
        marks = self.lib_marks.setdefault(key, set())
        view = self._lib_view(key)
        holder: List[Any] = []

        def build():
            res = self.lib_games[key]
            every = res["entries"]
            marks.intersection_update(e["path"] for e in every)
            rows = [e for e in every if view["flt"] == "all" or (view["flt"] == "have") == e["present"]]
            rows.sort(key=(lambda e: -e["size"]) if view["sort"] == "size" else (lambda e: e["path"].lower()))
            msize = sum(e["size"] for e in every if e["path"] in marks)
            shown = {e["path"] for e in rows}
            header = [f"Hệ {system}: {len(every)} file." + (f" Lọc: {len(rows)} hiện." if view["flt"] != "all" else "")]
            src = self.lib_data.get((node_id, root), {})       # "cũ" = lần đọc server ở màn hệ máy đã thất bại
            if src.get("stale"):
                header.append(f"! DANH SÁCH CŨ ({src.get('fetched_at')}): {src.get('error') or 'chỉ đọc cache'}")
            header.append(f"Sắp xếp: {ROM_SORT_LABEL[view['sort']]} (Y đổi).   Lọc: {self.LIB_FILTER_LABEL[view['flt']]} (A trên mục Lọc).")
            if marks:
                hid = len(marks - shown)
                header.append(f"Đã chọn {len(marks)} file ({mb(msize)})" + (f", {hid} đang bị bộ lọc ẩn" if hid else "") + ". X chọn/bỏ chọn.")
            nxt = ROM_SORT_LABEL["name" if view["sort"] == "size" else "size"]
            y = (f"Sắp xếp theo {nxt}", lambda: self.lib_toggle_sort(holder[0], key))
            items = []
            if marks:
                items.append(Item(f"Tải & cài {len(marks)} file đã chọn", mb(msize), "green", [
                    f"Đã chọn {len(marks)} file, {mb(msize)}.",
                    "A — kiểm với server (kích thước + SHA-256), chọn thẻ đích và xem kế hoạch (chưa tải gì).", "X — bỏ chọn tất cả."],
                    a=(f"Tải & cài {len(marks)} file", lambda: self.lib_plan(key, sorted(marks))),
                    x=("Bỏ chọn tất cả", lambda: self.lib_clear_marks(holder[0], key)), y=y))
            nflt = self.LIB_FILTER_LABEL[self.LIB_FILTER_CYCLE[(self.LIB_FILTER_CYCLE.index(view["flt"]) + 1) % 3]]
            flt = Item(f"Lọc: {self.LIB_FILTER_LABEL[view['flt']]}", f"{len(rows)} file", "unknown", [
                "Lọc theo việc game đã có trong Roms/ của máy (so tên file) hay chưa.", "Dấu chọn (X) được giữ khi đổi lọc hay sắp xếp.",
                f"A — đổi sang: {nflt}.", "Y — đổi cách sắp xếp."], a=(f"Lọc: {nflt}", lambda: self.lib_cycle_filter(holder[0], key)), y=y)
            flt.lib_ctl = "filter"
            items.append(flt)
            for e in rows:
                on = e["path"] in marks
                tag = ("đã có " if e["present"] else "") + mb(e["size"])
                lines = [f"File: {e['path']}", f"Dung lượng: {mb(e['size'])}", "SHA-256 server: " + (e["sha256"] or "chưa băm xong — chưa tải được"),
                         "Trên máy: " + ("ĐÃ CÓ file cùng tên trong Roms/" if e["present"] else "chưa có")]
                if e["problem"]:
                    lines.append("!! " + e["problem"])
                lines += ["A — kiểm với server và xem kế hoạch tải & cài game này.", "X — chọn / bỏ chọn để tải nhiều file.", "Y — đổi cách sắp xếp."]
                ok = e["installable"]
                it = Item(("[x] " if on else "[ ] ") + e["name"], tag, "green" if on else ("yellow" if e["problem"] else "unknown"), lines,
                          a=("Tải & cài", lambda p=e["path"]: self.lib_plan(key, [p])) if ok else None,
                          x=("Bỏ chọn" if on else "Chọn", lambda p=e["path"]: self.lib_toggle(holder[0], key, p)) if ok else None, y=y)
                it.lib_path = e["path"]
                items.append(it)
            if not rows:
                items.append(Item("Không có game nào khớp", "", "unknown", ["Không có file nào khớp bộ lọc."], y=y))
            return header, items
        scr = ListScreen(self, f"THƯ VIỆN — {system if system != '?' else 'chưa rõ hệ'}", build)
        scr.sel = next((i for i, it in enumerate(scr.items) if getattr(it, "lib_path", None)), scr.sel)
        scr.lib_key = key
        holder.append(scr)
        self.push(scr)

    @staticmethod
    def _lib_keep(scr, path=None, ctl=None):
        scr.refresh()
        for i, it in enumerate(scr.items):
            if (path is not None and getattr(it, "lib_path", None) == path) or (ctl is not None and getattr(it, "lib_ctl", None) == ctl):
                scr.sel = i
                return

    def lib_toggle_sort(self, scr, key):
        view, cur = self._lib_view(key), scr.cur_item()
        view["sort"] = "name" if view["sort"] == "size" else "size"
        self._lib_keep(scr, getattr(cur, "lib_path", None), getattr(cur, "lib_ctl", None))

    def lib_cycle_filter(self, scr, key):
        view = self._lib_view(key)
        view["flt"] = self.LIB_FILTER_CYCLE[(self.LIB_FILTER_CYCLE.index(view["flt"]) + 1) % 3]
        self._lib_keep(scr, ctl="filter")

    def lib_toggle(self, scr, key, path):
        self.lib_marks.setdefault(key, set()).symmetric_difference_update({path})
        self._lib_keep(scr, path)

    def lib_clear_marks(self, scr, key):
        self.lib_marks.get(key, set()).clear()
        scr.sel = 0
        scr.refresh()

    def lib_plan(self, key, paths, pref="auto"):
        node_id, root, _system = key

        def done(pl):
            if pl["needs_card_choice"]:
                lines = ["!! " + pl["card"]["reason"], "",
                         "Dùng thẻ Leaf (thẻ chính) thì game nằm cùng thẻ với hệ thống; thẻ này thường gần đầy.",
                         "LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."]
                prim = [o for o in pl["card"]["options"] if o["card"] == "primary"][0]
                if prim["usable"] and not pl["problems"]:
                    self.confirm("Thẻ phụ không dùng được", lines, lambda: self.lib_plan(key, paths, "primary"), "Dùng thẻ Leaf")
                else:
                    self.info("Không có thẻ nào dùng được" if not prim["usable"] else "Không tải được",
                              lines + [f"!! {p['path']}: {p['reason']}" for p in pl["problems"]])
                return
            if not pl["ok"]:
                self.info("Không tải được", [f"!! {p['path']}: {p['reason']}" for p in pl["problems"]] or ["!! " + pl["message"]])
                return
            lines = [f"Thư viện: {node_id}/{root}", f"Thẻ đích: {pl['card']['root']}", ""]
            lines += [f"+ {f['path']} -> {f['system']} ({mb(f['size'])})" + ("  [đã có file cùng tên trong Roms/: importer bỏ qua nếu giống hệt]" if f.get("present") else "")
                      for f in pl["files"]]
            lines += ["", f"Tổng {mb(pl['total_bytes'])}; cần ~{mb(pl['needed_bytes'])} trống (file tải + staging + dự phòng).",
                      "SHA-256 file tải về phải khớp hash server công bố, sai thì KHÔNG cài.",
                      "Cài bằng đúng trình Nhập game (kiểm an toàn, nhận hệ bằng header, giao dịch chống mất điện).",
                      "Mất mạng giữa chừng: tải tiếp được ở Thư viện riêng."]
            use = "primary" if pl["card"]["selected"] == "primary" else pref
            self.confirm(f"Tải & cài {len(pl['files'])} file", lines, lambda: self.lib_do_fetch(key, paths, use), "Tải & cài")
        self._lib_task("Đang kiểm với server", lambda: libsource_mod.prepare(self._lib_env(), node_id, root, paths, pref), done)

    @staticmethod
    def _lib_progress():
        prog = {"ev": None}
        return prog, (lambda ev: prog.__setitem__("ev", ev))

    def lib_do_fetch(self, key, paths, pref):
        node_id, root, system = key
        prog, cb = self._lib_progress()

        def work():
            env = self._lib_env()
            res = libsource_mod.fetch(env, node_id, root, paths, pref, cb)
            fresh = None
            try:
                fresh = libsource_mod.browse(env, node_id, root, system=system, refresh=False)
            except libsource_mod.LibError:
                pass
            return res, fresh
        self._lib_task("Đang tải & cài", work, lambda out: self._lib_fetched(key, paths, out), progress=prog)

    def _lib_fetched(self, key, paths, out):
        res, fresh = out
        if fresh is not None:
            self.lib_games[key] = fresh
        if res.get("success"):
            self.lib_marks.get(key, set()).difference_update(paths)
        self.finish_change("Kết quả tải & cài", self._lib_result_lines(res), bool(res.get("changed")))
        self.refresh_cur()

    @staticmethod
    def _lib_result_lines(res) -> List[str]:
        lines = [("+ " if res.get("success") else "!! ") + (res.get("message") or "")]
        for r in res.get("results", []) or []:
            mark = "+" if r["status"] in ("installed", "already_present") else "!!" if r["status"] in ("blocked", "failed") else "!"
            lines.append(f"{mark} {r['name']}: {r['status']} — {r['message']}")
        if res.get("resumable"):
            lines.append(f"! Job {res.get('job_id')} còn nguyên: mở Thư viện riêng > Tải dở để tiếp tục (file đã tải được giữ, tiếp bằng Range + If-Range).")
        return lines

    def lib_resume(self, j):
        prog, cb = self._lib_progress()

        def done(res):
            self.finish_change("Kết quả tải & cài", self._lib_result_lines(res), bool(res.get("changed")))
            self.refresh_cur()
        self.confirm("Tiếp tục tải", [f"Tiếp tục job {j['job_id']}: {j['files_done']}/{j['files']} file xong ({j['percent']}%).",
                                      "Tải tiếp phần còn lại, kiểm SHA-256 rồi cài."],
                     lambda: self._lib_task("Đang tải & cài", lambda: libsource_mod.resume(self._lib_env(), j["job_id"], cb), done, progress=prog),
                     "Tiếp tục")

    # ---- HOMEBREW (tải trực tiếp, engine/homebrew.py): chỉ game có giấy phép rõ ràng; tải qua HTTPS kiểm CA, cài bằng importer
    HB_SORT_LABEL = {"name": "Tên", "new": "Mới thêm trước"}
    HB_HINT = {"tls": "Máy chủ không xác minh được (chứng thư/bộ CA) nên KHÔNG tải. Không tắt kiểm tra.",
               "unreachable": "Kiểm tra Wi-Fi/Internet của máy (Homebrew tải thẳng qua Wi-Fi, không qua Tailscale).",
               "timeout": "Kiểm tra Wi-Fi rồi thử lại.", "network": "Kiểm tra Wi-Fi rồi thử lại.",
               "not_found": "Mở Homebrew > Cập nhật danh mục (cần Internet)."}

    def _hb_task(self, label, fn, done, progress=None):
        def work():
            try:
                return {"ok": True, "v": fn()}
            except libsource_mod.LibError as e:
                return {"ok": False, "e": e}

        def after(r):
            if r["ok"]:
                done(r["v"])
            else:
                e = r["e"]
                self.info("Homebrew", ["!! " + e.message] + ([self.HB_HINT[e.kind]] if e.kind in self.HB_HINT else []))
        self.run(label, work, after)
        self.task.progress = progress

    def open_hb(self):
        if self.need(self.app_data):
            self.push(ListScreen(self, "HOMEBREW (tải trực tiếp)", self._build_hb))

    def _build_hb(self):
        env = self._lib_env()
        header = ["Chỉ hiện game có GIẤY PHÉP RÕ RÀNG (Homebrew Hub); mục không giấy phép bị ẩn.",
                  "Tải qua Internet (HTTPS kiểm chứng thư), cài bằng Nhập game."]
        items = []
        try:
            res = homebrew_mod.list_games(env)
        except libsource_mod.LibError:
            res = None
        if res is None:
            header.append("Chưa có danh mục: chọn Cập nhật danh mục (cần Internet, ~30 giây).")
        else:
            header.append(f"{res['total']} game · cập nhật {res['fetched_at'][:16].replace('T', ' ')} UTC · {res['rejected']} mục bị ẩn.")
        items.append(Item("Cập nhật danh mục", "", "yellow", [
            "Tải toàn bộ danh mục Homebrew Hub (≤ 1 yêu cầu/giây, vài chục trang), giữ lại mục có giấy phép rõ ràng.",
            "Chỉ ghi cache trong dữ liệu LoongMate; không đụng thẻ nhớ. Lỗi giữa chừng thì giữ nguyên danh mục cũ.",
            "A — cập nhật."], a=("Cập nhật", self.hb_sync)))
        if res:
            for s in res["systems"]:
                items.append(Item(s["folder"], f"{s['count']} game", "unknown", [
                    f"Hệ {s['folder']}: {s['count']} game có giấy phép rõ ràng.", "A — duyệt danh sách."],
                    a=("Duyệt", lambda sy=s["system"]: self.open_hb_games(sy))))
            for j in res["jobs"]:
                nm = ", ".join(j["names"][:2]) + ("…" if len(j["names"]) > 2 else "")
                items.append(Item(f"Tải dở: {nm}", f"{j['percent']}%", "yellow", [
                    f"Job {j['job_id']} ({self.LIB_STATE_VI.get(j['state'], j['state'])}) trên thẻ {'SD1' if j['card'] == 'primary' else 'SD2'}",
                    j["message"] or "Mất mạng/ngủ máy giữa chừng: tiếp tục được (Range + If-Range), file đã tải được giữ.",
                    "A — tiếp tục tải rồi cài.   X — huỷ job và xoá file tải dở."],
                    a=("Tiếp tục", lambda jj=j: self.lib_resume(jj)),
                    x=("Huỷ job", lambda jj=j: self.confirm("Huỷ job tải", [f"Xoá job {jj['job_id']} và mọi file tải dở?"],
                                                             lambda: self.lib_cancel(jj["job_id"]), "Huỷ job"))))
        return header, items

    def hb_sync(self):
        prog, cb = self._lib_progress()

        def done(r):
            self.info("Cập nhật danh mục", ["+ " + r["message"], f"{r['pages']} trang trong {r['elapsed_s']} giây."])
            self.refresh_cur()
        self._hb_task("Đang cập nhật danh mục", lambda: homebrew_mod.sync_catalog(self.app_data, homebrew_mod.Session(), progress=cb),
                      done, progress=prog)

    def open_hb_games(self, system):
        key = ("hb", system)

        def done(res):
            self.lib_games[key] = res
            self._push_hb_games(system)
        self._hb_task("Đang đọc danh sách", lambda: homebrew_mod.list_games(self._lib_env(), system=system), done)

    def _push_hb_games(self, system):
        key = ("hb", system)
        marks = self.lib_marks.setdefault(key, set())
        view = self.lib_view.setdefault(key, {"sort": "name", "flt": "all"})
        holder: List[Any] = []

        def build():
            every = self.lib_games[key]["entries"]
            marks.intersection_update(e["slug"] for e in every)
            rows = [e for e in every if view["flt"] == "all" or (view["flt"] == "have") == e["present"]]
            rows.sort(key=(lambda e: e["added"]) if view["sort"] == "new" else (lambda e: e["title"].lower()), reverse=view["sort"] == "new")
            header = [f"Hệ {system}: {len(every)} game có giấy phép rõ ràng." + (f" Lọc: {len(rows)} hiện." if view["flt"] != "all" else ""),
                      f"Sắp xếp: {self.HB_SORT_LABEL[view['sort']]} (Y đổi).   Lọc: {self.LIB_FILTER_LABEL[view['flt']]} (A trên mục Lọc)."]
            if marks:
                header.append(f"Đã chọn {len(marks)} game. X chọn/bỏ chọn.")
            y = (f"Sắp xếp theo {self.HB_SORT_LABEL['name' if view['sort'] == 'new' else 'new']}", lambda: self.hb_toggle_sort(holder[0], key))
            items = []
            if marks:
                items.append(Item(f"Tải & cài {len(marks)} game đã chọn", "", "green", [
                    "A — hỏi máy chủ kích thước từng file, chọn thẻ đích và xem kế hoạch (chưa tải gì).", "X — bỏ chọn tất cả."],
                    a=(f"Tải & cài {len(marks)} game", lambda: self.hb_plan(key, sorted(marks))),
                    x=("Bỏ chọn tất cả", lambda: self.lib_clear_marks(holder[0], key)), y=y))
            nflt = self.LIB_FILTER_LABEL[self.LIB_FILTER_CYCLE[(self.LIB_FILTER_CYCLE.index(view["flt"]) + 1) % 3]]
            flt = Item(f"Lọc: {self.LIB_FILTER_LABEL[view['flt']]}", f"{len(rows)} game", "unknown", [
                "Lọc theo việc game đã có trong Roms/ của máy (so tên file) hay chưa.", f"A — đổi sang: {nflt}."],
                a=(f"Lọc: {nflt}", lambda: self.lib_cycle_filter(holder[0], key)), y=y)
            flt.lib_ctl = "filter"
            items.append(flt)
            for e in rows:
                on = e["slug"] in marks
                lines = [e["description"] or "(không có mô tả)", "", f"Tác giả: {e['author']}",
                         f"Giấy phép: {e['license']}" + (f" (tài nguyên: {e['license_assets']})" if e.get("license_assets") else ""),
                         f"Nguồn: {e['repository'] or e['website'] or e['url']}", f"File: {e['file']}",
                         "Trên máy: " + ("ĐÃ CÓ file cùng tên trong Roms/" if e["present"] else "chưa có"),
                         "A — kiểm với máy chủ và xem kế hoạch tải & cài.", "X — chọn / bỏ chọn để tải nhiều game."]
                it = Item(("[x] " if on else "[ ] ") + e["title"], ("đã có " if e["present"] else "") + e["license_id"], "green" if on else "unknown", lines,
                          a=("Tải & cài", lambda s=e["slug"]: self.hb_plan(key, [s])),
                          x=("Bỏ chọn" if on else "Chọn", lambda s=e["slug"]: self.lib_toggle(holder[0], key, s)), y=y)
                it.lib_path = e["slug"]
                items.append(it)
            if not rows:
                items.append(Item("Không có game nào khớp", "", "unknown", ["Không có game nào khớp bộ lọc."], y=y))
            return header, items
        scr = ListScreen(self, f"HOMEBREW — {system}", build)
        scr.sel = next((i for i, it in enumerate(scr.items) if getattr(it, "lib_path", None)), scr.sel)
        holder.append(scr)
        self.push(scr)

    def hb_toggle_sort(self, scr, key):
        view, cur = self.lib_view[key], scr.cur_item()
        view["sort"] = "name" if view["sort"] == "new" else "new"
        self._lib_keep(scr, getattr(cur, "lib_path", None), getattr(cur, "lib_ctl", None))

    def hb_plan(self, key, slugs, pref="auto"):
        def done(pl):
            if pl["needs_card_choice"]:
                lines = ["!! " + pl["card"]["reason"], "", "LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."]
                prim = [o for o in pl["card"]["options"] if o["card"] == "primary"][0]
                if prim["usable"] and not pl["problems"]:
                    self.confirm("Thẻ phụ không dùng được", lines, lambda: self.hb_plan(key, slugs, "primary"), "Dùng thẻ Leaf")
                else:
                    self.info("Không có thẻ nào dùng được" if not prim["usable"] else "Không tải được",
                              lines + [f"!! {p['path']}: {p['reason']}" for p in pl["problems"]])
                return
            if not pl["ok"]:
                self.info("Không tải được", [f"!! {p['path']}: {p['reason']}" for p in pl["problems"]] or ["!! " + pl["message"]])
                return
            lines = [f"Thẻ đích: {pl['card']['root']}", ""]
            lines += [f"+ {f['title']} ({f['license_id']}) -> {f['system']} ({mb(f['size'])})" + ("  [đã có: importer bỏ qua nếu giống hệt]" if f.get("present") else "")
                      for f in pl["files"]]
            lines += ["", f"Tổng {mb(pl['total_bytes'])}; cần ~{mb(pl['needed_bytes'])} trống.",
                      "Tải thẳng qua Internet (HTTPS kiểm chứng thư; chỉ máy chủ Homebrew Hub/GitHub). Hash trong danh mục sai thì KHÔNG cài.",
                      "Cài bằng đúng trình Nhập game. Ghi chú giấy phép lưu ngoài Roms/ (dữ liệu LoongMate).",
                      "Mất mạng giữa chừng: tải tiếp được ở Homebrew > Tải dở."]
            use = "primary" if pl["card"]["selected"] == "primary" else pref
            self.confirm(f"Tải & cài {len(pl['files'])} game", lines, lambda: self.hb_do_fetch(key, slugs, use), "Tải & cài")
        self._hb_task("Đang kiểm với máy chủ", lambda: homebrew_mod.prepare(self._lib_env(), slugs, pref), done)

    def hb_do_fetch(self, key, slugs, pref):
        system = key[1]
        prog, cb = self._lib_progress()

        def work():
            env = self._lib_env()
            res = homebrew_mod.fetch(env, slugs, pref, cb)
            return res, homebrew_mod.list_games(env, system=system)

        def done(out):
            res, fresh = out
            self.lib_games[key] = fresh
            if res.get("success"):
                self.lib_marks.get(key, set()).difference_update(slugs)
            self.finish_change("Kết quả tải & cài", self._lib_result_lines(res), bool(res.get("changed")))
            self.refresh_cur()
        self._hb_task("Đang tải & cài", work, done, progress=prog)

    # ---- MÁY: lịch sử pin/nhiệt, trạng thái agent
    def open_history(self):
        if not self.need(self.app_data):
            return
        now = time.time()
        self.run("Đang đọc lịch sử", lambda: telemetry_mod.load(self.app_data, since_ts=now - 7 * 86400),
                 lambda rows: self.push(HistoryScreen(self, rows, now)))

    def open_battery_health(self):
        fn = getattr(telemetry_mod, "battery_health", None)
        if fn is None:
            self.info("Sức khoẻ pin", ["!! Engine chưa có telemetry.battery_health nên chưa tính được.", "Không đoán số."])
            return
        if not self.need(self.app_data):
            return

        def work():
            return fn(telemetry_mod.load(self.app_data, since_ts=time.time() - 90 * 86400))
        self.run("Đang tính sức khoẻ pin", work,
                 lambda bh: self.push(ListScreen(self, "SỨC KHOẺ PIN (ước lượng)", lambda: self._build_battery(bh))))

    @staticmethod
    def _build_battery(bh):
        def num(v, unit=""):
            return "không có" if v is None else f"{v:g}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"
        header = ["ƯỚC LƯỢNG từ mẫu telemetry (90 ngày gần nhất), không phải số đo chính hãng.", f"Số mẫu dùng: {num(bh.get('samples'))}"]
        if not bh.get("available"):
            return header, [Item("Chưa tính được", "", "unknown", ["!! " + str(bh.get("reason") or "không rõ lý do"),
                                                                    AGENT_HINT, "LoongMate không đoán số khi thiếu dữ liệu."])]
        pct = bh.get("estimated_pct")
        pc = "unknown" if pct is None else ("green" if pct >= 80 else "yellow" if pct >= 60 else "red")
        items = [
            Item("Dung lượng đầy hiện tại", num(bh.get("charge_full_mah"), " mAh"), "unknown", [
                "Dung lượng pin sạc đầy hiện nay mà máy ghi nhận (charge_full)."]),
            Item("Dung lượng thiết kế", num(bh.get("design_mah"), " mAh"), "unknown", [
                f"Nguồn: {bh.get('design_source')}", "Đây là mốc để so sánh, không do LoongMate đo."]),
            Item("Sức khoẻ ước lượng", num(pct, "%") + " (ước lượng)", pc, [
                "= dung lượng đầy hiện tại / dung lượng thiết kế.", "Ngưỡng màu của LoongMate: từ 80% xanh, từ 60% vàng, dưới đó đỏ.",
                f"Nguồn dung lượng thiết kế: {bh.get('design_source')}"]),
            Item("Sức khoẻ theo driver", num(bh.get("driver_health")), "unknown", [
                "Trường 'health' do driver pin của kernel báo; không phải con số của LoongMate."]),
            Item("Xu hướng", num(bh.get("trend_mah_per_30d"), " mAh/30 ngày"), "unknown", [
                "Thay đổi dung lượng đầy ước tính theo 30 ngày; chưa đủ dữ liệu thì 'không có'.", str(bh.get("reason") or "")]),
        ]
        return header, items

    def open_update(self):
        if self.need(self.app_data, self.probe.primary_path):
            self.push(ListScreen(self, "CẬP NHẬT LOONGMATE", self._build_update))

    def _build_update(self):
        if update_mod is None:
            return ["!! Engine chưa có engine/update.py."], [Item("Không khả dụng", "", "red", ["!! Thiếu engine/update.py trong bản LoongMate này."])]
        primary = self.probe.primary_path
        apps = default_apps_dir(primary)
        st = update_mod.status(self.app_data)
        inst = st.get("installed") or {}
        ver = inst.get("version")
        header = [f"Phiên bản đang chạy: {ver}" + (f" (cài lúc {inst['installed_at']})" if inst.get("installed_at") else "")
                  if ver else "Phiên bản đang chạy: chưa ghi nhận (bản cài tay, chưa từng cập nhật bằng LoongMate).",
                  f"Thư mục Apps: {apps}"]
        items = [Item("Gói cập nhật", head=True)]
        bundles = find_update_bundles(primary)
        for d in bundles:
            bv = bundle_version(d)
            items.append(Item(d.name, bv or "manifest lỗi", "yellow" if bv else "red", [
                f"Thư mục: {d}", f"Phiên bản trong gói: {bv or 'không đọc được manifest.json'}",
                "A — kiểm tra từng file bằng SHA-256 (có thể mất vài giây), rồi hỏi xác nhận mới cập nhật.",
                "Cập nhật chỉ ghi vào thư mục pak của LoongMate (LoongMate, LoongMateAgent; gỡ LoongMateNative cũ nếu gói yêu cầu); không đụng ROM/save/state.",
                "Bản đang chạy được sao lưu trước; lỗi giữa chừng thì tự trả về bản cũ."],
                a=("Kiểm tra & cập nhật", lambda d=d: self.do_update_verify(d))))
        if not bundles:
            items.append(Item("(Không thấy gói cập nhật nào)", head=True))
            items.append(Item("Cách đặt gói", "", "unknown", [
                f"Chép thư mục gói (có manifest.json) vào {Path(primary or '<thẻ chính>') / UPDATE_DIR_NAME}/<tên gói>/ rồi mở lại màn này."]))
        items.append(Item("Quay về", head=True))
        backs = st.get("backups") or []
        if backs:
            b = backs[-1]
            vb = b.get("version_before")
            items.append(Item("Quay về bản trước", vb or "bản cài tay", "yellow", [
                f"Bản sao lưu mới nhất: {b.get('name')}", f"Phiên bản trong bản sao lưu: {vb or 'không rõ (bản cài tay trước khi cập nhật)'}",
                f"Có {len(backs)} bản sao lưu; quay về luôn dùng bản mới nhất.",
                "Có hộp xác nhận (mặc định Huỷ)."], a=("Quay về", lambda b=b: self.ask_rollback_update(b))))
        else:
            items.append(Item("Quay về bản trước", "không có", "unknown", ["Chưa có bản sao lưu nào (chưa từng cập nhật bằng LoongMate)."]))
        return header, items

    def do_update_verify(self, d: Path):
        self.run("Đang kiểm tra gói", lambda: update_mod.verify_bundle(d), lambda v: self._after_verify(d, v))

    def _after_verify(self, d: Path, v):
        if not v.get("ok"):
            errs = v.get("errors") or ["không rõ lý do"]
            self.info("Gói KHÔNG hợp lệ", ["!! Không cập nhật. Các lỗi:"] + [f"!! {e}" for e in errs[:8]] +
                      ([f"… và {len(errs) - 8} lỗi nữa"] if len(errs) > 8 else []))
            return
        inst = (update_mod.status(self.app_data).get("installed") or {}).get("version")
        self.confirm("Xác nhận cập nhật LoongMate", [
            f"+ Gói hợp lệ: {v.get('files')} file khớp SHA-256.", f"Lên phiên bản: {v.get('version')}",
            f"Đang chạy: {inst or 'chưa ghi nhận'}", f"Sẽ thay: {', '.join(v.get('paks') or [])}",
            "Bản đang chạy được sao lưu trước; lỗi giữa chừng thì tự trả về bản cũ.",
            f"{RESTART_NOTE} sau khi cập nhật."], lambda: self.do_update_apply(d), "Cập nhật")

    def do_update_apply(self, d: Path):
        apps = default_apps_dir(self.probe.primary_path)

        def done(r):
            if r.get("ok"):
                lines = [f"+ Đã cập nhật lên {r.get('version')} (trước đó: {r.get('previous_version') or 'chưa ghi nhận'}).",
                         f"Thay: {', '.join(r.get('paks') or [])}  ({r.get('files')} file)", f"Bản sao lưu: {r.get('backup')}",
                         "", f"!! {RESTART_NOTE}."]
            else:
                lines = [f"!! Cập nhật thất bại ở bước '{r.get('stage')}': {r.get('error')}",
                         "+ Đã trả về bản cũ." if r.get("rolled_back") else "Chưa thay gì."] + [f"!! {e}" for e in (r.get("errors") or [])[:6]]
            self.info("Kết quả cập nhật", lines)
            self.refresh_cur()
        self.run("Đang cập nhật", lambda: update_mod.apply_update(d, apps, self.app_data), done)

    def ask_rollback_update(self, b):
        self.confirm("Xác nhận quay về bản trước", [
            f"Sẽ khôi phục các pak từ bản sao lưu {b.get('name')}.", f"Phiên bản trong bản sao lưu: {b.get('version_before') or 'không rõ'}",
            "Bản đang chạy sẽ bị thay (các bản sao lưu được giữ nguyên).", f"{RESTART_NOTE_ROLLBACK} sau khi quay về."],
            self.do_update_rollback, "Quay về")

    def do_update_rollback(self):
        apps = default_apps_dir(self.probe.primary_path)

        def done(r):
            if r.get("ok"):
                lines = [f"+ Đã quay về {r.get('version') or 'bản cài tay'} (từ {r.get('from_version') or 'chưa ghi nhận'}).",
                         f"Khôi phục: {', '.join(r.get('paks') or [])}", f"Từ bản sao lưu: {r.get('backup')}", "", f"!! {RESTART_NOTE_ROLLBACK}."]
            else:
                lines = [f"!! Quay về thất bại: {r.get('error')}", "+ Đã trả về như trước." if r.get("rolled_back") else "Chưa thay gì."]
            self.info("Kết quả quay về", lines)
            self.refresh_cur()
        self.run("Đang quay về bản trước", lambda: update_mod.rollback(apps, self.app_data), done)

    # ---- LEAF HOT TABS
    def open_hottabs(self):
        if self.need(self.probe.primary_path):
            self.push(ListScreen(self, "LEAF HOT TABS", self._build_hottabs))

    def _build_hottabs(self):
        sd = self.probe.primary_path
        ud = hottabs_mod.userdata_for(sd, os.environ.get("USERDATA_PATH"))
        st = hottabs_mod.status(sd, ud)
        inst = st["installed"]
        if not st["bundle_matches"] and st["bundle_version"]:
            line, tag, tc = "Không khớp phiên bản Leaf — cần build lại bộ cài", "không khớp", "red"
        elif inst == "patched":
            line, tag, tc = "Đang dùng bản Hot Tabs", "Hot Tabs", "green"
        elif inst == "stock":
            line, tag, tc = "Bản gốc của Leaf (chưa cài Hot Tabs)", "bản gốc", "yellow"
        elif inst == "absent":
            line, tag, tc = "Không thấy launcher của Leaf trên thẻ", "không có", "red"
        else:
            line, tag, tc = "Không nhận diện được launcher (không có bộ cài trên thẻ hoặc md5 lạ)", "không rõ", "yellow"
        header = [f"Trạng thái: {line}", f"Leaf: {st['leaf_version'] or 'không đọc được'}; bộ cài: {st['bundle_version'] or 'không có trên thẻ'}",
                  "Leaf vừa cập nhật? Hot Tabs bị thay về bản gốc — xem docs/CAP-NHAT-LEAF.md"]
        items = [Item("Trạng thái", head=True)]
        items.append(Item("Launcher", tag, tc, [
            st["message"], f"Phiên bản Leaf: {st['leaf_version'] or '?'}  (commit launcher {st['launcher_commit'] or '?'})",
            f"Bộ cài trên thẻ: {st['bundle_version'] or 'không có'}" + (" — khớp Leaf này." if st["bundle_matches"] else " — KHÔNG khớp Leaf này."),
            f"Bản gốc đã sao lưu: {'có' if st['backup_present'] else 'không'}",
            "Cập nhật Leaf thay cả thư mục launcher nên Hot Tabs biến mất (máy về bản gốc, vô hại). Cài lại theo docs/CAP-NHAT-LEAF.md."]))
        items.append(Item("Tính năng", head=True))
        on = st["resume_from_lists"]
        items.append(Item("Tự tiếp tục khi mở từ Games/Favorites", "Bật" if on else "Tắt", "green" if on else "yellow", [
            "Bật: mở game từ Games/Favorites thì vào thẳng chỗ đang chơi dở như Recents (game chưa có state thì chạy mới).",
            "Tắt: Games/Favorites luôn chạy mới như Leaf gốc.",
            "A đổi ngay; launcher kiểm mỗi lần mở game nên không cần khởi động lại.",
            "Không tốn pin: không có gì chạy nền, chỉ khác lúc mở game.",
            "! Chỉ có tác dụng khi đang dùng bản Hot Tabs." if inst != "patched" else "Ghi một file cờ nhỏ trên thẻ; bật = xoá file cờ."],
            a=("Bật" if not on else "Tắt", self.do_hottabs_toggle)))
        items.append(Item("Cài / gỡ", head=True))
        can = st["installer_path"] is not None and st["bundle_matches"]
        why = ("" if can else "!! Không dùng được: " + ("không có hottabs/hottabs.sh trên thẻ." if st["installer_path"] is None
                                                       else "bộ cài không khớp phiên bản Leaf — cần build lại (docs/CAP-NHAT-LEAF.md)."))
        if inst == "patched":
            items.append(Item("Gỡ Hot Tabs", "", "unknown", [
                "Trả jawaka-launcher về bản gốc từ bản sao lưu (kiểm md5). Xem trước rồi hỏi xác nhận (mặc định Huỷ).",
                f"{hottabs_mod.RESTART_NOTE} sau khi gỡ.", why], a=("Gỡ", lambda: self.ask_hottabs("uninstall")) if can else None))
        else:
            items.append(Item("Cài lại Hot Tabs", "", "unknown", [
                "Thay jawaka-launcher bằng bản Hot Tabs (chỉ khi launcher hiện tại đúng bản gốc của Leaf; sao lưu trước, kiểm md5).",
                "Xem trước rồi hỏi xác nhận (mặc định Huỷ).", f"{hottabs_mod.RESTART_NOTE} sau khi cài.", why],
                a=("Cài", lambda: self.ask_hottabs("install")) if can else None))
        return header, items

    def do_hottabs_toggle(self):
        sd = self.probe.primary_path
        ud = hottabs_mod.userdata_for(sd, os.environ.get("USERDATA_PATH"))
        want_on = not hottabs_mod.status(sd, ud)["resume_from_lists"]
        try:
            hottabs_mod.set_resume(ud, want_on)
        except OSError as e:
            self.info("Lỗi", [f"!! Không ghi được file cờ: {e}"])
            return
        self.refresh_cur()

    def ask_hottabs(self, action):
        sd = self.probe.primary_path
        word = "gỡ" if action == "uninstall" else "cài"

        def preview(r):
            if not r["ok"]:
                self.info("Bộ cài từ chối", ["!! Không đổi gì.", ""] + [f"!! {x}" for x in r["output"].splitlines()[-8:]])
                return
            self.confirm(f"Xác nhận {word} Hot Tabs", ["Bản xem trước (chưa ghi gì):", ""] + r["output"].splitlines() + [
                "", f"!! {hottabs_mod.RESTART_NOTE} sau khi {word}."], lambda: self.do_hottabs_run(action), word.capitalize())
        self.run("Đang xem trước", lambda: hottabs_mod.run_installer(sd, action, dry_run=True), preview)

    def do_hottabs_run(self, action):
        sd = self.probe.primary_path
        word = "gỡ" if action == "uninstall" else "cài"

        def done(r):
            if r["ok"]:
                lines = [f"+ Đã {word} Hot Tabs.", "", f"!! {hottabs_mod.RESTART_NOTE}."] + [""] + r["output"].splitlines()[-6:]
            else:
                lines = [f"!! {word.capitalize()} thất bại (bộ cài tự trả về như trước nếu đã ghi dở):"] + [f"!! {x}" for x in r["output"].splitlines()[-8:]]
            self.info(f"Kết quả {word} Hot Tabs", lines)
            self.refresh_cur()
        self.run(f"Đang {word} Hot Tabs", lambda: hottabs_mod.run_installer(sd, action, dry_run=False), done)

    def open_agent(self):
        if self.need(self.app_data):
            self.push(ListScreen(self, "TRẠNG THÁI AGENT", self._build_agent))

    @staticmethod
    def agent_running(app_data) -> Optional[bool]:
        """Chỉ đọc; xem agent.lock_held."""
        return agent_mod.lock_held(app_data)

    def _build_agent(self):
        ad, now = Path(self.app_data), time.time()
        running = self.agent_running(ad)
        try:
            st = json.loads((ad / agent_mod.STATE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        ex: List[str] = []
        if running:
            ex.append(f"Đang chạy (pid {PidLock(ad / agent_mod.LOCK_NAME).holder_pid() or '?'}).")
            if st.get("started"):
                ex.append(f"Khởi động lúc: {st['started']}")
        elif running is None:
            ex += ["!! Chưa từng chạy: không có file khoá agent trong thư mục dữ liệu LoongMate.", AGENT_HINT]
        else:
            ex += ["! Agent KHÔNG chạy (không ai giữ khoá). Không có mẫu pin/nhiệt mới và không tự sao lưu khi thoát game.", AGENT_HINT]
            if st.get("started"):
                ex.append(f"Lần chạy gần nhất khởi động lúc: {st['started']}")
        items = [Item("LoongMate Agent", "ĐANG CHẠY" if running else ("KHÔNG CHẠY" if running is False else "chưa từng chạy"),
                      "green" if running else ("yellow" if running is False else "unknown"), ex)]
        lb = st.get("last_backup")
        if isinstance(lb, dict) and lb.get("at"):
            ok = bool(lb.get("success"))
            bex = [f"Lúc: {lb['at']}", f"Snapshot: {lb.get('id')}", "+ Thành công." if ok else f"!! Thất bại: {lb.get('error') or 'không rõ nguyên nhân'}"]
            items.append(Item("Sao lưu tự động gần nhất", lb["at"], "green" if ok else "red", bex))
        else:
            items.append(Item("Sao lưu tự động gần nhất", "chưa có", "unknown", [
                "Chưa ghi nhận lần sao lưu tự động nào.", "Agent tự sao lưu SaveVault khi phát hiện bạn thoát game."]))
        rows = telemetry_mod.load(ad, since_ts=now - 7 * 86400)
        if rows:
            r = rows[-1]
            age = int((now - r["ts"]) / 60)
            items.append(Item("Mẫu pin/nhiệt gần nhất", f"{age} phút trước", "green" if running and age <= 15 else "yellow", [
                f"Lúc: {time.strftime('%d/%m %H:%M:%S', time.localtime(r['ts']))}",
                f"Pin: {r['battery_pct']}% ({r['status']})", f"SoC: {r['soc_c']}°C   GPU: {r['gpu_c']}°C   Pin: {r['batt_c']}°C",
                f"7 ngày qua: {len(rows)} mẫu."] + (["! Mẫu đã cũ: agent không còn ghi mẫu mới."] if age > 15 else [])))
        else:
            items.append(Item("Mẫu pin/nhiệt gần nhất", "chưa có", "unknown", ["Chưa có mẫu nào trong 7 ngày qua.", AGENT_HINT]))
        vv = vault_verify_status(st, now)
        if vv["level"] == "none":
            items.append(Item("Kiểm tra toàn vẹn kho", "chưa có", "unknown", ["Agent chưa ghi kết quả kiểm tra toàn vẹn SaveVault."]))
        else:
            vex = [vv["line"]]
            if vv["ts"] is not None:
                vex.append(f"Lúc: {_fmt_ts(vv['ts'])}")
            if vv["checked"] is not None:
                vex.append(f"Đã kiểm: {vv['checked']} object")
            vex.append(f"Hỏng: {len(vv['bad'])}   Thiếu: {vv['missing']}" + ("   (kiểm một phần)" if vv["partial"] else ""))
            items.append(Item("Kiểm tra toàn vẹn kho", {"ok": "ổn", "stale": "cũ", "bad": "CÓ LỖI"}[vv["level"]], vv["color"], vex))
        pw = st.get("prewarm")
        if isinstance(pw, dict) and isinstance(pw.get("done"), bool):
            pex = [prewarm_line(st), "Agent băm sẵn ROM nghi trùng khi rảnh để lần quét Dọn ROM trùng chạy nhanh."]
            if _num(pw.get("hashed")) is not None:
                pex.append(f"Đã băm: {int(pw['hashed'])} file")
            if _num(pw.get("remaining")) is not None:
                pex.append(f"Còn lại: {int(pw['remaining'])} file")
            if _num(pw.get("ts")) is not None:
                pex.append(f"Cập nhật lúc: {_fmt_ts(pw['ts'])}")
            items.append(Item("Băm trước (hash)", "xong" if pw["done"] else "đang làm", "green" if pw["done"] else "yellow", pex))
        else:
            items.append(Item("Băm trước (hash)", "chưa có", "unknown", ["Agent chưa ghi trạng thái băm trước."]))
        rl = st.get("reload")
        if isinstance(rl, dict) and isinstance(rl.get("to"), str):
            rex = [f"Lên bản: {rl['to']}", f"Từ bản: {rl['from'] if isinstance(rl.get('from'), str) else 'không rõ (lần đầu)'}"]
            if _num(rl.get("ts")) is not None:
                rex.append(f"Lúc: {_fmt_ts(rl['ts'])}")
            rex.append("Agent tự nạp lại chính nó sau khi cập nhật LoongMate.")
            items.append(Item("Tự nạp lại gần nhất", rl["to"], "green", rex))
        else:
            items.append(Item("Tự nạp lại gần nhất", "chưa có", "unknown", ["Chưa ghi nhận lần agent tự nạp lại sau cập nhật nào."]))
        items.append(Item("Cách bật service", "", "unknown", [
            AGENT_HINT, "Service chạy nền: ghi mẫu pin/nhiệt mỗi 5 phút và tự sao lưu khi thoát game.",
            "Chỉ chạy một bản agent (khoá agent.lock)."]))
        return ["Chỉ đọc: màn này không ghi gì."], items

    # ---- MÁY: thiết bị trong mạng
    def open_nodes(self):
        if self.need(self.app_data):
            self.fetch_nodes(lambda: self.push(ListScreen(self, "THIẾT BỊ TRONG MẠNG", self._build_nodes)))

    def fetch_nodes(self, then):
        store = ConfigStore(self.app_data)

        def done(res):
            self.nodes_data = res
            then()
        self.run("Đang đọc danh sách thiết bị", lambda: nodes_mod.list_nodes(store=store), done)

    def refresh_nodes(self):
        self.fetch_nodes(lambda: self.cur().refresh())

    def _node(self, name):
        return next((n for n in (self.nodes_data or {}).get("nodes", []) if n["name"] == name), None)

    @staticmethod
    def node_lines(n) -> List[str]:
        out = [f"IP: {n['ip'] or '(không có)'}", f"Hệ điều hành: {n['os'] or 'không rõ'}",
               "Trạng thái: ONLINE" if n["online"] else "Trạng thái: OFFLINE" + (f" (thấy lần cuối {n['last_seen']})" if n["last_seen"] else "")]
        out.append({"direct": "Đường đi lần cuối biết: trực tiếp", "relay": f"Đường đi lần cuối biết: qua relay {n['relay']}"}
                   .get(n["path"], "Đường đi: không rõ") + " (muốn đo thật dùng Ping)")
        out.append("Yêu thích: có" if n["favorite"] else "Yêu thích: không")
        out.append(f"MAC (cho Wake-on-LAN): {fmt_mac(n['mac'])}" if n["mac"] else "MAC: chưa lưu (cần để dùng Wake-on-LAN)")
        return out

    def _build_nodes(self):
        d = self.nodes_data or {}
        header = []
        nodes = d.get("nodes", [])
        if not d.get("ok"):
            header += ["!! " + (d.get("error") or "Chưa đọc được danh sách."),
                       "Tailscale có đang chạy không? LoongMate chỉ đọc trạng thái, không quản lý daemon."]
        else:
            me = d.get("self")
            header.append(f"Tailscale: {d.get('backend_state') or '?'}" + (f" • máy này: {me['name']} {me['ip']}" if me else ""))
            header.append(f"{len(nodes)} máy khác, {sum(1 for n in nodes if n['online'])} đang online.")
        items = [Item("Làm mới danh sách", "", "unknown", ["Đọc lại trạng thái các máy trong tailnet (chạy nền)."], a=("Làm mới", self.refresh_nodes))]
        grp = None
        for n in nodes:
            g_ = "Yêu thích" if n["favorite"] else ("Online" if n["online"] else "Offline")
            if g_ != grp:
                grp = g_
                items.append(Item(g_, head=True))
            ex = self.node_lines(n)
            for k, lab in (("ping", "Ping"), ("probe", "Kiểm cổng")):
                if k in self.node_res.get(n["name"], {}):
                    ex.append(f"{lab} gần nhất: {self.node_res[n['name']][k]['summary']}")
            items.append(Item(n["name"], "online" if n["online"] else "offline", "green" if n["online"] else "unknown", ex,
                              a=("Mở", lambda name=n["name"]: self.open_node(name))))
        if d.get("ok") and not nodes:
            items.append(Item("(Không có máy nào khác trong tailnet)", head=True))
        return header, items

    def open_node(self, name):
        self.push(ListScreen(self, f"THIẾT BỊ — {name}", lambda: self._build_node(name)))

    def _build_node(self, name):
        n = self._node(name)
        if n is None:
            return [f"!! '{name}' không còn trong danh sách — quay lại và làm mới."], []
        res = self.node_res.get(name, {})
        header = [f"{n['ip'] or 'không có IP'} • {'ONLINE' if n['online'] else 'OFFLINE'} • {n['os'] or 'OS không rõ'}"]
        pg, pb = res.get("ping"), res.get("probe")
        items = [
            Item("Ping", pg["tag"] if pg else "chưa đo", pg["color"] if pg else "unknown", [
                "Chạy `tailscale ping` một lần: đo thật, không phải trạng thái cũ.",
                "Trong tailnet không có ICMP ping; đây là ping của Tailscale."] + ([f"Kết quả ({pg['at']}): {pg['summary']}"] if pg else []),
                a=("Ping", lambda: self.do_ping(name))),
            Item("Kiểm cổng", pb["tag"] if pb else "chưa đo", pb["color"] if pb else "unknown", [
                "Thử nối TCP tới SSH (22), SMB (445), Sunshine (47989), RDP (3389) qua SOCKS5 của Tailscale.",
                "'Không phản hồi' thường là tường lửa chặn hoặc chưa có dịch vụ; 'đóng' là máy chủ động từ chối.",
                "Mất tới vài giây."] + ([f"Kết quả ({pb['at']}):"] + pb["lines"] if pb else []),
                a=("Kiểm cổng", lambda: self.do_probe(name))),
            Item("Yêu thích", "có" if n["favorite"] else "không", "green" if n["favorite"] else "unknown", [
                "Máy yêu thích được đưa lên đầu danh sách. Lưu trong config.json của LoongMate.", "A — bật/tắt."],
                a=("Bật/tắt", lambda: self.toggle_fav(name))),
            Item("Wake-on-LAN", "có MAC" if n["mac"] else "chưa có MAC", "yellow" if n["mac"] else "unknown", [
                "Gửi gói đánh thức UDP broadcast tới MAC đã lưu.",
                (f"MAC: {fmt_mac(n['mac'])}" if n["mac"] else "! Chưa lưu MAC cho máy này. Lưu bằng lệnh: loongmate nodes mac " + name + " <MAC>"),
                "! CHƯA KIỂM CHỨNG trên Wi-Fi của máy: nhiều AP chặn broadcast; máy đích phải bật WOL trong BIOS và driver.",
                "Gói đi qua LAN, KHÔNG qua tailnet. 'Đã gửi' không có nghĩa là máy đã dậy."],
                a=("Gửi gói đánh thức", lambda: self.ask_wol(name)) if n["mac"] else None),
        ]
        return header, items

    def _remember(self, name, key, summary, tag, color, lines=()):
        self.node_res.setdefault(name, {})[key] = {"summary": summary, "tag": tag, "color": color, "lines": list(lines),
                                                   "at": time.strftime("%H:%M:%S")}

    def do_ping(self, name):
        def done(r):
            if r["ok"]:
                self._remember(name, "ping", r["message"], f"{r['latency_ms']:g} ms", "green")
                self.info(f"Ping {name}", ["+ " + r["message"], f"Đường: {'trực tiếp' if r['via'] == 'direct' else 'qua relay DERP ' + r['derp']}"])
            else:
                self._remember(name, "ping", r["message"], "lỗi", "red")
                self.info(f"Ping {name}", ["!! Không ping được:", r["message"]])
            self.cur().refresh()
        self.run("Đang ping", lambda: nodes_mod.ping_node(name), done)

    PORT_STATE_VI = {"open": "MỞ", "closed": "đóng", "unreachable": "không tới được", "timeout": "không phản hồi", "error": "lỗi"}

    def do_probe(self, name):
        n = self._node(name)
        if n is None:
            return

        def done(r):
            if not r["ok"]:
                self._remember(name, "probe", r["error"], "lỗi", "red")
                self.info(f"Kiểm cổng {name}", ["!! " + r["error"]])
            else:
                lines = [f"{p['label']}: {self.PORT_STATE_VI.get(p['state'], p['state'])} ({p['ms']} ms) — {p['detail']}" for p in r["ports"]]
                nopen = sum(1 for p in r["ports"] if p["state"] == "open")
                self._remember(name, "probe", f"{nopen}/{len(r['ports'])} cổng mở", f"{nopen}/{len(r['ports'])} mở",
                               "green" if nopen else "yellow", lines)
                self.info(f"Kiểm cổng {name} ({r['host']})", [("+ " if p["state"] == "open" else "") + ln for p, ln in zip(r["ports"], lines)])
            self.cur().refresh()
        self.run("Đang kiểm cổng", lambda: nodes_mod.probe_node(n), done)

    def toggle_fav(self, name):
        n = self._node(name)
        if n is None:
            return
        try:
            set_node_favorite(ConfigStore(self.app_data), name, not n["favorite"])
        except (ValueError, OSError) as e:
            self.info("Lỗi", [f"!! Không lưu được yêu thích: {e}"])
            return
        n["favorite"] = not n["favorite"]
        self.nodes_data["nodes"].sort(key=lambda x: (not x["favorite"], not x["online"], x["name"].lower()))
        self.cur().refresh()

    def ask_wol(self, name):
        n = self._node(name)
        if n is None or not n["mac"]:
            return
        bc = get_nodes_settings(ConfigStore(self.app_data))["macs"].get(name, {}).get("broadcast") or "255.255.255.255"
        self.confirm(f"Wake-on-LAN {name}", [
            f"Sẽ gửi gói đánh thức tới MAC {fmt_mac(n['mac'])} qua broadcast {bc}:9.",
            "Gói đi qua LAN (Wi-Fi của máy), KHÔNG qua tailnet.",
            "! CHƯA KIỂM CHỨNG trên Wi-Fi của máy: nhiều AP chặn broadcast / cô lập client.",
            "! Máy đích phải bật Wake-on-LAN trong BIOS và driver.",
            "'Đã gửi' chỉ nghĩa là gói đã rời máy này, không chứng tỏ máy đích dậy."],
            lambda: self.do_wol(name, n["mac"], bc), "Gửi")

    def do_wol(self, name, mac, bc):
        def done(r):
            self.info(f"Wake-on-LAN {name}", [("+ " if r["ok"] else "!! ") + r["message"],
                                              "! Chưa kiểm chứng trên Wi-Fi của máy: hãy kiểm tra máy đích có dậy thật không."])
        self.run("Đang gửi gói đánh thức", lambda: nodes_mod.wake_on_lan(mac, broadcast=bc), done)

    # ---- THẺ NHỚ: chuyển game sang thẻ kia
    SD = {"primary": "SD1", "secondary": "SD2"}

    def open_transfer(self):
        if not self.need(self.inv_engine, self.app_data):
            return
        sec = self.probe.get_readiness_summary()["storage"].get("secondary")
        if not (sec and sec.get("mounted")):
            self.info("Chuyển game", ["Chưa gắn thẻ ở khe 2 — cần cả hai thẻ để chuyển game."])
            return
        self.push(ListScreen(self, "CHUYỂN GAME SANG THẺ KIA", self._build_transfer))

    def _build_transfer(self):
        st = self.probe.get_readiness_summary()["storage"]
        fr = lambda c: f"{c['free_mb']:.0f} MB trống" if c and c.get("mounted") else "không có"
        header = [f"SD1: {fr(st.get('primary'))}   SD2: {fr(st.get('secondary'))}",
                  "Các bước ở đây chỉ XEM gợi ý; chưa chép/xoá gì cho tới khi bạn xác nhận."]
        items: List[Item] = []
        for src, dst in (("primary", "secondary"), ("secondary", "primary")):
            S, D = self.SD[src], self.SD[dst]
            items.append(Item(f"Gợi ý hàng loạt: giải phóng {S} (chuyển sang {D})", head=True))
            for gb in CHUNK_GB:
                items.append(Item(f"Để {S} trống {gb} GB", "", "unknown", [
                    f"Đề xuất game nên chuyển từ {S} sang {D} để {S} còn trống ít nhất {gb} GB (quét ~15 giây).",
                    "Ưu tiên game không có save; vừa đủ thì lấy game nhỏ nhất đủ lớn.",
                    "Bỏ qua game có save/state sửa trong 60 phút qua (coi là đang chơi).",
                    "Bước này chỉ liệt kê, chưa chuyển gì."], a=("Xem gợi ý", lambda d=dst, g=gb: self.do_bulk_plan(d, g * 1024))))
        items.append(Item("Chọn từng game", head=True))
        for src in ("primary", "secondary"):
            S, D = self.SD[src], self.SD["secondary" if src == "primary" else "primary"]
            items.append(Item(f"Từ {S} sang {D}", "", "unknown", [
                f"Duyệt hệ máy > game trên {S}, xem kế hoạch rồi mới xác nhận.",
                "Game nhiều file (.cue/.m3u): chọn file .cue/.m3u để chuyển trọn bộ."], a=("Duyệt", lambda s_=src: self.open_move_systems(s_))))
        return header, items

    def do_bulk_plan(self, dest, target_mb):
        p, s = self.probe.primary_path, self.probe.secondary_path

        def work():
            self.inv = self.inv_engine.scan_all()
            return transfer_mod.plan_bulk_move(self.inv, p, s, dest, target_free_mb=target_mb)

        def done(plan):
            warns = [f"! {w}" for w in plan["warnings"]]
            if not plan["ok"]:
                self.info("Không lập được gợi ý", [f"!! {plan['message']}"] + warns)
            elif not plan["moves"]:
                self.info("Gợi ý chuyển hàng loạt", [("+ " if plan["reached"] else "!! ") + plan["message"]] + warns +
                          [f"Bỏ qua {x['rom_path']}: {x['reason']}" for x in plan["skipped"]])
            else:
                self.push(ListScreen(self, "GỢI Ý CHUYỂN HÀNG LOẠT", lambda: self._build_bulk(plan)))
        self.run("Đang quét & lập gợi ý", work, done)

    def _build_bulk(self, plan):
        S, D = self.SD[plan["source_card"]], self.SD[plan["dest_card"]]
        mv = plan["moves"]
        header = [("" if plan["reached"] else f"! CHƯA đạt mục tiêu {plan['target_free_mb']} MB: ") +
                  f"{len(mv)} game, {S} từ {plan['free_before_mb']} lên {plan['free_after_mb']} MB trống."] + [f"! {w}" for w in plan["warnings"]]
        items = [Item(f"Chuyển TẤT CẢ {len(mv)} game sang {D}", mb(plan["bytes_freed"]), "yellow", [
            f"{len(mv)} game: giải phóng {mb(plan['bytes_freed'])} ở {S}, chép tổng {mb(plan['bytes_copy'])} sang {D}.",
            f"{S} từ {plan['free_before_mb']} MB lên {plan['free_after_mb']} MB trống" +
            ("." if plan["reached"] else f" — CHƯA đạt mục tiêu {plan['target_free_mb']} MB."),
            "Bước sau là hộp xác nhận (mặc định Huỷ)."], a=("Tiếp: xác nhận", lambda: self.ask_moves([m["plan"] for m in mv], plan)))]
        items.append(Item(f"Game sẽ chuyển ({len(mv)})", head=True))
        for m in mv:
            ex = [f"Hệ: {m['system']}", f"Giải phóng ở {S}: {mb(m['bytes_freed'])}", f"Chép sang {D}: {mb(m['bytes_copy'])}",
                  ("! CÓ save/state: được CHÉP sang " + D + " nhưng GIỮ LẠI ở " + S + ".") if m["has_save"] else "Không có save/state đi kèm.",
                  "", m["rom_path"]]
            ex += [f"• {e['kind']}: {Path(e['src']).name} ({mb(e['size'])})" + ("" if e["remove_src"] else " — chỉ chép") for e in m["plan"]["files"]]
            items.append(Item(m["name"], mb(m["bytes_freed"]) + (" +save" if m["has_save"] else ""), "yellow" if m["has_save"] else "unknown", ex))
        if plan["skipped"]:
            items.append(Item(f"Bỏ qua ({len(plan['skipped'])})", head=True))
            for x in plan["skipped"]:
                items.append(Item(Path(x["rom_path"]).name, "bỏ qua", "unknown", [x["reason"], x["rom_path"]]))
        return header, items

    def ask_moves(self, plans, bulk=None):
        S, D = self.SD[plans[0]["source_card"]], self.SD[plans[0]["dest_card"]]
        lines = [f"CHUYỂN {len(plans)} game từ {S} sang {D}."]
        if bulk:
            lines.append(f"• Giải phóng khoảng {mb(bulk['bytes_freed'])} ở {S}; chép tổng {mb(bulk['bytes_copy'])} sang {D}.")
        lines += ["• Mỗi game: chép sang thẻ đích, xác minh SHA-256 từng file, CHỈ KHI cả game đủ mới xoá bản ở thẻ nguồn.",
                  "• File nào lỗi hoặc lệch hash: xoá bản chép dở, nguồn còn nguyên (hoàn tác cả game đó).",
                  "• Đích đã có file cùng tên khác nội dung: từ chối cả game, không ghi đè.",
                  "! Save/state ĐƯỢC CHÉP sang thẻ đích nhưng GIỮ LẠI ở thẻ cũ (CHƯA KIỂM CHỨNG: Leaf/RetroArch tìm save theo thẻ chứa ROM hay luôn ở Primary).",
                  f"• Sổ ghi: {Path(self.probe.userdata_path) / artwork_mod.LEDGER_NAME}", ""]
        for pl in plans:
            lines.append(f"• {pl['game']} — giải phóng {mb(pl['bytes_freed'])}")
        if len(plans) == 1:
            lines += [""] + [f"{e['kind']}: {Path(e['src']).name} ({mb(e['size'])})" + ("" if e["remove_src"] else " — chỉ chép, giữ nguồn")
                             for e in plans[0]["files"]]
            lines += [f"! {w}" for w in plans[0]["warnings"] if "Save/state được CHÉP" not in w]
        self.confirm("Xác nhận chuyển game", lines, lambda: self.do_moves(plans), "CHUYỂN")

    def do_moves(self, plans):
        p, s, ad = self.probe.primary_path, self.probe.secondary_path, self.probe.userdata_path
        src = plans[0]["source_card"]

        def work():
            res = [transfer_mod.execute_move(pl, ad, p, s) for pl in plans]
            return res, self.probe.get_readiness_summary()["storage"].get(src)

        def done(out):
            results, card = out
            n_mv = sum(len(r["moved"]) for r in results)
            n_cp = sum(len(r["copied"]) for r in results)
            n_rf = sum(len(r["refused"]) for r in results)
            n_rb = sum(1 for r in results if r["rolled_back"])
            lines = [("+ " if n_mv else "") + f"Đã chuyển (moved): {n_mv} file, giải phóng {mb(sum(r['bytes_moved'] for r in results))} ở {self.SD[src]}.",
                     f"Chỉ chép, nguồn giữ nguyên (copied): {n_cp} file (save/state, hoặc ảnh còn được game khác dùng).",
                     ("!! " if n_rf else "") + f"Từ chối (refused): {n_rf}",
                     ("!! " if n_rb else "") + f"Hoàn tác cả game (rolled_back): {n_rb} game"]
            if card and card.get("mounted"):
                lines.append(f"{self.SD[src]} hiện trống {card['free_mb']:.0f} MB.")
            for pl, r in zip(plans, results):
                if len(plans) > 1:
                    lines.append(f"• {pl['game']}: moved {len(r['moved'])}, copied {len(r['copied'])}" + (", ROLLED BACK" if r["rolled_back"] else ""))
                lines += [f"!! TỪ CHỐI {x['path']}: {x['reason']}" for x in r["refused"]]
            lines.append(f"Sổ: {Path(ad) / artwork_mod.LEDGER_NAME}")
            self.inv = None
            self.dup_cache.clear()
            self.dup_compare = None
            while len(self.stack) > 2:
                self.stack.pop()
            self.finish_change("Kết quả chuyển game", lines, bool(n_mv or n_cp))
            self.refresh_cur()
        self.run("Đang chuyển game", work, done)

    def open_move_systems(self, src):
        self._with_inv(lambda: self.push(ListScreen(self, f"CHUYỂN GAME — hệ máy trên {self.SD[src]}", lambda: self._build_move_systems(src))))

    def _src_roms(self, src):
        return ((self.inv or {}).get(src) or {}).get("roms", [])

    def _build_move_systems(self, src):
        by: Dict[str, List[dict]] = {}
        for r in self._src_roms(src):
            by.setdefault(r["system"], []).append(r)
        items = [Item(s_, f"{len(rs)} file", "unknown", [f"Hệ máy: {s_}", f"Trên {self.SD[src]}: {len(rs)} file, {mb(sum(r['size'] for r in rs))}",
                                                         "A — duyệt danh sách game của hệ này."],
                      a=("Duyệt", lambda s_=s_: self.open_move_games(src, s_))) for s_, rs in sorted(by.items(), key=lambda kv: kv[0].lower())]
        return [f"{len(items)} hệ máy trên {self.SD[src]}."], items

    def open_move_games(self, src, system):
        dest = "secondary" if src == "primary" else "primary"

        def build():
            rs = sorted((r for r in self._src_roms(src) if r["system"] == system), key=lambda r: r["name"].lower())
            items = [Item(r["name"], mb(r["size"]), "unknown", [
                f"File: {r['absolute_path']}", f"Dung lượng: {mb(r['size'])}", "",
                "Game nhiều file (.cue/.m3u): chọn file .cue/.m3u để chuyển trọn bộ.",
                f"A — xem kế hoạch chuyển sang {self.SD[dest]} (chưa chuyển gì)."],
                a=("Xem kế hoạch", lambda p_=r["absolute_path"]: self.plan_move_ui(p_, dest))) for r in rs]
            return [f"Hệ {system}: {len(rs)} file trên {self.SD[src]}."], items
        self.push(ListScreen(self, f"CHUYỂN GAME — {system}", build))

    def plan_move_ui(self, path, dest):
        def done(plan):
            if not plan["ok"]:
                self.info("Không thể chuyển game này", [f"!! {plan['message']}"] + [f"! {w}" for w in plan["warnings"]])
                return
            self.ask_moves([plan])
        self.run("Đang lập kế hoạch", lambda: transfer_mod.plan_move(path, self.probe.primary_path, self.probe.secondary_path, dest), done)

    # ---- LIBRARY: sửa hàng loạt, hoàn tác, BIOS
    KIND_VI = {"create_dirs": "Tạo thư mục chuẩn", "fix_cue": "Sửa tham chiếu .cue", "create_m3u": "Tạo playlist .m3u",
               "legacy_folder": "Gộp thư mục tên cũ"}
    RISK_VI = {"low": ("thấp", "green", "thấp"), "medium": ("TRUNG BÌNH", "yellow", "vừa")}   # (tên, màu, nhãn ngắn cho ô tag)

    def open_batchfix(self):
        if not self.need(self.doctor, self.inv_engine):
            return
        p, s = self.probe.primary_path, self.probe.secondary_path

        def work():
            self.inv = self.inv_engine.scan_all()
            rep = self.doctor.audit(self.inv, self.probe.get_readiness_summary()["storage"])
            save_audit_cache(self.app_data, rep)
            return rep, batchfix_mod.plan_batch_fix(self.inv, rep, p, s, self.doctor.systems_catalog)

        def done(res):
            self.report = res[0]
            self.push(ListScreen(self, "SỬA HÀNG LOẠT — kế hoạch", lambda: self._build_batchfix(res[1])))
        self.run("Đang quét & lập kế hoạch", work, done)

    def _ordered_steps(self, plan):
        return [st for k in batchfix_mod.KIND_ORDER for st in plan["steps"] if st["kind"] == k]

    def _build_batchfix(self, plan):
        steps = self._ordered_steps(plan)
        n_by = {k: sum(1 for st in steps if st["kind"] == k) for k in batchfix_mod.KIND_ORDER}
        header = [f"{len(steps)} bước: " + ", ".join(f"{self.KIND_VI[k]} x{v}" for k, v in n_by.items() if v)] if steps else [plan["summary"]]
        if not steps:
            return header, [Item("(Không có gì để sửa tự động)", head=True)]
        n_med = sum(1 for st in steps if st["risk"] != "low")
        n_irr = sum(1 for st in steps if not st["reversible"])
        items = [Item(f"Áp dụng {len(steps)} bước", f"{n_med} rủi ro TB" if n_med else "rủi ro thấp", "yellow" if n_med else "green", [
            f"Thực hiện lần lượt {len(steps)} bước bên dưới; lỗi một bước không chặn các bước khác.",
            f"Rủi ro trung bình: {n_med} bước. Không hoàn tác được: {n_irr} bước.",
            "Bước sau là hộp xác nhận (mặc định Huỷ)."], a=("Tiếp: xác nhận", lambda: self.ask_batchfix(plan)))]
        items.append(Item("Các bước (theo thứ tự thực hiện)", head=True))
        for st in steps:
            rv, rc, rt = self.RISK_VI.get(st["risk"], (st["risk"], "unknown", st["risk"]))
            ex = [self.KIND_VI.get(st["kind"], st["kind"]), "", st["detail"], "", f"Số file ảnh hưởng: {st['files_affected']}",
                  ("! " if st["risk"] != "low" else "") + f"Rủi ro: {rv}",
                  "+ Hoàn tác được: có" if st["reversible"] else "!! Hoàn tác được: KHÔNG"]
            items.append(Item(st["label"], rt, rc, ex))
        return header, items

    def ask_batchfix(self, plan):
        steps = self._ordered_steps(plan)
        lines = [f"Sẽ thực hiện {len(steps)} bước theo thứ tự:"]
        for st in steps:
            rv = self.RISK_VI.get(st["risk"], (st["risk"],))[0]
            lines.append(("! " if st["risk"] != "low" else "") + f"[{rv}] {st['label']} — " + ("hoàn tác được" if st["reversible"] else "KHÔNG hoàn tác được"))
        lines += ["", "• Sửa .cue luôn sao lưu bản gốc thành <tên>.cue.bak-loongmate trước.",
                  "• Gộp thư mục: trùng tên cùng nội dung -> Quarantine; trùng tên khác nội dung -> bỏ qua, nguồn giữ nguyên.",
                  f"• Nhật ký hoàn tác: {Path(self.app_data) / batchfix_mod.JOURNAL_NAME}",
                  "• Hoàn tác sau đó: Library > Hoàn tác lô sửa gần nhất."]
        self.confirm("Xác nhận sửa hàng loạt", lines, lambda: self.do_batchfix(plan), "Áp dụng")

    def do_batchfix(self, plan):
        def done(res):
            lines = [("+ " if res["done"] else "") + f"Đã làm {len(res['done'])} bước, từ chối {len(res['refused'])}."]
            for d in res["done"]:
                if d["kind"] == "legacy_folder":
                    lines.append(f"+ {d['label']}: chuyển {d['moved']}, trùng vào Quarantine {d['quarantined_duplicates']}, "
                                 f"bỏ qua {d['skipped']}" + ("" if d["source_folder_removed"] else " (thư mục cũ còn sót file nên được giữ)"))
                else:
                    lines.append(f"+ {d['label']}")
            lines += [f"!! TỪ CHỐI [{self.KIND_VI.get(x['kind'], x['kind'])}] {x['path']}: {x['reason']}" for x in res["refused"]]
            lines += [f"Lô: {res['batch_id']}", f"Nhật ký: {res['journal']}", "Hoàn tác: Library > Hoàn tác lô sửa gần nhất."]
            self.report = self.inv = None
            self.dup_cache.clear()
            self.dup_compare = None
            while len(self.stack) > 2:
                self.stack.pop()
            self.finish_change("Kết quả sửa hàng loạt", lines, bool(res["done"]))
            self.refresh_cur()
        self.run("Đang sửa hàng loạt", lambda: batchfix_mod.execute_batch_fix(plan, self.app_data), done)

    def _last_batch(self):
        """(batch_id, số thao tác) của lô gần nhất chưa hoàn tác — cùng luật chọn với undo_batch_fix."""
        if not self.app_data:
            return None
        ents = batchfix_mod._read_journal(Path(self.app_data) / batchfix_mod.JOURNAL_NAME)
        undone = {e["batch_id"] for e in ents if e.get("op") == "undone"}
        ids = [e["batch_id"] for e in ents if e.get("op") != "undone" and e["batch_id"] not in undone]
        return (ids[-1], sum(1 for e in ents if e["batch_id"] == ids[-1])) if ids else None

    def _undo_item(self):
        last = self._last_batch()
        if last is None:
            return Item("Hoàn tác lô sửa gần nhất", "không có lô", "unknown", ["Chưa có lô sửa hàng loạt nào để hoàn tác (hoặc đã hoàn tác hết)."])
        return Item("Hoàn tác lô sửa gần nhất", "có 1 lô", "yellow", [
            f"Lô: {last[0]} ({last[1]} thao tác trong nhật ký).",
            "Hoàn tác theo thứ tự ngược, dựa vào trạng thái THẬT trên đĩa chứ không tin mù quáng vào nhật ký.",
            "Việc nào không hoàn tác an toàn được (đường dẫn cũ đã bị chiếm, file đã bị sửa...) sẽ bị bỏ qua và nêu lý do."],
            a=("Hoàn tác", lambda: self.ask_undo(last)))

    def ask_undo(self, last):
        self.confirm("Hoàn tác lô sửa hàng loạt", [
            f"Sẽ hoàn tác lô {last[0]} ({last[1]} thao tác).", "• Theo thứ tự ngược; trạng thái thật trên đĩa quyết định.",
            "• File đã bị sửa sau lô đó, hoặc đường dẫn cũ đã bị chiếm: BỎ QUA, không ghi đè.",
            "• Kết quả hiện từng nhóm: đã hoàn tác / bỏ qua kèm lý do."], self.do_undo, "Hoàn tác")

    def do_undo(self):
        def done(r):
            if r["batch_id"] is None:
                self.info("Hoàn tác lô sửa", ["Không có lô nào để hoàn tác."])
                return
            lines = [("+ " if r["undone"] else "") + f"Lô {r['batch_id']}: đã hoàn tác {len(r['undone'])} thao tác, bỏ qua {len(r['skipped'])}."]
            lines += [f"+ {u['op']}: {u['path']}" for u in r["undone"]]
            lines += [f"! BỎ QUA {x['op']} {x['path']}: {x['reason']}" for x in r["skipped"]]
            self.report = self.inv = None
            self.dup_cache.clear()
            self.dup_compare = None
            self.finish_change("Kết quả hoàn tác", lines, bool(r["undone"]))
            self.refresh_cur()
        self.run("Đang hoàn tác", lambda: batchfix_mod.undo_batch_fix(Path(self.app_data) / batchfix_mod.JOURNAL_NAME), done)

    BIOS_VI = {"ok": ("OK", "green", "md5 khớp nguồn công khai."), "wrong_hash": ("SAI HASH", "red", "Có file nhưng md5 không nằm trong danh sách hợp lệ."),
               "missing": ("THIẾU", "yellow", "Không thấy trong BIOS/ của thẻ nào."),
               "unknown_hash": ("chưa có hash", "unknown", "Chưa có md5 nguồn công khai nên chỉ biết là file có tồn tại.")}

    def open_bios(self):
        if not self.need(self.doctor, self.inv_engine):
            return
        p, s = self.probe.primary_path, self.probe.secondary_path

        def work():
            self.inv = self.inv_engine.scan_all()
            active = {r["system"] for r in self._roms()}
            return bios_mod.check_bios(p, s, self.doctor.systems_catalog, active), bios_mod.load_hashes()
        self.run("Đang kiểm tra BIOS", work, lambda res: self.push(ListScreen(self, "KIỂM TRA BIOS", lambda: self._build_bios(*res))))

    def _build_bios(self, rep, hashes):
        cnt = {k: 0 for k in self.BIOS_VI}
        cnt["optional_missing"] = 0
        items: List[Item] = []
        for sy in rep["systems"]:
            items.append(Item(sy["system"], head=True))
            for e in sy["required"]:
                tag, color, meaning = self.BIOS_VI[e["status"]]
                opt = bool(e.get("optional"))
                if opt and e["status"] == "missing":    # thiếu file không bắt buộc KHÔNG phải lỗi
                    cnt["optional_missing"] += 1
                    tag, color, meaning = "không bắt buộc", "unknown", "file này không bắt buộc nên thiếu không phải lỗi."
                    stat = "THIẾU (không bắt buộc)"
                else:
                    cnt[e["status"]] += 1
                    stat = tag
                ent = hashes.get(e["name"].lower())
                ex = [f"Hệ máy: {sy['system']}", f"File: {e['name']}", ("!! " if color == "red" else "! " if color == "yellow" else "") + f"Trạng thái: {stat} — {meaning}"]
                if opt and e["status"] != "missing":
                    ex.append("Không bắt buộc.")
                if e["found_path"]:
                    ex += [f"Đường dẫn: {e['found_path']}", f"md5 hiện có: {e['md5'] or '(không băm được)'}"]
                ex.append(("md5 mong đợi: " + " hoặc ".join(ent["md5"]) + f" (nguồn: {ent.get('source')})") if ent and ent["md5"]
                          else "md5 mong đợi: chưa có từ nguồn công khai cho file này.")
                ex += ["", e["note"], "", bios_mod.NO_DOWNLOAD]
                items.append(Item(e["name"], tag, color, ex))
        if not items:
            items.append(Item("(Không hệ máy nào đang có ROM cần BIOS)", head=True))
        header = [f"{len(rep['systems'])} hệ máy cần BIOS • ok {cnt['ok']} • sai hash {cnt['wrong_hash']} • thiếu {cnt['missing']} • "
                  f"chưa có hash nguồn {cnt['unknown_hash']} • thiếu nhưng không bắt buộc {cnt['optional_missing']}", bios_mod.NO_DOWNLOAD]
        return header, items


def fmt_mac(mac: str) -> str:
    return ":".join(mac[i:i + 2] for i in range(0, len(mac), 2)) if len(mac) == 12 else mac
