#!/usr/bin/env python3
"""Đầu-cuối Thư viện riêng với agent PocketBridge THẬT (build từ HEAD repo pocketbridge), thẻ GIẢ trong thư mục tạm.
Chạy ở Docker (python:3.12-slim + agent linux/amd64) HOẶC trên Pocket 1 (agent linux/arm64, Python của LoongMate) — cùng một script.

  python3 lib_e2e.py --agent /path/pocketbridge-agent --repo <thư mục có engine/ + systems.json> --work <thư mục tạm> [--python PY] [--secrets-default]

Dữ liệu: byte tự tạo (header GBA theo tài liệu + ngẫu nhiên), KHÔNG có ROM thương mại. Agent bind 127.0.0.1 (cổng tự chọn).
Luồng: config check -> chạy agent -> pair (CLI) -> 409 khi nhập lại bundle -> roots/list -> fetch --dry-run ->
fetch với PROXY CẮT KẾT NỐI giữa chừng -> jobs -> resume (Range + If-Range, 206) -> file đúng Roms/<hệ>/ + sha khớp ->
(tuỳ chọn --change-midway) file đổi trên server giữa chừng -> 200, tải lại từ đầu, không nối -> unpair -> dừng agent (kill theo PID của script) + xác nhận sạch.
Mã thoát 0 nếu mọi kiểm tra đạt. In log từng bước (lệnh, mã thoát, JSON rút gọn)."""
import argparse
import hashlib
import json
import os
import random
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

FAILS = []


def say(msg=""):
    print(msg, flush=True)


def check(cond, msg):
    say(("  [ĐẠT] " if cond else "  [HỎNG] ") + msg)
    if not cond:
        FAILS.append(msg)
    return cond


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class CutProxy:
    """TCP proxy 127.0.0.1:port -> agent. cut_after = số byte chiều server->client cho phép chảy qua MỘT kết nối trước khi cắt (None = không cắt)."""

    def __init__(self, upstream_port):
        self.up = upstream_port
        self.cut_after = None
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        self.cuts = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._pair, args=(c,), daemon=True).start()

    def _pair(self, c):
        try:
            u = socket.create_connection(("127.0.0.1", self.up), timeout=10)
        except OSError:
            c.close()
            return
        limit = self.cut_after

        def up_to_client():
            sent = 0
            try:
                while True:
                    d = u.recv(65536)
                    if not d:
                        break
                    if limit is not None and sent + len(d) > limit:
                        c.sendall(d[:max(0, limit - sent)])
                        self.cuts += 1
                        break
                    c.sendall(d)
                    sent += len(d)
            except OSError:
                pass
            finally:
                for s in (c, u):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    s.close()

        def client_to_up():
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
        threading.Thread(target=up_to_client, daemon=True).start()
        client_to_up()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--secrets-default", action="store_true",
                    help="dùng /userdata/LoongMate/secrets (đường dẫn thật mặc định) thay vì <work>/secrets; script tự dọn token của nó")
    ap.add_argument("--change-midway", action="store_true", help="thêm kịch bản: file đổi trên server giữa chừng (chờ ~40 s cho agent băm lại)")
    ap.add_argument("--node-id", default="lmtest")
    a = ap.parse_args()

    work = Path(a.work).resolve()
    assert work.name.startswith("lm-"), "từ chối: thư mục work (sẽ bị XOÁ rồi dựng lại) phải có tên bắt đầu bằng lm-"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    repo = Path(a.repo).resolve()
    sys.path.insert(0, str(repo))
    from engine import importer as IM

    lib = work / "lib"
    sd1, sd2, ud = work / "sd1", work / "sd2", work / "ud" / "LoongMate"
    ag = work / "agent"                              # cấu hình/log/state của agent: KHÔNG được chứa hay nằm trong thư mục thư viện
    state = ag / "state"
    secrets = Path("/userdata/LoongMate/secrets") if a.secrets_default else work / "secrets"
    for d in (lib, sd1 / "Roms", sd2 / "Roms", ud, state, ag / "actions.d"):
        d.mkdir(parents=True, exist_ok=True)

    # ---- thư viện giả
    rnd = random.Random(2026)
    gba = bytearray(rnd.randbytes(1_500_000))
    gba[0:4] = b"\x2e\x00\x00\xea"
    gba[4:0xA0] = IM.GBA_LOGO
    gba[0xA0:0xAC] = b"HOMEBREWDEMO"
    gba[0xB2] = 0x96
    gba[0xBD] = (-(0x19 + sum(gba[0xA0:0xBD]))) & 0xFF
    sfc = rnd.randbytes(2_000_000)
    pbin = rnd.randbytes(2352 * 400)
    cue = b'FILE "Test Disc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
    files = {"GBA/Homebrew Demo.gba": bytes(gba), "SNES/Random Demo.sfc": sfc, "PSX/Test Disc/Test Disc.cue": cue,
             "PSX/Test Disc/Test Disc.bin": pbin, "readme.txt": b"toi la readme", "GBA/NOTES.md": b"# ghi chu", "tools/run.sh": b"#!/bin/sh\necho hi\n"}
    for rel, data in files.items():
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    old = time.time() - 120                         # mtime cũ > 30 giây: agent băm ngay
    for p in lib.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))

    # ---- agent
    port = free_port()
    cfg = ag / "config.yaml"
    cfg.write_text(f"""schema: 1
listen:
  - 127.0.0.1:{port}
node_id: lm-e2e
actions:
  dir: {ag}/actions.d
state_dir: {state}
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
    agent = [a.agent]
    say(f"== agent: {subprocess.run(agent + ['version'], capture_output=True, text=True).stdout.strip()}")
    r = subprocess.run(agent + ["config", "check", "--config", str(cfg)], capture_output=True, text=True)
    say(f"== config check rc={r.returncode}\n{(r.stdout + r.stderr).strip()[:600]}")
    check(r.returncode == 0, "pocketbridge-agent config check mã thoát 0")
    proc = subprocess.Popen(agent + ["run", "--config", str(cfg)], stdout=open(ag / "agent.out", "w"), stderr=subprocess.STDOUT)
    say(f"== agent đã chạy, PID {proc.pid}, 127.0.0.1:{port}")
    proxy = CutProxy(port)

    env = dict(os.environ, LOONGMATE_SECRETS_DIR=str(secrets), LOONGMATE_LIB_PROXY="none", LOONGMATE_NO_RESCAN="1")

    def cli(*argv, expect=None, quiet=False):
        cmd = [a.python, str(repo / "engine" / "cli.py"), "--primary", str(sd1), "--secondary", str(sd2), "--userdata", str(ud),
               "--quiet", "--json", "--no-rescan", "library", *argv]
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(repo), encoding="utf-8")
        try:
            j = json.loads(r.stdout)
        except ValueError:
            j = {"_raw": r.stdout[:400], "_stderr": r.stderr[-400:]}
        if not quiet:
            short = json.dumps(j, ensure_ascii=False)
            say(f"$ library {' '.join(argv)}   -> rc={r.returncode} ({time.time() - t0:.1f}s)\n    {short[:700]}{'…' if len(short) > 700 else ''}")
        if expect is not None:
            check(r.returncode == expect, f"library {argv[0]} mã thoát {expect} (thật: {r.returncode})")
        return r.returncode, j, r.stderr

    ok_all = False
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            check(False, "agent không mở cổng")
            raise SystemExit(1)

        # ---- pair: bundle bootstrap qua proxy (cổng proxy, fingerprint của agent)
        bundle = work / "bundle.json"
        r = subprocess.run(agent + ["pair", "--config", str(cfg), "--id", "pocket1", "--scopes", "library:read", "--host", "127.0.0.1",
                                    "--name", "E2E agent", "--out", str(bundle)], capture_output=True, text=True)
        say(f"== agent pair rc={r.returncode} {r.stderr.strip()[:200]}")
        time.sleep(6)          # agent đang chạy nạp lại danh sách token/sổ ghép đôi trong ≤ 5 giây (docs/api.md): đừng đổi bootstrap ngay
        b = json.loads(bundle.read_text())
        b["node"]["id"] = a.node_id
        b["node"]["port"] = proxy.port
        bundle.write_text(json.dumps(b))
        spare = work / "bundle-again.json"
        shutil.copy(bundle, spare)
        pi = sd1 / "PocketBridge-import"
        pi.mkdir(exist_ok=True)
        shutil.move(str(bundle), pi / "bundle.json")
        say(f"== bundle ở {pi / 'bundle.json'} (host 127.0.0.1 port proxy {proxy.port}, fingerprint {b['node']['fingerprint'][:23]}…)")
        rc, j, _ = cli("bundles", expect=0)
        check(len(j.get("bundles", [])) == 1 and j["bundles"][0]["valid"], "bundle hợp lệ được liệt kê")
        rc, j, _ = cli("pair", str(pi / "bundle.json"), "--yes", expect=0)
        check(j.get("paired") and j.get("bundle_removed") and not (pi / "bundle.json").exists(), "ghép đôi OK và bundle bị xoá")
        tok = secrets / f"{a.node_id}.token"
        check(tok.is_file(), f"token đã lưu ở {tok}")
        if os.name == "posix":
            check(stat.S_IMODE(tok.stat().st_mode) == 0o600, f"token quyền 0600 (thật: {oct(stat.S_IMODE(tok.stat().st_mode))})")
            check(stat.S_IMODE(secrets.stat().st_mode) == 0o700, f"thư mục secrets quyền 0700 (thật: {oct(stat.S_IMODE(secrets.stat().st_mode))})")
        check(tok.read_text().startswith("pb_") and "pb_" not in (ud / "library-nodes.json").read_text(), "token không nằm trong library-nodes.json")
        # ---- nhập lại cùng bundle: 409 already_exchanged
        shutil.copy(spare, pi / "again.json")
        rc, j, _ = cli("pair", str(pi / "again.json"), "--yes", expect=1)
        check(j.get("code") == "already_exchanged" and (pi / "again.json").exists(), "nhập lại bundle -> already_exchanged, file giữ nguyên")
        (pi / "again.json").unlink()

        # ---- duyệt
        rc, j, _ = cli("roots", a.node_id, expect=0)
        check(j["roots"][0]["id"] == "roms", "roots trả thư viện 'roms'")
        deadline = time.time() + 60
        while True:
            rc, j, _ = cli("list", a.node_id, "roms", "--all", quiet=True)
            if j.get("success") and all(e["sha256"] for e in j.get("entries", [])) and j.get("entries"):
                break
            if time.time() > deadline:
                break
            time.sleep(1)
        rc, j, _ = cli("list", a.node_id, "roms", "--all", expect=0)
        names = sorted(e["path"] for e in j["entries"])
        check(names == sorted(["GBA/Homebrew Demo.gba", "SNES/Random Demo.sfc", "PSX/Test Disc/Test Disc.cue", "PSX/Test Disc/Test Disc.bin"]),
              f"chỉ hiện file game (ẩn .txt/.md/.sh): {names}")
        check(all(e["sha256"] == sha(files[e["path"]]) for e in j["entries"]), "sha256 trong index khớp byte thật")
        check({s["system"] for s in j["systems"]} == {"GBA", "SNES", "PSX"}, "tóm tắt hệ máy GBA/SNES/PSX")

        # ---- dry-run
        rc, j, _ = cli("fetch", a.node_id, "roms", "SNES/Random Demo.sfc", "--dry-run", expect=0)
        check(j["ok"] and j["total_bytes"] == len(sfc) and j["card"]["selected"] == "secondary", "dry-run: ok, thẻ phụ, đúng kích thước")
        check(not (sd2 / ".loongmate-import").exists(), "dry-run không tạo gì trên thẻ")

        # ---- tải với proxy CẮT kết nối giữa chừng
        proxy.cut_after = 700_000
        rc, j, err = cli("fetch", a.node_id, "roms", "SNES/Random Demo.sfc", "--yes", "--progress", expect=1)
        prog = [l for l in err.splitlines() if l.startswith("LMPROGRESS")]
        check(j.get("resumable") and j.get("state") == "interrupted", "bị cắt giữa chừng: job còn nguyên, resumable")
        check(len(prog) >= 1, f"CLI báo tiến độ trên stderr ({len(prog)} dòng LMPROGRESS)")
        parts = list((sd2 / ".loongmate-import" / "downloads").rglob("*.part"))
        check(len(parts) == 1 and 0 < parts[0].stat().st_size < len(sfc), f".part còn lại {parts[0].stat().st_size if parts else '?'} / {len(sfc)} byte")
        check(parts and parts[0].read_bytes() == sfc[:parts[0].stat().st_size], ".part là tiền tố đúng của file gốc")
        check(proxy.cuts >= 1, "proxy đã cắt kết nối")
        check(not list((sd2 / "Roms").rglob("*")), "chưa cài gì vào Roms/")
        rc, j, _ = cli("jobs", expect=0)
        check(len(j["jobs"]) == 1 and j["jobs"][0]["state"] == "interrupted" and j["jobs"][0]["percent"] > 0, f"library jobs: {j['jobs'][0]['percent']}%")
        jid = j["jobs"][0]["job_id"]

        # ---- resume
        proxy.cut_after = None
        rc, j, err = cli("resume", jid, "--yes", "--progress", expect=0)
        check(j.get("success") and j["installed"] == 1, "resume xong: cài 1 game")
        got = sd2 / "Roms" / "SNES" / "Random Demo.sfc"
        check(got.is_file() and sha(got.read_bytes()) == sha(sfc), "Roms/SNES/Random Demo.sfc đúng từng byte (SHA-256 khớp)")
        check(not list((sd2 / ".loongmate-import" / "downloads").rglob("*")), "job + .part đã dọn")
        aud = [json.loads(l) for l in (ag / "audit.jsonl").read_text().splitlines() if '"library_download"' in l]
        sfc_aud = [x for x in aud if x.get("path") == "SNES/Random Demo.sfc"]
        say("== audit của AGENT cho file này (path, status, bytes): " + ", ".join(f"{x['status']}/{x['bytes']}" for x in sfc_aud))
        check(any(x["status"] == 206 for x in sfc_aud), "agent ghi nhận có lượt tải 206 (Range) từ resume")

        # ---- nhiều file + game nhiều file (.cue + .bin) vào đúng hệ
        rc, j, _ = cli("fetch", a.node_id, "roms", "GBA/Homebrew Demo.gba", "PSX/Test Disc/Test Disc.cue", "PSX/Test Disc/Test Disc.bin", "--yes", expect=0)
        roms = sorted(str(p.relative_to(sd2)).replace("\\", "/") for p in (sd2 / "Roms").rglob("*") if p.is_file())
        say(f"== Roms/ sau khi cài: {roms}")
        check(j["installed"] == 2, f"cài 2 game (1 GBA + 1 đĩa PSX nhiều file): installed={j.get('installed')}")
        check("Roms/GBA/Homebrew Demo.gba" in roms and any(x.startswith("Roms/PSX/") and x.endswith(".cue") for x in roms)
              and any(x.startswith("Roms/PSX/") and x.endswith(".bin") for x in roms), "file nằm đúng Roms/<hệ>/")
        check(sha((sd2 / "Roms/GBA/Homebrew Demo.gba").read_bytes()) == sha(files["GBA/Homebrew Demo.gba"]), "GBA đúng từng byte")
        check(not (sd1 / "Roms").glob("*") or not list((sd1 / "Roms").rglob("*")), "thẻ chính (Leaf) không bị đụng")
        rc, j, _ = cli("list", a.node_id, "roms", "--system", "GBA", "--offline", expect=0)
        check(j["entries"][0]["present"], "list đánh dấu 'đã có' cho game vừa cài")

        # ---- file đổi trên server giữa chừng
        if a.change_midway:
            say("== kịch bản file đổi giữa chừng (chờ agent băm lại, ~40 s)")
            (sd2 / "Roms/SNES/Random Demo.sfc").unlink()
            new = rnd.randbytes(len(sfc))
            proxy.cut_after = 600_000
            rc, j, _ = cli("fetch", a.node_id, "roms", "SNES/Random Demo.sfc", "--yes", expect=1)
            check(j.get("resumable"), "lần 1 bị cắt")
            jid = cli("jobs", quiet=True)[1]["jobs"][0]["job_id"]
            (lib / "SNES/Random Demo.sfc").write_bytes(new)
            for _ in range(80):
                time.sleep(1)
                rc, jj, _ = cli("list", a.node_id, "roms", "--system", "SNES", quiet=True)
                ent = (cli("list", a.node_id, "roms", "--system", "SNES", quiet=True)[1].get("entries") or [{}])[0]
                if ent.get("sha256") == sha(new):
                    break
            else:
                check(False, "agent chưa băm lại file mới sau 80 s")
            proxy.cut_after = None
            rc, j, _ = cli("resume", jid, "--yes", expect=0)
            aud2 = [json.loads(l) for l in (ag / "audit.jsonl").read_text().splitlines() if '"library_download"' in l]
            last = [x for x in aud2 if x.get("path") == "SNES/Random Demo.sfc"][-1]
            say(f"== audit lượt tải cuối: status={last['status']} bytes={last['bytes']}")
            check(last["status"] == 200 and last["bytes"] == len(new), "If-Range lệch -> agent trả 200 toàn bộ; client tải lại từ đầu")
            check(sha((sd2 / "Roms/SNES/Random Demo.sfc").read_bytes()) == sha(new), "file cài là bản MỚI nguyên vẹn (không nối vào .part cũ)")

        # ---- gỡ ghép đôi
        rc, j, _ = cli("unpair", a.node_id, "--yes", expect=0)
        check(not tok.exists() and not (ud / "library-cache").glob("*") or not list((ud / "library-cache").glob("*")), "unpair xoá token + cache")
        ok_all = True
    finally:
        # dọn: kill ĐÚNG PID do script này tạo, xác nhận
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        time.sleep(0.3)
        alive = True
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            alive = False
        say(f"== dọn: agent PID {proc.pid} đã dừng (rc={proc.returncode}); cổng {port} {'VẪN MỞ' if alive else 'đã đóng'}")
        check(not alive, "agent đã dừng, cổng đóng")
        if a.secrets_default:
            try:
                (secrets / f"{a.node_id}.token").unlink()
            except OSError:
                pass
            try:
                secrets.rmdir()
                secrets.parent.rmdir()
            except OSError:
                pass
            say(f"== secrets mặc định sau dọn: {'CÒN' if secrets.exists() else 'sạch'}")
        proxy.srv.close()
        if not os.environ.get("LM_E2E_KEEP"):
            shutil.rmtree(work, ignore_errors=True)
            say(f"== đã xoá {work}: {'CÒN' if work.exists() else 'sạch'}")
    say()
    say(f"KẾT QUẢ: {'ĐẠT TOÀN BỘ' if ok_all and not FAILS else 'CÓ ' + str(len(FAILS)) + ' KIỂM TRA HỎNG'}")
    for f in FAILS:
        say("  - " + f)
    sys.exit(0 if ok_all and not FAILS else 1)


if __name__ == "__main__":
    main()
