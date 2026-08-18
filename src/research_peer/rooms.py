from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Any

from . import PROTOCOL_VERSION
from .identity import Identity, cert_pem
from .protocol import ProtocolError, decode_urlsafe, encode_urlsafe, format_time, parse_time, utc_now
from .store import Store

INVITE_PREFIX = "rp1_"
DEFAULT_INVITE_MINUTES = 24 * 60


def create_invite(
    store: Store, identity: Identity, *, display_name: str, endpoint: str,
    expires_minutes: int = DEFAULT_INVITE_MINUTES, allow_loopback: bool = False,
) -> tuple[str, dict[str, Any]]:
    if not display_name.strip() or len(display_name) > 128:
        raise ValueError("room display name must be 1-128 characters")
    _validate_advertised_endpoint(endpoint, allow_loopback=allow_loopback)
    if not 1 <= expires_minutes <= 7 * 24 * 60:
        raise ValueError("invite expiry must be between 1 minute and 7 days")
    room_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    expires_at = format_time(utc_now() + timedelta(minutes=expires_minutes))
    store.create_room(room_id, display_name.strip())
    store.add_invite(token, room_id, expires_at)
    invite = {
        "protocol_version": PROTOCOL_VERSION,
        "room_id": room_id,
        "display_name": display_name.strip(),
        "endpoint": endpoint,
        "transport": "tcp-tls",
        "fingerprint": identity.fingerprint,
        "tls_fingerprint": identity.tls_fingerprint,
        "certificate": cert_pem(identity.cert_path),
        "expires_at": expires_at,
        "token": token,
    }
    return INVITE_PREFIX + encode_urlsafe(invite), invite


def decode_invite(code: str) -> dict[str, Any]:
    if not code.startswith(INVITE_PREFIX):
        raise ProtocolError("SCHEMA_INVALID", "invite prefix is invalid")
    invite = decode_urlsafe(code[len(INVITE_PREFIX):])
    required = {
        "protocol_version", "room_id", "display_name", "endpoint", "transport",
        "fingerprint", "tls_fingerprint", "certificate", "expires_at", "token",
    }
    if set(invite) != required:
        raise ProtocolError("SCHEMA_INVALID", "invite fields are invalid")
    if invite["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("PROTOCOL_MISMATCH", "invite protocol version is unsupported")
    try:
        uuid.UUID(invite["room_id"])
    except (ValueError, TypeError) as exc:
        raise ProtocolError("SCHEMA_INVALID", "invite room_id is invalid") from exc
    if invite["transport"] != "tcp-tls":
        raise ProtocolError("SCHEMA_INVALID", "invite transport is unsupported")
    _validate_advertised_endpoint(invite["endpoint"], allow_loopback=True)
    if parse_time(invite["expires_at"]) <= utc_now():
        raise ProtocolError("INVITE_EXPIRED", "invite has expired")
    for field in ("display_name", "fingerprint", "tls_fingerprint", "certificate", "token"):
        if not isinstance(invite[field], str) or not invite[field]:
            raise ProtocolError("SCHEMA_INVALID", f"invite {field} is invalid")
    return invite


def _validate_endpoint(endpoint: str) -> tuple[str, int]:
    if not isinstance(endpoint, str) or ":" not in endpoint:
        raise ValueError("endpoint must be HOST:PORT")
    host, port_text = endpoint.rsplit(":", 1)
    host = host.strip("[]")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("endpoint port is invalid") from exc
    if not host or not 1024 < port <= 65535:
        raise ValueError("endpoint must use a host and high port (1025-65535)")
    return host, port


def _validate_advertised_endpoint(endpoint: str, *, allow_loopback: bool = False) -> tuple[str, int]:
    host, port = _validate_endpoint(endpoint)
    normalized = host.lower()
    if normalized in {"0.0.0.0", "::"}:
        raise ValueError("advertised endpoint cannot be a wildcard address; use a reachable interface address")
    if normalized in {"127.0.0.1", "::1", "localhost"} and not allow_loopback:
        raise ValueError(
            "advertised endpoint is loopback-only; pass --advertise-loopback only when an SSH tunnel makes it reachable"
        )
    return host, port
