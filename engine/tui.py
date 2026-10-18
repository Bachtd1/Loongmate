"""
TUI tối giản cho LoongMate, chỉ dùng thư viện chuẩn.
Gọi engine thật (PlatformProbe, InventoryEngine, LibraryDoctor, SaveVault,
DeviceHealth) và chỉ hiển thị số liệu thật lấy từ engine — không bịa số.

Điều hướng khớp phím vật lý của Pocket 1:
  w/a/s/d hoặc phím mũi tên = D-pad   j / Enter = A   k = B
  u = X (sao lưu nhanh)      i = Y (xuất gói hỗ trợ)      q = thoát

KHÔNG dùng curses (không có trên Windows, chưa kiểm chứng trên thiết bị).
"""

import sys
import json
import argparse
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.probe import PlatformProbe
from engine.inventory import InventoryEngine, find_systems_catalog
from engine.config import load_audit_cache, save_audit_cache
from engine.doctor import LibraryDoctor
from engine.savevault import SaveVault
from engine.health import DeviceHealth

STATUS_LABEL = {"green": "OK", "yellow": "CHÚ Ý", "red": "LỖI", "unknown": "KHÔNG RÕ"}


def _read_key() -> str:
    """Đọc một phím. termios/tty trên POSIX, msvcrt trên Windows, input() khi không có cả hai."""
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch
    except ImportError:
        pass

    try:
        import msvcrt
        ch = msvcrt.getch()
        try:
            return ch.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    except ImportError:
        pass

    line = input("> Phím (w/a/s/d j k u i q) rồi Enter: ")
    return line[:1] if line else ""


def _fmt_battery(bat: Dict[str, Any]) -> str:
    if not bat.get("available"):
        return "Pin: không đọc được"
    percent = bat.get("percent")
    if percent is None:
        return "Pin: không đọc được"
    status = bat.get("status", "")
    return f"Pin: {percent}% ({status})"


def _fmt_thermal(therm: Dict[str, Any]) -> str:
    if not therm.get("available"):
        return "Nhiệt độ: —"
    cur = therm.get("current_celsius")
    if cur is None:
        return "Nhiệt độ: —"
    trend = therm.get("trend", "")
    return f"Nhiệt độ: {cur}°C ({trend})"


def _card_storage(st: Dict[str, Any]) -> List[str]:
    lines = []
    primary = st.get("primary") or {}
    secondary = st.get("secondary")
    lines.append(
        f"SD1: {primary.get('free_mb', '—')}MB trống / {primary.get('total_mb', '—')}MB "
        f"| Ghi: {'Có' if primary.get('writable') else 'KHÔNG'} | FS: {primary.get('filesystem', '—')}"
    )
    if secondary:
        fs_detected = secondary.get("fs_detected", True)
        if fs_detected is False:
            fs_text = "chưa xác minh được"
        else:
            fs_text = secondary.get("filesystem", "—") + (" (FAT32 OK)" if secondary.get("is_fat32") else " (LỖI: CẦN FAT32)")
        ro = secondary.get("read_only_mount")
        ro_text = " [READ-ONLY]" if ro else ""
        lines.append(
            f"SD2: {secondary.get('free_mb', '—')}MB trống / {secondary.get('total_mb', '—')}MB "
            f"| Ghi: {'Có' if secondary.get('writable') else 'KHÔNG'} | FS: {fs_text}{ro_text}"
        )
    else:
        lines.append("SD2: Chưa gắn thẻ")
    return lines


class TuiApp:
    def __init__(self, probe: PlatformProbe, inventory: InventoryEngine, doctor: LibraryDoctor,
                 vault: SaveVault, health: DeviceHealth, cache_file: Path):
        self.probe = probe
        self.inventory = inventory
        self.doctor = doctor
        self.vault = vault
        self.health = health
        self.cache_file = cache_file
        # cache_file = <app_data>/cache/last_audit.json
        self.app_data = cache_file.parent.parent
        self.screen = "home"
        self.message: List[str] = []
        self.running = True

    def render(self) -> None:
        print("\033[2J\033[H", end="")
        print("=" * 72)
        print("  LOONGMATE — Pocket 1 Care & Sync (TUI)")
        print("=" * 72)

        if self.screen == "home":
            self._render_home()
        elif self.screen == "library":
            self._render_library()
        elif self.screen == "savevault":
            self._render_savevault()

        if self.message:
            print("-" * 72)
            for m in self.message:
                print(m)

        print("-" * 72)
        print("[D-pad w/a/s/d] di chuyển  [j/Enter] A  [k] B  [u] Sao lưu nhanh  "
              "[i] Xuất gói hỗ trợ  [q] Thoát")

    def _render_home(self) -> None:
        readiness = self.probe.get_readiness_summary()
        dev = readiness["device"]
        st = readiness["storage"]
        status_tag = STATUS_LABEL.get(dev.get("status"), dev.get("status", "KHÔNG RÕ"))

        print(f"\n1) MÁY [{status_tag}]")
        print(f"   {_fmt_battery(dev['battery'])}")
        print(f"   {_fmt_thermal(dev['thermal'])}")
        for w in dev.get("warnings", []):
            print(f"   ! {w}")

        storage_tag = STATUS_LABEL.get(st.get("status"), st.get("status", "KHÔNG RÕ"))
        print(f"\n2) THẺ NHỚ [{storage_tag}]")
        for line in _card_storage(st):
            print(f"   {line}")

        print("\n3) SAVE (SaveVault)")
        snapshots = self.vault.list_snapshots()
        if not snapshots:
            print("   Chưa có bản sao lưu nào.")
        else:
            latest = snapshots[0]
            print(f"   Số snapshot: {len(snapshots)} | Mới nhất: {latest['id']} lúc {latest['created_at']}")

        print("\n4) THƯ VIỆN (Library Doctor)")
        cached = load_audit_cache(self.app_data)
        if cached:
            print(f"   {cached['total_issues']} vấn đề (số liệu lúc {cached['generated_at']})")
        else:
            print("   Chưa quét lần nào — vào mục Library Doctor để quét.")

        recovery = self.vault.check_crash_recovery()
        if recovery:
            print("\n!!! CẢNH BÁO KHÔI PHỤC SAU MẤT ĐIỆN !!!")
            print(f"   {recovery['message']}")
            print("   Hành động cần làm: vào SaveVault (phím j) rồi xem lựa chọn rollback.")

    def _run_audit(self) -> Dict[str, Any]:
        inv = self.inventory.scan_all()
        storage_probe = self.probe.get_readiness_summary()["storage"]
        report = self.doctor.audit(inv, storage_probe)
        save_audit_cache(self.app_data, report)
        return report

    def _render_library(self) -> None:
        print("\n[LIBRARY DOCTOR]")
        print("Nhấn [j] để quét lại (deep scan), [k] quay lại Home.\n")
        cached = load_audit_cache(self.app_data)
        if cached:
            sc = cached.get("severity_counts", {})
            print(f"Số liệu lúc {cached['generated_at']}:")
            print(f"  Tổng số vấn đề: {cached['total_issues']} "
                  f"(Nghiêm trọng: {sc.get('critical', 0)}, "
                  f"Cảnh báo: {sc.get('warning', 0)}, "
                  f"Ghi chú: {sc.get('info', 0)})")
            # Cache chỉ giữ tóm tắt (xem engine/config.py), nên muốn xem danh
            # sách issue thì phải quét lại.
            print("  Nhấn [j] để quét lại và xem chi tiết từng vấn đề.")
        else:
            print("Chưa có số liệu — nhấn [j] để quét.")

    def _render_savevault(self) -> None:
        print("\n[SAVEVAULT]")
        print("Nhấn [j] để tạo snapshot ngay, [k] quay lại Home.\n")
        snapshots = self.vault.list_snapshots()
        if not snapshots:
            print("Chưa có bản sao lưu nào.")
        else:
            for s in snapshots[:5]:
                print(f"  - {s['id']} | {s['total_files']} file | {s['created_at']}")

        recovery = self.vault.check_crash_recovery()
        if recovery:
            print("\n!!! Phiên khôi phục trước bị gián đoạn !!!")
            print(f"  {recovery['message']}")
            if hasattr(self.vault, "rollback_from_journal"):
                print("  Nhấn [r] để rollback (sẽ hỏi xác nhận).")

    def do_backup(self) -> None:
        self.message = ["Đang sao lưu..."]
        snap = self.vault.create_snapshot()
        if snap.get("success"):
            kb = round(snap["total_bytes"] / 1024, 2)
            self.message = [
                f"Sao lưu thành công: {snap['files_count']} file, {kb} KB, "
                f"snapshot {snap['snapshot_id']}"
            ]
        else:
            self.message = [f"LỖI sao lưu: {snap.get('message', 'không rõ nguyên nhân')}"]

    def do_bundle(self) -> None:
        self.message = ["Đang xuất gói hỗ trợ..."]
        try:
            readiness = self.probe.get_readiness_summary()
            # Gói hỗ trợ cần báo cáo ĐẦY ĐỦ (có danh sách issue), mà cache chỉ
            # giữ tóm tắt — nên luôn quét lại thay vì dùng cache.
            report = self._run_audit()
            path = self.health.export_support_bundle(readiness, report)
            self.message = [f"Đã xuất gói hỗ trợ tại: {path}"]
        except Exception as e:
            self.message = [f"LỖI xuất gói hỗ trợ: {e}"]

    def do_rollback(self) -> None:
        if not hasattr(self.vault, "rollback_from_journal"):
            self.message = ["Chức năng rollback chưa có trong engine hiện tại."]
            return
        print("Xác nhận rollback về bản trước khi khôi phục? (y/N): ", end="", flush=True)
        ans = input().strip().lower()
        if ans == "y":
            res = self.vault.rollback_from_journal()
            self.message = [str(res)]
        else:
            self.message = ["Đã hủy rollback."]

    def handle_key(self, key: str) -> None:
        self.message = []
        key = key.lower()
        if key == "q":
            self.running = False
            return
        if key == "u":
            self.do_backup()
            return
        if key == "i":
            self.do_bundle()
            return

        if self.screen == "home":
            if key in ("j", "\r", "\n"):
                self.screen = "library"
            elif key == "s":
                self.screen = "savevault"
        elif self.screen == "library":
            if key == "j":
                report = self._run_audit()
                self.message = [f"Đã quét xong lúc {time.strftime('%H:%M:%S')}: "
                                 f"{report['total_issues']} vấn đề."]
            elif key == "k":
                self.screen = "home"
        elif self.screen == "savevault":
            if key == "j":
                self.do_backup()
            elif key == "k":
                self.screen = "home"
            elif key == "r":
                self.do_rollback()

    def run(self) -> int:
        recovery = self.vault.check_crash_recovery()
        if recovery:
            print(recovery["message"])
            print("Hành động cần làm: xem lại SaveVault trước khi tiếp tục, có thể rollback về "
                  f"snapshot {recovery['journal'].get('pre_restore_id')} nếu cần.")
            input("Nhấn Enter để vào Home...")

        while self.running:
            self.render()
            key = _read_key()
            if not key:
                continue
            self.handle_key(key)
        print("Tạm biệt.")
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LoongMate TUI (stdlib-only)")
    parser.add_argument("--primary", help="Đường dẫn thẻ SD chính")
    parser.add_argument("--secondary", help="Đường dẫn thẻ SD phụ")
    parser.add_argument("--userdata", help="Đường dẫn userdata của LoongMate")
    args = parser.parse_args(argv)

    probe = PlatformProbe(args.primary, args.secondary, args.userdata)
    catalog_path = find_systems_catalog()

    inventory = InventoryEngine(probe.primary_path, probe.secondary_path)
    doctor = LibraryDoctor(probe.primary_path, probe.secondary_path, catalog_path, probe.userdata_path)
    vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)
    health = DeviceHealth(probe.primary_path, probe.secondary_path, probe.userdata_path)

    cache_file = probe.userdata_path / "cache" / "last_audit.json"

    app = TuiApp(probe, inventory, doctor, vault, health, cache_file)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
