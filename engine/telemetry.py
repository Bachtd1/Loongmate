"""
Lich su pin / nhiet do cho LoongMate (Pocket 1, Leaf CFW).

API:
    sample(probe, thermal_root=..., cpufreq_path=..., now=None) -> dict
        Mot mau: {"ts", "battery_pct", "status", "soc_c", "gpu_c", "batt_c", "cpu_khz",
                  "charge_full_uah", "current_ua", "voltage_uv", "health"}.
        Bon cot cuoi doc tu /sys/class/power_supply/<pin> (power_root; pin = muc co type "Battery"):
        charge_full (uAh), current_now (uA), voltage_now (uV), health (chuoi cua driver).
        Cam bien khong doc duoc -> None (KHONG dien so). `probe` can co
        get_battery_info() (PlatformProbe hoac doi tuong gia lap).
    append(app_data, row) -> None
        Ghi them 1 dong vao <app_data>/telemetry/YYYY-MM.csv (thang theo gio
        dia phuong cua row["ts"]). Mot lan write() + flush + fsync.
    load(app_data, since_ts=None) -> list[dict]
        Doc lai cac mau (da ep kieu, o trong -> None), sap theo ts. Dong hong
        (vd. dong cuoi bi cat do mat dien) bi bo qua, khong lam hong ca file.
    summarize(rows, max_gap_s=1800, min_discharge_h=0.5) -> dict
        drain_pct_per_hour: chi tinh tren cac cap mau LIEN TIEP deu dang
          "Discharging", cach nhau <= max_gap_s (may ngu/tat thi khong tinh),
          pin khong tang. None neu tong thoi gian xa < min_discharge_h.
        temp: {"soc"|"gpu"|"batt": {"max","avg"}}; over_70_events = so lan
          nhiet cao nhat trong mau vuot 70 (dem moi lan VUOT LEN, khong dem
          moi mau); over_70_samples = so mau > 70.
    battery_health(rows, design_mah=4000) -> dict
        UOC LUONG suc khoe pin tu lich su charge_full (xem docstring ham). CSV cu khong co cot moi van doc duoc.
    prune(app_data, keep_days=90, now=None) -> list[str]
        Xoa file thang nam HOAN TOAN truoc moc (now - keep_days). Do hat la
        theo thang nen thuc te giu 90..120 ngay. Tra ve ten file da xoa.

Dung luong: mot dong ~50 byte, 1 mau / 5 phut, 90 ngay ~ 1.3 MB.
"""

import csv
import io
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

COLUMNS = ["ts", "battery_pct", "status", "soc_c", "gpu_c", "batt_c", "cpu_khz",
           "charge_full_uah", "current_ua", "voltage_uv", "health"]
LEGACY_COLUMNS = 7   # CSV cu chi co 7 cot dau; dong cu va moi co the nam chung mot file thang

DEFAULT_THERMAL_ROOT = Path("/sys/class/thermal")
DEFAULT_POWER_ROOT = Path("/sys/class/power_supply")
DEFAULT_CPUFREQ = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")

# Ten loai vung nhiet that tren Pocket 1 (da doc tren may 2026-10-02):
# thermal_zone0=soc-thermal, thermal_zone1=gpu-thermal, thermal_zone2=battery.
# Khop theo file `type`, khong theo so thu tu, de khong dua vao chi so.
_ZONE_KEYS = (("soc", "soc_c"), ("gpu", "gpu_c"), ("battery", "batt_c"))


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _read_zone_temps(thermal_root: Path) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {k: None for _, k in _ZONE_KEYS}
    try:
        zones = sorted(Path(thermal_root).glob("thermal_zone*"))
    except OSError:
        return out
    for zone in zones:
        ztype = (_read_text(zone / "type") or "").lower()
        raw = _read_text(zone / "temp")
        if not ztype or raw is None:
            continue
        try:
            val = int(raw)
        except ValueError:
            continue
        celsius = round(val / 1000.0 if abs(val) > 200 else float(val), 1)
        for needle, key in _ZONE_KEYS:
            if needle in ztype and out[key] is None:
                out[key] = celsius
                break
    return out


def _read_int(path: Path) -> Optional[int]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_battery_extra(power_root: Path) -> Dict[str, Any]:
    """charge_full / current_now / voltage_now / health cua muc pin dau tien (type == Battery)."""
    out: Dict[str, Any] = {"charge_full_uah": None, "current_ua": None, "voltage_uv": None, "health": None}
    try:
        supplies = sorted(Path(power_root).iterdir())
    except OSError:
        return out
    for sup in supplies:
        if "battery" not in (_read_text(sup / "type") or "").lower():
            continue
        out["charge_full_uah"] = _read_int(sup / "charge_full")
        out["current_ua"] = _read_int(sup / "current_now")
        out["voltage_uv"] = _read_int(sup / "voltage_now")
        out["health"] = _read_text(sup / "health") or None
        break
    return out


def sample(probe: Any, thermal_root: Any = DEFAULT_THERMAL_ROOT, cpufreq_path: Any = DEFAULT_CPUFREQ,
           now: Optional[float] = None, power_root: Any = DEFAULT_POWER_ROOT) -> Dict[str, Any]:
    bat: Dict[str, Any] = {}
    try:
        bat = probe.get_battery_info() or {}
    except Exception:
        bat = {}
    pct = bat.get("percent") if bat.get("available") else None
    status = bat.get("status") if bat.get("available") else None

    row: Dict[str, Any] = {
        "ts": int(now if now is not None else time.time()),
        "battery_pct": pct,
        "status": status,
    }
    row.update(_read_zone_temps(Path(thermal_root)))

    khz: Optional[int] = None
    raw = _read_text(Path(cpufreq_path))
    if raw is not None:
        try:
            khz = int(raw)
        except ValueError:
            khz = None
    row["cpu_khz"] = khz
    row.update(_read_battery_extra(Path(power_root)))
    return row


def _month_file(app_data: Any, ts: float) -> Path:
    return Path(app_data) / "telemetry" / (time.strftime("%Y-%m", time.localtime(ts)) + ".csv")


def append(app_data: Any, row: Dict[str, Any]) -> None:
    path = _month_file(app_data, row["ts"])
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if not path.exists() or path.stat().st_size == 0:
        w.writerow(COLUMNS)
    w.writerow(["" if row.get(c) is None else row[c] for c in COLUMNS])
    data = buf.getvalue().encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _to_num(text: str, kind: type) -> Any:
    if text == "":
        return None
    try:
        return kind(float(text)) if kind is int else float(text)
    except ValueError:
        return None


def _parse_row(rec: List[str]) -> Optional[Dict[str, Any]]:
    if len(rec) not in (LEGACY_COLUMNS, len(COLUMNS)):
        return None
    ts = _to_num(rec[0], int)
    if ts is None:
        return None
    rec = rec + [""] * (len(COLUMNS) - len(rec))   # dong cu: cot moi de trong -> None
    return {
        "ts": ts,
        "battery_pct": _to_num(rec[1], int),
        "status": rec[2] or None,
        "soc_c": _to_num(rec[3], float),
        "gpu_c": _to_num(rec[4], float),
        "batt_c": _to_num(rec[5], float),
        "cpu_khz": _to_num(rec[6], int),
        "charge_full_uah": _to_num(rec[7], int),
        "current_ua": _to_num(rec[8], int),
        "voltage_uv": _to_num(rec[9], int),
        "health": rec[10] or None,
    }


def load(app_data: Any, since_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    tdir = Path(app_data) / "telemetry"
    rows: List[Dict[str, Any]] = []
    if not tdir.is_dir():
        return rows
    for f in sorted(tdir.glob("????-??.csv")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rec in csv.reader(io.StringIO(text)):
            if not rec or rec[0] == "ts":
                continue
            r = _parse_row(rec)
            if r is None:
                continue
            if since_ts is not None and r["ts"] < since_ts:
                continue
            rows.append(r)
    rows.sort(key=lambda r: r["ts"])
    return rows


def summarize(rows: List[Dict[str, Any]], max_gap_s: float = 1800, min_discharge_h: float = 0.5) -> Dict[str, Any]:
    rows = sorted(rows, key=lambda r: r["ts"])
    drop_total = 0.0
    secs_total = 0.0
    for a, b in zip(rows, rows[1:]):
        if a.get("battery_pct") is None or b.get("battery_pct") is None:
            continue
        if (a.get("status") or "").lower() != "discharging" or (b.get("status") or "").lower() != "discharging":
            continue
        dt = b["ts"] - a["ts"]
        if dt <= 0 or dt > max_gap_s:
            continue
        drop = a["battery_pct"] - b["battery_pct"]
        if drop < 0:
            continue
        drop_total += drop
        secs_total += dt
    drain: Optional[float] = None
    if secs_total / 3600.0 >= min_discharge_h:
        drain = round(drop_total / (secs_total / 3600.0), 2)

    temp: Dict[str, Optional[Dict[str, float]]] = {}
    for name, key in (("soc", "soc_c"), ("gpu", "gpu_c"), ("batt", "batt_c")):
        vals = [r[key] for r in rows if r.get(key) is not None]
        temp[name] = {"max": max(vals), "avg": round(sum(vals) / len(vals), 1)} if vals else None

    events = 0
    over_samples = 0
    was_over = False
    for r in rows:
        peak = max((r[k] for k in ("soc_c", "gpu_c", "batt_c") if r.get(k) is not None), default=None)
        is_over = peak is not None and peak > 70
        if is_over:
            over_samples += 1
            if not was_over:
                events += 1
        was_over = is_over

    return {
        "samples": len(rows),
        "first_ts": rows[0]["ts"] if rows else None,
        "last_ts": rows[-1]["ts"] if rows else None,
        "drain_pct_per_hour": drain,
        "discharge_hours": round(secs_total / 3600.0, 2),
        "temp": temp,
        "over_70_events": events,
        "over_70_samples": over_samples,
    }


DESIGN_MAH_POCKET1 = 4000
DESIGN_SOURCE_POCKET1 = "thông số công khai Pocket 1 (4000 mAh) — driver không cung cấp charge_full_design"
MIN_TREND_DAYS = 7
_ESTIMATE_NOTE = ("Đây là ƯỚC LƯỢNG: charge_full do bộ đo pin (fuel gauge) tự học nên có thể lệch, và dung lượng "
                  "thiết kế lấy từ thông số công khai chứ không đọc được từ máy.")


def battery_health(rows: List[Dict[str, Any]], design_mah: float = DESIGN_MAH_POCKET1) -> Dict[str, Any]:
    """UOC LUONG suc khoe pin tu lich su charge_full (uAh) trong `rows` (ket qua load()).

    charge_full_mah  : charge_full cua mau moi nhat co du lieu, doi sang mAh
    estimated_pct    : charge_full_mah / design_mah * 100 (khong cat o 100; fuel gauge co the vuot nhe)
    driver_health    : chuoi `health` moi nhat driver bao (vd. "Good"), None neu khong co
    trend_mah_per_30d: do doc hoi quy tuyen tinh (binh phuong toi thieu) charge_full theo ngay, x30.
                       Can cac mau trai >= 7 ngay; khong du -> None va "reason" giai thich.
    Khong co mau charge_full nao -> available=False (khong bia so)."""
    design_source = (DESIGN_SOURCE_POCKET1 if design_mah == DESIGN_MAH_POCKET1
                     else f"do người gọi cung cấp ({design_mah:g} mAh) — driver không cung cấp charge_full_design")
    out: Dict[str, Any] = {"available": False, "charge_full_mah": None, "design_mah": design_mah,
                           "design_source": design_source, "estimated_pct": None, "driver_health": None,
                           "trend_mah_per_30d": None, "samples": 0, "reason": ""}
    rows = sorted(rows or [], key=lambda r: r["ts"])
    pts = [(r["ts"], r["charge_full_uah"] / 1000.0) for r in rows
           if r.get("charge_full_uah") is not None and r["charge_full_uah"] > 0]
    out["samples"] = len(pts)
    healths = [r["health"] for r in rows if r.get("health")]
    out["driver_health"] = healths[-1] if healths else None
    if not pts:
        out["reason"] = "Chưa có mẫu charge_full nào (máy không cung cấp hoặc agent chưa ghi). " + _ESTIMATE_NOTE
        return out
    out["available"] = True
    out["charge_full_mah"] = round(pts[-1][1], 1)
    if design_mah and design_mah > 0:
        out["estimated_pct"] = round(pts[-1][1] / design_mah * 100.0, 1)
    span_days = (pts[-1][0] - pts[0][0]) / 86400.0
    if span_days < MIN_TREND_DAYS:
        out["reason"] = (f"Chưa đủ dữ liệu để tính xu hướng: cần các mẫu trải ít nhất {MIN_TREND_DAYS} ngày, "
                         f"hiện có {span_days:.1f} ngày. " + _ESTIMATE_NOTE)
        return out
    n = len(pts)
    xs = [(t - pts[0][0]) / 86400.0 for t, _ in pts]
    mx, my = sum(xs) / n, sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, (_, y) in zip(xs, pts)) / sxx   # mAh / ngay
    out["trend_mah_per_30d"] = round(slope * 30.0, 2)
    out["reason"] = f"Xu hướng tính từ {n} mẫu trải {span_days:.1f} ngày. " + _ESTIMATE_NOTE
    return out


def prune(app_data: Any, keep_days: int = 90, now: Optional[float] = None) -> List[str]:
    tdir = Path(app_data) / "telemetry"
    removed: List[str] = []
    if not tdir.is_dir():
        return removed
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    for f in sorted(tdir.glob("????-??.csv")):
        try:
            year, month = int(f.name[0:4]), int(f.name[5:7])
        except ValueError:
            continue
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        month_end = time.mktime((ny, nm, 1, 0, 0, 0, 0, 0, -1))
        if month_end <= cutoff:
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                pass
    return removed
