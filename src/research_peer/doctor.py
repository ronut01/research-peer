from __future__ import annotations

import errno
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION, __version__
from .identity import client_tls_context, fingerprint_peer_der
from .paths import Paths, load_config


def _command(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def classify_socket_error(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "DNS_FAILURE"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(exc, ConnectionRefusedError):
        return "CONNECTION_REFUSED"
    if isinstance(exc, OSError):
        if exc.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
            return "NO_ROUTE"
        if exc.errno == errno.ECONNREFUSED:
            return "CONNECTION_REFUSED"
    return "CONNECTION_ERROR"


def local_network_checks() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        results.append({"name": "local_bind", "status": "fail", "code": classify_socket_error(exc), "detail": str(exc)})
        server = None
    if server is None:
        return results + _unix_socket_check()
    server.settimeout(3)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        results.append({"name": "local_bind", "status": "pass", "code": "LOCAL_BIND_OK", "address": "127.0.0.1", "port": port})
        accepted: list[bytes] = []

        def accept_once() -> None:
            connection, _ = server.accept()
            with connection:
                accepted.append(connection.recv(16))
                connection.sendall(b"pong")

        thread = threading.Thread(target=accept_once, daemon=True)
        thread.start()
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            client.sendall(b"ping")
            response = client.recv(16)
        thread.join(timeout=3)
        if accepted == [b"ping"] and response == b"pong":
            results.append({"name": "loopback", "status": "pass", "code": "LOOPBACK_OK"})
        else:
            results.append({"name": "loopback", "status": "fail", "code": "LOOPBACK_FAILED"})
    except OSError as exc:
        results.append({"name": "local_bind", "status": "fail", "code": classify_socket_error(exc), "detail": str(exc)})
    finally:
        server.close()

    results.extend(_unix_socket_check())
    return results


def _unix_socket_check() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if hasattr(socket, "AF_UNIX"):
        with tempfile.TemporaryDirectory(prefix="research-peer-doctor-") as temp:
            path = str(Path(temp) / "doctor.sock")
            try:
                unix_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            except OSError as exc:
                return [{"name": "unix_socket", "status": "fail", "code": classify_socket_error(exc), "detail": str(exc)}]
            try:
                unix_server.bind(path)
                unix_server.listen(1)
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(path)
                connection, _ = unix_server.accept()
                client.sendall(b"ok")
                ok = connection.recv(2) == b"ok"
                connection.close()
                client.close()
                results.append({"name": "unix_socket", "status": "pass" if ok else "fail", "code": "UNIX_SOCKET_OK" if ok else "UNIX_SOCKET_FAILED"})
            except OSError as exc:
                results.append({"name": "unix_socket", "status": "fail", "code": classify_socket_error(exc), "detail": str(exc)})
            finally:
                unix_server.close()
    return results


def peer_check(endpoint: str, expected_tls_fingerprint: str | None = None, timeout: float = 4.0) -> dict[str, Any]:
    try:
        host, port_text = endpoint.rsplit(":", 1)
        port = int(port_text)
        raw = socket.create_connection((host.strip("[]"), port), timeout=timeout)
        with raw:
            with client_tls_context().wrap_socket(raw, server_hostname=host) as connection:
                certificate = connection.getpeercert(binary_form=True)
                actual = fingerprint_peer_der(certificate)
                if expected_tls_fingerprint and actual != expected_tls_fingerprint:
                    return {"status": "fail", "code": "FINGERPRINT_MISMATCH", "actual_tls_fingerprint": actual}
                payload = json.dumps({"kind": "probe", "protocol_version": PROTOCOL_VERSION}).encode()
                connection.sendall(len(payload).to_bytes(4, "big") + payload)
                header = _recv_exact(connection, 4)
                length = int.from_bytes(header, "big")
                if length > 65536:
                    return {"status": "fail", "code": "PROTOCOL_MISMATCH"}
                response = json.loads(_recv_exact(connection, length))
                if response.get("protocol_version") != PROTOCOL_VERSION:
                    return {"status": "fail", "code": "PROTOCOL_MISMATCH", "peer_version": response.get("protocol_version")}
                if response.get("kind") != "probe_ack":
                    return {"status": "fail", "code": "PROTOCOL_MISMATCH"}
                return {"status": "pass", "code": "PEER_OK", "tls_fingerprint": actual, "bidirectional": "not_tested"}
    except ssl.SSLError as exc:
        return {"status": "fail", "code": "TLS_FAILURE", "detail": str(exc)}
    except (OSError, ValueError, json.JSONDecodeError, EOFError) as exc:
        code = classify_socket_error(exc)
        result = {"status": "fail", "code": code, "detail": str(exc)}
        if code in {"TIMEOUT", "NO_ROUTE"}:
            result["possible_cause"] = "firewall_or_routing"
        if code == "CONNECTION_REFUSED":
            result["possible_cause"] = "peer_daemon_not_running_or_wrong_port"
        return result


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise EOFError("connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _ssh_target_check(target: str | None) -> dict[str, Any]:
    installed = bool(shutil.which("ssh"))
    result: dict[str, Any] = {"installed": installed, "code": "SSH_AVAILABLE" if installed else "SSH_UNAVAILABLE"}
    if not target or not installed:
        result["connection"] = "not_tested"
        return result
    code, output = _command([
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=yes", target, "true",
    ], timeout=8)
    if code == 0:
        result.update({"connection": "pass", "code": "SSH_TRANSPORT_AVAILABLE"})
    else:
        lowered = output.lower()
        if "permission denied" in lowered:
            failure = "AUTH_FAILURE"
        elif "resolve hostname" in lowered or "name or service not known" in lowered:
            failure = "DNS_FAILURE"
        elif "timed out" in lowered:
            failure = "TIMEOUT"
        elif "no route" in lowered:
            failure = "NO_ROUTE"
        else:
            failure = "SSH_CONNECTION_FAILED"
        result.update({"connection": "fail", "code": failure, "detail": output[-500:]})
    return result


def configured_listener_check() -> dict[str, Any]:
    paths = Paths.discover()
    config = load_config(paths)
    configured = f"{config['listen_host']}:{config['listen_port']}"
    ready_path = paths.runtime_dir / "daemon.ready"
    running = False
    if paths.pid_file.exists():
        try:
            pid = int(paths.pid_file.read_text().strip())
            os.kill(pid, 0)
            running = True
        except (OSError, ValueError):
            pass
    if not running:
        return {
            "name": "configured_listener", "status": "not_tested",
            "code": "DAEMON_NOT_RUNNING", "configured": configured,
        }
    try:
        ready = json.loads(ready_path.read_text())
        actual = f"{ready['host']}:{ready['port']}"
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "name": "configured_listener", "status": "fail",
            "code": "LISTENER_STATE_UNKNOWN", "configured": configured,
        }
    if actual != configured:
        return {
            "name": "configured_listener", "status": "fail",
            "code": "LISTENER_CONFIG_MISMATCH", "configured": configured,
            "actual": actual,
            "remediation": "run research-peer stop && research-peer start",
        }
    return {
        "name": "configured_listener", "status": "pass",
        "code": "LISTENER_CONFIG_OK", "configured": configured, "actual": actual,
    }


def firewall_heuristic() -> dict[str, Any]:
    ufw_enabled = False
    default_drop = False
    try:
        ufw_enabled = "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text(errors="replace")
    except OSError:
        pass
    try:
        default_drop = 'DEFAULT_INPUT_POLICY="DROP"' in Path("/etc/default/ufw").read_text(errors="replace")
    except OSError:
        pass
    active_services = []
    for service in ("ufw", "firewalld", "nftables"):
        code, _ = _command(["systemctl", "is-active", "--quiet", service], timeout=2)
        if code == 0:
            active_services.append(service)
    likely_blocked = ufw_enabled and default_drop
    result: dict[str, Any] = {
        "status": "warn" if likely_blocked else "pass",
        "code": "INBOUND_DEFAULT_DROP_LIKELY" if likely_blocked else "NO_DEFAULT_DROP_DETECTED",
        "ufw_enabled": ufw_enabled,
        "default_input_drop": default_drop,
        "active_services": active_services,
    }
    if likely_blocked:
        result["remediation"] = (
            "use an already-authorized port or an owner-approved SSH tunnel; "
            "for restricted keys use an explicit reverse bind: "
            "-R 127.0.0.1:REMOTE_PORT:127.0.0.1:LOCAL_PORT"
        )
    return result


def _authenticated_check(peer_endpoint: str, room_value: str) -> dict[str, Any]:
    try:
        from .identity import Identity
        from .paths import Paths, load_config
        from .store import Store
        from .transport import deliver, signed_auth_probe

        paths = Paths.discover()
        config = load_config(paths)
        identity = Identity.load_or_create(paths, config["user_name"])
        store = Store(paths.db_file)
        try:
            room = store.resolve_room(room_value)
            matches = [item for item in store.peers_for_room(room["room_id"]) if item["endpoint"] == peer_endpoint]
            if len(matches) != 1:
                return {"status": "not_tested", "code": "AUTH_PEER_NOT_UNAMBIGUOUS"}
            peer = matches[0]
            response = deliver(peer["endpoint"], peer["tls_fingerprint"], signed_auth_probe(identity, room["room_id"]))
            if response.get("kind") == "auth_probe_ack" and response.get("authenticated") is True:
                return {"status": "pass", "code": "AUTHENTICATION_OK"}
            return {"status": "fail", "code": "PROTOCOL_MISMATCH"}
        finally:
            store.close()
    except Exception as exc:
        return {"status": "fail", "code": getattr(exc, "code", "AUTH_FAILURE"), "detail": str(exc)}


def run_doctor(
    peer: str | None = None, expected_tls_fingerprint: str | None = None,
    ssh_target: str | None = None, room: str | None = None,
    reciprocal_status: str | None = None,
) -> dict[str, Any]:
    systemd_code, systemd_output = _command(["systemctl", "--user", "is-system-running"])
    linger_code, linger_output = _command(["loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value"])
    claude_path = shutil.which("claude")
    claude_version = _command([claude_path, "--version"])[1] if claude_path else None
    channel_parse = False
    if claude_path:
        channel_parse = _command([claude_path, "--dangerously-load-development-channels", "server:research-peer-doctor", "--version"])[0] == 0
    ssh = _ssh_target_check(ssh_target)
    peer_result = peer_check(peer, expected_tls_fingerprint) if peer else {"status": "not_tested", "reason": "no peer endpoint provided"}
    auth_result = _authenticated_check(peer, room) if peer and room else {"status": "not_tested", "reason": "pass --peer and --room after pairing"}
    if peer_result.get("status") == "pass" and reciprocal_status == "failed":
        bilateral = {"status": "fail", "code": "ONE_WAY_ONLY"}
    elif peer_result.get("status") == "pass" and reciprocal_status == "ok":
        bilateral = {"status": "pass", "code": "BIDIRECTIONAL_OK"}
    else:
        bilateral = {"status": "not_tested", "reason": "requires --reciprocal-status from the peer result"}
    assessment = None
    if peer_result.get("code") in {"TIMEOUT", "NO_ROUTE"} and ssh.get("connection") == "pass":
        assessment = "DIRECT_TCP_BLOCKED_SSH_AVAILABLE"
    result: dict[str, Any] = {
        "research_peer_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "system": {
            "os": platform.platform(), "architecture": platform.machine(), "hostname": socket.gethostname(),
            "user": os.environ.get("USER", str(os.getuid())), "home": os.environ.get("HOME"),
            "python": platform.python_version(), "git": _command(["git", "--version"])[1],
            "node": _command(["node", "--version"])[1], "bun": shutil.which("bun"),
            "tmux": _command(["tmux", "-V"])[1] if shutil.which("tmux") else None,
            "screen": shutil.which("screen"),
        },
        "service": {
            "systemd_user": "available" if systemd_code == 0 else "unavailable",
            "systemd_detail": systemd_output,
            "linger": linger_output if linger_code == 0 else "unknown",
        },
        "claude": {
            "installed": bool(claude_path), "path": claude_path, "version": claude_version,
            "development_channel_flag": channel_parse,
            "channel_policy": "requires_session_start_verification",
            "remote_control_eligibility": "run claude doctor / opt-in session to verify",
        },
        "ssh": ssh,
        "local_connectivity": [*local_network_checks(), configured_listener_check()],
        "firewall": firewall_heuristic(),
        "peer": peer_result,
        "peer_authentication": auth_result,
        "transport_assessment": assessment,
        "bidirectional": bilateral,
    }
    return result
