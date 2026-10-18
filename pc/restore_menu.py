#!/usr/bin/env python3
"""LoongMate - khôi phục từ PC: chọn snapshot đang có trên PC rồi đẩy về vault trên Pocket 1.

Tương tác (mặc định khi chạy trong terminal):
    python pc/restore_menu.py
Không tương tác / cho script và giao diện:
    python pc/restore_menu.py --list --json
    python pc/restore_menu.py --push snap_20261002_213000_manual --yes --json

Công cụ này CHỈ đẩy dữ liệu (object trước, manifest sau cùng, kiểm hash trên máy) bằng
pull_saves.push_snapshot. Nó KHÔNG tự khôi phục save trên máy: bước đó làm trên máy, có xem trước.

Vault trên máy mặc định được dò theo thẻ Leaf (pc/device_paths.py); --device-vault / --sdcard ghi đè.

Mã thoát: 0 ok | 1 lỗi dữ liệu/đẩy/không dò được thẻ Leaf | 2 không kết nối được máy |
          3 sai cách dùng / không xác nhận / nhiều thẻ Leaf (cần --sdcard).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pull_saves as ps  # noqa: E402
import device_paths  # noqa: E402


def _is_tty():
    return sys.stdin.isatty()


def list_snapshots(dest, transport=None):
    """Snapshot committed hợp lệ trên PC, mới nhất trước.
    on_device: True/False nếu hỏi được máy, None nếu không hỏi / máy không tới được."""
    on_dev, err = None, None
    if transport is not None:
        try:
            on_dev = set(transport.list_snapshot_ids())
        except (ps.TransportError, device_paths.LeafCardError) as e:
            err = str(e)
    rows = []
    for sid, (m, refs) in ps.local_snapshots(dest).items():
        files = m.get("files") or {}
        rows.append({"id": sid, "label": m.get("label"), "created_at": m.get("created_at"),
                     "files": len(files), "bytes": sum(f.get("size", 0) for f in files.values()),
                     "on_device": None if on_dev is None else sid in on_dev})
    rows.sort(key=lambda r: (r["created_at"] or "", r["id"]), reverse=True)
    return rows, err


def instructions(sid):
    return (f"Trên máy: LoongMate → SaveVault → chọn snapshot {sid} → Xem trước khôi phục → Khôi phục")


def push(transport, dest, sid):
    """Đẩy snapshot (nếu máy chưa có). Trả về dict kết quả; ném ValueError/TransportError nếu không an toàn."""
    if sid in transport.list_snapshot_ids():
        r = {"id": sid, "already_on_device": True, "objects_pushed": 0, "objects_already": 0, "bytes_pushed": 0}
        if sid not in ps.local_snapshots(dest):
            r["warning"] = "PC không có bản của snapshot này; chỉ dùng bản đang có trên máy"
    else:
        r = {"id": sid, "already_on_device": False, **ps.push_snapshot(transport, dest, sid)}
    r["next_step"] = instructions(sid)
    return r


def _fmt_bytes(n):
    return f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.1f} KB"


def _print_table(rows):
    print(f"{'#':>3}  {'Thời điểm':<19}  {'File':>5}  {'Dung lượng':>10}  {'Trên máy':<8}  ID")
    for i, r in enumerate(rows, 1):
        od = {True: "có", False: "chưa", None: "?"}[r["on_device"]]
        print(f"{i:>3}  {(r['created_at'] or '?'):<19}  {r['files']:>5}  {_fmt_bytes(r['bytes']):>10}  {od:<8}  {r['id']}")


def main(argv=None, transport=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Khôi phục từ PC: đẩy snapshot trên PC về vault trên Pocket 1.")
    ap.add_argument("--dest", type=Path, default=ps.DEFAULT_DEST, help="thư mục SaveVault trên PC")
    ap.add_argument("--host", default=ps.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=ps.DEFAULT_PORT)
    ap.add_argument("--user", default=ps.DEFAULT_USER)
    ap.add_argument("--key", type=Path, default=ps.DEFAULT_KEY)
    ap.add_argument("--device-vault", default=None, help="đường dẫn SaveVault trên máy (mặc định: dò thẻ Leaf)")
    ap.add_argument("--sdcard", default=None, help="thư mục gốc thẻ Leaf trên máy (khi có hơn một thẻ Leaf)")
    ap.add_argument("--list", action="store_true", help="chỉ liệt kê snapshot (không tương tác)")
    ap.add_argument("--push", metavar="ID", help="đẩy snapshot ID (không tương tác; cần --yes)")
    ap.add_argument("--yes", action="store_true", help="xác nhận đẩy mà không hỏi")
    ap.add_argument("--json", action="store_true", help="in kết quả dạng JSON")
    ap.add_argument("--offline", action="store_true", help="không hỏi máy (cột 'Trên máy' sẽ là ?)")
    a = ap.parse_args(argv)

    def out(obj, human=None):
        if a.json:
            print(json.dumps(obj, ensure_ascii=False, indent=2))
        elif human:
            print(human)

    def err(msg):
        if a.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)

    interactive = not (a.list or a.push) and _is_tty()
    if not (a.list or a.push or interactive):
        err("Không ở terminal: dùng --list hoặc --push ID --yes (xem --help).")
        return 3
    if a.push and not a.yes and not _is_tty():
        err("--push cần --yes khi không chạy trong terminal.")
        return 3

    t = transport
    if t is None and not (a.offline and a.list):
        t = ps.SshTransport(a.host, a.port, a.user, a.key, a.device_vault, sdcard=a.sdcard)
    try:
        if a.list:
            rows, e = list_snapshots(a.dest, None if a.offline else t)
            out({"ok": True, "dest": str(a.dest), "device_vault": getattr(t, "_vault", None),
                 "device_error": e, "snapshots": rows})
            if not a.json:
                if e:
                    print(f"Cảnh báo: không hỏi được máy ({e})", file=sys.stderr)
                _print_table(rows) if rows else print("PC chưa có snapshot nào.")
            return 0

        rows, e = list_snapshots(a.dest, None if a.offline else t)
        if a.push:
            sid = a.push
        else:
            if not rows:
                print("PC chưa có snapshot nào. Chạy pull_saves.py trước.")
                return 1
            if e:
                print(f"Cảnh báo: không hỏi được máy ({e})", file=sys.stderr)
            _print_table(rows)
            pick = input("Chọn số thứ tự snapshot (Enter để thoát): ").strip()
            if not pick:
                print("Đã thoát.")
                return 3
            if not pick.isdigit() or not 1 <= int(pick) <= len(rows):
                print("Lựa chọn không hợp lệ.", file=sys.stderr)
                return 3
            sid = rows[int(pick) - 1]["id"]
        if not a.yes:
            print(f"Sắp ĐẨY snapshot '{sid}' vào vault trên máy ({t.vault}).")
            print("Việc này chỉ thêm dữ liệu vào vault; không ghi đè save đang chơi.")
            if input("Gõ 'yes' để tiếp tục: ").strip() != "yes":
                print("Đã huỷ.")
                return 3
        r = push(t, a.dest, sid)
        out({"ok": True, **r}, None)
        if not a.json:
            if r["already_on_device"]:
                print(f"Máy đã có snapshot {sid}; không cần đẩy.")
            else:
                print(f"Đã đẩy: {r['objects_pushed']} object mới ({r['objects_already']} đã có), "
                      f"manifest ghi sau cùng, đã đọc lại và so khớp.")
            print(r["next_step"])
        return 0
    except ps.Unreachable as e:
        err(f"MÁY KHÔNG TỚI ĐƯỢC: {e}")
        return 2
    except device_paths.LeafCardAmbiguous as e:
        err(str(e))
        return 3
    except device_paths.LeafCardError as e:
        err(f"Lỗi: {e}")
        return 1
    except (ps.TransportError, ValueError) as e:
        err(f"Lỗi: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
