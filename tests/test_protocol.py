from __future__ import annotations

import copy
import unittest
import uuid
from datetime import timedelta

from research_peer.protocol import (
    HANDOFF_FIELDS, ProtocolError, canonical_json, new_envelope, utc_now, validate_envelope,
)


def handoff_body() -> dict:
    value = {field: [] for field in HANDOFF_FIELDS}
    for field in ("purpose", "hypothesis", "repository", "git_remote", "branch", "commit", "data", "model", "checkpoint", "environment", "hyperparameters", "aggregation"):
        value[field] = "unknown"
    value["artifacts"] = [{"kind": "git_commit", "value": "abc123"}]
    return value


class ProtocolTests(unittest.TestCase):
    def test_question_and_answer_keep_request_id(self) -> None:
        room_id = str(uuid.uuid4())
        question = new_envelope(
            room_id=room_id, message_type="QUESTION", from_user="a", from_session="toy",
            to_user="b", to_session="next", body={"text": "which seeds?"},
        )
        answer = new_envelope(
            room_id=room_id, message_type="ANSWER", from_user="b", from_session="next",
            to_user="a", to_session="toy", body={"text": "1, 2, 3"}, request_id=question["request_id"],
        )
        self.assertEqual(question["request_id"], answer["request_id"])

    def test_handoff_requires_complete_reproduction_fields(self) -> None:
        envelope = new_envelope(
            room_id=str(uuid.uuid4()), message_type="HANDOFF", from_user="a", from_session="toy",
            to_user="b", to_session="next", body=handoff_body(),
        )
        self.assertEqual("HANDOFF", envelope["type"])
        broken = copy.deepcopy(envelope)
        del broken["body"]["failed_attempts"]
        with self.assertRaisesRegex(ProtocolError, "failed_attempts"):
            validate_envelope(broken)

    def test_unknown_type_version_and_fields_rejected(self) -> None:
        base = new_envelope(
            room_id=str(uuid.uuid4()), message_type="STATUS", from_user="a", from_session="s",
            to_user="b", to_session="", body={"text": "ready"},
        )
        for field, value, code in (
            ("type", "EXECUTE", "UNKNOWN_MESSAGE_TYPE"),
            ("protocol_version", "99", "PROTOCOL_MISMATCH"),
        ):
            changed = {**base, field: value}
            with self.assertRaises(ProtocolError) as raised:
                validate_envelope(changed)
            self.assertEqual(code, raised.exception.code)
        changed = {**base, "permission_approved": True}
        with self.assertRaises(ProtocolError):
            validate_envelope(changed)

    def test_clock_replay_window_and_size(self) -> None:
        base = new_envelope(
            room_id=str(uuid.uuid4()), message_type="STATUS", from_user="a", from_session="s",
            to_user="b", to_session="", body={"text": "ready"},
        )
        with self.assertRaises(ProtocolError) as raised:
            validate_envelope(base, now=utc_now() + timedelta(hours=1))
        self.assertEqual("TIMESTAMP_INVALID", raised.exception.code)
        base["body"]["text"] = "x" * 3000
        with self.assertRaises(ProtocolError) as raised:
            validate_envelope(base, max_bytes=1024)
        self.assertEqual("PAYLOAD_TOO_LARGE", raised.exception.code)

    def test_canonical_json_is_stable(self) -> None:
        self.assertEqual(canonical_json({"b": 1, "a": "한글"}), canonical_json({"a": "한글", "b": 1}))


if __name__ == "__main__":
    unittest.main()

