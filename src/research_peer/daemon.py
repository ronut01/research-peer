from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import socketserver
import threading
import uuid
from typing import Any

from . import PROTOCOL_VERSION
from .identity import Identity, cert_pem, server_tls_context
from .paths import Paths, load_config
from .protocol import ProtocolError, format_time, utc_now, validate_envelope
from .rooms import _validate_advertised_endpoint
from .store import Store
from .transport import TransportError, deliver_envelope, receive_frame, send_frame, verify_packet


class PeerRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        daemon: "PeerDaemon" = self.server.daemon  # type: ignore[attr-defined]
        connection = None
        try:
            connection = daemon.tls_context.wrap_socket(self.request, server_side=True)
            connection.settimeout(10)
            packet = receive_frame(connection)
            response = daemon.process_packet(packet)
        except ProtocolError as exc:
            daemon.logger.warning(
                "inbound rejected remote=%s code=%s", self.client_address[0], exc.code
            )
            response = {"kind": "error", "code": exc.code, "message": str(exc), "protocol_version": PROTOCOL_VERSION}
        except PermissionError as exc:
            daemon.logger.warning(
                "inbound rejected remote=%s code=AUTH_FAILURE", self.client_address[0]
            )
            response = {"kind": "error", "code": "AUTH_FAILURE", "message": str(exc), "protocol_version": PROTOCOL_VERSION}
        except Exception as exc:
            daemon.logger.warning(
                "inbound failure remote=%s error=%s", self.client_address[0], type(exc).__name__
            )
            response = {"kind": "error", "code": "INTERNAL_ERROR", "message": "request failed", "protocol_version": PROTOCOL_VERSION}
        if connection is not None:
            try:
                send_frame(connection, response)
            except Exception:
                pass
            finally:
                connection.close()


class ThreadingTLSServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class PeerDaemon:
    def __init__(self, paths: Paths, host: str | None = None, port: int | None = None):
        self.paths = paths
        self.paths.ensure_runtime()
        config = load_config(paths)
        self.host = host if host is not None else config["listen_host"]
        self.port = port if port is not None else config["listen_port"]
        self.user_name = config["user_name"]
        self.max_attempts = config["max_attempts"]
        self.identity = Identity.load_or_create(paths, self.user_name)
        self.store = Store(paths.db_file)
        self.tls_context = server_tls_context(self.identity)
        self.stop_event = threading.Event()
        self.logger = logging.getLogger("research-peer")
        self.server: ThreadingTLSServer | None = None

    def process_packet(self, packet: dict[str, Any]) -> dict[str, Any]:
        kind = packet.get("kind")
        if packet.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError("PROTOCOL_MISMATCH", "unsupported protocol version")
        if kind == "probe":
            return {
                "kind": "probe_ack", "protocol_version": PROTOCOL_VERSION,
                "fingerprint": self.identity.fingerprint, "tls_fingerprint": self.identity.tls_fingerprint,
            }
        if kind == "join":
            return self._process_join(packet)
        if kind == "auth_probe":
            return self._process_auth_probe(packet)
        if kind == "message":
            return self._process_message(packet)
        raise ProtocolError("SCHEMA_INVALID", "unknown wire packet kind")

    def _process_auth_probe(self, packet: dict[str, Any]) -> dict[str, Any]:
        room_id = packet.get("room_id")
        fingerprint = packet.get("signer_fingerprint")
        if not isinstance(room_id, str) or not isinstance(fingerprint, str):
            raise ProtocolError("AUTH_FAILURE", "authenticated probe identity is missing")
        peer = self.store.peer_by_fingerprint(room_id, fingerprint)
        if not peer:
            raise ProtocolError("AUTH_FAILURE", "probe sender is not an allowed room member")
        verify_packet(packet, peer["certificate"], fingerprint)
        return {"kind": "auth_probe_ack", "protocol_version": PROTOCOL_VERSION, "authenticated": True}

    def _process_join(self, packet: dict[str, Any]) -> dict[str, Any]:
        room_id = packet.get("room_id")
        token = packet.get("token")
        peer = packet.get("peer")
        if not isinstance(room_id, str) or not isinstance(token, str) or not isinstance(peer, dict):
            raise ProtocolError("SCHEMA_INVALID", "join packet fields are invalid")
        required = {"peer_id", "user_name", "fingerprint", "tls_fingerprint", "certificate", "endpoint"}
        if set(peer) != required or not all(isinstance(peer[key], str) and peer[key] for key in required):
            raise ProtocolError("SCHEMA_INVALID", "join peer fields are invalid")
        try:
            _validate_advertised_endpoint(peer["endpoint"], allow_loopback=True)
        except ValueError as exc:
            raise ProtocolError("SCHEMA_INVALID", str(exc)) from exc
        verify_packet(packet, peer["certificate"], peer["fingerprint"])
        self.store.resolve_room(room_id)
        self.store.consume_invite(token, room_id, format_time(utc_now()))
        self.store.add_peer(
            peer_id=peer["peer_id"], user_name=peer["user_name"], fingerprint=peer["fingerprint"],
            tls_fingerprint=peer["tls_fingerprint"], certificate=peer["certificate"],
            endpoint=peer["endpoint"], room_id=room_id,
        )
        return {
            "kind": "join_accepted", "protocol_version": PROTOCOL_VERSION, "room_id": room_id,
            "peer": {
                "peer_id": str(uuid.uuid5(uuid.NAMESPACE_URL, self.identity.fingerprint)),
                "user_name": self.user_name, "fingerprint": self.identity.fingerprint,
                "tls_fingerprint": self.identity.tls_fingerprint,
                "certificate": cert_pem(self.identity.cert_path),
                "endpoint": f"{self.host}:{self.server.server_address[1] if self.server else self.port}",
            },
        }

    def _process_message(self, packet: dict[str, Any]) -> dict[str, Any]:
        envelope = validate_envelope(packet.get("envelope"))
        fingerprint = packet.get("signer_fingerprint")
        nonce = packet.get("nonce")
        if not isinstance(fingerprint, str) or not isinstance(nonce, str):
            raise ProtocolError("AUTH_FAILURE", "signer identity is missing")
        peer = self.store.peer_by_fingerprint(envelope["room_id"], fingerprint)
        if not peer:
            raise ProtocolError("AUTH_FAILURE", "sender is not an allowed room member")
        verify_packet(packet, peer["certificate"], fingerprint)
        inserted = self.store.receive(envelope, fingerprint, nonce)
        self.logger.info(
            "inbound message room=%s message=%s sender=%s type=%s duplicate=%s",
            envelope["room_id"], envelope["message_id"], fingerprint[:12], envelope["type"], not inserted,
        )
        return {
            "kind": "ack", "protocol_version": PROTOCOL_VERSION,
            "message_id": envelope["message_id"], "duplicate": not inserted,
        }

    def retry_once(self) -> int:
        processed = 0
        for item in self.store.due_outbox():
            processed += 1
            envelope = json.loads(item["envelope_json"])
            try:
                deliver_envelope(self.identity, item, envelope)
                self.store.mark_delivered(item["message_id"])
            except TransportError as exc:
                self.logger.warning(
                    "outbound retry message=%s peer=%s code=%s",
                    item["message_id"], item["peer_id"], exc.code,
                )
                self.store.mark_retry(item["message_id"], exc.code, self.max_attempts)
            except ProtocolError as exc:
                self.logger.warning(
                    "outbound rejected message=%s peer=%s code=%s",
                    item["message_id"], item["peer_id"], exc.code,
                )
                self.store.mark_retry(item["message_id"], exc.code, self.max_attempts)
        return processed

    def serve(self) -> None:
        handler = RotatingFileHandler(self.paths.log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(handler)
        self.server = ThreadingTLSServer((self.host, self.port), PeerRequestHandler)
        self.server.daemon = self  # type: ignore[attr-defined]
        actual_port = self.server.server_address[1]
        self.paths.pid_file.write_text(f"{os.getpid()}\n", encoding="ascii")
        os.chmod(self.paths.pid_file, 0o600)
        ready = self.paths.runtime_dir / "daemon.ready"
        ready.write_text(json.dumps({"host": self.host, "port": actual_port}), encoding="utf-8")
        os.chmod(ready, 0o600)
        retry_thread = threading.Thread(target=self._retry_loop, daemon=True)
        retry_thread.start()

        def stop_handler(_signum: int, _frame: object) -> None:
            self.stop_event.set()
            if self.server:
                threading.Thread(target=self.server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)
        try:
            self.server.serve_forever(poll_interval=0.25)
        finally:
            self.stop_event.set()
            self.server.server_close()
            self.store.close()
            for path in (self.paths.pid_file, ready, self.paths.socket_file):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def _retry_loop(self) -> None:
        while not self.stop_event.wait(0.5):
            try:
                self.retry_once()
            except Exception as exc:
                self.logger.warning("retry worker failure: %s", type(exc).__name__)


def run_daemon(host: str | None = None, port: int | None = None) -> None:
    PeerDaemon(Paths.discover(), host=host, port=port).serve()
