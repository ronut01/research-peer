from __future__ import annotations

import json
import os
import tempfile
import unittest

from research_peer.cli import _daemon_status, _start_daemon
from research_peer.paths import Paths


class DaemonStatusV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.copy()
        os.environ["RESEARCH_PEER_HOME"] = self.temp.name
        os.environ["XDG_RUNTIME_DIR"] = os.path.join(self.temp.name, "run")
        self.paths = Paths.discover()
        self.paths.ensure_runtime()
        self.paths.pid_file.write_text(f"{os.getpid()}\n")
        (self.paths.runtime_dir / "daemon.ready").write_text(
            json.dumps({"host": "127.0.0.1", "port": 41000})
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old)
        self.temp.cleanup()

    def test_status_exposes_config_mismatch_and_start_refuses(self) -> None:
        config = {"listen_host": "127.0.0.1", "listen_port": 42000}
        status = _daemon_status(self.paths, config)
        self.assertTrue(status["running"])
        self.assertTrue(status["config_mismatch"])
        self.assertEqual("127.0.0.1:41000", status["actual_endpoint"])
        with self.assertRaisesRegex(RuntimeError, "already running on 127.0.0.1:41000"):
            _start_daemon(self.paths, "127.0.0.1", 42000)


if __name__ == "__main__":
    unittest.main()
