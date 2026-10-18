"""
DeviceHealth module for LoongMate.
Runs non-destructive diagnostics, read/write latency tests, and builds
sanitized support bundles with secret redaction (Wi-Fi keys, SSH private keys, tokens).
"""

import os
import re
import sys
import json
import time
import platform
import zipfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

MAX_LOG_BUNDLE_BYTES = 256 * 1024  # 256 KB total, per the spec's "log giới hạn dung lượng".
MAX_LOG_FILES = 5

SECRET_PATTERNS = [
    (re.compile(r'-----BEGIN [A-Z\s]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z\s]+ PRIVATE KEY-----'), '[PRIVATE_KEY_REDACTED]'),
    (re.compile(r'Authorization:\s*(Bearer|Basic)\s+\S+', re.IGNORECASE), r'Authorization: \1 [REDACTED]'),
    (re.compile(r'wpa_passphrase\s+\S+\s+\S+', re.IGNORECASE), 'wpa_passphrase [REDACTED] [REDACTED]'),
    (re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.?[A-Za-z0-9_-]*'), '[REDACTED]'),
    (
        re.compile(
            r'(?i)\b(password|passwd|pwd|pass|psk|passphrase|secret|token|api[-_]?key|'
            r'access[-_]?token|refresh[-_]?token|client[-_]?secret|private[-_]?key)\b'
            r'\s*[:=]?\s*["\']?([^\s,;"\']+)'
        ),
        lambda m: f'{m.group(1)}={"[REDACTED]"}'
    ),
]

def sanitize_text(text: str) -> str:
    """Removes sensitive keys, passwords, and tokens from logs and diagnostics."""
    sanitized = text
    for pattern, repl in SECRET_PATTERNS:
        sanitized = pattern.sub(repl, sanitized)
    return sanitized

class DeviceHealth:
    def __init__(self, primary_path: Path, secondary_path: Optional[Path], userdata_path: Path):
        self.primary_path = primary_path.resolve()
        self.secondary_path = secondary_path.resolve() if secondary_path else None
        self.userdata_path = userdata_path.resolve()
        self.exports_dir = self.userdata_path / "Diagnostics"

    def run_diagnostics(self, storage: Optional[Dict[str, Any]] = None, sample_seconds: int = 0) -> Dict[str, Any]:
        """Runs non-destructive self-tests.

        storage: optional readiness["storage"] dict (with "primary"/"secondary" keys) from
        PlatformProbe. When None, the mount_and_space test is skipped rather than probing
        hardware itself, to keep this module independent of engine.probe.
        sample_seconds: reserved for a future background UI sampling window; 0 means take a
        single instantaneous sample instead of sampling across real time.
        """
        results = []

        # Test 1: Userdata read/write speed and integrity test
        rw_passed = False
        rw_latency_ms = 0
        rw_error = None
        test_file = self.userdata_path / ".health_rw_test.tmp"
        try:
            payload = b"LOONGMATE_STORAGE_TEST_PAYLOAD_" * 1024  # 32KB
            t0 = time.time()
            test_file.write_bytes(payload)
            read_back = test_file.read_bytes()
            t1 = time.time()
            if read_back == payload:
                rw_passed = True
                rw_latency_ms = round((t1 - t0) * 1000, 2)
            test_file.unlink(missing_ok=True)
        except Exception as e:
            rw_passed = False
            rw_error = str(e)
            try:
                test_file.unlink(missing_ok=True)
            except Exception:
                pass

        rw_result = {
            "test": "userdata_rw_integrity",
            "passed": rw_passed,
            "latency_ms": rw_latency_ms,
            "description": "Kiểm tra quyền ghi và toàn vẹn dữ liệu thư mục $USERDATA_PATH"
        }
        if rw_error is not None:
            rw_result["error"] = rw_error
        results.append(rw_result)

        # Test 2: Leaf system directories presence
        leaf_dirs = [
            self.primary_path / "Roms",
            self.primary_path / "Images",
            self.primary_path / "Saves",
            self.primary_path / "BIOS"
        ]
        present = [str(d.name) for d in leaf_dirs if d.exists()]
        results.append({
            "test": "leaf_directory_structure",
            "passed": len(present) >= 3,
            "present_dirs": present,
            "description": "Kiểm tra cấu trúc thư mục tiêu chuẩn của Leaf CFW"
        })

        # Test 3: Secondary card presence. A secondary path may be declared (non-None) but
        # unreachable, e.g. the card was pulled mid-session; that is the real failure case.
        if self.secondary_path:
            accessible = False
            try:
                if self.secondary_path.exists() and self.secondary_path.is_dir():
                    list(self.secondary_path.iterdir())  # forces an OSError if the card was pulled
                    accessible = True
            except OSError:
                accessible = False
            results.append({
                "test": "secondary_sd_presence",
                "passed": accessible,
                "path": str(self.secondary_path),
                "description": "Kiểm tra kết nối và trạng thái thẻ nhớ khe SD2"
            })
        else:
            results.append({
                "test": "secondary_sd_presence",
                "passed": True,
                "path": None,
                "description": "Không có thẻ Secondary được khai báo — bỏ qua hợp lệ (không phải lỗi)"
            })

        # Test 4: mount_and_space — needs storage data from PlatformProbe, passed in by the caller.
        if storage is None:
            results.append({
                "test": "mount_and_space",
                # "passed": True here only means "not evaluated, not counted as a failure" —
                # kept alongside skipped/reason so callers that read t["passed"] on every
                # test (e.g. engine/cli.py's health command) don't KeyError on a skip.
                "passed": True,
                "skipped": True,
                "reason": "Không được cung cấp dữ liệu thẻ",
                "description": "Kiểm tra filesystem, read-only mount và dung lượng trống"
            })
        else:
            mount_entries = {}
            all_ok = True
            for card_id in ("primary", "secondary"):
                st = storage.get(card_id)
                if not st:
                    continue
                free_mb = st.get("free_mb")
                read_only = bool(st.get("read_only_mount"))
                card_ok = (free_mb is not None and free_mb >= 200) and not read_only
                all_ok = all_ok and card_ok
                mount_entries[card_id] = {
                    "filesystem": st.get("filesystem"),
                    "read_only_mount": read_only,
                    "free_mb": free_mb
                }
            results.append({
                "test": "mount_and_space",
                "passed": all_ok if mount_entries else False,
                "mounts": mount_entries,
                "description": "Kiểm tra filesystem, read-only mount và dung lượng trống"
            })

        # Test 5: thermal_battery_sample
        results.append(self._thermal_battery_sample(sample_seconds))

        # Test 6: platform_identity
        results.append(self._platform_identity())

        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "all_passed": all(r.get("passed", True) for r in results),
            "tests": results
        }

    def _thermal_battery_sample(self, sample_seconds: int) -> Dict[str, Any]:
        """Samples thermal/battery sysfs directly (no engine.probe dependency, per module
        isolation rule). sample_seconds=0 takes a single instantaneous sample; a UI running
        in the background may pass a longer window to collect multiple samples over time."""
        def _take_sample() -> Dict[str, Any]:
            temp_c = None
            battery_percent = None
            charging = None
            thermal_path = Path("/sys/class/thermal")
            if thermal_path.exists():
                try:
                    for zone in thermal_path.glob("thermal_zone*"):
                        temp_file = zone / "temp"
                        if temp_file.exists():
                            raw = int(temp_file.read_text().strip())
                            temp_c = raw / 1000.0 if raw > 200 else float(raw)
                            break
                except (OSError, ValueError):
                    temp_c = None
            power_path = Path("/sys/class/power_supply")
            if power_path.exists():
                try:
                    for supply in power_path.iterdir():
                        type_file = supply / "type"
                        if type_file.exists() and "battery" in type_file.read_text().lower():
                            cap_file = supply / "capacity"
                            if cap_file.exists():
                                battery_percent = int(cap_file.read_text().strip())
                            status_file = supply / "status"
                            if status_file.exists():
                                charging = status_file.read_text().strip().lower() in ["charging", "full"]
                            break
                except (OSError, ValueError):
                    pass
            return {"timestamp": time.time(), "temperature_celsius": temp_c, "battery_percent": battery_percent, "is_charging": charging}

        samples = [_take_sample()]
        if sample_seconds > 0:
            # Reserved path for a longer background sampling window; not exercised by the
            # synchronous CLI, kept simple (single extra sample at the end of the window).
            time.sleep(min(sample_seconds, 1))
            samples.append(_take_sample())

        available = any(s["temperature_celsius"] is not None or s["battery_percent"] is not None for s in samples)
        return {
            "test": "thermal_battery_sample",
            "passed": True,
            "available": available,
            "samples": samples,
            "description": "Lấy mẫu nhiệt độ, pin và trạng thái sạc (sample_seconds=0 => một mẫu tức thời)"
        }

    def _platform_identity(self) -> Dict[str, Any]:
        """Reads real model/version identifiers only; never fabricates hardware info."""
        model = None
        model_reason = None
        dt_model_path = Path("/sys/firmware/devicetree/base/model")
        if dt_model_path.exists():
            try:
                model = dt_model_path.read_text(errors="ignore").strip("\x00\n ")
            except OSError as e:
                model_reason = str(e)
        else:
            model_reason = f"{dt_model_path} không tồn tại trên hệ điều hành này"

        os_release = {}
        os_release_path = Path("/etc/os-release")
        if os_release_path.exists():
            try:
                for line in os_release_path.read_text(errors="ignore").splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        os_release[k.strip()] = v.strip().strip('"')
            except OSError:
                pass

        leaf_version = os.environ.get("LEAF_VERSION")

        return {
            "test": "platform_identity",
            "passed": True,
            "model": model,
            "model_reason": model_reason if model is None else None,
            "os_release": os_release or None,
            "leaf_version": leaf_version,
            "description": "Đọc model/phiên bản thiết bị thật nếu có; không bịa khi không đọc được"
        }

    def _collect_real_logs(self) -> str:
        """Gathers real log files from $USERDATA_PATH/logs (created by pak/launch.sh),
        newest first, trimmed from the end (most recent content), capped at 256KB total."""
        logs_dir = self.userdata_path / "logs"
        if not logs_dir.exists() or not logs_dir.is_dir():
            return f"Không tìm thấy log nào trong {logs_dir}."

        try:
            log_files = sorted(
                [f for f in logs_dir.iterdir() if f.is_file()],
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )[:MAX_LOG_FILES]
        except OSError:
            return f"Không tìm thấy log nào trong {logs_dir}."

        if not log_files:
            return f"Không tìm thấy log nào trong {logs_dir}."

        chunks = []
        remaining = MAX_LOG_BUNDLE_BYTES
        for f in log_files:
            if remaining <= 0:
                break
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if len(data) > remaining:
                data = data[-remaining:]  # keep the most recent tail of the file
            remaining -= len(data)
            text = data.decode("utf-8", errors="ignore")
            chunks.append(f"===== {f.name} =====\n{text}")

        if not chunks:
            return f"Không tìm thấy log nào trong {logs_dir}."
        return "\n\n".join(chunks)

    def _build_environment_text(self) -> str:
        """Only real environment values; unset variables are explicitly marked, never guessed."""
        keys = ["SDCARD_PATH", "SDCARD_PATHS", "USERDATA_PATH", "PLATFORM"]
        lines = [f"{k}={os.environ.get(k, '(chưa set)')}" for k in keys]
        lines.append(f"platform.platform()={platform.platform()}")
        lines.append(f"sys.version={sys.version}")
        return "\n".join(lines)

    def _anonymize_issues(self, issues: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        """Replaces every user filename with a stable per-file token ([ITEM_01], [ITEM_02], ...),
        keeping the same filename always mapped to the same token and its extension visible
        (needed for diagnosis). 'missing' (BIOS filenames) is intentionally left untouched —
        BIOS names are not personal data, they identify emulator firmware.

        Approach: collect every real filename that appears in a structured path-like field
        across ALL issues first (file_path, target_path, folder, m3u_name, discs — anything
        with a dot in its basename, so plain directory names like "BIOS"/"Roms" are never
        mistaken for a filename), then do literal substring replacement of those exact names
        inside every text field (title/description included). A naive "filename-shaped" regex
        was tried first and failed on real game titles with spaces (e.g. "My Private Game.xyz"
        only had its last word replaced); exact known-name substitution avoids that."""
        # NOTE: ids are deliberately NOT exempted from substitution below, even though the
        # spec says to keep id/severity/category/action_type intact for diagnosis. Several
        # issue ids embed the raw filename stem (e.g. "INVALID_EXT_GBA_primary_My Private
        # Game") — leaving ids untouched would leak exactly the data privacy_mode exists to
        # hide. Only the embedded filename/stem substring is replaced; the
        # category/system/card_id prefix around it stays intact.
        token_map: Dict[str, str] = {}

        def get_token(full_name: str) -> str:
            if full_name in token_map:
                return token_map[full_name]
            _, ext = os.path.splitext(full_name)
            token = f"[ITEM_{len(token_map) + 1:02d}]{ext}"
            token_map[full_name] = token
            return token

        def bare_token_for(full_name: str) -> str:
            token = get_token(full_name)
            _, ext = os.path.splitext(full_name)
            return token[:-len(ext)] if ext else token

        multidisc_title_re = re.compile(r'^(.*?)\s*\(Disc\s*[0-9]+\)', re.IGNORECASE)

        known_names = set()
        for issue in issues:
            for field in ("file_path", "target_path", "folder"):
                v = issue.get(field)
                if isinstance(v, str):
                    base = Path(v).name
                    if "." in base:
                        known_names.add(base)
            m3u_name = issue.get("m3u_name")
            if isinstance(m3u_name, str) and "." in m3u_name:
                known_names.add(m3u_name)
            for d in (issue.get("discs") or []) + (issue.get("items") or []):
                if isinstance(d, str) and "." in d:
                    known_names.add(d)
            if isinstance(issue.get("dest"), str) and "." in Path(issue["dest"]).name:
                known_names.add(Path(issue["dest"]).name)

        # candidate substring -> replacement. For each known (extension-bearing) filename,
        # register: the full name itself, its bare stem in original case, its lowercased
        # stem (doctor.py lowercases stems for some dict keys/ids), and — for multi-disc
        # files — the shared game title with the "(Disc N)" suffix stripped (also embedded
        # bare, without extension, in MULTIDISC_NO_M3U's id/title). Stem-like variants map
        # to the bare token since id/title text has no extension context there.
        replacements: Dict[str, str] = {}
        for full_name in known_names:
            full_token = get_token(full_name)
            replacements[full_name] = full_token
            stem, _ext = os.path.splitext(full_name)
            if stem:
                bare = bare_token_for(full_name)
                replacements.setdefault(stem, bare)
                replacements.setdefault(stem.lower(), bare)
                m = multidisc_title_re.match(stem)
                if m:
                    base_title = m.group(1).strip()
                    if base_title:
                        replacements.setdefault(base_title, bare)

        # Longest-first so a full name is substituted before any shorter stem/title fragment
        # of it, avoiding double/partial substitutions.
        ordered_candidates = sorted(replacements.keys(), key=len, reverse=True)

        def replace_known(text: str) -> str:
            for candidate in ordered_candidates:
                if candidate in text:
                    text = text.replace(candidate, replacements[candidate])
            return text

        anonymized = []
        for issue in issues:
            item = dict(issue)
            for field in ("id", "title", "description", "file_path", "target_path", "folder", "m3u_name", "dest", "label"):
                if isinstance(item.get(field), str):
                    item[field] = replace_known(item[field])
            for field in ("discs", "items"):
                if isinstance(item.get(field), list):
                    item[field] = [replace_known(d) if isinstance(d, str) else d for d in item[field]]
            # "missing" (BIOS filenames) is intentionally left as-is: not personal data.
            anonymized.append(item)

        return anonymized, len(token_map)

    def export_support_bundle(self, readiness_data: Dict[str, Any], library_issues: Dict[str, Any], privacy_mode: bool = True) -> str:
        """
        Creates a support bundle ZIP containing summary, diagnostics, mounts, and sanitized logs.
        Anonymizes ROM names when privacy_mode is True.
        """
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        bundle_name = f"loongmate_support_{timestamp}.zip"
        bundle_path = self.exports_dir / bundle_name
        partial_path = self.exports_dir / f"{bundle_name}.partial"

        diag_report = self.run_diagnostics(storage=readiness_data.get("storage"))

        anonymized_count = 0
        issues_to_export = library_issues.get("issues", [])
        if privacy_mode:
            issues_to_export, anonymized_count = self._anonymize_issues(issues_to_export)

        # Sanitize and prepare summary
        summary = {
            "device": "Miniloong Pocket 1",
            "cfw": "Leaf",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "privacy_mode_enabled": privacy_mode,
            "anonymized_item_count": anonymized_count,
            "readiness": readiness_data.get("device", {}),
            "storage": readiness_data.get("storage", {}),
            "network": {
                "connected": readiness_data.get("network", {}).get("connected"),
                "ip": "192.168.x.x (anonymized)" if privacy_mode else readiness_data.get("network", {}).get("ip_address")
            }
        }

        # Real mounts info only — never fabricate mount lines when /proc/mounts is unavailable.
        mounts_path = Path("/proc/mounts")
        if mounts_path.exists():
            try:
                mounts_text = mounts_path.read_text(errors="ignore")
            except OSError:
                mounts_text = "Không đọc được /proc/mounts (lỗi I/O). Không có dữ liệu mount thật."
        else:
            mounts_text = "Không đọc được /proc/mounts (hệ điều hành này không có). Không có dữ liệu mount thật."

        logs_text = self._collect_real_logs()
        environment_text = self._build_environment_text()

        # Every text payload is sanitized before being written — redaction must cover the
        # whole bundle, not just logs.txt, since e.g. cifs mount options can carry passwords.
        summary_text = sanitize_text(json.dumps(summary, indent=2, ensure_ascii=False))
        checks_text = sanitize_text(json.dumps(diag_report, indent=2, ensure_ascii=False))
        mounts_text = sanitize_text(mounts_text)
        issues_text = sanitize_text(json.dumps(issues_to_export, indent=2, ensure_ascii=False))
        logs_text = sanitize_text(logs_text)
        environment_text = sanitize_text(environment_text)

        try:
            with zipfile.ZipFile(partial_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("summary.json", summary_text)
                z.writestr("checks.txt", checks_text)
                z.writestr("mounts.txt", mounts_text)
                z.writestr("library_issues.json", issues_text)
                z.writestr("logs.txt", logs_text)
                z.writestr("environment.txt", environment_text)
            os.replace(str(partial_path), str(bundle_path))
        except Exception:
            try:
                partial_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        return str(bundle_path)
