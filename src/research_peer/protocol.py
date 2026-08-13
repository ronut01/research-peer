from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from . import PROTOCOL_VERSION

ALLOWED_TYPES = frozenset({"HANDOFF", "QUESTION", "ANSWER", "ARTIFACT_REF", "STATUS", "ACK"})
MAX_MESSAGE_BYTES = 256 * 1024
MAX_TEXT_CHARS = 200_000
MAX_AUTOMATION_DEPTH = 4
HANDOFF_FIELDS = (
    "purpose", "hypothesis", "repository", "git_remote", "branch", "commit",
    "modified_files", "data", "model", "checkpoint", "commands", "environment",
    "seeds", "hyperparameters", "metrics", "aggregation", "raw_logs",
    "artifacts", "successful_results", "failed_attempts", "confirmed_facts",
    "interpretations", "unverified_assumptions", "remaining_questions",
    "followup_cautions",
)
_RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_META_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ProtocolError("SCHEMA_INVALID", "created_at must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("SCHEMA_INVALID", "created_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ProtocolError("SCHEMA_INVALID", "created_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _uuid(value: Any, field: str, *, required: bool = True) -> str | None:
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str):
        raise ProtocolError("SCHEMA_INVALID", f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ProtocolError("SCHEMA_INVALID", f"{field} must be a UUID string") from exc
    if str(parsed) != value.lower():
        raise ProtocolError("SCHEMA_INVALID", f"{field} must use canonical UUID form")
    return str(parsed)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("SCHEMA_INVALID", "value is not canonical JSON") from exc


def validate_endpoint_party(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ProtocolError("SCHEMA_INVALID", f"{field} must be an object")
    if set(value) - {"user", "session"}:
        raise ProtocolError("SCHEMA_INVALID", f"{field} has unknown fields")
    user = value.get("user")
    session = value.get("session", "")
    if not isinstance(user, str) or not user.strip() or len(user) > 128:
        raise ProtocolError("SCHEMA_INVALID", f"{field}.user is required")
    if not isinstance(session, str) or len(session) > 128:
        raise ProtocolError("SCHEMA_INVALID", f"{field}.session is invalid")
    return {"user": user, "session": session}


def validate_handoff(body: Mapping[str, Any]) -> None:
    missing = [field for field in HANDOFF_FIELDS if field not in body]
    if missing:
        raise ProtocolError("SCHEMA_INVALID", f"HANDOFF missing fields: {', '.join(missing)}")
    list_fields = {
        "modified_files", "commands", "seeds", "metrics", "raw_logs", "artifacts",
        "successful_results", "failed_attempts", "confirmed_facts", "interpretations",
        "unverified_assumptions", "remaining_questions", "followup_cautions",
    }
    for field in list_fields:
        if not isinstance(body[field], list):
            raise ProtocolError("SCHEMA_INVALID", f"HANDOFF.{field} must be an array")
    for artifact in body["artifacts"]:
        if not isinstance(artifact, Mapping) or artifact.get("kind") not in {
            "git_commit", "url", "shared_path", "content_hash", "allowed_file"
        }:
            raise ProtocolError("SCHEMA_INVALID", "HANDOFF artifact reference is invalid")


def validate_envelope(
    envelope: Any,
    *,
    now: datetime | None = None,
    max_bytes: int = MAX_MESSAGE_BYTES,
    clock_skew_seconds: int = 300,
) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise ProtocolError("SCHEMA_INVALID", "envelope must be an object")
    raw_size = len(canonical_json(envelope))
    if raw_size > max_bytes:
        raise ProtocolError("PAYLOAD_TOO_LARGE", f"message exceeds {max_bytes} bytes")
    allowed_fields = {
        "protocol_version", "message_id", "room_id", "type", "from", "to",
        "request_id", "created_at", "reply_required", "owner_attention", "body",
        "sequence", "automation_depth",
    }
    unknown = set(envelope) - allowed_fields
    if unknown:
        raise ProtocolError("SCHEMA_INVALID", f"unknown envelope fields: {', '.join(sorted(unknown))}")
    if envelope.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("PROTOCOL_MISMATCH", "unsupported protocol version")
    message_id = _uuid(envelope.get("message_id"), "message_id")
    room_id = _uuid(envelope.get("room_id"), "room_id")
    message_type = envelope.get("type")
    if message_type not in ALLOWED_TYPES:
        raise ProtocolError("UNKNOWN_MESSAGE_TYPE", "unsupported message type")
    sender = validate_endpoint_party(envelope.get("from"), "from")
    recipient = validate_endpoint_party(envelope.get("to"), "to")
    request_id = _uuid(envelope.get("request_id"), "request_id", required=False)
    if message_type in {"QUESTION", "ANSWER"} and request_id is None:
        raise ProtocolError("SCHEMA_INVALID", f"{message_type} requires request_id")
    created = parse_time(envelope.get("created_at"))
    current = (now or utc_now()).astimezone(timezone.utc)
    if abs((current - created).total_seconds()) > clock_skew_seconds:
        raise ProtocolError("TIMESTAMP_INVALID", "message timestamp outside allowed clock skew")
    if not isinstance(envelope.get("reply_required"), bool) or not isinstance(envelope.get("owner_attention"), bool):
        raise ProtocolError("SCHEMA_INVALID", "reply_required and owner_attention must be booleans")
    body = envelope.get("body")
    if not isinstance(body, Mapping):
        raise ProtocolError("SCHEMA_INVALID", "body must be an object")
    if "text" in body and (not isinstance(body["text"], str) or len(body["text"]) > MAX_TEXT_CHARS):
        raise ProtocolError("SCHEMA_INVALID", "body.text is invalid or too long")
    if message_type in {"QUESTION", "ANSWER", "STATUS"} and not isinstance(body.get("text"), str):
        raise ProtocolError("SCHEMA_INVALID", f"{message_type} requires body.text")
    if message_type == "HANDOFF":
        validate_handoff(body)
    sequence = envelope.get("sequence", 0)
    depth = envelope.get("automation_depth", 0)
    if not isinstance(sequence, int) or sequence < 0:
        raise ProtocolError("SCHEMA_INVALID", "sequence must be a non-negative integer")
    if not isinstance(depth, int) or not 0 <= depth <= MAX_AUTOMATION_DEPTH:
        raise ProtocolError("LOOP_LIMIT", "automation_depth exceeds policy")
    return {
        **dict(envelope), "message_id": message_id, "room_id": room_id,
        "from": sender, "to": recipient, "request_id": request_id,
        "created_at": format_time(created), "sequence": sequence, "automation_depth": depth,
        "body": dict(body),
    }


def new_envelope(
    *, room_id: str, message_type: str, from_user: str, from_session: str,
    to_user: str, to_session: str, body: Mapping[str, Any], request_id: str | None = None,
    reply_required: bool | None = None, owner_attention: bool = False,
    sequence: int = 0, automation_depth: int = 0,
) -> dict[str, Any]:
    if message_type == "QUESTION" and request_id is None:
        request_id = str(uuid.uuid4())
    value = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": str(uuid.uuid4()),
        "room_id": room_id,
        "type": message_type,
        "from": {"user": from_user, "session": from_session},
        "to": {"user": to_user, "session": to_session},
        "request_id": request_id,
        "created_at": format_time(utc_now()),
        "reply_required": message_type == "QUESTION" if reply_required is None else reply_required,
        "owner_attention": owner_attention,
        "body": dict(body),
        "sequence": sequence,
        "automation_depth": automation_depth,
    }
    return validate_envelope(value)


def encode_urlsafe(value: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json(value)).decode("ascii").rstrip("=")


def decode_urlsafe(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        result = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("SCHEMA_INVALID", "invalid encoded value") from exc
    if not isinstance(result, dict):
        raise ProtocolError("SCHEMA_INVALID", "encoded value must contain an object")
    return result


def valid_meta_key(value: str) -> bool:
    return bool(_META_ID_RE.fullmatch(value))

