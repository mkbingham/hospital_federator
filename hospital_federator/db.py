from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from .config import Peer
from .utils import now_ts


class OutboxDB:
    """Persistence of inbox, sent items, and send queue.

    Outbox tables:
      - outbox_jobs: job metadata + events + target list
      - deliveries: per-target delivery status

    Inbox table:
      - inbox_events: idempotent store of received events (by event_id)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS outbox_jobs (
                        job_id TEXT PRIMARY KEY,
                        created_at REAL NOT NULL,
                        origin_peer_id TEXT NOT NULL,
                        label TEXT,
                        events_json TEXT NOT NULL,
                        targets_json TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS deliveries (
                        delivery_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        target_peer_id TEXT NOT NULL,
                        target_url TEXT NOT NULL,
                        status TEXT NOT NULL, -- PENDING|SENT|FAILED
                        attempts INTEGER NOT NULL,
                        last_error TEXT,
                        last_attempt_at REAL,
                        last_http_status INTEGER,
                        UNIQUE(job_id, target_peer_id),
                        FOREIGN KEY(job_id) REFERENCES outbox_jobs(job_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS inbox_events (
                        event_id TEXT PRIMARY KEY,
                        received_at REAL NOT NULL,
                        from_peer_id TEXT,
                        event_type TEXT NOT NULL,
                        doc_id TEXT,
                        kind TEXT,
                        origin_node TEXT,
                        created_at REAL,
                        payload_json TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS inbox_pushes (
                        push_id TEXT PRIMARY KEY,
                        received_at REAL NOT NULL,
                        from_peer_id TEXT,
                        remote_addr TEXT,
                        bytes_len INTEGER NOT NULL,
                        events_count INTEGER NOT NULL,
                        body_json TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_inbox_pushes_received_at ON inbox_pushes(received_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_inbox_received_at ON inbox_events(received_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_inbox_event_type ON inbox_events(event_type);
                    CREATE INDEX IF NOT EXISTS idx_inbox_from_peer ON inbox_events(from_peer_id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ---------------------------------------------------------------------
    # Outbox jobs
    # ---------------------------------------------------------------------

    def add_job(self, origin_peer_id: str, label: str, events: List[dict], targets: List[Peer]) -> str:
        job_id = str(uuid.uuid4())
        created_at = now_ts()
        events_json = json.dumps(events, ensure_ascii=False)
        targets_json = json.dumps([t.peer_id for t in targets], ensure_ascii=False)

        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO outbox_jobs(job_id, created_at, origin_peer_id, label, events_json, targets_json) VALUES(?,?,?,?,?,?)",
                    (job_id, created_at, origin_peer_id, label, events_json, targets_json),
                )
                for t in targets:
                    conn.execute(
                        "INSERT OR REPLACE INTO deliveries(delivery_id, job_id, target_peer_id, target_url, status, attempts) VALUES(?,?,?,?,?,?)",
                        (str(uuid.uuid4()), job_id, t.peer_id, t.url, "PENDING", 0),
                    )
                conn.commit()
                return job_id
            finally:
                conn.close()

    def list_jobs(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "SELECT job_id, created_at, origin_peer_id, label, targets_json FROM outbox_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                jobs: List[Dict[str, Any]] = []
                for row in cur.fetchall():
                    jobs.append(
                        {
                            "job_id": row[0],
                            "created_at": row[1],
                            "origin_peer_id": row[2],
                            "label": row[3] or "",
                            "targets": json.loads(row[4]),
                        }
                    )
                return jobs
            finally:
                conn.close()

    def get_job_events(self, job_id: str) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("SELECT events_json FROM outbox_jobs WHERE job_id=?", (job_id,))
                row = cur.fetchone()
                if not row:
                    return []
                return json.loads(row[0])
            finally:
                conn.close()

    def list_deliveries(self, job_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "SELECT target_peer_id, target_url, status, attempts, last_error, last_attempt_at, last_http_status "
                    "FROM deliveries WHERE job_id=? ORDER BY target_peer_id",
                    (job_id,),
                )
                out: List[Dict[str, Any]] = []
                for r in cur.fetchall():
                    out.append(
                        {
                            "target_peer_id": r[0],
                            "target_url": r[1],
                            "status": r[2],
                            "attempts": r[3],
                            "last_error": r[4],
                            "last_attempt_at": r[5],
                            "last_http_status": r[6],
                        }
                    )
                return out
            finally:
                conn.close()

    def update_delivery(
        self,
        job_id: str,
        target_peer_id: str,
        status: str,
        attempts: int,
        last_error: Optional[str],
        last_attempt_at: float,
        last_http_status: Optional[int],
    ) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE deliveries SET status=?, attempts=?, last_error=?, last_attempt_at=?, last_http_status=? "
                    "WHERE job_id=? AND target_peer_id=?",
                    (status, attempts, last_error, last_attempt_at, last_http_status, job_id, target_peer_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_pending_or_failed_targets(self, job_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "SELECT target_peer_id, target_url, status, attempts FROM deliveries WHERE job_id=? AND status IN ('PENDING','FAILED')",
                    (job_id,),
                )
                return [
                    {
                        "target_peer_id": r[0],
                        "target_url": r[1],
                        "status": r[2],
                        "attempts": r[3],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

    # ---------------------------------------------------------------------
    # Inbox (received events)
    # ---------------------------------------------------------------------

    def add_inbox_events(self, events: List[dict], from_peer_id: Optional[str] = None) -> int:
        """Persist received events (idempotent on event_id). Returns number of newly inserted events."""

        if not events:
            return 0

        with self._lock:
            conn = self._conn()
            try:
                before = conn.total_changes
                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    ev_id = str(ev.get("event_id", "")).strip()
                    if not ev_id:
                        continue

                    ev_type = str(ev.get("event_type", "")).strip() or "Unknown"
                    origin_node = str(ev.get("origin_node", "")).strip() or None

                    created_at = ev.get("created_at")
                    try:
                        created_at_f = float(created_at) if created_at is not None else None
                    except Exception:
                        created_at_f = None

                    payload = ev.get("payload") or {}
                    doc_id = payload.get("doc_id")
                    kind = payload.get("kind")

                    payload_json = json.dumps(ev, ensure_ascii=False)
                    conn.execute(
                        "INSERT OR IGNORE INTO inbox_events(event_id, received_at, from_peer_id, event_type, doc_id, kind, origin_node, created_at, payload_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (ev_id, now_ts(), from_peer_id, ev_type, doc_id, kind, origin_node, created_at_f, payload_json),
                    )

                conn.commit()
                return conn.total_changes - before
            finally:
                conn.close()

    def add_inbox_push(
        self,
        *,
        from_peer_id: Optional[str],
        remote_addr: Optional[str],
        raw_bytes_len: int,
        events_count: int,
        body_obj: Dict[str, Any],
    ) -> str:
        """Persist a single inbound /events/push request.
        """

        push_id = str(uuid.uuid4())
        received_at = now_ts()
        body_json = json.dumps(body_obj, ensure_ascii=False)

        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO inbox_pushes(push_id, received_at, from_peer_id, remote_addr, bytes_len, events_count, body_json) VALUES(?,?,?,?,?,?,?)",
                    (push_id, received_at, from_peer_id, remote_addr, int(raw_bytes_len), int(events_count), body_json),
                )
                conn.commit()
                return push_id
            finally:
                conn.close()

    def list_inbox_pushes(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "SELECT push_id, received_at, from_peer_id, remote_addr, bytes_len, events_count FROM inbox_pushes ORDER BY received_at DESC LIMIT ?",
                    (limit,),
                )
                return [
                    {
                        "push_id": r[0],
                        "received_at": r[1],
                        "from_peer_id": r[2],
                        "remote_addr": r[3],
                        "bytes_len": r[4],
                        "events_count": r[5],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

    def get_inbox_push_body(self, push_id: str) -> Optional[str]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("SELECT body_json FROM inbox_pushes WHERE push_id=?", (push_id,))
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                conn.close()

    def list_inbox_events(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "SELECT event_id, received_at, from_peer_id, event_type, doc_id, kind, origin_node, created_at "
                    "FROM inbox_events ORDER BY received_at DESC LIMIT ?",
                    (limit,),
                )
                out: List[Dict[str, Any]] = []
                for r in cur.fetchall():
                    out.append(
                        {
                            "event_id": r[0],
                            "received_at": r[1],
                            "from_peer_id": r[2],
                            "event_type": r[3],
                            "doc_id": r[4],
                            "kind": r[5],
                            "origin_node": r[6],
                            "created_at": r[7],
                        }
                    )
                return out
            finally:
                conn.close()

    def get_inbox_event_payload(self, event_id: str) -> Optional[str]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("SELECT payload_json FROM inbox_events WHERE event_id=?", (event_id,))
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                conn.close()
