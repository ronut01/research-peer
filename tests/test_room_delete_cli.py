from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RoomDeleteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "RESEARCH_PEER_HOME": str(self.home),
            "RESEARCH_PEER_TESTING": "1",
            "XDG_RUNTIME_DIR": str(self.home / "run"),
            "PYTHONPATH": str(ROOT / "src"),
        }
        self.cli("init", "--listen", "127.0.0.1:54321")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def raw(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "-m", "research_peer", *args],
            env=self.env,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )

    def cli(self, *args: str) -> dict | list:
        result = self.raw(*args)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_make_dry_run_noninteractive_guard_and_confirmed_delete(self) -> None:
        created = self.cli("room", "make", "toy")
        room_id = created["room_id"]
        plan = self.cli("room", "delete", "toy", "--dry-run")
        self.assertEqual(room_id, plan["room_id"])
        self.assertEqual(1, len(self.cli("room", "list")))

        refused = self.raw("room", "delete", "toy")
        self.assertEqual(2, refused.returncode)
        self.assertIn("non-interactive room deletion", refused.stderr)
        self.assertEqual(1, len(self.cli("room", "list")))

        deleted = self.raw("room", "delete", "toy", "--yes")
        self.assertEqual(0, deleted.returncode, deleted.stderr)
        objects = self._json_objects(deleted.stdout)
        self.assertEqual(room_id, objects[-1]["deleted"])
        self.assertEqual([], self.cli("room", "list"))

    @staticmethod
    def _json_objects(text: str) -> list[dict]:
        decoder = json.JSONDecoder()
        values = []
        index = 0
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text):
                break
            value, index = decoder.raw_decode(text, index)
            values.append(value)
        return values


if __name__ == "__main__":
    unittest.main()
