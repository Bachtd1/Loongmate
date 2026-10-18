"""
Gọi "Rescan Library" của Jawaka (launcher Leaf) từ LoongMate, thay cho việc bảo
người dùng bấm tay System -> Rescan Library.

GIAO THỨC (có bằng chứng, KHÔNG đoán)
-------------------------------------
Nguồn: mã nguồn Central Scrutinizer của UMRK, file `src/jawaka_ipc.c`
  https://github.com/Utility-Muffin-Research-Kitchen/CentralScrutinizer
  (hàm `cs_jawaka_request_library_rescan`, `cs_ipc_request`, `cs_jawaka_socket_path`).

* Unix stream socket, đường dẫn theo thứ tự ưu tiên:
    $JAWAKA_SOCKET_PATH | $JAWAKAD_SOCKET_PATH
    -> $UMRK_RUNTIME_PATH/jawakad.sock | $JAWAKA_RUNTIME_DIR/jawakad.sock
    -> ${TMPDIR:-/tmp}/jawaka-runtime/jawakad.sock
* Khung (frame): 4 byte độ dài big-endian (htonl) + JSON UTF-8; phản hồi cùng dạng.
* Lệnh rescan: `{"type":"scan-library"}`. Phản hồi `{"type":"ok", ...}`; nếu có cả
  `game_count` và `app_count` (số) thì quét đã xong; nếu không thì quét chạy ngầm và
  phải hỏi `{"type":"library-status"}` -> `{"type":"library-status","scan_running":bool,
  "pending_rescan":bool,"scan_error":str}` cho tới khi cả hai cờ false (CS thăm dò mỗi
  100 ms, tối đa 600 lần).
* Đối chứng trên máy thật (2026-10-02): `strings` trên central-scrutinizer chứa đúng
  các chuỗi `{"type":"scan-library"}`, `{"type":"library-status"}`,
  `%s/jawakad.sock`; umrk-leaf-session.log ghi "scan-library requested ... indexed N games".

API
---
rescan_available() -> {"available": bool, "method": str, "evidence": str, "reason": str}
    Chỉ kiểm tra socket có tồn tại, là socket, và trả lời `library-status` (lệnh chỉ-đọc).
    KHÔNG kích hoạt quét.
request_rescan(timeout=60) -> {"ok": bool, "message": str, "waited_s": float}
    Gửi `scan-library` ĐÚNG MỘT LẦN rồi chờ xong. Không bao giờ ném exception.

Chỉ dùng stdlib. Trên Windows (Python không có AF_UNIX) available=False.
"""

import json
import os
import socket
import stat
import struct
import time
from typing import Any, Dict, Optional

MAX_FRAME = 16 * 1024 * 1024  # CS_JAWAKA_IPC_MAX_FRAME
POLL_INTERVAL_S = 0.1         # CS_JAWAKA_SCAN_POLL_INTERVAL_US
POLL_MAX_FAILURES = 5         # CS_JAWAKA_SCAN_POLL_MAX_FAILURES

_METHOD = "jawakad unix socket: frame u32-BE + JSON, lệnh scan-library / library-status"
_EVIDENCE = ("CentralScrutinizer/src/jawaka_ipc.c: cs_ipc_request(socket_path, "
             "\"{\\\"type\\\":\\\"scan-library\\\"}\") ; chờ bằng \"{\\\"type\\\":\\\"library-status\\\"}\"")


class IpcError(Exception):
    pass


def socket_path(env: Optional[Dict[str, str]] = None) -> str:
    """Cùng thứ tự ưu tiên với cs_jawaka_socket_path (bản không phải macOS)."""
    env = os.environ if env is None else env

    def first(*names):
        for n in names:
            v = env.get(n)
            if v:
                return v
        return None

    override = first("JAWAKA_SOCKET_PATH", "JAWAKAD_SOCKET_PATH")
    if override:
        return override
    runtime = first("UMRK_RUNTIME_PATH", "JAWAKA_RUNTIME_DIR")
    if runtime:
        return runtime + "/jawakad.sock"
    return (env.get("TMPDIR") or "/tmp") + "/jawaka-runtime/jawakad.sock"


def _read_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise IpcError("jawakad đóng kết nối giữa chừng")
        buf += chunk
    return buf


def _request(path: str, obj: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    if not hasattr(socket, "AF_UNIX"):
        raise IpcError("Python trên máy này không hỗ trợ unix socket")
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(struct.pack(">I", len(data)) + data)
        (n,) = struct.unpack(">I", _read_exact(s, 4))
        if n > MAX_FRAME:
            raise IpcError("phản hồi quá lớn")
        payload = _read_exact(s, n)
    except (OSError, socket.timeout) as e:
        raise IpcError(f"jawakad không phản hồi: {e}")
    finally:
        s.close()
    try:
        resp = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise IpcError("jawakad trả JSON không hợp lệ")
    if not isinstance(resp, dict):
        raise IpcError("jawakad trả JSON không phải object")
    return resp


def rescan_available(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or socket_path()
    out = {"available": False, "method": _METHOD, "evidence": _EVIDENCE, "reason": ""}
    if not hasattr(socket, "AF_UNIX"):
        out["reason"] = "Python trên máy này không có unix socket (chạy trên Windows?)."
        return out
    try:
        st = os.stat(path)
    except OSError:
        out["reason"] = f"Không thấy {path} — Jawaka chưa chạy hoặc sai $UMRK_RUNTIME_PATH."
        return out
    if not stat.S_ISSOCK(st.st_mode):
        out["reason"] = f"{path} không phải socket."
        return out
    try:
        resp = _request(path, {"type": "library-status"}, 3.0)
    except IpcError as e:
        out["reason"] = str(e)
        return out
    if resp.get("type") != "library-status":
        out["reason"] = "jawakad trả lời library-status không đúng định dạng."
        return out
    out["available"] = True
    return out


def request_rescan(timeout: float = 60, path: Optional[str] = None) -> Dict[str, Any]:
    """Gửi scan-library một lần và chờ xong. Nếu thất bại, người dùng vẫn có thể bấm
    tay System -> Rescan Library."""
    path = path or socket_path()
    t0 = time.monotonic()

    def done(ok, msg):
        return {"ok": ok, "message": msg, "waited_s": round(time.monotonic() - t0, 2)}

    def left():
        return max(0.5, timeout - (time.monotonic() - t0))

    try:
        resp = _request(path, {"type": "scan-library"}, left())
    except IpcError as e:
        return done(False, f"Không gọi được jawakad ({e}). Hãy bấm tay System -> Rescan Library.")
    if resp.get("type") != "ok":
        return done(False, "jawakad từ chối lệnh rescan. Hãy bấm tay System -> Rescan Library.")

    g, a = resp.get("game_count"), resp.get("app_count")
    num = (int, float)
    if isinstance(g, num) and isinstance(a, num) and not isinstance(g, bool) and not isinstance(a, bool):
        return done(True, f"Quét xong: {int(g)} game, {int(a)} app.")

    # Quét chạy ngầm: thăm dò library-status như Central Scrutinizer.
    failures = 0
    while time.monotonic() - t0 < timeout:
        try:
            st = _request(path, {"type": "library-status"}, left())
            if st.get("type") != "library-status":
                raise IpcError("library-status sai định dạng")
            failures = 0
            if not st.get("scan_running") and not st.get("pending_rescan"):
                err = st.get("scan_error")
                if isinstance(err, str) and err:
                    return done(False, f"Quét lỗi: {err}")
                return done(True, "Quét xong.")
        except IpcError:
            failures += 1
            if failures >= POLL_MAX_FAILURES:
                return done(False, "Không đọc được trạng thái quét thư viện.")
        time.sleep(POLL_INTERVAL_S)
    return done(False, f"Hết {timeout}s mà quét chưa xong (có thể vẫn đang chạy ngầm).")
