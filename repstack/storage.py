"""SQLite storage layer for users, logs, and issues."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import CanonicalLog, IssueRecord, UserInput


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


class Storage:
    """SQLite-backed storage for RepStack."""

    def __init__(self, db_path: str | Path = "repstack.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = _dict_factory
            self._ensure_schema()
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_schema(self) -> None:
        conn = self.connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                default_unit TEXT NOT NULL DEFAULT 'lb',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS logs (
                log_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                canonical_json TEXT NOT NULL,
                canonical_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS issues (
                log_id TEXT PRIMARY KEY,
                issue_json TEXT NOT NULL,
                FOREIGN KEY (log_id) REFERENCES logs(log_id)
            );
            CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id);
            CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);
        """)
        conn.commit()

    def ensure_user(self, user: UserInput) -> str:
        """Ensure user exists; return user_id (generated if not provided)."""
        conn = self.connect()
        user_id = user.user_id or generate_id("user")
        conn.execute(
            """
            INSERT INTO users (user_id, default_unit, timezone)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                default_unit = excluded.default_unit,
                timezone = excluded.timezone
            """,
            (user_id, user.default_unit, user.timezone),
        )
        conn.commit()
        return user_id

    def store_log(
        self,
        log_id: str,
        user_id: str,
        canonical_log: CanonicalLog,
        canonical_sha256: str,
    ) -> None:
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO logs (log_id, user_id, canonical_json, canonical_sha256)
            VALUES (?, ?, ?, ?)
            """,
            (log_id, user_id, canonical_log.model_dump_json(), canonical_sha256),
        )
        conn.commit()

    def store_issues(self, log_id: str, issues: list[IssueRecord]) -> None:
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO issues (log_id, issue_json) VALUES (?, ?)",
            (log_id, json.dumps([i.model_dump() for i in issues])),
        )
        conn.commit()

    def get_log(self, log_id: str) -> Optional[dict]:
        """Return dict with log_id, user_id, canonical_json (parsed), canonical_sha256, created_at."""
        conn = self.connect()
        row = conn.execute(
            "SELECT log_id, user_id, canonical_json, canonical_sha256, created_at FROM logs WHERE log_id = ?",
            (log_id,),
        ).fetchone()
        if not row:
            return None
        row = dict(row)
        row["canonical_json"] = json.loads(row["canonical_json"])
        return row

    def get_issues(self, log_id: str) -> list[dict]:
        conn = self.connect()
        row = conn.execute("SELECT issue_json FROM issues WHERE log_id = ?", (log_id,)).fetchone()
        if not row:
            return []
        return json.loads(row["issue_json"])

    def get_logs_for_user(
        self,
        user_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Return list of log rows (canonical_json parsed) in date order."""
        conn = self.connect()
        query = """
            SELECT log_id, user_id, canonical_json, canonical_sha256, created_at
            FROM logs WHERE user_id = ?
        """
        params: list = [user_id]
        if start_date:
            # We store full JSON; filter by parsed date in app if needed, or add a date column later
            pass
        if end_date:
            pass
        rows = conn.execute(query, params).fetchall()
        out = []
        for row in rows:
            r = dict(row)
            r["canonical_json"] = json.loads(r["canonical_json"])
            out.append(r)
        def _session_dates(r: dict) -> list[str]:
            sessions = r.get("canonical_json", {}).get("sessions", [])
            return [s["date"] for s in sessions if s.get("date")] if sessions else []

        out.sort(key=lambda r: min(_session_dates(r)) if _session_dates(r) else "9999-99-99")
        if start_date or end_date:
            filtered = []
            for r in out:
                dates = _session_dates(r)
                if not dates:
                    continue
                if start_date and max(dates) < start_date:
                    continue
                if end_date and min(dates) > end_date:
                    continue
                filtered.append(r)
            out = filtered
        return out


def generate_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
