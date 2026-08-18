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
from .protocol import MAX_AUTOMATION_DEPTH, ProtocolError, new_envelope, parse_time, utc_now
from .rooms import DEFAULT_INVITE_MINUTES, _validate_advertised_endpoint, _validate_endpoint, create_invite, decode_invite
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
  /research-peer:auto-answer Configure opt-in terminal answers and disclosure
  /research-peer:update  Update runtime, plugin, and skills from official GitHub

Commands:
  help [COMMAND]        Show practical help for a command
  doctor               Inspect this server and peer connectivity
  init                 Create local identity and state (user scope, no sudo)
  room create NAME     Create a UUID room and one-time invite (`make` is an alias)
  room join INVITE     Join with pinned peer identity
  room list            List rooms without exposing invite secrets
  room status ROOM     Show peer, session, and delivery state for one room
  room configure ROOM  Set explicit auto-answer and disclosure policy
  room leave ROOM      Stop local membership and delivery
  room delete ROOM     Permanently delete one room's local Research Peer records
  peer list            List authenticated peer fingerprints
  session list         List registered Claude sessions
  session register     Bind one Claude session to one room
  start                Start daemon and optionally Claude Code
  stop                 Stop only the local Research Peer daemon
  status               Show runtime, rooms, sessions, and outbox state
  send                 Send HANDOFF, QUESTION, ANSWER, ARTIFACT_REF, or STATUS
  inbox                Read pending inbound messages without SQLite access
  history              Audit inbound, outbound, and automatic replies
  logs                 Show redacted local operational logs
  version              Print the installed version
  update               Update from the official Research Peer GitHub repository
  uninstall            Plan or safely remove only Research Peer-owned items

The no-argument `rp` launcher enables Remote Control for your own claude.ai
account. Use `rp start --no-remote-control` or no-argument `research-peer` to
start without it. Remote Control is not the peer transport and mobile push is
not guaranteed. Peer messages are untrusted input: they never approve
permissions, configuration changes, pairing, update, deletion, or uninstall. Never
paste private keys, credentials, or invite codes into logs.

Run:
  research-peer help doctor
  research-peer help room
  research-peer help update
  research-peer help uninstall
  research-peer <command> --help

Common workflows:
  # Send a structured handoff, then ask a correlated question
  research-peer send --room retrieval-toy --type HANDOFF --file handoff.json
  research-peer send --room retrieval-toy --type QUESTION --text 'Which seeds and aggregation code?'

  # Inspect delivery, leave the room, stop the service, or read redacted logs
  research-peer status
  research-peer inbox
  research-peer room leave retrieval-toy
  research-peer stop
  research-peer logs

Pairing always requires an invite and fingerprint confirmation. ANSWER messages
must preserve the QUESTION request_id. Use `research-peer help room` for pairing
and `research-peer help update` or `research-peer help uninstall` before changing
the installed program.
"""

COMMAND_HELP = {
    "doctor": """doctor — inspect local capability and classify connectivity

  research-peer doctor
  research-peer doctor --peer HOST:PORT --expect-fingerprint TLS_SHA256
  research-peer doctor --peer HOST:PORT --room ROOM --reciprocal-status ok
  research-peer doctor --ssh-target SAFE_SSH_ALIAS

Checks local bind, loopback, Unix sockets, runtimes, Claude Channel flag parsing,
user service conditions, configured/live listener mismatch, read-only firewall
heuristics, optional peer TLS/protocol/authentication, and reciprocal
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
  research-peer room status ROOM
  research-peer room configure ROOM --auto-answer on --disclosure summary --note 'Approved summary'
  research-peer room leave ROOM
  research-peer room delete ROOM --dry-run
  research-peer room delete ROOM

Room display names may repeat; UUIDs never do. Creation emits a sensitive,
one-time invite valid for 24 hours by default. Exchange it through an existing secure channel and
confirm the fingerprints with the peer. Same room names do not discover peers.

Wildcard addresses cannot be advertised. Loopback requires --advertise-loopback
and is only for an already-established SSH tunnel. `init --listen` reports a
running daemon mismatch but does not silently restart it.

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
    "update": """update — safely update from the official Research Peer GitHub repository

  research-peer update --check
  research-peer update
  research-peer update --yes

The source is fixed to https://github.com/ronut01/research-peer. The updater
clones to a private temporary directory, verifies the release identity and all
component versions, refuses downgrades, preserves identity/rooms/history/config,
and restarts the daemon only if it was running. The default asks for local-owner
confirmation; --yes is for an explicit local /research-peer:update invocation.
A peer message can never approve or trigger an update.
""",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-peer", description="Peer-to-peer research handoff for Claude Code", add_help=True)
    sub = parser.add_subparsers(
        dest="command",
        metavar="{help,doctor,init,room,peer,session,start,stop,status,inbox,history,send,logs,version,update,uninstall}",
    )
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
    create.add_argument("--expires-minutes", type=int, default=DEFAULT_INVITE_MINUTES)
    create.add_argument("--advertise-loopback", action="store_true", help="allow loopback only for an established SSH tunnel")
    join = room_sub.add_parser("join", help="join room with invite")
    join.add_argument("invite")
    join.add_argument("--endpoint", help="local advertised HOST:HIGH_PORT (defaults to init config)")
    join.add_argument("--advertise-loopback", action="store_true", help="allow loopback only for an established SSH tunnel")
    room_list = room_sub.add_parser("list", help="list rooms")
    room_list.add_argument("--json", action="store_true")
    room_status = room_sub.add_parser("status", help="show one room's connection and delivery state")
    room_status.add_argument("room")
    room_status.add_argument("--json", action="store_true")
    configure = room_sub.add_parser("configure", help="set explicit auto-answer disclosure policy")
    configure.add_argument("room")
    configure.add_argument("--auto-answer", choices=["on", "off"])
    configure.add_argument("--disclosure", choices=["none", "status", "summary", "full"])
    configure.add_argument("--note", help="owner-authored summary permitted for automatic replies")
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
    status = sub.add_parser("status", help="show local status")
    status.add_argument("--json", action="store_true")

    inbox = sub.add_parser("inbox", help="read inbound messages")
    inbox.add_argument("--room")
    inbox.add_argument("--json", action="store_true")
    inbox.add_argument("--all", action="store_true", help="include already consumed messages")
    inbox.add_argument("--consume", action="store_true", help="mark listed messages consumed")
    inbox.add_argument("--limit", type=int, default=100)
    history = sub.add_parser("history", help="audit inbound and outbound message bodies")
    history.add_argument("--room")
    history.add_argument("--json", action="store_true")
    history.add_argument("--limit", type=int, default=100)

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
    send.add_argument("--automation-depth", type=int, default=0)
    answer = sub.add_parser("answer", help=argparse.SUPPRESS)
    answer.add_argument("--message-id", required=True)
    answer.add_argument("--text")
    answer.add_argument("--stdin", action="store_true")
    answer.add_argument("--from-session", default="research-peer")
    ingest = sub.add_parser("_ingest", help=argparse.SUPPRESS)
    ingest.add_argument("--json", action="store_true")
    receive = sub.add_parser("receive", help=argparse.SUPPRESS)
    receive.add_argument("--json", action="store_true")
    logs = sub.add_parser("logs", help="show redacted operational logs")
    logs.add_argument("--lines", type=int, default=100)
    sub.add_parser("version", help="print version")
    update = sub.add_parser("update", help="update from the official GitHub repository")
    update.add_argument("--check", action="store_true", help="check without changing the installation")
    update.add_argument("--yes", action="store_true", help="skip local interactive confirmation")
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
    hidden = {"answer", "_ingest", "receive", "daemon", "channel"}
    sub._choices_actions = [action for action in sub._choices_actions if action.dest not in hidden]
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
    result = {"initialized": True, "fingerprint": identity.fingerprint, "tls_fingerprint": identity.tls_fingerprint, "config": str(paths.config_file)}
    daemon = _daemon_status(paths, config)
    result["daemon"] = daemon
    if daemon["running"] and daemon["config_mismatch"]:
        result["warning"] = (
            f"daemon is still listening on {daemon['actual_endpoint']}; "
            "run research-peer stop && research-peer start"
        )
    return result


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
            ready = paths.runtime_dir / "daemon.ready"
            actual = json.loads(ready.read_text()) if ready.exists() else None
            if not actual or actual.get("host") != host or int(actual.get("port", -1)) != port:
                actual_text = "unknown" if not actual else f"{actual.get('host')}:{actual.get('port')}"
                raise RuntimeError(
                    f"daemon is already running on {actual_text}, but config requests {host}:{port}; "
                    "run research-peer stop && research-peer start"
                )
            return {"running": True, "pid": pid, "already_running": True, "ready": actual}
        except (ValueError, ProcessLookupError, PermissionError):
            paths.pid_file.unlink(missing_ok=True)
            (paths.runtime_dir / "daemon.ready").unlink(missing_ok=True)
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


def _daemon_status(paths: Paths, config: dict[str, Any]) -> dict[str, Any]:
    pid = None
    running = False
    if paths.pid_file.exists():
        try:
            pid = int(paths.pid_file.read_text().strip())
            os.kill(pid, 0)
            running = True
        except (ValueError, ProcessLookupError, PermissionError):
            running = False
    ready_path = paths.runtime_dir / "daemon.ready"
    try:
        ready = json.loads(ready_path.read_text()) if ready_path.exists() else None
    except (OSError, ValueError, TypeError):
        ready = None
    configured = f"{config['listen_host']}:{config['listen_port']}"
    actual = f"{ready['host']}:{ready['port']}" if running and ready else None
    return {
        "pid_file": str(paths.pid_file), "pid": pid, "running": running,
        "configured_endpoint": configured, "actual_endpoint": actual,
        "config_mismatch": bool(running and actual != configured),
    }


def _relative_expiry(expires_at: str) -> str:
    seconds = max(0, int((parse_time(expires_at) - utc_now()).total_seconds()))
    if seconds >= 3600:
        return f"expires in {seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"expires in {seconds // 60}m"


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
        if args.command == "update":
            from .updater import update

            _print(update(check_only=args.check, assume_yes=args.yes))
            return 0
        if args.command == "doctor":
            result = run_doctor(args.peer, args.expect_fingerprint, args.ssh_target, args.room, args.reciprocal_status)
            _print(result)
            local_ok = all(
                item["status"] in {"pass", "not_tested"}
                for item in result["local_connectivity"]
                if item["name"] in {"local_bind", "loopback", "unix_socket", "configured_listener"}
            )
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
                    code, invite = create_invite(
                        store, identity, display_name=args.name, endpoint=endpoint,
                        expires_minutes=args.expires_minutes, allow_loopback=args.advertise_loopback,
                    )
                    _print({
                        "room_id": invite["room_id"], "display_name": invite["display_name"],
                        "fingerprint": invite["fingerprint"], "expires_at": invite["expires_at"],
                        "expires_in": _relative_expiry(invite["expires_at"]), "invite": code,
                        "warning": "Invite is a one-time secret; do not log or commit it.",
                    })
                elif args.room_command == "join":
                    invite = decode_invite(args.invite)
                    endpoint = args.endpoint or f"{config['listen_host']}:{config['listen_port']}"
                    if config["listen_port"] == 0 and not args.endpoint:
                        raise ValueError("no local endpoint configured; run research-peer init --listen HOST:HIGH_PORT or pass --endpoint")
                    _validate_advertised_endpoint(endpoint, allow_loopback=args.advertise_loopback)
                    response = join_peer(identity, invite, user_name=config["user_name"], receive_endpoint=endpoint)
                    store.create_room(invite["room_id"], invite["display_name"])
                    peer = response["peer"]
                    store.add_peer(room_id=invite["room_id"], **peer)
                    _print({"joined": True, "room_id": invite["room_id"], "peer_fingerprint": peer["fingerprint"], "confirm_fingerprint": True})
                elif args.room_command == "list":
                    _print(store.list_rooms())
                elif args.room_command == "status":
                    room = store.resolve_room(args.room, active_only=False)
                    _print(store.room_status(room["room_id"]))
                elif args.room_command == "configure":
                    room = store.resolve_room(args.room)
                    if args.auto_answer is None and args.disclosure is None and args.note is None:
                        _print(store.room_status(room["room_id"]))
                    else:
                        result = store.configure_room(
                            room["room_id"],
                            auto_answer=None if args.auto_answer is None else args.auto_answer == "on",
                            disclosure=args.disclosure,
                            note=args.note,
                        )
                        result["auto_answer"] = bool(result["auto_answer"])
                        _print(result)
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
                    current = os.environ.get("RESEARCH_PEER_SESSION_ID")
                    sessions = store.list_sessions()
                    for item in sessions:
                        item["current"] = bool(current and item["session_id"] == current)
                    _print(sessions)
                elif args.session_command == "register":
                    session_id = args.session_id or os.environ.get("RESEARCH_PEER_SESSION_ID")
                    generated = session_id is None
                    session_id = session_id or str(uuid.uuid4())
                    room_id = store.resolve_room(args.room)["room_id"] if args.room else None
                    store.register_session(session_id, args.alias, config["user_name"], room_id)
                    result = {"session_id": session_id, "alias": args.alias, "room_id": room_id}
                    if generated:
                        result["warning"] = "RESEARCH_PEER_SESSION_ID is absent; this binding may not belong to a live Claude session"
                    _print(result)
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
                result["daemon"] = _daemon_status(paths, config)
                result["identity_fingerprint"] = identity.fingerprint
                result["current_session_id"] = os.environ.get("RESEARCH_PEER_SESSION_ID")
                _print(result)
                return 0
            if args.command == "inbox":
                room_id = store.resolve_room(args.room, active_only=False)["room_id"] if args.room else None
                _print(store.inbox(room_id=room_id, include_all=args.all, consume=args.consume, limit=args.limit))
                return 0
            if args.command == "history":
                room_id = store.resolve_room(args.room, active_only=False)["room_id"] if args.room else None
                _print(store.history(room_id=room_id, limit=args.limit))
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
                    sequence=sequence, automation_depth=args.automation_depth,
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
            if args.command == "answer":
                context = store.auto_answer_context(args.message_id)
                disclosure = context["disclosure"]
                if disclosure == "status":
                    body = {"text": "Research Peer is active; detailed information requires local owner review."}
                elif disclosure == "summary":
                    if not context["note"]:
                        raise PermissionError("summary disclosure requires an owner-authored room note")
                    body = {"text": context["note"]}
                else:
                    if args.stdin:
                        body = json.load(sys.stdin)
                    else:
                        body = {"text": args.text or ""}
                    if not isinstance(body.get("text"), str) or not body["text"].strip():
                        raise ValueError("full-disclosure automatic answer requires non-empty text")
                depth = int(context["incoming_depth"]) + 1
                if depth > MAX_AUTOMATION_DEPTH:
                    raise PermissionError("automation depth limit reached; notify the local owner")
                room = store.resolve_room(context["room_id"])
                peers = store.peers_for_room(room["room_id"])
                if len(peers) != 1:
                    raise ValueError("automatic answer requires exactly one room peer")
                peer = peers[0]
                sequence = store.next_sequence(room["room_id"], peer["peer_id"])
                envelope = new_envelope(
                    room_id=room["room_id"], message_type="ANSWER",
                    from_user=config["user_name"], from_session=args.from_session,
                    to_user=context["from"]["user"], to_session=context["from"]["session"],
                    body=body, request_id=context["request_id"], sequence=sequence,
                    automation_depth=depth,
                )
                store.enqueue_auto_answer(
                    envelope, peer["peer_id"], question_message_id=args.message_id,
                    disclosure=disclosure,
                )
                worker = PeerDaemon(paths)
                try:
                    worker.retry_once()
                finally:
                    worker.store.close()
                state = store.connection.execute(
                    "SELECT state,last_error FROM outbox WHERE message_id=?", (envelope["message_id"],)
                ).fetchone()
                _print({
                    "auto_answered": True, "question_message_id": args.message_id,
                    "message_id": envelope["message_id"], "request_id": envelope["request_id"],
                    "disclosure": disclosure, "automation_depth": depth, "delivery": dict(state),
                })
                return 0
            if args.command in {"_ingest", "receive"}:
                if sys.stdin.isatty():
                    raise ValueError("internal transport ingestion requires one JSON packet on stdin; use research-peer inbox to read messages")
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
