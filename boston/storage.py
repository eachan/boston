from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER,
                    timed INTEGER NOT NULL DEFAULT 1,
                    white_score INTEGER NOT NULL DEFAULT 0,
                    blue_score INTEGER NOT NULL DEFAULT 0,
                    winner TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id INTEGER,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT,
                    points INTEGER,
                    detail TEXT,
                    payload_json TEXT,
                    FOREIGN KEY(match_id) REFERENCES matches(id)
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def start_match(self, duration_seconds: int | None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO matches (started_at, duration_seconds, timed)
                VALUES (?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
                    duration_seconds,
                    0 if duration_seconds is None else 1,
                ),
            )
            return int(cur.lastrowid)

    def end_match(
        self,
        match_id: int,
        white_score: int,
        blue_score: int,
        winner: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE matches
                SET ended_at = ?, white_score = ?, blue_score = ?, winner = ?
                WHERE id = ?
                """,
                (
                    datetime.utcnow().isoformat(),
                    white_score,
                    blue_score,
                    winner,
                    match_id,
                ),
            )

    def add_event(
        self,
        event_type: str,
        match_id: int | None = None,
        actor: str | None = None,
        points: int | None = None,
        detail: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO events (match_id, timestamp, event_type, actor, points, detail, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    datetime.utcnow().isoformat(),
                    event_type,
                    actor,
                    points,
                    detail,
                    json.dumps(payload or {}),
                ),
            )

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, match_id, timestamp, event_type, actor, points, detail, payload_json
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._event_row_to_dict(r) for r in rows]

    def get_match_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            matches = conn.execute(
                """
                SELECT id, started_at, ended_at, duration_seconds, timed, white_score, blue_score, winner
                FROM matches
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            history: list[dict[str, Any]] = []
            for m in matches:
                events = conn.execute(
                    """
                    SELECT id, match_id, timestamp, event_type, actor, points, detail, payload_json
                    FROM events
                    WHERE match_id = ?
                    ORDER BY id ASC
                    """,
                    (m["id"],),
                ).fetchall()
                history.append(
                    {
                        "id": m["id"],
                        "started_at": m["started_at"],
                        "ended_at": m["ended_at"],
                        "duration_seconds": m["duration_seconds"],
                        "timed": bool(m["timed"]),
                        "white_score": m["white_score"],
                        "blue_score": m["blue_score"],
                        "winner": m["winner"],
                        "events": [self._event_row_to_dict(e) for e in events],
                    }
                )
        return history

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, datetime.utcnow().isoformat()),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                return str(row["value"])
        return default

    def set_runtime_state(self, state: dict[str, Any]) -> None:
        self.set_setting("runtime_state_json", json.dumps(state))

    def get_runtime_state(self) -> dict[str, Any]:
        raw = self.get_setting("runtime_state_json", "{}") or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload_text = row["payload_json"] or "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            payload = {}
        return {
            "id": row["id"],
            "match_id": row["match_id"],
            "timestamp": row["timestamp"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "points": row["points"],
            "detail": row["detail"],
            "payload": payload,
        }
