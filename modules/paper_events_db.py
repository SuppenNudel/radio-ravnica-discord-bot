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
    location_country: str | None
    location_lat: float | None
    location_lng: float | None
    location_state: str | None
    location_url: str | None
    location_osm_type: str | None
    location_osm_id: str | None
    location_place_id: str | None
    fee_from: float | None
    fee_to: float | None
    fee_currency: str | None
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
            location_name TEXT,
            location_address TEXT,
            location_city TEXT,
            location_country TEXT,
            location_lat REAL,
            location_lng REAL,
            location_state TEXT,
            location_url TEXT,
            location_osm_type TEXT,
            location_osm_id TEXT,
            location_place_id TEXT,
            fee_eur REAL,
            fee_from REAL,
            fee_to REAL,
            fee_currency TEXT,
            formats_json TEXT,
            event_type TEXT,
            url TEXT,
            image_url TEXT,
            is_recurring INTEGER NOT NULL DEFAULT 0,
            recurrence_rule TEXT,
            recurrence_until TEXT,
            recurrence_count INTEGER,
            is_generated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_paper_events_server_start ON paper_events(server_id, start_at);
        CREATE INDEX IF NOT EXISTS idx_paper_events_thread_id ON paper_events(thread_id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_events_server_thread ON paper_events(server_id, thread_id);
        """
    )

    has_timezone_column = bool(
        conn.execute(
            "SELECT 1 FROM pragma_table_info('paper_events') WHERE name = 'timezone' LIMIT 1"
        ).fetchone()
    )
    if has_timezone_column:
        try:
            conn.execute("ALTER TABLE paper_events DROP COLUMN timezone")
        except sqlite3.OperationalError:
            # Older SQLite versions may not support DROP COLUMN.
            pass

    existing_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info('paper_events')").fetchall()
    }
    if "location_country" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_country TEXT")
    if "location_lat" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_lat REAL")
    if "location_lng" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_lng REAL")
    if "location_state" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_state TEXT")
    if "location_url" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_url TEXT")
    if "location_osm_type" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_osm_type TEXT")
    if "location_osm_id" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_osm_id TEXT")
    if "location_place_id" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN location_place_id TEXT")
    if "fee_from" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN fee_from REAL")
    if "fee_to" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN fee_to REAL")
    if "fee_currency" not in existing_columns:
        conn.execute("ALTER TABLE paper_events ADD COLUMN fee_currency TEXT")

    conn.execute(
        """
        UPDATE paper_events
        SET fee_from = COALESCE(fee_from, fee_eur),
            fee_currency = COALESCE(fee_currency, CASE WHEN fee_eur IS NOT NULL THEN 'EUR' ELSE fee_currency END)
        WHERE fee_eur IS NOT NULL
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
    return int(cursor.lastrowid or 0)


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
                    AND (is_generated = 1 OR is_recurring = 0 OR thread_id IS NOT NULL)
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
                location_country=row["location_country"] if "location_country" in row.keys() else None,
                location_lat=row["location_lat"] if "location_lat" in row.keys() else None,
                location_lng=row["location_lng"] if "location_lng" in row.keys() else None,
                location_state=row["location_state"] if "location_state" in row.keys() else None,
                location_url=row["location_url"] if "location_url" in row.keys() else None,
                location_osm_type=row["location_osm_type"] if "location_osm_type" in row.keys() else None,
                location_osm_id=row["location_osm_id"] if "location_osm_id" in row.keys() else None,
                location_place_id=row["location_place_id"] if "location_place_id" in row.keys() else None,
                fee_from=row["fee_from"] if "fee_from" in row.keys() else row["fee_eur"] if "fee_eur" in row.keys() else None,
                fee_to=row["fee_to"] if "fee_to" in row.keys() else None,
                fee_currency=row["fee_currency"] if "fee_currency" in row.keys() else "EUR" if row["fee_eur"] is not None else None,
                event_type=row["event_type"],
                url=row["url"],
                image_url=row["image_url"],
                formats=json.loads(row["formats_json"] or "[]"),
            )
        )
    return result


def delete_event_by_thread_id(conn: sqlite3.Connection, thread_id: int | str) -> int:
    """Hard-delete a paper event row by thread_id.

    Handles legacy schemas that still contain parent_template_id FK links by
    detaching children first, so events remain independent.
    """
    target_rows = conn.execute(
        "SELECT id FROM paper_events WHERE thread_id = ?",
        (str(thread_id),),
    ).fetchall()
    if not target_rows:
        return 0

    has_parent_template_id = bool(
        conn.execute(
            "SELECT 1 FROM pragma_table_info('paper_events') WHERE name = 'parent_template_id' LIMIT 1"
        ).fetchone()
    )

    if has_parent_template_id:
        for target_row in target_rows:
            conn.execute(
                """
                UPDATE paper_events
                SET parent_template_id = NULL,
                    updated_at = datetime('now')
                WHERE parent_template_id = ?
                """,
                (int(target_row["id"]),),
            )

    cursor = conn.execute(
        "DELETE FROM paper_events WHERE thread_id = ?",
        (str(thread_id),),
    )
    deleted_rows = cursor.rowcount
    conn.commit()
    return deleted_rows


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
                "location_country": item.location_country,
                "location_lat": item.location_lat,
                "location_lng": item.location_lng,
                "location_state": item.location_state,
                "location_url": item.location_url,
                "location_osm_type": item.location_osm_type,
                "location_osm_id": item.location_osm_id,
                "location_place_id": item.location_place_id,
                "fee_from": item.fee_from,
                "fee_to": item.fee_to,
                "fee_currency": item.fee_currency,
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
