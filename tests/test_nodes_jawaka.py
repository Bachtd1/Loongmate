"""
Test cho engine/nodes.py (Remote Nodes) và engine/jawaka.py (Rescan Library).

* nodes: parse `tailscale status --json` (mẫu có cấu trúc lấy từ máy thật, tên/IP đã
  ẩn danh), client SOCKS5 chạy thật với server giả trên localhost, gói WOL gửi thật qua
  UDP loopback và so từng byte.
* jawaka: server giả trên unix socket nói đúng giao thức của Central Scrutinizer
  (frame u32-BE + JSON). Bị bỏ qua nếu Python không có AF_UNIX (Windows) — khi đó chạy
  bộ test này trên Pocket 1.
"""

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import nodes, jawaka
from engine.config import ConfigStore, get_nodes_settings, set_node_favorite, set_node_mac

STATUS = {
    "BackendState": "Running",
    "Self": {"HostName": "pocket1", "DNSName": "pocket1.example.ts.net.", "OS": "linux",
             "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"], "Online": True,
             "CurAddr": "", "Relay": "hkg", "LastSeen": "0001-01-01T00:00:00Z"},
    "Peer": {
        "k1": {"HostName": "localhost", "DNSName": "ipad.example.ts.net.", "OS": "iOS",
               "TailscaleIPs": ["100.64.0.2", "fd7a:115c:a1e0::2"], "Online": False,
               "CurAddr": "", "Relay": "", "LastSeen": "2026-09-16T20:34:53.1Z"},
        "k2": {"HostName": "DESKTOP-AAAA", "DNSName": "desktop-aaaa.example.ts.net.",
               "OS": "windows", "TailscaleIPs": ["100.64.0.3", "fd7a:115c:a1e0::3"],
               "Online": True, "CurAddr": "", "Relay": "hkg",
               "LastSeen": "0001-01-01T00:00:00Z"},
        "k3": {"HostName": "nas", "DNSName": "nas.example.ts.net.", "OS": "linux",
               "TailscaleIPs": ["100.64.0.4"], "Online": True,
               "CurAddr": "192.168.1.50:41641", "Relay": "sin",
               "LastSeen": "0001-01-01T00:00:00Z"},
    },
}


class TestParseStatus(unittest.TestCase):
    def test_names_ips_online(self):
        r = nodes.parse_status(STATUS)
        self.assertTrue(r["ok"])
        self.assertEqual(r["backend_state"], "Running")
        by = {n["name"]: n for n in r["nodes"]}
        # tên lấy từ DNSName (iPad báo HostName 'localhost')
        self.assertEqual(set(by), {"ipad", "desktop-aaaa", "nas"})
        self.assertEqual(by["desktop-aaaa"]["ip"], "100.64.0.3")
        self.assertTrue(by["desktop-aaaa"]["online"])
        self.assertFalse(by["ipad"]["online"])
        self.assertEqual(by["ipad"]["last_seen"], "2026-09-16T20:34:53.1Z")
        self.assertIsNone(by["nas"]["last_seen"])  # online -> không có last_seen
        self.assertEqual(by["nas"]["path"], "direct")
        self.assertEqual(by["desktop-aaaa"]["path"], "relay")
        self.assertEqual(by["ipad"]["path"], "unknown")
        self.assertEqual(r["self"]["name"], "pocket1")
        # online xếp trước offline
        self.assertEqual(r["nodes"][-1]["name"], "ipad")

    def test_garbage(self):
        self.assertFalse(nodes.parse_status([])["ok"])
        self.assertEqual(nodes.parse_status({})["nodes"], [])

    def test_list_nodes_with_store_marks_favorite_and_mac(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfigStore(Path(d))
            set_node_favorite(store, "nas")
            set_node_mac(store, "desktop-aaaa", "AA:BB:CC:DD:EE:FF")
            orig = nodes._run_ts
            nodes._run_ts = lambda *a, **k: (0, json.dumps(STATUS), "")
            try:
                r = nodes.list_nodes(store=store)
            finally:
                nodes._run_ts = orig
            self.assertTrue(r["ok"])
            self.assertEqual(r["nodes"][0]["name"], "nas")  # yêu thích lên đầu
            self.assertTrue(r["nodes"][0]["favorite"])
            by = {n["name"]: n for n in r["nodes"]}
            self.assertEqual(by["desktop-aaaa"]["mac"], "aabbccddeeff")

    def test_list_nodes_no_binary(self):
        orig = nodes._run_ts
        nodes._run_ts = lambda *a, **k: (None, "", "Không tìm thấy binary tailscale.")
        try:
            r = nodes.list_nodes()
        finally:
            nodes._run_ts = orig
        self.assertFalse(r["ok"])
        self.assertIn("tailscale", r["error"])


class FakeSocks5(threading.Thread):
    """Server SOCKS5 giả: rep_by_port[port] = mã REP; port thiếu -> không trả lời (treo)."""

    def __init__(self, rep_by_port):
        super().__init__(daemon=True)
        self.rep_by_port = rep_by_port
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.addr = self.srv.getsockname()
        self.requests = []
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        try:
            c.settimeout(5)
            ver, n = c.recv(2)
            c.recv(n)
            c.sendall(b"\x05\x00")
            ver, cmd, rsv, atyp = c.recv(4)
            if atyp == 1:
                host = socket.inet_ntoa(c.recv(4))
            else:
                ln = c.recv(1)[0]
                host = c.recv(ln).decode()
            (port,) = struct.unpack(">H", c.recv(2))
            self.requests.append((cmd, host, port))
            rep = self.rep_by_port.get(port)
            if rep is None:
                time.sleep(3)
                return
            c.sendall(bytes([5, rep, 0, 1]) + b"\x00\x00\x00\x00\x00\x00")
        except Exception:
            pass
        finally:
            c.close()

    def close(self):
        self._stop = True
        self.srv.close()


class TestSocks5Probe(unittest.TestCase):
    def test_open_closed_unreachable_timeout(self):
        fake = FakeSocks5({22: 0, 445: 5, 3389: 4, 47989: 1})
        fake.start()
        try:
            r = nodes.probe_node({"ip": "100.64.0.3"}, socks=fake.addr, timeout=1.0)
        finally:
            fake.close()
        self.assertTrue(r["ok"])
        st = {p["port"]: p["state"] for p in r["ports"]}
        self.assertEqual(st, {22: "open", 445: "closed", 47989: "timeout", 3389: "unreachable"})  # REP=1 (dial hết hạn) -> timeout
        # nhãn tiếng Việt và đúng đích đã gửi cho proxy
        self.assertIn("SSH", r["ports"][0]["label"])
        self.assertIn((1, "100.64.0.3", 22), fake.requests)

    def test_silent_proxy_times_out(self):
        fake = FakeSocks5({})  # proxy chào xong rồi im
        fake.start()
        try:
            state, _ = nodes.socks5_connect_state(fake.addr, "100.64.0.3", 22, 0.5)
        finally:
            fake.close()
        self.assertEqual(state, "timeout")

    def test_domain_name_uses_atyp3(self):
        fake = FakeSocks5({22: 0})
        fake.start()
        try:
            state, _ = nodes.socks5_connect_state(fake.addr, "desktop-aaaa", 22, 2.0)
        finally:
            fake.close()
        self.assertEqual(state, "open")
        self.assertIn((1, "desktop-aaaa", 22), fake.requests)

    def test_proxy_down_is_not_reported_as_closed_ports(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        addr = s.getsockname()
        s.close()  # không ai nghe
        r = nodes.probe_node({"ip": "100.64.0.3"}, socks=addr, timeout=1.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["ports"], [])
        self.assertIn("SOCKS5", r["error"])

    def test_rejects_option_like_host(self):
        self.assertFalse(nodes.probe_node("-evil")["ok"])


class TestWol(unittest.TestCase):
    def test_magic_packet_bytes(self):
        pkt = nodes.build_magic_packet("AA:bb-cc:DD:ee:ff")
        self.assertEqual(len(pkt), 102)
        self.assertEqual(pkt[:6], b"\xff" * 6)
        for i in range(16):
            self.assertEqual(pkt[6 + 6 * i: 12 + 6 * i], bytes.fromhex("aabbccddeeff"))
        self.assertEqual(nodes.build_magic_packet("aabbccddeeff"), pkt)

    def test_bad_mac(self):
        for bad in ("", "aa:bb:cc", "zz:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff:00", None):
            self.assertFalse(nodes.wake_on_lan(bad)["ok"], bad)

    def test_packet_actually_sent_over_udp(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(3)
        port = rx.getsockname()[1]
        try:
            r = nodes.wake_on_lan("01:23:45:67:89:ab", broadcast="127.0.0.1", port=port)
            data, _ = rx.recvfrom(2048)
        finally:
            rx.close()
        self.assertTrue(r["ok"])
        self.assertTrue(r["unverified"])
        self.assertEqual(data, b"\xff" * 6 + bytes.fromhex("0123456789ab") * 16)


class TestPingParse(unittest.TestCase):
    def test_derp(self):
        r = nodes.parse_ping("pong from desktop-faehddl (100.120.106.121) via DERP(hkg) in 568ms\n"
                             "direct connection not established\n")
        self.assertTrue(r["ok"])
        self.assertEqual((r["via"], r["derp"], r["latency_ms"]), ("derp", "hkg", 568.0))

    def test_direct(self):
        r = nodes.parse_ping("pong from nas (100.64.0.4) via 192.168.1.50:41641 in 7ms")
        self.assertEqual((r["via"], r["endpoint"], r["latency_ms"]), ("direct", "192.168.1.50:41641", 7.0))

    def test_failure_and_bad_name(self):
        self.assertFalse(nodes.parse_ping("direct connection not established")["ok"])
        self.assertFalse(nodes.ping_node("--evil")["ok"])


class TestNodeConfig(unittest.TestCase):
    def test_roundtrip_and_backward_compat(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfigStore(Path(d))
            # config cũ không có khoá 'nodes'
            store.save({"config_version": 1, "retention": 9, "endpoints": []})
            self.assertEqual(get_nodes_settings(store), {"favorites": [], "macs": {}})
            set_node_favorite(store, "nas")
            set_node_mac(store, "nas", "AA-BB-CC-DD-EE-FF", "192.168.1.255")
            s = get_nodes_settings(store)
            self.assertEqual(s["favorites"], ["nas"])
            self.assertEqual(s["macs"]["nas"], {"mac": "aabbccddeeff", "broadcast": "192.168.1.255"})
            self.assertEqual(store.load()["retention"], 9)  # khoá cũ nguyên vẹn
            set_node_favorite(store, "nas", False)
            set_node_mac(store, "nas", "")
            self.assertEqual(get_nodes_settings(store), {"favorites": [], "macs": {}})

    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfigStore(Path(d))
            with self.assertRaises(ValueError):
                set_node_mac(store, "nas", "nope")
            with self.assertRaises(ValueError):
                set_node_favorite(store, "../x")
            Path(d, "config.json").write_text('{"nodes": "rác"}', encoding="utf-8")
            self.assertEqual(get_nodes_settings(store), {"favorites": [], "macs": {}})


# --------------------------------------------------------------------------- jawaka
@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Python không có AF_UNIX (chạy trên Pocket 1)")
class TestJawaka(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jw")
        self.path = os.path.join(self.dir, "jawakad.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.seen = []
        self.handler = lambda req: {"type": "ok"}
        self._stop = False
        threading.Thread(target=self.loop, daemon=True).start()

    def tearDown(self):
        self._stop = True
        self.srv.close()
        try:
            os.unlink(self.path)
            os.rmdir(self.dir)
        except OSError:
            pass

    def loop(self):
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                (n,) = struct.unpack(">I", c.recv(4))
                req = json.loads(c.recv(n).decode())
                self.seen.append(req["type"])
                resp = self.handler(req)
                if resp is not None:
                    b = json.dumps(resp).encode()
                    c.sendall(struct.pack(">I", len(b)) + b)
            finally:
                c.close()

    def test_available(self):
        self.handler = lambda req: {"type": "library-status", "scan_running": False,
                                    "pending_rescan": False}
        r = jawaka.rescan_available(self.path)
        self.assertTrue(r["available"], r)
        self.assertEqual(self.seen, ["library-status"])  # không kích hoạt quét

    def test_unavailable_reasons(self):
        r = jawaka.rescan_available(os.path.join(self.dir, "none.sock"))
        self.assertFalse(r["available"])
        self.assertTrue(r["reason"])
        self.assertFalse(jawaka.rescan_available(self.dir)["available"])  # không phải socket

    def test_sync_response(self):
        self.handler = lambda req: {"type": "ok", "game_count": 3767, "app_count": 8}
        r = jawaka.request_rescan(5, path=self.path)
        self.assertTrue(r["ok"], r)
        self.assertIn("3767", r["message"])
        self.assertEqual(self.seen, ["scan-library"])  # đúng một lần, không poll

    def test_async_polls_until_done(self):
        state = {"n": 0}

        def h(req):
            if req["type"] == "scan-library":
                return {"type": "ok"}
            state["n"] += 1
            return {"type": "library-status", "scan_running": state["n"] < 4,
                    "pending_rescan": False, "scan_error": ""}
        self.handler = h
        r = jawaka.request_rescan(10, path=self.path)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.seen[0], "scan-library")
        self.assertEqual(self.seen.count("scan-library"), 1)
        self.assertEqual(self.seen.count("library-status"), 4)

    def test_scan_error_and_rejection_and_timeout(self):
        def h(req):
            if req["type"] == "scan-library":
                return {"type": "ok"}
            return {"type": "library-status", "scan_running": False,
                    "pending_rescan": False, "scan_error": "đĩa lỗi"}
        self.handler = h
        r = jawaka.request_rescan(5, path=self.path)
        self.assertFalse(r["ok"])
        self.assertIn("đĩa lỗi", r["message"])

        self.handler = lambda req: {"type": "error"}
        self.assertFalse(jawaka.request_rescan(5, path=self.path)["ok"])

        self.handler = lambda req: ({"type": "ok"} if req["type"] == "scan-library" else
                                    {"type": "library-status", "scan_running": True,
                                     "pending_rescan": False})
        r = jawaka.request_rescan(0.6, path=self.path)
        self.assertFalse(r["ok"])
        self.assertGreaterEqual(r["waited_s"], 0.5)

    def test_no_socket(self):
        r = jawaka.request_rescan(2, path=os.path.join(self.dir, "none.sock"))
        self.assertFalse(r["ok"])
        self.assertIn("Rescan Library", r["message"])  # còn hướng dẫn bấm tay

    def test_socket_path_precedence(self):
        self.assertEqual(jawaka.socket_path({"JAWAKA_SOCKET_PATH": "/a", "UMRK_RUNTIME_PATH": "/b"}), "/a")
        self.assertEqual(jawaka.socket_path({"UMRK_RUNTIME_PATH": "/b"}), "/b/jawakad.sock")
        self.assertEqual(jawaka.socket_path({}), "/tmp/jawaka-runtime/jawakad.sock")


if __name__ == "__main__":
    unittest.main()
