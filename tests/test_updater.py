from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_peer.installer import install
from research_peer.paths import Paths
from research_peer.updater import UpdateError, _clone_official, inspect_release, update


ROOT = Path(__file__).resolve().parents[1]


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.old = os.environ.copy()
        os.environ.update(
            {
                "HOME": str(self.home),
                "RESEARCH_PEER_HOME": str(self.home),
                "RESEARCH_PEER_TESTING": "1",
                "XDG_RUNTIME_DIR": self.runtime_temp.name,
                "PATH": "/usr/bin:/bin",
            }
        )
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            os.environ.pop(key, None)
        self.paths = Paths.discover()
        install(ROOT, self.paths)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old)
        self.temp.cleanup()
        self.runtime_temp.cleanup()

    def _candidate(self, version: str) -> Path:
        candidate = Path(self.temp.name) / f"candidate-{version}"
        shutil.copytree(
            ROOT,
            candidate,
            ignore=shutil.ignore_patterns(".git", "dist", "node_modules", "__pycache__", "*.pyc"),
        )
        init_file = candidate / "src/research_peer/__init__.py"
        init_file.write_text(init_file.read_text().replace('2.0.0', version), encoding="utf-8")
        pyproject = candidate / "pyproject.toml"
        pyproject.write_text(pyproject.read_text().replace('version = "2.0.0"', f'version = "{version}"'), encoding="utf-8")
        for relative in ("package.json", "package-lock.json", "plugin/.claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
            path = candidate / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            if relative == "package.json":
                value["version"] = version
            elif relative == "package-lock.json":
                value["version"] = version
                value["packages"][""]["version"] = version
            elif relative.startswith("plugin/"):
                value["version"] = version
            else:
                value["plugins"][0]["version"] = version
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return candidate

    @staticmethod
    def _revision() -> dict[str, str]:
        return {"commit": "a" * 40, "branch": "main"}

    def test_source_release_versions_are_consistent(self) -> None:
        self.assertEqual("2.0.0", inspect_release(ROOT)["version"])
        candidate = self._candidate("2.0.1")
        plugin = candidate / "plugin/.claude-plugin/plugin.json"
        value = json.loads(plugin.read_text())
        value["version"] = "9.9.9"
        plugin.write_text(json.dumps(value))
        with self.assertRaisesRegex(UpdateError, "inconsistent component versions"):
            inspect_release(candidate)

    def test_clone_records_exact_test_origin_and_commit(self) -> None:
        candidate = self._candidate("2.0.1")
        subprocess.run(["git", "init", str(candidate)], stdout=subprocess.DEVNULL, check=True)
        subprocess.run(["git", "-C", str(candidate), "add", "."], stdout=subprocess.DEVNULL, check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(candidate),
                "-c",
                "user.name=Research Peer Test",
                "-c",
                "user.email=research-peer@example.invalid",
                "commit",
                "-m",
                "candidate",
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        os.environ["RESEARCH_PEER_UPDATE_REPOSITORY"] = str(candidate)
        destination = Path(self.temp.name) / "clone"
        revision = _clone_official(destination)
        self.assertEqual(str(candidate), subprocess.run(
            ["git", "-C", str(destination), "remote", "get-url", "origin"],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip())
        self.assertRegex(revision["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual("2.0.1", inspect_release(destination)["version"])

    def test_check_reports_update_without_installing(self) -> None:
        candidate = self._candidate("2.0.1")

        def clone(destination: Path) -> dict[str, str]:
            shutil.copytree(candidate, destination)
            return self._revision()

        with mock.patch("research_peer.updater._clone_official", side_effect=clone), mock.patch(
            "research_peer.updater._install_checkout"
        ) as installer:
            result = update(check_only=True, assume_yes=False, paths=self.paths)
        self.assertTrue(result["update_available"])
        self.assertFalse(result["updated"])
        installer.assert_not_called()

    def test_update_installs_candidate_and_preserves_state(self) -> None:
        candidate = self._candidate("2.0.1")
        identity_before = self.paths.identity_key.read_bytes()
        connection = sqlite3.connect(self.paths.db_file)
        connection.execute(
            "INSERT INTO rooms(room_id,display_name,status,created_at) VALUES(?,?,?,?)",
            ("11111111-1111-4111-8111-111111111111", "preserve-me", "active", "2026-08-18T00:00:00Z"),
        )
        connection.commit()
        connection.close()

        def clone(destination: Path) -> dict[str, str]:
            shutil.copytree(candidate, destination)
            return self._revision()

        def install_candidate(checkout: Path) -> None:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(checkout / "src")
            completed = subprocess.run(
                [sys.executable, "-m", "research_peer.installer", "install", "--source", str(checkout)],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if completed.returncode:
                raise AssertionError(completed.stderr)

        with mock.patch("research_peer.updater._clone_official", side_effect=clone), mock.patch(
            "research_peer.updater._install_checkout", side_effect=install_candidate
        ), mock.patch("research_peer.updater._daemon_running", return_value=True), mock.patch(
            "research_peer.updater._stop_for_update"
        ) as stop, mock.patch("research_peer.updater._restart_after_update") as restart:
            result = update(check_only=False, assume_yes=True, paths=self.paths)

        self.assertTrue(result["updated"])
        self.assertTrue(result["daemon_restarted"])
        stop.assert_called_once_with()
        restart.assert_called_once_with(self.paths)
        self.assertEqual(identity_before, self.paths.identity_key.read_bytes())
        self.assertEqual("2.0.1", json.loads(self.paths.manifest_file.read_text())["version"])
        self.assertTrue((self.home / ".claude/skills/research-peer-plugin/skills/update/SKILL.md").is_file())
        version = subprocess.run(
            [str(self.home / ".local/bin/research-peer"), "version"],
            env=os.environ.copy(),
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        self.assertEqual("2.0.1", version)
        connection = sqlite3.connect(self.paths.db_file)
        room = connection.execute("SELECT display_name FROM rooms WHERE room_id=?", ("11111111-1111-4111-8111-111111111111",)).fetchone()
        connection.close()
        self.assertEqual(("preserve-me",), room)

    def test_downgrade_is_refused(self) -> None:
        candidate = self._candidate("1.9.9")

        def clone(destination: Path) -> dict[str, str]:
            shutil.copytree(candidate, destination)
            return self._revision()

        with mock.patch("research_peer.updater._clone_official", side_effect=clone), mock.patch(
            "research_peer.updater._install_checkout"
        ) as installer, self.assertRaisesRegex(UpdateError, "refusing downgrade"):
            update(check_only=False, assume_yes=True, paths=self.paths)
        installer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
