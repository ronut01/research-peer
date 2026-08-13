from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from research_peer.identity import Identity, cert_pem, verify
from research_peer.paths import Paths
from research_peer.protocol import new_envelope
from research_peer.store import Store


class IdentityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.copy()
        os.environ["RESEARCH_PEER_HOME"] = self.temp.name
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("XDG_DATA_HOME", None)
        os.environ.pop("XDG_STATE_HOME", None)
        os.environ.pop("XDG_CACHE_HOME", None)
        os.environ.pop("XDG_RUNTIME_DIR", None)
        self.paths = Paths.discover()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old)
        self.temp.cleanup()

    def test_identity_sign_verify_and_permissions(self) -> None:
        identity = Identity.load_or_create(self.paths, "alice")
        payload = b"research packet"
        verify(cert_pem(identity.cert_path), payload, identity.sign(payload), identity.fingerprint)
        self.assertEqual(0o600, self.paths.identity_key.stat().st_mode & 0o777)
        other_home = Path(self.temp.name) / "other"
        os.environ["RESEARCH_PEER_HOME"] = str(other_home)
        other = Identity.load_or_create(Paths.discover(), "bob")
        with self.assertRaises(Exception):
            verify(cert_pem(other.cert_path), payload, identity.sign(payload), other.fingerprint)

    def test_room_name_collision_requires_uuid(self) -> None:
        store = Store(self.paths.db_file)
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        store.create_room(first, "same")
        store.create_room(second, "same")
        with self.assertRaisesRegex(LookupError, "ambiguous"):
            store.resolve_room("same")
        self.assertEqual(first, store.resolve_room(first)["room_id"])
        store.close()

    def test_outbox_dedup_and_session_room_isolation(self) -> None:
        store = Store(self.paths.db_file)
        room = str(uuid.uuid4())
        store.create_room(room, "toy")
        identity = Identity.load_or_create(self.paths, "alice")
        peer_id = str(uuid.uuid4())
        store.add_peer(
            peer_id=peer_id, user_name="bob", fingerprint=identity.fingerprint,
            tls_fingerprint=identity.tls_fingerprint, certificate=cert_pem(identity.cert_path),
            endpoint="127.0.0.1:50001", room_id=room,
        )
        envelope = new_envelope(
            room_id=room, message_type="STATUS", from_user="bob", from_session="source",
            to_user="alice", to_session="target", body={"text": "ready"},
        )
        self.assertTrue(store.receive(envelope, identity.fingerprint, "a" * 32))
        self.assertFalse(store.receive(envelope, identity.fingerprint, "a" * 32))
        store.register_session(str(uuid.uuid4()), "other", "alice", room)
        self.assertEqual([], store.poll_session(store.list_sessions()[0]["session_id"]))
        target_id = str(uuid.uuid4())
        store.register_session(target_id, "target", "alice", room)
        self.assertEqual(envelope["message_id"], store.poll_session(target_id)[0]["message_id"])
        self.assertEqual([], store.poll_session(target_id))
        store.close()

    def test_rate_and_request_loop_limits(self) -> None:
        store = Store(self.paths.db_file)
        room = str(uuid.uuid4())
        store.create_room(room, "limited")
        identity = Identity.load_or_create(self.paths, "alice")
        request_id = str(uuid.uuid4())
        for index in range(4):
            envelope = new_envelope(
                room_id=room, message_type="QUESTION", from_user="peer", from_session="agent",
                to_user="alice", to_session="", body={"text": "loop"}, request_id=request_id,
            )
            self.assertTrue(store.receive(envelope, identity.fingerprint, f"nonce-{index:02d}-abcdefghijklmnop"))
        envelope = new_envelope(
            room_id=room, message_type="ANSWER", from_user="peer", from_session="agent",
            to_user="alice", to_session="", body={"text": "too many"}, request_id=request_id,
        )
        with self.assertRaisesRegex(PermissionError, "loop limit"):
            store.receive(envelope, identity.fingerprint, "nonce-loop-exceeded-abcdefghijkl")
        for index in range(6):
            status = new_envelope(
                room_id=room, message_type="STATUS", from_user="peer", from_session="agent",
                to_user="alice", to_session="", body={"text": str(index)},
            )
            store.receive(status, identity.fingerprint, f"rate-{index:02d}-abcdefghijklmnop")
        status = new_envelope(
            room_id=room, message_type="STATUS", from_user="peer", from_session="agent",
            to_user="alice", to_session="", body={"text": "burst"},
        )
        with self.assertRaisesRegex(PermissionError, "rate limit"):
            store.receive(status, identity.fingerprint, "rate-limit-abcdefghijklmnop")
        store.close()


if __name__ == "__main__":
    unittest.main()
