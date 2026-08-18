from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GuidedOnboardingSkillTests(unittest.TestCase):
    def skill(self, name: str) -> str:
        return (ROOT / f"plugin/skills/{name}/SKILL.md").read_text(encoding="utf-8")

    def test_make_owns_first_time_endpoint_and_daemon_setup(self) -> None:
        make = self.skill("make")
        for required in (
            "do not require the slash command again",
            "direct private/VPN TCP or an owner-approved SSH tunnel",
            "자동 선택",
            "research-peer init --listen ENDPOINT",
            "research-peer stop",
            "research-peer start --daemon-only --listen ENDPOINT",
            "configured_endpoint == actual_endpoint",
            "research-peer room make ROOM --endpoint ENDPOINT",
            "auto-answer is off",
        ):
            self.assertIn(required, make)
        self.assertIn("must not have to run `init`", make)

    def test_join_owns_endpoint_flags_and_continues_after_invite_prompt(self) -> None:
        join = self.skill("join")
        for required in (
            "do not require the slash command again",
            "Determine the connection path from the invite endpoint",
            "research-peer init --listen ENDPOINT",
            "research-peer start --daemon-only --listen ENDPOINT",
            "research-peer room join INVITE --endpoint ENDPOINT",
            "adding `--advertise-loopback` only for the verified tunnel path",
            "Never ask the owner to type or assemble this command",
            "auto-answer is off",
        ):
            self.assertIn(required, join)

    def test_auto_answer_states_default_and_live_session_requirement(self) -> None:
        auto_answer = self.skill("auto-answer")
        self.assertIn("Auto-answer is off by default", auto_answer)
        self.assertIn("daemon alone does not generate model answers", auto_answer)


if __name__ == "__main__":
    unittest.main()
