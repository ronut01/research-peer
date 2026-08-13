from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

from .protocol import canonical_json, format_time, utc_now

SCHEMA_VERSION = 1


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._migrate()
        try:
            path.chmod(0o600)
        except FileNotFoundError:
            pass

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def _migrate(self) -> None:
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rooms (
          room_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS rooms_name ON rooms(display_name);
        CREATE TABLE IF NOT EXISTS peers (
          peer_id TEXT PRIMARY KEY, user_name TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
          tls_fingerprint TEXT NOT NULL, certificate TEXT NOT NULL, endpoint TEXT NOT NULL,
          transport TEXT NOT NULL, allowed INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS room_peers (
          room_id TEXT NOT NULL REFERENCES rooms(room_id),
          peer_id TEXT NOT NULL REFERENCES peers(peer_id),
          PRIMARY KEY(room_id, peer_id)
        );
        CREATE TABLE IF NOT EXISTS invites (
          token_hash TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(room_id),
          expires_at TEXT NOT NULL, consumed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY, alias TEXT NOT NULL, user_name TEXT NOT NULL,
          room_id TEXT REFERENCES rooms(room_id), active INTEGER NOT NULL DEFAULT 1,
          last_seen TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS session_alias ON sessions(alias);
        CREATE TABLE IF NOT EXISTS messages (
          message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, direction TEXT NOT NULL,
          envelope_json TEXT NOT NULL, state TEXT NOT NULL, received_at TEXT NOT NULL,
          consumed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS outbox (
          message_id TEXT PRIMARY KEY REFERENCES messages(message_id),
          peer_id TEXT NOT NULL REFERENCES peers(peer_id), attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at REAL NOT NULL, last_error TEXT, state TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS replay_nonces (
          fingerprint TEXT NOT NULL, nonce TEXT NOT NULL, seen_at REAL NOT NULL,
          PRIMARY KEY(fingerprint, nonce)
        );
        CREATE TABLE IF NOT EXISTS rate_events (
          fingerprint TEXT NOT NULL, seen_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS rate_events_peer_time ON rate_events(fingerprint,seen_at);
        CREATE TABLE IF NOT EXISTS request_counts (
          room_id TEXT NOT NULL, request_id TEXT NOT NULL, count INTEGER NOT NULL,
          PRIMARY KEY(room_id,request_id)
        );
        CREATE TABLE IF NOT EXISTS sequence_counters (
          room_id TEXT NOT NULL, peer_id TEXT NOT NULL, value INTEGER NOT NULL,
          PRIMARY KEY(room_id, peer_id)
        );
        """)
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def create_room(self, room_id: str, display_name: str) -> None:
        self.connection.execute(
            "INSERT INTO rooms(room_id,display_name,status,created_at) VALUES(?,?,?,?)",
            (room_id, display_name, "active", format_time(utc_now())),
        )

    def resolve_room(self, value: str, *, active_only: bool = True) -> sqlite3.Row:
        status_clause = " AND status='active'" if active_only else ""
        rows = self.connection.execute(
            f"SELECT * FROM rooms WHERE (room_id=? OR display_name=?){status_clause} ORDER BY created_at",
            (value, value),
        ).fetchall()
        if not rows:
            raise LookupError(f"room not found: {value}")
        exact_id = [row for row in rows if row["room_id"] == value]
        if exact_id:
            return exact_id[0]
        if len(rows) > 1:
            raise LookupError(f"room name is ambiguous: {value}; use room_id")
        return rows[0]

    def list_rooms(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM rooms ORDER BY created_at")]

    def leave_room(self, room_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE rooms SET status='left' WHERE room_id=?", (room_id,))
            connection.execute("UPDATE sessions SET active=0, room_id=NULL WHERE room_id=?", (room_id,))

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(("research-peer-invite-v1\0" + token).encode()).hexdigest()

    def add_invite(self, token: str, room_id: str, expires_at: str) -> None:
        self.connection.execute(
            "INSERT INTO invites(token_hash,room_id,expires_at) VALUES(?,?,?)",
            (self.token_hash(token), room_id, expires_at),
        )

    def consume_invite(self, token: str, room_id: str, now_text: str) -> None:
        digest = self.token_hash(token)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM invites WHERE token_hash=? AND room_id=?", (digest, room_id)).fetchone()
            if not row or row["consumed_at"] is not None or row["expires_at"] < now_text:
                raise PermissionError("invite is invalid, expired, or already used")
            connection.execute("UPDATE invites SET consumed_at=? WHERE token_hash=?", (now_text, digest))

    def add_peer(
        self, *, peer_id: str, user_name: str, fingerprint: str, tls_fingerprint: str,
        certificate: str, endpoint: str, room_id: str,
    ) -> None:
        now = format_time(utc_now())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO peers(peer_id,user_name,fingerprint,tls_fingerprint,certificate,endpoint,transport,allowed,created_at)
                VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(peer_id) DO UPDATE SET
                user_name=excluded.user_name,fingerprint=excluded.fingerprint,tls_fingerprint=excluded.tls_fingerprint,
                certificate=excluded.certificate,endpoint=excluded.endpoint,transport=excluded.transport,allowed=1""",
                (peer_id, user_name, fingerprint, tls_fingerprint, certificate, endpoint, "tcp-tls", now),
            )
            connection.execute("INSERT OR IGNORE INTO room_peers(room_id,peer_id) VALUES(?,?)", (room_id, peer_id))

    def peers_for_room(self, room_id: str) -> list[dict[str, Any]]:
        query = """SELECT p.* FROM peers p JOIN room_peers rp ON p.peer_id=rp.peer_id
                   WHERE rp.room_id=? AND p.allowed=1 ORDER BY p.created_at"""
        return [dict(row) for row in self.connection.execute(query, (room_id,))]

    def list_peers(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT peer_id,user_name,fingerprint,tls_fingerprint,endpoint,transport,allowed,created_at FROM peers ORDER BY created_at"
        )]

    def peer_by_fingerprint(self, room_id: str, fingerprint: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT p.* FROM peers p JOIN room_peers rp ON p.peer_id=rp.peer_id
               JOIN rooms r ON r.room_id=rp.room_id
               WHERE rp.room_id=? AND p.fingerprint=? AND p.allowed=1 AND r.status='active'""",
            (room_id, fingerprint),
        ).fetchone()
        return dict(row) if row else None

    def next_sequence(self, room_id: str, peer_id: str) -> int:
        with self.transaction() as connection:
            row = connection.execute("SELECT value FROM sequence_counters WHERE room_id=? AND peer_id=?", (room_id, peer_id)).fetchone()
            value = 1 if row is None else int(row["value"]) + 1
            connection.execute(
                "INSERT INTO sequence_counters(room_id,peer_id,value) VALUES(?,?,?) ON CONFLICT(room_id,peer_id) DO UPDATE SET value=excluded.value",
                (room_id, peer_id, value),
            )
            return value

    def enqueue(self, envelope: dict[str, Any], peer_id: str) -> None:
        now = format_time(utc_now())
        payload = canonical_json(envelope).decode("utf-8")
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO messages(message_id,room_id,direction,envelope_json,state,received_at) VALUES(?,?,?,?,?,?)",
                (envelope["message_id"], envelope["room_id"], "out", payload, "pending", now),
            )
            connection.execute(
                "INSERT INTO outbox(message_id,peer_id,attempts,next_attempt_at,state) VALUES(?,?,0,?,'pending')",
                (envelope["message_id"], peer_id, time.time()),
            )

    def due_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        query = """SELECT o.*,m.envelope_json,p.endpoint,p.fingerprint,p.tls_fingerprint,p.certificate
                   FROM outbox o JOIN messages m ON m.message_id=o.message_id
                   JOIN peers p ON p.peer_id=o.peer_id
                   WHERE o.state='pending' AND o.next_attempt_at<=? ORDER BY o.next_attempt_at LIMIT ?"""
        return [dict(row) for row in self.connection.execute(query, (time.time(), limit))]

    def mark_delivered(self, message_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE outbox SET state='delivered',last_error=NULL WHERE message_id=?", (message_id,))
            connection.execute("UPDATE messages SET state='delivered' WHERE message_id=?", (message_id,))

    def mark_retry(self, message_id: str, error_code: str, max_attempts: int = 12) -> None:
        row = self.connection.execute("SELECT attempts FROM outbox WHERE message_id=?", (message_id,)).fetchone()
        attempts = int(row["attempts"]) + 1
        permanent = error_code in {"AUTH_FAILURE", "FINGERPRINT_MISMATCH", "PROTOCOL_MISMATCH", "SCHEMA_INVALID"}
        state = "failed" if permanent or attempts >= max_attempts else "pending"
        jitter = (int(hashlib.sha256(message_id.encode()).hexdigest()[:4], 16) % 1000) / 1000
        delay = min(300.0, 2 ** min(attempts - 1, 8)) + jitter
        with self.transaction() as connection:
            connection.execute(
                "UPDATE outbox SET attempts=?,next_attempt_at=?,last_error=?,state=? WHERE message_id=?",
                (attempts, time.time() + delay, error_code, state, message_id),
            )
            connection.execute("UPDATE messages SET state=? WHERE message_id=?", (state, message_id))

    def receive(self, envelope: dict[str, Any], fingerprint: str, nonce: str) -> bool:
        now_text = format_time(utc_now())
        payload = canonical_json(envelope).decode("utf-8")
        with self.transaction() as connection:
            if connection.execute("SELECT 1 FROM replay_nonces WHERE fingerprint=? AND nonce=?", (fingerprint, nonce)).fetchone():
                existing = connection.execute("SELECT 1 FROM messages WHERE message_id=?", (envelope["message_id"],)).fetchone()
                if existing:
                    return False
                raise PermissionError("nonce replay detected")
            connection.execute("INSERT INTO replay_nonces(fingerprint,nonce,seen_at) VALUES(?,?,?)", (fingerprint, nonce, time.time()))
            existing = connection.execute("SELECT 1 FROM messages WHERE message_id=?", (envelope["message_id"],)).fetchone()
            if existing:
                return False
            now_epoch = time.time()
            connection.execute("DELETE FROM rate_events WHERE seen_at<?", (now_epoch - 60,))
            minute_count = connection.execute("SELECT COUNT(*) FROM rate_events WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
            burst_count = connection.execute("SELECT COUNT(*) FROM rate_events WHERE fingerprint=? AND seen_at>=?", (fingerprint, now_epoch - 10)).fetchone()[0]
            if minute_count >= 30 or burst_count >= 10:
                raise PermissionError("peer rate limit exceeded")
            connection.execute("INSERT INTO rate_events(fingerprint,seen_at) VALUES(?,?)", (fingerprint, now_epoch))
            request_id = envelope.get("request_id")
            if request_id and envelope.get("type") in {"QUESTION", "ANSWER"}:
                request_row = connection.execute("SELECT count FROM request_counts WHERE room_id=? AND request_id=?", (envelope["room_id"], request_id)).fetchone()
                count = 1 if request_row is None else int(request_row["count"]) + 1
                if count > 4:
                    raise PermissionError("request loop limit exceeded")
                connection.execute(
                    "INSERT INTO request_counts(room_id,request_id,count) VALUES(?,?,?) ON CONFLICT(room_id,request_id) DO UPDATE SET count=excluded.count",
                    (envelope["room_id"], request_id, count),
                )
            target = envelope["to"]["session"]
            cutoff = format_time(utc_now() - timedelta(seconds=300))
            if target:
                target_count = connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE room_id=? AND alias=? AND active=1 AND last_seen>=?",
                    (envelope["room_id"], target, cutoff),
                ).fetchone()[0]
            else:
                target_count = connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE room_id=? AND active=1 AND last_seen>=?",
                    (envelope["room_id"], cutoff),
                ).fetchone()[0]
            message_state = "received" if target_count == 1 else "no_target_session"
            connection.execute(
                "INSERT INTO messages(message_id,room_id,direction,envelope_json,state,received_at) VALUES(?,?,?,?,?,?)",
                (envelope["message_id"], envelope["room_id"], "in", payload, message_state, now_text),
            )
            return True

    def register_session(self, session_id: str, alias: str, user_name: str, room_id: str | None) -> None:
        self.connection.execute(
            """INSERT INTO sessions(session_id,alias,user_name,room_id,active,last_seen) VALUES(?,?,?,?,1,?)
               ON CONFLICT(session_id) DO UPDATE SET alias=excluded.alias,user_name=excluded.user_name,
               room_id=excluded.room_id,active=1,last_seen=excluded.last_seen""",
            (session_id, alias, user_name, room_id, format_time(utc_now())),
        )

    def deactivate_session(self, session_id: str) -> None:
        self.connection.execute("UPDATE sessions SET active=0,room_id=NULL,last_seen=? WHERE session_id=?", (format_time(utc_now()), session_id))

    def list_sessions(self) -> list[dict[str, Any]]:
        now = utc_now()
        values = []
        for row in self.connection.execute("SELECT * FROM sessions ORDER BY last_seen DESC"):
            item = dict(row)
            try:
                from .protocol import parse_time
                item["stale"] = (now - parse_time(item["last_seen"])).total_seconds() > 300
            except Exception:
                item["stale"] = True
            values.append(item)
        return values

    def prune_stale_sessions(self, older_than_seconds: int = 3600) -> int:
        cutoff = format_time(utc_now() - timedelta(seconds=older_than_seconds))
        cursor = self.connection.execute("UPDATE sessions SET active=0,room_id=NULL WHERE active=1 AND last_seen<?", (cutoff,))
        return cursor.rowcount

    def poll_session(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        session = self.connection.execute("SELECT * FROM sessions WHERE session_id=? AND active=1", (session_id,)).fetchone()
        if not session or not session["room_id"]:
            return []
        self.connection.execute("UPDATE sessions SET last_seen=? WHERE session_id=?", (format_time(utc_now()), session_id))
        cutoff = format_time(utc_now() - timedelta(seconds=300))
        active_count = self.connection.execute("SELECT COUNT(*) FROM sessions WHERE room_id=? AND active=1 AND last_seen>=?", (session["room_id"], cutoff)).fetchone()[0]
        rows = self.connection.execute(
            """SELECT * FROM messages WHERE direction='in' AND state IN ('received','no_target_session') AND room_id=?
               AND (json_extract(envelope_json,'$.to.session')=?
                    OR (json_extract(envelope_json,'$.to.session')='' AND ?=1))
               ORDER BY received_at LIMIT ?""",
            (session["room_id"], session["alias"], active_count, limit),
        ).fetchall()
        ids = [row["message_id"] for row in rows]
        if ids:
            marks = ",".join("?" for _ in ids)
            self.connection.execute(f"UPDATE messages SET state='consumed',consumed_at=? WHERE message_id IN ({marks})", (format_time(utc_now()), *ids))
        return [json.loads(row["envelope_json"]) for row in rows]

    def status(self) -> dict[str, Any]:
        counts = {}
        for state, count in self.connection.execute("SELECT state,COUNT(*) FROM outbox GROUP BY state"):
            counts[state] = count
        return {
            "rooms": self.connection.execute("SELECT COUNT(*) FROM rooms WHERE status='active'").fetchone()[0],
            "peers": self.connection.execute("SELECT COUNT(*) FROM peers WHERE allowed=1").fetchone()[0],
            "sessions": self.connection.execute("SELECT COUNT(*) FROM sessions WHERE active=1").fetchone()[0],
            "outbox": counts,
            "inbox_waiting_for_session": self.connection.execute("SELECT COUNT(*) FROM messages WHERE direction='in' AND state='no_target_session'").fetchone()[0],
        }
