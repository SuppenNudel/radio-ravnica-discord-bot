from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, tzinfo, timezone
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import env, notion, paper_events_db


DEFAULT_DATE_PROPERTY = "Start (und Ende)"


@dataclass
class MigrationStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def _get_text(entry: notion.Entry, name: str) -> str | None:
    try:
        return entry.get_text_property(name)
    except Exception:
        return None


def _get_date(entry: notion.Entry, name: str) -> dict[str, Any] | None:
    try:
        return entry.get_date_property(name)
    except Exception:
        return None


def _get_status(entry: notion.Entry, name: str) -> str | None:
    try:
        value = entry.get_status_property(name)
    except Exception:
        return None
    return str(value) if value else None


def _get_multi_select(entry: notion.Entry, name: str) -> list[str]:
    try:
        value = entry.get_multi_select_property(name)
    except Exception:
        return []
    return [str(item) for item in value if item]


def _get_url(entry: notion.Entry, name: str) -> str | None:
    try:
        return entry.get_url_property(name)
    except Exception:
        return None


def _get_file(entry: notion.Entry, name: str) -> str | None:
    try:
        return entry.get_file_property(name)
    except Exception:
        return None


def _get_number(entry: notion.Entry, name: str) -> float | int | None:
    try:
        return entry.get_number_property(name)
    except Exception:
        return None


def _get_formula(entry: notion.Entry, name: str) -> str | None:
    try:
        value = entry.get_formula_property(name)
    except Exception:
        return None
    return str(value) if value else None


def _normalize_discord_user_id(value: str | int | float | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return None
    text = str(value).strip()
    if not text:
        return None
    # Handle mention-like formats: <@123>, <@!123>
    if text.startswith("<@") and text.endswith(">"):
        text = text[2:-1].lstrip("!")
    return text if text.isdigit() else None


def _get_author_id(entry: notion.Entry) -> str | None:
    text_value = _get_text(entry, "Author ID")
    author_id = _normalize_discord_user_id(text_value)
    if author_id:
        return author_id

    number_value = _get_number(entry, "Author ID")
    return _normalize_discord_user_id(number_value)


def _build_title(entry: notion.Entry) -> str | None:
    title = _get_text(entry, "Event Titel")
    if title:
        return title

    formats = _get_multi_select(entry, "Format(e)")
    event_type = _get_status(entry, "Event Typ")
    if formats and event_type:
        return f"{', '.join(formats)} {event_type}"
    if formats:
        return ", ".join(formats)
    return event_type


def _resolve_timezone(name: str | None) -> tzinfo:
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return env.TIMEZONE


def _ensure_aware(value: datetime, fallback_tz: tzinfo) -> datetime:
    if value.tzinfo is not None:
        return value
    localize = getattr(fallback_tz, "localize", None)
    if callable(localize):
        return cast(datetime, localize(value))
    return value.replace(tzinfo=fallback_tz)


def _normalize_datetime(value: datetime | None, *, fallback_tz: tzinfo) -> str | None:
    if value is None:
        return None
    return _ensure_aware(value, fallback_tz).isoformat()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _map_entry_to_row(entry: notion.Entry, *, default_server_id: str | None) -> dict[str, Any] | None:
    date_data = _get_date(entry, DEFAULT_DATE_PROPERTY)
    event_timezone = _resolve_timezone(date_data["tz"] if date_data else None)
    start_at = _normalize_datetime(date_data["start"], fallback_tz=event_timezone) if date_data else None
    if not start_at:
        return None

    title = _build_title(entry)
    if not title:
        return None

    thread_id = _get_text(entry, "Thread ID")
    server_id = _get_text(entry, "Server ID") or default_server_id
    if server_id is None:
        return None

    location_formula = _get_formula(entry, "Google Maps")
    image_url = _get_file(entry, "Eigenes Bild Datei") or _get_url(entry, "Eigenes Bild URL")

    return {
        "server_id": str(server_id),
        "thread_id": thread_id,
        "author_id": _get_author_id(entry),
        "author_name": _get_text(entry, "Author"),
        "title": title,
        "description": _get_text(entry, "Freitext"),
        "start_at": start_at,
        "end_at": _normalize_datetime(date_data["end"], fallback_tz=event_timezone) if date_data else None,
        "timezone": (date_data or {}).get("tz") or str(event_timezone),
        "location_name": _get_text(entry, "Name des Ladens"),
        "location_address": location_formula if location_formula and not location_formula.startswith("http") else None,
        "location_city": _get_text(entry, "Stadt"),
        "location_url": location_formula if location_formula and location_formula.startswith("http") else None,
        "fee_eur": _get_number(entry, "Gebühr"),
        "formats": _get_multi_select(entry, "Format(e)"),
        "event_type": _get_status(entry, "Event Typ"),
        "url": _get_url(entry, "URL"),
        "image_url": image_url,
        "occurrence_key": f"notion:{entry.id}",
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }


def _upsert_by_occurrence_key(conn, row: dict[str, Any]) -> str:
    existing = conn.execute(
        "SELECT id FROM paper_events WHERE occurrence_key = ?",
        (row["occurrence_key"],),
    ).fetchone()

    payload = dict(row)
    payload.pop("for_test", None)
    payload.pop("deleted_at", None)
    payload["formats_json"] = json.dumps(payload.pop("formats", []), ensure_ascii=True)

    if existing:
        row_id = int(existing["id"])
        update_cols = [key for key in payload.keys() if key not in {"occurrence_key", "created_at"}]
        set_clause = ", ".join(f"{column} = ?" for column in update_cols)
        values = [payload[column] for column in update_cols] + [payload["occurrence_key"]]
        conn.execute(
            f"UPDATE paper_events SET {set_clause} WHERE occurrence_key = ?",
            values,
        )
        return f"updated:{row_id}"

    columns = sorted(payload.keys())
    values = [payload[column] for column in columns]
    cursor = conn.execute(
        f"INSERT INTO paper_events ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])})",
        values,
    )
    return f"inserted:{cursor.lastrowid}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate paper events from Notion into SQLite.")
    parser.add_argument("--db-path", default=str(paper_events_db.DEFAULT_DB_PATH), help="Path to the SQLite database.")
    parser.add_argument("--database-id", default=env.EVENT_DATABASE_ID, help="Notion database id to import from.")
    parser.add_argument("--server-id", default=str(env.GUILD_ID) if env.GUILD_ID else None, help="Default Discord server id when the Notion row has no Server ID property.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N Notion rows.")
    parser.add_argument("--dry-run", action="store_true", help="Read and map entries without writing to SQLite.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_id:
        raise SystemExit("Missing --database-id and EVENT_DATABASE_ID is not configured.")

    entries = notion.get_all_entries(database_id=args.database_id)
    if args.limit is not None:
        entries = entries[: args.limit]

    stats = MigrationStats()
    conn = paper_events_db.connect(Path(args.db_path))
    paper_events_db.init_db(conn)

    try:
        for entry in entries:
            row = _map_entry_to_row(entry, default_server_id=args.server_id)
            if row is None:
                stats.skipped += 1
                print(f"skip {entry.id}: missing required fields")
                continue

            if not row.get("author_id"):
                print(f"warn {entry.id}: missing Author ID for '{row['title']}'")

            if args.dry_run:
                stats.inserted += 1
                print(f"dry-run {entry.id}: {row['title']}")
                continue

            result = _upsert_by_occurrence_key(conn, row)
            action, row_id = result.split(":", 1)
            if action == "inserted":
                stats.inserted += 1
            else:
                stats.updated += 1
            print(f"{action} {entry.id} -> row {row_id}: {row['title']}")

        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    print(
        f"done: inserted={stats.inserted} updated={stats.updated} skipped={stats.skipped} total={len(entries)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
