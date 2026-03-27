"""
Hall of Fame module backed directly by tournaments.db.

This module provides a read-only, always up-to-date projection of tournament
results and participant statistics. JSON persistence is intentionally removed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Dict, List, Optional

from ezcord import log

from modules import swiss_mtg
from modules.db import (
    SessionLocal,
    DiscordUser,
    Tournament,
    SwissTournamentRecord,
)
from modules.spelltable.tournament_model import _build_swiss_json


class TournamentFormat(StrEnum):
    PAUPER = "Pauper"
    LEGACY = "Legacy"
    MODERN = "Modern"
    PREMODERN = "Premodern"
    PIONEER = "Pioneer"
    LIMITED = "Limited"
    STANDARD = "Standard"
    ANDERE = "Andere"


FORMAT_CHOICES = [f.value for f in TournamentFormat]


def _parse_record_component(value: str) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return 0


def _record_tuple(match_record: str) -> tuple[int, int, int]:
    if not isinstance(match_record, str):
        return 0, 0, 0
    parts = match_record.split("-")
    if len(parts) < 3:
        return 0, 0, 0
    return (
        _parse_record_component(parts[0]),
        _parse_record_component(parts[1]),
        _parse_record_component(parts[2]),
    )


def _safe_datetime_sort_key(value: str):
    if not isinstance(value, str):
        return datetime.min
    normalized = value.strip()
    if not normalized:
        return datetime.min
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min


class ParticipantStats:
    """Represents statistics for a tournament participant."""

    def __init__(self, user_id: int, username: str):
        self.user_id = user_id
        self.username = username
        self.total_match_wins = 0
        self.total_match_losses = 0
        self.total_draws = 0
        self.tournament_participations = 0
        self.tournament_winners = 0
        self.tournament_top3s = 0
        self.tournament_top8s = 0
        self.last_updated = datetime.now().isoformat()
        self.match_history: list[str] = []

    def add_match(self, result: str):
        if result == "win":
            self.total_match_wins += 1
        elif result == "loss":
            self.total_match_losses += 1
        elif result == "draw":
            self.total_draws += 1
        self.last_updated = datetime.now().isoformat()

    def add_tournament(self, placement: int):
        self.tournament_participations += 1
        if placement == 1:
            self.tournament_winners += 1
        elif placement <= 3:
            self.tournament_top3s += 1
        elif placement <= 8:
            self.tournament_top8s += 1
        self.last_updated = datetime.now().isoformat()

    def get_match_record(self) -> str:
        return f"{self.total_match_wins}-{self.total_match_losses}-{self.total_draws}"

    def get_match_win_percentage(self) -> float:
        total_matches = self.total_match_wins + self.total_match_losses + self.total_draws
        if total_matches == 0:
            return 0.0
        return (self.total_match_wins + self.total_draws * 0.5) / total_matches


class TournamentRecord:
    """Represents a single concluded tournament entry in the hall of fame."""

    def __init__(
        self,
        tournament_id: str,
        tournament_title: str,
        guild_id: int,
        start_date: str,
        standings_url: str = "",
        format_name: str | None = None,
    ):
        self.tournament_id = tournament_id
        self.tournament_title = tournament_title
        self.guild_id = guild_id
        self.start_date = start_date
        self.standings_url = standings_url
        self.format_name = format_name
        self.results: List[Dict] = []

    def add_result(self, user_id: int, username: str, placement: int, match_record: str):
        self.results.append(
            {
                "user_id": user_id,
                "username": username,
                "placement": placement,
                "match_record": match_record,
            }
        )


class HallOfFame:
    """Read-only hall of fame projection built directly from tournaments.db."""

    def __init__(
        self,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        format_filter: str | None = None,
    ):
        self.participants: Dict[int, ParticipantStats] = {}
        self.tournaments: List[TournamentRecord] = []
        self._load_from_db(time_from=time_from, time_to=time_to, format_filter=format_filter)

    def _display_name_for_user(self, user_id: int, users: dict[str, DiscordUser]) -> str:
        user_row = users.get(str(user_id))
        if user_row is None:
            return str(user_id)
        return user_row.display_name or user_row.username or str(user_id)

    def _load_from_db(
        self,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        format_filter: str | None = None,
    ):
        db = SessionLocal()
        try:
            user_rows = db.query(DiscordUser).all()
            users_by_id = {str(row.user_id): row for row in user_rows}

            query = (
                db.query(Tournament, SwissTournamentRecord)
                .join(SwissTournamentRecord, SwissTournamentRecord.tournament_id == Tournament.id)
                .filter(SwissTournamentRecord.winner_player_id.isnot(None))
            )
            if time_from is not None:
                query = query.filter(Tournament.start_time >= time_from)
            if time_to is not None:
                query = query.filter(Tournament.start_time <= time_to)
            concluded_rows = query.all()

            for tournament_row, swiss_row in concluded_rows:
                try:
                    # Apply format filter early to avoid unnecessary DB work
                    if format_filter:
                        row_format = tournament_row.format_name or ""
                        if format_filter != row_format:
                            continue

                    swiss_json = _build_swiss_json(db, swiss_row)
                    swiss_tournament = swiss_mtg.SwissTournament.deserialize(swiss_json)
                    swiss_mtg.sort_players_by_standings(swiss_tournament.players)
                except Exception as exc:
                    log.warning(
                        f"Skipping hall of fame reconstruction for tournament_id={tournament_row.id}: {exc}"
                    )
                    continue

                guild_id = int(tournament_row.guild_id)
                channel_id = int(tournament_row.channel_id)
                message_id = int(tournament_row.message_id)
                tournament_id = f"{guild_id}/{channel_id}/{message_id}"

                latest_round = max(swiss_tournament.rounds, key=lambda r: r.round_number, default=None)
                standings_url = ""
                if latest_round and latest_round.message_id_standings:
                    standings_url = (
                        f"https://discord.com/channels/{guild_id}/{channel_id}/{latest_round.message_id_standings}"
                    )

                start_date = (
                    tournament_row.start_time.isoformat()
                    if tournament_row.start_time is not None
                    else tournament_row.created_at.isoformat()
                )

                tournament_record = TournamentRecord(
                    tournament_id=tournament_id,
                    tournament_title=tournament_row.name,
                    guild_id=guild_id,
                    start_date=start_date,
                    standings_url=standings_url,
                    format_name=tournament_row.format_name,
                )

                placement = 1
                for player in swiss_tournament.players:
                    if player.dropped:
                        continue

                    user_id = int(player.player_id)
                    username = self._display_name_for_user(user_id, users_by_id)
                    match_record = player.get_match_results()

                    tournament_record.add_result(
                        user_id=user_id,
                        username=username,
                        placement=placement,
                        match_record=match_record,
                    )

                    participant = self.participants.get(user_id)
                    if participant is None:
                        participant = ParticipantStats(user_id, username)
                        self.participants[user_id] = participant
                    else:
                        participant.username = username

                    participant.add_tournament(placement)
                    wins, losses, draws = _record_tuple(match_record)
                    for _ in range(wins):
                        participant.add_match("win")
                    for _ in range(losses):
                        participant.add_match("loss")
                    for _ in range(draws):
                        participant.add_match("draw")

                    placement += 1

                self.tournaments.append(tournament_record)

            self.tournaments.sort(key=lambda t: _safe_datetime_sort_key(t.start_date), reverse=True)
            log.debug(
                f"Hall of Fame loaded from DB: {len(self.participants)} participants, {len(self.tournaments)} tournaments"
            )
        finally:
            db.close()

    def save_to_file(self):
        """Compatibility no-op: Hall of Fame is derived from DB."""
        return

    def add_participant(self, user_id: int, username: str) -> ParticipantStats:
        """Compatibility helper; kept for API stability."""
        participant = self.participants.get(user_id)
        if participant is None:
            participant = ParticipantStats(user_id, username)
            self.participants[user_id] = participant
        else:
            participant.username = username
        return participant

    def get_participant(self, user_id: int) -> Optional[ParticipantStats]:
        return self.participants.get(user_id)

    def add_tournament_result(
        self,
        tournament_id: str,
        tournament_title: str,
        guild_id: int,
        start_date: str,
        results: List[Dict],
        *,
        standings_url: str = "",
        save: bool = True,
    ):
        """Compatibility no-op: tournament results are sourced from DB."""
        _ = (
            tournament_id,
            tournament_title,
            guild_id,
            start_date,
            results,
            standings_url,
            save,
        )
        return

    def get_top_participants(self, limit: int = 10, sort_by: str = "match_wins") -> List[ParticipantStats]:
        participants_list = list(self.participants.values())

        if sort_by == "match_wins":
            participants_list.sort(key=lambda x: x.total_match_wins, reverse=True)
        elif sort_by == "match_winrate":
            participants_list.sort(
                key=lambda x: (x.get_match_win_percentage(), x.total_match_wins), reverse=True
            )
        elif sort_by == "tournaments":
            participants_list.sort(key=lambda x: x.tournament_participations, reverse=True)
        elif sort_by == "top3s":
            participants_list.sort(key=lambda x: x.tournament_top3s, reverse=True)
        elif sort_by == "top8s":
            participants_list.sort(key=lambda x: x.tournament_top8s, reverse=True)
        elif sort_by == "winners":
            participants_list.sort(key=lambda x: x.tournament_winners, reverse=True)
        else:
            participants_list.sort(
                key=lambda x: (x.tournament_winners, x.tournament_top3s, x.total_match_wins),
                reverse=True,
            )

        return participants_list[:limit]

    def get_user_stats(self, user_id: int) -> Optional[Dict]:
        participant = self.get_participant(user_id)
        if not participant:
            return None

        tournament_placements = []
        for tournament in self.tournaments:
            for result in tournament.results:
                if result["user_id"] == user_id:
                    tournament_placements.append(
                        {
                            "tournament": tournament.tournament_title,
                            "placement": result["placement"],
                            "match_record": result["match_record"],
                            "date": tournament.start_date,
                            "standings_url": tournament.standings_url,
                        }
                    )

        tournament_placements.sort(
            key=lambda placement: _safe_datetime_sort_key(placement.get("date", "")),
            reverse=True,
        )

        return {
            "user_id": user_id,
            "username": participant.username,
            "match_record": participant.get_match_record(),
            "match_winrate": f"{participant.get_match_win_percentage()*100:.1f}%",
            "tournament_participations": participant.tournament_participations,
            "tournament_winners": participant.tournament_winners,
            "tournament_top3s": participant.tournament_top3s,
            "tournament_top8s": participant.tournament_top8s,
            "tournament_placements": tournament_placements,
        }

    def get_tournament_results(self, tournament_id: str) -> Optional[TournamentRecord]:
        for tournament in self.tournaments:
            if tournament.tournament_id == tournament_id:
                return tournament
        return None

    def get_guild_tournaments(self, guild_id: int) -> List[TournamentRecord]:
        return [t for t in self.tournaments if t.guild_id == guild_id]


def get_hall_of_fame(
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    format_filter: str | None = None,
) -> HallOfFame:
    """Return a fresh DB-backed snapshot so data is always up to date."""
    return HallOfFame(time_from=time_from, time_to=time_to, format_filter=format_filter)
