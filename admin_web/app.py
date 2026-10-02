from __future__ import annotations

from datetime import datetime, timedelta
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
RR_DISCORD_BOT_DIR = Path(os.getenv("RR_DISCORD_BOT_DIR", str(BASE_DIR / "rr-discord-bot"))).resolve()
BLACKY_DISCORD_BOT_DIR = Path(os.getenv("BLACKY_DISCORD_BOT_DIR", str(BASE_DIR / "blacky-discord-bot"))).resolve()
TOPCARDS_DIR = BASE_DIR / "mtgtop8-topcards"
DB_PATH = RR_DISCORD_BOT_DIR / "data" / "tournaments.db"
TOURNAMENT_JSON_DIR = RR_DISCORD_BOT_DIR / "tournaments"

DEFAULT_LOG_LINES = 120
MIN_LOG_LINES = 20
MAX_LOG_LINES = 500

BOT_LOG_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "Radio Ravnica Bot",
        "service": "rr-discord-bot.service",
        "file_env": "RR_DISCORD_BOT_LOG_FILE",
        "file_default": str(RR_DISCORD_BOT_DIR / "logs" / "bot.log"),
    },
    {
        "name": "Blacky Bot",
        "service": "blacky-discord-bot.service",
        "file_env": "BLACKY_DISCORD_BOT_LOG_FILE",
        "file_default": str(BLACKY_DISCORD_BOT_DIR / "logs" / "bot.log"),
    },
)

if str(RR_DISCORD_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(RR_DISCORD_BOT_DIR))

from modules import swiss_mtg

WEBAPP_PREFIX = "/" + os.getenv("WEBAPP_PREFIX", "rr-tournaments-web").strip("/")
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path=f"{WEBAPP_PREFIX}/static",
)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
HOME_PANELS_PATH = Path(__file__).resolve().parent / "data" / "home_panels.json"
HOME_PANEL_ICONS: dict[str, str] = {
    "link": "🔗",
    "server": "🖥️",
    "home": "🏠",
    "shield": "🛡️",
    "media": "🎵",
    "game": "🎮",
    "tools": "🛠️",
    "star": "⭐",
}
DEFAULT_HOME_PANELS: tuple[dict[str, str], ...] = (
    {
        "id": "tournament-viewer",
        "title": "Radio Ravnica Tournament Viewer",
        "description": "Browse active and historical tournaments, participants, and Swiss rounds.",
        "url": "/rr-tournaments-web/",
        "icon": "game",
        "image_url": "",
    },
    {
        "id": "bot-logs",
        "title": "Discord Bot Logs",
        "description": "Live and searchable logs for the Radio Ravnica and Blacky Discord bots.",
        "url": f"{WEBAPP_PREFIX}/bot-logs/",
        "icon": "server",
        "image_url": "",
    },
    {
        "id": "lorcana-card-browser",
        "title": "Lorcana Card Browser",
        "description": "Browse and search all Disney Lorcana cards. Filter by color, type, rarity, and more. EN & DE.",
        "url": "/lorcana-scryfall/",
        "icon": "star",
        "image_url": "",
    },
    {
        "id": "home-assistant",
        "title": "Home Assistant",
        "description": "Manage your smart home, automations, and connected devices.",
        "url": "https://ha.raspi.web/",
        "icon": "home",
        "image_url": "",
    },
    {
        "id": "teddycloud",
        "title": "TeddyCloud",
        "description": "Manage your Toniebox content and TeddyCloud settings.",
        "url": "http://192.168.188.50/web",
        "icon": "media",
        "image_url": "",
    },
    {
        "id": "pihole-admin",
        "title": "Pi-hole Admin",
        "description": "Manage network-wide DNS filtering, statistics, and settings.",
        "url": "http://192.168.188.31:8081/admin/",
        "icon": "shield",
        "image_url": "",
    },
)
ui = Blueprint("ui", __name__)


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf_token() -> None:
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, "Invalid CSRF token")


@ui.before_request
def require_admin_login() -> Any:
    if request.endpoint == "ui.admin_login":
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            validate_csrf_token()
        return None

    if not session.get("admin_authenticated"):
        if request.endpoint == "ui.bot_logs_api":
            return jsonify({"error": "Authentication required."}), 401
        next_url = request.full_path.rstrip("?")
        return redirect(url_for("ui.admin_login", next=next_url))

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        validate_csrf_token()


@app.context_processor
def inject_csrf_token() -> dict[str, Any]:
    return {"csrf_token": get_csrf_token}


def safe_next_url(target: str | None) -> str:
    parsed = urlparse(target or "")
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(f"{WEBAPP_PREFIX}/"):
        return url_for("ui.index")
    return target or url_for("ui.index")


@ui.route("/login", methods=["GET", "POST"])
def admin_login() -> str | Any:
    if session.get("admin_authenticated"):
        return redirect(url_for("ui.index"))

    error = None
    if request.method == "POST":
        submitted_username = request.form.get("username", "")
        submitted_password = request.form.get("password", "")
        try:
            password_matches = bool(ADMIN_PASSWORD_HASH) and check_password_hash(
                ADMIN_PASSWORD_HASH,
                submitted_password,
            )
        except ValueError:
            password_matches = False

        if hmac.compare_digest(submitted_username, ADMIN_USERNAME) and password_matches:
            session.clear()
            session.permanent = True
            session["admin_authenticated"] = True
            session["_csrf_token"] = secrets.token_urlsafe(32)
            return redirect(safe_next_url(request.form.get("next")))

        error = "Anmeldung fehlgeschlagen. Bitte Zugangsdaten prüfen."

    return render_template("login.html", error=error, next_url=safe_next_url(request.values.get("next")))


@ui.route("/logout", methods=["POST"])
def admin_logout() -> Any:
    session.clear()
    return redirect(url_for("ui.admin_login"))

TOURNAMENT_FORM_FIELDS: tuple[str, ...] = (
    "name",
    "format_name",
    "organizer_id",
    "description",
    "start_time",
    "guild_id",
    "channel_id",
    "message_id",
    "max_participants",
    "max_rounds",
    "days_per_match",
    "cancelled",
)

FORMAT_ORDER: tuple[str, ...] = (
    "Standard",
    "Pioneer",
    "Premodern",
    "Modern",
    "Legacy",
    "Pauper",
    "Commander",
    "Limited",
    "Other",
)

STATE_LABELS: dict[str, str] = {
    "participate": "Zugesagt",
    "registered": "Zugesagt",
    "tentative": "Unsicher",
    "waitlist": "Warteliste",
    "decline": "Abgesagt",
}


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    return int(stripped)


def _normalize_datetime_local(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    parsed = datetime.strptime(stripped, "%Y-%m-%dT%H:%M")
    return parsed.isoformat(timespec="minutes")


def datetime_local_value(value: str | None) -> str:
    if not value:
        return ""

    normalized = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            if fmt is None:
                parsed = datetime.fromisoformat(normalized)
            else:
                parsed = datetime.strptime(normalized, fmt)
            return parsed.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue

    return value


app.jinja_env.filters["datetime_local_value"] = datetime_local_value


def load_home_panels() -> list[dict[str, str]]:
    try:
        panels = json.loads(HOME_PANELS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [dict(panel) for panel in DEFAULT_HOME_PANELS]
    except json.JSONDecodeError:
        return [dict(panel) for panel in DEFAULT_HOME_PANELS]

    if not isinstance(panels, list):
        return []

    return [
        panel
        for panel in panels
        if isinstance(panel, dict)
        and all(isinstance(panel.get(field), str) for field in ("id", "title", "description", "url"))
    ]


def save_home_panels(panels: list[dict[str, str]]) -> None:
    HOME_PANELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOME_PANELS_PATH.write_text(json.dumps(panels, indent=2) + "\n", encoding="utf-8")


def validate_home_panel(data: Any) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(data, dict):
        return None, "Invalid panel data."

    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    url = str(data.get("url", "")).strip()
    icon = str(data.get("icon", "link")).strip()
    image_url = str(data.get("image_url", "")).strip()
    parsed_url = urlparse(url)
    parsed_image_url = urlparse(image_url)

    if not 1 <= len(title) <= 80:
        return None, "The title must contain 1 to 80 characters."
    if len(description) > 240:
        return None, "The description may contain at most 240 characters."
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return None, "Please enter a valid http or https URL."
    if icon not in HOME_PANEL_ICONS:
        return None, "Please select a valid icon."
    if image_url and (parsed_image_url.scheme not in {"http", "https"} or not parsed_image_url.netloc):
        return None, "Please enter a valid image URL."

    return {
        "id": uuid.uuid4().hex,
        "title": title,
        "description": description,
        "url": url,
        "icon": icon,
        "image_url": image_url,
    }, None


def get_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def pretty_datetime(value: str | None) -> str:
    if not value:
        return "Unbekannt"

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed.strftime("%d.%m.%Y, %H:%M Uhr")
    except (ValueError, TypeError):
        return value


app.jinja_env.filters["pretty_datetime"] = pretty_datetime


def state_label(value: str | None) -> str:
    if value is None:
        return "-"
    return STATE_LABELS.get(value, value)


app.jinja_env.filters["state_label"] = state_label


_URL_PATTERN = re.compile(r"(https?://[^\s<]+)")


def linkify_text(value: str | None) -> Markup:
    if not value:
        return Markup("")

    escaped_text = str(escape(value))
    linked_text = _URL_PATTERN.sub(
        lambda match: (
            f'<a class="discord-link" href="{match.group(1)}" '
            f'target="_blank" rel="noopener noreferrer">{match.group(1)}</a>'
        ),
        escaped_text,
    )
    linked_text = linked_text.replace("\n", "<br>")
    return Markup(linked_text)


app.jinja_env.filters["linkify_text"] = linkify_text


def _tail_text_file(path: Path, line_count: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError as error:
        return f"Could not read log file: {error}"

    if not lines:
        return "No log entries yet."

    return "".join(lines[-line_count:]).rstrip() or "No log entries yet."


def _journalctl_tail(service_name: str, line_count: int) -> tuple[str, bool]:
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                service_name,
                "--no-pager",
                "--output=short-iso",
                "-n",
                str(line_count),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
        )
    except FileNotFoundError:
        return "journalctl not found on this system.", True
    except subprocess.TimeoutExpired:
        return "Timed out while reading logs.", True

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip() or "unknown error"
        return f"Could not read journal logs: {details}", True

    output = result.stdout.strip()
    if not output:
        return "No log entries yet.", False

    return output, False


def get_bot_logs(line_count: int) -> list[dict[str, str | bool]]:
    logs: list[dict[str, str | bool]] = []
    for source in BOT_LOG_SOURCES:
        configured_path = os.getenv(source["file_env"], source["file_default"]).strip()
        log_file = Path(configured_path)
        if configured_path and log_file.exists() and log_file.is_file():
            logs.append(
                {
                    "name": source["name"],
                    "source": f"file: {log_file}",
                    "content": _tail_text_file(log_file, line_count),
                    "has_error": False,
                }
            )
            continue

        journal_output, has_error = _journalctl_tail(source["service"], line_count)
        logs.append(
            {
                "name": source["name"],
                "source": f"journalctl unit: {source['service']}",
                "content": journal_output,
                "has_error": has_error,
            }
        )

    return logs


def parse_log_line_count(raw_value: str | None) -> int:
    value = (raw_value or "").strip()
    try:
        line_count = int(value) if value else DEFAULT_LOG_LINES
    except ValueError:
        line_count = DEFAULT_LOG_LINES

    return max(MIN_LOG_LINES, min(MAX_LOG_LINES, line_count))


def get_participant_counts(connection: sqlite3.Connection, tournament_id: int) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT state, COUNT(*) AS count
        FROM participants
        WHERE tournament_id = ?
        GROUP BY state
        """,
        (tournament_id,),
    ).fetchall()

    counts = {"participate": 0, "tentative": 0, "decline": 0, "waitlist": 0}
    for row in rows:
        state = row["state"]
        if state in counts:
            counts[state] = row["count"]

    if sum(counts.values()) == 0:
        swiss_count_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM swiss_players sp
            JOIN swiss_tournaments st ON st.tournament_id = sp.swiss_tournament_id
            WHERE st.tournament_id = ?
            """,
            (tournament_id,),
        ).fetchone()
        swiss_count = int(swiss_count_row["count"]) if swiss_count_row is not None else 0
        if swiss_count > 0:
            counts["participate"] = swiss_count

    return counts


def get_tournament_form_data(source: Any | None = None) -> dict[str, str]:
    if source is None:
        return {
            "name": "",
            "format_name": "Other",
            "organizer_id": "",
            "description": "",
            "start_time": "",
            "guild_id": "",
            "channel_id": "",
            "message_id": "",
            "max_participants": "",
            "max_rounds": "",
            "days_per_match": "",
            "cancelled": "0",
            "winner_player_id": "",
        }

    if isinstance(source, sqlite3.Row):
        db_format_name = "Other"
        if "format_name" in source.keys() and source["format_name"]:
            db_format_name = str(source["format_name"]).strip() or "Other"

        return {
            "name": source["name"] or "",
            "format_name": db_format_name,
            "organizer_id": "" if source["organizer_id"] is None else str(source["organizer_id"]),
            "description": source["description"] or "",
            "start_time": datetime_local_value(source["start_time"]),
            "guild_id": "" if source["guild_id"] is None else str(source["guild_id"]),
            "channel_id": "" if source["channel_id"] is None else str(source["channel_id"]),
            "message_id": "" if source["message_id"] is None else str(source["message_id"]),
            "max_participants": "" if source["max_participants"] is None else str(source["max_participants"]),
            "max_rounds": "" if source["max_rounds"] is None else str(source["max_rounds"]),
            "days_per_match": "" if source["days_per_match"] is None else str(source["days_per_match"]),
            "cancelled": "1" if source["cancelled"] else "0",
            "winner_player_id": "",  # Winner is in swiss_tournaments, not tournaments
        }

    return {
        "name": (source.get("name") or "").strip(),
        "format_name": (source.get("format_name") or "Other").strip(),
        "organizer_id": (source.get("organizer_id") or "").strip(),
        "description": (source.get("description") or "").strip(),
        "start_time": (source.get("start_time") or "").strip(),
        "guild_id": (source.get("guild_id") or "").strip(),
        "channel_id": (source.get("channel_id") or "").strip(),
        "message_id": (source.get("message_id") or "").strip(),
        "max_participants": (source.get("max_participants") or "").strip(),
        "max_rounds": (source.get("max_rounds") or "").strip(),
        "days_per_match": (source.get("days_per_match") or "").strip(),
        "cancelled": "1" if source.get("cancelled") else "0",
        "winner_player_id": (source.get("winner_player_id") or "").strip(),
    }


def validate_tournament_form(form_data: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []

    name = form_data["name"]
    if not name:
        errors.append("Name is required.")

    parsed: dict[str, Any] = {"name": name}

    format_name = form_data["format_name"] or "Other"
    if format_name not in FORMAT_ORDER:
        errors.append("Format must be one of the supported options.")
        format_name = "Other"
    parsed["format_name"] = format_name

    int_fields = (
        "organizer_id",
        "guild_id",
        "channel_id",
        "message_id",
        "max_participants",
        "max_rounds",
        "days_per_match",
        "winner_player_id",
    )
    for field_name in int_fields:
        try:
            parsed[field_name] = _optional_int(form_data[field_name])
        except ValueError:
            errors.append(f"{field_name.replace('_', ' ').title()} must be a whole number.")

    parsed["description"] = form_data["description"] or None

    try:
        parsed["start_time"] = _normalize_datetime_local(form_data["start_time"])
    except ValueError:
        errors.append("Start time must be a valid date and time.")

    parsed["cancelled"] = 1 if form_data["cancelled"] == "1" else 0
    return parsed, errors


def get_user_choices() -> list[dict[str, str]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT user_id, display_name
            FROM users
            WHERE display_name IS NOT NULL
              AND TRIM(display_name) != ''
            ORDER BY LOWER(display_name), user_id
            """
        ).fetchall()

    return [
        {
            "user_id": str(row["user_id"]),
            "display_name": str(row["display_name"]),
            "label": f"{row['display_name']} ({row['user_id']})",
        }
        for row in rows
    ]


def enrich_form_data_with_user_label(
    form_data: dict[str, str], user_choices: list[dict[str, str]]
) -> dict[str, str]:
    enriched = dict(form_data)
    organizer_id = enriched.get("organizer_id", "")
    organizer_label = organizer_id

    if organizer_id:
        for choice in user_choices:
            if choice["user_id"] == organizer_id:
                organizer_label = choice["label"]
                break

    enriched["organizer_lookup"] = organizer_label
    return enriched


def render_tournament_form(
    *,
    form_data: dict[str, str],
    form_mode: str,
    form_title: str,
    submit_label: str,
    tournament_id: int | None = None,
) -> str:
    user_choices = get_user_choices()
    winner_candidates = []

    # Get potential winners from swiss_players for this tournament
    if tournament_id is not None:
        with get_connection() as connection:
            swiss_player_rows = connection.execute(
                """
                SELECT DISTINCT sp.player_id, u.display_name
                FROM swiss_players sp
                LEFT JOIN users u ON u.user_id = sp.player_id
                WHERE sp.swiss_tournament_id = ?
                ORDER BY COALESCE(u.display_name, sp.player_id)
                """,
                (tournament_id,),
            ).fetchall()

            winner_candidates = [
                {
                    "player_id": str(row["player_id"]),
                    "display_name": row["display_name"] or str(row["player_id"]),
                    "label": f"{row['display_name'] or row['player_id']} ({row['player_id']})",
                }
                for row in swiss_player_rows
            ]

    return render_template(
        "tournament_form.html",
        form_data=enrich_form_data_with_user_label(form_data, user_choices),
        format_choices=FORMAT_ORDER,
        form_mode=form_mode,
        form_title=form_title,
        submit_label=submit_label,
        tournament_id=tournament_id,
        user_choices=user_choices,
        winner_candidates=winner_candidates,
    )


def validate_match_result_form(form: Any, allow_bye: bool) -> tuple[dict[str, int | None], list[str]]:
    errors: list[str] = []

    def parse_non_negative(field_name: str) -> int:
        raw_value = (form.get(field_name) or "").strip()
        if raw_value == "":
            return 0

        try:
            value = int(raw_value)
        except ValueError:
            errors.append(f"{field_name.replace('_', ' ').title()} must be a whole number.")
            return 0

        if value < 0:
            errors.append(f"{field_name.replace('_', ' ').title()} cannot be negative.")
            return 0
        return value

    player1_wins = parse_non_negative("player1_wins")
    player2_wins = parse_non_negative("player2_wins")
    draws = parse_non_negative("draws")

    if allow_bye:
        if player2_wins != 0 or draws != 0:
            errors.append("Bye matches can only record wins for player 1.")
        if player1_wins not in {0, 2}:
            errors.append("Bye matches should be pending or recorded as 2 wins for player 1.")

    return {
        "player1_wins": player1_wins,
        "player2_wins": player2_wins,
        "draws": draws,
    }, errors


def update_swiss_winner(connection: sqlite3.Connection, tournament_id: int) -> None:
    tournament = connection.execute(
        "SELECT tournament_id, rounds_count FROM swiss_tournaments WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    if tournament is None:
        return

    round_rows = connection.execute(
        """
        SELECT id, round_number
        FROM swiss_rounds
        WHERE swiss_tournament_id = ?
        ORDER BY round_number ASC
        """,
        (tournament_id,),
    ).fetchall()

    rounds: list[dict[str, Any]] = []
    has_pending_results = False
    for round_row in round_rows:
        matches = connection.execute(
            """
            SELECT id, player1_id, player2_id, player1_wins, player2_wins, draws
            FROM swiss_matches
            WHERE swiss_round_id = ?
            ORDER BY id ASC
            """,
            (round_row["id"],),
        ).fetchall()
        rounds.append({"round": round_row, "matches": matches})
        if any(_is_pending_match_result(match) for match in matches if match["player2_id"] is not None):
            has_pending_results = True

    if not rounds or has_pending_results:
        connection.execute(
            "UPDATE swiss_tournaments SET winner_player_id = NULL WHERE tournament_id = ?",
            (tournament_id,),
        )
        return

    swiss_player_rows = connection.execute(
        "SELECT player_id, deck_link, deck_archetype, dropped FROM swiss_players WHERE swiss_tournament_id = ?",
        (tournament_id,),
    ).fetchall()
    swiss_player_by_id = {
        int(row["player_id"]): {
            "deck_link": row["deck_link"],
            "deck_archetype": row["deck_archetype"],
        }
        for row in swiss_player_rows
    }
    dropped_player_ids = {int(row["player_id"]) for row in swiss_player_rows if row["dropped"]}

    standings = _calculate_final_standings(rounds, swiss_player_by_id, dropped_player_ids)
    winner_player_id = standings[0]["player_id"] if standings else None
    connection.execute(
        "UPDATE swiss_tournaments SET winner_player_id = ? WHERE tournament_id = ?",
        (winner_player_id, tournament_id),
    )


def validate_round_form(form: Any) -> tuple[dict[str, int | None], list[str]]:
    errors: list[str] = []
    round_number_raw = (form.get("round_number") or "").strip()
    message_id_pairings_raw = (form.get("message_id_pairings") or "").strip()
    message_id_standings_raw = (form.get("message_id_standings") or "").strip()

    if not round_number_raw:
        errors.append("Round number is required.")
        round_number = 0
    else:
        try:
            round_number = int(round_number_raw)
        except ValueError:
            errors.append("Round number must be a whole number.")
            round_number = 0

    if round_number < 1:
        errors.append("Round number must be at least 1.")

    try:
        message_id_pairings = _optional_int(message_id_pairings_raw)
    except ValueError:
        errors.append("Pairings message ID must be a whole number.")
        message_id_pairings = None

    try:
        message_id_standings = _optional_int(message_id_standings_raw)
    except ValueError:
        errors.append("Standings message ID must be a whole number.")
        message_id_standings = None

    return {
        "round_number": round_number,
        "message_id_pairings": message_id_pairings,
        "message_id_standings": message_id_standings,
    }, errors


def validate_match_form(form: Any) -> tuple[dict[str, int | None], list[str]]:
    errors: list[str] = []
    player1_id_raw = (form.get("player1_id") or "").strip()
    player2_id_raw = (form.get("player2_id") or "").strip()

    def parse_player_id(raw_value: str, field_label: str) -> int | None:
        if not raw_value:
            return None

        explicit_id_match = re.search(r"\(([-0-9]+)\)$", raw_value)
        candidate = explicit_id_match.group(1) if explicit_id_match else raw_value
        try:
            return int(candidate)
        except ValueError:
            errors.append(f"{field_label} must be a valid user ID.")
            return None

    if not player1_id_raw:
        errors.append("Player 1 is required.")
        player1_id = None
    else:
        player1_id = parse_player_id(player1_id_raw, "Player 1")

    if not player2_id_raw:
        player2_id = None
    else:
        player2_id = parse_player_id(player2_id_raw, "Player 2")

    if player1_id is not None and player2_id is not None and player1_id == player2_id:
        errors.append("Player 1 and Player 2 must be different users.")

    result_values, result_errors = validate_match_result_form(form, allow_bye=player2_id is None)
    errors.extend(result_errors)

    return {
        "player1_id": player1_id,
        "player2_id": player2_id,
        "player1_wins": int(result_values["player1_wins"] or 0),
        "player2_wins": int(result_values["player2_wins"] or 0),
        "draws": int(result_values["draws"] or 0),
    }, errors


def ensure_swiss_tournament(connection: sqlite3.Connection, tournament_id: int) -> None:
    existing = connection.execute(
        "SELECT tournament_id FROM swiss_tournaments WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO swiss_tournaments (tournament_id, rounds_count, winner_player_id) VALUES (?, 0, NULL)",
            (tournament_id,),
        )


def sync_swiss_round_count(connection: sqlite3.Connection, tournament_id: int) -> None:
    round_count_row = connection.execute(
        "SELECT COUNT(*) AS count FROM swiss_rounds WHERE swiss_tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    round_count = int(round_count_row["count"]) if round_count_row is not None else 0
    connection.execute(
        "UPDATE swiss_tournaments SET rounds_count = ? WHERE tournament_id = ?",
        (round_count, tournament_id),
    )


def validate_swiss_player_form(form: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    player_id_raw = (form.get("player_id") or "").strip()
    dropped = bool(form.get("dropped"))
    deck_link = (form.get("deck_link") or "").strip() or None
    deck_archetype = (form.get("deck_archetype") or "").strip() or None

    explicit_id_match = re.search(r"\(([-0-9]+)\)$", player_id_raw)
    candidate = explicit_id_match.group(1) if explicit_id_match else player_id_raw

    if not candidate:
        errors.append("Player is required.")
        player_id = None
    else:
        try:
            player_id = int(candidate)
        except ValueError:
            errors.append("Player must be a valid user ID.")
            player_id = None

    return {
        "player_id": player_id,
        "dropped": dropped,
        "deck_link": deck_link,
        "deck_archetype": deck_archetype,
    }, errors


@ui.route("/tournament/<int:tournament_id>/player/search", methods=["GET"])
def tournament_player_search(tournament_id: int) -> str:
    """Search for players by name or ID for autocomplete."""
    query = request.args.get("q", "").strip().lower()

    if not query or len(query) < 2:
        return json.dumps([])

    with get_connection() as connection:
        # Search in both display_name and username, case-insensitive
        results = connection.execute(
            """
            SELECT DISTINCT user_id, display_name, username
            FROM users
            WHERE (LOWER(display_name) LIKE ? OR LOWER(username) LIKE ?)
            AND display_name IS NOT NULL
            LIMIT 20
            """,
            (f"%{query}%", f"%{query}%"),
        ).fetchall()

        # Format results for autocomplete
        options = [
            {
                "id": row["user_id"],
                "label": f"{row['display_name']} ({row['user_id']})",
                "value": row["display_name"],
            }
            for row in results
        ]

    return json.dumps(options)


@ui.route("/tournament/<int:tournament_id>/player/new", methods=["POST"])
def tournament_player_create(tournament_id: int) -> str:
    with get_connection() as connection:
        tournament = connection.execute(
            "SELECT id FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        if tournament is None:
            abort(404)

        ensure_swiss_tournament(connection, tournament_id)
        player_id_raw = (request.form.get("player_id") or "").strip()
        dropped = bool(request.form.get("dropped"))
        deck_link = (request.form.get("deck_link") or "").strip() or None
        deck_archetype = (request.form.get("deck_archetype") or "").strip() or None

        errors: list[str] = []
        player_id = None

        if not player_id_raw:
            errors.append("Player is required.")
        else:
            # First try to extract explicit ID from format "Name (ID)"
            explicit_id_match = re.search(r"\(([-0-9]+)\)$", player_id_raw)
            if explicit_id_match:
                try:
                    player_id = int(explicit_id_match.group(1))
                except ValueError:
                    errors.append("Could not parse user ID from input.")
            else:
                # No explicit ID found, try to parse as pure number
                try:
                    player_id = int(player_id_raw)
                except ValueError:
                    # Not a number - try to look up by display_name or username (case-insensitive)
                    user_row = connection.execute(
                        """
                        SELECT user_id FROM users
                        WHERE LOWER(display_name) = LOWER(?)
                           OR LOWER(username) = LOWER(?)
                        LIMIT 1
                        """,
                        (player_id_raw, player_id_raw),
                    ).fetchone()
                    if user_row:
                        try:
                            player_id = int(user_row["user_id"])
                        except (ValueError, TypeError):
                            errors.append("Could not parse user ID from database.")
                    else:
                        errors.append(f"User '{player_id_raw}' not found. Try entering the User ID or exact display name.")

        if not errors and player_id is not None:
            existing = connection.execute(
                "SELECT id FROM swiss_players WHERE swiss_tournament_id = ? AND player_id = ?",
                (tournament_id, str(player_id)),
            ).fetchone()
            if existing is not None:
                errors.append("That player is already registered for this tournament.")

        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id))

        connection.execute(
            """
            INSERT INTO swiss_players (
                swiss_tournament_id,
                player_id,
                dropped,
                deck_link,
                deck_archetype
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                tournament_id,
                str(player_id),
                1 if dropped else 0,
                deck_link,
                deck_archetype,
            ),
        )

    flash("Swiss player added.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, _anchor="player-form"))


@ui.route("/tournament/<int:tournament_id>/player/<int:player_id>/dropped", methods=["POST"])
def tournament_player_dropped_update(tournament_id: int, player_id: int) -> str:
    page_raw = request.form.get("page", "0")
    try:
        page = int(page_raw)
    except ValueError:
        page = 0

    with get_connection() as connection:
        player_row = connection.execute(
            """
            SELECT id, dropped
            FROM swiss_players
            WHERE swiss_tournament_id = ? AND player_id = ?
            """,
            (tournament_id, player_id),
        ).fetchone()

        if player_row is None:
            abort(404)

        new_dropped = 0 if int(player_row["dropped"] or 0) else 1
        connection.execute(
            """
            UPDATE swiss_players
            SET dropped = ?
            WHERE swiss_tournament_id = ? AND player_id = ?
            """,
            (new_dropped, tournament_id, player_id),
        )

    flash("Player drop status updated.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=page))


@ui.route("/tournament/<int:tournament_id>/participant/<int:player_id>/delete", methods=["POST"])
def tournament_participant_delete(tournament_id: int, player_id: int) -> str:
    page_raw = request.form.get("page", "0")
    try:
        page = int(page_raw)
    except ValueError:
        page = 0

    with get_connection() as connection:
        tournament = connection.execute(
            "SELECT id FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        if tournament is None:
            abort(404)

        connection.execute(
            "DELETE FROM participants WHERE tournament_id = ? AND user_id = ?",
            (tournament_id, player_id),
        )
        connection.execute(
            "DELETE FROM swiss_players WHERE swiss_tournament_id = ? AND player_id = ?",
            (tournament_id, player_id),
        )

    flash("Participant removed.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=page))


@ui.route("/tournament/<int:tournament_id>/round/new", methods=["POST"])
def tournament_round_create(tournament_id: int) -> str:
    with get_connection() as connection:
        tournament = connection.execute(
            "SELECT id FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        if tournament is None:
            abort(404)

        ensure_swiss_tournament(connection, tournament_id)
        parsed, errors = validate_round_form(request.form)
        if not errors:
            existing_round = connection.execute(
                "SELECT id FROM swiss_rounds WHERE swiss_tournament_id = ? AND round_number = ?",
                (tournament_id, parsed["round_number"]),
            ).fetchone()
            if existing_round is not None:
                errors.append("That round number already exists for this tournament.")

        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id))

        connection.execute(
            """
            INSERT INTO swiss_rounds (
                swiss_tournament_id,
                round_number,
                message_id_pairings,
                message_id_standings
            ) VALUES (?, ?, ?, ?)
            """,
            (
                tournament_id,
                parsed["round_number"],
                parsed["message_id_pairings"],
                parsed["message_id_standings"],
            ),
        )
        sync_swiss_round_count(connection, tournament_id)

    flash("Round created.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=parsed["round_number"]))


@ui.route("/tournament/<int:tournament_id>/round/<int:round_id>/edit", methods=["POST"])
def tournament_round_edit(tournament_id: int, round_id: int) -> str:
    with get_connection() as connection:
        round_row = connection.execute(
            "SELECT id, round_number, swiss_tournament_id FROM swiss_rounds WHERE id = ?",
            (round_id,),
        ).fetchone()
        if round_row is None or int(round_row["swiss_tournament_id"]) != tournament_id:
            abort(404)

        parsed, errors = validate_round_form(
            {
                "round_number": str(round_row["round_number"]),
                "message_id_pairings": request.form.get("message_id_pairings", ""),
                "message_id_standings": request.form.get("message_id_standings", ""),
            }
        )
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            connection.execute(
                """
                UPDATE swiss_rounds
                SET message_id_pairings = ?,
                    message_id_standings = ?
                WHERE id = ?
                """,
                (parsed["message_id_pairings"], parsed["message_id_standings"], round_id),
            )
            flash("Round updated.", "success")

    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=round_row["round_number"]))


@ui.route("/tournament/<int:tournament_id>/round/<int:round_id>/match/new", methods=["POST"])
def tournament_match_create(tournament_id: int, round_id: int) -> str:
    with get_connection() as connection:
        swiss_matches_columns = get_table_columns(connection, "swiss_matches")
        round_fk_column = "swiss_round_id" if "swiss_round_id" in swiss_matches_columns else "round_id"
        round_row = connection.execute(
            "SELECT id, round_number, swiss_tournament_id FROM swiss_rounds WHERE id = ?",
            (round_id,),
        ).fetchone()
        if round_row is None or int(round_row["swiss_tournament_id"]) != tournament_id:
            abort(404)

        parsed, errors = validate_match_form(request.form)

        player_rows = connection.execute(
            "SELECT player_id FROM swiss_players WHERE swiss_tournament_id = ?",
            (tournament_id,),
        ).fetchall()
        allowed_player_ids = {int(row["player_id"]) for row in player_rows if row["player_id"] is not None}

        if parsed["player1_id"] is not None and parsed["player1_id"] not in allowed_player_ids:
            errors.append("Player 1 is not registered in swiss_players for this tournament.")
        if parsed["player2_id"] is not None and parsed["player2_id"] not in allowed_player_ids:
            errors.append("Player 2 is not registered in swiss_players for this tournament.")

        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=round_row["round_number"]))

        connection.execute(
            f"""
            INSERT INTO swiss_matches (
                {round_fk_column},
                player1_id,
                player2_id,
                player1_wins,
                player2_wins,
                draws
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                round_id,
                parsed["player1_id"],
                parsed["player2_id"],
                parsed["player1_wins"],
                parsed["player2_wins"],
                parsed["draws"],
            ),
        )
        update_swiss_winner(connection, tournament_id)

    flash("Match created.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=round_row["round_number"], _anchor="match-form"))


def get_dropped_player_ids(connection: sqlite3.Connection, tournament: sqlite3.Row) -> set[int]:
    dropped_ids_from_db = {
        int(row["player_id"])
        for row in connection.execute(
            """
            SELECT sp.player_id
            FROM swiss_players sp
                        JOIN swiss_tournaments st ON st.tournament_id = sp.swiss_tournament_id
            WHERE st.tournament_id = ?
              AND sp.dropped = 1
            """,
            (tournament["id"],),
        ).fetchall()
    }

    json_path = TOURNAMENT_JSON_DIR / (
        f"{tournament['guild_id']}_{tournament['channel_id']}_{tournament['message_id']}.json"
    )
    if not json_path.exists():
        return dropped_ids_from_db

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dropped_ids_from_db

    swiss_tournament = data.get("tournament")
    if not isinstance(swiss_tournament, dict):
        return dropped_ids_from_db

    players = swiss_tournament.get("players")
    if not isinstance(players, list):
        return dropped_ids_from_db

    dropped_ids: set[int] = set()
    for player in players:
        if not isinstance(player, dict) or not player.get("dropped"):
            continue

        player_id = player.get("player_id")
        if isinstance(player_id, int):
            dropped_ids.add(player_id)

    return dropped_ids | dropped_ids_from_db


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_pending_match_result(match: Any) -> bool:
    player2_id = match["player2_id"]
    if player2_id is None:
        return False

    player1_wins = _safe_int(match["player1_wins"])
    player2_wins = _safe_int(match["player2_wins"])
    draws = _safe_int(match["draws"])
    return player1_wins == 0 and player2_wins == 0 and draws == 0


def _calculate_final_standings(
    rounds: list[dict[str, Any]],
    swiss_player_by_id: dict[int, dict[str, Any]],
    dropped_player_ids: set[int],
) -> list[dict[str, Any]]:
    player_ids: set[int] = set(swiss_player_by_id.keys())
    for round_entry in rounds:
        for match in round_entry["matches"]:
            if match["player1_id"] is not None:
                player_ids.add(int(match["player1_id"]))
            if match["player2_id"] is not None:
                player_ids.add(int(match["player2_id"]))

    if not player_ids:
        return []

    sorted_player_ids = sorted(player_ids)
    players = [swiss_mtg.Player(player_id) for player_id in sorted_player_ids]
    player_by_id = {player.player_id: player for player in players}

    for player_id in dropped_player_ids:
        player = player_by_id.get(player_id)
        if player is not None:
            player.dropped = True

    for round_entry in rounds:
        round_number = _safe_int(round_entry["round"]["round_number"])
        reconstructed_round = swiss_mtg.Round(round_number)

        for match_row in round_entry["matches"]:
            player1_id_raw = match_row["player1_id"]
            if player1_id_raw is None:
                continue

            player1_id = int(player1_id_raw)
            player2_id_raw = match_row["player2_id"]
            player2_id = int(player2_id_raw) if player2_id_raw is not None else None

            player1 = player_by_id.get(player1_id)
            if player1 is None:
                continue
            player2 = player_by_id.get(player2_id) if player2_id is not None else None

            try:
                match = swiss_mtg.Match(player1, player2, round_number)
            except ValueError:
                # Duplicate pairing — can occur with legacy-imported tournaments
                # where all rounds were collapsed into one. Skip the duplicate.
                continue
            if player2 is not None and not _is_pending_match_result(match_row):
                match.set_result(
                    _safe_int(match_row["player1_wins"]),
                    _safe_int(match_row["player2_wins"]),
                    _safe_int(match_row["draws"]),
                )

            reconstructed_round.add_match(match)
    standings: list[dict[str, Any]] = []
    for player in players:
        wins, losses, draws = [int(part) for part in player.get_match_results().split("-")]
        player_id = int(player.player_id)
        standings.append(
            {
                "rank": 0,
                "player_id": player_id,
                "match_points": player.calculate_match_points(),
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "omw": player.calculate_opponent_match_win_percentage(),
                "gw": player.calculate_game_win_percentage(),
                "ogw": player.calculate_opponent_game_win_percentage(),
                "dropped": player.dropped,
                "deck_link": swiss_player_by_id.get(player_id, {}).get("deck_link"),
                "deck_archetype": swiss_player_by_id.get(player_id, {}).get("deck_archetype"),
            }
        )

    standings.sort(
        key=lambda standing: (
            -standing["match_points"],
            -standing["omw"],
            -standing["gw"],
            -standing["ogw"],
            standing["player_id"],
        )
    )

    for index, standing in enumerate(standings, start=1):
        standing["rank"] = index

    return standings


@ui.route("/")
def index() -> str:
    show_cancelled = request.args.get("show_cancelled", "0") == "1"
    sort_order = request.args.get("sort", "oldest")
    search_query = request.args.get("q", "").strip()
    selected_format = request.args.get("format", "all").strip()
    if sort_order not in {"oldest", "newest"}:
        sort_order = "oldest"

    with get_connection() as connection:
        tournament_columns = get_table_columns(connection, "tournaments")
        query = """
            SELECT
                t.*,
                st.rounds_count,
                st.winner_player_id
            FROM tournaments t
            LEFT JOIN swiss_tournaments st ON st.tournament_id = t.id
        """
        params: list[Any] = []
        where_clauses: list[str] = []
        if not show_cancelled and "cancelled" in tournament_columns:
            where_clauses.append("COALESCE(t.cancelled, 0) = 0")
        if search_query:
            where_clauses.append("LOWER(COALESCE(t.name, '')) LIKE ?")
            params.append(f"%{search_query.lower()}%")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        if sort_order == "newest":
            query += " ORDER BY t.start_time IS NULL, t.start_time DESC, t.id DESC"
        else:
            query += " ORDER BY t.start_time IS NULL, t.start_time ASC, t.id ASC"

        tournaments = connection.execute(query, params).fetchall()

        enriched: list[dict[str, Any]] = []
        available_formats: set[str] = set()
        for tournament in tournaments:
            counts = get_participant_counts(connection, tournament["id"])
            format_name = "Other"
            if "format_name" in tournament.keys() and tournament["format_name"]:
                format_name = str(tournament["format_name"]).strip() or "Other"
            available_formats.add(format_name)
            if selected_format != "all" and format_name != selected_format:
                continue

            enriched.append(
                {
                    "tournament": tournament,
                    "counts": counts,
                    "format_name": format_name,
                }
            )

    format_order_rank = {format_name: index for index, format_name in enumerate(FORMAT_ORDER)}
    ordered_formats = sorted(
        available_formats,
        key=lambda format_name: (format_order_rank.get(format_name, 999), format_name),
    )
    if selected_format != "all" and selected_format not in available_formats:
        selected_format = "all"

    return render_template(
        "index.html",
        tournaments=enriched,
        available_formats=ordered_formats,
        selected_format=selected_format,
        search_query=search_query,
        show_cancelled=show_cancelled,
        sort_order=sort_order,
    )


@ui.route("/tournament/new", methods=["GET", "POST"])
def tournament_create() -> str:
    form_data = get_tournament_form_data(request.form if request.method == "POST" else None)
    if request.method == "POST":
        parsed, errors = validate_tournament_form(form_data)
        if not errors:
            now = datetime.utcnow().isoformat(timespec="seconds")
            try:
                with get_connection() as connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO tournaments (
                            name,
                            format_name,
                            organizer_id,
                            description,
                            start_time,
                            created_at,
                            guild_id,
                            channel_id,
                            message_id,
                            max_participants,
                            max_rounds,
                            days_per_match,
                            cancelled
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            parsed["name"],
                            parsed["format_name"],
                            parsed["organizer_id"],
                            parsed["description"],
                            parsed["start_time"],
                            now,
                            parsed["guild_id"],
                            parsed["channel_id"],
                            parsed["message_id"],
                            parsed["max_participants"],
                            parsed["max_rounds"],
                            parsed["days_per_match"],
                            parsed["cancelled"],
                        ),
                    )
                    tournament_id = int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
                flash(f"Could not create tournament: {exc}.", "error")
            else:
                flash("Tournament created.", "success")
                return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id))

        for error in errors:
            flash(error, "error")

    return render_tournament_form(
        form_data=form_data,
        form_mode="create",
        form_title="Add Tournament",
        submit_label="Create Tournament",
    )


@ui.route("/tournament/<int:tournament_id>/edit", methods=["GET", "POST"])
def tournament_edit(tournament_id: int) -> str:
    with get_connection() as connection:
        tournament = connection.execute(
            "SELECT * FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()

        if tournament is None:
            abort(404)

        # Get the current winner from swiss_tournaments if it exists
        swiss_tournament = connection.execute(
            "SELECT winner_player_id FROM swiss_tournaments WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()

    form_data = get_tournament_form_data(tournament)
    if swiss_tournament and swiss_tournament["winner_player_id"]:
        form_data["winner_player_id"] = str(swiss_tournament["winner_player_id"])

    if request.method == "POST":
        form_data = get_tournament_form_data(request.form)
        parsed, errors = validate_tournament_form(form_data)
        if not errors:
            try:
                with get_connection() as connection:
                    connection.execute(
                        """
                        UPDATE tournaments
                        SET name = ?,
                            format_name = ?,
                            organizer_id = ?,
                            description = ?,
                            start_time = ?,
                            guild_id = ?,
                            channel_id = ?,
                            message_id = ?,
                            max_participants = ?,
                            max_rounds = ?,
                            days_per_match = ?,
                            cancelled = ?
                        WHERE id = ?
                        """,
                        (
                            parsed["name"],
                            parsed["format_name"],
                            parsed["organizer_id"],
                            parsed["description"],
                            parsed["start_time"],
                            parsed["guild_id"],
                            parsed["channel_id"],
                            parsed["message_id"],
                            parsed["max_participants"],
                            parsed["max_rounds"],
                            parsed["days_per_match"],
                            parsed["cancelled"],
                            tournament_id,
                        ),
                    )

                    # Update winner if present
                    if parsed["winner_player_id"] is not None:
                        # Ensure swiss_tournament record exists
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO swiss_tournaments
                            (tournament_id, rounds_count, winner_player_id)
                            VALUES (?, 0, NULL)
                            """,
                            (tournament_id,),
                        )
                        connection.execute(
                            "UPDATE swiss_tournaments SET winner_player_id = ? WHERE tournament_id = ?",
                            (str(parsed["winner_player_id"]) if parsed["winner_player_id"] else None, tournament_id),
                        )
            except sqlite3.IntegrityError as exc:
                flash(f"Could not save tournament: {exc}.", "error")
            else:
                flash("Tournament updated.", "success")
                return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id))

        else:
            for error in errors:
                flash(error, "error")

    return render_tournament_form(
        form_data=form_data,
        form_mode="edit",
        form_title="Edit Tournament",
        submit_label="Save Changes",
        tournament_id=tournament_id,
    )


@ui.route("/tournament/<int:tournament_id>/match/<int:match_id>/result", methods=["POST"])
def tournament_match_result_update(tournament_id: int, match_id: int) -> str:
    page_raw = request.form.get("page", "0")
    try:
        page = int(page_raw)
    except ValueError:
        page = 0

    with get_connection() as connection:
        swiss_matches_columns = get_table_columns(connection, "swiss_matches")
        round_fk_column = "swiss_round_id" if "swiss_round_id" in swiss_matches_columns else "round_id"
        match_row = connection.execute(
            f"""
            SELECT sm.id, sm.player1_id, sm.player2_id, sm.player1_wins, sm.player2_wins, sm.draws,
                   sr.id AS swiss_round_id, sr.round_number, sr.swiss_tournament_id
            FROM swiss_matches sm
            JOIN swiss_rounds sr ON sr.id = sm.{round_fk_column}
            WHERE sm.id = ?
            """,
            (match_id,),
        ).fetchone()

        if match_row is None or int(match_row["swiss_tournament_id"]) != tournament_id:
            abort(404)

        parsed, errors = validate_match_result_form(request.form, allow_bye=match_row["player2_id"] is None)
        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=page))

        connection.execute(
            """
            UPDATE swiss_matches
            SET player1_wins = ?,
                player2_wins = ?,
                draws = ?
            WHERE id = ?
            """,
            (
                parsed["player1_wins"],
                parsed["player2_wins"],
                parsed["draws"],
                match_id,
            ),
        )
        update_swiss_winner(connection, tournament_id)

    flash("Match result updated.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=page))


@ui.route("/tournament/<int:tournament_id>/match/<int:match_id>/delete", methods=["POST"])
def tournament_match_delete(tournament_id: int, match_id: int) -> str:
    page_raw = request.form.get("page", "0")
    try:
        page = int(page_raw)
    except ValueError:
        page = 0

    with get_connection() as connection:
        swiss_matches_columns = get_table_columns(connection, "swiss_matches")
        round_fk_column = "swiss_round_id" if "swiss_round_id" in swiss_matches_columns else "round_id"
        match_row = connection.execute(
            f"""
            SELECT sm.id, sr.round_number, sr.swiss_tournament_id
            FROM swiss_matches sm
            JOIN swiss_rounds sr ON sr.id = sm.{round_fk_column}
            WHERE sm.id = ?
            """,
            (match_id,),
        ).fetchone()

        if match_row is None or int(match_row["swiss_tournament_id"]) != tournament_id:
            abort(404)

        connection.execute("DELETE FROM swiss_matches WHERE id = ?", (match_id,))
        update_swiss_winner(connection, tournament_id)

    flash("Match removed.", "success")
    return redirect(url_for("ui.tournament_detail", tournament_id=tournament_id, page=page, _anchor="match-form"))


@ui.route("/tournament/<int:tournament_id>")
def tournament_detail(tournament_id: int) -> str:
    page_raw = request.args.get("page", "0")
    try:
        current_page = int(page_raw)
    except ValueError:
        current_page = 0

    with get_connection() as connection:
        swiss_matches_columns = get_table_columns(connection, "swiss_matches")
        round_fk_column = "swiss_round_id" if "swiss_round_id" in swiss_matches_columns else "round_id"

        tournament = connection.execute(
            """
            SELECT
                t.*,
                st.tournament_id AS swiss_tournament_id,
                st.rounds_count,
                st.winner_player_id
            FROM tournaments t
            LEFT JOIN swiss_tournaments st ON st.tournament_id = t.id
            WHERE t.id = ?
            """,
            (tournament_id,),
        ).fetchone()

        if tournament is None:
            abort(404)

        swiss_player_rows = []
        if tournament["swiss_tournament_id"] is not None:
            swiss_player_rows = connection.execute(
                """
                SELECT sp.player_id, sp.deck_link, sp.deck_archetype, sp.dropped, u.display_name
                FROM swiss_players sp
                LEFT JOIN users u ON u.user_id = sp.player_id
                WHERE sp.swiss_tournament_id = ?
                """,
                (tournament["swiss_tournament_id"],),
            ).fetchall()

        swiss_player_by_id = {
            int(row["player_id"]): {
                "deck_link": row["deck_link"],
                "deck_archetype": row["deck_archetype"],
            }
            for row in swiss_player_rows
        }

        dropped_player_ids = get_dropped_player_ids(connection, tournament)
        participants: list[dict[str, Any]] = []
        if swiss_player_rows:
            participant_state_rows = connection.execute(
                """
                SELECT p.user_id, p.state, p.joined_at
                FROM participants p
                WHERE p.tournament_id = ?
                """,
                (tournament_id,),
            ).fetchall()
            participant_state_by_user = {
                int(row["user_id"]): {
                    "state": row["state"],
                    "joined_at": row["joined_at"],
                }
                for row in participant_state_rows
            }

            for swiss_player in swiss_player_rows:
                user_id = int(swiss_player["player_id"])
                participant_state = participant_state_by_user.get(user_id, {})
                participants.append(
                    {
                        "user_id": user_id,
                        "state": participant_state.get("state", "participate"),
                        "joined_at": participant_state.get("joined_at"),
                        "display_name": swiss_player["display_name"],
                        "dropped": bool(swiss_player["dropped"]) or user_id in dropped_player_ids,
                        "deck_link": swiss_player["deck_link"],
                        "deck_archetype": swiss_player["deck_archetype"],
                    }
                )

            state_sort_order = {
                "participate": 1,
                "registered": 1,
                "tentative": 2,
                "waitlist": 3,
                "decline": 4,
            }
            participants.sort(
                key=lambda participant: (
                    state_sort_order.get(participant["state"], 5),
                    participant["joined_at"] or "",
                    participant["user_id"],
                )
            )
        else:
            participant_rows = connection.execute(
                """
                SELECT p.user_id, p.state, p.joined_at, u.display_name
                FROM participants p
                LEFT JOIN users u ON u.user_id = p.user_id
                WHERE p.tournament_id = ?
                ORDER BY
                    CASE p.state
                        WHEN 'participate' THEN 1
                            WHEN 'registered' THEN 1
                        WHEN 'tentative' THEN 2
                        WHEN 'waitlist' THEN 3
                        WHEN 'decline' THEN 4
                        ELSE 5
                    END,
                    p.joined_at ASC,
                    p.user_id ASC
                """,
                (tournament_id,),
            ).fetchall()

            participants = [
                {
                    "user_id": participant["user_id"],
                    "state": participant["state"],
                    "joined_at": participant["joined_at"],
                    "display_name": participant["display_name"],
                    "dropped": participant["user_id"] in dropped_player_ids,
                    "deck_link": swiss_player_by_id.get(int(participant["user_id"]), {}).get("deck_link"),
                    "deck_archetype": swiss_player_by_id.get(int(participant["user_id"]), {}).get("deck_archetype"),
                }
                for participant in participant_rows
            ]

        users: dict[int, str] = {
            int(row["user_id"]): row["display_name"]
            for row in connection.execute("SELECT user_id, display_name FROM users").fetchall()
            if row["display_name"] is not None and str(row["user_id"]).strip().lstrip("-").isdigit()
        }

        swiss_player_choices = [
            {
                "user_id": int(row["player_id"]),
                "display_name": row["display_name"] or str(row["player_id"]),
                "label": f"{row['display_name'] or row['player_id']} ({row['player_id']})",
            }
            for row in sorted(
                swiss_player_rows,
                key=lambda row: ((row["display_name"] or str(row["player_id"])).lower(), int(row["player_id"])),
            )
        ]

        rounds = []
        last_participation_round_by_player: dict[int, int] = {}
        swiss_tournament_id = tournament["swiss_tournament_id"]
        if swiss_tournament_id is not None:
            round_rows = connection.execute(
                """
                SELECT id, round_number, message_id_pairings, message_id_standings
                FROM swiss_rounds
                WHERE swiss_tournament_id = ?
                ORDER BY round_number ASC
                """,
                (swiss_tournament_id,),
            ).fetchall()

            for round_row in round_rows:
                matches = connection.execute(
                    f"""
                    SELECT
                        id,
                        player1_id,
                        player2_id,
                        player1_wins,
                        player2_wins,
                        draws
                    FROM swiss_matches
                    WHERE {round_fk_column} = ?
                    ORDER BY id ASC
                    """,
                    (round_row["id"],),
                ).fetchall()
                matches = sorted(matches, key=lambda match: 1 if match["player2_id"] is None else 0)

                for match in matches:
                    round_number = int(round_row["round_number"])
                    player1_id = match["player1_id"]
                    player2_id = match["player2_id"]

                    if player1_id is not None:
                        last_participation_round_by_player[int(player1_id)] = round_number
                    if player2_id is not None:
                        last_participation_round_by_player[int(player2_id)] = round_number

                rounds.append({"round": round_row, "matches": matches})

        round_standings_by_number: dict[int, dict[str, Any]] = {}
        for round_index, round_entry in enumerate(rounds):
            round_number = int(round_entry["round"]["round_number"])
            standings_for_round = _calculate_final_standings(
                rounds[: round_index + 1],
                swiss_player_by_id,
                dropped_player_ids,
            )

            pending_player_ids: set[int] = set()
            for match in round_entry["matches"]:
                if not _is_pending_match_result(match):
                    continue

                player1_id = match["player1_id"]
                player2_id = match["player2_id"]
                if player1_id is not None:
                    pending_player_ids.add(int(player1_id))
                if player2_id is not None:
                    pending_player_ids.add(int(player2_id))

            is_round_ongoing = len(pending_player_ids) > 0
            standings_view = []
            for standing in standings_for_round:
                standing_view = dict(standing)
                standing_view["still_playing_in_round"] = (
                    is_round_ongoing and standing_view["player_id"] in pending_player_ids
                )
                standings_view.append(standing_view)

            round_standings_by_number[round_number] = {
                "standings": standings_view,
                "is_ongoing": is_round_ongoing,
            }

        dropped_indicator_round_by_player = {
            player_id: last_participation_round_by_player[player_id]
            for player_id in dropped_player_ids
            if player_id in last_participation_round_by_player
        }

        final_standings = _calculate_final_standings(rounds, swiss_player_by_id, dropped_player_ids)

        counts = get_participant_counts(connection, tournament_id)

    if current_page < 0:
        current_page = 0

    has_final_standings = len(final_standings) > 0
    final_standings_page_index = len(rounds) + 1 if has_final_standings else None

    max_page = len(rounds) + (1 if has_final_standings else 0)
    if current_page > max_page:
        current_page = max_page

    current_round = rounds[current_page - 1] if 0 < current_page <= len(rounds) else None
    current_round_number = (
        int(current_round["round"]["round_number"]) if current_round is not None else None
    )
    current_round_standings = (
        round_standings_by_number.get(current_round_number, {}).get("standings", [])
        if current_round_number is not None
        else []
    )
    current_round_is_ongoing = (
        bool(round_standings_by_number.get(current_round_number, {}).get("is_ongoing", False))
        if current_round_number is not None
        else False
    )
    is_final_standings_page = has_final_standings and current_page == final_standings_page_index
    final_standings_message_id = rounds[-1]["round"]["message_id_standings"] if rounds else None

    navigation_pages: list[tuple[int, str]] = [(0, "Participants")]
    navigation_pages.extend(
        (index, f"Round {entry['round']['round_number']}")
        for index, entry in enumerate(rounds, start=1)
    )
    if has_final_standings and final_standings_page_index is not None:
        navigation_pages.append((final_standings_page_index, "Final Standings"))

    return render_template(
        "tournament_detail.html",
        tournament=tournament,
        participants=participants,
        participant_counts=counts,
        rounds=rounds,
        current_round=current_round,
        current_round_standings=current_round_standings,
        current_round_is_ongoing=current_round_is_ongoing,
        final_standings=final_standings,
        final_standings_message_id=final_standings_message_id,
        is_final_standings_page=is_final_standings_page,
        current_page=current_page,
        navigation_pages=navigation_pages,
        dropped_player_ids=dropped_player_ids,
        dropped_indicator_round_by_player=dropped_indicator_round_by_player,
        state_labels=STATE_LABELS,
        users=users,
        swiss_player_choices=swiss_player_choices,
    )


@app.route("/")
def home() -> str:
    line_count = parse_log_line_count(request.args.get("lines"))
    bot_logs = get_bot_logs(line_count)

    return render_template(
        "home.html",
        webapp_prefix=WEBAPP_PREFIX,
        bot_logs=bot_logs,
        line_count=line_count,
        min_log_lines=MIN_LOG_LINES,
        max_log_lines=MAX_LOG_LINES,
        custom_panels=load_home_panels(),
        panel_icons=HOME_PANEL_ICONS,
    )


@app.route("/api/home-panels", methods=["POST"])
def create_home_panel() -> Any:
    panel, error = validate_home_panel(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400

    panels = load_home_panels()
    panels.append(panel)
    save_home_panels(panels)
    return jsonify(panel), 201


@app.route("/api/home-panels/<panel_id>", methods=["DELETE"])
def delete_home_panel(panel_id: str) -> Any:
    panels = load_home_panels()
    remaining_panels = [panel for panel in panels if panel["id"] != panel_id]
    if len(remaining_panels) == len(panels):
        return jsonify({"error": "Panel not found."}), 404

    save_home_panels(remaining_panels)
    return "", 204


@app.route("/api/home-panels/<panel_id>", methods=["PATCH"])
def update_home_panel(panel_id: str) -> Any:
    panel, error = validate_home_panel(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400

    panels = load_home_panels()
    for index, existing_panel in enumerate(panels):
        if existing_panel["id"] == panel_id:
            panel["id"] = panel_id
            panels[index] = panel
            save_home_panels(panels)
            return jsonify(panel)

    return jsonify({"error": "Panel not found."}), 404


@ui.route("/bot-logs/")
@ui.route("/bot-logs")
def bot_logs_page() -> str:
    line_count = parse_log_line_count(request.args.get("lines"))
    bot_logs = get_bot_logs(line_count)
    return render_template(
        "bot_logs.html",
        bot_logs=bot_logs,
        line_count=line_count,
        min_log_lines=MIN_LOG_LINES,
        max_log_lines=MAX_LOG_LINES,
    )


@ui.route("/api/bot-logs", methods=["GET"])
def bot_logs_api() -> Any:
    line_count = parse_log_line_count(request.args.get("lines"))
    logs = get_bot_logs(line_count)
    return jsonify(
        {
            "line_count": line_count,
            "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "logs": logs,
        }
    )


TOPCARDS_ALLOWED_FILES = frozenset(
    ["app.js", "styles.css", "Standard.json", "Pioneer.json", "Modern.json", "Legacy.json", "Pauper.json"]
)

app.register_blueprint(ui, url_prefix=WEBAPP_PREFIX)


@app.route("/topcards")
def topcards_redirect() -> Any:
    return redirect(url_for("topcards"))


@app.route("/topcards/")
def topcards() -> Any:
    return send_from_directory(TOPCARDS_DIR, "index.html")


@app.route("/topcards/<filename>")
def topcards_static(filename: str) -> Any:
    if filename not in TOPCARDS_ALLOWED_FILES:
        abort(404)
    return send_from_directory(TOPCARDS_DIR, filename)


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
