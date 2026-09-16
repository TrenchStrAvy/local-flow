from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SOURCE = PROJECT_ROOT / "launcher" / "LocalFlowLauncher.swift"
FAKE_LAUNCHCTL = PROJECT_ROOT / "tests" / "fake_launchctl.py"


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_dir = tempfile.TemporaryDirectory()
        cls.launcher = Path(cls.build_dir.name) / "LocalFlowLauncher"
        os.chmod(FAKE_LAUNCHCTL, 0o755)
        subprocess.run(
            [
                "/usr/bin/swiftc",
                "-parse-as-library",
                "-D",
                "LOCALFLOW_TESTING",
                str(LAUNCHER_SOURCE),
                "-o",
                str(cls.launcher),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.build_dir.cleanup()

    def setUp(self) -> None:
        self.fixture = tempfile.TemporaryDirectory()
        self.fixture_path = Path(self.fixture.name)
        self.state_path = self.fixture_path / "state.json"
        self.log_path = self.fixture_path / "calls.jsonl"
        self.home_path = self.fixture_path / "home"
        (self.home_path / "Library" / "LaunchAgents").mkdir(parents=True)
        (
            self.home_path
            / "Library"
            / "LaunchAgents"
            / "com.localflow.dictation.plist"
        ).write_text("<plist/>")

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def run_launcher(self, state: dict) -> subprocess.CompletedProcess[str]:
        self.state_path.write_text(json.dumps(state))
        self.log_path.write_text("")
        environment = os.environ.copy()
        environment.update(
            {
                "LOCALFLOW_LAUNCHCTL_PATH": str(FAKE_LAUNCHCTL),
                "LOCALFLOW_TEST_UID": "501",
                "LOCALFLOW_TEST_HOME": str(self.home_path),
                "LOCALFLOW_POLL_INTERVAL_MS": "1",
                "LOCALFLOW_POLL_ATTEMPTS": "4",
                "LOCALFLOW_ERROR_MODE": "stderr",
                "FAKE_LAUNCHCTL_STATE": str(self.state_path),
                "FAKE_LAUNCHCTL_LOG": str(self.log_path),
            }
        )
        return subprocess.run(
            [str(self.launcher)],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def calls(self) -> list[list[str]]:
        return [
            json.loads(line)
            for line in self.log_path.read_text().splitlines()
            if line
        ]

    def test_running_service_is_idempotent(self) -> None:
        result = self.run_launcher(
            {"loaded": True, "running": True, "pid": 41001, "uid": 501}
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [["print", "gui/501/com.localflow.dictation"]],
        )
        final_state = json.loads(self.state_path.read_text())
        self.assertEqual(final_state["pid"], 41001)

    def test_stopped_service_is_kickstarted(self) -> None:
        result = self.run_launcher(
            {
                "loaded": True,
                "running": False,
                "next_pid": 42001,
                "uid": 501,
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                ["print", "gui/501/com.localflow.dictation"],
                ["kickstart", "gui/501/com.localflow.dictation"],
                ["print", "gui/501/com.localflow.dictation"],
            ],
        )
        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["running"])
        self.assertEqual(final_state["pid"], 42001)

    def test_unloaded_service_is_bootstrapped_without_redundant_kickstart(
        self,
    ) -> None:
        result = self.run_launcher(
            {
                "loaded": False,
                "running": False,
                "bootstrap_runs": True,
                "next_pid": 43001,
                "uid": 501,
            }
        )

        plist = (
            self.home_path
            / "Library"
            / "LaunchAgents"
            / "com.localflow.dictation.plist"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                ["print", "gui/501/com.localflow.dictation"],
                ["bootstrap", "gui/501", str(plist)],
                ["print", "gui/501/com.localflow.dictation"],
            ],
        )
        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["loaded"])
        self.assertTrue(final_state["running"])
        self.assertEqual(final_state["pid"], 43001)

    def test_unexpected_inspection_error_is_reported_without_bootstrap(
        self,
    ) -> None:
        result = self.run_launcher(
            {
                "loaded": True,
                "running": False,
                "inspection_status": 5,
                "inspection_error": "permission denied by launchd",
                "uid": 501,
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permission denied by launchd", result.stderr)
        self.assertIn("~/Library/Logs/local-flow.log", result.stderr)
        self.assertEqual(
            self.calls(),
            [["print", "gui/501/com.localflow.dictation"]],
        )

    def test_running_state_without_pid_is_not_disrupted(self) -> None:
        result = self.run_launcher(
            {
                "loaded": True,
                "running": True,
                "include_pid": False,
                "uid": 501,
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without a valid PID", result.stderr)
        self.assertEqual(
            self.calls(),
            [["print", "gui/501/com.localflow.dictation"]],
        )

    def test_startup_timeout_reports_last_observed_state(self) -> None:
        result = self.run_launcher(
            {
                "loaded": True,
                "running": False,
                "kickstart_runs": False,
                "uid": 501,
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("state = not running", result.stderr)
        self.assertEqual(len(self.calls()), 6)
        self.assertEqual(
            self.calls()[:2],
            [
                ["print", "gui/501/com.localflow.dictation"],
                ["kickstart", "gui/501/com.localflow.dictation"],
            ],
        )

    def test_malformed_inspection_output_is_not_treated_as_stopped(self) -> None:
        result = self.run_launcher(
            {
                "loaded": True,
                "running": False,
                "omit_state": True,
                "uid": 501,
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed service state", result.stderr)
        self.assertEqual(
            self.calls(),
            [["print", "gui/501/com.localflow.dictation"]],
        )
