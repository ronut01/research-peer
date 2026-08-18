from __future__ import annotations

import os
import tempfile
import unittest

from research_peer.identity import Identity
from research_peer.paths import Paths
from research_peer.rooms import DEFAULT_INVITE_MINUTES, _validate_advertised_endpoint, create_invite
from research_peer.store import Store


class RoomsV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.copy()
        os.environ["RESEARCH_PEER_HOME"] = self.temp.name
        os.environ["XDG_RUNTIME_DIR"] = os.path.join(self.temp.name, "run")
        self.paths = Paths.discover()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old)
        self.temp.cleanup()

    def test_advertised_wildcard_and_implicit_loopback_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "wildcard"):
            _validate_advertised_endpoint("0.0.0.0:40000")
        with self.assertRaisesRegex(ValueError, "--advertise-loopback"):
            _validate_advertised_endpoint("127.0.0.1:40000")
        self.assertEqual(("127.0.0.1", 40000), _validate_advertised_endpoint(
            "127.0.0.1:40000", allow_loopback=True,
        ))

    def test_invite_default_is_twenty_four_hours(self) -> None:
        self.assertEqual(1440, DEFAULT_INVITE_MINUTES)
        store = Store(self.paths.db_file)
        identity = Identity.load_or_create(self.paths, "alice")
        _, invite = create_invite(
            store, identity, display_name="tunnel", endpoint="127.0.0.1:40000",
            allow_loopback=True,
        )
        self.assertIn("expires_at", invite)
        store.close()


if __name__ == "__main__":
    unittest.main()
