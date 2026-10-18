#!/usr/bin/env python3
"""Chứng minh test của Thư viện riêng BIẾT ĐỎ: với từng "đột biến" cố ý làm hỏng engine/libsource.py, các test tương ứng PHẢI đỏ.
  python native/tools/lib_red.py        (chạy từ gốc repo; Linux để có cả test quyền 0600)
Mã thoát 0 nếu mọi đột biến đều làm test đỏ."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
src = (repo / "engine" / "libsource.py").read_text(encoding="utf-8")
MUTS = {
    "PIN (bỏ so fingerprint)": ("if not hmac.compare_digest(got, self.fingerprint):", "if False:",
                                ["PairingTests.test_wrong_fingerprint_sends_no_token_and_keeps_bundle",
                                 "PairingTests.test_pinned_node_wrong_fingerprint_blocks_browse_without_token_on_wire"]),
    "NỐI NHẦM KHI 200 (không bỏ .part cũ)": ('have, mode = 0, "wb"', 'mode = "ab"',
                                             ["FetchTests.test_resume_gets_200_when_etag_changed_restarts_from_zero_never_appends",
                                              "FetchTests.test_resume_200_without_etag_keeps_recorded_hash_and_restarts"]),
    "SHA SAI VẪN CÀI (bỏ so hash)": ('if digest != f["sha256"]:', "if False:", ["FetchTests.test_wrong_sha_not_installed_and_part_removed"]),
    "CÀI KHI SERVER CHƯA BĂM": ('reason = "server chưa băm xong, thử lại sau (chưa có SHA-256 để kiểm file tải về)"', "reason = None",
                                ["FetchTests.test_no_hash_yet_means_no_download_no_install"]),
    "TOKEN 0644": ("os.chmod(tmp, 0o600)", "os.chmod(tmp, 0o644)", ["PairingTests.test_token_file_0600_and_dir_0700"]),
    "BUNDLE KHÔNG XOÁ": ('        bp.unlink()\n    except OSError as e:\n        res["message"] = f"Đã ghép đôi', '        pass\n    except OSError as e:\n        res["message"] = f"Đã ghép đôi',
                         ["PairingTests.test_bootstrap_exchange_once_then_bundle_removed"]),
}
bad = 0
for name, (old, new, tests) in MUTS.items():
    assert old in src, f"đột biến '{name}' không còn khớp mã nguồn — cập nhật lib_red.py"
    d = Path(tempfile.mkdtemp())
    f = d / "libsource_bad.py"
    f.write_text(src.replace(old, new, 1), encoding="utf-8")
    env = dict(os.environ, LOONGMATE_LIBSOURCE_PATH=str(f), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest"] + ["tests.test_libsource." + t for t in tests], cwd=repo, env=env,
                       capture_output=True, text=True, encoding="utf-8")
    skipped = "skipped" in r.stderr
    red = r.returncode != 0
    bad += not red
    print(f"--- {name}: {'ĐỎ (đúng kỳ vọng)' if red else 'VẪN XANH' + (' (bị skip: cần Linux)' if skipped else '')} — "
          + " | ".join(l for l in r.stderr.splitlines() if l.startswith(('FAIL:', 'ERROR:', 'FAILED', 'OK'))))
sys.exit(1 if bad else 0)
