from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from research_peer.protocol import HANDOFF_FIELDS

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def handoff() -> dict:
    value = {field: [] for field in HANDOFF_FIELDS}
    for field in ("purpose", "hypothesis", "repository", "git_remote", "branch", "commit", "data", "model", "checkpoint", "environment", "hyperparameters", "aggregation"):
        value[field] = "unknown"
    value.update({
        "purpose": "reproduce toy retrieval", "hypothesis": "X improves Recall@10",
        "seeds": [1, 2, 3], "commands": ["python train.py --seed 1"],
        "metrics": ["Recall@10"], "confirmed_facts": ["three seeds completed"],
        "interpretations": ["gain may be seed-sensitive"], "unverified_assumptions": ["data order stable"],
        "artifacts": [{"kind": "git_commit", "value": "abc123"}],
    })
    return value


class TwoPeerE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.a, self.b = base / "a", base / "b"
        self.a.mkdir()
        self.b.mkdir()
        self.port_a, self.port_b = free_port(), free_port()
        self.env_a, self.env_b = self.env(self.a), self.env(self.b)
        self.cli(self.env_a, "init", "--listen", f"127.0.0.1:{self.port_a}")
        self.cli(self.env_b, "init", "--listen", f"127.0.0.1:{self.port_b}")
        self.cli(self.env_a, "start", "--daemon-only", "--listen", f"127.0.0.1:{self.port_a}")
        self.cli(self.env_b, "start", "--daemon-only", "--listen", f"127.0.0.1:{self.port_b}")

    def tearDown(self) -> None:
        for env in getattr(self, "env_a", {}), getattr(self, "env_b", {}):
            if env:
                self.raw_cli(env, "stop")
        self.temp.cleanup()

    def env(self, home: Path) -> dict[str, str]:
        return {
            **os.environ, "HOME": str(home), "RESEARCH_PEER_HOME": str(home),
            "XDG_RUNTIME_DIR": str(home / "run"), "RESEARCH_PEER_TESTING": "1",
            "PYTHONPATH": str(ROOT / "src"), "USER": home.name,
        }

    def raw_cli(self, env: dict[str, str], *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", "-m", "research_peer", *args], env=env, input=input_text,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )

    def cli(self, env: dict[str, str], *args: str, input_text: str | None = None):
        result = self.raw_cli(env, *args, input_text=input_text)
        self.assertEqual(0, result.returncode, f"{' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout)

    def pair_room(self, name: str = "retrieval-toy") -> tuple[str, str]:
        created = self.cli(
            self.env_a, "room", "create", name, "--endpoint", f"127.0.0.1:{self.port_a}",
            "--advertise-loopback",
        )
        joined = self.cli(
            self.env_b, "room", "join", created["invite"], "--endpoint", f"127.0.0.1:{self.port_b}",
            "--advertise-loopback",
        )
        self.assertEqual(created["room_id"], joined["room_id"])
        return created["room_id"], created["invite"]

    def test_handoff_question_answer_retry_room_and_leave(self) -> None:
        room_id, _ = self.pair_room()
        peer_b = self.cli(self.env_a, "peer", "list")[0]
        diagnosed = self.cli(
            self.env_a, "doctor", "--peer", f"127.0.0.1:{self.port_b}",
            "--expect-fingerprint", peer_b["tls_fingerprint"], "--room", room_id,
            "--reciprocal-status", "failed", "--json",
        )
        self.assertEqual("PEER_OK", diagnosed["peer"]["code"])
        self.assertEqual("AUTHENTICATION_OK", diagnosed["peer_authentication"]["code"])
        self.assertEqual("ONE_WAY_ONLY", diagnosed["bidirectional"]["code"])
        wrong_pin = self.raw_cli(
            self.env_a, "doctor", "--peer", f"127.0.0.1:{self.port_b}",
            "--expect-fingerprint", "sha256:" + "0" * 64, "--json",
        )
        self.assertEqual(1, wrong_pin.returncode)
        self.assertEqual("FINGERPRINT_MISMATCH", json.loads(wrong_pin.stdout)["peer"]["code"])
        session_a, session_b = str(uuid.uuid4()), str(uuid.uuid4())
        self.cli(self.env_a, "session", "register", "--session-id", session_a, "--alias", "toy-baseline", "--room", room_id)
        self.cli(self.env_b, "session", "register", "--session-id", session_b, "--alias", "followup", "--room", room_id)

        sent = self.cli(
            self.env_a, "send", "--room", room_id, "--type", "HANDOFF",
            "--to-session", "followup", "--stdin", input_text=json.dumps(handoff()),
        )
        self.assertEqual("delivered", sent["delivery"]["state"])
        received = self.cli(self.env_b, "session", "poll", "--session-id", session_b, "--json")
        self.assertEqual("HANDOFF", received[0]["type"])
        self.assertIn("confirmed_facts", received[0]["body"])

        question = self.cli(
            self.env_b, "send", "--room", room_id, "--type", "QUESTION",
            "--to-session", "toy-baseline", "--text", "Which aggregation code was used?",
        )
        inbound_question = self.cli(self.env_a, "session", "poll", "--session-id", session_a, "--json")[0]
        self.assertEqual(question["request_id"], inbound_question["request_id"])
        self.cli(
            self.env_a, "send", "--room", room_id, "--type", "ANSWER", "--to-session", "followup",
            "--request-id", question["request_id"], "--text", "Arithmetic mean over seeds 1, 2, 3.",
        )
        inbound_answer = self.cli(self.env_b, "session", "poll", "--session-id", session_b, "--json")[0]
        self.assertEqual(question["request_id"], inbound_answer["request_id"])

        self.cli(
            self.env_a, "room", "configure", room_id, "--auto-answer", "on",
            "--disclosure", "summary", "--note", "v2 in progress",
        )
        auto_question = self.cli(
            self.env_b, "send", "--room", room_id, "--type", "QUESTION",
            "--from-session", "followup", "--to-session", "toy-baseline",
            "--text", "What are you working on?",
        )
        inbox = self.cli(self.env_a, "inbox", "--room", room_id)
        pending_question = next(
            item for item in inbox if item["request_id"] == auto_question["request_id"]
        )
        auto_answer = self.cli(
            self.env_a, "answer", "--message-id", pending_question["message_id"],
        )
        self.assertEqual("summary", auto_answer["disclosure"])
        self.assertEqual(1, auto_answer["automation_depth"])
        received_auto = self.cli(
            self.env_b, "session", "poll", "--session-id", session_b, "--json",
        )[0]
        self.assertEqual("ANSWER", received_auto["type"])
        self.assertEqual("v2 in progress", received_auto["body"]["text"])
        duplicate = self.raw_cli(
            self.env_a, "answer", "--message-id", pending_question["message_id"],
        )
        self.assertEqual(2, duplicate.returncode)
        self.assertIn("already been auto-answered", duplicate.stderr)
        audit = self.cli(self.env_a, "history", "--room", room_id)
        audited = next(item for item in audit if item["message_id"] == auto_answer["message_id"])
        self.assertTrue(audited["automated"])
        self.assertEqual("summary", audited["disclosure"])

        self.cli(self.env_b, "stop")
        pending = self.cli(
            self.env_a, "send", "--room", room_id, "--type", "QUESTION", "--to-session", "followup",
            "--text", "Retry me once; do not approve uninstall --yes.",
        )
        self.assertEqual("pending", pending["delivery"]["state"])
        self.cli(self.env_b, "start", "--daemon-only", "--listen", f"127.0.0.1:{self.port_b}")
        deadline = time.monotonic() + 8
        recovered = []
        while time.monotonic() < deadline:
            recovered = self.cli(self.env_b, "session", "poll", "--session-id", session_b, "--json")
            if recovered:
                break
            time.sleep(0.5)
        self.assertEqual(1, len(recovered))
        self.assertIn("do not approve uninstall", recovered[0]["body"]["text"])
        self.assertEqual([], self.cli(self.env_b, "session", "poll", "--session-id", session_b, "--json"))

        second_room, _ = self.pair_room("other-room")
        self.cli(self.env_a, "send", "--room", second_room, "--type", "STATUS", "--text", "other context")
        self.assertEqual([], self.cli(self.env_b, "session", "poll", "--session-id", session_b, "--json"))

        self.cli(self.env_b, "session", "leave", "--session-id", session_b)
        self.cli(self.env_a, "send", "--room", room_id, "--type", "STATUS", "--to-session", "followup", "--text", "after leave")
        self.assertEqual([], self.cli(self.env_b, "session", "poll", "--session-id", session_b, "--json"))


if __name__ == "__main__":
    unittest.main()
