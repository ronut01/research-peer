#!/usr/bin/env python3
"""Prepare or clean two isolated Research Peer states for interactive Claude acceptance.

This script never copies Claude credentials. Both Claude processes keep the real HOME for
their own login/plugin discovery while RESEARCH_PEER_HOME isolates Research Peer state.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def env_for(path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "RESEARCH_PEER_HOME": str(path),
        "XDG_RUNTIME_DIR": str(path / "run"),
        "RESEARCH_PEER_TESTING": "1",
    }


def cli(env: dict[str, str], *args: str, stdin: str | None = None) -> dict:
    result = subprocess.run(
        ["research-peer", *args], env=env, input=stdin, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return json.loads(result.stdout)


def prepare(info_path: Path) -> None:
    root = Path(tempfile.mkdtemp(prefix="research-peer-two-claudes-"))
    a, b = root / "a", root / "b"
    a.mkdir()
    b.mkdir()
    env_a, env_b = env_for(a), env_for(b)
    port_a, port_b = free_port(), free_port()
    cli(env_a, "init", "--listen", f"127.0.0.1:{port_a}")
    cli(env_b, "init", "--listen", f"127.0.0.1:{port_b}")
    cli(env_a, "start", "--daemon-only", "--listen", f"127.0.0.1:{port_a}")
    cli(env_b, "start", "--daemon-only", "--listen", f"127.0.0.1:{port_b}")
    created = cli(env_a, "room", "create", "claude-live", "--endpoint", f"127.0.0.1:{port_a}")
    cli(env_b, "room", "join", created["invite"], "--endpoint", f"127.0.0.1:{port_b}")
    session_a, session_b = str(uuid.uuid4()), str(uuid.uuid4())
    cli(env_a, "session", "register", "--session-id", session_a, "--alias", "claude-a", "--room", created["room_id"])
    cli(env_b, "session", "register", "--session-id", session_b, "--alias", "claude-b", "--room", created["room_id"])
    info = {
        "root": str(root), "a": str(a), "b": str(b), "port_a": port_a, "port_b": port_b,
        "room_id": created["room_id"], "session_a": session_a, "session_b": session_b,
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    os.chmod(info_path, 0o600)
    print(json.dumps(info, indent=2))


def cleanup(info_path: Path) -> None:
    if not info_path.exists():
        return
    info = json.loads(info_path.read_text())
    for key in ("a", "b"):
        subprocess.run(["research-peer", "stop"], env=env_for(Path(info[key])), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    root = Path(info["root"])
    if root.name.startswith("research-peer-two-claudes-") and root.parent == Path("/tmp") and not root.is_symlink():
        shutil.rmtree(root)
    info_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "cleanup"])
    parser.add_argument("--info", type=Path, default=Path("/tmp/research-peer-two-claudes.json"))
    args = parser.parse_args()
    cleanup(args.info)
    if args.action == "prepare":
        prepare(args.info)


if __name__ == "__main__":
    main()
