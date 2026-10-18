#!/usr/bin/env python3
"""Bổ sung dữ liệu cho sandbox (make_sandbox.py) để thử các màn đợt D: nhiều snapshot, agent_state, gói cập nhật.
Chỉ ghi dưới <gốc sandbox> (tên phải bắt đầu bằng lm-sandbox). Dùng:
  d4_prep.py <gốc> <thư mục chứa engine/> <bước,...>
bước: snaps | agent_bad | agent_stale | agent_ok | agent_never | bundles"""
import hashlib, json, os, subprocess, sys, time

root, engine_parent, steps = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), sys.argv[3].split(",")
assert os.path.basename(root).startswith("lm-sandbox"), "từ chối: gốc phải tên lm-sandbox*"
sd1, sd2, ud = (os.path.join(root, n) for n in ("sd1", "sd2", "userdata"))
cli = os.path.join(engine_parent, "engine", "cli.py")


def run(*a):
    r = subprocess.run([sys.executable, cli, "--primary", sd1, "--secondary", sd2, "--userdata", ud, "--quiet", "--json", "--no-rescan", *a],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout


def state(**kw):
    p = os.path.join(ud, "agent_state.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.update(kw)
    open(p, "w", encoding="utf-8").write(json.dumps(d))


now = time.time()
for st in steps:
    if st == "snaps":
        for i in range(3):
            with open(os.path.join(sd1, "Saves", "Alpha Quest.srm"), "ab") as f:
                f.write(b"x" * (100 + i))
            run("backup")
            time.sleep(1.1)       # id snapshot theo giây
        print("snaps:", json.loads(run("snapshots"))["count"])
    elif st == "agent_bad":
        state(vault_verify={"ts": int(now - 3600), "ok": False, "checked": 120, "bad_objects": ["ab12", "cd34"], "missing_objects": 1, "partial": False},
              prewarm={"ts": int(now - 600), "hashed": 40, "remaining": 12, "done": False},
              reload={"from": "1.0", "to": "1.1", "ts": int(now - 86400)})
    elif st == "agent_stale":
        state(vault_verify={"ts": int(now - 20 * 86400), "ok": True, "checked": 120, "bad_objects": [], "missing_objects": 0, "partial": False},
              prewarm={"ts": int(now - 600), "hashed": 52, "remaining": 0, "done": True})
    elif st == "agent_ok":
        state(vault_verify={"ts": int(now - 2 * 86400), "ok": True, "checked": 120, "bad_objects": [], "missing_objects": 0, "partial": False})
    elif st == "agent_never":
        for n in ("agent_state.json", "agent.lock"):
            try:
                os.remove(os.path.join(ud, n))
            except OSError:
                pass
    elif st == "bundles":
        upd = os.path.join(sd1, "LoongMate-update")
        body = b"print('ban cap nhat')\n"
        sha = hashlib.sha256(body).hexdigest()
        for name, ver, data in (("loongmate-2.0", "2.0", body), ("loongmate-2.1-hong", "2.1", b"bi sua")):
            d = os.path.join(upd, name, "LoongMate.pak", "engine")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "x.py"), "wb").write(data)
            json.dump({"schema": 1, "version": ver, "created_at": "2026-10-02 09:00:00", "files": {"LoongMate.pak/engine/x.py": sha}},
                      open(os.path.join(upd, name, "manifest.json"), "w"))
        os.makedirs(os.path.join(upd, "loongmate-hong-manifest"), exist_ok=True)
        open(os.path.join(upd, "loongmate-hong-manifest", "manifest.json"), "w").write("{hong")
        json.dump({"schema": 1, "version": "2.0", "bundle_created_at": "2026-10-02 09:00:00"}, open(os.path.join(ud, "installed-version.json"), "w"))
    else:
        sys.exit("bước lạ: " + st)
print("prep ok:", steps)
