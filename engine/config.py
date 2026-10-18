"""
ConfigStore module for LoongMate.
Reads/writes $USERDATA_PATH/LoongMate/config.json atomically.

Security rule (tài liệu nghiên cứu, mục "Bảo mật và dữ liệu"): "Dùng SSH key riêng
cho LoongMate ... không lưu password thô". ConfigStore.save() therefore refuses to
persist any endpoint that carries a raw secret (password/passphrase/authkey/token) —
the only supported SFTP auth is a key file referenced by 'key_path'.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Endpoint 'name' is used as a directory/path component by local-folder backends,
# so it is restricted to a safe filesystem-and-URL-safe character set.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Keys that would mean a raw secret is being stored in config.json. SFTP auth in
# LoongMate is SSH-key-only (key_path); these must never appear on a saved endpoint.
_FORBIDDEN_SECRET_KEYS = ("password", "passphrase", "authkey", "token")

_DEFAULT_CONFIG: Dict[str, Any] = {
    "config_version": 1,
    "retention": 5,
    "privacy_mode": True,
    "endpoints": []
}


class ConfigStore:
    def __init__(self, app_data_path: Path):
        self.app_data_path = Path(app_data_path).resolve()
        self.config_path = self.app_data_path / "config.json"

    def _validate_endpoint(self, ep: Dict[str, Any]) -> None:
        name = ep.get("name")
        if not name or not _NAME_RE.fullmatch(str(name)):
            raise ValueError(
                f"Tên endpoint không hợp lệ: '{name}'. Chỉ cho phép chữ cái, số, '_' và '-', "
                f"độ dài 1-32 ký tự (tên này được dùng làm tên thư mục/đường dẫn ở backend local)."
            )
        for forbidden in _FORBIDDEN_SECRET_KEYS:
            if ep.get(forbidden):
                raise ValueError(
                    f"Endpoint '{name}' chứa trường '{forbidden}' — LoongMate không lưu mật khẩu/bí mật "
                    f"thô trong config.json. Chỉ dùng xác thực bằng SSH key: đặt đường dẫn private key "
                    f"vào trường 'key_path' (tạo key riêng cho LoongMate, private key chỉ chủ sở hữu đọc "
                    f"được — xem TAILSCALE.md mục 7)."
                )

    def load(self) -> Dict[str, Any]:
        """Returns the config dict, filled with defaults for any missing top-level key.
        A missing or corrupt file returns a fresh default config instead of raising.
        """
        if not self.config_path.exists():
            return json.loads(json.dumps(_DEFAULT_CONFIG))
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return json.loads(json.dumps(_DEFAULT_CONFIG))
        if not isinstance(data, dict):
            return json.loads(json.dumps(_DEFAULT_CONFIG))
        merged = json.loads(json.dumps(_DEFAULT_CONFIG))
        merged.update(data)
        return merged

    def save(self, cfg: Dict[str, Any]) -> None:
        """Writes cfg atomically: .partial -> fsync -> os.replace.
        Raises ValueError (not caught) if any endpoint is invalid or carries a raw secret;
        callers must fix the data rather than have it silently written anyway.
        """
        for ep in cfg.get("endpoints", []):
            self._validate_endpoint(ep)

        self.app_data_path.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8")
        tmp = self.config_path.parent / f"{self.config_path.name}.partial"
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.config_path)

    def list_endpoints(self) -> List[Dict[str, Any]]:
        return self.load().get("endpoints", [])

    def get_endpoint(self, name: str) -> Optional[Dict[str, Any]]:
        for ep in self.list_endpoints():
            if ep.get("name") == name:
                return ep
        return None

    def add_endpoint(self, ep: Dict[str, Any]) -> None:
        """Adds ep, overwriting any existing endpoint with the same name."""
        self._validate_endpoint(ep)
        cfg = self.load()
        endpoints = cfg.setdefault("endpoints", [])
        endpoints[:] = [e for e in endpoints if e.get("name") != ep.get("name")]
        endpoints.append(ep)
        self.save(cfg)

    def remove_endpoint(self, name: str) -> bool:
        """Returns True if an endpoint named `name` was found and removed."""
        cfg = self.load()
        endpoints = cfg.get("endpoints", [])
        new_endpoints = [e for e in endpoints if e.get("name") != name]
        if len(new_endpoints) == len(endpoints):
            return False
        cfg["endpoints"] = new_endpoints
        self.save(cfg)
        return True


# ---------------------------------------------------------------------------
# Audit cache dùng chung cho CLI và TUI.
#
# Trước đây cli.py và tui.py mỗi bên tự định nghĩa định dạng cho cùng một file
# cache/last_audit.json: cli.py ghi phẳng, tui.py ghi lồng trong khoá "report".
# Hệ quả trên máy thật (2026-10-02): mở app từ tab Apps sau khi đã chạy `scan`
# làm TUI chết với KeyError: 'report' — màn hình đen rồi thoát. Một file thì
# phải có đúng MỘT định dạng, và chỉ một chỗ đọc/ghi nó.
# ---------------------------------------------------------------------------

AUDIT_CACHE_VERSION = 1


def audit_cache_path(app_data_path: Path) -> Path:
    """Đường dẫn file cache kết quả quét thư viện."""
    return Path(app_data_path) / "cache" / "last_audit.json"


def save_audit_cache(app_data_path: Path, report: Dict[str, Any]) -> None:
    """Lưu BẢN TÓM TẮT của báo cáo audit (không lưu toàn bộ danh sách issue).

    Chỉ lưu tóm tắt vì Home chỉ cần con số, mà thẻ nhớ thường gần đầy — ghi cả
    báo cáo đầy đủ có thể lên vài trăm KB mỗi lần quét.
    """
    path = audit_cache_path(app_data_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": AUDIT_CACHE_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(time.time()),
        "total_issues": report.get("total_issues", 0),
        "severity_counts": report.get("severity_counts", {}),
        "catalog_version": report.get("catalog_version"),
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".partial")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_audit_cache(app_data_path: Path) -> Optional[Dict[str, Any]]:
    """Đọc cache và luôn trả về định dạng phẳng, hoặc None nếu không đọc được.

    Chấp nhận cả định dạng lồng {"report": {...}, "saved_at": ...} mà bản cũ của
    tui.py từng ghi, để file có sẵn trên thẻ không làm app chết.
    """
    path = audit_cache_path(app_data_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if "report" in raw and isinstance(raw.get("report"), dict):
        report = raw["report"]
        return {
            "cache_version": 0,
            "generated_at": raw.get("saved_at") or raw.get("generated_at") or "không rõ",
            "timestamp": raw.get("timestamp", 0),
            "total_issues": report.get("total_issues", 0),
            "severity_counts": report.get("severity_counts", {}),
            "catalog_version": report.get("catalog_version"),
        }
    if "total_issues" not in raw:
        return None
    raw.setdefault("generated_at", "không rõ")
    raw.setdefault("severity_counts", {})
    return raw


# ---------------------------------------------------------------------------
# Remote Nodes (engine/nodes.py): node yeu thich + MAC cho Wake-on-LAN.
# Luu trong khoa moi "nodes" cua config.json; khoa cu khong bi dong toi, file cu
# khong co khoa nay van doc duoc (load() giu nguyen khoa la).
#   "nodes": {"favorites": ["my-pc"],
#             "macs": {"my-pc": {"mac": "aabbccddeeff",
#                                          "broadcast": "192.168.1.255"}}}
# ---------------------------------------------------------------------------

_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NODE_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$")


def _check_node_name(name: str) -> str:
    if not isinstance(name, str) or not _NODE_NAME_RE.fullmatch(name):
        raise ValueError(f"Ten node khong hop le: {name!r}")
    return name


def get_nodes_settings(store: "ConfigStore") -> Dict[str, Any]:
    """Tra ve {"favorites": [str], "macs": {ten: {"mac", "broadcast"}}}; luon du khoa."""
    raw = store.load().get("nodes")
    raw = raw if isinstance(raw, dict) else {}
    favs = raw.get("favorites")
    macs = raw.get("macs")
    return {"favorites": [f for f in favs if isinstance(f, str)] if isinstance(favs, list) else [],
            "macs": {k: v for k, v in macs.items() if isinstance(v, dict)} if isinstance(macs, dict) else {}}


def set_node_favorite(store: "ConfigStore", name: str, favorite: bool = True) -> None:
    _check_node_name(name)
    cfg = store.load()
    nodes = get_nodes_settings(store)
    nodes["favorites"] = [f for f in nodes["favorites"] if f != name]
    if favorite:
        nodes["favorites"].append(name)
    cfg["nodes"] = nodes
    store.save(cfg)


def set_node_mac(store: "ConfigStore", name: str, mac: str, broadcast: Optional[str] = None) -> None:
    """Luu MAC (chuan hoa thanh 12 ky tu hex thuong) cho node; mac rong = xoa."""
    _check_node_name(name)
    cfg = store.load()
    nodes = get_nodes_settings(store)
    if not mac:
        nodes["macs"].pop(name, None)
    else:
        if not isinstance(mac, str) or not _NODE_MAC_RE.fullmatch(mac.strip()):
            raise ValueError(f"MAC khong hop le: {mac!r}")
        entry = {"mac": re.sub(r"[^0-9a-f]", "", mac.lower())}
        if broadcast:
            entry["broadcast"] = str(broadcast)
        nodes["macs"][name] = entry
    cfg["nodes"] = nodes
    store.save(cfg)
