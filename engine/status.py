"""
Trạng thái "bản sao ngoài máy" (offsite.json) — tách khỏi ui_screens để CLI dùng mà không phải nạp cả
bộ màn hình (ui_screens kéo agent, nodes, transfer... ~50 ms khởi động trên máy). ui_screens re-export
nguyên các tên này nên chỗ đang `ui.offsite_status` vẫn chạy.
"""
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------- bản sao ngoài máy
OFFSITE_NAME = "offsite.json"      # do PC ghi sau mỗi lần kéo thành công (pc/pull_saves.py)
OFFSITE_STALE_DAYS = 3             # cũ hơn: vàng
OFFSITE_OLD_DAYS = 14              # cũ hơn: đỏ
OFFSITE_MISSING = "Chưa có bản sao ngoài máy — cài lịch kéo về PC (pc/install_schedule.ps1)"
_COLOR_RANK = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}
_COLOR_PREFIX = {"green": "+ ", "unknown": "", "yellow": "! ", "red": "!! "}


def offsite_status(app_data, now: Optional[float] = None) -> Dict[str, Any]:
    """Đọc <app_data>/offsite.json và chấm mức cảnh báo theo tuổi lần kéo gần nhất. CHỈ ĐỌC.
    level: missing | invalid | future | ok | stale | old.  color: green/yellow/red/unknown.
    verified không phải True (kể cả thiếu khoá) thì tối thiểu vàng. `line` là dòng sẵn để hiện
    (có tiền tố !! / ! / + cho line_color)."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"level": "missing", "color": "yellow", "age_days": None, "verified": None,
                           "last_pull_at": None, "pc_host": None, "snapshot_ids": [], "objects": None,
                           "message": OFFSITE_MISSING}

    def done(level, color, msg):
        out.update(level=level, color=color, message=msg)
        out["line"] = _COLOR_PREFIX[color] + msg
        return out

    if not app_data:
        return done("invalid", "unknown", "Không xác định được thư mục dữ liệu LoongMate nên không kiểm tra được bản sao ngoài máy.")
    path = Path(app_data) / OFFSITE_NAME
    if not path.exists():
        return done("missing", "yellow", OFFSITE_MISSING)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("không phải đối tượng JSON")
        ts = d.get("last_pull_ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise ValueError("thiếu last_pull_ts dạng số")
    except (OSError, ValueError) as e:
        return done("invalid", "yellow", f"{OFFSITE_NAME} không đọc được ({e}) — kiểm tra lịch kéo về PC")
    out.update(last_pull_at=d.get("last_pull_at"), pc_host=d.get("pc_host"), snapshot_ids=list(d.get("snapshot_ids") or []),
               objects=d.get("objects"), verified=d.get("verified") is True)
    age = (now - ts) / 86400
    out["age_days"] = int(age) if age >= 0 else None
    if age < -0.04:   # lệch quá ~1 giờ: đồng hồ máy (hoặc PC) sai, không tính được tuổi
        level, color, msg = "future", "yellow", "Bản sao ngoài máy: giờ lần kéo nằm SAU giờ máy — đồng hồ lệch, không tính được tuổi"
    else:
        n = max(0, int(age))
        level = "old" if age > OFFSITE_OLD_DAYS else ("stale" if age > OFFSITE_STALE_DAYS else "ok")
        color = {"old": "red", "stale": "yellow", "ok": "green"}[level]
        msg = f"Bản sao ngoài máy: {n} ngày trước" if n else "Bản sao ngoài máy: hôm nay"
    if not out["verified"]:
        msg += " — CHƯA xác minh"
        if _COLOR_RANK[color] < _COLOR_RANK["yellow"]:
            color = "yellow"
    return done(level, color, msg)
