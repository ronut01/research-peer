from __future__ import annotations

import json
import os
import socket
import ssl
import uuid
from typing import Any

from . import PROTOCOL_VERSION
from .doctor import classify_socket_error
from .identity import Identity, cert_pem, client_tls_context, fingerprint_peer_der, verify
from .protocol import ProtocolError, canonical_json, validate_envelope
from .rooms import _validate_endpoint

MAX_FRAME_BYTES = 256 * 1024


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def recv_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise EOFError("connection closed before frame completed")
        result.extend(chunk)
    return bytes(result)


def send_frame(connection: socket.socket, value: dict[str, Any], *, max_bytes: int = MAX_FRAME_BYTES) -> None:
    body = canonical_json(value)
    if len(body) > max_bytes:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "wire frame exceeds maximum size")
    connection.sendall(len(body).to_bytes(4, "big") + body)


def receive_frame(connection: socket.socket, *, max_bytes: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    length = int.from_bytes(recv_exact(connection, 4), "big")
    if length <= 0 or length > max_bytes:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "wire frame length is invalid")
    try:
        value = json.loads(recv_exact(connection, length))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("SCHEMA_INVALID", "wire frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("SCHEMA_INVALID", "wire frame must contain an object")
    return value


def signature_payload(packet: dict[str, Any]) -> bytes:
    return canonical_json({key: packet[key] for key in sorted(packet) if key != "signature"})


def signed_message(identity: Identity, envelope: dict[str, Any]) -> dict[str, Any]:
    packet = {
        "kind": "message", "protocol_version": PROTOCOL_VERSION,
        "envelope": envelope, "signer_fingerprint": identity.fingerprint,
        "nonce": os.urandom(24).hex(),
    }
    packet["signature"] = identity.sign(signature_payload(packet))
    return packet


def signed_auth_probe(identity: Identity, room_id: str) -> dict[str, Any]:
    packet = {
        "kind": "auth_probe", "protocol_version": PROTOCOL_VERSION, "room_id": room_id,
        "signer_fingerprint": identity.fingerprint, "nonce": os.urandom(24).hex(),
    }
    packet["signature"] = identity.sign(signature_payload(packet))
    return packet


def signed_join(
    identity: Identity, *, invite: dict[str, Any], user_name: str,
    receive_endpoint: str, peer_id: str,
) -> dict[str, Any]:
    _validate_endpoint(receive_endpoint)
    packet = {
        "kind": "join", "protocol_version": PROTOCOL_VERSION,
        "room_id": invite["room_id"], "token": invite["token"],
        "peer": {
            "peer_id": peer_id, "user_name": user_name, "fingerprint": identity.fingerprint,
            "tls_fingerprint": identity.tls_fingerprint, "certificate": cert_pem(identity.cert_path),
            "endpoint": receive_endpoint,
        },
        "nonce": os.urandom(24).hex(),
    }
    packet["signature"] = identity.sign(signature_payload(packet))
    return packet


def verify_packet(packet: dict[str, Any], certificate: str, expected_fingerprint: str) -> None:
    if packet.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("PROTOCOL_MISMATCH", "wire protocol version mismatch")
    signature = packet.get("signature")
    nonce = packet.get("nonce")
    if not isinstance(signature, str) or not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
        raise ProtocolError("AUTH_FAILURE", "packet signature or nonce is missing")
    verify(certificate, signature_payload(packet), signature, expected_fingerprint)


def _connect(endpoint: str, expected_tls_fingerprint: str, timeout: float) -> ssl.SSLSocket:
    host, port = _validate_endpoint(endpoint)
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
        connection = client_tls_context().wrap_socket(raw, server_hostname=host)
        actual = fingerprint_peer_der(connection.getpeercert(binary_form=True))
        if actual != expected_tls_fingerprint:
            connection.close()
            raise TransportError("FINGERPRINT_MISMATCH", "TLS certificate fingerprint mismatch")
        connection.settimeout(timeout)
        return connection
    except TransportError:
        raise
    except (OSError, ssl.SSLError) as exc:
        code = classify_socket_error(exc)
        if isinstance(exc, ssl.SSLError):
            code = "TLS_FAILURE"
        raise TransportError(code, str(exc), retryable=code in {"DNS_FAILURE", "CONNECTION_REFUSED", "TIMEOUT", "NO_ROUTE", "CONNECTION_ERROR"}) from exc


def deliver(endpoint: str, tls_fingerprint: str, packet: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    try:
        with _connect(endpoint, tls_fingerprint, timeout) as connection:
            send_frame(connection, packet)
            response = receive_frame(connection)
    except ProtocolError as exc:
        raise TransportError(exc.code, str(exc)) from exc
    if response.get("kind") == "error":
        code = response.get("code", "PROTOCOL_ERROR")
        raise TransportError(code, response.get("message", code), retryable=False)
    return response


def join_peer(
    identity: Identity, invite: dict[str, Any], *, user_name: str,
    receive_endpoint: str, peer_id: str | None = None,
) -> dict[str, Any]:
    peer_id = peer_id or str(uuid.uuid5(uuid.NAMESPACE_URL, identity.fingerprint))
    packet = signed_join(identity, invite=invite, user_name=user_name, receive_endpoint=receive_endpoint, peer_id=peer_id)
    response = deliver(invite["endpoint"], invite["tls_fingerprint"], packet)
    if response.get("kind") != "join_accepted" or response.get("room_id") != invite["room_id"]:
        raise TransportError("PROTOCOL_MISMATCH", "join response is invalid")
    return response


def deliver_envelope(identity: Identity, peer: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    validated = validate_envelope(envelope)
    response = deliver(peer["endpoint"], peer["tls_fingerprint"], signed_message(identity, validated))
    if response.get("kind") != "ack" or response.get("message_id") != envelope["message_id"]:
        raise TransportError("PROTOCOL_MISMATCH", "message ACK is invalid")
    return response
