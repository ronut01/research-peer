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

SCHEMA_VERSION = 2
SESSION_STALE_SECONDS = 300


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
          created_at TEXT NOT NULL, disclosure TEXT NOT NULL DEFAULT 'status',
          auto_answer INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT ''
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
          consumed_at TEXT, delivery_session_id TEXT
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
        CREATE TABLE IF NOT EXISTS auto_answers (
          room_id TEXT NOT NULL, request_id TEXT NOT NULL, question_message_id TEXT NOT NULL,
          answer_message_id TEXT NOT NULL, disclosure TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(room_id, request_id)
        );
        """)
        self._ensure_column("rooms", "disclosure", "TEXT NOT NULL DEFAULT 'status'")
        self._ensure_column("rooms", "auto_answer", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("rooms", "note", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("messages", "delivery_session_id", "TEXT")
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

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

    def configure_room(
        self, room_id: str, *, auto_answer: bool | None = None,
        disclosure: str | None = None, note: str | None = None,
    ) -> dict[str, Any]:
        if disclosure is not None and disclosure not in {"none", "status", "summary", "full"}:
            raise ValueError("disclosure must be one of: none, status, summary, full")
        if note is not None and len(note) > 4000:
            raise ValueError("room note must be at most 4000 characters")
        assignments: list[str] = []
        values: list[Any] = []
        if auto_answer is not None:
            assignments.append("auto_answer=?")
            values.append(int(auto_answer))
        if disclosure is not None:
            assignments.append("disclosure=?")
            values.append(disclosure)
        if note is not None:
            assignments.append("note=?")
            values.append(note)
        if assignments:
            self.connection.execute(
                f"UPDATE rooms SET {', '.join(assignments)} WHERE room_id=?",
                (*values, room_id),
            )
        room = self.connection.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        if room is None:
            raise LookupError(f"room not found: {room_id}")
        return dict(room)

    def room_status(self, room_id: str) -> dict[str, Any]:
        room = dict(self.resolve_room(room_id, active_only=False))
        peers = self.peers_for_room(room["room_id"])
        sessions = [item for item in self.list_sessions() if item["room_id"] == room["room_id"]]
        message_counts = {
            f"{row['direction']}_{row['state']}": row["count"]
            for row in self.connection.execute(
                "SELECT direction,state,COUNT(*) AS count FROM messages WHERE room_id=? GROUP BY direction,state",
                (room["room_id"],),
            )
        }
        last = self.connection.execute(
            "SELECT received_at,direction,state,envelope_json FROM messages WHERE room_id=? ORDER BY received_at DESC LIMIT 1",
            (room["room_id"],),
        ).fetchone()
        return {
            **room,
            "auto_answer": bool(room["auto_answer"]),
            "peers": [
                {
                    "peer_id": peer["peer_id"], "user_name": peer["user_name"],
                    "fingerprint": peer["fingerprint"], "tls_fingerprint": peer["tls_fingerprint"],
                    "endpoint": peer["endpoint"], "allowed": bool(peer["allowed"]),
                }
                for peer in peers
            ],
            "sessions": sessions,
            "messages": message_counts,
            "last_exchange": None if last is None else {
                "at": last["received_at"], "direction": last["direction"], "state": last["state"],
                "type": json.loads(last["envelope_json"])["type"],
            },
        }

    def leave_room(self, room_id: str) -> int:
        with self.transaction() as connection:
            connection.execute("UPDATE rooms SET status='left' WHERE room_id=?", (room_id,))
            connection.execute("UPDATE sessions SET active=0, room_id=NULL WHERE room_id=?", (room_id,))
            cancelled = connection.execute(
                """UPDATE outbox SET state='cancelled',last_error='ROOM_LEFT'
                   WHERE state IN ('pending','attempting') AND message_id IN
                   (SELECT message_id FROM messages WHERE room_id=?)""",
                (room_id,),
            ).rowcount
            connection.execute(
                """UPDATE messages SET state='cancelled'
                   WHERE room_id=? AND direction='out' AND state IN ('pending','attempting')""",
                (room_id,),
            )
            return cancelled

    def room_delete_plan(self, room_id: str) -> dict[str, Any]:
        room = self.resolve_room(room_id, active_only=False)

        def count(query: str) -> int:
            return int(self.connection.execute(query, (room["room_id"],)).fetchone()[0])

        return {
            "room_id": room["room_id"],
            "display_name": room["display_name"],
            "status": room["status"],
            "local_only": True,
            "remove": {
                "messages": count("SELECT COUNT(*) FROM messages WHERE room_id=?"),
                "pending_outbox": count(
                    """SELECT COUNT(*) FROM outbox WHERE state IN ('pending','attempting')
                       AND message_id IN (SELECT message_id FROM messages WHERE room_id=?)"""
                ),
                "invites": count("SELECT COUNT(*) FROM invites WHERE room_id=?"),
                "peer_memberships": count("SELECT COUNT(*) FROM room_peers WHERE room_id=?"),
                "session_bindings": count("SELECT COUNT(*) FROM sessions WHERE room_id=?"),
                "request_counters": count("SELECT COUNT(*) FROM request_counts WHERE room_id=?"),
                "sequence_counters": count("SELECT COUNT(*) FROM sequence_counters WHERE room_id=?"),
                "auto_answers": count("SELECT COUNT(*) FROM auto_answers WHERE room_id=?"),
            },
            "preserve": [
                "project repositories",
                "experiment artifacts",
                "other rooms",
                "remote peer data",
            ],
        }

    def delete_room(self, room_id: str) -> dict[str, Any]:
        """Delete one exact local room and its Research Peer-owned records.

        Sessions are preserved as inactive records, but their room binding is
        cleared. A peer identity is removed only if no other room references it
        and it has no remaining outbox record.
        """
        plan = self.room_delete_plan(room_id)
        room_id = plan["room_id"]
        removed_orphan_peers = 0
        with self.transaction() as connection:
            peer_rows = connection.execute(
                """SELECT p.peer_id,p.fingerprint FROM peers p
                   JOIN room_peers rp ON rp.peer_id=p.peer_id WHERE rp.room_id=?""",
                (room_id,),
            ).fetchall()
            connection.execute("UPDATE sessions SET active=0,room_id=NULL WHERE room_id=?", (room_id,))
            connection.execute(
                "DELETE FROM outbox WHERE message_id IN (SELECT message_id FROM messages WHERE room_id=?)",
                (room_id,),
            )
            connection.execute("DELETE FROM messages WHERE room_id=?", (room_id,))
            connection.execute("DELETE FROM invites WHERE room_id=?", (room_id,))
            connection.execute("DELETE FROM request_counts WHERE room_id=?", (room_id,))
            connection.execute("DELETE FROM sequence_counters WHERE room_id=?", (room_id,))
            connection.execute("DELETE FROM auto_answers WHERE room_id=?", (room_id,))
            connection.execute("DELETE FROM room_peers WHERE room_id=?", (room_id,))
            connection.execute("DELETE FROM rooms WHERE room_id=?", (room_id,))
            for peer in peer_rows:
                still_used = connection.execute(
                    """SELECT 1 FROM room_peers WHERE peer_id=?
                       UNION SELECT 1 FROM outbox WHERE peer_id=? LIMIT 1""",
                    (peer["peer_id"], peer["peer_id"]),
                ).fetchone()
                if still_used:
                    continue
                connection.execute("DELETE FROM replay_nonces WHERE fingerprint=?", (peer["fingerprint"],))
                connection.execute("DELETE FROM rate_events WHERE fingerprint=?", (peer["fingerprint"],))
                connection.execute("DELETE FROM peers WHERE peer_id=?", (peer["peer_id"],))
                removed_orphan_peers += 1
        return {
            "deleted": room_id,
            "display_name": plan["display_name"],
            "removed": {**plan["remove"], "orphan_peers": removed_orphan_peers},
            "remote_data_removed": False,
            "project_artifacts_removed": False,
        }

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
            cutoff = format_time(utc_now() - timedelta(seconds=SESSION_STALE_SECONDS))
            connection.execute(
                "UPDATE sessions SET active=0 WHERE active=1 AND last_seen<?", (cutoff,)
            )
            target = envelope["to"]["session"]
            if target:
                delivery = connection.execute(
                    """SELECT session_id FROM sessions
                       WHERE room_id=? AND alias=? AND active=1 AND last_seen>=?
                       ORDER BY last_seen DESC,rowid DESC LIMIT 1""",
                    (envelope["room_id"], target, cutoff),
                ).fetchone()
            else:
                delivery = connection.execute(
                    """SELECT session_id FROM sessions
                       WHERE room_id=? AND active=1 AND last_seen>=?
                       ORDER BY last_seen DESC,rowid DESC LIMIT 1""",
                    (envelope["room_id"], cutoff),
                ).fetchone()
            delivery_session_id = delivery["session_id"] if delivery else None
            message_state = "received" if delivery_session_id else "no_target_session"
            connection.execute(
                """INSERT INTO messages(
                     message_id,room_id,direction,envelope_json,state,received_at,delivery_session_id
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    envelope["message_id"], envelope["room_id"], "in", payload,
                    message_state, now_text, delivery_session_id,
                ),
            )
            return True

    def register_session(self, session_id: str, alias: str, user_name: str, room_id: str | None) -> None:
        now = format_time(utc_now())
        cutoff = format_time(utc_now() - timedelta(seconds=SESSION_STALE_SECONDS))
        with self.transaction() as connection:
            connection.execute("UPDATE sessions SET active=0 WHERE active=1 AND last_seen<?", (cutoff,))
            if room_id:
                connection.execute(
                    "UPDATE sessions SET active=0 WHERE room_id=? AND alias=? AND session_id<>?",
                    (room_id, alias, session_id),
                )
            connection.execute(
                """INSERT INTO sessions(session_id,alias,user_name,room_id,active,last_seen) VALUES(?,?,?,?,1,?)
                   ON CONFLICT(session_id) DO UPDATE SET alias=excluded.alias,user_name=excluded.user_name,
                   room_id=excluded.room_id,active=1,last_seen=excluded.last_seen""",
                (session_id, alias, user_name, room_id, now),
            )
            if room_id:
                connection.execute(
                    """UPDATE messages SET delivery_session_id=?
                       WHERE room_id=? AND direction='in' AND state='received'
                       AND delivery_session_id IN (
                         SELECT session_id FROM sessions WHERE room_id=? AND alias=? AND active=0
                       )""",
                    (session_id, room_id, room_id, alias),
                )
                waiting = connection.execute(
                    """SELECT message_id,envelope_json FROM messages
                       WHERE room_id=? AND direction='in' AND state='no_target_session'
                       ORDER BY received_at""",
                    (room_id,),
                ).fetchall()
                claim = []
                for item in waiting:
                    target = json.loads(item["envelope_json"])["to"]["session"]
                    if not target or target == alias:
                        claim.append(item["message_id"])
                if claim:
                    placeholders = ",".join("?" for _ in claim)
                    connection.execute(
                        f"UPDATE messages SET state='received',delivery_session_id=? WHERE message_id IN ({placeholders})",
                        (session_id, *claim),
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
        rows = self.connection.execute(
            """SELECT * FROM messages WHERE direction='in' AND state='received' AND room_id=?
               AND delivery_session_id=?
               ORDER BY received_at LIMIT ?""",
            (session["room_id"], session_id, limit),
        ).fetchall()
        ids = [row["message_id"] for row in rows]
        if ids:
            marks = ",".join("?" for _ in ids)
            self.connection.execute(f"UPDATE messages SET state='consumed',consumed_at=? WHERE message_id IN ({marks})", (format_time(utc_now()), *ids))
        return [json.loads(row["envelope_json"]) for row in rows]

    def inbox(
        self, *, room_id: str | None = None, include_all: bool = False,
        consume: bool = False, limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["direction='in'"]
        values: list[Any] = []
        if room_id:
            clauses.append("room_id=?")
            values.append(room_id)
        if not include_all:
            clauses.append("state<>'consumed'")
        rows = self.connection.execute(
            f"SELECT * FROM messages WHERE {' AND '.join(clauses)} ORDER BY received_at LIMIT ?",
            (*values, limit),
        ).fetchall()
        results = []
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            results.append({
                "message_id": row["message_id"], "room_id": row["room_id"],
                "state": row["state"], "received_at": row["received_at"],
                "consumed_at": row["consumed_at"], "sender": envelope["from"],
                "type": envelope["type"], "request_id": envelope.get("request_id"),
                "owner_attention": envelope["owner_attention"], "body": envelope["body"],
                "automation_depth": envelope.get("automation_depth", 0),
            })
        if consume and rows:
            ids = [row["message_id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            self.connection.execute(
                f"UPDATE messages SET state='consumed',consumed_at=? WHERE message_id IN ({placeholders})",
                (format_time(utc_now()), *ids),
            )
        return results

    def history(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE m.room_id=?" if room_id else ""
        values: tuple[Any, ...] = (room_id, limit) if room_id else (limit,)
        rows = self.connection.execute(
            f"""SELECT m.*,a.disclosure AS auto_disclosure
                FROM messages m LEFT JOIN auto_answers a ON a.answer_message_id=m.message_id
                {where} ORDER BY m.received_at DESC LIMIT ?""",
            values,
        ).fetchall()
        result = []
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            result.append({
                "message_id": row["message_id"], "room_id": row["room_id"],
                "direction": row["direction"], "state": row["state"],
                "at": row["received_at"], "type": envelope["type"],
                "from": envelope["from"], "to": envelope["to"],
                "request_id": envelope.get("request_id"), "body": envelope["body"],
                "automation_depth": envelope.get("automation_depth", 0),
                "automated": row["auto_disclosure"] is not None,
                "disclosure": row["auto_disclosure"],
            })
        return result

    def auto_answer_context(self, question_message_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """SELECT m.*,r.auto_answer,r.disclosure,r.note
               FROM messages m JOIN rooms r ON r.room_id=m.room_id
               WHERE m.message_id=? AND m.direction='in'""",
            (question_message_id,),
        ).fetchone()
        if row is None:
            raise LookupError("inbound question message not found")
        envelope = json.loads(row["envelope_json"])
        if envelope["type"] != "QUESTION" or not envelope["reply_required"]:
            raise ValueError("automatic replies are allowed only for QUESTION messages requiring a reply")
        if envelope["owner_attention"]:
            raise PermissionError("question requires local owner attention")
        if not row["auto_answer"]:
            raise PermissionError("auto-answer is disabled for this room")
        if row["disclosure"] == "none":
            raise PermissionError("room disclosure policy forbids automatic answers")
        existing = self.connection.execute(
            "SELECT 1 FROM auto_answers WHERE room_id=? AND request_id=?",
            (row["room_id"], envelope["request_id"]),
        ).fetchone()
        if existing:
            raise ValueError("this request_id has already been auto-answered")
        return {
            "room_id": row["room_id"], "request_id": envelope["request_id"],
            "question_message_id": question_message_id, "from": envelope["from"],
            "incoming_depth": envelope.get("automation_depth", 0),
            "disclosure": row["disclosure"], "note": row["note"],
        }

    def enqueue_auto_answer(
        self, envelope: dict[str, Any], peer_id: str, *, question_message_id: str,
        disclosure: str,
    ) -> None:
        now = format_time(utc_now())
        payload = canonical_json(envelope).decode("utf-8")
        try:
            with self.transaction() as connection:
                connection.execute(
                    """INSERT INTO auto_answers(
                         room_id,request_id,question_message_id,answer_message_id,disclosure,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        envelope["room_id"], envelope["request_id"], question_message_id,
                        envelope["message_id"], disclosure, now,
                    ),
                )
                connection.execute(
                    "INSERT INTO messages(message_id,room_id,direction,envelope_json,state,received_at) VALUES(?,?,?,?,?,?)",
                    (envelope["message_id"], envelope["room_id"], "out", payload, "pending", now),
                )
                connection.execute(
                    "INSERT INTO outbox(message_id,peer_id,attempts,next_attempt_at,state) VALUES(?,?,0,?,'pending')",
                    (envelope["message_id"], peer_id, time.time()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("this request_id has already been auto-answered") from exc

    def status(self) -> dict[str, Any]:
        cutoff = format_time(utc_now() - timedelta(seconds=SESSION_STALE_SECONDS))
        self.connection.execute("UPDATE sessions SET active=0 WHERE active=1 AND last_seen<?", (cutoff,))
        counts = {}
        for state, count in self.connection.execute("SELECT state,COUNT(*) FROM outbox GROUP BY state"):
            counts[state] = count
        return {
            "rooms": self.connection.execute("SELECT COUNT(*) FROM rooms WHERE status='active'").fetchone()[0],
            "peers": self.connection.execute("SELECT COUNT(*) FROM peers WHERE allowed=1").fetchone()[0],
            "sessions": self.connection.execute("SELECT COUNT(*) FROM sessions WHERE active=1").fetchone()[0],
            "stale_sessions": self.connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE last_seen<?", (cutoff,)
            ).fetchone()[0],
            "inactive_sessions": self.connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE active=0"
            ).fetchone()[0],
            "outbox": counts,
            "inbox_waiting_for_session": self.connection.execute("SELECT COUNT(*) FROM messages WHERE direction='in' AND state='no_target_session'").fetchone()[0],
        }
