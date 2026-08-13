from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from research_peer.installer import install, residue_scan
from research_peer.paths import Paths

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.old = os.environ.copy()
        os.environ.update({"HOME": str(self.home), "RESEARCH_PEER_HOME": str(self.home), "RESEARCH_PEER_TESTING": "1"})
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            os.environ.pop(key, None)
        os.environ["XDG_RUNTIME_DIR"] = self.runtime_temp.name
        self.paths = Paths.discover()
        claude = self.home / ".claude"
        claude.mkdir(parents=True)
        (claude / "settings.json").write_text('{"unrelated": true}\n')
        (claude / "skills/unrelated").mkdir(parents=True)
        (claude / "skills/unrelated/SKILL.md").write_text("unrelated")
        (self.home / "experiment.txt").write_text("keep")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old)
        self.temp.cleanup()
        self.runtime_temp.cleanup()

    def run_cli(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        return subprocess.run([str(self.home / ".local/bin/research-peer"), *args], input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def test_install_dry_run_purge_and_idempotent_uninstall(self) -> None:
        install(ROOT, self.paths)
        install(ROOT, self.paths)
        self.assertTrue((self.home / ".local/bin/research-peer").exists())
        self.assertTrue((self.home / ".claude/skills/research-peer/SKILL.md").exists())
        self.assertTrue((self.home / ".claude/skills/research-peer-plugin/.claude-plugin/plugin.json").exists())
        self.assertTrue((self.home / ".claude/skills/research-peer-plugin/.mcp.json").exists())
        for action in ("make", "join", "ask", "handoff", "rooms", "use", "status", "leave", "delete", "peers"):
            self.assertTrue((self.home / f".claude/skills/research-peer-plugin/skills/{action}/SKILL.md").exists())
        before = self.paths.manifest_file.read_bytes()
        dry = self.run_cli("uninstall", "--dry-run", "--purge")
        self.assertEqual(0, dry.returncode, dry.stderr)
        self.assertEqual(before, self.paths.manifest_file.read_bytes())
        outside = self.home / "outside.txt"
        outside.write_text("preserve symlink target")
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.cache_dir / "link").symlink_to(outside)
        purged = self.run_cli("uninstall", "--yes", "--purge")
        self.assertEqual(0, purged.returncode, purged.stderr)
        self.assertTrue(outside.exists())
        self.assertEqual('{"unrelated": true}\n', (self.home / ".claude/settings.json").read_text())
        self.assertTrue((self.home / ".claude/skills/unrelated/SKILL.md").exists())
        self.assertTrue((self.home / "experiment.txt").exists())
        self.assertFalse(any(path.exists() or path.is_symlink() for path in residue_scan(self.paths)))
        second = subprocess.run(
            ["python3", "-m", "research_peer", "uninstall", "--yes", "--purge"],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(0, second.returncode, second.stderr)

    def test_keep_data_removes_program_and_preserves_identity_history(self) -> None:
        install(ROOT, self.paths)
        kept = self.run_cli("uninstall", "--yes", "--keep-data")
        self.assertEqual(0, kept.returncode, kept.stderr)
        self.assertFalse((self.home / ".local/bin/research-peer").exists())
        self.assertFalse((self.home / ".claude/skills/research-peer").exists())
        self.assertFalse((self.home / ".claude/skills/research-peer-plugin").exists())
        self.assertFalse((self.home / ".config/systemd/user/research-peer.service").exists())
        self.assertTrue(self.paths.identity_key.exists())
        self.assertTrue(self.paths.db_file.exists())
        self.assertTrue((self.home / "experiment.txt").exists())
        self.assertTrue((self.home / ".claude/skills/unrelated/SKILL.md").exists())

    def test_default_uninstall_removes_program_state_and_keys(self) -> None:
        install(ROOT, self.paths)
        removed = self.run_cli("uninstall", "--yes")
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertFalse(self.paths.identity_key.exists())
        self.assertFalse(self.paths.db_file.exists())
        self.assertFalse(any(path.exists() or path.is_symlink() for path in residue_scan(self.paths)))
        self.assertTrue((self.home / "experiment.txt").exists())
        self.assertTrue((self.home / ".claude/settings.json").exists())


if __name__ == "__main__":
    unittest.main()
