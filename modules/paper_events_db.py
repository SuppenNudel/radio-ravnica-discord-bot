from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data/paper_events.db")


@dataclass
class EventRow:
    id: int
    server_id: str
    thread_id: str | None
    title: str
    start_at: str
    end_at: str | None
    location_name: str | None
    location_address: str | None
    location_city: str | None
    event_type: str | None
    url: str | None
    image_url: str | None
    formats: list[str]


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS paper_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            thread_id TEXT,
            author_id TEXT,
            author_name TEXT,
            title TEXT NOT NULL,
            description TEXT,
            start_at TEXT NOT NULL,
            end_at TEXT,
            timezone TEXT,
            location_name TEXT,
            location_address TEXT,
            location_city TEXT,
            location_state TEXT,
            location_url TEXT,
            fee_eur REAL,
            formats_json TEXT,
            event_type TEXT,
            url TEXT,
            image_url TEXT,
            is_recurring INTEGER NOT NULL DEFAULT 0,
            recurrence_rule TEXT,
            recurrence_until TEXT,
            recurrence_count INTEGER,
            parent_template_id INTEGER,
            occurrence_key TEXT UNIQUE,
            is_generated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(parent_template_id) REFERENCES paper_events(id)
        );

        CREATE INDEX IF NOT EXISTS idx_paper_events_server_start ON paper_events(server_id, start_at);
        CREATE INDEX IF NOT EXISTS idx_paper_events_thread_id ON paper_events(thread_id);
        CREATE INDEX IF NOT EXISTS idx_paper_events_parent_template ON paper_events(parent_template_id);
        """
    )
    conn.commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def upsert_event(conn: sqlite3.Connection, event: dict[str, Any]) -> int:
    """Insert or update a paper event. Uses thread_id + server_id as the upsert key."""
    event_copy = dict(event)
    event_copy["formats_json"] = _json_dumps(event_copy.pop("formats", []))
    event_copy.setdefault("created_at", datetime.utcnow().isoformat())
    event_copy["updated_at"] = datetime.utcnow().isoformat()

    thread_id = event_copy.get("thread_id")
    server_id = event_copy.get("server_id")

    if thread_id and server_id:
        existing = conn.execute(
            "SELECT id FROM paper_events WHERE thread_id = ? AND server_id = ?",
            (str(thread_id), str(server_id)),
        ).fetchone()
        if existing:
            row_id = int(existing["id"])
            update_cols = [k for k in event_copy if k not in {"server_id", "thread_id", "created_at"}]
            set_clause = ", ".join(f"{c} = ?" for c in update_cols)
            values = [event_copy[c] for c in update_cols] + [str(server_id), str(thread_id)]
            conn.execute(
                f"UPDATE paper_events SET {set_clause} WHERE server_id = ? AND thread_id = ?",
                values,
            )
            conn.commit()
            return row_id

    columns = sorted(event_copy.keys())
    values = [event_copy[c] for c in columns]
    cursor = conn.execute(
        f"INSERT INTO paper_events ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])})",
        values,
    )
    conn.commit()
    return cursor.lastrowid


def list_upcoming_events(
    conn: sqlite3.Connection,
    *,
    server_id: str,
    now: datetime | None = None,
    horizon_days: int = 365,
) -> list[EventRow]:
    now = now or datetime.utcnow()
    horizon = now + timedelta(days=horizon_days)

    rows = conn.execute(
        """
        SELECT *
        FROM paper_events
        WHERE server_id = ?
          AND start_at >= ?
          AND start_at <= ?
          AND (is_generated = 1 OR is_recurring = 0)
        ORDER BY start_at ASC
        """,
        (
            server_id,
            now.isoformat(),
            horizon.isoformat(),
        ),
    ).fetchall()

    result: list[EventRow] = []
    for row in rows:
        result.append(
            EventRow(
                id=int(row["id"]),
                server_id=str(row["server_id"]),
                thread_id=row["thread_id"],
                title=row["title"],
                start_at=row["start_at"],
                end_at=row["end_at"],
                location_name=row["location_name"],
                location_address=row["location_address"],
                location_city=row["location_city"],
                event_type=row["event_type"],
                url=row["url"],
                image_url=row["image_url"],
                formats=json.loads(row["formats_json"] or "[]"),
            )
        )
    return result


def delete_event_by_thread_id(conn: sqlite3.Connection, thread_id: int | str) -> int:
    """Hard-delete a paper event row by thread_id. Returns number of rows deleted."""
    cursor = conn.execute(
        "DELETE FROM paper_events WHERE thread_id = ?",
        (str(thread_id),),
    )
    conn.commit()
    return cursor.rowcount


def export_public_events_json(
    conn: sqlite3.Connection,
    *,
    output_path: str | Path,
    server_id: str,
    now: datetime | None = None,
    horizon_days: int = 365,
) -> Path:
    now = now or datetime.utcnow()
    items = list_upcoming_events(
        conn,
        server_id=server_id,
        now=now,
        horizon_days=horizon_days,
    )

    payload = {
        "generated_at": now.isoformat(),
        "server_id": server_id,
        "count": len(items),
        "events": [
            {
                "id": item.id,
                "thread_id": item.thread_id,
                "title": item.title,
                "start_at": item.start_at,
                "end_at": item.end_at,
                "location_name": item.location_name,
                "location_address": item.location_address,
                "location_city": item.location_city,
                "event_type": item.event_type,
                "formats": item.formats,
                "url": item.url,
                "image_url": item.image_url,
            }
            for item in items
        ],
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return output
