"""
Remote Nodes: liệt kê / kiểm tra các máy trong tailnet (PC, Mac, Pi, NAS).

Nguyên tắc (tài liệu nghiên cứu, "Khả năng mở rộng"): Tailscale đã được cài ngoài
LoongMate; module này chỉ ĐỌC trạng thái và dùng hostname/IP tailnet như địa chỉ mạng,
không quản lý daemon.

Bối cảnh Pocket 1: tailscaled chạy userspace (không có /dev/net/tun) -> chiều VÀO tới
được, chiều RA phải qua SOCKS5 localhost:1055; không có ICMP ping trong tailnet.

API
---
list_nodes(ts_bin=None, ts_sock=None, store=None) -> dict
    {"ok", "error", "backend_state", "self": node, "nodes": [node, ...]}
    node = {"name", "hostname", "dns_name", "ip", "ips", "os", "online",
            "last_seen" (chuỗi ISO hoặc None khi đang online), "path"
            ("direct" | "relay" | "unknown"), "relay" (mã vùng DERP), "cur_addr",
            "favorite", "mac"}   (favorite/mac chỉ có nghĩa khi truyền `store`)
    path: "direct" nếu CurAddr có giá trị; "relay" nếu online, chưa có CurAddr và có
    vùng DERP; "unknown" nếu offline. Đây là trạng thái LẦN CUỐI biết — muốn đo thật
    dùng ping_node().
parse_status(data: dict) -> dict          (hàm thuần, dùng cho test)
probe_node(node, ports=DEFAULT_PORTS, socks=("127.0.0.1", 1055), timeout=5.0) -> dict
    {"ok", "error", "host", "ports": [{"port","label","state","detail","ms"}]}
    state: "open" | "closed" | "unreachable" | "timeout" | "error".
    "timeout" gồm cả trường hợp tailscaled trả SOCKS5 REP=1 (dial hết hạn, thường do tường lửa
    đích drop gói); "closed" chỉ khi đích chủ động từ chối (REP=5).
    socks=None -> nối thẳng (dùng khi chạy trên máy có TUN, ví dụ PC).
ping_node(name, ts_bin=None, ts_sock=None) -> dict
    {"ok", "latency_ms", "via" ("direct" | "derp"), "derp", "endpoint", "message"}
    Dùng `tailscale ping --c 1 --timeout 5s`. Lưu ý: mã thoát của tailscale là 1 ngay cả
    khi có pong (vì chưa có đường trực tiếp) nên kết quả được suy từ output, không từ rc.
build_magic_packet(mac) -> bytes          6x0xFF + 16 x MAC (102 byte)
wake_on_lan(mac, broadcast="255.255.255.255", port=9) -> dict
    {"ok", "message", "unverified"}. CHƯA KIỂM CHỨNG: UDP broadcast có ra được qua
    Wi-Fi của Pocket 1 hay không (nhiều AP chặn broadcast / client isolation), và PC
    đích phải bật WOL trong BIOS + driver. "ok" chỉ nghĩa là gói đã được gửi đi.
    Gói đi qua LAN, KHÔNG qua tailnet.

Chỉ dùng stdlib.
"""

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

DEVICE_TS_BIN = "/mnt/sdcard/.userdata/mlp1/tailscale/tailscale"
DEVICE_TS_SOCK = "/userdata/tailscale-state/tailscaled.sock"
DEFAULT_SOCKS = ("127.0.0.1", 1055)

PORT_LABELS = {
    22: "SSH (điều khiển từ xa bằng dòng lệnh)",
    445: "SMB (chia sẻ file Windows)",
    47989: "Sunshine (máy chủ Moonlight, stream game)",
    3389: "RDP (Remote Desktop Windows)",
}
DEFAULT_PORTS = (22, 445, 47989, 3389)

_SAFE_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$")


# --------------------------------------------------------------------------- tailscale
def _device_ts_bin():
    # Hai thẻ nhớ có thể đổi chỗ mount sau khi khởi động lại: ưu tiên USERDATA_PATH do Leaf công bố
    # (cùng cách Tailscale.pak/launch.sh chọn TS_DIR), hằng DEVICE_TS_BIN chỉ là mặc định cuối.
    ud = os.environ.get("USERDATA_PATH")
    return os.path.join(ud, "tailscale", "tailscale") if ud else DEVICE_TS_BIN


def _resolve_ts(ts_bin, ts_sock):
    if not ts_bin:
        dev_bin = _device_ts_bin()
        ts_bin = dev_bin if os.path.exists(dev_bin) else shutil.which("tailscale")
    if not ts_sock and os.path.exists(DEVICE_TS_SOCK):
        ts_sock = DEVICE_TS_SOCK
    return ts_bin, ts_sock


def _run_ts(args: List[str], ts_bin, ts_sock, timeout: float) -> Tuple[Optional[int], str, str]:
    """Trả (returncode, stdout, stderr); returncode None nếu không chạy được."""
    ts_bin, ts_sock = _resolve_ts(ts_bin, ts_sock)
    if not ts_bin:
        return None, "", "Không tìm thấy binary tailscale."
    cmd = [ts_bin] + (["--socket=" + ts_sock] if ts_sock else []) + args
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "tailscale không trả lời kịp."
    except OSError as e:
        return None, "", f"Không chạy được tailscale: {e}"
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def _node_from_raw(p: Dict[str, Any]) -> Dict[str, Any]:
    dns = (p.get("DNSName") or "").rstrip(".")
    hostname = p.get("HostName") or ""
    # DNSName là nhãn duy nhất và là thứ `tailscale ping` phân giải được; HostName có
    # thể trùng/sai (iPad báo "localhost").
    name = dns.split(".")[0] if dns else hostname
    ips = list(p.get("TailscaleIPs") or [])
    ip4 = next((i for i in ips if ":" not in i), ips[0] if ips else "")
    online = bool(p.get("Online"))
    last_seen = p.get("LastSeen") or None
    if online or (last_seen and last_seen.startswith("0001-01-01")):
        last_seen = None
    cur_addr = p.get("CurAddr") or ""
    relay = p.get("Relay") or ""
    if not online:
        path = "unknown"
    elif cur_addr:
        path = "direct"
    elif relay:
        path = "relay"
    else:
        path = "unknown"
    return {"name": name, "hostname": hostname, "dns_name": dns, "ip": ip4, "ips": ips,
            "os": p.get("OS") or "", "online": online, "last_seen": last_seen,
            "path": path, "relay": relay, "cur_addr": cur_addr,
            "favorite": False, "mac": ""}


def parse_status(data: Dict[str, Any]) -> Dict[str, Any]:
    """Chuyển JSON của `tailscale status --json` thành kết quả của list_nodes."""
    if not isinstance(data, dict):
        return {"ok": False, "error": "Đầu ra tailscale không phải JSON object.",
                "backend_state": "", "self": None, "nodes": []}
    peers = data.get("Peer") or {}
    nodes = [_node_from_raw(p) for p in peers.values() if isinstance(p, dict)]
    # Online trước, rồi theo tên.
    nodes.sort(key=lambda n: (not n["online"], n["name"].lower()))
    me = data.get("Self")
    return {"ok": True, "error": "", "backend_state": data.get("BackendState") or "",
            "self": _node_from_raw(me) if isinstance(me, dict) else None, "nodes": nodes}


def list_nodes(ts_bin=None, ts_sock=None, store=None) -> Dict[str, Any]:
    rc, out, err = _run_ts(["status", "--json"], ts_bin, ts_sock, 15)
    if rc is None or not out.strip():
        msg = err.strip() or "tailscale status không trả về dữ liệu."
        return {"ok": False, "error": msg, "backend_state": "", "self": None, "nodes": []}
    try:
        data = json.loads(out)
    except ValueError:
        return {"ok": False, "error": "tailscale status --json trả về JSON hỏng.",
                "backend_state": "", "self": None, "nodes": []}
    result = parse_status(data)
    if store is not None and result["ok"]:
        try:
            from engine.config import get_nodes_settings
            st = get_nodes_settings(store)
        except Exception:
            st = {"favorites": [], "macs": {}}
        for n in result["nodes"]:
            n["favorite"] = n["name"] in st["favorites"]
            n["mac"] = st["macs"].get(n["name"], {}).get("mac", "")
        result["nodes"].sort(key=lambda n: (not n["favorite"], not n["online"], n["name"].lower()))
    return result


# --------------------------------------------------------------------------- SOCKS5
_SOCKS_REP = {
    # Quan sát trên máy thật: tới PC Windows bật tường lửa, cổng không có dịch vụ, tailscaled
    # trả REP=1 sau ~5 giây (dial hết hạn), KHÔNG trả REP=5. Nên coi là "không phản hồi".
    1: ("timeout", "không phản hồi (thường là tường lửa chặn hoặc chưa có dịch vụ ở cổng này)"),
    2: ("error", "SOCKS5: bị chặn bởi luật"),
    3: ("unreachable", "mạng không tới được"),
    4: ("unreachable", "máy không tới được"),
    5: ("closed", "cổng đóng (từ chối kết nối)"),
    6: ("timeout", "hết thời gian"),
    7: ("error", "SOCKS5: lệnh không hỗ trợ"),
    8: ("error", "SOCKS5: kiểu địa chỉ không hỗ trợ"),
}


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError("proxy đóng kết nối giữa chừng")
        buf += c
    return buf


def socks5_connect_state(proxy: Tuple[str, int], host: str, port: int, timeout: float) -> Tuple[str, str]:
    """Client SOCKS5 tối giản (RFC 1928, không xác thực, CONNECT).
    Trả (state, detail) với state thuộc open/closed/unreachable/timeout/error;
    state 'proxy' nghĩa là không nói chuyện được với chính proxy."""
    try:
        s = socket.create_connection(proxy, timeout=min(timeout, 3.0))
    except OSError as e:
        return "proxy", f"Không nối được SOCKS5 {proxy[0]}:{proxy[1]} ({e})"
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        greet = _recv_exact(s, 2)
        if greet != b"\x05\x00":
            return "proxy", f"SOCKS5 không chấp nhận không-xác-thực (trả {greet.hex()})"
        try:
            addr = b"\x01" + socket.inet_aton(host)
        except OSError:
            hb = host.encode("idna")
            if len(hb) > 255:
                return "error", "tên máy quá dài"
            addr = b"\x03" + bytes([len(hb)]) + hb
        s.sendall(b"\x05\x01\x00" + addr + struct.pack(">H", port))
        head = _recv_exact(s, 4)
        if head[0] != 5:
            return "proxy", "phản hồi không phải SOCKS5"
        # đọc nốt BND.ADDR/BND.PORT cho đủ khung
        atyp = head[3]
        if atyp == 1:
            _recv_exact(s, 6)
        elif atyp == 4:
            _recv_exact(s, 18)
        elif atyp == 3:
            _recv_exact(s, _recv_exact(s, 1)[0] + 2)
        rep = head[1]
        if rep == 0:
            return "open", "kết nối được"
        return _SOCKS_REP.get(rep, ("error", f"SOCKS5 mã {rep}"))
    except socket.timeout:
        return "timeout", "hết thời gian chờ"
    except (OSError, ConnectionError) as e:
        return "error", str(e)
    finally:
        s.close()


def _direct_connect_state(host: str, port: int, timeout: float) -> Tuple[str, str]:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return "open", "kết nối được"
    except socket.timeout:
        return "timeout", "hết thời gian chờ"
    except ConnectionRefusedError:
        return "closed", "cổng đóng (từ chối kết nối)"
    except OSError as e:
        return "unreachable", str(e)


def probe_node(node, ports=DEFAULT_PORTS, socks=DEFAULT_SOCKS, timeout: float = 5.0) -> Dict[str, Any]:
    """node: dict của list_nodes (dùng 'ip') hoặc chuỗi IP/hostname."""
    host = node.get("ip") or node.get("name") if isinstance(node, dict) else str(node)
    if not host or not _SAFE_TARGET.match(host):
        return {"ok": False, "error": "Địa chỉ node không hợp lệ.", "host": host or "", "ports": []}
    ports = list(ports)
    results: Dict[int, Dict[str, Any]] = {}

    def work(port):
        t0 = time.monotonic()
        if socks:
            state, detail = socks5_connect_state(tuple(socks), host, port, timeout)
        else:
            state, detail = _direct_connect_state(host, port, timeout)
        results[port] = {"port": port, "label": PORT_LABELS.get(port, f"TCP {port}"),
                         "state": state, "detail": detail,
                         "ms": int((time.monotonic() - t0) * 1000)}

    threads = [threading.Thread(target=work, args=(p,), daemon=True) for p in ports]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 5)
    rows = [results.get(p) or {"port": p, "label": PORT_LABELS.get(p, f"TCP {p}"),
                               "state": "timeout", "detail": "không có kết quả", "ms": 0}
            for p in ports]
    proxy_down = [r for r in rows if r["state"] == "proxy"]
    if proxy_down:
        return {"ok": False, "error": proxy_down[0]["detail"] + " — Tailscale có đang chạy không?",
                "host": host, "ports": []}
    return {"ok": True, "error": "", "host": host, "ports": rows}


# --------------------------------------------------------------------------- ping
_PONG_RE = re.compile(r"pong from (\S+) \(([^)]+)\) via (?:DERP\(([^)]*)\)|(\S+)) in ([0-9.]+)(ms|s)")


def parse_ping(out: str) -> Dict[str, Any]:
    m = _PONG_RE.search(out)
    if not m:
        return {"ok": False, "latency_ms": None, "via": "", "derp": "", "endpoint": "",
                "message": (out.strip().splitlines() or ["không có phản hồi"])[-1]}
    ms = float(m.group(5)) * (1000 if m.group(6) == "s" else 1)
    derp = m.group(3) or ""
    return {"ok": True, "latency_ms": ms, "via": "derp" if m.group(3) is not None else "direct",
            "derp": derp, "endpoint": m.group(4) or "",
            "message": f"pong từ {m.group(1)} qua {'DERP ' + derp if derp else m.group(4)} sau {ms:g} ms"}


def ping_node(name: str, ts_bin=None, ts_sock=None) -> Dict[str, Any]:
    if not isinstance(name, str) or not _SAFE_TARGET.match(name):
        return {"ok": False, "latency_ms": None, "via": "", "derp": "", "endpoint": "",
                "message": "Tên/IP node không hợp lệ."}
    rc, out, err = _run_ts(["ping", "--c", "1", "--timeout", "5s", name], ts_bin, ts_sock, 20)
    if rc is None:
        return {"ok": False, "latency_ms": None, "via": "", "derp": "", "endpoint": "", "message": err.strip()}
    res = parse_ping(out)
    if not res["ok"] and err.strip():
        res["message"] = err.strip().splitlines()[-1]
    return res


# --------------------------------------------------------------------------- WOL
def normalize_mac(mac: str) -> str:
    if not isinstance(mac, str) or not _MAC_RE.match(mac.strip()):
        raise ValueError(f"MAC không hợp lệ: {mac!r}")
    return re.sub(r"[^0-9a-f]", "", mac.lower())


def build_magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac)) * 16


def wake_on_lan(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> Dict[str, Any]:
    try:
        pkt = build_magic_packet(mac)
    except ValueError as e:
        return {"ok": False, "message": str(e), "unverified": True}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, (broadcast, port))
    except OSError as e:
        return {"ok": False, "message": f"Không gửi được gói WOL: {e}", "unverified": True}
    finally:
        s.close()
    return {"ok": True, "unverified": True,
            "message": f"Đã gửi gói đánh thức tới {broadcast}:{port}. Chưa xác nhận máy có dậy — "
                       f"cần WOL bật trong BIOS và Wi-Fi/AP cho phép broadcast."}
