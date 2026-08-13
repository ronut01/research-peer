from __future__ import annotations

import contextlib
import io
import unittest

from research_peer.cli import build_claude_command, main


class HelpLauncherTests(unittest.TestCase):
    def capture(self, args: list[str]) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(args)
        return code, output.getvalue()

    def test_main_help_has_required_actions_and_security(self) -> None:
        code, output = self.capture(["help"])
        self.assertEqual(0, code)
        for phrase in ("Quick start", "/research-peer:make", "room create", "room delete", "Remote Control", "handoff", "uninstall", "untrusted", "logs"):
            self.assertIn(phrase, output)

    def test_subcommand_help(self) -> None:
        for topic in ("doctor", "room", "uninstall"):
            code, output = self.capture(["help", topic])
            self.assertEqual(0, code)
            self.assertIn(topic, output.lower())

    def test_remote_control_is_opt_in_and_resume_is_explicit(self) -> None:
        default = build_claude_command()
        self.assertNotIn("--remote-control", default)
        self.assertNotIn("--continue", default)
        enabled = build_claude_command(remote_control=True, continue_session=True)
        self.assertIn("--remote-control", enabled)
        self.assertIn("--continue", enabled)
        resumed = build_claude_command(resume="abc")
        self.assertEqual(["--resume", "abc"], resumed[-2:])

    def test_noninteractive_no_args_still_shows_help(self) -> None:
        code, output = self.capture([])
        self.assertEqual(0, code)
        self.assertIn("research-peer          Open Claude Code", output)

    def test_room_make_alias_and_delete_flags_parse(self) -> None:
        from research_peer.cli import build_parser

        make = build_parser().parse_args(["room", "make", "toy"])
        self.assertEqual("make", make.room_command)
        delete = build_parser().parse_args(["room", "delete", "toy", "--dry-run"])
        self.assertTrue(delete.dry_run)
        self.assertFalse(delete.yes)


if __name__ == "__main__":
    unittest.main()
