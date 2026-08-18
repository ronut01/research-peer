from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .daemon import PeerDaemon, run_daemon
from .doctor import run_doctor
from .identity import Identity
from .paths import Paths, atomic_write_json, load_config
from .protocol import ProtocolError, new_envelope
from .rooms import _validate_endpoint, create_invite, decode_invite
from .store import Store
from .transport import TransportError, join_peer

HELP = f"""Research Peer — peer-to-peer research handoff for Claude Code

Version: {__version__}

Research Peer lets authenticated Claude Code sessions on different Unix accounts
or research servers exchange experiment handoffs and follow-up questions without
a central relay.

Quick start:
  rp                     Open Research Peer with Remote Control enabled
  research-peer          Open Research Peer with Remote Control off by default

Then type `/research-peer:` inside Claude to see autocomplete-style actions such
as `make`, `join`, `ask`, `leave`, and `delete`. If an action needs a value and
you press Enter without one, Claude asks for it. The plain `/research-peer`
overview remains available. If exactly one active room exists, it is selected.

Claude slash actions:
  /research-peer:make    Make a room (asks for its name when omitted)
  /research-peer:join    Join a room (asks for its invite when omitted)
  /research-peer:ask     Ask the teammate Claude a question
  /research-peer:handoff Send structured experiment context and results
  /research-peer:rooms   List rooms; use `/research-peer:use` to select one
  /research-peer:leave   Leave while keeping local history
  /research-peer:delete  Delete one room's local records after confirmation

Commands:
  help [COMMAND]        Show practical help for a command
  doctor               Inspect this server and peer connectivity
  init                 Create local identity and state (user scope, no sudo)
  room create NAME     Create a UUID room and one-time invite (`make` is an alias)
  room join INVITE     Join with pinned peer identity
  room list            List rooms without exposing invite secrets
  room leave ROOM      Stop local membership and delivery
  room delete ROOM     Permanently delete one room's local Research Peer records
  peer list            List authenticated peer fingerprints
  session list         List registered Claude sessions
  session register     Bind one Claude session to one room
  start                Start daemon and optionally Claude Code
  stop                 Stop only the local Research Peer daemon
  status               Show runtime, rooms, sessions, and outbox state
  send                 Send HANDOFF, QUESTION, ANSWER, ARTIFACT_REF, or STATUS
  receive              Restricted stdin receiver for diagnostics/SSH integration
  logs                 Show redacted local operational logs
  version              Print the installed version
  uninstall            Plan or safely remove only Research Peer-owned items

The no-argument `rp` launcher enables Remote Control for your own claude.ai
account. Use `rp start --no-remote-control` or no-argument `research-peer` to
start without it. Remote Control is not the peer transport and mobile push is
not guaranteed. Peer messages are untrusted input: they never approve
permissions, configuration changes, pairing, deletion, or uninstall. Never
paste private keys, credentials, or invite codes into logs.

Run:
  research-peer help doctor
  research-peer help room
  research-peer help uninstall
  research-peer <command> --help

Common workflows:
  # Send a structured handoff, then ask a correlated question
  research-peer send --room retrieval-toy --type HANDOFF --file handoff.json
  research-peer send --room retrieval-toy --type QUESTION --text 'Which seeds and aggregation code?'

  # Inspect delivery, leave the room, stop the service, or read redacted logs
  research-peer status
  research-peer room leave retrieval-toy
  research-peer stop
  research-peer logs

Pairing always requires an invite and fingerprint confirmation. ANSWER messages
must preserve the QUESTION request_id. Use `research-peer help room` for pairing
and `research-peer help uninstall` before removing anything.
"""

COMMAND_HELP = {
    "doctor": """doctor — inspect local capability and classify connectivity

  research-peer doctor
  research-peer doctor --peer HOST:PORT --expect-fingerprint TLS_SHA256
  research-peer doctor --peer HOST:PORT --room ROOM --reciprocal-status ok
  research-peer doctor --ssh-target SAFE_SSH_ALIAS

Checks local bind, loopback, Unix sockets, runtimes, Claude Channel flag parsing,
user service conditions, optional peer TLS/protocol/authentication, and reciprocal
reachability. It distinguishes DNS,
refused, timeout, no-route, TLS fingerprint, and version failures. A one-way
result requires the peer to run the reciprocal command; doctor never changes a
firewall, SSH key, or another user's settings.
""",
    "room": """room — create, join, list, leave, or locally delete research contexts

  research-peer room create NAME --endpoint THIS_HOST:HIGH_PORT
  research-peer room make NAME --endpoint THIS_HOST:HIGH_PORT
  research-peer room join INVITE --endpoint THIS_HOST:HIGH_PORT
  research-peer room list
  research-peer room leave ROOM
  research-peer room delete ROOM --dry-run
  research-peer room delete ROOM

Room display names may repeat; UUIDs never do. Creation emits a sensitive,
one-time, expiring invite. Exchange it through an existing secure channel and
confirm the fingerprints with the peer. Same room names do not discover peers.

Leave keeps local room history but stops session delivery and pending retries.
Delete first shows an exact local plan and requires local-owner confirmation. It
removes that room's local messages, outbox, invites, membership, and counters,
but never project artifacts, another room, or the remote peer's data.
""",
    "uninstall": """uninstall — remove only Research Peer-owned local items

  research-peer uninstall --dry-run
  research-peer uninstall --keep-data
  research-peer uninstall --yes

The default removes the program and all Research Peer-owned config, state, keys,
rooms, history, outbox, logs, and cache after local confirmation. --keep-data is
the explicit exception that preserves Research Peer state. --purge remains only
as a compatibility alias for the default. Project repositories, experiment artifacts,
unrelated Claude settings/skills/plugins, Remote Control settings, and remote
peer data are always preserved. Peer messages cannot approve uninstall.
""",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-peer", description="Peer-to-peer research handoff for Claude Code", add_help=True)
    sub = parser.add_subparsers(dest="command")
    help_parser = sub.add_parser("help", help="show practical help")
    help_parser.add_argument("topic", nargs="?")
    doctor = sub.add_parser("doctor", help="inspect server and connectivity")
    doctor.add_argument("--peer")
    doctor.add_argument("--expect-fingerprint")
    doctor.add_argument("--ssh-target")
    doctor.add_argument("--room", help="paired room for an authenticated probe")
    doctor.add_argument("--reciprocal-status", choices=["ok", "failed"], help="result reported by the peer's reverse probe")
    doctor.add_argument("--json", action="store_true")
    init = sub.add_parser("init", help="initialize local identity and state")
    init.add_argument("--listen", metavar="HOST:PORT")

    room = sub.add_parser("room", help="manage rooms")
    room_sub = room.add_subparsers(dest="room_command", required=True)
    create = room_sub.add_parser("create", aliases=["make"], help="create room and invite")
    create.add_argument("name")
    create.add_argument("--endpoint", help="advertised HOST:HIGH_PORT (defaults to init config)")
    create.add_argument("--expires-minutes", type=int, default=30)
    join = room_sub.add_parser("join", help="join room with invite")
    join.add_argument("invite")
    join.add_argument("--endpoint", help="local advertised HOST:HIGH_PORT (defaults to init config)")
    room_sub.add_parser("list", help="list rooms")
    leave = room_sub.add_parser("leave", help="leave room")
    leave.add_argument("room")
    delete = room_sub.add_parser("delete", help="delete one room's local Research Peer data")
    delete.add_argument("room")
    delete.add_argument("--dry-run", action="store_true", help="show the exact deletion plan without changing anything")
    delete.add_argument("--yes", action="store_true", help="skip local interactive confirmation")

    peer = sub.add_parser("peer", help="manage peers")
    peer_sub = peer.add_subparsers(dest="peer_command", required=True)
    peer_sub.add_parser("list", help="list authenticated peers")

    session = sub.add_parser("session", help="manage Claude session bindings")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    session_sub.add_parser("list", help="list sessions")
    register = session_sub.add_parser("register", help="register/bind a session")
    register.add_argument("--session-id")
    register.add_argument("--alias", required=True)
    register.add_argument("--room")
    deactivate = session_sub.add_parser("leave", help="unbind one session")
    deactivate.add_argument("--session-id", required=True)
    poll = session_sub.add_parser("poll", help=argparse.SUPPRESS)
    poll.add_argument("--session-id", required=True)
    poll.add_argument("--json", action="store_true")
    prune = session_sub.add_parser("prune", help="deactivate stale sessions")
    prune.add_argument("--older-than", type=int, default=3600, metavar="SECONDS")

    start = sub.add_parser("start", help="start daemon and Claude")
    start.add_argument("--room")
    start.add_argument("--session-alias", default="research-peer")
    start.add_argument("--session-id")
    start.add_argument("--listen", metavar="HOST:PORT")
    start.add_argument("--daemon-only", action="store_true")
    remote = start.add_mutually_exclusive_group()
    remote.add_argument("--remote-control", action="store_true")
    remote.add_argument("--no-remote-control", action="store_true")
    resume = start.add_mutually_exclusive_group()
    resume.add_argument("--continue", dest="continue_session", action="store_true")
    resume.add_argument("--resume")
    start.add_argument("--print-command", action="store_true")
    sub.add_parser("stop", help="stop local daemon")
    sub.add_parser("status", help="show local status")

    send = sub.add_parser("send", help="send a research message")
    send.add_argument("--room", required=True)
    send.add_argument("--type", required=True, choices=["HANDOFF", "QUESTION", "ANSWER", "ARTIFACT_REF", "STATUS"])
    send.add_argument("--text")
    send.add_argument("--file")
    send.add_argument("--stdin", action="store_true", help="read a JSON body from stdin")
    send.add_argument("--from-session", default="cli")
    send.add_argument("--to-user")
    send.add_argument("--to-session", default="")
    send.add_argument("--request-id")
    send.add_argument("--peer-id")
    send.add_argument("--owner-attention", action="store_true")
    receive = sub.add_parser("receive", help="restricted stdin receiver")
    receive.add_argument("--json", action="store_true")
    logs = sub.add_parser("logs", help="show redacted operational logs")
    logs.add_argument("--lines", type=int, default=100)
    sub.add_parser("version", help="print version")
    uninstall = sub.add_parser("uninstall", help="safely remove Research Peer")
    uninstall.add_argument("--dry-run", action="store_true")
    data_policy = uninstall.add_mutually_exclusive_group()
    data_policy.add_argument("--keep-data", action="store_true")
    uninstall.add_argument("--yes", action="store_true")
    data_policy.add_argument("--purge", action="store_true", help="compatibility alias; full local removal is already the default")
    daemon = sub.add_parser("daemon", help=argparse.SUPPRESS)
    daemon.add_argument("--host")
    daemon.add_argument("--port", type=int)
    sub.add_parser("channel", help=argparse.SUPPRESS)
    return parser


def _runtime() -> tuple[Paths, dict[str, Any], Identity, Store]:
    paths = Paths.discover()
    paths.ensure_runtime()
    config = load_config(paths)
    identity = Identity.load_or_create(paths, config["user_name"])
    return paths, config, identity, Store(paths.db_file)


def _init(listen: str | None = None) -> dict[str, Any]:
    paths = Paths.discover()
    paths.ensure_runtime()
    config = load_config(paths)
    if listen:
        host, port = _validate_endpoint(listen)
        config["listen_host"], config["listen_port"] = host, port
    atomic_write_json(paths.config_file, config)
    identity = Identity.load_or_create(paths, config["user_name"])
    store = Store(paths.db_file)
    store.close()
    return {"initialized": True, "fingerprint": identity.fingerprint, "tls_fingerprint": identity.tls_fingerprint, "config": str(paths.config_file)}


def _split_endpoint(value: str | None, config: dict[str, Any]) -> tuple[str, int]:
    if value:
        host, port = value.rsplit(":", 1)
        return host, int(port)
    return config["listen_host"], config["listen_port"]


def _start_daemon(paths: Paths, host: str, port: int) -> dict[str, Any]:
    if paths.pid_file.exists():
        try:
            pid = int(paths.pid_file.read_text().strip())
            os.kill(pid, 0)
            return {"running": True, "pid": pid, "already_running": True}
        except (ValueError, ProcessLookupError, PermissionError):
            paths.pid_file.unlink(missing_ok=True)
    paths.log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_handle = paths.log_file.open("ab")
    command = [sys.executable, "-m", "research_peer", "daemon", "--host", host, "--port", str(port)]
    service_file = paths.home / ".config/systemd/user/research-peer.service"
    testing = os.environ.get("RESEARCH_PEER_TESTING") == "1"
    if service_file.exists() and not testing and subprocess.run(["systemctl", "--user", "is-system-running"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        result = subprocess.run(["systemctl", "--user", "start", "research-peer.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"systemd user service failed: {result.stderr.strip()}")
        process = None
        launch_mode = "systemd-user"
    elif shutil.which("tmux") and not testing:
        subprocess.run(["tmux", "kill-session", "-t", "research-peer-daemon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = subprocess.run(["tmux", "new-session", "-d", "-s", "research-peer-daemon", *command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"tmux daemon launch failed: {result.stderr.strip()}")
        process = None
        launch_mode = "tmux"
    else:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log_handle, stderr=log_handle, start_new_session=True)
        launch_mode = "detached-process"
    log_handle.close()
    ready = paths.runtime_dir / "daemon.ready"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ready.exists():
            pid = int(paths.pid_file.read_text().strip()) if paths.pid_file.exists() else (process.pid if process else None)
            return {"running": True, "pid": pid, "ready": json.loads(ready.read_text()), "launch_mode": launch_mode}
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"daemon exited with status {process.returncode}; run research-peer logs")
        time.sleep(0.1)
    if process is not None:
        process.terminate()
    raise RuntimeError("daemon did not become ready")


def build_claude_command(remote_control: bool = False, continue_session: bool = False, resume: str | None = None) -> list[str]:
    command = ["claude", "--dangerously-load-development-channels", "plugin:research-peer@skills-dir"]
    if remote_control:
        command.append("--remote-control")
    if continue_session:
        command.append("--continue")
    if resume:
        command.extend(["--resume", resume])
    return command


def _stop(paths: Paths) -> dict[str, Any]:
    if not paths.pid_file.exists():
        return {"stopped": True, "already_stopped": True}
    try:
        pid = int(paths.pid_file.read_text().strip())
    except (ValueError, FileNotFoundError):
        paths.pid_file.unlink(missing_ok=True)
        return {"stopped": True, "stale_pid": True}
    service_file = paths.home / ".config/systemd/user/research-peer.service"
    service_stopped = False
    if service_file.exists() and os.environ.get("RESEARCH_PEER_TESTING") != "1":
        service_stopped = subprocess.run(["systemctl", "--user", "stop", "research-peer.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        cmdline = cmdline_path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except FileNotFoundError:
        paths.pid_file.unlink(missing_ok=True)
        (paths.runtime_dir / "daemon.ready").unlink(missing_ok=True)
        return {"stopped": True, "pid": pid, "launch_mode": "systemd-user"} if service_stopped else {"stopped": True, "stale_pid": True}
    if "research_peer" not in cmdline and "research-peer" not in cmdline:
        raise RuntimeError("PID file does not refer to Research Peer; refusing to signal it")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    paths.pid_file.unlink(missing_ok=True)
    (paths.runtime_dir / "daemon.ready").unlink(missing_ok=True)
    paths.socket_file.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            if argv is None and sys.stdin.isatty():
                return main(["start"])
            print(HELP)
            return 0
        if args.command == "help":
            topic = getattr(args, "topic", None)
            print(COMMAND_HELP.get(topic, HELP if not topic else f"No dedicated help for {topic}.\n\n{HELP}"))
            return 0
        if args.command == "version":
            print(__version__)
            return 0
        if args.command == "doctor":
            result = run_doctor(args.peer, args.expect_fingerprint, args.ssh_target, args.room, args.reciprocal_status)
            _print(result)
            local_ok = all(item["status"] == "pass" for item in result["local_connectivity"] if item["name"] in {"local_bind", "loopback", "unix_socket"})
            peer_ok = not args.peer or result["peer"]["status"] == "pass"
            auth_ok = not args.room or result["peer_authentication"]["status"] == "pass"
            return 0 if local_ok and peer_ok and auth_ok else 1
        if args.command == "init":
            _print(_init(args.listen))
            return 0
        if args.command == "daemon":
            run_daemon(args.host, args.port)
            return 0
        if args.command == "channel":
            node = shutil.which("node")
            script = Paths.discover().data_dir / "app/channel/research-peer-channel.mjs"
            if node is None:
                raise RuntimeError("Node.js is required for the Research Peer Claude Channel")
            if not script.is_file():
                raise RuntimeError("Research Peer Channel runtime is not installed; run install.sh")
            os.execv(node, [node, str(script)])
        if args.command == "uninstall":
            from .installer import uninstall

            paths = Paths.discover()
            purge = not args.keep_data
            return uninstall(
                paths,
                dry_run=args.dry_run,
                keep_data=args.keep_data,
                assume_yes=args.yes,
                purge=purge,
            )
        paths, config, identity, store = _runtime()
        try:
            if args.command == "room":
                if args.room_command in {"create", "make"}:
                    endpoint = args.endpoint or f"{config['listen_host']}:{config['listen_port']}"
                    if config["listen_port"] == 0 and not args.endpoint:
                        raise ValueError("no advertised endpoint configured; run research-peer init --listen HOST:HIGH_PORT or pass --endpoint")
                    code, invite = create_invite(store, identity, display_name=args.name, endpoint=endpoint, expires_minutes=args.expires_minutes)
                    _print({"room_id": invite["room_id"], "display_name": invite["display_name"], "fingerprint": invite["fingerprint"], "expires_at": invite["expires_at"], "invite": code, "warning": "Invite is a one-time secret; do not log or commit it."})
                elif args.room_command == "join":
                    invite = decode_invite(args.invite)
                    endpoint = args.endpoint or f"{config['listen_host']}:{config['listen_port']}"
                    if config["listen_port"] == 0 and not args.endpoint:
                        raise ValueError("no local endpoint configured; run research-peer init --listen HOST:HIGH_PORT or pass --endpoint")
                    response = join_peer(identity, invite, user_name=config["user_name"], receive_endpoint=endpoint)
                    store.create_room(invite["room_id"], invite["display_name"])
                    peer = response["peer"]
                    store.add_peer(room_id=invite["room_id"], **peer)
                    _print({"joined": True, "room_id": invite["room_id"], "peer_fingerprint": peer["fingerprint"], "confirm_fingerprint": True})
                elif args.room_command == "list":
                    _print(store.list_rooms())
                elif args.room_command == "leave":
                    room = store.resolve_room(args.room)
                    cancelled = store.leave_room(room["room_id"])
                    _print({"left": room["room_id"], "pending_retries_cancelled": cancelled, "history_preserved": True, "remote_data_removed": False})
                elif args.room_command == "delete":
                    room = store.resolve_room(args.room, active_only=False)
                    plan = store.room_delete_plan(room["room_id"])
                    _print({"action": "delete_local_room", **plan})
                    if args.dry_run:
                        return 0
                    if not args.yes:
                        if not sys.stdin.isatty():
                            raise ValueError("refusing non-interactive room deletion without --yes")
                        answer = input(
                            f"Type DELETE {room['display_name']} to permanently delete this local room: "
                        ).strip()
                        if answer != f"DELETE {room['display_name']}":
                            print("Room deletion cancelled.")
                            return 1
                    _print(store.delete_room(room["room_id"]))
                return 0
            if args.command == "peer" and args.peer_command == "list":
                _print(store.list_peers())
                return 0
            if args.command == "session":
                if args.session_command == "list":
                    _print(store.list_sessions())
                elif args.session_command == "register":
                    session_id = args.session_id or str(uuid.uuid4())
                    room_id = store.resolve_room(args.room)["room_id"] if args.room else None
                    store.register_session(session_id, args.alias, config["user_name"], room_id)
                    _print({"session_id": session_id, "alias": args.alias, "room_id": room_id})
                elif args.session_command == "leave":
                    store.deactivate_session(args.session_id)
                    _print({"session_id": args.session_id, "active": False})
                elif args.session_command == "poll":
                    _print(store.poll_session(args.session_id))
                elif args.session_command == "prune":
                    _print({"deactivated": store.prune_stale_sessions(args.older_than)})
                return 0
            if args.command == "start":
                host, port = _split_endpoint(args.listen, config)
                if args.listen:
                    config["listen_host"], config["listen_port"] = host, port
                    atomic_write_json(paths.config_file, config)
                daemon_status = _start_daemon(paths, host, port)
                session_id = args.session_id or str(uuid.uuid4())
                selected_room = args.room
                if selected_room is None:
                    active_rooms = [room for room in store.list_rooms() if room["status"] == "active"]
                    if len(active_rooms) == 1:
                        selected_room = active_rooms[0]["room_id"]
                if selected_room:
                    room_id = store.resolve_room(selected_room)["room_id"]
                    store.register_session(session_id, args.session_alias, config["user_name"], room_id)
                command = build_claude_command(args.remote_control, args.continue_session, args.resume)
                if args.daemon_only or args.print_command:
                    _print({"daemon": daemon_status, "session_id": session_id if selected_room else None, "room": selected_room, "claude_command": command})
                    return 0
                if not sys.stdin.isatty():
                    _print({"daemon": daemon_status, "claude_command": command, "note": "Claude not launched because stdin is not a TTY"})
                    return 0
                os.environ["RESEARCH_PEER_SESSION_ID"] = session_id
                os.environ["RESEARCH_PEER_SESSION_ALIAS"] = args.session_alias
                os.execvp(command[0], command)
            if args.command == "stop":
                _print(_stop(paths))
                return 0
            if args.command == "status":
                result = store.status()
                result["daemon"] = {"pid_file": str(paths.pid_file), "running": paths.pid_file.exists()}
                result["identity_fingerprint"] = identity.fingerprint
                _print(result)
                return 0
            if args.command == "send":
                room = store.resolve_room(args.room)
                peers = store.peers_for_room(room["room_id"])
                if args.peer_id:
                    peers = [peer for peer in peers if peer["peer_id"] == args.peer_id]
                if len(peers) != 1:
                    raise ValueError("send requires exactly one room peer; pass --peer-id when ambiguous")
                peer = peers[0]
                if args.file:
                    body = json.loads(Path(args.file).read_text(encoding="utf-8"))
                elif args.stdin:
                    body = json.load(sys.stdin)
                else:
                    body = {"text": args.text or ""}
                sequence = store.next_sequence(room["room_id"], peer["peer_id"])
                envelope = new_envelope(
                    room_id=room["room_id"], message_type=args.type,
                    from_user=config["user_name"], from_session=args.from_session,
                    to_user=args.to_user or peer["user_name"], to_session=args.to_session,
                    body=body, request_id=args.request_id, owner_attention=args.owner_attention,
                    sequence=sequence,
                )
                store.enqueue(envelope, peer["peer_id"])
                worker = PeerDaemon(paths)
                try:
                    worker.retry_once()
                finally:
                    worker.store.close()
                state = store.connection.execute("SELECT state,last_error FROM outbox WHERE message_id=?", (envelope["message_id"],)).fetchone()
                _print({"message_id": envelope["message_id"], "request_id": envelope["request_id"], "delivery": dict(state)})
                return 0
            if args.command == "receive":
                packet = json.load(sys.stdin)
                daemon = PeerDaemon(paths)
                try:
                    response = daemon.process_packet(packet)
                finally:
                    daemon.store.close()
                _print(response)
                return 0
            if args.command == "logs":
                if not paths.log_file.exists():
                    print("No Research Peer logs.")
                    return 0
                lines = paths.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines:]
                for line in lines:
                    print(_redact(line))
                return 0
        finally:
            store.close()
    except (ValueError, LookupError, RuntimeError, PermissionError, ProtocolError, TransportError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "ERROR")
        print(f"research-peer: {code}: {exc}", file=sys.stderr)
        return 2
    return 0


def rp_main(argv: Sequence[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if not effective_argv and sys.stdin.isatty():
        return main(["start", "--remote-control"])
    return main(effective_argv)


def _redact(text: str) -> str:
    for marker in ("rp1_", "token=", "Authorization:", "ANTHROPIC_API_KEY="):
        index = text.find(marker)
        if index >= 0:
            text = text[:index] + marker.split("=")[0] + "[REDACTED]"
    return text
