"""Persistent, user-scoped reminder events and a lightweight due-event worker."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_LOG = logging.getLogger("llm.scheduled_events")
_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "bot.sqlite3"
_MAX_ACTIVE_PER_USER_GROUP = 20
_MAX_HORIZON_SECONDS = 366 * 24 * 60 * 60
_MIN_LEAD_SECONDS = 10
_CLAIM_LEASE_SECONDS = 300
_MAX_ATTEMPTS = 5


def _connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _parse_remind_at(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("remind_at is required as ISO-8601 date/time")
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("remind_at must be an ISO-8601 date/time") from exc
    if parsed.tzinfo is None:
        # Naive values are interpreted in the host's current local timezone.
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    local = parsed.astimezone()
    return parsed.timestamp(), local.isoformat(timespec="minutes"), local.tzname() or "local"


class ScheduledEventManager:
    """Small SQLite-backed scheduler; callbacks run on one daemon thread."""

    def __init__(self, on_due=None, *, db_path=None, autostart=True):
        global _DB_PATH
        self.db_path = Path(db_path) if db_path else _DB_PATH
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._callback = on_due if callable(on_due) else None
        self._thread = None
        self._ensure_schema()
        if autostart:
            self.start()

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS llm_scheduled_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dedupe_key TEXT NOT NULL UNIQUE,
                        group_id TEXT NOT NULL,
                        owner_wxid TEXT NOT NULL,
                        owner_name TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL,
                        due_at REAL NOT NULL,
                        due_local TEXT NOT NULL,
                        timezone_name TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        claimed_until REAL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS ix_llm_scheduled_due
                        ON llm_scheduled_events(status, due_at, id);
                    CREATE INDEX IF NOT EXISTS ix_llm_scheduled_owner
                        ON llm_scheduled_events(group_id, owner_wxid, status, due_at);
                """)
            finally:
                conn.close()

    def set_callback(self, callback):
        with self._lock:
            self._callback = callback if callable(callback) else None
        self._wake.set()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="llm-scheduled-events",
            )
            self._thread.start()
        self._wake.set()

    def close(self):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def manage(self, payload, *, group_id, owner_wxid, owner_name=""):
        payload = payload if isinstance(payload, dict) else {}
        action = str(payload.get("action") or "schedule").strip().lower()
        group_id = _clean(group_id, 200)
        owner_wxid = _clean(owner_wxid, 160)
        owner_name = _clean(owner_name, 120)
        if not group_id or not owner_wxid:
            return {"ok": False, "error": "Current group and requesting user's wxid are required."}
        if action in {"schedule", "create", "add"}:
            return self._schedule(payload, group_id, owner_wxid, owner_name)
        if action == "list":
            return self._list(group_id, owner_wxid)
        if action == "cancel":
            return self._cancel(payload, group_id, owner_wxid)
        return {"ok": False, "error": "action must be schedule, list, or cancel."}

    def _schedule(self, payload, group_id, owner_wxid, owner_name):
        title = _clean(payload.get("title"), 240)
        if not title:
            return {"ok": False, "error": "A short reminder title is required."}
        try:
            due_at, due_local, timezone_name = _parse_remind_at(payload.get("remind_at"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        now = time.time()
        if due_at < now + _MIN_LEAD_SECONDS:
            return {"ok": False, "error": "The reminder time must be at least 10 seconds in the future."}
        if due_at > now + _MAX_HORIZON_SECONDS:
            return {"ok": False, "error": "Reminders can be scheduled no more than one year ahead."}
        canonical = f"{group_id}\0{owner_wxid}\0{title.casefold()}\0{int(due_at // 60)}"
        dedupe_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT id,status,due_local,title FROM llm_scheduled_events WHERE dedupe_key=?",
                    (dedupe_key,),
                ).fetchone()
                if existing and existing["status"] in {"pending", "dispatching"}:
                    row = existing
                    deduplicated = True
                    conn.commit()
                else:
                    # A finished/cancelled reminder with the same title and minute
                    # may be intentionally scheduled again; keep active retries idempotent.
                    if existing:
                        canonical_retry = f"{canonical}\0{now:.6f}"
                        dedupe_key = hashlib.sha256(canonical_retry.encode("utf-8")).hexdigest()
                    count = conn.execute(
                        "SELECT COUNT(*) FROM llm_scheduled_events WHERE group_id=? AND owner_wxid=? AND status IN ('pending','dispatching')",
                        (group_id, owner_wxid),
                    ).fetchone()[0]
                    if int(count) >= _MAX_ACTIVE_PER_USER_GROUP:
                        conn.rollback()
                        return {"ok": False, "error": f"At most {_MAX_ACTIVE_PER_USER_GROUP} active reminders per user and group."}
                    conn.execute(
                        "DELETE FROM llm_scheduled_events WHERE status IN ('completed','cancelled','failed') AND updated_at<?",
                        (now - 90 * 24 * 60 * 60,),
                    )
                    conn.execute(
                        "INSERT INTO llm_scheduled_events(dedupe_key,group_id,owner_wxid,owner_name,title,due_at,due_local,timezone_name,status,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,'pending',?,?)",
                        (dedupe_key, group_id, owner_wxid, owner_name, title, due_at, due_local, timezone_name, now, now),
                    )
                    row = conn.execute(
                        "SELECT id,status,due_local,title FROM llm_scheduled_events WHERE dedupe_key=?",
                        (dedupe_key,),
                    ).fetchone()
                    deduplicated = False
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        self._wake.set()
        return {
            "ok": True,
            "action": "scheduled",
            "event_id": int(row["id"]),
            "status": row["status"],
            "title": row["title"],
            "remind_at_local": row["due_local"],
            "timezone": timezone_name,
            "deduplicated": deduplicated,
            "delivery": "At the due time, the LLM will be invoked with this event and the then-current system time.",
        }

    def _list(self, group_id, owner_wxid):
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id,title,due_local,timezone_name,status FROM llm_scheduled_events "
                    "WHERE group_id=? AND owner_wxid=? AND status IN ('pending','dispatching') "
                    "ORDER BY due_at,id LIMIT 20",
                    (group_id, owner_wxid),
                ).fetchall()
            finally:
                conn.close()
        return {"ok": True, "action": "list", "events": [dict(row) for row in rows]}

    def _cancel(self, payload, group_id, owner_wxid):
        try:
            event_id = int(payload.get("event_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "A numeric event_id is required to cancel a reminder."}
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE llm_scheduled_events SET status='cancelled',updated_at=? "
                    "WHERE id=? AND group_id=? AND owner_wxid=? AND status='pending'",
                    (now, event_id, group_id, owner_wxid),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        if cursor.rowcount:
            self._wake.set()
            return {"ok": True, "action": "cancelled", "event_id": event_id}
        return {"ok": False, "error": "No cancellable reminder with that id belongs to you in this group."}

    def _claim_due(self, now):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE llm_scheduled_events SET status='pending',claimed_until=NULL,updated_at=? "
                    "WHERE status='dispatching' AND claimed_until IS NOT NULL AND claimed_until<?",
                    (now, now),
                )
                rows = conn.execute(
                    "SELECT * FROM llm_scheduled_events WHERE status='pending' AND due_at<=? ORDER BY due_at,id LIMIT 4",
                    (now,),
                ).fetchall()
                claimed = []
                for row in rows:
                    conn.execute(
                        "UPDATE llm_scheduled_events SET status='dispatching',attempts=attempts+1,claimed_until=?,updated_at=? WHERE id=? AND status='pending'",
                        (now + _CLAIM_LEASE_SECONDS, now, row["id"]),
                    )
                    claimed.append(dict(row) | {"attempts": int(row["attempts"]) + 1})
                conn.commit()
                return claimed
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _finish(self, event, success, error=""):
        now = time.time()
        event_id = int(event["id"])
        attempts = int(event.get("attempts") or 1)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if success:
                    conn.execute(
                        "UPDATE llm_scheduled_events SET status='completed',claimed_until=NULL,updated_at=?,last_error='' WHERE id=? AND status='dispatching'",
                        (now, event_id),
                    )
                elif attempts >= _MAX_ATTEMPTS:
                    conn.execute(
                        "UPDATE llm_scheduled_events SET status='failed',claimed_until=NULL,updated_at=?,last_error=? WHERE id=? AND status='dispatching'",
                        (now, str(error or "delivery failed")[:500], event_id),
                    )
                else:
                    retry_at = now + min(60, 5 * (2 ** min(attempts - 1, 3)))
                    conn.execute(
                        "UPDATE llm_scheduled_events SET status='pending',due_at=?,claimed_until=NULL,updated_at=?,last_error=? WHERE id=? AND status='dispatching'",
                        (retry_at, now, str(error or "delivery failed")[:500], event_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            callback = self._callback
            if not callable(callback):
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                due = self._claim_due(time.time())
            except Exception:
                _LOG.exception("failed to claim due reminders")
                self._stop.wait(1.0)
                continue
            if not due:
                self._wake.wait(0.75)
                self._wake.clear()
                continue
            for event in due:
                if self._stop.is_set():
                    break
                try:
                    outcome = callback(event)
                    if isinstance(outcome, tuple):
                        success = bool(outcome[0])
                        error = str(outcome[1] if len(outcome) > 1 else "")
                    else:
                        success, error = bool(outcome), ""
                    self._finish(event, success, error)
                except Exception as exc:
                    _LOG.exception("scheduled reminder %s callback failed", event.get("id"))
                    try:
                        self._finish(event, False, f"{type(exc).__name__}: {exc}")
                    except Exception:
                        _LOG.exception("failed to update reminder state %s", event.get("id"))


__all__ = ["ScheduledEventManager"]
