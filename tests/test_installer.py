from __future__ import annotations

import plistlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build-launcher.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "install-launcher.sh"
FAKE_LAUNCHCTL = PROJECT_ROOT / "tests" / "fake_launchctl.py"
INSTALLED_RUNTIME = (
    Path.home() / "Library" / "Application Support" / "LocalFlow"
    / "Runtime" / "LocalFlow.app"
)
INSTALLED_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / "com.localflow.dictation.plist"
)


class LauncherBundleBuildTests(unittest.TestCase):
    def test_builder_creates_valid_signed_launcher_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "LocalFlow.app"
            result = subprocess.run(
                [str(BUILD_SCRIPT), str(app)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            with (app / "Contents" / "Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            self.assertEqual(info["CFBundleIdentifier"], "com.localflow.launcher")
            self.assertEqual(info["CFBundleExecutable"], "LocalFlow")
            self.assertIs(info["LSUIElement"], True)
            self.assertEqual(info["CFBundleIconFile"], "LocalFlow.icns")

            executable = app / "Contents" / "MacOS" / "LocalFlow"
            icon = app / "Contents" / "Resources" / "LocalFlow.icns"
            self.assertTrue(executable.is_file())
            self.assertGreater(icon.stat().st_size, 1_000)

            file_result = subprocess.run(
                ["/usr/bin/file", str(executable)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Mach-O", file_result.stdout)
            self.assertIn("x86_64", file_result.stdout)

            signature = subprocess.run(
                [
                    "/usr/bin/codesign",
                    "--verify",
                    "--deep",
                    "--strict",
                    "--verbose=4",
                    str(app),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(signature.returncode, 0, signature.stderr)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = tempfile.TemporaryDirectory()
        self.root = Path(self.fixture.name)
        self.applications = self.root / "Applications"
        self.home = self.root / "Users" / "tester"
        self.support = self.home / "Library" / "Application Support" / "LocalFlow"
        self.launch_agents = self.home / "Library" / "LaunchAgents"
        self.visible_app = self.applications / "LocalFlow.app"
        self.plist = self.launch_agents / "com.localflow.dictation.plist"
        self.state_path = self.root / "launchctl-state.json"
        self.log_path = self.root / "launchctl-calls.jsonl"

        self.applications.mkdir(parents=True)
        self.launch_agents.mkdir(parents=True)
        runtime_fixture = (
            INSTALLED_RUNTIME
            if INSTALLED_RUNTIME.is_dir()
            else Path("/Applications/LocalFlow.app")
        )
        shutil.copytree(runtime_fixture, self.visible_app)
        if not INSTALLED_PLIST.is_file():
            self.skipTest("no installed LocalFlow LaunchAgent on this machine")
        shutil.copy2(INSTALLED_PLIST, self.plist)
        with self.plist.open("rb") as stream:
            launch_agent = plistlib.load(stream)
        launch_agent["ProgramArguments"][0] = str(
            self.visible_app / "Contents" / "MacOS" / "LocalFlow"
        )
        with self.plist.open("wb") as stream:
            plistlib.dump(launch_agent, stream)
        self.state_path.write_text(
            json.dumps(
                {
                    "loaded": True,
                    "running": True,
                    "pid": 51001,
                    "uid": 501,
                }
            )
        )
        self.log_path.write_text("")
        os.chmod(FAKE_LAUNCHCTL, 0o755)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "LOCALFLOW_TEST_MODE": "1",
                "LOCALFLOW_VISIBLE_APP": str(self.visible_app),
                "LOCALFLOW_SUPPORT_ROOT": str(self.support),
                "LOCALFLOW_PLIST": str(self.plist),
                "LOCALFLOW_LAUNCHCTL": str(FAKE_LAUNCHCTL),
                "LOCALFLOW_BUILDER": str(BUILD_SCRIPT),
                "LOCALFLOW_UID": "501",
                "LOCALFLOW_HOME": str(self.home),
                "FAKE_LAUNCHCTL_STATE": str(self.state_path),
                "FAKE_LAUNCHCTL_LOG": str(self.log_path),
            }
        )
        return environment

    def manifest(self) -> dict[str, str]:
        result = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                relative = str(path.relative_to(self.root))
                if relative in {
                    "launchctl-calls.jsonl",
                    "launchctl-state.json",
                }:
                    continue
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    @staticmethod
    def bundle_manifest(bundle: Path) -> dict[str, str]:
        result = {}
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                result[str(path.relative_to(bundle))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return result

    def test_dry_run_reports_preflight_without_mutation(self) -> None:
        before = self.manifest()
        result = subprocess.run(
            [str(INSTALL_SCRIPT), "--dry-run"],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.manifest(), before)
        self.assertIn(f"source_runtime={self.visible_app}", result.stdout)
        self.assertIn(
            f"runtime_destination={self.support / 'Runtime' / 'LocalFlow.app'}",
            result.stdout,
        )
        self.assertIn(f"visible_app={self.visible_app}", result.stdout)
        self.assertIn(f"launch_agent={self.plist}", result.stdout)
        self.assertIn("service_loaded=true", result.stdout)
        self.assertIn("service_running=true", result.stdout)
        self.assertRegex(result.stdout, r"runtime_cdhash=[0-9a-f]{40}")
        calls = [
            json.loads(line)
            for line in self.log_path.read_text().splitlines()
            if line
        ]
        self.assertEqual(
            calls,
            [["print", "gui/501/com.localflow.dictation"]],
        )

    def test_successful_migration_preserves_runtime_and_starts_service(
        self,
    ) -> None:
        original_runtime = self.bundle_manifest(self.visible_app)
        original_plist = self.plist.read_bytes()

        result = subprocess.run(
            [str(INSTALL_SCRIPT)],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = self.support / "Runtime" / "LocalFlow.app"
        self.assertEqual(self.bundle_manifest(runtime), original_runtime)

        with (self.visible_app / "Contents" / "Info.plist").open("rb") as stream:
            visible_info = plistlib.load(stream)
        self.assertEqual(
            visible_info["CFBundleIdentifier"],
            "com.localflow.launcher",
        )

        with self.plist.open("rb") as stream:
            launch_agent = plistlib.load(stream)
        self.assertEqual(
            launch_agent["ProgramArguments"][0],
            str(runtime / "Contents" / "MacOS" / "LocalFlow"),
        )

        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["loaded"])
        self.assertTrue(final_state["running"])

        backup_line = next(
            line
            for line in result.stdout.splitlines()
            if line.startswith("backup_path=")
        )
        backup = Path(backup_line.split("=", 1)[1])
        self.assertTrue((backup / "LocalFlow.app").is_dir())
        self.assertEqual(
            (backup / "com.localflow.dictation.plist").read_bytes(),
            original_plist,
        )
        self.assertIn("installation=passed", result.stdout)

    def test_rerun_reuses_verified_runtime_without_duplication(self) -> None:
        first = subprocess.run(
            [str(INSTALL_SCRIPT)],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        runtime = self.support / "Runtime" / "LocalFlow.app"
        runtime_inode = runtime.stat().st_ino
        runtime_manifest = self.bundle_manifest(runtime)

        second = subprocess.run(
            [str(INSTALL_SCRIPT)],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("runtime_reused=true", second.stdout)
        self.assertEqual(runtime.stat().st_ino, runtime_inode)
        self.assertEqual(self.bundle_manifest(runtime), runtime_manifest)
        self.assertEqual(
            len(list((self.support / "Runtime").glob("LocalFlow.app"))),
            1,
        )
        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["loaded"])
        self.assertTrue(final_state["running"])

    def assert_fault_restores_original_installation(self, checkpoint: str) -> None:
        original_app = self.bundle_manifest(self.visible_app)
        original_plist = self.plist.read_bytes()
        result = subprocess.run(
            [str(INSTALL_SCRIPT), "--fault-after", checkpoint],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"injected installer fault at {checkpoint}", result.stderr)
        self.assertIn("rollback=passed", result.stderr)
        self.assertEqual(self.bundle_manifest(self.visible_app), original_app)
        self.assertEqual(self.plist.read_bytes(), original_plist)
        self.assertFalse(
            (self.support / "Runtime" / "LocalFlow.app").exists()
        )
        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["loaded"])
        self.assertTrue(final_state["running"])
        backups = list((self.support / "Backups").glob("*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "LocalFlow.app").is_dir())

    def test_fault_after_plist_restores_original_installation(self) -> None:
        self.assert_fault_restores_original_installation("after-plist")

    def test_fault_after_visible_app_restores_original_installation(self) -> None:
        self.assert_fault_restores_original_installation("after-visible-app")

    def test_fault_after_bootstrap_restores_original_installation(self) -> None:
        self.assert_fault_restores_original_installation("after-bootstrap")

    def test_rollback_restores_previously_stopped_loaded_state(self) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "loaded": True,
                    "running": False,
                    "uid": 501,
                }
            )
        )
        original_app = self.bundle_manifest(self.visible_app)
        original_plist = self.plist.read_bytes()

        result = subprocess.run(
            [str(INSTALL_SCRIPT), "--fault-after", "after-bootstrap"],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rollback=passed", result.stderr)
        self.assertEqual(self.bundle_manifest(self.visible_app), original_app)
        self.assertEqual(self.plist.read_bytes(), original_plist)
        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["loaded"])
        self.assertFalse(final_state["running"])

    def test_migration_waits_for_unload_and_retries_bootstrap_eio(self) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "loaded": True,
                    "running": True,
                    "pid": 51001,
                    "uid": 501,
                    "bootout_linger_prints": 2,
                    "bootstrap_eio_failures": 2,
                }
            )
        )

        result = subprocess.run(
            [str(INSTALL_SCRIPT)],
            cwd=PROJECT_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [
            json.loads(line)
            for line in self.log_path.read_text().splitlines()
            if line
        ]
        bootout_index = next(
            index for index, call in enumerate(calls) if call[0] == "bootout"
        )
        bootstrap_indices = [
            index for index, call in enumerate(calls) if call[0] == "bootstrap"
        ]
        prints_after_bootout = [
            call
            for call in calls[bootout_index + 1 : bootstrap_indices[0]]
            if call[0] == "print"
        ]
        self.assertGreaterEqual(len(prints_after_bootout), 3)
        self.assertGreaterEqual(len(bootstrap_indices), 3)
        final_state = json.loads(self.state_path.read_text())
        self.assertTrue(final_state["loaded"])
        self.assertTrue(final_state["running"])
