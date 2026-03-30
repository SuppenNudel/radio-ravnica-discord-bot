from ezcord import Cog, log
from typing import Annotated
from discord.ext.commands import slash_command
from discord.ext import commands
from discord import ApplicationContext, Bot, Option
import discord
from modules import date_time_interpretation
from modules import swiss_mtg
from modules import env
import logging
import asyncio

from discord.ext import tasks
from datetime import datetime, time, timezone

from modules.db import (
    SessionLocal,
    Tournament as TournamentRecord,
    Participant,
    SwissTournamentRecord,
    SwissRound,
    SwissPlayer,
    SwissMatch,
    DiscordUser,
)
from modules.db import BannedUser
from modules.spelltable.tournament_model import SpelltableTournament, load_tournaments, active_tournaments, update_tournament_message
from modules.spelltable.common_views import SubmitDeckView, ParticipationState, next_round, update_hall_of_fame_batch
from modules.spelltable import common_views
from modules.hall_of_fame import FORMAT_CHOICES

link_log = logging.getLogger("link_logger")

def auto_detect_format_from_channel(channel: discord.TextChannel) -> str | None:
    """
    Try to auto-detect a tournament format based on the channel name or parent category name.
    Checks both the channel name and parent category name for format keywords.
    """
    channel_name = channel.name.lower() if channel.name else ""
    category_name = channel.category.name.lower() if channel.category and channel.category.name else ""
    
    # Combine both names for searching
    search_text = f"{channel_name} {category_name}"
    
    # Check each format against the search text
    for format_choice in FORMAT_CHOICES:
        if format_choice.lower() in search_text:
            return format_choice
    
    return None

class ParticipationView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await ParticipationView.create(...)' instead")

    async def _init(self, tournament:SpelltableTournament):
        super().__init__(timeout=None)  # Ensure persistence
        self.tournament = tournament
    
    @classmethod
    async def create(cls, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(tournament)
        return instance

    @discord.ui.button(label="Teilnehmen", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def join_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await self.tournament.user_state(interaction.user.id, ParticipationState.PARTICIPATE)
        if message:
            await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Vielleicht", style=discord.ButtonStyle.primary, emoji="❓", row=0)
    async def tentative_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.TENTATIVE)

    @discord.ui.button(label="Absagen", style=discord.ButtonStyle.danger, emoji="❌", row=0)
    async def leave_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.DECLINE)

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def edit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.respond("Du bist kein Mitglied auf diesem Server!", ephemeral=True)
            return
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        
        message = await self.tournament.message
        if message:
            if not isinstance(message.channel, discord.Thread):
                await interaction.respond("Bearbeiten ist nur in Threads moeglich.", ephemeral=True)
                return
            view = await EditTournamentView.create(self.tournament, message.channel)
            await interaction.respond(view=view, ephemeral=True)
        else:
            await interaction.respond("Turnier Nachricht nicht gefunden", ephemeral=True)

    @discord.ui.button(label="Spieler rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷", row=1)
    async def kick_button(self, button:discord.ui.Button, interaction:discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.respond("Du bist kein Mitglied auf diesem Server!", ephemeral=True)
            return
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        await common_views.open_kick_player_selection(self.tournament, interaction)

    @discord.ui.button(label="Starte Turnier", style=discord.ButtonStyle.primary, emoji="▶️", row=2)
    async def start_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        if type(interaction.user) != discord.Member:
            await interaction.respond("Du bist kein Mitglied auf diesem Server!", ephemeral=True)
            return
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        progress_message = await interaction.followup.send("Starte Turnier...", ephemeral=True, wait=True)

        try:
            message = await self.tournament.message
            if message:
                await message.edit(view=None)
            else:
                await interaction.followup.send("Turnier Nachricht nicht gefunden", ephemeral=True)
                return
            
            players = []
            participants = self.tournament.get_users_by_state(ParticipationState.PARTICIPATE)
            # Deduplicate participant IDs to prevent accidental duplicates in Swiss tournament
            seen_participant_ids = set()
            for participant_id in participants:
                if participant_id not in seen_participant_ids:
                    players.append(swiss_mtg.Player(participant_id))
                    seen_participant_ids.add(participant_id)

            swiss_tournament = swiss_mtg.SwissTournament(players, max_rounds=self.tournament.max_rounds if self.tournament.max_rounds else None)
            self.tournament.swiss_tournament = swiss_tournament

            # Populate member cache so users table gets human-readable names on first round save
            registered_user_ids = set(self.tournament.users.keys()) | set(self.tournament.waitlist)
            for user_id in registered_user_ids:
                await self.tournament.get_member(user_id)

            await next_round(self.tournament, interaction)
        finally:
            try:
                if progress_message and hasattr(progress_message, "delete"):
                    await progress_message.delete()
            except Exception:
                pass
    
    
    @discord.ui.button(label="Turnier abbrechen", style=discord.ButtonStyle.danger, emoji="🛑", row=1)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.respond("Du bist kein Mitglied auf diesem Server!", ephemeral=True)
            return
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Nur der Turnier-Organisator oder ein Moderator darf dies tun!", ephemeral=True)
            return
        
        await interaction.response.send_modal(common_views.CancelTournamentModal(self.tournament))

class EnterTextModal(discord.ui.Modal):
    def __init__(self, input:discord.ui.InputText, key, tournament:SpelltableTournament, view:"EditTournamentView", parse=None):
        super().__init__(title="Bearbeite Turnier")
        self.tournament = tournament
        self.parse = parse
        self.key = key
        self.view = view

        self.input = input
        self.add_item(self.input)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_value = self.input.value
        try:
            if self.parse:
                new_value = self.parse(self.input.value)
        except ValueError:
            await interaction.followup.send(f"Konnte die Eingabe `{self.input.value}` nicht auswerten.", ephemeral=True)
            return

        setattr(self.tournament, self.key, new_value)
        
        self.view._refresh_mandatory_field_labels()
        self.view.submit_button.disabled = not bool(self.tournament.time)
        await interaction.edit_original_response(embed=await self.tournament.to_embed(), view=self.view)

class EditTournamentView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await EditTournamentView.create(...)' instead")

    async def _init(self, tournament: SpelltableTournament, channel: discord.abc.Messageable):
        super().__init__()
        self.tournament = tournament
        self.channel = channel

        self._refresh_mandatory_field_labels()

        # Dynamically set the disabled state of the "Abschicken" button
        self.submit_button.disabled = not bool(self.tournament.time)

        self.days_per_match_callback.label = (
            "Turnier am Stück spielen" if self.tournament.days_per_match else "Eine Woche pro Runde"
        )

    def _refresh_mandatory_field_labels(self):
        status = "✅" if self.tournament.time else "⚠️"
        self.time_callback.label = f"Startzeit/-datum* {status}"

    @classmethod
    async def create(cls, tournament: SpelltableTournament, channel: discord.abc.Messageable):
        instance = object.__new__(cls)
        await instance._init(tournament, channel)
        return instance
    
    @discord.ui.button(label="Titel", style=discord.ButtonStyle.primary, emoji="✏️")
    async def title_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        input = discord.ui.InputText(
            label="Turniername",
            placeholder="Name des Turniers",
            required=True,
            value=self.tournament.title,
            style=discord.InputTextStyle.short,
        )

        await interaction.response.send_modal(EnterTextModal(input, "title", self.tournament, self))

    @discord.ui.button(label="Beschreibung", style=discord.ButtonStyle.primary, emoji="🗒️")
    async def description_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        input = discord.ui.InputText(
            label="Beschreibung",
            placeholder="Eine Beschreibung für das Turnier",
            required=True,
            value=self.tournament.description,
            style=discord.InputTextStyle.long,
        )

        await interaction.response.send_modal(EnterTextModal(input, "description", self.tournament, self))

          
    @discord.ui.button(label="Startzeit/-datum", style=discord.ButtonStyle.primary, emoji="🕑")
    async def time_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        input = discord.ui.InputText(
            label="Startzeit/-datum",
            placeholder="z.B. morgen um 17 Uhr, am 24.12.2025 um 12:00 Uhr, in 2 Stunden",
            required=True,
            value=str(self.tournament.time) if self.tournament.time else "",
        )

        def parse(input_value):
            return date_time_interpretation.parse_date(input_value)

        await interaction.response.send_modal(EnterTextModal(input, "time", self.tournament, self, parse))

    @discord.ui.button(label="Max. Teilnehmer", style=discord.ButtonStyle.primary, emoji="🔢")
    async def max_player_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        input = discord.ui.InputText(
            label="Maximale Teilnehmer",
            placeholder="Wieviele Spieler maximal Teilnehmen können",
            required=True,
            value=str(self.tournament.max_participants) if self.tournament.max_participants else "",
        )

        def parse(input_value):
            return int(input_value)

        await interaction.response.send_modal(EnterTextModal(input, "max_participants", self.tournament, self, parse))

    @discord.ui.button(label="Max. Runden", style=discord.ButtonStyle.primary, emoji="🔃")
    async def round_count_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        if self.tournament.swiss_tournament:
            await interaction.response.send_message(
                "Die Rundenzahl wurde beim Turnierstart festgelegt und kann nicht mehr geändert werden.",
                ephemeral=True,
            )
            return

        input = discord.ui.InputText(
            label="Maximal Runden (0 für empfohlene Anzahl)",
            placeholder="Wieviele Runden maximal gespielt werden sollen",
            required=True,
            value=str(self.tournament.max_rounds) if self.tournament.max_rounds else "",
        )

        def parse(input_value):
            return int(input_value)

        await interaction.response.send_modal(EnterTextModal(input, "max_rounds", self.tournament, self, parse))

    @discord.ui.button(label="Tage pro Runde", style=discord.ButtonStyle.primary, emoji="📆")
    async def days_per_match_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        # is toggle
        if self.tournament.days_per_match:
            self.tournament.days_per_match = 0
        else:
            self.tournament.days_per_match = 7
        view = await EditTournamentView.create(self.tournament, self.channel)
        await interaction.response.edit_message(embed=await self.tournament.to_embed(), view=view)

    @discord.ui.button(label="Format", style=discord.ButtonStyle.primary, emoji="🎴")
    async def format_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        from modules.hall_of_fame import FORMAT_CHOICES
        current = self.tournament.format_name or None
        options = [
            discord.SelectOption(label=f, value=f, default=(f == current))
            for f in FORMAT_CHOICES
        ]
        select = discord.ui.StringSelect(
            placeholder="Format auswählen",
            options=options,
            min_values=0,
            max_values=1,
        )
        tournament = self.tournament
        edit_view = self

        async def on_select(select_interaction: discord.Interaction):
            tournament.format_name = select.values[0] if select.values else None
            await select_interaction.response.edit_message(
                embed=await tournament.to_embed(),
                view=edit_view,
            )

        select.callback = on_select
        ephemeral_view = discord.ui.View(timeout=60)
        ephemeral_view.add_item(select)
        await interaction.response.send_message(view=ephemeral_view, ephemeral=True)

    @discord.ui.button(label="Abschicken", style=discord.ButtonStyle.success, emoji="✅")
    async def submit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        self.disable_all_items()
        await interaction.edit(view=self)
        # Save into database
        participation_view = await ParticipationView.create(self.tournament)

        existing_message = await self.tournament.message
        if existing_message:
            # Edit existing message
            await self.tournament.save_tournament_metadata()
            await self.tournament.save_tournament_participants()
            if self.tournament.swiss_tournament:
                await self.tournament.save_tournament_swiss()
            # Rename thread if message is in a thread
            if isinstance(existing_message.channel, discord.Thread):
                await existing_message.channel.edit(name=self.tournament.title)
            await existing_message.edit(content=await self.tournament.get_participants_message(), embed=await self.tournament.to_embed(), view=participation_view)
            await interaction.edit_original_response(content="Turnier wurde bearbeitet", embed=None, view=None)
        else:
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.followup.send("Threads können nur in Textkanälen erstellt werden.", ephemeral=True)
                return
            # Create new thread
            thread = await interaction.channel.create_thread(
                name=self.tournament.title, type=discord.ChannelType.public_thread, auto_archive_duration=10080 # eine Woche
            )
            organizer = await self.tournament.organizer
            if not organizer:
                await interaction.followup.send("Organisator konnte nicht geladen werden.", ephemeral=True)
                return
            await interaction.channel.send(
                f"{organizer.mention} hat ein Turnier erstellt. Macht doch mit! :) {thread.mention}"
            )
            participants_content = await self.tournament.get_participants_message()
            message = await thread.send(content=participants_content, embed=await self.tournament.to_embed(), view=participation_view)
            self.tournament.message = message
            await message.edit(view=participation_view)
            active_tournaments[await self.tournament.get_id()] = self.tournament
            await self.tournament.save_tournament_metadata()
            await self.tournament.save_tournament_participants()
            if self.tournament.swiss_tournament:
                await self.tournament.save_tournament_swiss()
            await interaction.followup.send(
                f"Prima! Das Turnier `{self.tournament.title}` wurde erstellt: {message.jump_url}", ephemeral=True
            )
            link_log.info(f"Ein Turnier wurde erstellt: {message.jump_url}")

class SpelltableTournamentManager(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.schedule_midnight_update.start()

    async def _refresh_historical_pairings_messages(
        self,
        tournament: SpelltableTournament,
        current_round_number: int | None,
        include_current_round: bool = False,
    ):
        if not tournament.swiss_tournament:
            return

        rounds_to_refresh = [
            round_obj for round_obj in tournament.swiss_tournament.rounds
            if round_obj.message_id_pairings and (
                include_current_round
                or current_round_number is None
                or round_obj.round_number != current_round_number
            )
        ]

        # Pre-populate the member cache for all players across all rounds before
        # processing rounds concurrently. Without this, the same user can be looked
        # up multiple times simultaneously (once per round they appear in) because
        # the cache hasn't been updated yet when the second concurrent lookup starts.
        all_player_ids: set[int] = set()
        for round_obj in rounds_to_refresh:
            for match in round_obj.matches:
                if match.player1:
                    all_player_ids.add(match.player1.player_id)
                if match.player2:
                    all_player_ids.add(match.player2.player_id)
        if all_player_ids:
            await asyncio.gather(*[tournament.get_member(uid) for uid in all_player_ids])

        async def _refresh_one(round_obj):
            pairings_message = await tournament.get_message(round_obj.message_id_pairings)
            if not pairings_message:
                return
            if isinstance(pairings_message.channel, discord.Thread) and pairings_message.channel.archived:
                await pairings_message.channel.edit(archived=False)
            try:
                pairings_image = await tournament.pairings_to_image(round_obj)
                pairings_file = discord.File(pairings_image, filename=pairings_image)
                await pairings_message.edit(attachments=[], file=pairings_file, view=None)
            except discord.errors.Forbidden as e:
                log.warning(
                    f"Cannot reconcile historical pairings message {pairings_message.jump_url} for tournament '{tournament.title}' "
                    f"(round {round_obj.round_number}): missing permissions. Error: {e}"
                )
            except Exception as e:
                log.warning(
                    f"Failed to reconcile historical pairings message {pairings_message.jump_url} for tournament '{tournament.title}' "
                    f"(round {round_obj.round_number}): {e}"
                )

        await asyncio.gather(*[_refresh_one(r) for r in rounds_to_refresh], return_exceptions=True)

    async def _resolve_discord_user(self, guild: discord.Guild, user_id: int) -> discord.abc.User | None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None

        if member is not None:
            return member

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        return user

    async def _sync_tournament_user_mappings(self, guild: discord.Guild) -> dict[str, object]:
        db = SessionLocal()
        try:
            tournament_rows = (
                db.query(TournamentRecord.id, TournamentRecord.organizer_id)
                .filter(TournamentRecord.guild_id == guild.id)
                .all()
            )

            tournament_ids = [row.id for row in tournament_rows]
            referenced_user_ids = {row.organizer_id for row in tournament_rows if row.organizer_id is not None}

            if tournament_ids:
                participant_ids = (
                    db.query(Participant.user_id)
                    .filter(Participant.tournament_id.in_(tournament_ids))
                    .all()
                )
                referenced_user_ids.update(user_id for (user_id,) in participant_ids if user_id is not None)

                swiss_rows = (
                    db.query(SwissTournamentRecord.tournament_id, SwissTournamentRecord.winner_player_id)
                    .filter(SwissTournamentRecord.tournament_id.in_(tournament_ids))
                    .all()
                )
                swiss_ids = [row.tournament_id for row in swiss_rows]
                referenced_user_ids.update(
                    row.winner_player_id
                    for row in swiss_rows
                    if row.winner_player_id is not None
                )

                if swiss_ids:
                    swiss_player_ids = (
                        db.query(SwissPlayer.player_id)
                        .filter(SwissPlayer.swiss_tournament_id.in_(swiss_ids))
                        .all()
                    )
                    referenced_user_ids.update(
                        user_id for (user_id,) in swiss_player_ids if user_id is not None
                    )

                    swiss_match_rows = (
                        db.query(SwissMatch.player1_id, SwissMatch.player2_id)
                        .join(SwissRound, SwissRound.id == SwissMatch.swiss_round_id)
                        .filter(SwissRound.swiss_tournament_id.in_(swiss_ids))
                        .all()
                    )
                    referenced_user_ids.update(
                        user_id
                        for match_row in swiss_match_rows
                        for user_id in match_row
                        if user_id is not None
                    )

            existing_user_rows = {
                row.user_id: row
                for row in db.query(DiscordUser).filter(DiscordUser.user_id.in_(referenced_user_ids)).all()
            } if referenced_user_ids else {}

            created_count = 0
            updated_count = 0
            unresolved_user_ids: list[int] = []
            missing_row_ids = sorted(user_id for user_id in referenced_user_ids if user_id not in existing_user_rows)
            incomplete_row_ids = sorted(
                user_id
                for user_id, row in existing_user_rows.items()
                if row.username in (None, "") or row.display_name in (None, "")
            )

            for user_id in sorted(referenced_user_ids):
                normalized_user_id = str(user_id).strip() if user_id is not None else ""
                if not normalized_user_id:
                    link_log.warning(
                        "Skipping user cache row with missing user_id during refresh: "
                        f"guild_id={guild.id if guild else None}, raw_user_id={user_id!r}"
                    )
                    continue

                user_row = existing_user_rows.get(user_id)
                discord_user = await self._resolve_discord_user(guild, user_id)
                if discord_user is None:
                    if user_row is None or user_row.username in (None, "") or user_row.display_name in (None, ""):
                        unresolved_user_ids.append(user_id)
                    continue

                if user_row is None:
                    user_row = DiscordUser(user_id=normalized_user_id)
                    db.add(user_row)
                    existing_user_rows[user_id] = user_row
                    created_count += 1
                    created_row = True
                else:
                    created_row = False

                if self._is_deleted_discord_user(discord_user):
                    username = f"deleted_user:{user_id}"
                    display_name = "Deleted Discord Account"
                else:
                    username = getattr(discord_user, "name", None)
                    display_name = getattr(discord_user, "display_name", None) or getattr(discord_user, "global_name", None)

                row_changed = False
                if username and user_row.username != username:
                    user_row.username = username
                    row_changed = True
                if display_name and user_row.display_name != display_name:
                    user_row.display_name = display_name
                    row_changed = True

                if row_changed or created_row:
                    user_row.updated_at = datetime.utcnow()
                    if row_changed and not created_row:
                        updated_count += 1

            if created_count or updated_count:
                db.commit()

            return {
                "tournaments": len(tournament_ids),
                "referenced_user_ids": sorted(referenced_user_ids),
                "missing_row_ids": missing_row_ids,
                "incomplete_row_ids": incomplete_row_ids,
                "created_count": created_count,
                "updated_count": updated_count,
                "unresolved_user_ids": unresolved_user_ids,
            }
        finally:
            db.close()

    @staticmethod
    def _is_deleted_discord_user(discord_user: discord.abc.User) -> bool:
        name = (getattr(discord_user, "name", None) or "").strip().lower()
        display_name = (
            getattr(discord_user, "display_name", None)
            or getattr(discord_user, "global_name", None)
            or ""
        ).strip().lower()

        return (
            name == "deleted user"
            or name.startswith("deleted user ")
            or display_name == "deleted user"
            or display_name.startswith("deleted user ")
        )

    @Cog.listener()
    async def on_ready(self):
        loaded_tournaments = await load_tournaments(self.bot)

        global active_tournaments

        async def _load_one(message_path: str, tournament: SpelltableTournament) -> SpelltableTournament | None:
            """Loads one tournament concurrently; returns the tournament if concluded, else None."""
            if tournament.cancelled:
                try:
                    await common_views.remove_tournament_views(tournament)
                except Exception:
                    log.exception(f"Failed to clear views for cancelled tournament `{tournament.title}` during startup")
                return None

            tournament.suppress_overview_update = True
            try:
                active_tournaments[message_path] = tournament
                tournament_message = await tournament.message
                if not tournament_message:
                    log.warning(f"Turnier Nachricht nicht gefunden: {message_path}")
                    return None

                # Unarchive thread if needed before any edits
                if isinstance(tournament_message.channel, discord.Thread) and tournament_message.channel.archived:
                    await tournament_message.channel.edit(archived=False)

                if not tournament.swiss_tournament:
                    # tournament has not been started yet
                    # sign-up is still open
                    view = await ParticipationView.create(tournament)
                    participants_content = await tournament.get_participants_message()
                    embed = await tournament.to_embed()
                    await tournament_message.edit(content=participants_content, embed=embed, view=view)
                    return None

                # tournament has been started
                swiss_tournament = tournament.swiss_tournament
                current_round = swiss_tournament.current_round()
                current_round_concluded = bool(current_round and current_round.is_concluded())
                is_last_round = bool(
                    current_round
                    and swiss_tournament.rounds_count
                    and current_round.round_number >= swiss_tournament.rounds_count
                )
                tournament_concluded = swiss_tournament.winner is not None

                if (is_last_round and current_round_concluded) and swiss_tournament.winner is None:
                    link_log.warning(
                        f"Tournament '{tournament.title}' has a concluded last round during startup but no winner_player_id is set. "
                        f"Keeping tournament open so organizer can finish it explicitly."
                    )

                await self._refresh_historical_pairings_messages(
                    tournament,
                    current_round.round_number if current_round else None,
                    include_current_round=tournament_concluded,
                )

                if tournament_concluded:
                    # Tournament is concluded

                    # unarchive thread if needed
                    if isinstance(tournament_message.channel, discord.Thread) and tournament_message.channel.archived:
                        await tournament_message.channel.edit(archived=False)

                    # Update standings with latest deck submissions
                    submit_deck_view = await SubmitDeckView.create(tournament)
                    total_rounds = tournament.swiss_tournament.rounds_count
                    await tournament.update_metagame(
                        include_embed=False,
                        content=f"## 🏆 Finale Platzierung nach {total_rounds} Runden",
                        view=submit_deck_view,
                    )

                    # activate deck submission button
                    standings_message = None
                    if current_round.message_id_standings:
                        message = await tournament.get_message(current_round.message_id_standings)
                        standings_message = message
                        if message:
                            if self.bot.user and message.author and message.author.id != self.bot.user.id:
                                link_log.debug(
                                    f"Skipping standings message edit because message {message.jump_url} in tournament `{tournament.title}` "
                                    f"is authored by user {message.author.id}, not the bot."
                                )
                        else:
                            link_log.error(f"Could not find standings message for concluded tournament: {tournament_message.jump_url}")

                    pairings_message = None
                    if current_round and current_round.message_id_pairings:
                        pairings_message = await tournament.get_message(current_round.message_id_pairings)
                    await common_views.swap_concluded_round_pins(pairings_message, standings_message, tournament.title)

                    return tournament  # concluded

                if current_round.message_id_standings:
                    # standings have been posted
                    message = await tournament.get_message(current_round.message_id_standings)
                    if message and isinstance(message.channel, discord.Thread) and message.channel.archived:
                        await message.channel.edit(archived=False)
                    if message:
                        await common_views.update_standings(tournament, None)
                if current_round.message_id_pairings:
                    # pairings have been posted — unarchive thread if needed before update
                    message = await tournament.get_message(current_round.message_id_pairings)
                    if message and isinstance(message.channel, discord.Thread) and message.channel.archived:
                        await message.channel.edit(archived=False)
                await common_views.update_pairings(tournament, current_round)

                link_log.info(f"Turnier geladen: {tournament.title}")
                return None
            except discord.errors.NotFound:
                log.warning(f"Turnier konnte nicht geladen werden (message_path={message_path}): Channel oder Nachricht wurde vermutlich gelöscht.")
                return None
            finally:
                tournament.suppress_overview_update = False

        load_semaphore = asyncio.Semaphore(3)

        async def _load_one_limited(message_path: str, tournament: SpelltableTournament):
            async with load_semaphore:
                return await _load_one(message_path, tournament)

        results = await asyncio.gather(
            *[_load_one_limited(k, v) for k, v in loaded_tournaments.items()],
            return_exceptions=True,
        )

        concluded_tournaments_for_hof: list[SpelltableTournament] = []
        for result in results:
            if isinstance(result, Exception):
                log.error(
                    f"Unexpected error loading tournament during startup: "
                    f"{type(result).__name__}: {result}"
                )
            elif isinstance(result, SpelltableTournament):
                concluded_tournaments_for_hof.append(result)

        await update_hall_of_fame_batch(concluded_tournaments_for_hof)
        await update_tournament_message(self.bot)

        log.info(self.__class__.__name__ + " is ready")

    @slash_command(description="Pingt alle Spieler, die ihre Matches noch nicht gemeldet haben")
    async def ping_missing_matches(
        self,
        ctx:ApplicationContext,
        message: Annotated[str, Option(str, description="Die Nachricht, die an die Spieler gesendet werden soll")]
    ):
        # Check privilege and context
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.respond("Dieser Befehl kann nur in einem Turnier-Thread ausgeführt werden.", ephemeral=True)
            return
        if not isinstance(ctx.author, discord.Member):
            await ctx.respond("Dieser Befehl kann nur in einem Server ausgefuehrt werden.", ephemeral=True)
            return
        
        this_tournament = None
        for id, tournament in list(active_tournaments.items()):
            tournament_message = await tournament.message
            if not tournament_message:
                continue
            if tournament_message.channel.id == ctx.channel.id:
                this_tournament = tournament
                break
        if not this_tournament:
            await ctx.respond("Kein Turnier in diesem Thread gefunden.", ephemeral=True)
            return
        
        if not (
            ctx.author.id == this_tournament.organizer_id or
            any(role.name in ["Admin", "Moderator"] for role in ctx.author.roles)
        ):
            await ctx.respond("Nur der Turnier-Organisator, ein Admin oder ein Moderator darf diesen Befehl verwenden.", ephemeral=True)
            return
        # Privilege and context ok

        if not this_tournament.swiss_tournament:
            await ctx.respond("Dieser Befehl ist erst verfügbar, wenn das Turnier gestartet wurde.", ephemeral=True)
            return

        current_round = this_tournament.swiss_tournament.current_round()
        open_matches = []
        for match in current_round.matches:
            if not match.is_finished():
                open_matches.append(match)

        message_str = "Die folgenden Matches sind noch offen:"
        for open_match in open_matches:
            open_match:swiss_mtg.Match
            if not open_match.player1 or not open_match.player2:
                continue
            message_str += f"\n<@{open_match.player1.player_id}> vs <@{open_match.player2.player_id}>"

        message_str += f"\n\nNachricht von der Organisation:\n>>> {message}"
        await ctx.channel.send(message_str)

    @slash_command(description="Pingt alle Turnierteilnehmer")
    async def ping_all_participants(
        self,
        ctx:ApplicationContext,
        message: Annotated[str, Option(str, description="Die Nachricht, die an alle Teilnehmer gesendet werden soll")]
    ):
        # Check privilege and context
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.respond("Dieser Befehl kann nur in einem Turnier-Thread ausgeführt werden.", ephemeral=True)
            return
        if not isinstance(ctx.author, discord.Member):
            await ctx.respond("Dieser Befehl kann nur in einem Server ausgefuehrt werden.", ephemeral=True)
            return
        
        this_tournament = None
        for id, tournament in list(active_tournaments.items()):
            tournament_message = await tournament.message
            if not tournament_message:
                continue
            if tournament_message.channel.id == ctx.channel.id:
                this_tournament = tournament
                break
        if not this_tournament:
            await ctx.respond("Kein Turnier in diesem Thread gefunden.", ephemeral=True)
            return
        
        if not (
            ctx.author.id == this_tournament.organizer_id or
            any(role.name in ["Admin", "Moderator"] for role in ctx.author.roles)
        ):
            await ctx.respond("Nur der Turnier-Organisator, ein Admin oder ein Moderator darf diesen Befehl verwenden.", ephemeral=True)
            return
        # Privilege and context ok

        if not this_tournament.swiss_tournament:
            await ctx.respond("Dieser Befehl ist erst verfügbar, wenn das Turnier gestartet wurde.", ephemeral=True)
            return

        players = this_tournament.swiss_tournament.players
        
        if not players:
            await ctx.respond("Keine Teilnehmer in diesem Turnier gefunden.", ephemeral=True)
            return

        message_str = "📣 **Nachricht an alle Turnierteilnehmer:**\n"
        for player in players:
            message_str += f"<@{player.player_id}> "

        message_str += f"\n\n>>> {message}"
        await ctx.channel.send(message_str)
        await ctx.respond("Alle Teilnehmer wurden gepingt.", ephemeral=True)

    @slash_command(
        name="sync_tournament_users",
        description="Prüft und ergänzt User-Mappings in tournaments.db",
        guild_ids=[env.GUILD_ID],
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def sync_tournament_users(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.followup.send("Dieser Befehl kann nur auf dem Server verwendet werden.", ephemeral=True)
            return

        if env.SPELLTABLE_MEDIA_EXPORT_USER_ID is None:
            await ctx.followup.send(
                "`SPELLTABLE_MEDIA_EXPORT_USER_ID` ist nicht gesetzt. Bitte in den ENV-Variablen konfigurieren.",
                ephemeral=True,
            )
            return

        if ctx.author.id != env.SPELLTABLE_MEDIA_EXPORT_USER_ID:
            await ctx.followup.send("Du darfst diesen Befehl nicht verwenden.", ephemeral=True)
            return

        stats = await self._sync_tournament_user_mappings(ctx.guild)
        referenced_user_ids: list[int] = stats["referenced_user_ids"]
        missing_row_ids: list[int] = stats["missing_row_ids"]
        incomplete_row_ids: list[int] = stats["incomplete_row_ids"]
        unresolved_user_ids: list[int] = stats["unresolved_user_ids"]

        embed = discord.Embed(
            title="Turnier-User-Mappings synchronisiert",
            color=discord.Color.green() if not unresolved_user_ids else discord.Color.orange(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Turniere", value=str(stats["tournaments"]), inline=True)
        embed.add_field(name="Referenzierte User-IDs", value=str(len(referenced_user_ids)), inline=True)
        embed.add_field(name="Neu angelegt", value=str(stats["created_count"]), inline=True)
        embed.add_field(name="Ergänzt", value=str(stats["updated_count"]), inline=True)
        embed.add_field(name="Fehlende Rows vorher", value=str(len(missing_row_ids)), inline=True)
        embed.add_field(name="Unvollständige Rows vorher", value=str(len(incomplete_row_ids)), inline=True)

        if unresolved_user_ids:
            preview = ", ".join(str(user_id) for user_id in unresolved_user_ids[:20])
            if len(unresolved_user_ids) > 20:
                preview += ", ..."
            embed.add_field(
                name="Nicht auflösbar",
                value=preview,
                inline=False,
            )
        elif not referenced_user_ids:
            embed.description = "Für diesen Server wurden keine Turnier-User-IDs in der Datenbank gefunden."
        else:
            embed.description = "Alle fehlenden, unvollständigen oder veralteten User-Mappings wurden geprüft und soweit möglich aktualisiert."

        await ctx.followup.send(embed=embed, ephemeral=True)

    # @has_role("Moderator")
    @slash_command(name="turnier_erstellen", description="Erstelle ein Spelltable Turnier für den Server")
    async def turnier_erstellen(
        self,
        ctx:ApplicationContext,
        titel: Annotated[str, Option(str, description="Der Titel, den das Turnier tragen soll")]
    ):
        # Defer the interaction response to avoid timeout
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            # Interaction expired before we could defer - the bot can't respond
            log.error("Interaction expired before defer could be called")
            return
        if type(ctx.channel) != discord.TextChannel:
            await ctx.followup.send("Dieser Befehl kann nur in einem Textkanal ausgeführt werden.", ephemeral=True)
            return
        if type(ctx.author) != discord.Member:
            await ctx.followup.send("Du bist kein Mitglied dieses Servers!", ephemeral=True)
            return
        if ctx.guild is None:
            await ctx.followup.send("Dieser Befehl kann nur in einem Server verwendet werden.", ephemeral=True)
            return

        # Nur Turnierorganisatoren dürfen in Känalen unter der Kategorie "Spelltable Turniere" Turniere erstellen
        official_tournament_roles = ["Turnier-Organisation", "Moderator", "Admin"]
        if (ctx.channel.category
            and ctx.channel.category.name == "Spelltable Turniere"
            and not any(role.name in official_tournament_roles for role in ctx.author.roles)):
            # Use official_tournament_roles to look up roles
            roles = [discord.utils.get(ctx.guild.roles, name=role_name) for role_name in official_tournament_roles]
            # Collect roles that are not None
            found_roles = [role for role in roles if role]
            not_found_roles = [name for role, name in zip(roles, official_tournament_roles) if not role]
            roles_mentions = ", ".join(role.mention for role in found_roles)
            message = ""
            if roles_mentions:
                message += f"Du hast keine der Rolle(n) {roles_mentions} und darfst daher keine offiziellen Radio Ravnica Turniere erstellen!\nDu darfst gerne in einem anderen Kanal, der nicht unter der Kategorie \"Spelltable Turniere\" steht, ein Turnier erstellen."
            if not_found_roles:
                message += f"\nDie Rolle(n) {', '.join(f'`{name}`' for name in not_found_roles)} existieren nicht... Bitte benachrichtige einen Moderator des Servers!"
            await ctx.followup.send(message.strip(), ephemeral=True)
            return

        tournament = SpelltableTournament(ctx.guild, titel, ctx.author.id, self.bot)
        tournament.organizer = ctx.author
        # Auto-detect format from channel name
        detected_format = auto_detect_format_from_channel(ctx.channel)
        if detected_format:
            tournament.format_name = detected_format
        view = await EditTournamentView.create(tournament, ctx.channel)
        await ctx.followup.send(
            embed=await tournament.to_embed(),
            view=view,
            ephemeral=True
        )

    @tasks.loop(time=time(hour=0, minute=0, second=1, tzinfo=env.TIMEZONE))
    async def schedule_midnight_update(self):
        """
        Task to update the tournament message shortly after midnight.
        """
        # Execute shortly after midnight in configured timezone.
        await update_tournament_message(self.bot)

    @schedule_midnight_update.before_loop
    async def before_update_task(self):
        """
        Wait until the bot is ready before starting the task.
        """
        await self.bot.wait_until_ready()

    async def _autocomplete_banned_users(self, ctx: discord.AutocompleteContext):
        query = (ctx.value or "").strip().lower()
        guild = getattr(ctx.interaction, "guild", None)

        db = SessionLocal()
        try:
            banned_users = db.query(BannedUser).all()
        finally:
            db.close()

        choices: list[discord.OptionChoice] = []
        for ban in banned_users:
            user_id_str = str(ban.user_id)
            display = user_id_str
            if guild is not None:
                try:
                    member = guild.get_member(int(user_id_str))
                    if member is not None:
                        display = f"{member.display_name} ({member.name})"
                except (TypeError, ValueError):
                    pass

            label = f"{display} [{user_id_str}]"
            searchable_text = f"{display} {user_id_str}".lower()
            if query and query not in searchable_text:
                continue

            choices.append(discord.OptionChoice(name=label[:100], value=user_id_str))
            if len(choices) >= 25:
                break

        return choices

    @slash_command(
        name="turnier_sperre",
        description="Sperrt einen Spieler für eine bestimmte Zeit von Turnieren",
        guild_ids=[env.GUILD_ID],
    )
    @commands.has_role("Moderator")
    async def ban_user_from_tournaments(
        self,
        ctx: ApplicationContext,
        mitglied: Annotated[discord.Member, Option(discord.Member, description="Der betroffene Spieler")],
        zeit: Annotated[str, Option(str, description="Dauer der Sperre (z.B. '2 Wochen', '30 Tage')")],
        aus_offenen_abmelden: Annotated[
            bool,
            Option(bool, description="Aus offenen Turnier-Anmeldungen austragen", required=False),
        ] = False,
        aus_laufenden_entfernen: Annotated[
            bool,
            Option(bool, description="Aus aktuell laufenden Turnieren entfernen", required=False),
        ] = False,
        grund: Annotated[str | None, Option(str, description="Begründung (optional)", required=False)] = None,
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.followup.send("Dieser Befehl kann nur auf dem Server verwendet werden.", ephemeral=True)
            return

        parsed_until = date_time_interpretation.parse_date(zeit)
        if parsed_until is None:
            await ctx.followup.send(
                "Die angegebene Zeit konnte nicht verstanden werden. Beispiel: `2 Wochen`, `30 Tage`, `in 12 Stunden`.",
                ephemeral=True,
            )
            return

        if parsed_until <= datetime.now(parsed_until.tzinfo):
            await ctx.followup.send("Die Sperrdauer muss in der Zukunft liegen.", ephemeral=True)
            return

        banned_until_utc_naive = parsed_until.astimezone(timezone.utc).replace(tzinfo=None)

        db = SessionLocal()
        try:
            existing_ban = db.query(BannedUser).filter(BannedUser.user_id == str(mitglied.id)).first()

            if existing_ban:
                await ctx.followup.send(
                    f"{mitglied.mention} ist bereits gesperrt.",
                    ephemeral=True,
                )
                return

            new_ban = BannedUser(
                user_id=str(mitglied.id),
                banned_by=str(ctx.author.id),
                reason=grund,
                banned_until=banned_until_utc_naive,
            )
            db.add(new_ban)
            db.commit()

            async def remove_user_from_open_registration(tournament: SpelltableTournament) -> str | None:
                if tournament.swiss_tournament is not None:
                    return None

                is_registered = mitglied.id in tournament.users or mitglied.id in tournament.waitlist
                if not is_registered:
                    return None

                await tournament.user_state(
                    mitglied.id,
                    ParticipationState.DECLINE,
                    forced_by=ctx.author.id,
                    forced_reason="Turnier-Sperre",
                )
                tournament_message = await tournament.message
                if tournament_message:
                    return tournament_message.jump_url
                return None

            async def remove_user_from_running_tournament(tournament: SpelltableTournament) -> str | None:
                swiss_tournament = tournament.swiss_tournament
                if swiss_tournament is None or swiss_tournament.winner or tournament.cancelled:
                    return None

                player = swiss_tournament.player_by_id(mitglied.id)
                if player is None or player.dropped:
                    return None

                player.dropped = True
                await tournament.save_tournament()

                tournament_message = await tournament.message
                if tournament_message:
                    link_log.info(
                        f"Drop erzwungen: Moderator <@{ctx.author.id}> hat Benutzer <@{mitglied.id}> im laufenden "
                        f"Turnier {tournament_message.jump_url} als gedroppt markiert. Grund: Turnier-Sperre."
                    )
                    return tournament_message.jump_url

                link_log.info(
                    f"Drop erzwungen: Moderator <@{ctx.author.id}> hat Benutzer <@{mitglied.id}> im laufenden "
                    f"Turnier '{tournament.title}' als gedroppt markiert. Grund: Turnier-Sperre."
                )
                return None

            removed_from_open_count = 0
            removed_from_running_count = 0
            removed_open_links: list[str] = []
            removed_running_links: list[str] = []

            for tournament in list(active_tournaments.values()):
                if not isinstance(tournament, SpelltableTournament):
                    continue
                try:
                    if aus_offenen_abmelden:
                        tournament_link = await remove_user_from_open_registration(tournament)
                        if tournament_link is not None:
                            removed_from_open_count += 1
                            removed_open_links.append(tournament_link)

                    if aus_laufenden_entfernen:
                        running_tournament_link = await remove_user_from_running_tournament(tournament)
                        if running_tournament_link is not None:
                            removed_from_running_count += 1
                            removed_running_links.append(running_tournament_link)
                except Exception as error:
                    log.error(
                        f"Fehler beim Entfernen von <@{mitglied.id}> aus einem Turnier in '{tournament.title}': {error}"
                    )

            reason_text = f"\nGrund: {grund}" if grund else ""
            until_text = f"\nSperre bis: <t:{int(parsed_until.timestamp())}:F> (<t:{int(parsed_until.timestamp())}:R>)"
            open_removal_text = (
                f"\nAus offenen Anmeldungen entfernt: {removed_from_open_count}"
                if aus_offenen_abmelden
                else ""
            )
            running_removal_text = (
                f"\nAus laufenden Turnieren entfernt: {removed_from_running_count}"
                if aus_laufenden_entfernen
                else ""
            )
            await ctx.followup.send(
                f"{mitglied.mention} wurde für Turniere gesperrt.{until_text}{reason_text}{open_removal_text}{running_removal_text}",
                ephemeral=True,
            )
            link_log.info(
                f"Turnier-Ausschluss gesetzt: Moderator <@{ctx.author.id}> hat Benutzer <@{mitglied.id}> bis "
                f"{discord.utils.format_dt(parsed_until, 'F')} ({discord.utils.format_dt(parsed_until, 'R')}) "
                f"von zukünftigen Turnieren ausgeschlossen. Grund: {grund or 'kein Grund angegeben'}. "
                f"Aus offenen Anmeldungen entfernt: {removed_from_open_count}. Aus laufenden Turnieren entfernt: {removed_from_running_count}."
            )
            for tournament_link in removed_open_links:
                link_log.info(
                    f"Offene Turnier-Anmeldung entfernt: Benutzer <@{mitglied.id}> wurde im Zuge einer Turnier-Sperre aus {tournament_link} ausgetragen."
                )
            for tournament_link in removed_running_links:
                link_log.info(
                    f"Laufendes Turnier: Benutzer <@{mitglied.id}> wurde im Zuge einer Turnier-Sperre in {tournament_link} als gedroppt markiert."
                )
        finally:
            db.close()

    @slash_command(
        name="turnier_sperre_aufheben",
        description="Hebt eine Turniersperre für einen Spieler auf",
        guild_ids=[env.GUILD_ID],
    )
    @commands.has_role("Moderator")
    async def unban_user_from_tournaments(
        self,
        ctx: ApplicationContext,
        mitglied: Annotated[
            str,
            Option(
                str,
                description="Gesperrter Spieler",
                autocomplete=_autocomplete_banned_users,
            ),
        ],
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.followup.send("Dieser Befehl kann nur auf dem Server verwendet werden.", ephemeral=True)
            return

        try:
            user_id = int(mitglied)
        except (TypeError, ValueError):
            await ctx.followup.send("Ungültiger Spieler. Bitte einen Eintrag aus der Vorschlagsliste auswählen.", ephemeral=True)
            return

        member = ctx.guild.get_member(user_id)
        mention = member.mention if member else f"<@{user_id}>"

        db = SessionLocal()
        try:
            ban = db.query(BannedUser).filter(BannedUser.user_id == str(user_id)).first()
            if not ban:
                await ctx.followup.send(
                    f"{mention} ist nicht gesperrt.",
                    ephemeral=True,
                )
                return

            db.delete(ban)
            db.commit()
            await ctx.followup.send(
                f"Die Turniersperre für {mention} wurde aufgehoben.",
                ephemeral=True,
            )
            link_log.info(
                f"Turnier-Ausschluss aufgehoben: Moderator <@{ctx.author.id}> hat den Ausschluss von Benutzer <@{user_id}> für zukünftige Turniere aufgehoben."
            )
        finally:
            db.close()

    @slash_command(
        name="turnier_sperrliste",
        description="Zeigt alle aktuell gesperrten Turnierspieler",
        guild_ids=[env.GUILD_ID],
    )
    @commands.has_role("Moderator")
    async def list_banned_users(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        db = SessionLocal()
        try:
            bans = db.query(BannedUser).order_by(BannedUser.banned_at.asc()).all()
        finally:
            db.close()

        if not bans:
            await ctx.followup.send("Es sind aktuell keine Spieler gesperrt.", ephemeral=True)
            return

        now = datetime.utcnow()
        lines: list[str] = []
        for ban in bans:
            user_mention = f"<@{ban.user_id}>"
            if ban.banned_until:
                if ban.banned_until <= now:
                    continue  # expired, skip (will be cleaned up on next sign-up attempt)
                until_ts = int(ban.banned_until.replace(tzinfo=timezone.utc).timestamp())
                until_str = f"bis <t:{until_ts}:F> (<t:{until_ts}:R>)"
            else:
                until_str = "unbefristet"

            reason_str = f" — {ban.reason}" if ban.reason else ""
            moderator_str = f" (gesperrt von <@{ban.banned_by}>)" if ban.banned_by else ""
            lines.append(f"• {user_mention}{moderator_str} {until_str}{reason_str}")

        if not lines:
            await ctx.followup.send("Es sind aktuell keine aktiven Sperren vorhanden.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🚫 Gesperrte Turnierspieler",
            description="\n".join(lines),
            color=discord.Color.red(),
            timestamp=datetime.utcnow(),
        )
        embed.set_footer(text=f"{len(lines)} aktive Sperre(n)")
        await ctx.followup.send(embed=embed, ephemeral=True)

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
