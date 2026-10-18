"""
HTTP-level tests for simulator/server.py.

Starts the real simulator server (bound to an OS-chosen free port, via port=0) in a
background thread, against an isolated temp directory -- never the project's own
playground/ -- and drives it with real HTTP requests (urllib.request), the same way a
browser or curl would. Security tests in particular must observe real file-system
behavior (os.path.exists), not just status codes, because a status code alone doesn't
prove the attack failed.
"""

import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIMULATOR_DIR = PROJECT_ROOT / "simulator"
sys.path.insert(0, str(SIMULATOR_DIR))


class TestSimulatorAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_playground = Path(tempfile.mkdtemp(prefix="loongmate_sim_test_"))
        # Picked up by simulator/server.py at import time, redirecting SD1_DIR/SD2_DIR/
        # USERDATA_DIR away from the project's playground/ entirely.
        os.environ["LOONGMATE_SIM_PLAYGROUND"] = str(cls.temp_playground)

        import server as server_module  # simulator/server.py
        importlib.reload(server_module)  # pick up the env var even if already imported
        cls.server_module = server_module
        cls.server_module.init_playground()

        cls.httpd = cls.server_module.build_server(0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls._wait_until_ready()

    @classmethod
    def _wait_until_ready(cls):
        last_err = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{cls.base}/api/readiness", timeout=1) as res:
                    if res.status == 200:
                        return
            except Exception as e:
                last_err = e
                time.sleep(0.05)
        raise RuntimeError(f"Simulator server did not become ready in time: {last_err}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.temp_playground, ignore_errors=True)
        os.environ.pop("LOONGMATE_SIM_PLAYGROUND", None)
        # Confirm the isolated temp dir is really gone, rather than trusting rmtree
        # silently did nothing.
        if cls.temp_playground.exists():
            raise AssertionError(f"Temp playground was not cleaned up: {cls.temp_playground}")

    # --- small HTTP helpers --------------------------------------------------------

    def _get(self, path, extra_headers=None):
        req = urllib.request.Request(f"{self.base}{path}", method="GET")
        for k, v in (extra_headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                body = json.loads(res.read().decode("utf-8"))
                return res.status, body, dict(res.headers)
        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                body = {"raw": body_bytes.decode("utf-8", errors="ignore")}
            return e.code, body, dict(e.headers)

    def _post(self, path, payload=None, headers=None, raw_body=None, omit_content_type=False):
        data = raw_body if raw_body is not None else json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(f"{self.base}{path}", data=data, method="POST")
        if not omit_content_type:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                body = json.loads(res.read().decode("utf-8"))
                return res.status, body, dict(res.headers)
        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                body = {"raw": body_bytes.decode("utf-8", errors="ignore")}
            return e.code, body, dict(e.headers)

    def _find_action_index(self, audit_data, action_type, path_suffix):
        for i, act in enumerate(audit_data["fixable_actions"]):
            if act["type"] == action_type and act.get("path", act.get("folder", "")).endswith(path_suffix):
                return i
        return None

    # --- behavioral tests ------------------------------------------------------------

    def test_readiness_has_four_parts(self):
        status, data, _ = self._get("/api/readiness")
        self.assertEqual(status, 200)
        for key in ("device", "storage", "savevault", "library", "network"):
            self.assertIn(key, data, f"readiness response missing '{key}'")
        self.assertIn("audit_age_seconds", data["library"])

    def test_audit_has_total_issues_and_severity_counts(self):
        status, data, _ = self._get("/api/audit")
        self.assertEqual(status, 200)
        self.assertIn("total_issues", data)
        self.assertIn("severity_counts", data)
        for sev in ("critical", "warning", "info"):
            self.assertIn(sev, data["severity_counts"])
        self.assertIsInstance(data["fixable_actions"], list)

    def test_backup_creates_real_snapshot_visible_in_list(self):
        status, res, _ = self._post("/api/backup", {"label": "unittest"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"), res)
        snap_id = res["snapshot_id"]

        status, data, _ = self._get("/api/snapshots")
        self.assertEqual(status, 200)
        ids = [s["id"] for s in data["snapshots"]]
        self.assertIn(snap_id, ids)

    def test_health_runs_mount_and_space_with_storage_data(self):
        status, data, _ = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("tests", data)
        mount_test = next(t for t in data["tests"] if t["test"] == "mount_and_space")
        # Before the fix, /api/health never passed `storage` to run_diagnostics(), so
        # this test was always skipped (skipped=True) regardless of real card state.
        self.assertFalse(mount_test.get("skipped", False), mount_test)
        self.assertIn("mounts", mount_test)

    def test_crash_recovery_endpoint_present(self):
        status, data, _ = self._get("/api/crash-recovery")
        self.assertEqual(status, 200)
        self.assertIn("needs_recovery", data)

    def test_valid_fix_executes_create_dir_action(self):
        """Proves the new action_index contract isn't just a wall -- a legitimate,
        confirmed fix for an action the server itself produced must still go through."""
        states_dir = self.temp_playground / "sd1" / "States"
        if states_dir.exists():
            shutil.rmtree(states_dir)
        self.assertFalse(states_dir.exists())

        status, audit_data, _ = self._get("/api/audit")
        self.assertEqual(status, 200)
        idx = self._find_action_index(audit_data, "create_dir", "States")
        self.assertIsNotNone(idx, "expected a create_dir fixable action for the missing States folder")

        status, res, headers = self._post("/api/fix", {"action_index": idx, "confirm": True})
        self.assertEqual(status, 200, res)
        self.assertTrue(res.get("success"), res)
        self.assertTrue(states_dir.exists(), "the confirmed, valid fix should have created the folder")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    # --- security tests: these must send real requests and check real disk state -----

    def test_no_cors_header_on_any_response(self):
        _, _, headers = self._get("/api/readiness")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_fix_rejects_text_plain_content_type(self):
        bios_dir = self.temp_playground / "sd1" / "BIOS"
        if bios_dir.exists():
            shutil.rmtree(bios_dir)
        status, audit_data, _ = self._get("/api/audit")
        idx = self._find_action_index(audit_data, "create_dir", "BIOS")
        self.assertIsNotNone(idx)

        status, res, _ = self._post(
            "/api/fix",
            {"action_index": idx, "confirm": True},
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(status, 403, res)
        self.assertFalse(bios_dir.exists(), "rejected request must not have created the folder")

    def test_fix_rejects_foreign_origin(self):
        states_dir = self.temp_playground / "sd1" / "States"
        if states_dir.exists():
            shutil.rmtree(states_dir)
        status, audit_data, _ = self._get("/api/audit")
        idx = self._find_action_index(audit_data, "create_dir", "States")
        self.assertIsNotNone(idx)

        status, res, _ = self._post(
            "/api/fix",
            {"action_index": idx, "confirm": True},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403, res)
        self.assertFalse(states_dir.exists(), "rejected cross-origin request must not have created the folder")

    def test_fix_rejects_bad_host_header(self):
        states_dir = self.temp_playground / "sd1" / "States"
        if states_dir.exists():
            shutil.rmtree(states_dir)
        status, audit_data, _ = self._get("/api/audit")
        idx = self._find_action_index(audit_data, "create_dir", "States")
        self.assertIsNotNone(idx)

        status, res, _ = self._post(
            "/api/fix",
            {"action_index": idx, "confirm": True},
            headers={"Host": "evil.example:1234"},
        )
        self.assertEqual(status, 403, res)
        self.assertFalse(states_dir.exists(), "DNS-rebinding-style Host header must be rejected before any write")

    def test_fix_rejects_missing_confirm(self):
        states_dir = self.temp_playground / "sd1" / "States"
        if states_dir.exists():
            shutil.rmtree(states_dir)
        status, audit_data, _ = self._get("/api/audit")
        idx = self._find_action_index(audit_data, "create_dir", "States")
        self.assertIsNotNone(idx)

        status, res, _ = self._post("/api/fix", {"action_index": idx})
        self.assertEqual(status, 400, res)
        self.assertFalse(states_dir.exists(), "missing confirm must not create the folder")

    def test_fix_rejects_arbitrary_client_supplied_action(self):
        """The old contract let the client send {"action": {"path": <anything>}}
        straight into execute_fix. The new contract ignores "action" entirely and only
        accepts action_index, so a forged action pointing outside the card must be
        rejected without the file ever being touched."""
        outside_dir = Path(tempfile.mkdtemp(prefix="loongmate_outside_card_"))
        victim_file = outside_dir / "victim.txt"
        victim_file.write_text("do not touch", encoding="utf-8")
        try:
            status, res, _ = self._post(
                "/api/fix",
                {"action": {"type": "quarantine_file", "path": str(victim_file)}, "confirm": True},
            )
            self.assertEqual(status, 400, res)
            self.assertTrue(victim_file.exists(), "file outside primary/secondary/userdata must remain untouched")
            self.assertEqual(victim_file.read_text(encoding="utf-8"), "do not touch")
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_restore_execute_rejects_path_traversal_snapshot_id(self):
        status, before_snaps, _ = self._get("/api/snapshots")
        status, res, _ = self._post(
            "/api/restore/execute",
            {"snapshot_id": "../../../x", "confirm": True},
        )
        self.assertEqual(status, 400, res)
        self.assertEqual(res.get("error"), "INVALID_SNAPSHOT_ID")
        status, after_snaps, _ = self._get("/api/snapshots")
        # A real restore always creates a pre-restore snapshot first; if the traversal
        # attempt had actually run restore_snapshot(), the snapshot count would differ.
        self.assertEqual(len(before_snaps["snapshots"]), len(after_snaps["snapshots"]))

    def test_restore_execute_rejects_missing_confirm(self):
        status, data, _ = self._get("/api/snapshots")
        if not data["snapshots"]:
            self._post("/api/backup", {"label": "for_restore_test"})
            status, data, _ = self._get("/api/snapshots")
        snap_id = data["snapshots"][0]["id"]

        status, res, _ = self._post("/api/restore/execute", {"snapshot_id": snap_id})
        self.assertEqual(status, 400, res)


if __name__ == "__main__":
    unittest.main()
