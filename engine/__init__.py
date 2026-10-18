"""
LoongMate Core Engine
Pocket 1 Care & Sync for Leaf CFW

Các tên công khai được nạp TRỄ (PEP 562): `from engine import SaveVault` vẫn chạy như cũ, nhưng
`import engine.probe` không còn kéo theo health/remote/inventory/doctor... (CLI được gọi cho MỖI
thao tác của giao diện native nên mỗi chục ms đều đáng kể).
"""

_LAZY = {
    "PlatformProbe": "probe",
    "InventoryEngine": "inventory", "compute_sha256": "inventory",
    "LibraryDoctor": "doctor",
    "SaveVault": "savevault",
    "DeviceHealth": "health", "sanitize_text": "health",
    "ConfigStore": "config",
    "RemoteVault": "remote", "RemoteTransport": "remote", "LocalFolderTransport": "remote",
    "SftpTransport": "remote", "probe_sftp_capability": "remote",
    "fetch_host_fingerprint": "remote", "pin_host_key": "remote",
}

__all__ = sorted(_LAZY)
__version__ = "1.0.0"


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module 'engine' has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(f"engine.{mod}"), name)
    globals()[name] = value
    return value
