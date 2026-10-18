"""
Local API & Web Server for LoongMate Handheld Simulator.
Connects the interactive 960x720 UI directly to the real LoongMate engine modules.
"""

import sys
import os
import json
import time
import shutil
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse
from typing import Dict, List, Any, Optional

# Set project root in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.probe import PlatformProbe
from engine.inventory import InventoryEngine
from engine.doctor import LibraryDoctor
from engine.savevault import SaveVault
from engine.health import DeviceHealth

# Setup default mock playground directories if not present.
# LOONGMATE_SIM_PLAYGROUND lets callers (tests!) redirect the whole playground to an
# isolated temp directory instead of writing into the project's own playground/.
_playground_override = os.environ.get("LOONGMATE_SIM_PLAYGROUND")
PLAYGROUND = Path(_playground_override).resolve() if _playground_override else PROJECT_ROOT / "playground"
SD1_DIR = PLAYGROUND / "sd1"
SD2_DIR = PLAYGROUND / "sd2"
USERDATA_DIR = PLAYGROUND / "userdata"

def init_playground(reset: bool = False):
    """Initializes realistic test data for the simulator.

    Only creates files that are missing: a file that already exists (e.g. a save the
    user modified, or a snapshot they took) is never overwritten. This used to run at
    import time and unconditionally rewrite Saves/Pokemon Emerald.srm to fixed bytes on
    every startup, silently destroying whatever the user had done in a previous session.

    reset=True (the --reset-playground CLI flag) deliberately wipes sd1/sd2 first to
    rebuild the sample library from scratch.
    """
    if reset:
        for d in (SD1_DIR, SD2_DIR):
            if d.exists():
                shutil.rmtree(d)

    for folder in ["Roms/GBA", "Roms/PS", "Images/GBA", "Images/PS", "Saves", "States", "BIOS"]:
        (SD1_DIR / folder).mkdir(parents=True, exist_ok=True)
        (SD2_DIR / folder).mkdir(parents=True, exist_ok=True)
    USERDATA_DIR.mkdir(parents=True, exist_ok=True)

    def ensure_bytes(p: Path, data: bytes):
        if not p.exists():
            p.write_bytes(data)

    def ensure_text(p: Path, text: str):
        if not p.exists():
            p.write_text(text, encoding="utf-8")

    # Sample ROMs and artwork
    ensure_bytes(SD1_DIR / "Roms/GBA/Pokemon Emerald.gba", b"GBA_POKEMON_ROM_DATA")
    ensure_bytes(SD1_DIR / "Images/GBA/Pokemon Emerald.png", b"IMAGE_PNG_DATA")
    ensure_bytes(SD1_DIR / "Saves/Pokemon Emerald.srm", b"POKEMON_SAVE_DATA_LEVEL_25")

    # Sample multi-disc without m3u
    ensure_bytes(SD1_DIR / "Roms/PS/Final Fantasy VII (Disc 1).chd", b"FF7_DISC1")
    ensure_bytes(SD1_DIR / "Roms/PS/Final Fantasy VII (Disc 2).chd", b"FF7_DISC2")
    ensure_bytes(SD1_DIR / "Roms/PS/Final Fantasy VII (Disc 3).chd", b"FF7_DISC3")

    # Sample broken CUE file
    ensure_text(
        SD1_DIR / "Roms/PS/Ridge Racer Type 4.cue",
        'FILE "Ridge Racer Type 4 (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
    )

    # Sample orphan artwork
    ensure_bytes(SD1_DIR / "Images/GBA/Super Mario Advance 4.png", b"ORPHAN_ART_DATA")

# Hardware simulation overrides
sim_state = {
    "battery_percent": 88,
    "is_charging": False,
    "temperature_c": 41.5,
    "sd2_mounted": True,
    "sd2_filesystem": "vfat", # "vfat", "exfat", "ntfs"
    "sd2_writable": True
}

catalog_path = PROJECT_ROOT / "systems.json"

# In-memory cache for the (expensive) full inventory scan + doctor audit, so
# /api/readiness can answer in well under 2 seconds from cache instead of re-walking
# the whole library on every poll. /api/audit always refreshes it. Any endpoint that
# changes files on disk (fix/backup/restore/rollback/override_sim) invalidates it, so
# a stale report is never shown silently as if it were current -- audit_age_seconds in
# the readiness response tells the UI exactly how old the cached numbers are.
_audit_cache: Dict[str, Any] = {"report": None, "ts": None}

def _refresh_audit_cache(probe: "PlatformProbe", inv_engine: InventoryEngine, doctor: LibraryDoctor) -> Dict[str, Any]:
    storage = probe.get_readiness_summary()["storage"]
    inv = inv_engine.scan_all()
    report = doctor.audit(inv, storage)
    _audit_cache["report"] = report
    _audit_cache["ts"] = time.time()
    return report

def _get_cached_audit(probe: "PlatformProbe", inv_engine: InventoryEngine, doctor: LibraryDoctor) -> Dict[str, Any]:
    if _audit_cache["report"] is None:
        return _refresh_audit_cache(probe, inv_engine, doctor)
    return _audit_cache["report"]

def _invalidate_audit_cache():
    _audit_cache["report"] = None
    _audit_cache["ts"] = None

def _apply_sim_overrides(probe: PlatformProbe) -> None:
    """Patches the probe instance's raw-data readers (battery/thermal/storage) with the
    simulator's values, then lets PlatformProbe's own get_readiness_summary() compute
    status from those values exactly like it would on real hardware.

    This deliberately does NOT re-implement the red/yellow/green thresholds here: an
    earlier version of this file duplicated them (checking temperature_c >= 58 for
    "yellow") while engine/probe.py used >= 60, so the simulator and the engine
    disagreed about when a card/device counted as warm. Overriding the raw sysfs-shaped
    data instead of the derived status means there is exactly one place (engine/probe.py)
    that decides what counts as a warning.
    """
    def battery_override():
        # available=True is required: on this dev machine PlatformProbe's real sysfs
        # read returns available=False/percent=None, and get_readiness_summary() would
        # then report "unknown" battery status no matter where the simulator slider is.
        return {
            "available": True,
            "percent": sim_state["battery_percent"],
            "status": "Charging" if sim_state["is_charging"] else "Discharging",
            "is_charging": sim_state["is_charging"],
            "device": "sim_battery",
            "reason": None
        }

    def thermal_override():
        t = sim_state["temperature_c"]
        return {
            "available": True,
            "current_celsius": t,
            "max_celsius": t,
            "zone": "sim_thermal_zone0",
            "trend": "stable" if t < 55 else "warm" if t < 70 else "hot",
            "reason": None
        }

    probe.get_battery_info = battery_override
    probe.get_thermal_info = thermal_override

    if probe.secondary_path is not None:
        real_get_storage_info = probe.get_storage_info

        def storage_override(path):
            result = real_get_storage_info(path)
            if path == probe.secondary_path:
                # fs_detected=True is required too: engine/doctor.py only raises
                # SEC_FS_NOT_FAT32 when fs_detected is True. Leaving it False (the real
                # value on a dev machine with no /proc/mounts) makes doctor.py emit the
                # weaker SEC_FS_UNKNOWN info notice instead, even though the user just
                # explicitly chose "exFAT" in the simulator dropdown.
                result["filesystem"] = sim_state["sd2_filesystem"]
                result["fs_detected"] = True
                result["is_fat32"] = (sim_state["sd2_filesystem"] == "vfat")
                result["writable"] = sim_state["sd2_writable"]
                result["read_only_mount"] = not sim_state["sd2_writable"]
            return result

        probe.get_storage_info = storage_override

def _make_probe() -> PlatformProbe:
    probe = PlatformProbe(str(SD1_DIR), str(SD2_DIR) if sim_state["sd2_mounted"] else None, str(USERDATA_DIR))
    _apply_sim_overrides(probe)
    return probe

class SimulatorHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT / "simulator"), **kwargs)

    # --- Security gates for /api/* ---------------------------------------------------
    # These exist because /api/fix and friends are primitives that move/write files on
    # the host. Without them, any webpage open in the same browser could POST to this
    # server (simple CORS requests don't need a preflight) and the response would be
    # unreadable cross-origin, but the file-system side effect would already have
    # happened. See the module docstring in engine/doctor.py for the destination-side
    # allow-list; this is the matching front door.

    def _allowed_hosts(self) -> set:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _check_host_header(self) -> Optional[str]:
        """Blocks DNS-rebinding: an attacker domain that resolves to 127.0.0.1 still
        sends its own hostname in the Host header, which this rejects."""
        host = self.headers.get("Host", "")
        if host not in self._allowed_hosts():
            port = self.server.server_address[1]
            return (f"Header Host '{host}' không hợp lệ. Server chỉ phục vụ "
                    f"127.0.0.1:{port} hoặc localhost:{port}. Hãy mở simulator đúng địa "
                    f"chỉ đó thay vì qua một tên miền khác trỏ về cùng IP.")
        return None

    def _check_origin_header(self) -> Optional[str]:
        """If the browser sent Origin/Referer (it always does for a cross-origin POST),
        it must point at this same server. Combined with _check_content_type, this is
        what actually stops another website's JS from driving /api/fix: a browser will
        not let a cross-origin fetch send Content-Type: application/json without a
        preflight, and this server never answers OPTIONS preflights, so the cross-origin
        request never leaves the browser with that header -- it falls back to a
        simple-request Content-Type like text/plain, which _check_content_type rejects."""
        allowed = self._allowed_hosts()
        for header_name in ("Origin", "Referer"):
            value = self.headers.get(header_name)
            if not value:
                continue
            netloc = urllib.parse.urlparse(value).netloc
            if netloc not in allowed:
                return (f"Header {header_name} '{value}' không khớp với server này "
                        f"(127.0.0.1/localhost). Yêu cầu bị từ chối để chống tấn công "
                        f"CSRF từ trang web khác.")
        return None

    def _check_content_type(self) -> Optional[str]:
        ct = self.headers.get("Content-Type", "")
        if not ct.lower().startswith("application/json"):
            return (f"Content-Type phải là 'application/json' (hiện tại: "
                    f"'{ct or '(không có)'}'). Hãy gửi header "
                    f"'Content-Type: application/json' kèm body JSON.")
        return None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            host_err = self._check_host_header()
            if host_err:
                self.send_json({"success": False, "message": host_err}, 403)
                return
            origin_err = self._check_origin_header()
            if origin_err:
                self.send_json({"success": False, "message": origin_err}, 403)
                return
            self.handle_api_get(path, urllib.parse.parse_qs(parsed.query))
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not path.startswith("/api/"):
            self.send_error(404, "Not Found")
            return

        # Đọc hết body TRƯỚC khi kiểm tra/từ chối: nếu trả 403 rồi đóng kết nối khi body còn nằm
        # chưa đọc trong bộ đệm nhận, TCP gửi RST và client gặp ConnectionReset/Aborted thay vì
        # thấy 403 (lỗi chập chờn trên Windows). Việc đọc không làm gì với nội dung.
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len) if content_len > 0 else b""

        host_err = self._check_host_header()
        if host_err:
            self.send_json({"success": False, "message": host_err}, 403)
            return
        ct_err = self._check_content_type()
        if ct_err:
            self.send_json({"success": False, "message": ct_err}, 403)
            return
        origin_err = self._check_origin_header()
        if origin_err:
            self.send_json({"success": False, "message": origin_err}, 403)
            return

        try:
            data = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        except Exception:
            data = {}

        self.handle_api_post(path, data)

    def send_json(self, obj: Any, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # No Access-Control-Allow-Origin header: this server only serves its own UI at
        # 127.0.0.1/localhost, loaded same-origin. Access-Control-Allow-Origin: * used to
        # let ANY webpage read these responses cross-origin (and, combined with simple
        # cross-origin POSTs never needing a preflight, let any page trigger /api/fix's
        # file-system side effects too). Same-origin-by-default is enough here.
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, path: str, query: Dict[str, Any]):
        probe = _make_probe()
        inv_engine = InventoryEngine(probe.primary_path, probe.secondary_path)
        doctor = LibraryDoctor(probe.primary_path, probe.secondary_path, catalog_path, probe.userdata_path)
        vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)
        health = DeviceHealth(probe.primary_path, probe.secondary_path, probe.userdata_path)

        if path == "/api/readiness":
            summary = probe.get_readiness_summary()

            # SaveVault info
            snaps = vault.list_snapshots()
            summary["savevault"] = {
                "total_snapshots": len(snaps),
                "latest": snaps[0] if snaps else None
            }

            # Doctor info preview: served from cache so Home answers in well under 2s;
            # /api/audit (and any file-changing POST) is what refreshes this cache.
            report = _get_cached_audit(probe, inv_engine, doctor)
            audit_age = round(time.time() - _audit_cache["ts"], 1) if _audit_cache["ts"] else 0.0
            summary["library"] = {
                "total_issues": report["total_issues"],
                "critical": report["severity_counts"]["critical"],
                "warning": report["severity_counts"]["warning"],
                "info": report["severity_counts"]["info"],
                "status": report["status"],
                "catalog_version": report["catalog_version"],
                "audit_age_seconds": audit_age
            }

            self.send_json(summary)

        elif path == "/api/audit":
            report = _refresh_audit_cache(probe, inv_engine, doctor)
            self.send_json(report)

        elif path == "/api/snapshots":
            snaps = vault.list_snapshots()
            self.send_json({"snapshots": snaps})

        elif path == "/api/crash-recovery":
            status = vault.check_crash_recovery()
            self.send_json(status if status is not None else {"needs_recovery": False})

        elif path == "/api/health":
            storage = probe.get_readiness_summary()["storage"]
            diag = health.run_diagnostics(storage=storage)
            self.send_json(diag)

        elif path == "/api/bundle":
            MAX_BUNDLE_BYTES = 50 * 1024 * 1024
            try:
                readiness = probe.get_readiness_summary()
                inv = inv_engine.scan_all()
                report = doctor.audit(inv, readiness["storage"])
                bundle_path = health.export_support_bundle(readiness, report, privacy_mode=True)
                size = Path(bundle_path).stat().st_size
                if size > MAX_BUNDLE_BYTES:
                    self.send_json({
                        "success": False,
                        "message": (f"Gói chẩn đoán quá lớn ({round(size / (1024 * 1024), 1)}MB, "
                                    f"giới hạn {MAX_BUNDLE_BYTES // (1024 * 1024)}MB). Hãy dọn bớt "
                                    f"log trong $USERDATA_PATH/logs rồi thử lại.")
                    }, 500)
                    return
                with open(bundle_path, "rb") as f:
                    content = f.read()
            except Exception as e:
                self.send_json({
                    "success": False,
                    "message": f"Lỗi khi tạo gói chẩn đoán: {e}. Thử lại hoặc kiểm tra dung lượng trống."
                }, 500)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename={Path(bundle_path).name}")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        else:
            self.send_error(404, "Not Found")

    def handle_api_post(self, path: str, data: Dict[str, Any]):
        probe = _make_probe()
        inv_engine = InventoryEngine(probe.primary_path, probe.secondary_path)
        doctor = LibraryDoctor(probe.primary_path, probe.secondary_path, catalog_path, probe.userdata_path)
        vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)

        if path == "/api/override_sim":
            for k in ["battery_percent", "is_charging", "temperature_c", "sd2_mounted", "sd2_filesystem", "sd2_writable"]:
                if k in data:
                    sim_state[k] = data[k]
            _invalidate_audit_cache()
            self.send_json({"success": True, "state": sim_state})

        elif path == "/api/fix":
            # The client used to send {"action": {...arbitrary dict with its own
            # "path"/"folder"...}}, which handed it full control over what execute_fix
            # moved/wrote on this machine. It now only gets to pick an index into the
            # fixable_actions list the SERVER just (re)computed itself -- the client can
            # no longer name a path at all.
            if not data.get("confirm"):
                self.send_json({
                    "success": False,
                    "message": ("Thiếu xác nhận: gửi kèm {\"confirm\": true} trong body để "
                                "xác nhận thực hiện sửa lỗi (hành động này di chuyển hoặc ghi file).")
                }, 400)
                return

            action_index = data.get("action_index")
            if not isinstance(action_index, int) or isinstance(action_index, bool):
                self.send_json({
                    "success": False,
                    "message": ("Thiếu hoặc sai kiểu 'action_index': phải là số nguyên chỉ vị trí "
                                "trong 'fixable_actions' lấy từ GET /api/audit. Server tự xác định "
                                "đường dẫn cần sửa; client không được tự gửi path/action.")
                }, 400)
                return

            report = _refresh_audit_cache(probe, inv_engine, doctor)
            fixable = report["fixable_actions"]
            if action_index < 0 or action_index >= len(fixable):
                self.send_json({
                    "success": False,
                    "message": (f"action_index={action_index} không hợp lệ: hiện chỉ có "
                                f"{len(fixable)} hành động khả dụng. Hãy gọi lại GET /api/audit "
                                f"để lấy danh sách fixable_actions mới nhất rồi thử lại.")
                }, 400)
                return

            action = fixable[action_index]
            res = doctor.execute_fix(action)
            _invalidate_audit_cache()
            self.send_json(res)

        elif path == "/api/backup":
            label = data.get("label", "manual")
            res = vault.create_snapshot(label=label)
            _invalidate_audit_cache()
            self.send_json(res)

        elif path == "/api/restore/preview":
            snapshot_id = data.get("snapshot_id")
            if not snapshot_id:
                snaps = vault.list_snapshots()
                if snaps:
                    snapshot_id = snaps[0]["id"]
            if not snapshot_id:
                self.send_json({"error": "NO_SNAPSHOTS"}, 400)
                return
            preview = vault.preview_restore(snapshot_id)
            self.send_json(preview)

        elif path == "/api/restore/execute":
            if not data.get("confirm"):
                self.send_json({
                    "success": False,
                    "message": ("Thiếu xác nhận: gửi kèm {\"confirm\": true} trong body để xác "
                                "nhận khôi phục (hành động này ghi đè file hiện có).")
                }, 400)
                return
            snapshot_id = data.get("snapshot_id")
            # SaveVault._resolve_snapshot already rejects this at the engine layer, but a
            # rejection there comes back as success:False with HTTP 200, which is easy to
            # miss. Checking it here too lets the HTTP layer answer with an explicit 400.
            if vault._resolve_snapshot(snapshot_id) is None:
                self.send_json({
                    "success": False,
                    "error": "INVALID_SNAPSHOT_ID",
                    "message": (f"ID snapshot không hợp lệ: '{snapshot_id}'. Hãy gọi GET "
                                f"/api/snapshots để chọn lại một ID hợp lệ.")
                }, 400)
                return
            res = vault.restore_snapshot(snapshot_id)
            _invalidate_audit_cache()
            self.send_json(res)

        elif path == "/api/verify":
            snapshot_id = data.get("snapshot_id")
            if vault._resolve_snapshot(snapshot_id) is None:
                self.send_json({
                    "ok": False,
                    "error": "INVALID_SNAPSHOT_ID",
                    "message": (f"ID snapshot không hợp lệ: '{snapshot_id}'. Hãy gọi GET "
                                f"/api/snapshots để chọn lại một ID hợp lệ.")
                }, 400)
                return
            res = vault.verify_snapshot(snapshot_id)
            self.send_json(res)

        elif path == "/api/rollback":
            if not data.get("confirm"):
                self.send_json({
                    "success": False,
                    "message": ("Thiếu xác nhận: gửi kèm {\"confirm\": true} trong body để xác "
                                "nhận rollback (hành động này ghi đè file về trạng thái trước khi "
                                "restore dở dang).")
                }, 400)
                return
            res = vault.rollback_from_journal()
            _invalidate_audit_cache()
            self.send_json(res)

        else:
            self.send_error(404, "Not Found")

def build_server(port: int = 8080) -> HTTPServer:
    """Binds and returns the HTTPServer without starting it. port=0 asks the OS for any
    free port; the actual port is then httpd.server_address[1]. Split out from run() so
    tests can bind an isolated server on a free port and drive it from a background
    thread without going through the CLI entrypoint."""
    server_address = ("127.0.0.1", port)
    return HTTPServer(server_address, SimulatorHandler)

def run(port: int = 8080, reset_playground: bool = False):
    init_playground(reset=reset_playground)
    httpd = build_server(port)
    actual_port = httpd.server_address[1]
    print(f"LoongMate Handheld Simulator Server running at http://127.0.0.1:{actual_port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    args = sys.argv[1:]
    reset_playground = "--reset-playground" in args
    port_args = [a for a in args if a != "--reset-playground"]
    port = int(port_args[0]) if port_args else 8080
    run(port, reset_playground=reset_playground)
