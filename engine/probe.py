"""
Platform probe module for LoongMate on Miniloong Pocket 1 (Leaf CFW).
Reads battery, thermal, storage, and mount information safely from user-space.
"""

import os
import sys
import shutil
import platform
import socket
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

# Status severity ranking used to combine independent health signals (battery,
# thermal) into one overall device status without ever downgrading a worse
# (more severe) status that was already determined, and without claiming
# "green" when a signal could not be measured at all.
_STATUS_RANK = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}

# Các khe thẻ của Pocket 1 (mlp1) và tên tương đương trên nền tảng khác, theo thứ tự thử.
CARD_MOUNT_CANDIDATES = ("/mnt/sdcard", "/media/sdcard1", "/mnt/SDCARD", "/mnt/sdcard2", "/mnt/SDCARD2")


def _same_path(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _bump_status(current: str, candidate: str) -> str:
    """Returns the more severe of two statuses, per _STATUS_RANK."""
    return candidate if _STATUS_RANK[candidate] > _STATUS_RANK[current] else current


class PlatformProbe:
    def __init__(self, primary_path: Optional[str] = None, secondary_path: Optional[str] = None, userdata_path: Optional[str] = None):
        self.probe_errors: List[str] = []

        # --- Primary / Secondary path resolution ---
        # Priority order (per Leaf/Disco Boy env contract):
        #   1. Explicit argument (CLI --primary/--secondary, simulator, tests) always wins.
        #   2. $SDCARD_PATHS (Leaf's multi-card list: first entry = primary, second = secondary).
        #   3. $SDCARD_PATH for primary.
        #   4. /mnt/SDCARD if it exists.
        # No mock_sd1/mock_sd2 cwd-based fallback: that made behavior depend on the
        # working directory, which is not something we should guess around.
        sdcard_paths_env = os.environ.get("SDCARD_PATHS")
        paths_list: List[str] = []
        if sdcard_paths_env:
            # os.pathsep is already ':' on the real Linux/Leaf target, which is what
            # the env contract uses. We deliberately do NOT also split on a literal
            # ':' here: on Windows (dev/test machine) that would shred a path's own
            # drive-letter colon (e.g. "C:\Users\...") into garbage fragments.
            paths_list = [p for p in sdcard_paths_env.split(os.pathsep) if p]

        if primary_path:
            primary_candidate: Optional[str] = primary_path
        elif paths_list:
            primary_candidate = paths_list[0]
        elif os.environ.get("SDCARD_PATH"):
            primary_candidate = os.environ.get("SDCARD_PATH")
        else:
            # KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02 (Pocket 1 / Leaf): thẻ mount ở
            # "/mnt/sdcard" VIẾT THƯỜNG trên mlp1. "/mnt/SDCARD" viết hoa là quy ước
            # của máy tg5040 — xác nhận trong Apps/mlp1/SSHServer.pak/launch.sh, nơi
            # phân biệt MLP1_DEFAULT_SDCARD_PATH=/mnt/sdcard với
            # TG5040_DEFAULT_SDCARD_PATH=/mnt/SDCARD. Thử cả hai, mlp1 trước.
            primary_candidate = None
            for guess in ("/mnt/sdcard", "/mnt/SDCARD"):
                if os.path.exists(guess):
                    primary_candidate = guess
                    break

        if primary_candidate:
            self.primary_path: Optional[Path] = Path(primary_candidate).resolve()
        else:
            self.primary_path = None
            self.probe_errors.append(
                "Không xác định được đường dẫn thẻ Primary ($SDCARD_PATH/$SDCARD_PATHS không có, /mnt/SDCARD không tồn tại)."
            )

        secondary_explicit = bool(secondary_path)
        if secondary_path:
            secondary_candidate: Optional[str] = secondary_path
        elif len(paths_list) > 1:
            secondary_candidate = paths_list[1]
        elif os.environ.get("UMRK_SECONDARY_SDCARD_PATH"):
            # KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02, đọc từ hợp đồng chính thức
            # .system/leaf/platforms/mlp1/launcher/env.sh:
            #   umrk_env_default UMRK_SECONDARY_SDCARD_PATH "/media/sdcard1"
            #   _umrk_sdcard_paths="$SDCARD_PATH:$UMRK_SECONDARY_SDCARD_PATH"
            # Đây mới là tên biến thật; "SECONDARY_SDCARD_PATH" là tên tôi từng tự
            # đặt và Leaf không hề dùng.
            secondary_candidate = os.environ.get("UMRK_SECONDARY_SDCARD_PATH")
        elif os.environ.get("SECONDARY_SDCARD_PATH"):
            secondary_candidate = os.environ.get("SECONDARY_SDCARD_PATH")
        else:
            # Thẻ khe 2 trên mlp1 mount ở /media/sdcard1 (xác nhận trong env.sh của
            # Leaf). Giữ /mnt/SDCARD2 cho các nền tảng khác.
            secondary_candidate = None
            for guess in ("/media/sdcard1", "/mnt/sdcard2", "/mnt/SDCARD2"):
                if os.path.exists(guess):
                    secondary_candidate = guess
                    break

        # Thẻ khe 2 suy ra từ môi trường phải là một mount point THẬT.
        # KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02: Leaf luôn công bố
        # SDCARD_PATHS="/mnt/sdcard:/media/sdcard1" dù khe 2 đang rỗng, và
        # /media/sdcard1 tồn tại như một thư mục rỗng trên rootfs. Không kiểm
        # mount point thì shutil.disk_usage() trả về số liệu của rootfs, và
        # LoongMate báo "SD2 định dạng ext4, cần FAT32" rồi khuyên format —
        # một cảnh báo giả, lại còn là lời khuyên nguy hiểm, trong khi máy
        # không hề có thẻ thứ hai. Tiêu chí nghiệm thu: "Chỉ Primary → không
        # cảnh báo giả về SD2".
        # Đường dẫn truyền tường minh (test/simulator) KHÔNG bị kiểm mount point,
        # vì thư mục tạm không bao giờ là mount point.
        if secondary_candidate and not secondary_explicit:
            try:
                if not os.path.ismount(secondary_candidate):
                    self.probe_errors.append(
                        f"Bỏ qua '{secondary_candidate}': có thư mục nhưng không phải thẻ được mount "
                        f"(khe thẻ thứ hai đang rỗng)."
                    )
                    secondary_candidate = None
            except OSError:
                secondary_candidate = None

        # KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-04: hai thẻ có thể đổi chỗ mount sau khi khởi động
        # (thẻ Leaf lên /media/sdcard1, thẻ kia lên /mnt/sdcard). Ngoài launcher (vd qua SSH),
        # env.sh đặt UMRK_SECONDARY_SDCARD_PATH theo mặc định "/media/sdcard1" = CHÍNH thẻ Leaf.
        # Thẻ phụ trùng thẻ chính thì tìm thẻ còn lại đang được mount; không có thì coi như không có.
        if secondary_candidate and not secondary_explicit and self.primary_path is not None \
                and _same_path(secondary_candidate, self.primary_path):
            secondary_candidate = None
            for guess in CARD_MOUNT_CANDIDATES:
                try:
                    if os.path.ismount(guess) and not _same_path(guess, self.primary_path):
                        secondary_candidate = guess
                        break
                except OSError:
                    continue
            if secondary_candidate is None:
                self.probe_errors.append("Thẻ phụ trong môi trường trùng thẻ chính; không thấy thẻ thứ hai đang mount.")

        self.secondary_path: Optional[Path] = Path(secondary_candidate).resolve() if secondary_candidate else None

        # --- Userdata path resolution ---
        # Leaf contract (per Disco Boy): app data lives under $USERDATA_PATH/<AppName>/.
        # If userdata_path is passed explicitly (CLI --userdata, simulator, tests), it is
        # used verbatim as the app's own data directory (no "/LoongMate" appended) —
        # callers that pass it explicitly already mean "this exact directory is mine".
        if userdata_path:
            self.userdata_root: Optional[Path] = Path(userdata_path).resolve()
            self.userdata_path: Optional[Path] = self.userdata_root
        else:
            env_userdata = os.environ.get("USERDATA_PATH")
            if env_userdata:
                self.userdata_root = Path(env_userdata).resolve()
            elif self.primary_path:
                # KIỂM CHỨNG TRÊN MÁY THẬT 2026-10-02: Leaf đặt userdata theo platform,
                # tức ".userdata/mlp1/", không phải ".userdata/" trần. Log của app SSH
                # ghi rõ: state_root=/mnt/sdcard/.userdata/mlp1/umrk-ssh-server.
                platform = os.environ.get("PLATFORM") or "mlp1"
                self.userdata_root = (self.primary_path / ".userdata" / platform).resolve()
            else:
                self.userdata_root = None
                self.probe_errors.append(
                    "Không xác định được $USERDATA_PATH và không có thẻ Primary để suy ra thư mục userdata."
                )

            if self.userdata_root is not None:
                # Guard against double-nesting: some launch.sh set USERDATA_PATH to
                # .../.userdata/LoongMate already.
                if self.userdata_root.name == "LoongMate":
                    self.userdata_path = self.userdata_root
                else:
                    self.userdata_path = self.userdata_root / "LoongMate"
            else:
                self.userdata_path = None

    def get_battery_info(self) -> Dict[str, Any]:
        """Reads power supply sysfs safely without hardcoding driver names.
        Never fabricates a value: if sysfs is unavailable or the percent cannot
        be derived, returns available=False / percent=None with a reason.
        """
        power_path = Path("/sys/class/power_supply")
        if power_path.exists() and power_path.is_dir():
            try:
                supplies = list(power_path.iterdir())
            except OSError:
                supplies = []

            for supply in supplies:
                try:
                    type_file = supply / "type"
                    if not (type_file.exists() and "battery" in type_file.read_text().lower()):
                        continue

                    status = "Unknown"
                    status_file = supply / "status"
                    if status_file.exists():
                        status = status_file.read_text().strip()

                    # "capacity" is already a direct percentage. Only if it's missing
                    # do we derive percent from charge_now/charge_full or
                    # energy_now/energy_full — never from charge_now/energy_now alone,
                    # which are in µAh/µWh, not percent.
                    percent: Optional[int] = None
                    cap_file = supply / "capacity"
                    if cap_file.exists():
                        try:
                            percent = int(cap_file.read_text().strip())
                        except ValueError:
                            percent = None

                    if percent is None:
                        for now_name, full_name in [("charge_now", "charge_full"), ("energy_now", "energy_full")]:
                            now_f = supply / now_name
                            full_f = supply / full_name
                            if now_f.exists() and full_f.exists():
                                try:
                                    now_val = float(now_f.read_text().strip())
                                    full_val = float(full_f.read_text().strip())
                                    if full_val > 0:
                                        percent = max(0, min(100, int(round(now_val / full_val * 100))))
                                        break
                                except ValueError:
                                    continue

                    if percent is None:
                        return {
                            "available": False,
                            "percent": None,
                            "status": status,
                            "is_charging": status.lower() in ["charging", "full"],
                            "device": supply.name,
                            "reason": "Không đọc được phần trăm pin từ sysfs (thiếu capacity/charge/energy)."
                        }

                    return {
                        "available": True,
                        "percent": percent,
                        "status": status,
                        "is_charging": status.lower() in ["charging", "full"],
                        "device": supply.name,
                        "reason": None
                    }
                except OSError:
                    continue

        return {
            "available": False,
            "percent": None,
            "status": "Unknown",
            "is_charging": False,
            "device": None,
            "reason": "Không tìm thấy /sys/class/power_supply"
        }

    def get_thermal_info(self) -> Dict[str, Any]:
        """Reads thermal zones from Linux sysfs. Never fabricates a temperature."""
        thermal_path = Path("/sys/class/thermal")
        temperatures: List[float] = []
        zones_seen: List[str] = []
        if thermal_path.exists() and thermal_path.is_dir():
            try:
                zone_dirs = list(thermal_path.glob("thermal_zone*"))
            except OSError:
                zone_dirs = []

            for zone in zone_dirs:
                temp_file = zone / "temp"
                if temp_file.exists():
                    try:
                        raw = int(temp_file.read_text().strip())
                        # Usually in millidegrees Celsius
                        val = raw / 1000.0 if raw > 200 else float(raw)
                        temperatures.append(val)
                        zones_seen.append(zone.name)
                    except (ValueError, OSError):
                        pass

        if temperatures:
            avg_temp = sum(temperatures) / len(temperatures)
            max_temp = max(temperatures)
            return {
                "available": True,
                "current_celsius": round(avg_temp, 1),
                "max_celsius": round(max_temp, 1),
                "zone": zones_seen[0] if zones_seen else None,
                "trend": "stable" if avg_temp < 55 else "warm" if avg_temp < 70 else "hot",
                "reason": None
            }

        return {
            "available": False,
            "current_celsius": None,
            "max_celsius": None,
            "zone": None,
            "trend": "unknown",
            "reason": "Không tìm thấy /sys/class/thermal hoặc không đọc được nhiệt độ"
        }

    def get_storage_info(self, path: Optional[Path]) -> Dict[str, Any]:
        """Returns disk usage and read/write permission info for a path.
        Read-only: never writes a probe file to determine writability.
        """
        if path is None or not path.exists():
            return {
                "mounted": False,
                "path": str(path) if path is not None else None,
                "total_mb": 0,
                "free_mb": 0,
                "used_mb": 0,
                "percent_used": 0,
                "writable": False,
                "read_only_mount": False,
                "filesystem": "unknown",
                "fs_detected": False,
                "is_fat32": False
            }

        try:
            total, used, free = shutil.disk_usage(path)
            total_mb = round(total / (1024 * 1024), 1)
            free_mb = round(free / (1024 * 1024), 1)
            used_mb = round(used / (1024 * 1024), 1)
            percent = round((used / total) * 100, 1) if total > 0 else 0
        except OSError:
            total_mb, free_mb, used_mb, percent = 0, 0, 0, 0

        filesystem, fs_detected, mount_options = self._detect_filesystem(path)
        read_only_mount = mount_options is not None and "ro" in mount_options

        if read_only_mount:
            writable = False
        else:
            writable = os.access(path, os.W_OK)

        return {
            "mounted": True,
            "path": str(path),
            "total_mb": total_mb,
            "free_mb": free_mb,
            "used_mb": used_mb,
            "percent_used": percent,
            "writable": writable,
            "read_only_mount": read_only_mount,
            "filesystem": filesystem,
            "fs_detected": fs_detected,
            "is_fat32": filesystem in ["vfat", "fat32", "msdos"]
        }

    def _parse_mounts(self) -> List[Tuple[str, str, List[str]]]:
        """Parses /proc/mounts into a list of (mount_point, fstype, options)."""
        mounts_file = Path("/proc/mounts")
        entries: List[Tuple[str, str, List[str]]] = []
        if mounts_file.exists():
            try:
                for line in mounts_file.read_text().splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        mount_point, fstype, options = parts[1], parts[2], parts[3]
                        entries.append((mount_point, fstype, options.split(",")))
            except OSError:
                pass
        return entries

    def _match_mount(self, resolved_path_str: str, entries: List[Tuple[str, str, List[str]]]) -> Optional[Tuple[int, str, List[str]]]:
        """Pure longest-prefix, path-boundary-safe match of a resolved path against
        mount entries. Fixes two bugs from the previous implementation:
          (a) it used to return the *first* matching mount line instead of the
              longest-prefix (most specific) one;
          (b) it used to use bare str.startswith(), so "/mnt/SDCARD" would wrongly
              match "/mnt/SDCARD2" too (no path-component boundary check).
        """
        resolved_norm = resolved_path_str.replace("\\", "/")
        best_match: Optional[Tuple[int, str, List[str]]] = None
        for mount_point, fstype, options in entries:
            mp_norm = mount_point.rstrip("/") or "/"
            # The root mount ("/") is a boundary-safe match for every absolute path
            # by definition; every other mount point needs the explicit boundary
            # check so "/mnt/SDCARD" cannot match "/mnt/SDCARD2".
            is_match = mp_norm == "/" or resolved_norm == mp_norm or resolved_norm.startswith(mp_norm + "/")
            if is_match:
                mp_len = len(mp_norm)
                if best_match is None or mp_len > best_match[0]:
                    best_match = (mp_len, fstype, options)
        return best_match

    def _detect_filesystem(self, path: Path) -> Tuple[str, bool, Optional[List[str]]]:
        """Returns (filesystem, fs_detected, mount_options). filesystem is "unknown"
        and fs_detected is False when it genuinely cannot be determined — never a
        guessed value based on the path string (e.g. "vfat if 'SD' in str(path)").
        """
        entries = self._parse_mounts()
        if not entries:
            return "unknown", False, None

        try:
            resolved_str = str(path.resolve())
        except OSError:
            return "unknown", False, None

        best_match = self._match_mount(resolved_str, entries)
        if best_match is None:
            return "unknown", False, None

        _, fstype, options = best_match
        return fstype, True, options

    def get_network_info(self) -> Dict[str, Any]:
        """Gets IP address and connection status."""
        ip = "127.0.0.1"
        connected = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            # Doesn't have to be reachable; initiates interface discovery
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            connected = not ip.startswith("127.")
            s.close()
        except Exception:
            connected = False

        return {
            "connected": connected,
            "ip_address": ip if connected else "Disconnected",
            "hostname": socket.gethostname()
        }

    def get_readiness_summary(self) -> Dict[str, Any]:
        """Provides the 4-card readiness status for the Home screen."""
        bat = self.get_battery_info()
        therm = self.get_thermal_info()
        primary_st = self.get_storage_info(self.primary_path)
        sec_st = self.get_storage_info(self.secondary_path) if self.secondary_path else None
        net = self.get_network_info()

        # Device health status. Starts at "green" only when both signals are
        # actually measured and within safe thresholds. A missing signal bumps
        # status to "unknown" rather than concluding the device is fine; a
        # genuinely measured red/yellow condition always takes priority.
        device_status = "green"
        device_reasons: List[str] = []

        if therm["available"]:
            if therm["current_celsius"] >= 70:
                device_status = _bump_status(device_status, "red")
                device_reasons.append("Nhiệt độ cao (>70°C)")
            elif therm["current_celsius"] >= 60:
                device_status = _bump_status(device_status, "yellow")
                device_reasons.append("Nhiệt độ ấm (60-70°C)")
        else:
            device_status = _bump_status(device_status, "unknown")
            device_reasons.append(f"Không đọc được nhiệt độ: {therm.get('reason')}")

        if bat["available"]:
            if bat["percent"] <= 15 and not bat["is_charging"]:
                device_status = _bump_status(device_status, "red")
                device_reasons.append("Pin yếu (<=15%)")
            elif bat["percent"] <= 25 and not bat["is_charging"]:
                device_status = _bump_status(device_status, "yellow")
                device_reasons.append("Pin sắp cạn")
        else:
            device_status = _bump_status(device_status, "unknown")
            device_reasons.append(f"Không đọc được pin: {bat.get('reason')}")

        # Storage health status
        storage_status = "green"
        storage_reasons = []
        if not primary_st["writable"]:
            storage_status = "red"
            storage_reasons.append("Thẻ Primary bị khoá Read-Only")
        elif primary_st["free_mb"] < 200:
            storage_status = "red"
            storage_reasons.append("Thẻ Primary sắp đầy (<200MB)")
        elif primary_st["free_mb"] < 1000:
            storage_status = "yellow"
            storage_reasons.append("Thẻ Primary còn ít dung lượng (<1GB)")

        if sec_st and sec_st["mounted"]:
            # Chỉ kết luận "sai định dạng" khi ĐÃ đọc được filesystem. fs_detected=False
            # nghĩa là chưa biết, không phải sai — báo đỏ ở đây kèm lời "sai định dạng
            # (unknown)" là cảnh báo giả, và dễ khiến người dùng format nhầm thẻ.
            # doctor.py đã xử lý đúng chỗ này; readiness phải nhất quán với nó.
            if sec_st.get("fs_detected", True) and not sec_st["is_fat32"]:
                storage_status = "red"
                storage_reasons.append(f"Thẻ SD2 sai định dạng ({sec_st['filesystem']}), Leaf yêu cầu FAT32")
            elif not sec_st.get("fs_detected", True):
                if storage_status == "green":
                    storage_status = "yellow"
                storage_reasons.append("Chưa xác minh được định dạng thẻ SD2 (không đọc được /proc/mounts)")
            if not sec_st["writable"]:
                storage_status = "red"
                storage_reasons.append("Thẻ SD2 bị khoá Read-Only")
            elif sec_st["free_mb"] < 500:
                if storage_status != "red":
                    storage_status = "yellow"
                storage_reasons.append("Thẻ SD2 còn ít dung lượng (<500MB)")

        return {
            "device": {
                "status": device_status,
                "battery": bat,
                "thermal": therm,
                "warnings": device_reasons
            },
            "storage": {
                "status": storage_status,
                "primary": primary_st,
                "secondary": sec_st,
                "warnings": storage_reasons
            },
            "network": net,
            "paths": {
                "primary": str(self.primary_path) if self.primary_path else None,
                "secondary": str(self.secondary_path) if self.secondary_path else None,
                "userdata": str(self.userdata_path) if self.userdata_path else None
            }
        }
