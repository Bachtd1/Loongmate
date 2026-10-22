"""
CLI entry point for LoongMate.
Implements:
  loongmate readiness
  loongmate scan [--fix] [--yes]
  loongmate backup [--endpoint <name>]
  loongmate restore [--snapshot <ID>] [--dry-run] [--file <rel_path> ...] [--yes]
  loongmate verify [--snapshot <ID>]
  loongmate recover [--rollback] [--yes]
  loongmate health
  loongmate bundle [--no-privacy]
  loongmate endpoint add|list|remove|test|pin
  loongmate push --endpoint <name> [--snapshot <ID>]
  loongmate pull --endpoint <name> --snapshot <ID>
  loongmate dedupe [--apply] [--yes] [--quarantine] [--max-hash-mb N] [--plan-out FILE | --plan FILE]
  loongmate rom delete <path> [--yes] [--keep-artwork]
  loongmate quarantine list | purge [--older-than DAYS] [--yes]
  loongmate move <rom> --to {primary,secondary} [--apply] [--yes]
  loongmate move --bulk --to {primary,secondary} --target-free-mb N [--system SYS] [--apply] [--yes] [--plan-out FILE | --plan FILE]
  loongmate fix batch [--apply] [--only KIND ...] [--yes] [--plan-out FILE | --plan FILE] | fix undo [--check] [--yes]
  loongmate snapshots | roms [--system SYS] [--card {primary,secondary}] [--sort name|size] [--save any|with|without] [--query TEXT] [--limit N]
  loongmate saves list | history KEY | restore KEY --snapshot ID [--yes]
  loongmate import scan [PATH ...] | plan [PATH ...] [--card auto|primary|secondary] [--select ID ...] [--choice ID=HỆ ...] [--plan-out FILE]
            | apply --plan FILE --yes [--choice ID=HỆ ...] [--conflict ID=keep|replace|copy|cancel ...] [--bios-ok ID|all ...] [--delete-source]
            | jobs | recover [--yes] | mkdir [--card primary|secondary]     (Local Importer: chỉ nhập file người dùng tự đưa vào)
  loongmate library nodes | overview | bundles | pair BUNDLE.json [--yes] | unpair NODE --yes | roots NODE | list NODE ROOT [--system S] [--query Q] [--offline] [--all]
            | fetch NODE ROOT PATH... [--card auto|primary|secondary] [--conflict keep|replace|copy|cancel] [--dry-run|--yes] [--progress]
            | jobs | resume JOB --yes [--progress] | cancel JOB --yes      (Thư viện riêng: agent PocketBridge của BẠN; tải + kiểm SHA-256 + cài bằng importer)
  loongmate homebrew sync [--progress] | list [--system S] [--query Q] [--all] | show SLUG | jobs
            | fetch SLUG... [--card auto|primary|secondary] [--conflict keep|replace|copy|cancel] [--dry-run|--yes] [--progress]
            | resume JOB --yes [--progress] | cancel JOB --yes     (Homebrew Hub: chỉ game có giấy phép rõ ràng; tải trực tiếp qua HTTPS kiểm CA)
  loongmate web home | open URL | fetch URL... [--card auto|primary|secondary] [--conflict keep|replace|copy|cancel] [--dry-run|--yes] [--progress]
            | jobs | resume JOB --yes [--progress] | cancel JOB --yes | bookmark add URL [--title T] | bookmark remove URL | history clear
            (Tải từ web: mở trang https bất kỳ, chọn link file, tải + kiểm + cài bằng Nhập game; không JavaScript/cookie)
  loongmate patch list | info ID | candidates ID [--system S] | apply ID --rom PATH [--name NAME] [--header-skipped] --yes | delete ID --yes | store PATH
            (Patch ROM .ips/.bps/.ups: tạo file ROM MỚI cạnh bản gốc, bản gốc không bị sửa; ID = s:<tên> (kho) hoặc i:<thẻ>:<tên> (Import/))
  loongmate hottabs status | on | off | install [--yes] | uninstall [--yes]   (Leaf Hot Tabs; install/uninstall chỉ xem trước nếu thiếu --yes)
  loongmate autoresume status|on|off | pocketlock status|on|off   (tự tiếp tục qua Agent; khoá khi thức dậy: giữ Select + Start 1 giây để mở)
  loongmate bios | history [--days N] | agent status | rescan
  loongmate battery [--days N] | update status|list [--verify]|verify|apply|rollback [--apps-dir DIR]
  loongmate nodes list | ping NAME | probe NAME [--ports ...] | wake NAME | fav NAME on|off | mac NAME MAC
  (cờ toàn cục --no-rescan: không tự gọi Rescan Library sau khi ghi)

Exit codes: 0 success, 1 task failure, 2 usage/environment error,
            3 scan found a 'critical'-severity issue.
"""

import sys
import os
import re
import time
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.probe import PlatformProbe
from engine.savevault import SaveVault
from engine.config import ConfigStore
from engine import savevault as savevault_mod
from engine import status as status_mod


class _LazyMod:
    """Nạp module ở lần dùng đầu tiên. CLI được native gọi cho MỖI thao tác nên khởi động phải nhẹ: `readiness`
    không được trả giá nạp ui_screens/transfer/nodes... Đọc VÀ ghi thuộc tính đều chuyển sang module thật
    (mock.patch.object(cli.transfer_mod, ...) vẫn đúng chỗ)."""

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)

    def _mod(self):
        import importlib
        return importlib.import_module(object.__getattribute__(self, "_name"))

    def __getattr__(self, attr):
        return getattr(self._mod(), attr)

    def __setattr__(self, attr, value):
        setattr(self._mod(), attr, value)

    def __delattr__(self, attr):
        delattr(self._mod(), attr)


dedupe_mod = _LazyMod("engine.dedupe")
hashcache_mod = _LazyMod("engine.hashcache")
transfer_mod = _LazyMod("engine.transfer")
batchfix_mod = _LazyMod("engine.batchfix")
bios_mod = _LazyMod("engine.bios")
telemetry_mod = _LazyMod("engine.telemetry")
agent_mod = _LazyMod("engine.agent")
nodes_mod = _LazyMod("engine.nodes")
jawaka_mod = _LazyMod("engine.jawaka")
config_mod = _LazyMod("engine.config")
ui_mod = _LazyMod("engine.ui_screens")      # plan_rom_batch/execute_rom_batch/find_update_bundles... (kéo cả bộ màn hình)
update_mod = _LazyMod("engine.update")
hottabs_mod = _LazyMod("engine.hottabs")
leafconfig_mod = _LazyMod("engine.leafconfig")
autoresume_mod = _LazyMod("engine.autoresume")
archive_mod = _LazyMod("engine.archive")
pocketlock_mod = _LazyMod("engine.pocketlock")
importer_mod = _LazyMod("engine.importer")
libsource_mod = _LazyMod("engine.libsource")
homebrew_mod = _LazyMod("engine.homebrew")
web_mod = _LazyMod("engine.web")
patch_mod = _LazyMod("engine.patcher")

_LAZY_NAMES = {   # tên lớp/hàm nạp trễ; vẫn đọc được là `cli.<tên>` (test patch CLI.InventoryEngine)
    "InventoryEngine": "engine.inventory", "find_systems_catalog": "engine.inventory",
    "LibraryDoctor": "engine.doctor", "DeviceHealth": "engine.health", "PidLock": "engine.autobackup",
    "RemoteVault": "engine.remote", "LocalFolderTransport": "engine.remote", "SftpTransport": "engine.remote",
    "fetch_host_fingerprint": "engine.remote", "pin_host_key": "engine.remote",
}


def __getattr__(name):
    mod = _LAZY_NAMES.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_CRITICAL_ISSUES = 3
EXIT_PLAN_REJECTED = 3   # --apply --plan: file kế hoạch bị từ chối toàn bộ (sai loại, bị sửa, quá cũ...)

EPILOG = """
Mã thoát:
  0  thành công
  1  tác vụ thất bại
  2  lỗi sử dụng / môi trường (vd: thiếu thẻ Primary, endpoint sai)
  3  lệnh 'scan' tìm thấy issue mức 'critical'
"""


def print_banner():
    banner = r"""
  _                               __  __       _
 | |                             |  \/  |     | |
 | |     ___   ___  _ __   __ _  | \  / | __ _| |_ ___
 | |    / _ \ / _ \| '_ \ / _` | | |\/| |/ _` | __/ _ \
 | |___| (_) | (_) | | | | (_| | | |  | | (_| | ||  __/
 |______\___/ \___/|_| |_|\__, | |_|  |_|\__,_|\__\___|
                           __/ |  Pocket 1 Care & Sync
                          |___/   Leaf CFW Edition v1.0
    """
    print(banner)


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = path.parent / f"{path.name}.partial"
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes", "c", "co", "có")


def _check_and_warn_crash_recovery(vault: SaveVault):
    """Called at the start of every command (README promises crash detection on
    reopen). Prints to stderr unconditionally (even under --json/--quiet) so it
    never corrupts a JSON stdout payload but is never silently swallowed either.
    """
    recovery = vault.check_crash_recovery()
    if recovery:
        action = (
            "Có thể rollback an toàn bằng lệnh: loongmate recover --rollback"
            if recovery.get("can_rollback") else
            "KHÔNG tìm thấy bản pre-restore để rollback tự động — hãy kiểm tra dữ liệu thủ công trước khi tiếp tục."
        )
        print(
            "!!! CẢNH BÁO: phát hiện phiên khôi phục (restore) bị gián đoạn trước đó (có thể do mất điện). "
            f"Đã lên kế hoạch khôi phục {recovery['planned_count']} file, mới khôi phục được "
            f"{recovery['restored_count']} file. {action}",
            file=sys.stderr
        )
    return recovery


def _build_remote_vault(endpoint_name: str, vault: SaveVault, config_store: ConfigStore):
    """Returns (RemoteVault, None) on success, or (None, error_message) on failure."""
    from engine.remote import RemoteVault, LocalFolderTransport, SftpTransport
    ep = config_store.get_endpoint(endpoint_name)
    if ep is None:
        return None, (
            f"Không tìm thấy endpoint '{endpoint_name}'. Dùng 'endpoint list' để xem danh sách, "
            f"hoặc 'endpoint add' để tạo mới."
        )

    ep_type = ep.get("type")
    if ep_type == "local":
        remote_path = ep.get("remote_path")
        if not remote_path:
            return None, f"Endpoint '{endpoint_name}' loại 'local' thiếu 'remote_path'."
        transport = LocalFolderTransport(Path(remote_path))
    elif ep_type == "sftp":
        known_hosts = vault.userdata_path / "known-hosts"
        try:
            transport = SftpTransport(
                host=ep.get("host"), user=ep.get("user"), remote_path=ep.get("remote_path"),
                port=ep.get("port") or 22, key_path=ep.get("key_path"), known_hosts=known_hosts
            )
        except ValueError as e:
            return None, str(e)
    else:
        return None, f"Endpoint '{endpoint_name}' có type không hợp lệ: '{ep_type}' (chỉ hỗ trợ 'sftp' hoặc 'local')."

    return RemoteVault(vault, transport), None


def _print_json(data) -> None:
    from engine import i18n                       # LOONGMATE_LANG=en: dịch chuỗi cho giao diện (vi thì không làm gì)
    print(json.dumps(i18n.translate_obj(data), indent=2, ensure_ascii=False))


def _cmd_readiness(probe: PlatformProbe, args) -> int:
    readiness = probe.get_readiness_summary()

    vault = None
    snapshots = []
    recovery = None
    if probe.primary_path is not None and probe.userdata_path is not None:
        try:
            vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)
            snapshots = vault.list_snapshots()
            recovery = vault.check_crash_recovery()
        except Exception:
            vault = None

    library_cache = None
    if probe.userdata_path is not None:
        cache_path = probe.userdata_path / "cache" / "last_audit.json"
        if cache_path.exists():
            try:
                library_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                library_cache = None

    readiness["savevault"] = {
        "snapshot_count": len(snapshots),
        "latest_snapshot_id": snapshots[0]["id"] if snapshots else None,
        "latest_snapshot_at": snapshots[0]["created_at"] if snapshots else None
    }
    readiness["library"] = library_cache if library_cache else {
        "available": False,
        "message": "Chưa quét lần nào. Chạy 'scan' để cập nhật."
    }
    readiness["crash_recovery"] = recovery
    # Agent tắt thì tự sao lưu, tự tiếp tục, khoá khi thức dậy đều ngừng mà không ai hay: báo ngay ở ô MÁY của Home.
    # Chỉ khi thẻ có cài LoongMateAgent.pak (chạy trên PC thì không báo).
    from engine.autobackup import pid_lock_held      # nhẹ: không import engine.agent ở lệnh chạy mỗi lần mở Home
    agent_running = bool(pid_lock_held(Path(probe.userdata_path) / "agent.lock")) if probe.userdata_path else False
    agent_installed = probe.primary_path is not None and (Path(probe.primary_path) / "Apps" / "mlp1" / "LoongMateAgent.pak").is_dir()
    readiness["agent"] = {"running": agent_running, "installed": agent_installed}
    dev = readiness.get("device")
    if agent_installed and not agent_running and isinstance(dev, dict):
        dev.setdefault("warnings", []).insert(0, "Agent chưa chạy: bật ở Settings > Services")
        if dev.get("status") in ("green", "unknown", None):
            dev["status"] = "yellow"
    offsite = status_mod.offsite_status(probe.userdata_path)
    readiness["offsite"] = offsite

    if probe.primary_path is None:
        readiness["primary_missing"] = True
        readiness["probe_errors"] = probe.probe_errors

    if args.json:
        _print_json(readiness)
        return EXIT_USAGE_ERROR if probe.primary_path is None else EXIT_OK

    if not args.quiet:
        print_banner()
    print("=== BÁO CÁO SẴN SÀNG THIẾT BỊ (READINESS) ===")
    dev = readiness["device"]
    bat = dev["battery"]
    therm = dev["thermal"]
    st = readiness["storage"]
    net = readiness["network"]

    status_icons = {"green": "[OK]", "yellow": "[WARN]", "red": "[ERR]", "unknown": "[?]"}
    # Never render an unreadable sensor as a value: "Pin: None%" looks like a
    # measurement. Say plainly that it could not be read.
    bat_txt = f"{bat['percent']}% ({bat['status']})" if bat.get("available") and bat.get("percent") is not None else "không đọc được"
    therm_txt = f"{therm['current_celsius']}°C ({therm['trend']})" if therm.get("available") and therm.get("current_celsius") is not None else "không đọc được"
    print(f"1. Thiết bị: {status_icons[dev['status']]} | Pin: {bat_txt} | Nhiệt độ: {therm_txt}")
    for w in dev["warnings"]:
        print(f"   ! Cảnh báo: {w}")

    p_st = st["primary"]
    if p_st["mounted"]:
        print(f"2. Thẻ SD Chính: {p_st['free_mb']}MB trống / {p_st['total_mb']}MB (Ghi: {'Có' if p_st['writable'] else 'KHÔNG'}) | Định dạng: {p_st['filesystem']}")
    else:
        print("2. Thẻ SD Chính: KHÔNG XÁC ĐỊNH ĐƯỢC")
    if st["secondary"]:
        s_st = st["secondary"]
        print(f"   Thẻ SD Phụ:   {s_st['free_mb']}MB trống / {s_st['total_mb']}MB (Ghi: {'Có' if s_st['writable'] else 'KHÔNG'}) | Định dạng: {s_st['filesystem']} {'(FAT32 OK)' if s_st['is_fat32'] else '(LỖI: CẦN FAT32!)'}")
    else:
        print("   Thẻ SD Phụ:   Chưa gắn thẻ SD khe 2")
    for w in st["warnings"]:
        print(f"   ! Cảnh báo thẻ nhớ: {w}")

    print(f"3. Mạng: {'Đã kết nối' if net['connected'] else 'Chưa kết nối'} | IP: {net['ip_address']}")

    print("4. SaveVault (save có an toàn không):")
    if snapshots:
        print(f"   {len(snapshots)} snapshot, mới nhất: {snapshots[0]['id']} lúc {snapshots[0]['created_at']}")
    else:
        print("   Chưa có snapshot nào — hãy chạy 'backup'.")
    print(f"   {'[ĐỎ] ' if offsite['color'] == 'red' else '[CẢNH BÁO] ' if offsite['color'] in ('yellow', 'unknown') else ''}{offsite['message']}")

    print("5. Library (library có lỗi không):")
    if library_cache:
        sev = library_cache.get("severity_counts", {})
        print(
            f"   Số liệu lúc {library_cache.get('generated_at')} (chạy `scan` để cập nhật): "
            f"{library_cache.get('total_issues', 0)} vấn đề (nghiêm trọng {sev.get('critical', 0)}, "
            f"cảnh báo {sev.get('warning', 0)}, ghi chú {sev.get('info', 0)})"
        )
    else:
        print("   Chưa quét lần nào. Chạy 'scan' để cập nhật.")

    if recovery:
        print(f"\n!!! {recovery['message']}")

    print("==============================================")

    if probe.primary_path is None:
        print("\n[LỖI] Không xác định được đường dẫn thẻ Primary:")
        for e in probe.probe_errors:
            print(f"  - {e}")
        print("Hành động: chạy lại với --primary <đường dẫn thẻ>, hoặc đặt biến môi trường SDCARD_PATH.")
        return EXIT_USAGE_ERROR

    return EXIT_OK


RESCAN_REMINDER = "Vào System → Rescan Library trên máy để Leaf cập nhật danh sách game."


def _gate(args):
    """True = proceed, False = user declined, None = refuse (non-TTY and no --yes)."""
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return None
    return _confirm("Xác nhận thực hiện?")


def _refuse_noninteractive(args, what: str, nothing: str = "Không xoá gì.") -> int:
    msg = f"Từ chối {what}: stdin không phải TTY và không có cờ --yes. {nothing} Chạy lại với '--yes' để xác nhận không cần hỏi."
    if args.json:
        _print_json({"success": False, "message": msg})
    else:
        print(f"[LỖI] {msg}")
    return EXIT_TASK_FAILED


def _decline(args, nothing: str = "Không xoá gì.") -> int:
    msg = f"Đã huỷ: người dùng không xác nhận. {nothing}"
    if args.json:
        _print_json({"success": False, "message": msg})
    else:
        print(f"[LoongMate] {msg}")
    return EXIT_TASK_FAILED


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------- kế hoạch đã duyệt (--plan-out / --plan)
# Native duyệt kế hoạch trên màn rồi mới ghi: --apply --plan chỉ làm ĐÚNG các mục trong file, không quét/băm
# lại để tự tìm thêm việc. Mỗi mục vẫn đi qua các chốt an toàn sẵn có của hàm thực thi (băm lại ngay trước khi xoá...).
PLAN_SCHEMA = 1
PLAN_MAX_AGE_S = 24 * 3600
PLAN_CLOCK_SKEW_S = 3600   # created_at nằm "tương lai" quá mức này = giờ máy lệch -> từ chối cho chắc


def _plan_id(items) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _write_plan(path, kind: str, params: dict, items: list) -> dict:
    items = json.loads(json.dumps(items, ensure_ascii=False))   # đúng dạng sẽ đọc lại từ file (khoá/ kiểu JSON)
    doc = {"schema": PLAN_SCHEMA, "kind": kind, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "params": params, "items": items, "plan_id": _plan_id(items)}
    _write_json_atomic(Path(path), doc)
    return doc


def _emit_plan_out(args, kind: str, params: dict, items: list):
    """(doc, None) nếu ghi được; (None, mã thoát) nếu lỗi (đã in)."""
    try:
        return _write_plan(args.plan_out, kind, params, items), None
    except OSError as e:
        msg = f"Không ghi được file kế hoạch '{args.plan_out}': {e}"
        if args.json:
            _print_json({"success": False, "message": msg})
        else:
            print(f"[LỖI] {msg}")
        return None, EXIT_TASK_FAILED


def _load_plan(path, kind: str):
    """(doc, None) hoặc (None, lý do từ chối)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"Không đọc được file kế hoạch '{path}': {e}"
    if not isinstance(doc, dict) or type(doc.get("schema")) is not int or doc["schema"] != PLAN_SCHEMA:
        return None, f"File kế hoạch có schema lạ (cần {PLAN_SCHEMA})."
    if doc.get("kind") != kind:
        return None, f"File kế hoạch loại '{doc.get('kind')}', lệnh này cần loại '{kind}'."
    if not isinstance(doc.get("items"), list) or not isinstance(doc.get("params"), dict):
        return None, "File kế hoạch thiếu 'items'/'params'."
    if doc.get("plan_id") != _plan_id(doc["items"]):
        return None, "plan_id không khớp nội dung 'items': file kế hoạch đã bị sửa. Lập lại kế hoạch."
    import calendar
    try:
        age = time.time() - calendar.timegm(time.strptime(doc["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
    except (KeyError, TypeError, ValueError):
        return None, "File kế hoạch thiếu hoặc sai 'created_at'."
    if age > PLAN_MAX_AGE_S:
        return None, f"Kế hoạch đã cũ ({age / 3600:.1f} giờ > {PLAN_MAX_AGE_S // 3600} giờ). Lập lại kế hoạch."
    if age < -PLAN_CLOCK_SKEW_S:
        return None, "created_at của kế hoạch nằm ở tương lai (giờ máy lệch?). Lập lại kế hoạch."
    return doc, None


def _plan_reject(args, msg: str, plan_id=None) -> int:
    if args.json:
        _print_json({"success": False, "message": msg, "plan_id": plan_id, "applied": [], "refused": []})
    else:
        print(f"[TỪ CHỐI KẾ HOẠCH] {msg}")
    return EXIT_PLAN_REJECTED


def _plan_usage(args) -> "int | None":
    """Kiểm tổ hợp cờ --plan-out/--plan/--apply (dùng chung dedupe, fix batch, move). None = hợp lệ."""
    if args.plan_out and (args.apply or args.plan):
        return _usage(args, "--plan-out chỉ đi với lệnh xem trước (không --apply, không --plan).")
    if args.plan and not args.apply:
        return _usage(args, "--plan chỉ dùng cùng --apply (thực hiện đúng kế hoạch đã duyệt).")
    return None


def _usage(args, msg: str) -> int:
    if args.json:
        _print_json({"success": False, "message": msg})
    else:
        print(f"[LỖI] {msg}")
    return EXIT_USAGE_ERROR


def _auto_rescan(args, changed: bool):
    """Sau một thao tác ghi có thay đổi thật: nhờ Jawaka quét lại thư viện (đúng một lần).
    Trả None nếu không có thay đổi. Không bao giờ đổi mã thoát: ghi đã xong, rescan chỉ là bước phụ.
    Gọi qua jawaka_mod.<hàm> (không import tên) để test thay thế được."""
    if not changed:
        return None
    if getattr(args, "no_rescan", False):
        return {"attempted": False, "ok": None, "message": "Đã tắt tự động rescan (--no-rescan). " + RESCAN_REMINDER}
    avail = jawaka_mod.rescan_available()
    if not avail.get("available"):
        return {"attempted": False, "ok": None,
                "message": f"{RESCAN_REMINDER} (Không tự rescan được: {avail.get('reason') or 'Jawaka không sẵn sàng'})"}
    res = jawaka_mod.request_rescan()
    return {"attempted": True, "ok": bool(res.get("ok")), "message": res.get("message", ""),
            "waited_s": res.get("waited_s")}


def _say_rescan(args, rescan) -> None:
    if rescan is not None and not args.json:
        print(("[Rescan] " if rescan["attempted"] else "") + rescan["message"])


def _dedupe_apply_plan(args, probe) -> int:
    """--apply --plan: xoá ĐÚNG các mục trong file kế hoạch, không quét lại. execute_deletion vẫn băm mới cả
    bản xoá lẫn bản giữ, vẫn giữ bản có save/được playlist tham chiếu -> mục nào không còn hợp lệ thì bị từ chối riêng."""
    doc, err = _load_plan(args.plan, "dedupe")
    if err:
        return _plan_reject(args, err)
    pid, params, items = doc["plan_id"], doc["params"], doc["items"]
    quarantine, copy_art = bool(params.get("quarantine")), bool(params.get("copy_artwork"))
    if (args.quarantine and not quarantine) or (args.copy_artwork and not copy_art):
        return _plan_reject(args, "Cờ --quarantine/--copy-artwork khác với kế hoạch đã duyệt "
                                  f"(kế hoạch: quarantine={quarantine}, copy_artwork={copy_art}). Lập lại kế hoạch với cờ mong muốn.", pid)
    n_remove = sum(len(i["remove"]) for i in items if isinstance(i, dict) and isinstance(i.get("remove"), list))
    if not items:
        if args.json:
            _print_json({"kind": "dedupe", "plan_id": pid, "success": True, "applied": [], "refused": [],
                         "mode": "quarantine" if quarantine else "delete", "bytes_freed": 0, "results": [], "rescan": None})
        else:
            print("Kế hoạch rỗng — không có gì để làm.")
        return EXIT_OK
    if not args.json:
        verb = "chuyển vào Quarantine" if quarantine else "XOÁ VĨNH VIỄN"
        print(f"Kế hoạch {pid[:12]}: sẽ {verb} tối đa {n_remove} file trong {len(items)} nhóm (chỉ các file có trong kế hoạch).")
    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "dedupe --apply --plan")
    if not gate:
        return _decline(args)
    applied, refused, results = [], [], []
    for it in items:
        try:
            keep, sha = it["keep"]["path"], it["sha256"]
            rem = [r["path"] for r in it["remove"]]
            if not (isinstance(keep, str) and isinstance(sha, str) and all(isinstance(p, str) for p in rem)):
                raise TypeError
        except (KeyError, TypeError):
            refused.append({"keep": None, "path": None, "reason": "Mục kế hoạch không đúng dạng — bỏ qua."})
            continue
        res = dedupe_mod.execute_deletion(
            rem, probe.userdata_path, "duplicate", kept_path=keep, expected_sha256=sha,
            primary_path=probe.primary_path, secondary_path=probe.secondary_path,
            quarantine=quarantine, copy_artwork=copy_art)
        results.append({"keep": keep, **res})
        applied += [{"keep": keep, "path": p} for p in res["deleted"]]
        refused += [{"keep": keep, **x} for x in res["refused"]]
    total_freed = sum(r["bytes_freed"] for r in results)
    rescan = _auto_rescan(args, bool(applied))
    if args.json:
        _print_json({"kind": "dedupe", "plan_id": pid, "success": not refused, "applied": applied, "refused": refused,
                     "mode": "quarantine" if quarantine else "delete", "bytes_freed": total_freed,
                     "results": results, "rescan": rescan})
    else:
        for x in refused:
            print(f"[TỪ CHỐI] {x.get('path')}: {x['reason']}")
        print(f"\nĐã {'chuyển vào Quarantine' if quarantine else 'xoá'} {len(applied)} file, giải phóng {_mb(total_freed)}. "
              f"Sổ xoá: {probe.userdata_path / dedupe_mod.LEDGER_NAME}")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if refused else EXIT_OK


def _dedupe_plan_items(groups) -> list:
    return [{"sha256": g["sha256"], "size": g["size"],
             "keep": {k: g["keep"][k] for k in ("path", "card_id", "system", "name")},
             "remove": [{k: r[k] for k in ("path", "card_id", "size", "has_save")} for r in g["remove"]]}
            for g in groups]


def _cmd_dedupe(args, probe, inventory_engine, doctor) -> int:
    bad = _plan_usage(args)
    if bad is None and args.plan_out and args.compare:
        bad = _usage(args, "--plan-out không đi với --compare (chỉ tính, không có kế hoạch xoá).")
    if bad is not None:
        return bad
    if args.plan:
        return _dedupe_apply_plan(args, probe)
    if not args.json:
        print("[LoongMate] Đang quét và băm các file cùng dung lượng (chỉ file nghi trùng mới bị băm)...")
    inv = inventory_engine.scan_all()
    max_bytes = int(args.max_hash_mb * 1024 * 1024) if args.max_hash_mb else None
    cache = hashcache_mod.HashCache(probe.userdata_path)   # chỉ để tìm ứng viên nhanh; xoá luôn băm lại
    try:
        rep = dedupe_mod.find_duplicates(inv, doctor.systems_catalog, probe.primary_path, probe.secondary_path, max_bytes,
                                         prefer_card=args.prefer_card, compare=args.compare, cache=cache)
    finally:
        cache.close()
    groups = rep["groups"]
    n_remove = sum(len(g["remove"]) for g in groups)

    if args.compare:
        if args.json:
            _print_json(rep)
        else:
            print(f"Đã băm {rep['hashed_files']} file ({_mb(rep['hashed_bytes'])}). So sánh các lựa chọn --prefer-card (chỉ tính, không xoá):")
            if ui_mod.hash_cache_note(rep):
                print(ui_mod.hash_cache_note(rep).strip())
            print(f"{'prefer-card':<12} {'file':>6} {'thu hồi SD1':>13} {'thu hồi SD2':>13} {'tổng':>11}")
            for k in ("auto", "primary", "secondary"):
                c = rep["compare"][k]
                print(f"{k:<12} {c['files']:>6} {_mb(c['by_card']['primary']):>13} {_mb(c['by_card']['secondary']):>13} {_mb(c['reclaimable_bytes']):>11}")
            print("(Bản có save và bản được playlist tham chiếu luôn được giữ, bất kể lựa chọn.)")
        return EXIT_OK

    if not args.json:
        print("=== ROM TRÙNG NỘI DUNG (SHA-256) ===")
        by = rep["reclaimable_by_card"]
        print(f"{len(groups)} nhóm trùng | đã băm {rep['hashed_files']} file ({_mb(rep['hashed_bytes'])}) | "
              f"thu hồi được {n_remove} file = {_mb(rep['reclaimable_bytes'])} "
              f"(SD1: {_mb(by['primary'])}, SD2: {_mb(by['secondary'])}) | prefer-card={rep['prefer_card']}")
        if ui_mod.hash_cache_note(rep):
            print(ui_mod.hash_cache_note(rep).strip())
        if rep["skipped_budget_files"]:
            print(f"! Bỏ qua {rep['skipped_budget_files']} file nghi trùng vì vượt giới hạn --max-hash-mb.")
        for e in rep["hash_errors"]:
            print(f"! Không băm được {e['path']}: {e['reason']}")
        for i, g in enumerate(groups, 1):
            print(f"\n{i}. {g['keep']['name']} ({g['keep']['system']}, {_mb(g['size'])})")
            print(f"   GIỮ : {g['keep']['path']}")
            print(f"         Lý do: {g['keep_reason']}")
            for o in g.get("also_kept", []):
                print(f"   GIỮ : {o['path']}  (cũng được playlist tham chiếu)")
            for r in g["remove"]:
                warn = "  [!] CÓ SAVE/STATE đi kèm bản này (sẽ không bị xoá nhưng thành mồ côi)" if r["has_save"] else ""
                print(f"   XOÁ : {r['path']}{warn}")
        if rep["skipped_multifile"]:
            print(f"\n--- {len(rep['skipped_multifile'])} game nhiều file nghi trùng (KHÔNG tự đề xuất xoá) ---")
            for m in rep["skipped_multifile"]:
                print(f"* {m['name']} ({m['system']}, {_mb(m['size'])}): " + " | ".join(m["paths"]))
            print("  Lý do: xoá lẻ một file .bin/đĩa sẽ làm hỏng game; dùng 'rom delete <file .cue/.m3u>' để xoá trọn bộ.")

    if not args.apply:
        if args.plan_out:
            doc, code = _emit_plan_out(args, "dedupe", {"prefer_card": args.prefer_card, "max_hash_mb": args.max_hash_mb,
                                                        "quarantine": bool(args.quarantine),
                                                        "copy_artwork": bool(args.copy_artwork)},
                                       _dedupe_plan_items(groups))
            if doc is None:
                return code
            rep = {**rep, "plan_id": doc["plan_id"], "plan_path": str(args.plan_out)}
            if not args.json:
                print(f"\nĐã ghi kế hoạch: {args.plan_out} (plan_id {doc['plan_id']}). Thực hiện: dedupe --apply --plan FILE --yes")
        if args.json:
            _print_json(rep)
        elif groups:
            print("\n(Chỉ xem, chưa xoá gì. Thêm --apply để thực hiện; thêm --quarantine nếu muốn có đường lùi.)")
        return EXIT_OK
    if not groups:
        if args.json:
            _print_json({**rep, "applied": False})
        return EXIT_OK

    if not args.json:
        verb = "chuyển vào Quarantine" if args.quarantine else "XOÁ VĨNH VIỄN"
        print(f"\nSẽ {verb} {n_remove} file, {_mb(rep['reclaimable_bytes'])}.")
        if args.quarantine:
            print("Lưu ý: Quarantine nằm trên cùng thẻ nên chưa giải phóng dung lượng; dùng 'quarantine purge' sau để xoá hẳn.")
    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "dedupe --apply")
    if not gate:
        return _decline(args)

    results, failed = [], False
    for g in groups:
        res = dedupe_mod.execute_deletion(
            [r["path"] for r in g["remove"]], probe.userdata_path, "duplicate",
            kept_path=g["keep"]["path"], expected_sha256=g["sha256"],
            primary_path=probe.primary_path, secondary_path=probe.secondary_path,
            quarantine=args.quarantine, copy_artwork=args.copy_artwork)
        results.append({"keep": g["keep"]["path"], **res})
        failed = failed or bool(res["refused"])
    total_freed = sum(r["bytes_freed"] for r in results)
    n_done = sum(len(r["deleted"]) for r in results)
    rescan = _auto_rescan(args, n_done > 0)
    if args.json:
        _print_json({**rep, "applied": True, "results": results, "bytes_freed": total_freed, "rescan": rescan})
    else:
        for r in results:
            for x in r["refused"]:
                print(f"[TỪ CHỐI] {x['path']}: {x['reason']}")
        print(f"\nĐã {'chuyển vào Quarantine' if args.quarantine else 'xoá'} {n_done} file, giải phóng {_mb(total_freed)}. "
              f"Sổ xoá: {probe.userdata_path / dedupe_mod.LEDGER_NAME}")
        for r in results:
            a = r.get("artwork")
            if a:
                print(f"[Ảnh bìa] {a.get('status')}: {a.get('message')}")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if failed else EXIT_OK


def _cmd_rom(args, probe) -> int:
    if args.rom_command != "delete":
        print("[LỖI] Dùng: rom delete <đường dẫn file ROM> [<đường dẫn khác> ...] [--yes] [--keep-artwork]")
        return EXIT_USAGE_ERROR
    entries = ui_mod.plan_rom_batch(args.path, probe.primary_path, probe.secondary_path,
                                    include_artwork=not args.keep_artwork)
    single = len(entries) == 1
    ok = [e for e in entries if e["plan"]["ok"] and not e["merged_into"]]
    # Đúng một PATH: giữ các khoá cũ ({"plan"}, hoặc chính plan khi lập kế hoạch hỏng) cho client cũ.
    legacy = (({"plan": entries[0]["plan"]} if ok else dict(entries[0]["plan"])) if single else {})
    if not args.json:
        for e in entries:
            plan = e["plan"]
            if not single:
                print(f"== {e['path']}")
            if e["merged_into"]:
                print(f"(Nằm trong kế hoạch xoá của {e['merged_into']} — gộp, không xoá hai lần.)")
                continue
            for w in plan["warnings"]:
                print(f"! {w}")
            if not plan["ok"]:
                print(f"[LỖI] {plan['message']}")
                continue
            print("Sẽ XOÁ:")
            for f in plan["files"] + plan["artwork"]:
                print(f"  - {f}")
            print("Save/state được GIỮ LẠI (không xoá):" if plan["saves_kept"] else "Không có save/state đi kèm.")
            for f in plan["saves_kept"]:
                print(f"  + {f}")
            print(plan["message"])
    if not ok:
        if args.json:
            games = ui_mod.rom_batch_game_rows(entries)
            _print_json({"message": "Không có game nào lập được kế hoạch xoá.", **legacy, "games": games, "deleted": [],
                         "refused": [x for g in games for x in g["refused"]], "bytes_freed": 0,
                         "success": False, "rescan": None})
        return EXIT_TASK_FAILED
    gate = _gate(args)
    if gate is None or not gate:
        if args.json:
            games = ui_mod.rom_batch_game_rows(entries)
            _print_json({**legacy, "games": games, "deleted": [], "refused": [x for g in games for x in g["refused"]],
                         "bytes_freed": 0, "success": False, "rescan": None,
                         "message": "Từ chối: không có --yes và stdin không phải TTY." if gate is None else "Đã huỷ: người dùng không xác nhận."})
            return EXIT_TASK_FAILED
        return _refuse_noninteractive(args, "rom delete") if gate is None else _decline(args)
    res = ui_mod.execute_rom_batch(entries, probe.userdata_path, probe.primary_path, probe.secondary_path)
    rescan = _auto_rescan(args, bool(res["deleted"]))   # đúng một lần cho cả lô
    failed = bool(res["refused"])
    if args.json:
        if single:
            legacy.update(mode="delete", ledger_path=res["ledger_path"])
        _print_json({**legacy, "games": res["games"], "deleted": res["deleted"], "refused": res["refused"],
                     "bytes_freed": res["bytes_freed"], "success": bool(res["deleted"]) and not failed, "rescan": rescan})
    else:
        for x in res["refused"]:
            print(f"[TỪ CHỐI] {x['path']}: {x['reason']}")
        print(f"Đã xoá {len(res['deleted'])} file, giải phóng {_mb(res['bytes_freed'])}.")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if failed else EXIT_OK


def _cmd_quarantine(args, probe) -> int:
    lst = dedupe_mod.list_quarantine(probe.userdata_path, probe.secondary_path)
    if args.quarantine_command == "list":
        if args.json:
            _print_json(lst)
        else:
            print(f"=== QUARANTINE: {lst['total_files']} file, {_mb(lst['total_bytes'])} ===")
            for b in lst["batches"]:
                age = f"{b['age_days']:.1f} ngày" if b["age_days"] is not None else "không rõ tuổi"
                print(f"* {b['name']} ({age}): {len(b['files'])} file, {_mb(b['bytes'])}  [{b['root']}]")
        return EXIT_OK
    if args.quarantine_command != "purge":
        print("[LỖI] Dùng: quarantine list | quarantine purge [--older-than DAYS] [--yes]")
        return EXIT_USAGE_ERROR
    days = args.older_than
    sel = [b for b in lst["batches"]
           if days is None or (b["age_days"] is not None and b["age_days"] >= days)]
    n, size = sum(len(b["files"]) for b in sel), sum(b["bytes"] for b in sel)
    if not args.json:
        print(f"Sẽ XOÁ VĨNH VIỄN {n} file ({_mb(size)}) trong {len(sel)} lô Quarantine"
              + (f" cũ hơn {days} ngày." if days is not None else "."))
    if n == 0:
        if args.json:
            _print_json({"deleted": [], "refused": [], "bytes_freed": 0})
        return EXIT_OK
    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "quarantine purge")
    if not gate:
        return _decline(args)
    res = dedupe_mod.purge_quarantine(probe.userdata_path, probe.secondary_path, older_than_days=days)
    rescan = _auto_rescan(args, bool(res["deleted"]))
    if args.json:
        _print_json({**res, "rescan": rescan})
    else:
        for x in res["refused"]:
            print(f"[TỪ CHỐI] {x['path']}: {x['reason']}")
        print(f"Đã xoá vĩnh viễn {len(res['deleted'])} file, giải phóng {_mb(res['bytes_freed'])}.")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if res["refused"] else EXIT_OK


def _move_apply_plan(args, probe) -> int:
    """--apply --plan: chuyển ĐÚNG các game trong file. Mỗi game phải còn nguyên (size + mtime_ns từng file nguồn
    như lúc duyệt) mới được thực hiện; execute_move vẫn kiểm biên, hash đích và xác minh sau khi chép."""
    doc, err = _load_plan(args.plan, "move")
    if err:
        return _plan_reject(args, err)
    pid, params, items = doc["plan_id"], doc["params"], doc["items"]
    if params.get("to") != args.to:
        return _plan_reject(args, f"--to {args.to} khác thẻ đích trong kế hoạch ('{params.get('to')}').", pid)
    if not items:
        if args.json:
            _print_json({"kind": "move", "plan_id": pid, "success": True, "applied": [], "refused": [],
                         "bytes_moved": 0, "rolled_back": False, "results": [], "rescan": None})
        else:
            print("Kế hoạch rỗng — không có gì để làm.")
        return EXIT_OK
    if not args.json:
        print(f"Kế hoạch {pid[:12]}: sẽ chuyển {len(items)} game sang thẻ '{args.to}' (chỉ các game có trong kế hoạch).")
    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "move --apply --plan", MOVE_NOTHING)
    if not gate:
        return _decline(args, MOVE_NOTHING)
    applied, refused, results = [], [], []
    for it in items:
        name = it.get("name") if isinstance(it, dict) else None
        plan = it.get("plan") if isinstance(it, dict) else None
        if not isinstance(plan, dict):
            refused.append({"game": name, "path": None, "reason": "Mục kế hoạch không đúng dạng — bỏ qua."})
            continue
        why = transfer_mod.check_plan_stamps(plan)
        if why:
            refused.append({"game": name, "path": it.get("rom_path"), "reason": why})
            continue
        r = transfer_mod.execute_move(plan, probe.userdata_path, probe.primary_path, probe.secondary_path)
        results.append({"game": name, **r})
        if r["moved"] or r["copied"]:
            applied.append({"game": name, "moved": r["moved"], "copied": r["copied"], "bytes_moved": r["bytes_moved"]})
        refused += [{"game": name, **x} for x in r["refused"]]
    bytes_moved = sum(r["bytes_moved"] for r in results)
    rolled_back = any(r["rolled_back"] for r in results)
    rescan = _auto_rescan(args, bool(applied))
    if args.json:
        _print_json({"kind": "move", "plan_id": pid, "success": not refused and not rolled_back, "applied": applied,
                     "refused": refused, "bytes_moved": bytes_moved, "rolled_back": rolled_back,
                     "results": results, "rescan": rescan})
    else:
        for x in refused:
            print(f"[TỪ CHỐI] {x.get('game')}: {x.get('path')}: {x['reason']}")
        if rolled_back:
            print("[HOÀN TÁC] Một game bị lỗi nên đã xoá bản đã chép, nguồn còn nguyên.")
        print(f"Đã chuyển {len(applied)} game, giải phóng {_mb(bytes_moved)} ở thẻ nguồn. "
              f"Sổ: {probe.userdata_path / 'transfer-ledger.jsonl'}")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if (refused or rolled_back) else EXIT_OK


def _move_plan_items(args, probe, plan) -> list:
    if args.bulk:
        return [{"rom_path": m["rom_path"], "name": m["name"], "system": m["system"], "bytes_freed": m["bytes_freed"],
                 "bytes_copy": m["bytes_copy"], "has_save": m["has_save"], "discs": m["discs"],
                 "plan": transfer_mod.stamp_plan(m["plan"])} for m in plan["moves"]]
    loc = transfer_mod.locate_rom(Path(args.rom), probe.primary_path, probe.secondary_path)
    return [{"rom_path": str(args.rom), "name": plan["game"], "system": loc[1] if loc else None,
             "bytes_freed": plan["bytes_freed"], "bytes_copy": plan["bytes"],
             "has_save": any(e["kind"] in ("save", "state") for e in plan["files"]), "discs": 1,
             "plan": transfer_mod.stamp_plan(plan)}]


MOVE_NOTHING = "Không chuyển gì."
FIX_NOTHING = "Không sửa gì."


def _cmd_move(args, probe, inventory_engine) -> int:
    bad = _plan_usage(args)
    if bad is not None:
        return bad
    if args.plan:
        return _move_apply_plan(args, probe)
    dest = args.to
    if args.bulk:
        if args.rom:
            return _usage(args, "Dùng 'move <rom> --to ...' HOẶC 'move --bulk ...', không dùng cả hai.")
        if not args.target_free_mb or args.target_free_mb <= 0:
            return _usage(args, "move --bulk cần --target-free-mb N (N > 0, số MB trống mong muốn ở thẻ nguồn).")
        inv = inventory_engine.scan_all()
        plan = transfer_mod.plan_bulk_move(inv, probe.primary_path, probe.secondary_path, dest,
                                           target_free_mb=args.target_free_mb, system=args.system)
        plans = [m["plan"] for m in plan["moves"]]
    else:
        if not args.rom:
            return _usage(args, "Dùng: move <đường dẫn ROM> --to {primary,secondary}  hoặc  move --bulk --to ... --target-free-mb N")
        plan = transfer_mod.plan_move(args.rom, probe.primary_path, probe.secondary_path, dest)
        plans = [plan] if plan["ok"] else []

    if not args.json:
        for w in plan["warnings"]:
            print(f"! {w}")
        print(plan["message"] if plan["message"] else "")
        if args.bulk:
            for m in plan["moves"]:
                print(f"  - {m['name']} ({m['system']}): giải phóng {_mb(m['bytes_freed'])}, chép {_mb(m['bytes_copy'])}"
                      + ("  [có save/state]" if m["has_save"] else ""))
            for sk in plan["skipped"]:
                print(f"  ~ bỏ qua {sk['rom_path']}: {sk['reason']}")
        elif plan["ok"]:
            for f in plan["files"]:
                print(f"  - {'CHUYỂN' if f['remove_src'] else 'CHÉP (giữ nguồn)'} [{f['kind']}] {f['src']} -> {f['dst']}")
    if not plan["ok"]:
        if args.json:
            _print_json(plan)
        return EXIT_TASK_FAILED
    if not args.apply:
        if args.plan_out:
            doc, code = _emit_plan_out(args, "move", {"to": dest, "bulk": bool(args.bulk),
                                                      "target_free_mb": args.target_free_mb, "system": args.system},
                                       _move_plan_items(args, probe, plan) if plans else [])
            if doc is None:
                return code
            plan = {**plan, "plan_id": doc["plan_id"], "plan_path": str(args.plan_out)}
            if not args.json:
                print(f"\nĐã ghi kế hoạch: {args.plan_out} (plan_id {doc['plan_id']}). Thực hiện: move --apply --plan FILE --yes")
        if args.json:
            _print_json(plan)
        elif plans:
            print("\n(Chỉ xem, chưa chuyển gì. Thêm --apply để thực hiện.)")
        return EXIT_OK
    if not plans:
        if args.json:
            _print_json({"plan": plan, "applied": False})
        return EXIT_OK

    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "move --apply", MOVE_NOTHING)
    if not gate:
        return _decline(args, MOVE_NOTHING)

    results = [transfer_mod.execute_move(p, probe.userdata_path, probe.primary_path, probe.secondary_path) for p in plans]
    n_moved = sum(len(r["moved"]) for r in results)
    n_copied = sum(len(r["copied"]) for r in results)
    bytes_moved = sum(r["bytes_moved"] for r in results)
    failed = any(r["refused"] or r["rolled_back"] for r in results)
    rescan = _auto_rescan(args, n_moved + n_copied > 0)
    if args.json:
        _print_json({"plan": plan, "applied": True, "results": results, "bytes_moved": bytes_moved, "rescan": rescan})
    else:
        for r in results:
            for x in r["refused"]:
                print(f"[TỪ CHỐI] {x['path']}: {x['reason']}")
            if r["rolled_back"]:
                print("[HOÀN TÁC] Một game bị lỗi nên đã xoá bản đã chép, nguồn còn nguyên.")
        print(f"Đã chuyển {n_moved} file, chép thêm {n_copied} file giữ nguồn, giải phóng {_mb(bytes_moved)} ở thẻ nguồn. "
              f"Sổ: {probe.userdata_path / 'transfer-ledger.jsonl'}")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if failed else EXIT_OK


def _fix_apply_plan(args, probe) -> int:
    """--apply --plan: làm ĐÚNG các bước trong file. Dấu trong bước (sha256 cue, danh sách file của thư mục alias)
    làm execute_batch_fix từ chối riêng phần đã đổi/mới xuất hiện từ lúc duyệt."""
    doc, err = _load_plan(args.plan, "batchfix")
    if err:
        return _plan_reject(args, err)
    pid, items = doc["plan_id"], doc["items"]
    refused = [{"kind": (s.get("kind") if isinstance(s, dict) else None), "path": None,
                "reason": "Bước kế hoạch không đúng dạng hoặc loại lạ — bỏ qua."}
               for s in items if not (isinstance(s, dict) and s.get("kind") in batchfix_mod.KIND_ORDER)]
    steps = [s for s in items if isinstance(s, dict) and s.get("kind") in batchfix_mod.KIND_ORDER]
    if not items:
        if args.json:
            _print_json({"kind": "batchfix", "plan_id": pid, "success": True, "applied": [], "refused": [],
                         "batch_id": None, "journal": str(probe.userdata_path / batchfix_mod.JOURNAL_NAME), "rescan": None})
        else:
            print("Kế hoạch rỗng — không có gì để làm.")
        return EXIT_OK
    if not args.json:
        print(f"Kế hoạch {pid[:12]}: {len(steps)} bước (chỉ các bước có trong kế hoạch).")
        for s in steps:
            print(f"* [{s['kind']}] {s.get('label')}")
    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "fix batch --apply --plan", FIX_NOTHING)
    if not gate:
        return _decline(args, FIX_NOTHING)
    res = batchfix_mod.execute_batch_fix(
        {"steps": steps, "primary_path": str(probe.primary_path),
         "secondary_path": str(probe.secondary_path) if probe.secondary_path else None}, probe.userdata_path)
    refused += res["refused"]
    changed = any(d["kind"] != "legacy_folder" or d.get("moved") or d.get("quarantined_duplicates") for d in res["done"])
    rescan = _auto_rescan(args, changed)
    if args.json:
        _print_json({"kind": "batchfix", "plan_id": pid, "success": not refused, "applied": res["done"], "refused": refused,
                     "batch_id": res["batch_id"], "journal": res["journal"], "rescan": rescan})
    else:
        for d in res["done"]:
            print(f"[XONG] [{d['kind']}] {d.get('label') or d.get('path')}")
        for x in refused:
            print(f"[TỪ CHỐI] [{x.get('kind')}] {x.get('path')}: {x['reason']}")
        print(f"Nhật ký hoàn tác: {res['journal']} (lô {res['batch_id']}). Hoàn tác bằng: fix undo")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if refused else EXIT_OK


def _cmd_fix(args, probe, inventory_engine, doctor) -> int:
    journal = probe.userdata_path / batchfix_mod.JOURNAL_NAME
    if args.fix_command == "undo":
        if args.check:
            pb = batchfix_mod.pending_batch(journal)
            if args.json:
                _print_json(pb)
            elif pb["has_batch"]:
                print(f"Có lô {pb['batch_id']} ({pb['created_at']}) có thể hoàn tác: {pb['operations']} thao tác "
                      f"({', '.join(f'{k} x{v}' for k, v in sorted(pb['by_op'].items()))}).")
            else:
                print("Không có lô sửa nào để hoàn tác.")
            return EXIT_OK
        if not args.json:
            print("Sẽ hoàn tác lô 'fix batch' gần nhất chưa hoàn tác (theo trạng thái thật trên đĩa).")
        gate = _gate(args)
        if gate is None:
            return _refuse_noninteractive(args, "fix undo", "Không hoàn tác gì.")
        if not gate:
            return _decline(args, "Không hoàn tác gì.")
        res = batchfix_mod.undo_batch_fix(journal)
        rescan = _auto_rescan(args, bool(res["undone"]))
        if args.json:
            _print_json({**res, "rescan": rescan})
        else:
            if res["batch_id"] is None:
                print("Không có lô sửa nào để hoàn tác.")
            else:
                print(f"Lô {res['batch_id']}: đã hoàn tác {len(res['undone'])} thao tác.")
            for u in res["undone"]:
                print(f"  + [{u['op']}] {u['path']}")
            for s in res["skipped"]:
                print(f"  ! BỎ QUA [{s['op']}] {s['path']}: {s['reason']}")
            _say_rescan(args, rescan)
        return EXIT_TASK_FAILED if res["skipped"] else EXIT_OK
    if args.fix_command != "batch":
        print("[LỖI] Dùng: fix batch [--apply] [--only KIND ...] [--yes]  |  fix undo [--check] [--yes]")
        return EXIT_USAGE_ERROR
    bad = _plan_usage(args)
    if bad is None and args.plan and args.only:
        bad = _usage(args, "--only không đi với --plan (kế hoạch đã chốt các bước).")
    if bad is not None:
        return bad
    if args.plan:
        return _fix_apply_plan(args, probe)

    if not args.json:
        print("[LoongMate] Đang quét và lập kế hoạch sửa hàng loạt...")
    inv = inventory_engine.scan_all()
    report = doctor.audit(inv, probe.get_readiness_summary()["storage"])
    plan = batchfix_mod.plan_batch_fix(inv, report, probe.primary_path, probe.secondary_path, doctor.systems_catalog)
    only = args.only
    view = [s for s in plan["steps"] if not only or s["kind"] in only]
    shown = {**plan, "steps": view}
    if not args.json:
        print(f"=== SỬA HÀNG LOẠT: {len(view)} bước" + (f" (chỉ loại: {', '.join(only)})" if only else "") + " ===")
        for s in view:
            print(f"* [{s['kind']}] {s['label']} (rủi ro: {s['risk']})")
            print(f"    {s['detail']}")
    if not args.apply:
        if args.plan_out:
            doc, code = _emit_plan_out(args, "batchfix", {"only": list(only or [])},
                                       batchfix_mod.stamp_plan({**plan, "steps": view})["steps"])
            if doc is None:
                return code
            shown = {**shown, "plan_id": doc["plan_id"], "plan_path": str(args.plan_out)}
            if not args.json:
                print(f"\nĐã ghi kế hoạch: {args.plan_out} (plan_id {doc['plan_id']}). Thực hiện: fix batch --apply --plan FILE --yes")
        if args.json:
            _print_json(shown)
        elif view:
            print("\n(Chỉ xem, chưa sửa gì. Thêm --apply để thực hiện; hoàn tác được bằng 'fix undo'.)")
        return EXIT_OK
    if not view:
        if args.json:
            _print_json({**shown, "applied": False})
        return EXIT_OK

    gate = _gate(args)
    if gate is None:
        return _refuse_noninteractive(args, "fix batch --apply", FIX_NOTHING)
    if not gate:
        return _decline(args, FIX_NOTHING)
    res = batchfix_mod.execute_batch_fix(plan, probe.userdata_path, selected_kinds=only)
    # legacy_folder luôn ghi một mục "done" kể cả khi không đổi gì: chỉ tính khi thật sự có file đổi chỗ.
    changed = any(d["kind"] != "legacy_folder" or d.get("moved") or d.get("quarantined_duplicates") for d in res["done"])
    rescan = _auto_rescan(args, changed)
    if args.json:
        _print_json({"plan": shown, "applied": True, **res, "rescan": rescan})
    else:
        for d in res["done"]:
            print(f"[XONG] [{d['kind']}] {d.get('label') or d.get('path')}")
        for x in res["refused"]:
            print(f"[TỪ CHỐI] [{x['kind']}] {x['path']}: {x['reason']}")
        print(f"Nhật ký hoàn tác: {res['journal']} (lô {res['batch_id']}). Hoàn tác bằng: fix undo")
        _say_rescan(args, rescan)
    return EXIT_TASK_FAILED if res["refused"] else EXIT_OK


_BIOS_ICON = {"ok": "[OK]", "wrong_hash": "[SAI MD5]", "missing": "[THIẾU]", "unknown_hash": "[CÓ, CHƯA XÁC MINH]"}


def _cmd_bios(args, probe, inventory_engine, doctor) -> int:
    inv = inventory_engine.scan_all()
    active = sorted({r["system"] for cid in ("primary", "secondary") for r in (inv.get(cid) or {}).get("roms", [])})
    rep = bios_mod.check_bios(probe.primary_path, probe.secondary_path, doctor.systems_catalog, active)
    if args.json:
        _print_json(rep)
        return EXIT_OK
    if not rep["systems"]:
        print("Không có hệ máy nào đang có ROM cần BIOS.")
        return EXIT_OK
    print("=== KIỂM TRA BIOS (md5) ===")
    print(f"{'Hệ máy':<12} {'File':<18} {'Trạng thái':<22} Ghi chú")
    for sysrep in rep["systems"]:
        for e in sysrep["required"]:
            tag = "tuỳ chọn" if e.get("optional") else "bắt buộc"
            print(f"{sysrep['system']:<12} {e['name']:<18} {_BIOS_ICON.get(e['status'], e['status']):<22} ({tag}) {e['note']}")
    print("Ghi chú: danh sách lấy từ systems.json; catalog hiện chưa đánh dấu file nào tuỳ chọn nên mặc định ghi 'bắt buộc' "
          "(vd. PSX chỉ cần bản đúng vùng game). " + bios_mod.NO_DOWNLOAD)
    return EXIT_OK


def _cmd_history(args, probe) -> int:
    since = time.time() - args.days * 86400
    rows = telemetry_mod.load(probe.userdata_path, since_ts=since)
    s = telemetry_mod.summarize(rows)
    if args.json:
        _print_json({"days": args.days, "summary": s, "rows": rows})
        return EXIT_OK
    print(f"=== LỊCH SỬ PIN / NHIỆT ({args.days:g} ngày qua) ===")
    if not rows:
        print("Chưa có mẫu telemetry nào (bật service 'LoongMate Agent' để ghi mẫu mỗi 5 phút).")
        return EXIT_OK
    t0 = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["first_ts"]))
    t1 = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_ts"]))
    print(f"{s['samples']} mẫu, từ {t0} đến {t1}")
    d = s["drain_pct_per_hour"]
    print("Hao pin: " + (f"{d} %/giờ (trên {s['discharge_hours']} giờ xả liên tục)" if d is not None
                         else f"chưa đủ dữ liệu (cần >= 0.5 giờ xả liên tục, hiện có {s['discharge_hours']} giờ)"))
    for name in ("soc", "gpu", "batt"):
        t = s["temp"][name]
        print(f"Nhiệt {name}: " + (f"max {t['max']}°C, trung bình {t['avg']}°C" if t else "không đọc được"))
    print(f"Vượt 70°C: {s['over_70_events']} lần ({s['over_70_samples']} mẫu)")
    return EXIT_OK


def _fmt_num(v, unit: str = "") -> str:
    return "không có" if v is None else f"{v:g}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"


def _cmd_battery(args, probe) -> int:
    fn = getattr(telemetry_mod, "battery_health", None)
    if fn is None:
        msg = "Engine chưa có telemetry.battery_health nên không tính được sức khoẻ pin."
        if args.json:
            _print_json({"available": False, "reason": msg})
        else:
            print(f"[LỖI] {msg}")
        return EXIT_USAGE_ERROR
    rows = telemetry_mod.load(probe.userdata_path, since_ts=time.time() - args.days * 86400)
    bh = fn(rows)
    if args.json:
        _print_json(bh)
        return EXIT_OK
    print(f"=== SỨC KHOẺ PIN (ƯỚC LƯỢNG từ {bh.get('samples')} mẫu trong {args.days:g} ngày) ===")
    if not bh.get("available"):
        print(f"Chưa tính được: {bh.get('reason')}")
        return EXIT_OK
    print(f"Dung lượng đầy hiện tại : {_fmt_num(bh.get('charge_full_mah'), ' mAh')}")
    print(f"Dung lượng thiết kế     : {_fmt_num(bh.get('design_mah'), ' mAh')} (nguồn: {bh.get('design_source')})")
    print(f"Sức khoẻ ước lượng      : {_fmt_num(bh.get('estimated_pct'), '%')} (ước lượng = đầy hiện tại / thiết kế)")
    print(f"Sức khoẻ theo driver    : {_fmt_num(bh.get('driver_health'))}")
    print(f"Xu hướng                : {_fmt_num(bh.get('trend_mah_per_30d'), ' mAh/30 ngày')}")
    if bh.get("reason"):
        print(f"Ghi chú: {bh['reason']}")
    return EXIT_OK


def _update_list(args, probe) -> int:
    """CHỈ ĐỌC: các gói trong <thẻ chính>/LoongMate-update/*/manifest.json. Mặc định KHÔNG băm (chậm); --verify thì băm."""
    inst = update_mod.status(probe.userdata_path).get("installed")
    inst = inst if isinstance(inst, dict) else None
    rows = []
    for b in ui_mod.find_update_bundles(probe.primary_path):
        row = {"name": b.name, "path": str(b), "version": None, "created_at": None, "files": None,
               "installed": False, "error": None, "verify": None}
        try:
            m = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(m, dict) or not isinstance(m.get("version"), str) or not isinstance(m.get("files"), dict):
                raise ValueError("manifest thiếu 'version' hoặc 'files'")
            row.update(version=m["version"], files=len(m["files"]),
                       created_at=m["created_at"] if isinstance(m.get("created_at"), str) else None)
            row["installed"] = bool(inst and inst.get("version") == row["version"]
                                    and inst.get("bundle_created_at") in (None, row["created_at"]))
        except (OSError, ValueError) as e:
            row["error"] = f"manifest.json không đọc được: {e}"
        if args.verify and row["error"] is None:
            v = update_mod.verify_bundle(b)
            row["verify"] = {"ok": bool(v["ok"]), "errors": list(v.get("errors") or [])}
        rows.append(row)
    out = {"update_dir": str(Path(probe.primary_path) / ui_mod.UPDATE_DIR_NAME), "count": len(rows),
           "installed": inst, "verified": bool(args.verify), "bundles": rows}
    if args.json:
        _print_json(out)
        return EXIT_OK
    print(f"=== GÓI CẬP NHẬT trong {out['update_dir']} ===")
    if not rows:
        print("Không có gói nào.")
    for r in rows:
        tag = " [ĐANG CÀI]" if r["installed"] else ""
        if r["error"]:
            print(f"* {r['name']}: LỖI {r['error']}")
            continue
        vtxt = "" if r["verify"] is None else (" | hash OK" if r["verify"]["ok"] else f" | HASH SAI ({len(r['verify']['errors'])} lỗi)")
        print(f"* {r['name']}: v{r['version']} ({r['created_at'] or 'không rõ ngày'}), {r['files']} file{tag}{vtxt}")
    return EXIT_OK


def _cmd_update(args, probe) -> int:
    cmd = args.update_command
    if cmd == "list":
        return _update_list(args, probe)
    apps = Path(args.apps_dir) if getattr(args, "apps_dir", None) else ui_mod.default_apps_dir(probe.primary_path)
    app = probe.userdata_path
    if cmd == "status":
        st = update_mod.status(app)
        if args.json:
            _print_json(st)
        else:
            inst = st.get("installed") or {}
            print(f"Phiên bản đang chạy: {inst.get('version') or 'chưa ghi nhận (bản cài tay)'}"
                  + (f" (cài lúc {inst['installed_at']})" if inst.get("installed_at") else ""))
            print(f"Thư mục Apps: {apps}")
            bundles = ui_mod.find_update_bundles(probe.primary_path)
            print("Gói trên thẻ: " + (", ".join(f"{b.name} (v{ui_mod.bundle_version(b) or '?'})" for b in bundles) or "không có"))
            print(f"Bản sao lưu để quay về: {len(st.get('backups') or [])}"
                  + (f" (mới nhất: {st['backups'][-1]['name']}, phiên bản trước {st['backups'][-1].get('version_before') or 'không rõ'})" if st.get("backups") else ""))
        return EXIT_OK
    if cmd in ("verify", "apply"):
        bundle = args.bundle
        if not bundle:
            found = ui_mod.find_update_bundles(probe.primary_path)
            if len(found) != 1:
                return _usage(args, f"Cần chỉ rõ thư mục gói: {'không thấy gói nào' if not found else 'có ' + str(len(found)) + ' gói'} trong "
                                    f"{Path(probe.primary_path) / ui_mod.UPDATE_DIR_NAME}.")
            bundle = found[0]
        v = update_mod.verify_bundle(bundle)
        if cmd == "verify" or not v["ok"]:
            if args.json:
                _print_json(v)
            elif v["ok"]:
                print(f"[OK] Gói hợp lệ: phiên bản {v['version']}, {v['files']} file khớp SHA-256, pak: {', '.join(v['paks'])}")
            else:
                print("[LỖI] Gói KHÔNG hợp lệ:")
                for e in v["errors"]:
                    print(f"  - {e}")
            return EXIT_OK if v["ok"] else EXIT_TASK_FAILED
        gate = _gate(args)
        if gate is None:
            return _refuse_noninteractive(args, "cập nhật", "Không cập nhật gì.")
        if not gate:
            return _decline(args, "Không cập nhật gì.")
        r = update_mod.apply_update(bundle, apps, app)
    else:   # rollback
        gate = _gate(args)
        if gate is None:
            return _refuse_noninteractive(args, "quay về bản trước", "Không đổi gì.")
        if not gate:
            return _decline(args, "Không đổi gì.")
        r = update_mod.rollback(apps, app)
    if args.json:
        _print_json(r)
    elif r.get("ok"):
        print(f"[XONG] {'Cập nhật lên' if cmd == 'apply' else 'Quay về'} {r.get('version') or 'bản cài tay'}; pak: {', '.join(r.get('paks') or [])}; "
              f"bản sao lưu: {r.get('backup')}")
        print(f"{ui_mod.RESTART_NOTE if cmd == 'apply' else ui_mod.RESTART_NOTE_ROLLBACK}.")
    else:
        print(f"[LỖI] {r.get('error')}" + (f" (bước {r['stage']})" if r.get("stage") else "") + (" — đã trả về bản cũ." if r.get("rolled_back") else ""))
        for e in r.get("errors") or []:
            print(f"  - {e}")
    return EXIT_OK if r.get("ok") else EXIT_TASK_FAILED


def _read_agent_state(app) -> dict:
    """agent_state.json (chỉ đọc); {} nếu không có/hỏng."""
    try:
        d = json.loads((Path(app) / agent_mod.STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _cmd_snapshots(args, probe, vault) -> int:
    """CHỈ ĐỌC. "verified": true chỉ khi agent đã kiểm toàn vẹn TOÀN KHO (agent_state.json: vault_verify) xong hoàn
    toàn, ok, và snapshot được tạo trước thời điểm đó (object lưu theo nội dung nên bất biến); ngược lại null
    (không biết) — KHÔNG BAO GIỜ false vì chưa có bản ghi kiểm từng snapshot."""
    vv = _read_agent_state(probe.userdata_path).get("vault_verify")
    vv = vv if isinstance(vv, dict) else None
    ts = vv.get("ts") if vv and vv.get("ok") is True and not vv.get("partial") else None
    ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
    rows = []
    for s in vault.list_snapshots():
        try:
            made = time.mktime(time.strptime(s["created_at"], "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError):
            made = None
        rows.append({"id": s["id"], "label": s["label"], "auto": s["label"].startswith(savevault_mod.AUTO_LABEL_PREFIX),
                     "created_at": s["created_at"], "total_files": s["total_files"],
                     "total_size_bytes": s["total_size_bytes"], "path": s["path"],
                     "verified": True if (ts is not None and made is not None and made <= ts) else None})
    out = {"count": len(rows), "latest_id": rows[0]["id"] if rows else None, "snapshots": rows, "vault_verify": vv}
    if args.json:
        _print_json(out)
        return EXIT_OK
    print(f"=== SNAPSHOT SAVEVAULT: {len(rows)} ===")
    for r in rows:
        print(f"* {r['id']}  {r['created_at']}  {r['total_files']} file, {_mb(r['total_size_bytes'])}"
              + ("  [auto]" if r["auto"] else "") + ("  [đã xác minh]" if r["verified"] else ""))
    return EXIT_OK


_STATE_SUFFIX_RE = re.compile(r"\.state.*$")


def fold_text(s: str) -> str:
    """So khớp không phân biệt hoa/thường và dấu tiếng Việt (đ -> d)."""
    import unicodedata
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _cmd_roms(args, probe, inventory_engine, doctor) -> int:
    """CHỈ ĐỌC: mọi file trong Roms/ của các thẻ (đúng như InventoryEngine thấy, kể cả .bin track). has_save/has_state:
    có save/state cùng tên (stem, không phân biệt hoa/thường) ở BẤT KỲ thẻ nào."""
    if args.limit is not None and args.limit < 1:
        return _usage(args, "--limit phải >= 1.")
    inv = inventory_engine.scan_all()

    def canon(name: str) -> str:
        info = doctor.systems_catalog.get(name.upper())
        return (info["folder"] if info and info.get("folder") else name).upper()

    saves, states = set(), set()
    for cid in ("primary", "secondary"):
        c = inv.get(cid) or {}
        saves |= {sv.get("stem", "").lower() for sv in c.get("saves", [])}
        states |= {_STATE_SUFFIX_RE.sub("", sv.get("stem", "").lower()) for sv in c.get("states", [])}
    want = canon(args.system) if args.system else None
    rows = []
    for cid in ("primary", "secondary"):
        if args.card and args.card != cid:
            continue
        for r in (inv.get(cid) or {}).get("roms", []):
            if want and canon(r["system"]) != want:
                continue
            if Path(r["absolute_path"]).parent.name.lower() == "imgs":
                continue            # ảnh bìa Leaf để trong Roms/<hệ>/Imgs/ (vd CPS1/2/3) — không phải game
            stem = r["stem"].lower()
            rows.append({"path": r["absolute_path"], "name": r["name"], "system": r["system"], "card": cid,
                         "size": r["size"], "extension": r["extension"],
                         "has_save": stem in saves, "has_state": stem in states})
    rows.sort(key=lambda x: (x["card"], x["system"].lower(), x["name"].lower()))
    filters = {"system": args.system, "card": args.card}
    # Cờ mới (chỉ thêm khoá vào "filters" khi được dùng — không cờ thì output y hệt trước đây).
    extra = {}
    if args.query:
        q = fold_text(args.query)
        rows = [r for r in rows if q in fold_text(r["name"])]
        extra["query"] = args.query
    if args.save and args.save != "any":
        rows = [r for r in rows if r["has_save"] == (args.save == "with")]
        extra["save"] = args.save
    if args.sort == "name":
        rows.sort(key=lambda x: (fold_text(x["name"]), x["card"], x["system"].lower()))
    elif args.sort == "size":
        rows.sort(key=lambda x: (-x["size"], fold_text(x["name"]), x["card"]))
    if args.sort:
        extra["sort"] = args.sort
    matched = len(rows)
    if args.limit is not None:
        rows = rows[:args.limit]
        extra["limit"] = args.limit
    filters.update(extra)
    out = {"count": len(rows), "total_bytes": sum(r["size"] for r in rows), "filters": filters, "roms": rows}
    if args.limit is not None:
        out["matched"] = matched
    if args.json:
        _print_json(out)
        return EXIT_OK
    print(f"=== ROM: {len(rows)} file, {_mb(out['total_bytes'])} ===")
    for r in rows:
        print(f"{r['card']:<9} {r['system']:<10} {_mb(r['size']):>10}  {r['name']}"
              + ("  [save]" if r["has_save"] else "") + ("  [state]" if r["has_state"] else ""))
    return EXIT_OK


def _saves_system_map(probe) -> dict:
    """stem ROM (chữ thường) -> hệ máy, để gắn "system" cho game có save. Lỗi quét thì {} (system = null)."""
    try:
        from engine.inventory import InventoryEngine
        inv = InventoryEngine(probe.primary_path, probe.secondary_path).scan_all()
    except Exception:
        return {}
    out = {}
    for cid in ("primary", "secondary"):
        for r in sorted((inv.get(cid) or {}).get("roms", []), key=lambda x: x["absolute_path"]):
            if Path(r["absolute_path"]).parent.name.lower() != "imgs":
                out.setdefault(r["stem"].lower(), r["system"])
    return out


def _saves_err(args, code: str, msg: str, **extra) -> int:
    if args.json:
        _print_json({"error": code, "message": msg, **extra})
    else:
        print(f"[LỖI] {msg}")
    return EXIT_TASK_FAILED


def _cmd_saves(args, probe, vault) -> int:
    """saves list | history KEY | restore KEY --snapshot ID [--yes]. Khôi phục CHỈ file của một game, qua đúng
    SaveVault.restore_snapshot(selected_files=...) (pre-restore snapshot trước khi ghi)."""
    sub = args.saves_command
    if sub not in ("list", "history", "restore"):
        return _usage(args, "Dùng: saves list | saves history KEY | saves restore KEY --snapshot ID [--yes]")
    idx = vault.game_index()
    games, snaps, current = idx["games"], idx["snapshots"], idx["current"]

    if sub == "list":
        sysmap = _saves_system_map(probe)
        rows = []
        for g in sorted(games.values(), key=lambda x: (x["name"].lower(), x["key"])):
            cards = sorted(g["cards"])
            rows.append({"key": g["key"], "name": g["name"], "system": sysmap.get(g["key"]), "core": g["core"],
                         "card": cards[0] if len(cards) == 1 else "both" if cards else None,
                         "files": [{"path": f["absolute_path"], "rel": f["rel_path"], "size": f["size"],
                                    "mtime": f["mtime"]} for f in g["current"]],
                         "versions": len(g["snapshots"])})
        if args.json:
            _print_json({"games": rows})
            return EXIT_OK
        print(f"=== SAVE THEO GAME: {len(rows)} ===")
        for r in rows:
            print(f"{r['name']}  [{r['core'] or '-'}]  {len(r['files'])} file hiện tại, {r['versions']} phiên bản")
        return EXIT_OK

    key = args.key.strip().lower()
    g = games.get(key)
    if g is None:
        return _saves_err(args, "GAME_NOT_FOUND", f"Không có save/state nào của game '{args.key}' (hiện tại hoặc trong snapshot).")
    cur_files = current.get("files", {})
    known_snaps = {s["id"] for s in snaps}

    if sub == "history":
        versions = []
        for s in snaps:                                    # list_snapshots: mới nhất trước
            infos = g["snapshots"].get(s["id"])
            if not infos:
                continue
            files = []
            for rel in sorted(infos):
                cur = cur_files.get(rel)
                files.append({"rel": rel, "size": infos[rel]["size"], "sha256": infos[rel]["sha256"],
                              "same_as_current": None if cur is None else cur["sha256"] == infos[rel]["sha256"]})
            versions.append({"snapshot_id": s["id"], "label": s["label"], "created_at": s["created_at"], "files": files})
        if args.json:
            _print_json({"key": g["key"], "name": g["name"], "versions": versions})
            return EXIT_OK
        print(f"=== PHIÊN BẢN SAVE: {g['name']} ({len(versions)}) ===")
        for v in versions:
            print(f"* {v['snapshot_id']}  {v['created_at']}  {len(v['files'])} file")
        return EXIT_OK

    # restore
    sid = args.snapshot
    infos = g["snapshots"].get(sid)
    if infos is None:
        if sid not in known_snaps:
            return _saves_err(args, "SNAPSHOT_NOT_FOUND", f"Không có snapshot '{sid}'.")
        return _saves_err(args, "GAME_NOT_IN_SNAPSHOT", f"Snapshot '{sid}' không chứa file nào của '{g['name']}'.")
    plan, to_write = SaveVault.plan_game_restore(g, sid, current)
    head = {"key": g["key"], "name": g["name"], "snapshot_id": sid}

    def show_preview():
        print(f"=== KHÔI PHỤC SAVE: {g['name']} từ {sid} ===")
        for f in plan:
            note = " (bản hiện tại MỚI HƠN snapshot — sẽ được giữ lại ở thư mục xung đột)" if f["newer_than_snapshot"] else ""
            print(f" {f['action']:<9} {f['rel']}{note}")

    if not to_write:
        out = {"success": True, **head, "files": plan, "restored": [], "nothing_to_do": True,
               "message": "Mọi file của game đã giống hệt bản trong snapshot — không cần khôi phục."}
        if args.json:
            _print_json(out)
        else:
            print(out["message"])
        return EXIT_OK
    if not args.yes:
        if args.json or not sys.stdin.isatty():
            out = {"success": False, "preview": True, "requires_confirmation": True, **head, "files": plan,
                   "will_write": len(to_write),
                   "message": "Xem trước — chưa ghi gì. Chạy lại với --yes để khôi phục."}
            if args.json:
                _print_json(out)
            else:
                show_preview()
                print(f"[LỖI] {out['message']}")
            return EXIT_TASK_FAILED
        show_preview()
        if not _confirm("Khôi phục các file trên?"):
            return _decline(args, "Không ghi gì.")
    res = vault.restore_game_files(sid, to_write)
    if not res.get("success"):
        return _saves_err(args, "RESTORE_FAILED", res.get("message", "Khôi phục thất bại."), **head,
                          missing_files=res.get("missing_files", []), pre_restore_id=res.get("pre_restore_id"))
    bad = res["failed"]
    out = {"success": not bad and not res["rejected_paths"] and not res["skipped_readonly"], **head, "files": plan,
           "restored": res["restored"], "failed": bad,
           "pre_restore_id": res["pre_restore_id"], "preserved_conflicts": res["preserved_conflicts"],
           "conflicts_dir": res["conflicts_dir"], "rejected_paths": res["rejected_paths"],
           "skipped_readonly": res["skipped_readonly"], "verified": not bad}
    if not out["success"]:
        out["message"] = (f"{len(bad)} file không khớp sau khi ghi (hoặc bị bỏ qua). Có thể quay lại bằng snapshot "
                          f"{res['pre_restore_id']}.")
    if args.json:
        _print_json(out)
    elif out["success"]:
        print(f"=== ĐÃ KHÔI PHỤC {len(out['restored'])} file của {g['name']} (đã băm lại, khớp snapshot) ===")
        print(f"Bản sao lưu bảo vệ trước khi ghi: {res['pre_restore_id']} (quay lại bằng: saves restore {g['key']} --snapshot {res['pre_restore_id']})")
        if res["preserved_conflicts"]:
            print(f"Bản hiện tại mới hơn đã được giữ ở: {res['conflicts_dir']}")
    else:
        print(f"[LỖI] {out['message']}")
    return EXIT_OK if out["success"] else EXIT_TASK_FAILED


def _cmd_agent(args, probe) -> int:
    if args.agent_command != "status":
        print("[LỖI] Dùng: agent status")
        return EXIT_USAGE_ERROR
    app = probe.userdata_path
    # CHỈ ĐỌC: PidLock.acquire() sẽ tạo agent.lock và ghi pid của CLI khi agent không chạy.
    from engine.autobackup import PidLock
    running = bool(agent_mod.lock_held(app))
    pid = PidLock(app / agent_mod.LOCK_NAME).holder_pid() if running else None
    state = _read_agent_state(app)
    # "never" = chưa từng chạy: không có agent.lock lẫn agent_state.json (đủ cả hai mới kết luận).
    never = not (app / agent_mod.LOCK_NAME).exists() and not (app / agent_mod.STATE_NAME).exists()
    state_name = "running" if running else "never" if never else "stopped"
    leaf_log = agent_mod._leaf_log_path(argparse.Namespace(leaf_log=None), probe)
    rows = telemetry_mod.load(app, since_ts=time.time() - 7 * 86400)
    last = rows[-1] if rows else None
    last_backup = state.get("last_backup")
    st = {"state": state_name, "running": running, "pid": pid, "last_sample_ts": last["ts"] if last else None,
          "vault_verify": state.get("vault_verify"), "prewarm": state.get("prewarm"),
          "reload": state.get("reload"), "last_sample": last,
          "summary_7d": telemetry_mod.summarize(rows) if rows else None,
          "last_backup": last_backup, "leaf_log": str(leaf_log), "leaf_log_exists": leaf_log.exists(),
          "exit_signal": "log Leaf" if leaf_log.exists() else "tiến trình (không thấy log Leaf)"}
    if args.json:
        _print_json(st)
        return EXIT_OK
    state_txt = (f"ĐANG CHẠY (pid {pid or '?'})" if running else
                 "CHƯA TỪNG CHẠY (bật service 'LoongMate Agent' trong màn Services)" if never else
                 "KHÔNG chạy (bật service 'LoongMate Agent' trong màn Services)")
    print(f"Agent: {state_txt}")
    if last:
        print(f"Mẫu gần nhất: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last['ts']))} pin={last['battery_pct']}% "
              f"({last['status']}) soc={last['soc_c']}°C gpu={last['gpu_c']}°C")
    else:
        print("Chưa có mẫu telemetry nào trong 7 ngày qua.")
    print(f"Sao lưu tự động gần nhất: {last_backup or 'chưa có'}")
    print(f"Tín hiệu thoát game: {st['exit_signal']} [{leaf_log}]")
    return EXIT_OK


def _cmd_nodes(args, config_store) -> int:
    cmd = args.nodes_command
    if cmd == "list":
        res = nodes_mod.list_nodes(store=config_store)
        if args.json:
            _print_json(res)
        elif not res["ok"]:
            print(f"[LỖI] {res['error']}")
        else:
            print(f"=== NODE TAILNET (trạng thái: {res['backend_state']}) ===")
            for n in res["nodes"]:
                print(f"{'*' if n['favorite'] else ' '} {n['name']:<24} {'online ' if n['online'] else 'offline'} "
                      f"{n['path']:<8} {n['ip']:<16} {n['os']:<8} MAC={n['mac'] or '-'}")
            if not res["nodes"]:
                print("(Không có node nào khác trong tailnet.)")
        return EXIT_OK if res["ok"] else EXIT_TASK_FAILED
    if cmd == "ping":
        res = nodes_mod.ping_node(args.name)
        if args.json:
            _print_json(res)
        else:
            print(res["message"] if res["ok"] else f"[LỖI] {res['message']}")
        return EXIT_OK if res["ok"] else EXIT_TASK_FAILED
    if cmd == "probe":
        found = next((n for n in nodes_mod.list_nodes(store=config_store)["nodes"] if n["name"] == args.name), None)
        node = found if found else args.name
        kw = {"ports": args.ports} if args.ports else {}
        res = nodes_mod.probe_node(node, **kw)
        if args.json:
            _print_json(res)
        elif not res["ok"]:
            print(f"[LỖI] {res['error']}")
        else:
            print(f"=== QUÉT CỔNG {res['host']} ===")
            for p in res["ports"]:
                print(f"{p['port']:>6} {p['state']:<12} {p['ms']:>5} ms  {p['label']} - {p['detail']}")
        return EXIT_OK if res["ok"] else EXIT_TASK_FAILED
    if cmd == "wake":
        entry = config_mod.get_nodes_settings(config_store)["macs"].get(args.name)
        if not entry or not entry.get("mac"):
            return _usage(args, f"Node '{args.name}' chưa có MAC. Dùng: nodes mac {args.name} XX:XX:XX:XX:XX:XX")
        kw = {"broadcast": entry["broadcast"]} if entry.get("broadcast") else {}
        res = nodes_mod.wake_on_lan(entry["mac"], **kw)
        if args.json:
            _print_json(res)
        else:
            print(res["message"] if res["ok"] else f"[LỖI] {res['message']}")
        return EXIT_OK if res["ok"] else EXIT_TASK_FAILED
    if cmd == "fav":
        try:
            config_mod.set_node_favorite(config_store, args.name, args.state == "on")
        except ValueError as e:
            return _usage(args, str(e))
        msg = f"Node '{args.name}': yêu thích {'BẬT' if args.state == 'on' else 'TẮT'}."
    elif cmd == "mac":
        try:
            config_mod.set_node_mac(config_store, args.name, args.mac, args.broadcast)
        except ValueError as e:
            return _usage(args, str(e))
        msg = f"Đã lưu MAC cho node '{args.name}'."
    else:
        print("[LỖI] Dùng: nodes list | ping NAME | probe NAME [--ports ...] | wake NAME | fav NAME on|off | mac NAME MAC")
        return EXIT_USAGE_ERROR
    if args.json:
        _print_json({"success": True, "message": msg})
    else:
        print(msg)
    return EXIT_OK


def _cmd_rescan(args) -> int:
    res = jawaka_mod.request_rescan()
    if args.json:
        _print_json(res)
    else:
        print(res["message"] if res["ok"] else f"[LỖI] {res['message']}")
    return EXIT_OK if res["ok"] else EXIT_TASK_FAILED


def _kv(items, what: str):
    """['id=VALUE', ...] -> dict; lỗi định dạng -> (None, thông điệp)."""
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k or not v:
            return None, f"{what} sai định dạng '{it}' (cần ID=GIÁ_TRỊ)"
        out[k] = v
    return out, None


def _import_unit_line(u) -> str:
    st = {"ready": "SẴN SÀNG", "needs_choice": "CẦN CHỌN HỆ", "blocked": "BỊ CHẶN", "unsupported": "CHƯA HỖ TRỢ",
          "empty": "TRỐNG"}.get(u["status"], u["status"])
    tail = (f" -> {u['system']}" if u.get("system") else (f" ? {'/'.join(u['candidates'])}" if u.get("candidates") else ""))
    if u.get("bios"):
        tail = f" -> BIOS/{u['bios']['name']}"
    return f"[{st}] {u['name']}{tail}  ({_mb(u['bytes'])}; id {u['id']})"


def _import_print_units(units) -> None:
    for u in units:
        print(_import_unit_line(u))
        if u.get("evidence") and u["status"] in ("ready", "needs_choice"):
            print(f"    nhận diện: {u['evidence']}")
        for r in u.get("reasons", []):
            print(f"    ! {r}")
        for w in u.get("warnings", []):
            print(f"    ~ {w}")
        if u.get("action") and u["action"] not in ("install", u["status"]):
            print(f"    việc sẽ làm: {u['action']}")


def _cmd_import(args, probe) -> int:
    """Local Importer. scan/plan/jobs CHỈ ĐỌC. apply cần --yes (hoặc xác nhận TTY). Xem engine/importer.py."""
    im = importer_mod
    cards = {"primary": probe.primary_path, "secondary": probe.secondary_path}
    cmd = args.import_command
    if cmd == "unpack":
        # Chỉ archive nằm trong Import/ của một thẻ: kết quả ra Import/<tên>/ cạnh nó, archive gốc vào Import/.done/.
        src = Path(args.path)
        inside = any(c and src.parent.resolve() == im.import_dir(c).resolve() for c in cards.values())
        if not inside:
            return _usage(args, f"Chỉ giải nén archive nằm trong Import/ của thẻ: {src}")
        if not args.yes and (args.json or not sys.stdin.isatty() or not _confirm(f"Giải nén {src.name} vào Import/?")):
            return _lib_out(args, {"success": False, "message": "Cần --yes để giải nén."}, False)
        prog = None
        if getattr(args, "progress", False):
            def prog(pct, _n=src.name.replace("\t", " ")):
                sys.stderr.write("LMPROGRESS unpack %d 100 1 1\t%s\n" % (pct, _n))
                sys.stderr.flush()
        remember = Path(probe.userdata_path) / archive_mod.REMEMBERED if probe.userdata_path else None
        r = archive_mod.unpack(src, progress=prog, password=args.password or None,
                               auto_password=args.auto_password, remember_file=remember)
        return _lib_out(args, r, bool(r.get("success")))
    if cmd == "mkdir":
        outs = []
        for cid, c in cards.items():
            if c and (not args.card or args.card == cid):
                r = im.ensure_import_dir(c)
                outs.append({"card": cid, **r})
        if args.json:
            _print_json({"success": bool(outs) and all(o["ok"] for o in outs), "dirs": outs})
        else:
            for o in outs:
                print(o["message"])
        return EXIT_OK if outs and all(o["ok"] for o in outs) else EXIT_TASK_FAILED
    if cmd == "jobs":
        out = im.list_jobs(cards, probe.userdata_path)
        if args.json:
            _print_json({"success": True, **out})
        else:
            print(f"Job dở: {len(out['pending'])}")
            for j in out["pending"]:
                print(f"  {j['job_id']} thẻ {j['card']} trạng thái {j['state']}{' (đang chạy)' if j['running'] else ''}")
            print(f"Job gần đây đã xong/được phục hồi: {len(out['recent'])}")
        return EXIT_OK
    if cmd == "recover":
        pend = im.list_jobs(cards, probe.userdata_path)["pending"]
        if not pend:
            if args.json:
                _print_json({"success": True, "recovered": [], "message": "Không có job dở."})
            else:
                print("Không có job dở.")
            return EXIT_OK
        gate = _gate(args)
        if gate is None:
            return _refuse_noninteractive(args, "import recover", "Không dọn gì.")
        if not gate:
            return _decline(args, "Không dọn gì.")
        out = im.recover(cards, probe.userdata_path)
        if args.json:
            _print_json({"success": True, **out})
        else:
            for r in out["recovered"]:
                print(f"[{r['action']}] job {r['job_id']} (thẻ {r['card']}): " + "; ".join(r.get("notes", [])))
        return EXIT_OK
    hc = hashcache_mod.HashCache(probe.userdata_path) if probe.userdata_path else None
    try:
        if cmd == "scan":
            out = im.scan(args.paths or None, cards)
            out = {"success": True, **out}
            if args.json:
                _print_json(out)
            else:
                for d in out["import_dirs"]:
                    print(f"Thẻ {d['card']}: {d['path']} {'(có)' if d['exists'] else '(chưa có — chạy: import mkdir)'}")
                _import_print_units(out["units"])
                for e in out["errors"]:
                    print(f"[LỖI] {e['path']}: {e['message']}")
                print(f"Tổng: {len(out['units'])} mục  {out['summary']}")
            return EXIT_OK
        if cmd == "plan":
            choices, err = _kv(args.choice, "--choice")
            if err:
                return _usage(args, err)
            plan = im.build_plan(args.paths or None, cards, probe.userdata_path, card_pref=args.card,
                                 select=args.select, hashcache=hc, choices=choices)
            out = {"success": True, **plan}
            if args.plan_out:
                params = {"card": plan["card"], "scan_roots": plan["scan_roots"], "choices": choices,
                          "needed_bytes": plan["needed_bytes"], "limits": plan["limits"]}
                doc, code = _emit_plan_out(args, "import", params, plan["units"])
                if doc is None:
                    return code
                out.update(plan_id=doc["plan_id"], plan_path=str(args.plan_out))
            if args.json:
                _print_json(out)
            else:
                print(f"Thẻ đích: {plan['card']['reason']}")
                _import_print_units(plan["units"])
                if not plan["ok"]:
                    print("[CẦN QUYẾT ĐỊNH] Chưa chọn được thẻ đích — chạy lại với --card primary nếu bạn đồng ý dùng thẻ Leaf.")
                elif args.plan_out:
                    print(f"\nĐã ghi kế hoạch: {args.plan_out} (plan_id {out['plan_id']}). Thực hiện: import apply --plan FILE --yes")
            return EXIT_OK
        if cmd == "apply":
            doc, err = _load_plan(args.plan, "import")
            if err:
                return _plan_reject(args, err)
            choices, err = _kv(args.choice, "--choice")
            if not err:
                conflicts, err = _kv(args.conflict, "--conflict")
            if err:
                return _usage(args, err)
            bad = [v for v in conflicts.values() if v not in im.CONFLICT_ACTIONS]
            if bad:
                return _usage(args, f"--conflict: hành động lạ {bad} (keep|replace|copy|cancel)")
            params = doc["params"]
            plan = {"card": params.get("card"), "units": doc["items"], "scan_roots": params.get("scan_roots", [])}
            gate = _gate(args)
            if gate is None:
                return _refuse_noninteractive(args, "import apply", "Không cài gì.")
            if not gate:
                return _decline(args, "Không cài gì.")
            res = im.apply_plan(plan, cards, probe.userdata_path, choices={**(params.get("choices") or {}), **choices},
                                conflicts=conflicts, bios_ok=args.bios_ok or [], delete_source=args.delete_source,
                                hashcache=hc)
            rescan = _auto_rescan(args, res["changed"])
            if args.json:
                _print_json({**res, "rescan": rescan, "plan_id": doc["plan_id"]})
            else:
                for r in res["results"]:
                    print(f"[{r['status']}] {r['name']}: {r['message']}")
                print(res["message"])
                _say_rescan(args, rescan)
            return EXIT_OK if res["success"] else EXIT_TASK_FAILED
    finally:
        if hc:
            hc.close()
    return EXIT_USAGE_ERROR


def _lib_progress_cb(args):
    """--progress: mỗi dòng 'LMPROGRESS <phase> <done> <total> <index> <count>\\t<tên>' lên STDERR (native đọc để vẽ %); stdout vẫn chỉ là JSON."""
    if not getattr(args, "progress", False):
        return None

    def cb(ev):
        name = str(ev.get("name", "")).replace("\n", " ").replace("\t", " ")
        sys.stderr.write("LMPROGRESS %s %d %d %d %d\t%s\n" % (ev["phase"], ev["done"], ev["total"], ev["index"], ev["count"], name))
        sys.stderr.flush()
    return cb


def _lib_out(args, obj, ok: bool, text_lines=None) -> int:
    if args.json:
        _print_json(obj)
    else:
        for ln in text_lines or [obj.get("message", "")]:
            print(ln)
    return EXIT_OK if ok else EXIT_TASK_FAILED


def _lib_fetch_text(r) -> list:
    out = [r.get("message", "")]
    for x in r.get("results", []) or []:
        out.append(f"[{x.get('status')}] {x.get('name')}: {x.get('message')}")
    if r.get("resumable"):
        out.append(f"Job {r.get('job_id')} còn nguyên: chạy 'library resume {r.get('job_id')} --yes' để tiếp tục.")
    return out


def _cmd_library(args, probe) -> int:
    """Thư viện riêng (engine/libsource.py). nodes/bundles/roots/list/jobs không ghi gì lên thẻ hay secret (list chỉ cập nhật cache
    trong app_data). pair/unpair/fetch/resume/cancel cần --yes (hoặc xác nhận TTY); fetch --dry-run chỉ kiểm + lập kế hoạch."""
    ls = libsource_mod
    cmd = args.library_command
    if probe.userdata_path is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate (đặt --userdata hoặc $USERDATA_PATH).")
    env = ls.Env(probe.userdata_path, {"primary": probe.primary_path, "secondary": probe.secondary_path})
    try:
        if cmd == "nodes":
            out = ls.list_nodes(env)
            return _lib_out(args, out, True, [f"{n['id']}  {n['name']}  {n['host']}:{n['port']}  token={'có' if n['has_token'] else 'THIẾU'}"
                                              for n in out["nodes"]] or ["Chưa ghép đôi thư viện nào."])
        if cmd == "overview":       # một lần gọi cho màn Thư viện riêng của native (nodes + job dở + bundle chờ ghép đôi)
            out = {"success": True, "nodes": ls.list_nodes(env)["nodes"], "jobs": ls.list_jobs(env)["jobs"], "bundles": ls.find_bundles(env)}
            return _lib_out(args, out, True, [f"{len(out['nodes'])} thư viện, {len(out['jobs'])} job dở, {len(out['bundles'])} bundle."])
        if cmd == "bundles":
            out = {"success": True, "bundles": ls.find_bundles(env)}
            return _lib_out(args, out, True, [f"{b['card']}:{b['dir']}/{b['file']}  {'-> ' + b['node_name'] if b['valid'] else 'KHÔNG HỢP LỆ: ' + b['reason']}"
                                              for b in out["bundles"]] or ["Không có bundle nào trong <thẻ>/PocketBridge-import/ hay Import/."])
        if cmd == "roots":
            out = ls.list_roots(env, args.node)
            return _lib_out(args, out, True, [f"{r['id']}  {r['name']}  {r['files']} file  {_mb(r['bytes'])}{'  (đang quét)' if r['indexing'] else ''}"
                                              for r in out["roots"]] or ["Agent chưa khai báo library.roots."])
        if cmd == "list":
            out = ls.browse(env, args.node, args.root, system=args.system, query=args.query, refresh=not args.offline,
                            only="all" if args.all else None)
            lines = [f"{'(CŨ — dùng cache) ' if out['stale'] else ''}{out['total']} game; hệ máy: " + ", ".join(f"{x['system']}={x['count']}" for x in out["systems"])]
            for e in out.get("entries", []):
                lines.append(f"{e['system']:>10}  {_mb(e['size']):>10}  {e['path']}{'' if e['installable'] else '  [' + e['problem'] + ']'}")
            return _lib_out(args, out, True, lines)
        if cmd == "jobs":
            out = ls.list_jobs(env)
            return _lib_out(args, out, True, [f"{j['job_id']}  {j['state']}  {j['percent']}%  {j['files_done']}/{j['files']} file  {j['node']}/{j['root']}"
                                              for j in out["jobs"]] or ["Không có job tải dở."])
        if cmd == "fetch" and args.dry_run:
            pl = ls.prepare(env, args.node, args.root, args.paths, args.card)
            lines = [pl["message"]] + [f"{'!!' if f['problem'] else '+'} {f['path']}  {_mb(f['size'])}{'  — ' + f['problem'] if f['problem'] else ''}" for f in pl["files"]]
            lines.append(f"Tổng {_mb(pl['total_bytes'])}; cần ~{_mb(pl['needed_bytes'])} trên thẻ đích.")
            return _lib_out(args, pl, True, lines)
        # ---- từ đây là lệnh ghi
        gate = _gate(args)
        what = {"pair": "Không ghép đôi, bundle giữ nguyên.", "unpair": "Không gỡ gì.", "fetch": "Không tải/cài gì."}.get(cmd, "Không làm gì.")
        if gate is None:
            return _refuse_noninteractive(args, f"library {cmd}", what)
        if not gate:
            return _decline(args, what)
        if cmd == "pair":
            out = ls.pair(env, args.bundle)
            return _lib_out(args, out, out["success"])
        if cmd == "unpair":
            return _lib_out(args, ls.unpair(env, args.node), True)
        if cmd == "cancel":
            return _lib_out(args, ls.cancel_job(env, args.job), True)
        if cmd == "fetch":
            res = ls.fetch(env, args.node, args.root, args.paths, args.card, _lib_progress_cb(args), args.conflict)
        else:
            res = ls.resume(env, args.job, _lib_progress_cb(args), args.conflict)
        res["rescan"] = _auto_rescan(args, bool(res.get("changed")))
        return _lib_out(args, res, bool(res.get("success")), _lib_fetch_text(res) if res.get("started") else [res["message"]])
    except ls.LibError as e:
        return _lib_out(args, {"success": False, "error_kind": e.kind, "message": e.message}, False, [f"[LỖI] {e.message}"])


def _cmd_homebrew(args, probe) -> int:
    """Homebrew Hub tải trực tiếp (engine/homebrew.py). sync chỉ ghi cache trong app_data (cần Internet); list/show/jobs chỉ đọc;
    fetch/resume/cancel cần --yes (hoặc xác nhận TTY); fetch --dry-run chỉ hỏi kích thước và lập kế hoạch."""
    hb, ls = homebrew_mod, libsource_mod
    cmd = args.homebrew_command
    if probe.userdata_path is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate (đặt --userdata hoặc $USERDATA_PATH).")
    env = ls.Env(probe.userdata_path, {"primary": probe.primary_path, "secondary": probe.secondary_path})
    try:
        if cmd == "sync":
            out = hb.sync_catalog(env.app_data, hb.Session(), progress=_lib_progress_cb(args))
            return _lib_out(args, out, True, [out["message"], f"{out['pages']} trang trong {out['elapsed_s']} giây."])
        if cmd == "list":
            out = hb.list_games(env, system=args.system, query=args.query, only="all" if args.all else None)
            lines = [f"{out['total']} game (danh mục lúc {out['fetched_at']}); hệ máy: " + ", ".join(f"{x['system']}={x['count']}" for x in out["systems"])]
            for e in out.get("entries", []):
                lines.append(f"{e['system']:>4}  {e['license_id']:<14}  {'[đã có] ' if e['present'] else ''}{e['title']}  ({e['slug']})  — {e['author']}")
            return _lib_out(args, out, True, lines)
        if cmd == "show":
            out = hb.show_game(env, args.slug)
            if not out.get("listed"):
                return _lib_out(args, out, True, [out["message"]])
            lines = [f"{out['title']} ({out['slug']})", f"Tác giả: {out['author']}",
                     f"Giấy phép: {out['license']}" + (f" (tài nguyên: {out['license_assets']})" if out.get("license_assets") else ""),
                     f"Hệ máy: {out['folder']}", f"Nguồn: {out['url']}", f"Trang: {out['repository'] or out['website'] or '—'}",
                     out["description"] or "(không có mô tả)"]
            if out.get("license_file"):
                lines.append(f"Ghi chú giấy phép đã lưu: {out['license_file']}")
            return _lib_out(args, out, True, lines)
        if cmd == "jobs":
            out = {"success": True, "jobs": [j for j in ls.list_jobs(env)["jobs"] if j.get("node") == hb.JOB_NODE]}
            return _lib_out(args, out, True, [f"{j['job_id']}  {j['state']}  {j['percent']}%  {j['files_done']}/{j['files']} file  {', '.join(j['names'][:2])}"
                                              for j in out["jobs"]] or ["Không có job Homebrew dở."])
        if cmd == "fetch" and args.dry_run:
            pl = hb.prepare(env, args.slugs, args.card)
            lines = [pl["message"]] + [f"{'!!' if f['problem'] else '+'} {f['path']} -> {f['system']}  {_mb(f['size'])}{'  — ' + f['problem'] if f['problem'] else ''}"
                                       for f in pl["files"]]
            lines.append(f"Tổng {_mb(pl['total_bytes'])}; cần ~{_mb(pl['needed_bytes'])} trên thẻ đích.")
            return _lib_out(args, pl, True, lines)
        gate = _gate(args)
        what = {"fetch": "Không tải/cài gì.", "resume": "Không tải tiếp.", "cancel": "Không huỷ job."}.get(cmd, "Không làm gì.")
        if gate is None:
            return _refuse_noninteractive(args, f"homebrew {cmd}", what)
        if not gate:
            return _decline(args, what)
        if cmd == "cancel":
            return _lib_out(args, ls.cancel_job(env, args.job), True)
        if cmd == "fetch":
            res = hb.fetch(env, args.slugs, args.card, _lib_progress_cb(args), args.conflict)
        else:
            res = ls.resume(env, args.job, _lib_progress_cb(args), args.conflict)
        res["rescan"] = _auto_rescan(args, bool(res.get("changed")))
        return _lib_out(args, res, bool(res.get("success")), _lib_fetch_text(res) if res.get("started") else [res["message"]])
    except ls.LibError as e:
        return _lib_out(args, {"success": False, "error_kind": e.kind, "message": e.message}, False, [f"[LỖI] {e.message}"])



def _web_open_text(r) -> list:
    if r["kind"] == "file":
        return [f"{r['name']}  ({r['size'] if r['size'] is not None else '?'} byte, {r['content_type'] or '?'}) — đây là file, dùng 'web fetch {r['url']}' để tải."]
    c = r["counts"]
    out = [f"{r['title']}  ({r['host']})", f"{c['files']} file, {c['pages']} trang cùng trang web, {c['external']} link ngoài."
           + (" (Danh sách bị cắt bớt.)" if r["truncated"] else "")]
    if r["dropped_insecure"]:
        out.append(f"Đã bỏ {r['dropped_insecure']} link http:// không mã hoá.")
    out += [f"{'[file] ' if x['file'] else ''}{x['text']}  {x['url']}" for x in r["links"][:30]]
    out += [f"[tìm] {x['label']}: {x['template']}" for x in r["forms"]]
    return out


def _cmd_web(args, probe) -> int:
    """Tải từ web (engine/web.py). home/open/jobs chỉ đọc (open ghi lịch sử trong app_data); bookmark/history chỉ ghi app_data;
    fetch/resume/cancel cần --yes (hoặc xác nhận TTY); fetch --dry-run chỉ hỏi kích thước và lập kế hoạch."""
    wb, ls = web_mod, libsource_mod
    cmd = args.web_command
    if probe.userdata_path is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate (đặt --userdata hoặc $USERDATA_PATH).")
    env = ls.Env(probe.userdata_path, {"primary": probe.primary_path, "secondary": probe.secondary_path})
    try:
        if cmd == "home":
            out = wb.home(env)
            return _lib_out(args, out, True, [f"{len(out['bookmarks'])} dấu trang, {len(out['history'])} trang gần đây, {len(out['inbox'])} link trong Import/links.txt, {len(out['jobs'])} job dở."])
        if cmd == "open":
            out = wb.open_url(env.app_data, args.url, wb.Session())
            return _lib_out(args, out, True, _web_open_text(out))
        if cmd == "thumbs":
            out = wb.fetch_thumbs(env.app_data, first=args.first)
            return _lib_out(args, out, True, [out["message"]])
        if cmd == "jobs":
            out = {"success": True, "jobs": [j for j in ls.list_jobs(env)["jobs"] if j.get("node") == wb.JOB_NODE]}
            return _lib_out(args, out, True, [f"{j['job_id']}  {j['state']}  {j['percent']}%  {j['files_done']}/{j['files']} file  {', '.join(j['names'][:2])}"
                                              for j in out["jobs"]] or ["Không có job tải từ web dở."])
        if cmd == "bookmark":
            if args.web_bookmark_command == "add":
                out = wb.add_bookmark(env.app_data, args.url, args.title or "")
            else:
                out = wb.remove_bookmark(env.app_data, args.url)
            return _lib_out(args, out, True)
        if cmd == "history":
            return _lib_out(args, wb.clear_history(env.app_data), True)
        if cmd == "fetch" and args.dry_run:
            pl = wb.prepare(env, args.urls, args.card, wb.Session())
            lines = [pl["message"]] + [f"{'!!' if f['problem'] else '+'} {f['name']} -> {f['system']}  {_mb(f['size'])}{'  — ' + f['problem'] if f['problem'] else ''}"
                                       for f in pl["files"]]
            lines.append(f"Tổng {_mb(pl['total_bytes'])}; cần ~{_mb(pl['needed_bytes'])} trên thẻ đích.")
            return _lib_out(args, pl, True, lines)
        gate = _gate(args)
        what = {"fetch": "Không tải/cài gì.", "resume": "Không tải tiếp.", "cancel": "Không huỷ job."}.get(cmd, "Không làm gì.")
        if gate is None:
            return _refuse_noninteractive(args, f"web {cmd}", what)
        if not gate:
            return _decline(args, what)
        if cmd == "cancel":
            return _lib_out(args, ls.cancel_job(env, args.job), True)
        if cmd == "fetch":
            res = wb.fetch(env, args.urls, args.card, _lib_progress_cb(args), args.conflict, wb.Session(),
                           from_page=getattr(args, "from_page", None), cover_url=getattr(args, "cover_url", None))
        else:
            res = ls.resume(env, args.job, _lib_progress_cb(args), args.conflict, wb.Session())
        res["rescan"] = _auto_rescan(args, bool(res.get("changed")))
        return _lib_out(args, res, bool(res.get("success")), _lib_fetch_text(res) if res.get("started") else [res["message"]])
    except ls.LibError as e:
        return _lib_out(args, {"success": False, "error_kind": e.kind, "message": e.message}, False, [f"[LỖI] {e.message}"])


def _cmd_patch(args, probe) -> int:
    """Patch ROM (engine/patcher.py). list/info/candidates chỉ đọc; store chỉ ghi kho patch trong app_data; apply tạo file ROM MỚI cạnh
    bản gốc (cần --yes hoặc xác nhận TTY, rồi tự Rescan); delete (chỉ patch trong kho) cần --yes."""
    pm, ls = patch_mod, libsource_mod
    cmd = args.patch_command
    if probe.userdata_path is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate (đặt --userdata hoặc $USERDATA_PATH).")
    env = ls.Env(probe.userdata_path, {"primary": probe.primary_path, "secondary": probe.secondary_path})
    try:
        if cmd == "list":
            out = {"success": True, "patches": pm.list_patches(env)}
            return _lib_out(args, out, True, [f"{p['id']}  {p['format']}  {_mb(p['size'])}  {p['where']}{'  [LỖI: ' + p['error'] + ']' if p.get('error') else ''}"
                                              for p in out["patches"]] or ["Chưa có patch nào."])
        if cmd == "info":
            out = pm.get_info(env, args.id)
            return _lib_out(args, out, True, [f"{out['id']}  {out['format']}  {_mb(out['size'])}",
                                              f"ROM gốc cần: CRC32 {out['source_crc32']}, {out['source_size']} byte" if out["source_crc32"] else "IPS: không có mã kiểm tra ROM gốc."]
                            + ([out["metadata"]] if out.get("metadata") else []) + ([f"LỖI: {out['error']}"] if out.get("error") else []))
        if cmd == "candidates":
            out = pm.candidates(env, args.id, args.system)
            if out["verify"] == "crc":
                lines = [f"{len(out['matches'])} ROM khớp (đã băm {out['scanned']} file)."] + [f"{m['card']}  {m['system']}  {m['name']}" for m in out["matches"]]
            elif "systems" in out:
                lines = [f"{x['system']}: {x['count']} ROM" for x in out["systems"]] or ["Không có ROM nào."]
            else:
                lines = [f"{m['card']}  {m['name']}" for m in out["roms"]] or ["Không có ROM nào."]
            return _lib_out(args, out, True, lines)
        if cmd == "store":
            names = pm.store_patch_files(env.app_data, args.path)
            return _lib_out(args, {"success": True, "stored": names, "message": f"Đã lưu {len(names)} patch."}, True)
        gate = _gate(args)
        what = {"apply": "Không tạo file nào.", "delete": "Không xoá patch."}.get(cmd, "Không làm gì.")
        if gate is None:
            return _refuse_noninteractive(args, f"patch {cmd}", what)
        if not gate:
            return _decline(args, what)
        if cmd == "delete":
            return _lib_out(args, pm.delete_patch(env, args.id), True)
        res = pm.apply(env, args.id, args.rom, args.name, args.header_skipped)
        res["rescan"] = _auto_rescan(args, bool(res.get("changed")))
        lines = [res["message"]] + ([res["warning"]] if res.get("warning") else [])
        return _lib_out(args, res, True, lines)
    except ls.LibError as e:
        return _lib_out(args, {"success": False, "error_kind": e.kind, "message": e.message}, False, [f"[LỖI] {e.message}"])


def _cmd_leafconfig(args, probe) -> int:
    """Sao lưu cấu hình Leaf/RetroArch (engine/leafconfig.py): snapshot | list | diff ID (chỉ đọc) | restore ID --group ... (cần --yes)."""
    lc, cmd = leafconfig_mod, args.leafconfig_command
    if probe.userdata_path is None or not probe.primary_path:
        return _usage(args, "Không xác định được thẻ Leaf / thư mục userdata của LoongMate.")
    ud, sd, plat = probe.userdata_path, probe.primary_path, os.environ.get("PLATFORM") or "mlp1"
    try:
        if cmd == "list":
            snaps = lc.list_snapshots(ud)
            try:
                agent_st = json.loads((Path(ud) / "agent_state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                agent_st = {}
            upd = agent_st.get("leaf_updated") if isinstance(agent_st, dict) else None
            return _lib_out(args, {"success": True, "snapshots": snaps, "current_leaf_version": lc.leaf_version(sd, plat),
                                   "leaf_updated": upd if isinstance(upd, dict) else None}, True,
                            [f"{x['id']}  Leaf {x['leaf_version'] or '?'}  {x['files']} file  {x['label']}" for x in snaps]
                            or ["Chưa có bản sao lưu cấu hình nào."])
        if cmd == "snapshot":
            r = lc.snapshot(ud, sd, plat, label=args.label or "manual", force=args.force)
            return _lib_out(args, r, r["success"])
        if cmd == "diff":
            r = lc.diff(ud, sd, plat, args.id)
            lines = [r["message"]]
            for c in r["changed"]:
                lines.append(f"~ {c['path']}")
                lines += [f"    {k['key']}: {k['old']!r} -> {k['new']!r}" for k in c["keys"]]
            lines += [f"+ {a['path']}" for a in r["added"]] + [f"- {x['path']}" for x in r["removed"]]
            return _lib_out(args, r, True, lines)
        gate = _gate(args)
        if gate is None:
            return _refuse_noninteractive(args, "leafconfig restore", "Không ghi gì.")
        if not gate:
            return _decline(args, "Không ghi gì.")
        r = lc.restore(ud, sd, plat, args.id, args.group)
        return _lib_out(args, r, r["success"])
    except lc.ConfigError as e:
        return _lib_out(args, {"success": False, "message": str(e)}, False)


def _autoresume_info(probe) -> dict:
    """Trạng thái tự tiếp tục qua Agent cho màn hình: bật/tắt, Agent chạy không, Leaf này còn tương thích không, lần gần nhất."""
    app = probe.userdata_path
    if app is None:
        return {"agent_resume": False, "agent_running": False, "agent_compat": None, "agent_last": None}
    leaf_log = agent_mod._leaf_log_path(argparse.Namespace(leaf_log=None), probe)
    return {"agent_resume": autoresume_mod.enabled(app), "agent_running": bool(agent_mod.lock_held(app)),
            "agent_compat": autoresume_mod.compat(leaf_log), "agent_last": _read_agent_state(app).get("autoresume_last")}


def _cmd_autoresume(args, probe) -> int:
    """Tự tiếp tục khi mở từ Games/Favorites qua LoongMate Agent (engine/autoresume.py), không vá launcher."""
    if probe.userdata_path is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate.")
    ar, cmd = autoresume_mod, args.autoresume_command
    if cmd == "status":
        info = _autoresume_info(probe)
        on, c = info["agent_resume"], info["agent_compat"]
        return _lib_out(args, {"success": True, "enabled": on, **info},
                        True, [f"Tự tiếp tục khi mở từ Games/Favorites (qua Agent): {'BẬT' if on else 'TẮT'}",
                               f"LoongMate Agent: {'đang chạy' if info['agent_running'] else 'CHƯA CHẠY'}",
                               f"Leaf: {c['message']}"])
    try:
        r = ar.set_enabled(probe.userdata_path, cmd == "on")
    except OSError as e:
        return _lib_out(args, {"success": False, "message": f"Không ghi được cấu hình: {e}"}, False)
    return _lib_out(args, r, True)


ONBOARDING_NAME = "onboarding.json"


def _cmd_onboarding(args, probe) -> int:
    """Màn Bắt đầu: trạng thái thật của từng bước (Agent, tự tiếp tục, khoá, sao lưu lần đầu, quét thư viện); done/reset."""
    app = probe.userdata_path
    if app is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate.")
    flag = Path(app) / ONBOARDING_NAME
    cmd = args.onboarding_command
    if cmd in ("done", "reset"):
        try:
            if cmd == "done":
                _write_json_atomic(flag, {"done": True, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
            elif flag.exists():
                flag.unlink()
        except OSError as e:
            return _lib_out(args, {"success": False, "message": f"Không ghi được: {e}"}, False)
        return _lib_out(args, {"success": True, "done": cmd == "done"}, True)
    try:
        done = bool(json.loads(flag.read_text(encoding="utf-8")).get("done"))
    except (OSError, ValueError, AttributeError):
        done = False
    snaps = 0
    if probe.primary_path is not None:
        try:
            snaps = len(SaveVault(probe.primary_path, probe.secondary_path, app).list_snapshots())
        except Exception:
            snaps = 0
    st = {"success": True, "done": done, "agent_running": bool(agent_mod.lock_held(app)),
          "agent_installed": probe.primary_path is not None
          and (Path(probe.primary_path) / "Apps" / "mlp1" / "LoongMateAgent.pak").is_dir(),
          "autoresume": autoresume_mod.enabled(app), "pocketlock": pocketlock_mod.enabled(app),
          "snapshots": snaps, "library_scanned": (Path(app) / "cache" / "last_audit.json").is_file()}
    yn = lambda v: "có" if v else "chưa"  # noqa: E731
    return _lib_out(args, st, True, [f"Agent chạy: {yn(st['agent_running'])}; tự tiếp tục: {yn(st['autoresume'])}; "
                                     f"khoá: {yn(st['pocketlock'])}; snapshot: {snaps}; đã quét: {yn(st['library_scanned'])}"])


def _cmd_pocketlock(args, probe) -> int:
    """Khoá khi thức dậy (chống bấm trong túi): LoongMate Agent giữ tay cầm ảo tới khi giữ Select + Start 1 giây."""
    app = probe.userdata_path
    if app is None:
        return _usage(args, "Không xác định được thư mục userdata của LoongMate.")
    if args.pocketlock_command == "status":
        on, running = pocketlock_mod.enabled(app), bool(agent_mod.lock_held(app))
        return _lib_out(args, {"success": True, "enabled": on, "agent_running": running},
                        True, [f"Khoá khi thức dậy: {'BẬT' if on else 'TẮT'} (mở: giữ Select + Start 1 giây)",
                               f"LoongMate Agent: {'đang chạy' if running else 'CHƯA CHẠY'}"])
    try:
        r = pocketlock_mod.set_enabled(app, args.pocketlock_command == "on")
    except OSError as e:
        return _lib_out(args, {"success": False, "message": f"Không ghi được cấu hình: {e}"}, False)
    return _lib_out(args, r, True)


def _cmd_hottabs(args, probe) -> int:
    """Leaf Hot Tabs: status (chỉ đọc) | on | off | install | uninstall. Việc thay launcher do hottabs.sh làm."""
    sd = probe.primary_path
    ud = hottabs_mod.userdata_for(sd, os.environ.get("USERDATA_PATH"))
    cmd = args.hottabs_command
    if cmd == "status":
        st = hottabs_mod.status(sd, ud)
        st.update(_autoresume_info(probe))
        if args.json:
            _print_json(st)
        else:
            print(f"Launcher: {st['installed']} — {st['message']}")
            print(f"Leaf: {st['leaf_version'] or '?'} (launcher commit {st['launcher_commit'] or '?'}); bộ cài: {st['bundle_version'] or 'không có'}"
                  f"{'' if st['bundle_matches'] else ' (KHÔNG khớp)'}")
            print(f"Tự tiếp tục khi mở từ Games/Favorites: {'BẬT' if st['resume_from_lists'] else 'TẮT'}; bản sao lưu: {'có' if st['backup_present'] else 'không'}")
        return EXIT_OK
    if cmd in ("on", "off"):
        try:
            r = hottabs_mod.set_resume(ud, cmd == "on")
        except OSError as e:
            msg = f"Không ghi được file cờ: {e}"
            if args.json:
                _print_json({"success": False, "message": msg})
            else:
                print(f"[LỖI] {msg}")
            return EXIT_TASK_FAILED
        msg = f"Tự tiếp tục khi mở từ Games/Favorites: {'BẬT' if r['resume_from_lists'] else 'TẮT'} (có hiệu lực ngay lần mở game kế tiếp)."
        if args.json:
            _print_json({"success": True, "message": msg, **r})
        else:
            print(msg)
        return EXIT_OK
    # install / uninstall
    if hottabs_mod.installer_path(sd) is None:
        return _usage(args, f"Không có {Path(sd) / 'hottabs' / 'hottabs.sh'} trên thẻ: chép thư mục bộ cài hottabs/ vào thẻ trước.")
    prev = hottabs_mod.run_installer(sd, cmd, dry_run=True)
    gate = None if not prev["ok"] else _gate_preview(args, prev)
    if not prev["ok"] or gate is not True:
        msg = ("Bộ cài từ chối: " if not prev["ok"] else "Chưa xác nhận: " if gate is None else "Đã huỷ: ") + "không đổi gì."
        if args.json:
            _print_json({"success": False, "preview": True, "installer_ok": prev["ok"], "message": msg, "output": prev["output"],
                         "returncode": prev["returncode"]})
        else:
            print(prev["output"])
            print(f"[{'LỖI' if not prev['ok'] else 'LoongMate'}] {msg}" +
                  (" Chạy lại với '--yes' để thực hiện." if prev["ok"] and gate is None else ""))
        return EXIT_TASK_FAILED
    r = hottabs_mod.run_installer(sd, cmd, dry_run=False)
    if args.json:
        _print_json({"success": r["ok"], "message": (hottabs_mod.RESTART_NOTE + "." if r["ok"] else "Bộ cài báo lỗi."),
                     "output": r["output"], "returncode": r["returncode"], "reboot_required": r["ok"]})
    else:
        print(r["output"])
        print(f"[XONG] {hottabs_mod.RESTART_NOTE}." if r["ok"] else "[LỖI] Bộ cài báo lỗi (xem trên).")
    return EXIT_OK if r["ok"] else EXIT_TASK_FAILED


def _gate_preview(args, prev) -> "bool | None":
    """--yes: tiếp tục luôn. Có TTY: in bản xem trước (dry-run) rồi hỏi. Không có cả hai: None (chỉ xem trước)."""
    if args.yes:
        return True
    if not sys.stdin.isatty() or args.json:
        return None
    print(prev["output"])
    return _confirm("Xác nhận thực hiện?")


class _Sink:
    """Nhận mọi lời gọi dựng parser của lệnh KHÔNG được chọn và bỏ đi (xem build_parser(only=...))."""

    def __getattr__(self, name):
        return self

    def __call__(self, *a, **k):
        return self


_GLOBAL_VALUE_OPTS = ("--primary", "--secondary", "--userdata")


def _peek_command(argv):
    """Lệnh con mà argv sẽ chọn, hoặc None nếu không chắc (có -h, lệnh lạ, không có lệnh) — khi đó dựng đủ parser.
    Dựng 47 parser con tốn ~0,15 s trên máy mà mỗi lần chỉ dùng một."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("-h") or a.startswith("--h"):
            return None
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            takes_value = len(name) > 2 and any(g.startswith(name) for g in _GLOBAL_VALUE_OPTS)
            i += 2 if takes_value and "=" not in a else 1
            continue
        if a.startswith("-") and a != "-":
            i += 1
            continue
        return a if a in _COMMANDS else None
    return None


def build_parser(only: "str | None" = None) -> argparse.ArgumentParser:
    """only: chỉ dựng lệnh con này (các lệnh khác bị bỏ qua) — dùng cho đường chạy thật để khởi động nhanh;
    None = dựng đủ (--help, test)."""
    parser = argparse.ArgumentParser(
        description="LoongMate — Pocket 1 Care & Sync (Leaf User-Space Maintenance Suite)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG
    )
    parser.add_argument("--primary", help="Path to primary SD card root (defaults to $SDCARD_PATH or /mnt/SDCARD)")
    parser.add_argument("--secondary", help="Path to secondary SD card root (defaults to $SECONDARY_SDCARD_PATH or /mnt/SDCARD2)")
    parser.add_argument("--userdata", help="Path to LoongMate userdata (defaults to $USERDATA_PATH)")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--quiet", action="store_true", help="Suppress the ASCII banner on text output")
    parser.add_argument("--no-rescan", action="store_true", dest="no_rescan",
                        help="Do not ask Jawaka to Rescan Library after a write (just print the manual reminder)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    if only is not None:
        _real_add_parser = subparsers.add_parser
        subparsers.add_parser = lambda name, **kw: _real_add_parser(name, **kw) if name == only else _Sink()

    subparsers.add_parser("readiness", help="Check device readiness (Battery, Thermal, SD mounts, Network, SaveVault, Library)")

    scan_parser = subparsers.add_parser("scan", help="Scan ROM library, artwork, playlists, and run Library Doctor audit")
    scan_parser.add_argument("--fix", action="store_true", help="Apply safe fixes after listing them and confirming")
    scan_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt for --fix")

    backup_parser = subparsers.add_parser("backup", help="Perform incremental local snapshot of saves and states into SaveVault")
    backup_parser.add_argument("--endpoint", help="Push the new snapshot to this configured endpoint after creating it")

    restore_parser = subparsers.add_parser("restore", help="List snapshots or restore saves safely")
    restore_parser.add_argument("--snapshot", help="ID of the snapshot to restore (defaults to latest)")
    restore_parser.add_argument("--dry-run", action="store_true", help="Preview restore diff without modifying files")
    restore_parser.add_argument("--file", action="append", help="Restore only this rel_path (repeatable); omit for a full restore")
    restore_parser.add_argument("--yes", action="store_true", help="Skip the full-restore confirmation prompt")

    subparsers.add_parser("health", help="Run device diagnostic tests (userdata R/W, directories, mounts)")

    bundle_parser = subparsers.add_parser("bundle", help="Export sanitized diagnostic support bundle ZIP")
    bundle_parser.add_argument("--no-privacy", action="store_true", help="Do not anonymize ROM titles in support bundle")

    verify_parser = subparsers.add_parser("verify", help="Re-hash a snapshot's stored files and check them against its manifest")
    verify_parser.add_argument("--snapshot", help="ID of the snapshot to verify (defaults to latest)")

    recover_parser = subparsers.add_parser("recover", help="Show / resolve an interrupted (crashed) restore")
    recover_parser.add_argument("--rollback", action="store_true", help="Roll back to the pre-restore snapshot")
    recover_parser.add_argument("--yes", action="store_true", help="Skip the rollback confirmation prompt")

    endpoint_parser = subparsers.add_parser("endpoint", help="Manage remote backup endpoints (SFTP / local folder)")
    endpoint_sub = endpoint_parser.add_subparsers(dest="endpoint_command")

    ep_add = endpoint_sub.add_parser("add", help="Add or overwrite an endpoint")
    ep_add.add_argument("--name", required=True)
    ep_add.add_argument("--type", required=True, choices=["sftp", "local"])
    ep_add.add_argument("--host")
    ep_add.add_argument("--port", type=int, default=22)
    ep_add.add_argument("--user")
    ep_add.add_argument("--remote-path", dest="remote_path")
    ep_add.add_argument("--key", dest="key_path", help="Path to the SSH private key (sftp only)")

    endpoint_sub.add_parser("list", help="List configured endpoints")

    ep_remove = endpoint_sub.add_parser("remove", help="Remove an endpoint")
    ep_remove.add_argument("--name", required=True)

    ep_test = endpoint_sub.add_parser("test", help="Test connectivity to an endpoint")
    ep_test.add_argument("--name", required=True)

    ep_pin = endpoint_sub.add_parser("pin", help="Fetch and pin an sftp endpoint's host key")
    ep_pin.add_argument("--name", required=True)
    ep_pin.add_argument("--expected-sha256", dest="expected_sha256")
    ep_pin.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    push_parser = subparsers.add_parser("push", help="Push a local snapshot to a remote endpoint")
    push_parser.add_argument("--endpoint", required=True)
    push_parser.add_argument("--snapshot", help="Defaults to the latest local snapshot")

    pull_parser = subparsers.add_parser("pull", help="Pull a snapshot from a remote endpoint into the local vault")
    pull_parser.add_argument("--endpoint", required=True)
    pull_parser.add_argument("--snapshot", required=True)

    def _json_flag(p):
        # default=SUPPRESS so a missing sub-level --json does not overwrite the global one.
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output results in JSON format")

    def _norescan_flag(p):
        p.add_argument("--no-rescan", action="store_true", default=argparse.SUPPRESS, dest="no_rescan",
                       help="Do not auto-rescan the library after writing")

    dd = subparsers.add_parser("dedupe", help="Find byte-identical duplicate ROMs (SHA-256) and propose which to keep; --apply deletes")
    dd.add_argument("--apply", action="store_true", help="Delete the proposed duplicates after confirming")
    dd.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    dd.add_argument("--quarantine", action="store_true", help="Move to Quarantine instead of deleting (reversible)")
    dd.add_argument("--max-hash-mb", type=float, dest="max_hash_mb", help="Cap on total MB hashed (battery); groups beyond it are skipped")
    dd.add_argument("--prefer-card", choices=["auto", "primary", "secondary"], default="auto", dest="prefer_card",
                    help="Which card's copy to keep (save/state and playlist-referenced copies always win)")
    dd.add_argument("--compare", action="store_true", help="Only print reclaimable MB per card for auto/primary/secondary")
    dd.add_argument("--copy-artwork", action="store_true", dest="copy_artwork",
                    help="Before deleting, copy the cover art to the kept copy's card if it lacks one")
    dd.add_argument("--plan-out", dest="plan_out", metavar="FILE", help="Preview only: also write the approved-plan JSON to FILE")
    dd.add_argument("--plan", metavar="FILE", help="With --apply: do exactly (and only) the items in this plan file; no rescan/rehash to find more")
    _json_flag(dd)
    _norescan_flag(dd)

    rom_parser = subparsers.add_parser("rom", help="ROM management")
    rom_sub = rom_parser.add_subparsers(dest="rom_command")
    rd = rom_sub.add_parser("delete", help="Delete one or more games (with their tracks/discs/artwork); saves are kept")
    rd.add_argument("path", nargs="+", help="Full path of each game's ROM file (.cue/.m3u for multi-file games)")
    rd.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    rd.add_argument("--keep-artwork", action="store_true", help="Keep the cover image")
    _json_flag(rd)
    _norescan_flag(rd)

    qp = subparsers.add_parser("quarantine", help="List or permanently purge quarantined files")
    q_sub = qp.add_subparsers(dest="quarantine_command")
    ql = q_sub.add_parser("list", help="List quarantined files")
    _json_flag(ql)
    qg = q_sub.add_parser("purge", help="Permanently delete quarantined files")
    qg.add_argument("--older-than", type=float, dest="older_than", metavar="DAYS")
    qg.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(qg)
    _norescan_flag(qg)

    mv = subparsers.add_parser("move", help="Move a game (or, with --bulk, enough games) to the other SD card")
    mv.add_argument("rom", nargs="?", help="Full path of the game's ROM file (.cue/.m3u for multi-file games)")
    mv.add_argument("--to", required=True, choices=["primary", "secondary"], help="Destination card")
    mv.add_argument("--bulk", action="store_true", help="Pick games automatically until the source card has --target-free-mb free")
    mv.add_argument("--target-free-mb", type=int, dest="target_free_mb", help="Free MB wanted on the source card (--bulk)")
    mv.add_argument("--system", help="Limit --bulk to one system folder (e.g. PSX)")
    mv.add_argument("--apply", action="store_true", help="Actually move (default: only show the plan)")
    mv.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    mv.add_argument("--plan-out", dest="plan_out", metavar="FILE", help="Preview only: also write the approved-plan JSON to FILE")
    mv.add_argument("--plan", metavar="FILE", help="With --apply: move exactly (and only) the games in this plan file")
    _json_flag(mv)
    _norescan_flag(mv)

    fx = subparsers.add_parser("fix", help="One-tap batch fixes (cue refs, m3u, legacy folders) with undo")
    fx_sub = fx.add_subparsers(dest="fix_command")
    fb = fx_sub.add_parser("batch", help="Show the batch-fix plan; --apply to run it")
    fb.add_argument("--apply", action="store_true")
    kinds = batchfix_mod.KIND_ORDER if only in (None, "fix") else ()   # tránh nạp batchfix khi lệnh khác
    fb.add_argument("--only", nargs="+", choices=kinds, metavar="KIND",
                    help="Only these step kinds: " + ", ".join(kinds))
    fb.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    fb.add_argument("--plan-out", dest="plan_out", metavar="FILE", help="Preview only: also write the approved-plan JSON to FILE")
    fb.add_argument("--plan", metavar="FILE", help="With --apply: do exactly (and only) the steps in this plan file")
    _json_flag(fb)
    _norescan_flag(fb)
    fu = fx_sub.add_parser("undo", help="Undo the most recent batch fix")
    fu.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    fu.add_argument("--check", action="store_true", help="Read-only: is there a batch to undo, which one, how many operations")
    _json_flag(fu)
    _norescan_flag(fu)

    bp = subparsers.add_parser("bios", help="Check BIOS files per system (md5)")
    _json_flag(bp)

    sp_ = subparsers.add_parser("snapshots", help="List all SaveVault snapshots (read-only)")
    _json_flag(sp_)

    rp_ = subparsers.add_parser("roms", help="List ROM files from the inventory (read-only)")
    rp_.add_argument("--system", help="Only this system folder/alias (e.g. PSX)")
    rp_.add_argument("--card", choices=["primary", "secondary"], help="Only this card")
    rp_.add_argument("--sort", choices=["name", "size"], help="name = by file name; size = largest first (default: card, system, name)")
    rp_.add_argument("--save", choices=["any", "with", "without"], default="any", help="Only games that have / lack a same-name save")
    rp_.add_argument("--query", help="Only names containing TEXT (case- and Vietnamese-accent-insensitive)")
    rp_.add_argument("--limit", type=int, help="Keep only the first N rows after filtering/sorting")
    _json_flag(rp_)

    sv = subparsers.add_parser("saves", help="Save theo từng game: list | history KEY | restore KEY --snapshot ID")
    sv_sub = sv.add_subparsers(dest="saves_command")
    _json_flag(sv_sub.add_parser("list", help="Games with a current save/state or one in a snapshot (read-only)"))
    svh = sv_sub.add_parser("history", help="Versions of one game's saves across snapshots (read-only)")
    svh.add_argument("key", help="Game key from 'saves list' (lowercase name without extension)")
    _json_flag(svh)
    svr = sv_sub.add_parser("restore", help="Restore ONLY this game's files from a snapshot (preview unless --yes)")
    svr.add_argument("key")
    svr.add_argument("--snapshot", required=True, help="Snapshot ID from 'saves history'")
    svr.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(svr)

    hp = subparsers.add_parser("history", help="Battery / temperature history summary")
    hp.add_argument("--days", type=float, default=7, help="Window in days (default 7)")
    _json_flag(hp)

    bt = subparsers.add_parser("battery", help="Battery health estimate (full-charge vs design capacity, trend)")
    bt.add_argument("--days", type=float, default=90, help="Telemetry window in days (default 90)")
    _json_flag(bt)

    up = subparsers.add_parser("update", help="Update LoongMate paks from a hashed bundle: status | verify | apply | rollback")
    up_sub = up.add_subparsers(dest="update_command")
    def _apps_flag(p):
        p.add_argument("--apps-dir", default=argparse.SUPPRESS, help="Directory holding the paks (default <primary>/Apps/mlp1; use a sandbox to test)")

    st_p = up_sub.add_parser("status", help="Installed version, bundles found on the card, backups")
    _json_flag(st_p)
    _apps_flag(st_p)
    ul_p = up_sub.add_parser("list", help="Read-only: bundles in <SDCARD>/LoongMate-update (version, date, files, installed?)")
    ul_p.add_argument("--verify", action="store_true", help="Also SHA-256 verify every bundle (slow)")
    _json_flag(ul_p)
    for name, hlp in (("verify", "Check a bundle (SHA-256 of every file) without writing"),
                      ("apply", "Verify then install a bundle (backs up the running paks first)"),
                      ("rollback", "Restore the paks from the newest backup")):
        sp = up_sub.add_parser(name, help=hlp)
        if name != "rollback":
            sp.add_argument("bundle", nargs="?", help="Bundle folder (default: the only one in <SDCARD>/LoongMate-update/)")
        if name != "verify":
            sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
            _apps_flag(sp)
        _json_flag(sp)

    ap = subparsers.add_parser("agent", help="Background agent status")
    ap_sub = ap.add_subparsers(dest="agent_command")
    aps = ap_sub.add_parser("status", help="Is the agent running; last sample / backup")
    _json_flag(aps)

    nd = subparsers.add_parser("nodes", help="Tailnet nodes: list / ping / probe / wake / favorites / MAC")
    nd_sub = nd.add_subparsers(dest="nodes_command")
    _json_flag(nd_sub.add_parser("list", help="List nodes"))
    n_ping = nd_sub.add_parser("ping", help="tailscale ping a node")
    n_ping.add_argument("name")
    _json_flag(n_ping)
    n_probe = nd_sub.add_parser("probe", help="Check which service ports a node answers on")
    n_probe.add_argument("name")
    n_probe.add_argument("--ports", type=int, nargs="+", help="Ports (default: 22 445 47989 3389)")
    _json_flag(n_probe)
    n_wake = nd_sub.add_parser("wake", help="Send a Wake-on-LAN packet (needs 'nodes mac' first)")
    n_wake.add_argument("name")
    _json_flag(n_wake)
    n_fav = nd_sub.add_parser("fav", help="Mark / unmark a favorite node")
    n_fav.add_argument("name")
    n_fav.add_argument("state", choices=["on", "off"])
    _json_flag(n_fav)
    n_mac = nd_sub.add_parser("mac", help="Store a node's MAC for Wake-on-LAN")
    n_mac.add_argument("name")
    n_mac.add_argument("mac")
    n_mac.add_argument("--broadcast", help="Broadcast address (default 255.255.255.255)")
    _json_flag(n_mac)

    rs = subparsers.add_parser("rescan", help="Ask Jawaka (Leaf) to Rescan Library now")
    _json_flag(rs)

    arp = subparsers.add_parser("autoresume", help="Resume from Games/Favorites via LoongMate Agent (no launcher patch): status | on | off")
    ar_sub = arp.add_subparsers(dest="autoresume_command")
    for name in ("status", "on", "off"):
        _json_flag(ar_sub.add_parser(name))

    obp = subparsers.add_parser("onboarding", help="First-run guide state: status | done | reset")
    ob_sub = obp.add_subparsers(dest="onboarding_command")
    for name in ("status", "done", "reset"):
        _json_flag(ob_sub.add_parser(name))

    plp = subparsers.add_parser("pocketlock", help="Lock input after wake (pocket mode), unlock by holding Select+Start: status | on | off")
    pl_sub = plp.add_subparsers(dest="pocketlock_command")
    for name in ("status", "on", "off"):
        _json_flag(pl_sub.add_parser(name))

    lcp = subparsers.add_parser("leafconfig", help="Leaf/RetroArch config backup: snapshot | list | diff ID | restore ID --group G (--yes)")
    lc_sub = lcp.add_subparsers(dest="leafconfig_command")
    sp = lc_sub.add_parser("snapshot", help="Back up retroarch.cfg, overrides/remaps, standalone configs, Leaf DBs (skips if unchanged)")
    sp.add_argument("--label", default="")
    sp.add_argument("--force", action="store_true", help="Create a snapshot even if nothing changed")
    _json_flag(sp)
    _json_flag(lc_sub.add_parser("list", help="Read-only: config snapshots, newest first"))
    sp = lc_sub.add_parser("diff", help="Read-only: compare a snapshot with the current config (changed keys)")
    sp.add_argument("id", metavar="ID")
    _json_flag(sp)
    sp = lc_sub.add_parser("restore", help="Write back selected groups from a snapshot (backs up current first; refused while a game runs)")
    sp.add_argument("id", metavar="ID")
    sp.add_argument("--group", action="append", choices=["retroarch", "overrides", "standalone"], required=True)
    sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(sp)

    ht = subparsers.add_parser("hottabs", help="Leaf Hot Tabs: status | on | off | install | uninstall (install/uninstall preview unless --yes)")
    ht_sub = ht.add_subparsers(dest="hottabs_command")
    for name, hlp in (("status", "Read-only: patched/stock/unknown/absent, resume flag, backup"),
                      ("on", "Turn ON auto-resume when opening from Games/Favorites (removes the flag file)"),
                      ("off", "Turn OFF auto-resume (creates the flag file)"),
                      ("install", "Install the Hot Tabs launcher via <SD>/hottabs/hottabs.sh (dry-run preview unless --yes)"),
                      ("uninstall", "Restore Leaf's stock launcher via hottabs.sh (dry-run preview unless --yes)")):
        sp = ht_sub.add_parser(name, help=hlp)
        if name in ("install", "uninstall"):
            sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
        _json_flag(sp)

    im = subparsers.add_parser("import", help="Local Importer: nhập game người dùng tự đưa vào (Import/ trên mỗi thẻ hoặc đường dẫn tuỳ ý)")
    im_sub = im.add_subparsers(dest="import_command")
    ims = im_sub.add_parser("scan", help="Read-only: list importable items, detected system, warnings (default: <thẻ>/Import/ of both cards)")
    ims.add_argument("paths", nargs="*", metavar="PATH", help="File, .zip or folder (default: Import/ of each card)")
    _json_flag(ims)
    imp = im_sub.add_parser("plan", help="Read-only: scan + target card + duplicate check; --plan-out writes the approved plan")
    imp.add_argument("paths", nargs="*", metavar="PATH")
    imp.add_argument("--card", choices=["auto", "primary", "secondary"], default="auto",
                     help="auto = secondary card if usable, otherwise ASK (never silently falls back to the primary card)")
    imp.add_argument("--select", action="append", metavar="ID", help="Only these unit ids (repeatable)")
    imp.add_argument("--choice", action="append", metavar="ID=SYSTEM", help="Pick the system for an ambiguous unit (repeatable)")
    imp.add_argument("--plan-out", dest="plan_out", metavar="FILE", help="Also write the approved-plan JSON to FILE")
    _json_flag(imp)
    ima = im_sub.add_parser("apply", help="Install exactly the plan file (staging -> verify -> commit); needs --yes")
    ima.add_argument("--plan", required=True, metavar="FILE")
    ima.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    ima.add_argument("--choice", action="append", metavar="ID=SYSTEM", help="System for an ambiguous unit (repeatable)")
    ima.add_argument("--conflict", action="append", metavar="ID=ACTION", help="Same name, different content: keep (default) | replace | copy | cancel")
    ima.add_argument("--bios-ok", dest="bios_ok", action="append", metavar="ID", help="Confirm installing this BIOS unit ('all' = every BIOS unit in the plan)")
    ima.add_argument("--delete-source", dest="delete_source", action="store_true",
                     help="Delete (instead of moving to Import/.done/) the sources in Import/ after a successful install")
    _json_flag(ima)
    _norescan_flag(ima)
    imu = im_sub.add_parser("unpack", help="Extract a .7z/.rar/.tar.*/.gz... in Import/ to Import/<name>/ (checks paths, size, links first); needs --yes")
    imu.add_argument("path", metavar="ARCHIVE")
    imu.add_argument("--yes", action="store_true")
    imu.add_argument("--progress", action="store_true", help="LMPROGRESS lines on stderr")
    imu.add_argument("--password", default=None, help="Password for an encrypted .7z/.rar")
    imu.add_argument("--auto-password", dest="auto_password", action="store_true",
                     help="Try the password hints (archive comment, file name, .txt next to it, Import/passwords.txt, "
                          "passwords that worked before); never brute force")
    _json_flag(imu)
    _json_flag(im_sub.add_parser("jobs", help="Read-only: unfinished import jobs (staging on the cards) and recent finished ones"))
    imr = im_sub.add_parser("recover", help="Clean unfinished jobs: staging removed, half-committed games rolled back")
    imr.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(imr)
    imm = im_sub.add_parser("mkdir", help="Create <card>/Import/ if missing (both cards by default)")
    imm.add_argument("--card", choices=["primary", "secondary"])
    _json_flag(imm)

    lb = subparsers.add_parser("library", help="Thư viện riêng: duyệt + tải + cài từ agent PocketBridge của BẠN (không có danh mục ROM nào)")
    lb_sub = lb.add_subparsers(dest="library_command")
    _json_flag(lb_sub.add_parser("nodes", help="Read-only: paired private libraries"))
    _json_flag(lb_sub.add_parser("overview", help="Read-only: nodes + unfinished jobs + pairing bundles in one call (used by the native UI)"))
    _json_flag(lb_sub.add_parser("bundles", help="Read-only: pairing bundle files in <card>/PocketBridge-import/ and <card>/Import/"))
    lbp = lb_sub.add_parser("pair", help="Import a pairing bundle (pins the fingerprint, exchanges the bootstrap token, saves the token, deletes the bundle)")
    lbp.add_argument("bundle", metavar="BUNDLE.json")
    lbp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(lbp)
    lbu = lb_sub.add_parser("unpair", help="Forget a paired library (token + cache removed)")
    lbu.add_argument("node")
    lbu.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(lbu)
    lbr = lb_sub.add_parser("roots", help="Read-only: the libraries the agent serves")
    lbr.add_argument("node")
    _json_flag(lbr)
    lbl = lb_sub.add_parser("list", help="Read-only (updates the index cache in app data): games of a library, filtered by systems.json")
    lbl.add_argument("node")
    lbl.add_argument("root")
    lbl.add_argument("--system", help="Only this system folder (e.g. GBA; '?' = unknown system)")
    lbl.add_argument("--query", help="Case-insensitive substring of the path")
    lbl.add_argument("--offline", action="store_true", help="Use the cached index only (never touches the network)")
    lbl.add_argument("--all", action="store_true", help="Include every entry in the output, not only the system summary")
    _json_flag(lbl)
    lbf = lb_sub.add_parser("fetch", help="Download + verify SHA-256 + install through the Local Importer; --dry-run only checks and plans")
    lbf.add_argument("node")
    lbf.add_argument("root")
    lbf.add_argument("paths", nargs="+", metavar="PATH", help="Path inside the library, exactly as listed")
    lbf.add_argument("--card", choices=["auto", "primary", "secondary"], default="auto",
                     help="auto = secondary card if usable, otherwise ASK (never silently falls back to the primary card)")
    lbf.add_argument("--conflict", choices=["keep", "replace", "copy", "cancel"], default="keep",
                     help="Same name, different content already in Roms/: keep (default) | replace | copy | cancel")
    lbf.add_argument("--dry-run", action="store_true", dest="dry_run", help="Ask the server for size+hash and plan; writes nothing")
    lbf.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    lbf.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(lbf)
    _norescan_flag(lbf)
    _json_flag(lb_sub.add_parser("jobs", help="Read-only: unfinished downloads"))
    lbs = lb_sub.add_parser("resume", help="Continue an interrupted download (Range + If-Range) and install")
    lbs.add_argument("job")
    lbs.add_argument("--conflict", choices=["keep", "replace", "copy", "cancel"], default="keep")
    lbs.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    lbs.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(lbs)
    _norescan_flag(lbs)
    lbc = lb_sub.add_parser("cancel", help="Delete an unfinished download job and its .part files")
    lbc.add_argument("job")
    lbc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(lbc)

    hb = subparsers.add_parser("homebrew", help="Homebrew Hub: tải TRỰC TIẾP game có giấy phép rõ ràng (HTTPS kiểm CA), cài bằng Nhập game")
    hb_sub = hb.add_subparsers(dest="homebrew_command")
    hbs = hb_sub.add_parser("sync", help="Download the whole catalog (<= 1 request/s), keep only clearly licensed entries; writes the cache in app data only")
    hbs.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(hbs)
    hbl = hb_sub.add_parser("list", help="Read-only (cache): clearly licensed games; summary per system unless --system/--query/--all")
    hbl.add_argument("--system", help="GB | GBC | GBA | NES")
    hbl.add_argument("--query", help="Case-insensitive substring of title, author or slug")
    hbl.add_argument("--all", action="store_true", help="Include every entry in the output")
    _json_flag(hbl)
    hbw = hb_sub.add_parser("show", help="Read-only: details of one entry (description, license, author, source); says why a rejected entry is hidden")
    hbw.add_argument("slug")
    _json_flag(hbw)
    _json_flag(hb_sub.add_parser("jobs", help="Read-only: unfinished Homebrew downloads"))
    hbf = hb_sub.add_parser("fetch", help="Download + verify catalog hash + install through the Local Importer; --dry-run only checks sizes and plans")
    hbf.add_argument("slugs", nargs="+", metavar="SLUG")
    hbf.add_argument("--card", choices=["auto", "primary", "secondary"], default="auto",
                     help="auto = secondary card if usable, otherwise ASK (never silently falls back to the primary card)")
    hbf.add_argument("--conflict", choices=["keep", "replace", "copy", "cancel"], default="keep")
    hbf.add_argument("--dry-run", action="store_true", dest="dry_run", help="Ask the server for sizes and plan; writes nothing")
    hbf.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    hbf.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(hbf)
    _norescan_flag(hbf)
    hbr = hb_sub.add_parser("resume", help="Continue an interrupted Homebrew download (Range + If-Range) and install")
    hbr.add_argument("job")
    hbr.add_argument("--conflict", choices=["keep", "replace", "copy", "cancel"], default="keep")
    hbr.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    hbr.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(hbr)
    _norescan_flag(hbr)
    hbc = hb_sub.add_parser("cancel", help="Delete an unfinished download job and its .part files")
    hbc.add_argument("job")
    hbc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(hbc)

    wb = subparsers.add_parser("web", help="Tải từ web: mở trang https bất kỳ (chỉ xem link/ô tìm kiếm, không JavaScript/cookie), tải file đã chọn, cài bằng Nhập game")
    wb_sub = wb.add_subparsers(dest="web_command")
    _json_flag(wb_sub.add_parser("home", help="Read-only: bookmarks, recent pages, links from Import/links.txt, unfinished web downloads"))
    wbo = wb_sub.add_parser("open", help="Load one https page and list its links/search forms; a non-HTML target is reported as a file (body not read). Records history")
    wbo.add_argument("url", metavar="URL")
    _json_flag(wbo)
    wbt = wb_sub.add_parser("thumbs", help="Download the cover thumbnails queued by 'web open' into the app cache "
                                           "(https only, PNG/JPEG/GIF, size-capped); run in the background by the UI")
    wbt.add_argument("--first", default=None, metavar="PAGE_URL", help="Do this page's covers first")
    _json_flag(wbt)
    _json_flag(wb_sub.add_parser("jobs", help="Read-only: unfinished web downloads"))
    wbf = wb_sub.add_parser("fetch", help="Download files + install through the Local Importer; --dry-run only checks sizes/types and plans")
    wbf.add_argument("urls", nargs="+", metavar="URL")
    wbf.add_argument("--card", choices=["auto", "primary", "secondary"], default="auto",
                     help="auto = secondary card if usable, otherwise ASK (never silently falls back to the primary card)")
    wbf.add_argument("--conflict", choices=["keep", "replace", "copy", "cancel"], default="keep")
    wbf.add_argument("--from-page", dest="from_page", help="Page the link came from: attach its matching cover art after install")
    wbf.add_argument("--cover-url", dest="cover_url", help="Cover image of the game page the user came through (https); tried first")
    wbf.add_argument("--dry-run", action="store_true", dest="dry_run", help="Ask the server for sizes/types and plan; writes nothing")
    wbf.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    wbf.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(wbf)
    _norescan_flag(wbf)
    wbr = wb_sub.add_parser("resume", help="Continue an interrupted web download (Range + If-Range) and install")
    wbr.add_argument("job")
    wbr.add_argument("--conflict", choices=["keep", "replace", "copy", "cancel"], default="keep")
    wbr.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    wbr.add_argument("--progress", action="store_true", help="Print LMPROGRESS lines to stderr")
    _json_flag(wbr)
    _norescan_flag(wbr)
    wbc = wb_sub.add_parser("cancel", help="Delete an unfinished download job and its .part files")
    wbc.add_argument("job")
    wbc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(wbc)
    wbb = wb_sub.add_parser("bookmark", help="Bookmarks (app data only)")
    wbb_sub = wbb.add_subparsers(dest="web_bookmark_command")
    wbba = wbb_sub.add_parser("add", help="Add (or update) a bookmark")
    wbba.add_argument("url", metavar="URL")
    wbba.add_argument("--title", help="Display title")
    _json_flag(wbba)
    wbbr = wbb_sub.add_parser("remove", help="Remove a bookmark")
    wbbr.add_argument("url", metavar="URL")
    _json_flag(wbbr)
    wbh = wb_sub.add_parser("history", help="Browsing history (app data only)")
    wbh_sub = wbh.add_subparsers(dest="web_history_command")
    _json_flag(wbh_sub.add_parser("clear", help="Forget the recent pages"))

    pt = subparsers.add_parser("patch", help="Patch ROM (.ips/.bps/.ups): tạo file ROM mới cạnh bản gốc (bản gốc không bị sửa)")
    pt_sub = pt.add_subparsers(dest="patch_command")
    _json_flag(pt_sub.add_parser("list", help="Read-only: patches in the store (app data) and in <card>/Import/"))
    pti = pt_sub.add_parser("info", help="Read-only: format, required source ROM CRC32/size, metadata")
    pti.add_argument("id", metavar="ID")
    _json_flag(pti)
    ptc = pt_sub.add_parser("candidates", help="Read-only: ROMs matching a UPS/BPS patch by CRC32 (SNES +512 header aware); for IPS: systems / ROMs to choose from")
    ptc.add_argument("id", metavar="ID")
    ptc.add_argument("--system", help="Restrict to this Roms/<system> folder (IPS: list its ROMs)")
    _json_flag(ptc)
    pta = pt_sub.add_parser("apply", help="Create a NEW patched ROM next to the original (never modifies the original); needs --yes")
    pta.add_argument("id", metavar="ID")
    pta.add_argument("--rom", required=True, help="Source ROM: a file under <card>/Roms/")
    pta.add_argument("--name", help="Output file name (default: '<rom> [<patch>]<ext>')")
    pta.add_argument("--header-skipped", action="store_true", dest="header_skipped", help="Source ROM has a 512-byte SNES copier header: skip it, keep it in the output")
    pta.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(pta)
    _norescan_flag(pta)
    ptd = pt_sub.add_parser("delete", help="Delete a patch from the store (not from Import/)")
    ptd.add_argument("id", metavar="ID")
    ptd.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    _json_flag(ptd)
    pts = pt_sub.add_parser("store", help="Copy a patch file (or the patches inside a .zip) into the store")
    pts.add_argument("path", metavar="PATH")
    _json_flag(pts)

    rc = subparsers.add_parser("recent", help="Read-only: danh sách game mới tải về / nhập gần đây (từ installed.jsonl)")
    rc.add_argument("--card", choices=["primary", "secondary"])
    rc.add_argument("--limit", type=int, default=50)
    _json_flag(rc)

    return parser


def _local_time(iso: str) -> str:
    """"2026-10-17T12:16:03Z" -> "2026-10-17 19:16" theo múi giờ máy; không đọc được thì trả nguyên chuỗi."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso


def _cmd_recent(args, probe) -> int:
    """Game cài gần đây trên từng thẻ, đọc <thẻ>/.loongmate-import/installed.jsonl (importer ghi khi commit). Chỉ đọc.
    Bỏ mục BIOS; dòng hỏng bỏ qua. `exists` = file đầu tiên của game còn trên thẻ."""
    roots = [(cid, path) for cid, path in (("primary", probe.primary_path), ("secondary", probe.secondary_path))
             if path and args.card in (None, cid)]
    results = []
    for cid, root in roots:
        root = Path(root)
        try:
            lines = (root / ".loongmate-import" / "installed.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("kind") == "bios":
                continue
            files = [f for f in item.get("files") or [] if isinstance(f, dict) and f.get("path")]
            first = str(root / files[0]["path"]) if files else ""
            results.append({"name": item.get("name", "?"), "system": item.get("system", "?"), "card": cid,
                            "installed_at": item.get("at", ""), "when": _local_time(item.get("at", "")),
                            "size": sum(f.get("size", 0) for f in files),
                            "target_file": first, "exists": bool(first) and os.path.exists(first),
                            "source": item.get("source", ""), "job": item.get("job", "")})
    results.sort(key=lambda x: x["installed_at"], reverse=True)
    results = results[:max(args.limit, 1)]
    if args.json:
        _print_json({"success": True, "count": len(results), "recent": results})
    elif not results:
        print("Chưa có game nào được cài gần đây.")
    else:
        print(f"=== GAME CÀI GẦN ĐÂY ({len(results)}) ===")
        for r in results:
            print(f" - [{r['system']}] {r['name']} ({'SD1' if r['card'] == 'primary' else 'SD2'}) "
                  f"{r['when']}{'' if r['exists'] else '  [đã xoá]'}")
    return EXIT_OK


_COMMANDS = {"library", "homebrew", "web", "patch", "import", "readiness", "scan", "backup", "restore", "health", "bundle", "verify", "recover", "endpoint", "push",
             "pull", "dedupe", "rom", "saves", "quarantine", "move", "fix", "bios", "snapshots", "roms", "history",
             "battery", "update", "agent", "nodes", "rescan", "hottabs", "recent", "leafconfig", "autoresume", "pocketlock", "onboarding"}
_NEEDS_INVENTORY = {"scan", "health", "bundle", "dedupe", "move", "fix", "bios", "roms"}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = build_parser(_peek_command(sys.argv[1:] if argv is None else list(argv)))
    args = parser.parse_args(argv)

    if not args.command:
        if not args.json and not args.quiet:
            print_banner()
        parser.print_help()
        return EXIT_USAGE_ERROR

    probe = PlatformProbe(args.primary, args.secondary, args.userdata)

    # readiness can partially work without a Primary card (battery/thermal/network
    # still read fine); handle it before constructing engines that unconditionally
    # call primary_path.resolve() and would crash on None otherwise.
    if args.command == "readiness":
        return _cmd_readiness(probe, args)

    if probe.primary_path is None:
        if not args.json and not args.quiet:
            print_banner()
        message = (
            f"Không xác định được đường dẫn thẻ Primary, nên không thể chạy lệnh '{args.command}' "
            f"(cần Primary để quét save/library/health)."
        )
        if args.json:
            _print_json({
                "error": "NO_PRIMARY_CARD",
                "message": message,
                "probe_errors": probe.probe_errors,
                "action": "Chạy lại với --primary <đường dẫn thẻ>, hoặc đặt biến môi trường SDCARD_PATH."
            })
        else:
            print(f"[LỖI] {message}")
            for e in probe.probe_errors:
                print(f"  - {e}")
            print("Hành động: chạy lại với --primary <đường dẫn thẻ>, hoặc đặt biến môi trường SDCARD_PATH.")
        return EXIT_USAGE_ERROR

    if args.command == "recent":
        return _cmd_recent(args, probe)

    if args.command == "import":      # trước SaveVault: scan/plan/jobs chỉ đọc, không dựng/ghi gì ngoài việc nó làm
        if not args.import_command:
            return _usage(args, "Dùng: import scan [PATH ...] | plan | apply --plan FILE --yes | jobs | recover [--yes] | mkdir")
        args.yes = getattr(args, "yes", False)
        return _cmd_import(args, probe)

    if args.command == "library":     # trước SaveVault: nodes/roots/list/jobs không dựng/ghi gì ngoài việc nó làm
        if not args.library_command:
            return _usage(args, "Dùng: library nodes | bundles | pair BUNDLE | unpair NODE | roots NODE | list NODE ROOT | fetch NODE ROOT PATH... | jobs | resume JOB | cancel JOB")
        args.yes = getattr(args, "yes", False)
        return _cmd_library(args, probe)

    if args.command == "homebrew":    # trước SaveVault: sync/list/show/jobs không dựng/ghi gì ngoài việc nó làm
        if not args.homebrew_command:
            return _usage(args, "Dùng: homebrew sync | list | show SLUG | fetch SLUG... | jobs | resume JOB | cancel JOB")
        args.yes = getattr(args, "yes", False)
        return _cmd_homebrew(args, probe)

    if args.command == "web":         # trước SaveVault: home/open/jobs/bookmark/history chỉ đọc/ghi app_data
        if not args.web_command:
            return _usage(args, "Dùng: web home | open URL | fetch URL... | jobs | resume JOB | cancel JOB | bookmark add|remove URL | history clear")
        if (args.web_command == "bookmark" and not args.web_bookmark_command) or (args.web_command == "history" and not args.web_history_command):
            return _usage(args, "Dùng: web bookmark add URL [--title T] | bookmark remove URL | history clear")
        args.yes = getattr(args, "yes", False)
        return _cmd_web(args, probe)

    if args.command == "patch":       # trước SaveVault: list/info/candidates/store không dựng/ghi gì ngoài việc nó làm
        if not args.patch_command:
            return _usage(args, "Dùng: patch list | info ID | candidates ID [--system S] | apply ID --rom PATH [--name N] [--header-skipped] --yes | delete ID --yes | store PATH")
        args.yes = getattr(args, "yes", False)
        return _cmd_patch(args, probe)

    if args.command == "onboarding":
        if not args.onboarding_command:
            return _usage(args, "Dùng: onboarding status | done | reset")
        return _cmd_onboarding(args, probe)

    if args.command == "pocketlock":
        if not args.pocketlock_command:
            return _usage(args, "Dùng: pocketlock status | on | off")
        return _cmd_pocketlock(args, probe)

    if args.command == "autoresume":
        if not args.autoresume_command:
            return _usage(args, "Dùng: autoresume status | on | off")
        return _cmd_autoresume(args, probe)

    if args.command == "leafconfig":
        if not args.leafconfig_command:
            return _usage(args, "Dùng: leafconfig snapshot | list | diff ID | restore ID --group retroarch|overrides|standalone [--yes]")
        args.yes = getattr(args, "yes", False)
        return _cmd_leafconfig(args, probe)

    if args.command == "hottabs":     # trước SaveVault: lệnh chỉ-đọc không được dựng/ghi gì ngoài việc nó làm
        if not args.hottabs_command:
            return _usage(args, "Dùng: hottabs status | on | off | install [--yes] | uninstall [--yes]")
        args.yes = getattr(args, "yes", False)
        return _cmd_hottabs(args, probe)

    # Kiểm kê/Doctor/Health nặng (nạp ~60 ms): chỉ dựng cho lệnh thật sự dùng chúng.
    inventory_engine = doctor = health = None
    if args.command in _NEEDS_INVENTORY:
        from engine.inventory import InventoryEngine, find_systems_catalog
        from engine.doctor import LibraryDoctor
        from engine.health import DeviceHealth
        catalog_path = find_systems_catalog()
        inventory_engine = InventoryEngine(probe.primary_path, probe.secondary_path)
        doctor = LibraryDoctor(probe.primary_path, probe.secondary_path, catalog_path, probe.userdata_path)
        health = DeviceHealth(probe.primary_path, probe.secondary_path, probe.userdata_path)
    vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)
    config_store = ConfigStore(probe.userdata_path)

    _check_and_warn_crash_recovery(vault)

    if not args.json and not args.quiet:
        print_banner()

    # SCAN
    if args.command == "scan":
        if not args.json:
            print("[LoongMate] Đang quét thư viện ROM, Artwork và Save...")
        inv = inventory_engine.scan_all()
        storage_probe = probe.get_readiness_summary()["storage"]
        report = doctor.audit(inv, storage_probe)

        cache_payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": int(time.time()),
            "total_issues": report["total_issues"],
            "severity_counts": report["severity_counts"],
            "catalog_version": report.get("catalog_version"),
            # việc sửa được ngay: "Sửa an toàn" + mục "Sửa hàng loạt" (Home hiện con số này thay vì tổng số vấn đề)
            "fixable": len(report.get("fixable_actions") or []) + sum(1 for i in report["issues"] if i.get("action_type") == "batch_fix")
        }
        _write_json_atomic(probe.userdata_path / "cache" / "last_audit.json", cache_payload)

        exit_code = EXIT_CRITICAL_ISSUES if report["severity_counts"]["critical"] > 0 else EXIT_OK

        if not args.json:
            print("=== KẾT QUẢ KIỂM TOÁN THƯ VIỆN (LIBRARY DOCTOR) ===")
            print(f"Tổng số ROM phát hiện: {inv['total_roms']} | Saves: {inv['total_saves']} | States: {inv['total_states']} | Images: {inv['total_images']}")
            print(f"Tổng số vấn đề phát hiện: {report['total_issues']} (Nghiêm trọng: {report['severity_counts']['critical']}, Cảnh báo: {report['severity_counts']['warning']}, Ghi chú: {report['severity_counts']['info']})")
            print("--------------------------------------------------")
            for idx, issue in enumerate(report["issues"], start=1):
                sev_tag = "[CRITICAL]" if issue["severity"] == "critical" else "[WARNING]" if issue["severity"] == "warning" else "[INFO]"
                print(f"{idx}. {sev_tag} [{issue['category']}] {issue['title']}")
                print(f"   Chi tiết: {issue['description']}")
                if "guide" in issue:
                    print(f"   Khuyến nghị: {issue['guide']}")

        if not args.fix:
            if args.json:
                _print_json(report)
            return exit_code

        if not report["fixable_actions"]:
            if not args.json:
                print("\n[LoongMate] Không có hành động sửa an toàn nào để áp dụng.")
            report["fix_applied"] = False
            if args.json:
                _print_json(report)
            return exit_code

        if not args.json:
            print("\n[LoongMate] Các hành động sẽ được áp dụng nếu xác nhận:")
            for action in report["fixable_actions"]:
                print(f" - {action['label']}")

        if args.yes:
            proceed = True
        elif not sys.stdin.isatty():
            proceed = None  # refuse: cannot prompt
        else:
            proceed = _confirm("Áp dụng các sửa đổi trên?")

        if proceed is None:
            msg = "Từ chối áp dụng --fix: stdin không phải TTY và không có cờ --yes. Chạy lại với '--fix --yes' để xác nhận không cần hỏi."
            report["fix_applied"] = False
            report["fix_refused_reason"] = msg
            if args.json:
                _print_json(report)
            else:
                print(f"\n[LỖI] {msg}")
            return EXIT_TASK_FAILED

        if not proceed:
            msg = "Đã huỷ: người dùng không xác nhận."
            report["fix_applied"] = False
            report["fix_refused_reason"] = msg
            if args.json:
                _print_json(report)
            else:
                print(f"\n[LoongMate] {msg}")
            return EXIT_TASK_FAILED

        if not args.json:
            print("\n[LoongMate] Bắt đầu thực thi các sửa đổi đã xác nhận...")
        fix_results = []
        for action in report["fixable_actions"]:
            res = doctor.execute_fix(action)
            fix_results.append({"label": action["label"], **res})
            if not args.json:
                print(f" - {action['label']}: {'Thành công' if res['success'] else 'Thất bại'} ({res['message']})")
        report["fix_applied"] = True
        report["fix_results"] = fix_results
        if args.json:
            _print_json(report)
        return exit_code

    # BACKUP
    elif args.command == "backup":
        if not args.json:
            print("[LoongMate] Đang chuẩn bị snapshot SaveVault...")
        snap = vault.create_snapshot()

        if snap.get("success") and args.endpoint:
            rv, err = _build_remote_vault(args.endpoint, vault, config_store)
            if err:
                snap["push"] = {"success": False, "message": err}
            else:
                snap["push"] = rv.push_snapshot(snap["snapshot_id"])

        if args.json:
            _print_json(snap)
            if not snap.get("success"):
                return EXIT_TASK_FAILED
            if "push" in snap and not snap["push"].get("success"):
                return EXIT_TASK_FAILED
            return EXIT_OK

        if not snap["success"]:
            print(f"[LỖI] Sao lưu thất bại: {snap.get('message')}")
            return EXIT_TASK_FAILED

        print("=== SAO LƯU SAVEVAULT THÀNH CÔNG ===")
        print(f"Snapshot ID: {snap['snapshot_id']}")
        print(f"Số lượng file: {snap['files_count']}")
        print(f"Tổng dung lượng: {round(snap['total_bytes'] / 1024, 2)} KB")
        print(f"Vị trí lưu: {snap['path']}")
        print(f"Thời gian: {snap['timestamp']}")
        if "push" in snap:
            p = snap["push"]
            if p.get("success"):
                print(f"Đã đẩy lên endpoint '{args.endpoint}': {p['objects_uploaded']} object mới, {p['objects_skipped']} đã có sẵn, {p['bytes_uploaded']} bytes.")
            else:
                print(f"[LỖI ĐẨY REMOTE] {p['message']}")
                return EXIT_TASK_FAILED
        return EXIT_OK

    # RESTORE
    elif args.command == "restore":
        snapshots = vault.list_snapshots()
        if not snapshots:
            msg = "Chưa có bản snapshot nào trong SaveVault."
            if args.json:
                _print_json({"error": "NO_SNAPSHOTS", "message": msg})
            else:
                print(f"[LoongMate] {msg}")
            return EXIT_TASK_FAILED

        target_snap_id = args.snapshot or snapshots[0]["id"]
        selected_files = args.file if args.file else None

        if args.dry_run:
            preview = vault.preview_restore(target_snap_id)
            if args.json:
                _print_json(preview)
                return EXIT_OK
            print(f"=== XEM TRƯỚC KHÔI PHỤC (DRY-RUN): {target_snap_id} ===")
            print(f"File sẽ thêm mới: {len(preview['to_add'])}")
            for f in preview["to_add"]:
                print(f" + [ADD] {f}")
            print(f"File sẽ cập nhật: {len(preview['to_update'])}")
            for f in preview["to_update"]:
                print(f" ~ [UPDATE] {f}")
            print(f"File xung đột (giữ cả hai): {len(preview['conflicts'])}")
            for c in preview["conflicts"]:
                print(f" ! [CONFLICT] {c['rel_path']}")
            print(f"File không đổi: {len(preview['unchanged'])}")
            print("==================================================")
            return EXIT_OK

        is_full_restore = selected_files is None
        if is_full_restore and not args.yes:
            warning = (
                "CẢNH BÁO: đây là FULL RESTORE — sẽ ghi đè toàn bộ file save/state hiện tại bằng nội dung "
                f"từ snapshot '{target_snap_id}'. LoongMate sẽ tự tạo một pre-restore snapshot trước khi ghi, "
                "nhưng hãy xác nhận bạn thực sự muốn full restore (dùng --file để chỉ khôi phục từng file)."
            )
            if not args.json:
                print(warning)
            if not sys.stdin.isatty():
                msg = "Từ chối full restore: stdin không phải TTY và không có cờ --yes. Dùng '--yes', hoặc '--file' để chọn từng file."
                if args.json:
                    _print_json({"success": False, "message": msg})
                else:
                    print(f"[LỖI] {msg}")
                return EXIT_USAGE_ERROR
            if not _confirm("Tiếp tục full restore?"):
                msg = "Đã huỷ: người dùng không xác nhận full restore."
                if args.json:
                    _print_json({"success": False, "message": msg})
                else:
                    print(f"[LoongMate] {msg}")
                return EXIT_TASK_FAILED

        if not args.json:
            print(f"[LoongMate] Đang khôi phục an toàn từ snapshot '{target_snap_id}'...")
        res = vault.restore_snapshot(target_snap_id, selected_files=selected_files)

        if args.json:
            _print_json(res)
            return EXIT_OK if res["success"] else EXIT_TASK_FAILED

        if not res["success"]:
            print(f"[LỖI] {res.get('message')}")
            return EXIT_TASK_FAILED

        print("=== KHÔI PHỤC THÀNH CÔNG ===")
        print(f"Số file đã khôi phục: {res['restored_files_count']}")
        print(f"Bản sao lưu bảo vệ trước khôi phục: {res['pre_restore_id']}")
        if res["preserved_conflicts"]:
            print(f"Các file xung đột đã được bảo toàn: {res['preserved_conflicts']}")
            print(f"Thư mục chứa xung đột: {res['conflicts_dir']}")
        if res["rejected_paths"]:
            print(f"Các path bị từ chối (không an toàn): {res['rejected_paths']}")
        if res["skipped_readonly"]:
            print(f"Các file bị bỏ qua (thẻ read-only/gần đầy): {res['skipped_readonly']}")
        return EXIT_OK

    # VERIFY
    elif args.command == "verify":
        snapshots = vault.list_snapshots()
        if not snapshots:
            msg = "Chưa có snapshot nào để xác minh."
            if args.json:
                _print_json({"error": "NO_SNAPSHOTS", "message": msg})
            else:
                print(f"[LoongMate] {msg}")
            return EXIT_TASK_FAILED

        target = args.snapshot or snapshots[0]["id"]
        result = vault.verify_snapshot(target)
        if args.json:
            _print_json(result)
            return EXIT_OK if result.get("ok") else EXIT_TASK_FAILED

        print(f"=== XÁC MINH SNAPSHOT: {target} ===")
        print(f"Đã kiểm tra: {result.get('checked', 0)} file")
        print(f"Hợp lệ: {'CÓ' if result.get('ok') else 'KHÔNG'}")
        if result.get("missing"):
            print(f"Thiếu: {result['missing']}")
        if result.get("size_mismatch"):
            print(f"Sai kích thước: {result['size_mismatch']}")
        if result.get("hash_mismatch"):
            print(f"Sai hash: {result['hash_mismatch']}")
        return EXIT_OK if result.get("ok") else EXIT_TASK_FAILED

    # RECOVER
    elif args.command == "recover":
        recovery = vault.check_crash_recovery()
        if recovery is None:
            msg = "Không phát hiện phiên khôi phục nào bị gián đoạn."
            if args.json:
                _print_json({"needs_recovery": False, "message": msg})
            else:
                print(f"[LoongMate] {msg}")
            return EXIT_OK

        if not args.rollback:
            if args.json:
                _print_json(recovery)
            else:
                print("=== PHÁT HIỆN PHIÊN KHÔI PHỤC BỊ GIÁN ĐOẠN ===")
                print(f"Snapshot nguồn: {recovery['journal'].get('snapshot_id')}")
                print(f"Dự kiến khôi phục: {recovery['planned_count']} file, đã khôi phục: {recovery['restored_count']} file")
                print(f"Có thể rollback: {'CÓ' if recovery['can_rollback'] else 'KHÔNG'}")
                print(recovery["message"])
                print("Chạy 'recover --rollback' để rollback về bản trước khi restore.")
            return EXIT_TASK_FAILED

        if not recovery.get("can_rollback"):
            msg = "Không thể rollback tự động: không tìm thấy bản pre-restore snapshot. Hãy kiểm tra dữ liệu thủ công."
            if args.json:
                _print_json({"success": False, "message": msg})
            else:
                print(f"[LỖI] {msg}")
            return EXIT_TASK_FAILED

        if not args.yes:
            if not sys.stdin.isatty():
                msg = "Từ chối rollback: stdin không phải TTY và không có cờ --yes. Chạy lại với '--yes'."
                if args.json:
                    _print_json({"success": False, "message": msg})
                else:
                    print(f"[LỖI] {msg}")
                return EXIT_USAGE_ERROR
            pre_id = recovery["journal"].get("pre_restore_id")
            if not _confirm(f"Rollback về pre-restore snapshot '{pre_id}'?"):
                msg = "Đã huỷ: người dùng không xác nhận rollback."
                if args.json:
                    _print_json({"success": False, "message": msg})
                else:
                    print(f"[LoongMate] {msg}")
                return EXIT_TASK_FAILED

        result = vault.rollback_from_journal()
        if args.json:
            _print_json(result)
        else:
            print(result.get("message"))
        return EXIT_OK if result.get("success") else EXIT_TASK_FAILED

    # HEALTH
    elif args.command == "health":
        # Pass real card data in, otherwise the mount/free-space check has
        # nothing to inspect and silently reports itself as skipped.
        diag = health.run_diagnostics(storage=probe.get_readiness_summary().get("storage"))
        if args.json:
            _print_json(diag)
            return EXIT_OK if diag["all_passed"] else EXIT_TASK_FAILED

        print("=== CHẨN ĐOÁN HỆ THỐNG (DEVICE HEALTH) ===")
        print(f"Thời gian: {diag['timestamp']}")
        for t in diag["tests"]:
            if t.get("skipped"):
                mark = "[SKIP]"
            elif t.get("passed", True):
                mark = "[PASS]"
            else:
                mark = "[FAIL]"
            print(f"{mark} {t['test']}: {t['description']}")
            if "latency_ms" in t:
                print(f"       Độ trễ R/W: {t['latency_ms']} ms")
            if "present_dirs" in t:
                print(f"       Thư mục tìm thấy: {', '.join(t['present_dirs'])}")
        return EXIT_OK if diag["all_passed"] else EXIT_TASK_FAILED

    # BUNDLE
    elif args.command == "bundle":
        readiness = probe.get_readiness_summary()
        inv = inventory_engine.scan_all()
        report = doctor.audit(inv, readiness["storage"])
        privacy = not args.no_privacy

        bundle_path = health.export_support_bundle(readiness, report, privacy_mode=privacy)
        if args.json:
            _print_json({"bundle_path": bundle_path, "privacy_mode": privacy})
            return EXIT_OK

        print("=== XUẤT GÓI CHẨN ĐOÁN (SUPPORT BUNDLE) ===")
        print(f"Tạo file thành công: {bundle_path}")
        print(f"Chế độ bảo mật (Privacy Mode): {'Bật (đã ẩn tên ROM & thông tin nhạy cảm)' if privacy else 'Tắt'}")
        print("Gói ZIP đã được lọc sạch mật khẩu Wi-Fi, SSH keys và auth tokens.")
        return EXIT_OK

    # ENDPOINT
    elif args.command == "endpoint":
        if args.endpoint_command == "add":
            ep = {
                "name": args.name, "type": args.type,
                "host": args.host, "port": args.port, "user": args.user,
                "remote_path": args.remote_path, "key_path": args.key_path,
                "host_key_sha256": None
            }
            try:
                config_store.add_endpoint(ep)
            except ValueError as e:
                if args.json:
                    _print_json({"success": False, "message": str(e)})
                else:
                    print(f"[LỖI] {e}")
                return EXIT_USAGE_ERROR
            msg = f"Đã thêm endpoint '{args.name}'."
            if args.json:
                _print_json({"success": True, "message": msg})
            else:
                print(msg)
            return EXIT_OK

        elif args.endpoint_command == "list":
            endpoints = config_store.list_endpoints()
            if args.json:
                _print_json(endpoints)
                return EXIT_OK
            if not endpoints:
                print("Chưa có endpoint nào. Dùng 'endpoint add' để tạo.")
                return EXIT_OK
            for ep in endpoints:
                print(f"- {ep['name']} ({ep['type']}) host={ep.get('host')} remote_path={ep.get('remote_path')}")
            return EXIT_OK

        elif args.endpoint_command == "remove":
            removed = config_store.remove_endpoint(args.name)
            msg = f"Đã xoá endpoint '{args.name}'." if removed else f"Không tìm thấy endpoint '{args.name}'."
            if args.json:
                _print_json({"success": removed, "message": msg})
            else:
                print(msg)
            return EXIT_OK if removed else EXIT_TASK_FAILED

        elif args.endpoint_command == "test":
            rv, err = _build_remote_vault(args.name, vault, config_store)
            if err:
                if args.json:
                    _print_json({"success": False, "message": err})
                else:
                    print(f"[LỖI] {err}")
                return EXIT_USAGE_ERROR
            result = rv.test_endpoint()
            if args.json:
                _print_json(result)
            else:
                print(result["message"] if result["success"] else f"[LỖI] {result['message']}")
            return EXIT_OK if result["success"] else EXIT_TASK_FAILED

        elif args.endpoint_command == "pin":
            ep = config_store.get_endpoint(args.name)
            if ep is None:
                msg = f"Không tìm thấy endpoint '{args.name}'."
                if args.json:
                    _print_json({"success": False, "message": msg})
                else:
                    print(f"[LỖI] {msg}")
                return EXIT_USAGE_ERROR
            if ep.get("type") != "sftp":
                msg = f"Endpoint '{args.name}' không phải loại 'sftp' — chỉ SFTP cần pin host key."
                if args.json:
                    _print_json({"success": False, "message": msg})
                else:
                    print(f"[LỖI] {msg}")
                return EXIT_USAGE_ERROR

            from engine.remote import fetch_host_fingerprint
            fp = fetch_host_fingerprint(ep["host"], port=ep.get("port") or 22)
            if not fp.get("success"):
                if args.json:
                    _print_json(fp)
                else:
                    print(f"[LỖI] {fp['message']}")
                return EXIT_TASK_FAILED

            if not args.json:
                print(f"Fingerprint host key của {ep['host']}: {fp['fingerprint']}")

            if not args.yes:
                if not sys.stdin.isatty():
                    msg = "Từ chối ghim host key: stdin không phải TTY và không có cờ --yes."
                    if args.json:
                        _print_json({"success": False, "message": msg})
                    else:
                        print(f"[LỖI] {msg}")
                    return EXIT_USAGE_ERROR
                if not _confirm(f"Xác nhận ghim host key này cho '{ep['host']}'?"):
                    msg = "Đã huỷ: người dùng không xác nhận."
                    if args.json:
                        _print_json({"success": False, "message": msg})
                    else:
                        print(f"[LoongMate] {msg}")
                    return EXIT_TASK_FAILED

            known_hosts = vault.userdata_path / "known-hosts"
            from engine.remote import pin_host_key
            result = pin_host_key(ep["host"], port=ep.get("port") or 22, known_hosts=known_hosts,
                                   expected_sha256=args.expected_sha256)
            if args.json:
                _print_json(result)
            else:
                print(result["message"])
            return EXIT_OK if result.get("success") else EXIT_TASK_FAILED

        else:
            parser.parse_args(["endpoint", "--help"])
            return EXIT_USAGE_ERROR

    # PUSH
    elif args.command == "push":
        snapshots = vault.list_snapshots()
        target = args.snapshot or (snapshots[0]["id"] if snapshots else None)
        if target is None:
            msg = "Chưa có snapshot cục bộ nào để đẩy. Chạy 'backup' trước."
            if args.json:
                _print_json({"success": False, "message": msg})
            else:
                print(f"[LỖI] {msg}")
            return EXIT_TASK_FAILED

        rv, err = _build_remote_vault(args.endpoint, vault, config_store)
        if err:
            if args.json:
                _print_json({"success": False, "message": err})
            else:
                print(f"[LỖI] {err}")
            return EXIT_USAGE_ERROR

        result = rv.push_snapshot(target)
        if args.json:
            _print_json(result)
        else:
            print(result["message"])
        return EXIT_OK if result.get("success") else EXIT_TASK_FAILED

    # PULL
    elif args.command == "pull":
        rv, err = _build_remote_vault(args.endpoint, vault, config_store)
        if err:
            if args.json:
                _print_json({"success": False, "message": err})
            else:
                print(f"[LỖI] {err}")
            return EXIT_USAGE_ERROR

        result = rv.pull_snapshot(args.snapshot)
        if args.json:
            _print_json(result)
        else:
            print(result["message"])
        return EXIT_OK if result.get("success") else EXIT_TASK_FAILED

    elif args.command == "dedupe":
        return _cmd_dedupe(args, probe, inventory_engine, doctor)

    elif args.command == "rom":
        return _cmd_rom(args, probe)

    elif args.command == "quarantine":
        return _cmd_quarantine(args, probe)

    elif args.command == "move":
        return _cmd_move(args, probe, inventory_engine)

    elif args.command == "fix":
        return _cmd_fix(args, probe, inventory_engine, doctor)

    elif args.command == "bios":
        return _cmd_bios(args, probe, inventory_engine, doctor)

    elif args.command == "snapshots":
        return _cmd_snapshots(args, probe, vault)

    elif args.command == "roms":
        return _cmd_roms(args, probe, inventory_engine, doctor)

    elif args.command == "saves":
        return _cmd_saves(args, probe, vault)

    elif args.command == "history":
        return _cmd_history(args, probe)

    elif args.command == "battery":
        return _cmd_battery(args, probe)

    elif args.command == "update":
        if not args.update_command:
            return _usage(args, "Dùng: update status | list [--verify] | verify [GÓI] | apply [GÓI] [--yes] | rollback [--yes]")
        return _cmd_update(args, probe)

    elif args.command == "agent":
        return _cmd_agent(args, probe)

    elif args.command == "nodes":
        return _cmd_nodes(args, config_store)

    elif args.command == "rescan":
        return _cmd_rescan(args)

    return EXIT_USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main())
