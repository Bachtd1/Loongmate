#!/usr/bin/env python3
"""Dịch vụ thử cho màn "Thư viện riêng" (chạy TRÊN MÁY hoặc trong container ARM64): agent PocketBridge THẬT bind 127.0.0.1 + proxy
TCP có thể giới hạn tốc độ / cắt kết nối + thư viện giả + hai thẻ giả. Mọi thứ nằm dưới <work> (tên thư mục phải bắt đầu bằng lm-).

  lib_devsvc.py start  --repo R --agent A --work W [--mb 3]   # dựng thư viện/thẻ, chạy agent + proxy (nền), ghi PID vào W/pids.json
  lib_devsvc.py bundle --work W [--id pocket1]                # tạo bundle ghép đôi mới -> W/sd1/PocketBridge-import/bundle.json (cổng = cổng proxy)
  lib_devsvc.py proxy  --work W [--rate BYTES_PER_S] [--cut N]  # đổi cấu hình proxy (--rate 0 / --cut 0 = tắt)
  lib_devsvc.py env    --work W                               # in biến môi trường cho CLI/native: LOONGMATE_SECRETS_DIR, LOONGMATE_LIB_PROXY
  lib_devsvc.py stop   --work W                               # kill ĐÚNG các PID đã ghi, xác nhận cổng đã đóng

Dữ liệu: byte tự tạo (header GBA theo tài liệu + ngẫu nhiên), KHÔNG có ROM thương mại."""
import argparse
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def read_json(p, dflt=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return dflt


def proxy_main(work):
    """Proxy: cấu hình đọc lại từ W/proxy.json MỖI kết nối ({"rate": B/s, "cut": N}). Chạy nền (PID trong pids.json)."""
    w = Path(work)
    ports = read_json(w / "ports.json")
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", ports["proxy"]))
    srv.listen(16)

    def pair(c):
        cfg = read_json(w / "proxy.json", {})
        rate, cut = cfg.get("rate") or 0, cfg.get("cut") or 0
        try:
            u = socket.create_connection(("127.0.0.1", ports["agent"]), timeout=10)
        except OSError:
            c.close()
            return

        def down():
            sent, t0 = 0, time.monotonic()
            try:
                while True:
                    d = u.recv(16384)
                    if not d:
                        break
                    if cut and sent + len(d) > cut:
                        c.sendall(d[:max(0, cut - sent)])
                        break
                    c.sendall(d)
                    sent += len(d)
                    if rate:
                        ahead = sent / rate - (time.monotonic() - t0)
                        if ahead > 0:
                            time.sleep(ahead)
            except OSError:
                pass
            finally:
                for s in (c, u):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    s.close()

        def up():
            try:
                while True:
                    d = c.recv(65536)
                    if not d:
                        break
                    u.sendall(d)
            except OSError:
                pass
            finally:
                try:
                    u.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        threading.Thread(target=down, daemon=True).start()
        up()

    while True:
        c, _ = srv.accept()
        threading.Thread(target=pair, args=(c,), daemon=True).start()


def cmd_start(a):
    w = Path(a.work).resolve()
    assert w.name.startswith("lm-"), "từ chối: thư mục work phải bắt đầu bằng lm-"
    if w.exists():
        shutil.rmtree(w)
    repo = Path(a.repo).resolve()
    sys.path.insert(0, str(repo))
    from engine import importer as IM
    lib, ag = w / "lib", w / "agent"
    for d in (lib, w / "sd1" / "Roms", w / "sd2" / "Roms", w / "userdata", ag / "state", ag / "actions.d", w / "secrets"):
        d.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(7)
    M = 1024 * 1024
    gba = bytearray(rnd.randbytes(int(a.mb * M)))
    gba[0:4] = b"\x2e\x00\x00\xea"
    gba[4:0xA0] = IM.GBA_LOGO
    gba[0xA0:0xAC] = b"HOMEBREWDEMO"
    gba[0xB2] = 0x96
    gba[0xBD] = (-(0x19 + sum(gba[0xA0:0xBD]))) & 0xFF
    files = {"GBA/Homebrew Demo.gba": bytes(gba), "GBA/Tiny Test.gba": bytes(gba[:200000]),
             "SNES/Random Demo.sfc": rnd.randbytes(int(a.mb * 1.4 * M)), "SNES/Small Demo.sfc": rnd.randbytes(300000),
             "PSX/Test Disc/Test Disc.cue": b'FILE "Test Disc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
             "PSX/Test Disc/Test Disc.bin": rnd.randbytes(2352 * 300), "readme.txt": b"toi la readme", "GBA/NOTES.md": b"# ghi chu",
             "tools/run.sh": b"#!/bin/sh\necho hi\n"}
    for rel, data in files.items():
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    old = time.time() - 120
    for p in lib.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    ports = {"agent": free_port(), "proxy": free_port()}
    (w / "ports.json").write_text(json.dumps(ports))
    (w / "proxy.json").write_text("{}")
    cfg = ag / "config.yaml"
    cfg.write_text(f"""schema: 1
listen:
  - 127.0.0.1:{ports['agent']}
node_id: lm-libtest
actions:
  dir: {ag}/actions.d
state_dir: {ag}/state
log:
  path: {ag}/agent.jsonl
  audit_path: {ag}/audit.jsonl
library:
  roots:
    - id: roms
      name: ROM thu nghiem
      path: {lib}
""")
    os.chmod(cfg, 0o640)
    r = subprocess.run([a.agent, "config", "check", "--config", str(cfg)], capture_output=True, text=True)
    print(f"config check rc={r.returncode}")
    if r.returncode:
        print(r.stdout + r.stderr)
        sys.exit(1)
    agent = subprocess.Popen([a.agent, "run", "--config", str(cfg)], stdout=open(ag / "agent.out", "w"), stderr=subprocess.STDOUT,
                             start_new_session=True)
    prox = subprocess.Popen([sys.executable, __file__, "proxy-run", "--work", str(w)], stdout=open(w / "proxy.out", "w"), stderr=subprocess.STDOUT,
                            start_new_session=True)
    (w / "pids.json").write_text(json.dumps({"agent": agent.pid, "proxy": prox.pid}))
    for name, p in (("agent", ports["agent"]), ("proxy", ports["proxy"])):
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", p), timeout=0.3).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            print(f"{name} không mở cổng {p}")
            sys.exit(1)
    print(f"start ok: agent PID {agent.pid} cổng {ports['agent']}; proxy PID {prox.pid} cổng {ports['proxy']}; work {w}")


def cmd_bundle(a):
    w = Path(a.work).resolve()
    ports = read_json(w / "ports.json")
    cfg = w / "agent" / "config.yaml"
    out = w / "bundle.tmp.json"
    agent = a.agent or read_json(w / "agent.path")["agent"]
    r = subprocess.run([agent, "pair", "--config", str(cfg), "--id", a.id, "--scopes", "library:read", "--host", "127.0.0.1",
                        "--name", "Pi thử nghiệm", "--out", str(out)], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr)
        sys.exit(1)
    time.sleep(6)       # agent đang chạy nạp sổ ghép đôi trong ≤ 5 giây
    b = json.loads(out.read_text())
    b["node"]["port"] = ports["proxy"]
    d = w / "sd1" / "PocketBridge-import"
    d.mkdir(exist_ok=True)
    (d / "bundle.json").write_text(json.dumps(b))
    out.unlink()
    print(f"bundle mới: {d / 'bundle.json'} (id {a.id}, cổng proxy {ports['proxy']}, bootstrap dùng một lần)")


def cmd_proxy(a):
    w = Path(a.work).resolve()
    cfg = read_json(w / "proxy.json", {})
    if a.rate is not None:
        cfg["rate"] = a.rate
    if a.cut is not None:
        cfg["cut"] = a.cut
    (w / "proxy.json").write_text(json.dumps(cfg))
    print(f"proxy.json = {cfg}")


def cmd_env(a):
    w = Path(a.work).resolve()
    print(f"LOONGMATE_SECRETS_DIR={w / 'secrets'} LOONGMATE_LIB_PROXY=none LOONGMATE_NO_RESCAN=1")


def cmd_stop(a):
    w = Path(a.work).resolve()
    pids = read_json(w / "pids.json", {})
    ports = read_json(w / "ports.json", {})
    for name, pid in pids.items():
        try:
            os.killpg(pid, signal.SIGTERM)       # chỉ PID/nhóm do chính script này tạo (start_new_session)
        except OSError:
            pass
    time.sleep(1)
    for name, pid in pids.items():
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.3)
    for name, p in ports.items():
        try:
            socket.create_connection(("127.0.0.1", p), timeout=0.5).close()
            print(f"CÒN MỞ: {name} cổng {p}")
        except OSError:
            print(f"đã đóng: {name} cổng {p}")
    alive = []
    for name, pid in pids.items():
        try:
            os.kill(pid, 0)
            alive.append(f"{name}:{pid}")
        except OSError:
            pass
    print("tiến trình còn sống: " + (", ".join(alive) if alive else "không"))
    if a.rm:
        shutil.rmtree(w, ignore_errors=True)
        print(f"đã xoá {w}: {'CÒN' if w.exists() else 'sạch'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "bundle", "proxy", "env", "stop", "proxy-run"])
    ap.add_argument("--work", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--agent")
    ap.add_argument("--mb", type=float, default=3.0)
    ap.add_argument("--id", default="pocket1")
    ap.add_argument("--rate", type=int)
    ap.add_argument("--cut", type=int)
    ap.add_argument("--rm", action="store_true")
    a = ap.parse_args()
    if a.cmd == "proxy-run":
        proxy_main(a.work)
    elif a.cmd == "start":
        Path(a.work).mkdir(parents=True, exist_ok=True)
        cmd_start(a)
        Path(a.work, "agent.path").write_text(json.dumps({"agent": str(Path(a.agent).resolve())}))
    else:
        {"bundle": cmd_bundle, "proxy": cmd_proxy, "env": cmd_env, "stop": cmd_stop}[a.cmd](a)


if __name__ == "__main__":
    main()
