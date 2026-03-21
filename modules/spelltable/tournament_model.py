from datetime import datetime, timedelta
from ezcord import log
import logging
import traceback
import discord
from enum import StrEnum, auto
from modules import swiss_mtg, table_to_image
from modules.util.generate_calendar_image import generate_calendar
from modules.serializable import Serializable
from modules import env
from modules.db import (
    SessionLocal,
    Tournament as TournamentRecord,
    Participant,
    SwissTournamentRecord,
    SwissPlayer,
    SwissRound,
    SwissMatch,
    DiscordUser,
)
import pytz
from pathlib import Path
import asyncio
import aiohttp

timezone = pytz.timezone("Europe/Berlin")

class ParticipationState(StrEnum):
    PARTICIPATE = auto()
    TENTATIVE = auto()
    DECLINE = auto()

link_log = logging.getLogger("link_logger")

test_participants = []

if env.DEBUG:
    with open("test_participants.txt", "r", encoding="utf-8") as file:
        test_participants = [int(line.strip()) for line in file]

active_tournaments:dict[str, "SpelltableTournament"] = {}

async def save_tournaments():
    for tournament in active_tournaments.values():
        await tournament.save_tournament()


def _build_swiss_json(db, swiss_t_row: SwissTournamentRecord) -> dict:
    """Reconstruct the JSON-like dict that SwissTournament.deserialize() expects."""
    player_rows = (
        db.query(SwissPlayer)
        .filter(SwissPlayer.swiss_tournament_id == swiss_t_row.tournament_id)
        .all()
    )
    round_rows = (
        db.query(SwissRound)
        .filter(SwissRound.swiss_tournament_id == swiss_t_row.tournament_id)
        .order_by(SwissRound.round_number.asc())
        .all()
    )

    players = []
    for p_row in player_rows:
        deck_dict = None
        if p_row.deck_link or p_row.deck_archetype:
            deck_dict = {"link": p_row.deck_link, "archetype": p_row.deck_archetype}
        player_dict: dict = {"player_id": p_row.player_id, "deck": deck_dict}
        if p_row.dropped:
            player_dict["dropped"] = True
        players.append(player_dict)

    rounds = []
    for r_row in round_rows:
        match_rows = (
            db.query(SwissMatch)
            .filter(SwissMatch.swiss_round_id == r_row.id)
            .order_by(SwissMatch.id.asc())
            .all()
        )
        matches = []
        for m_row in match_rows:
            if m_row.player2_id == m_row.player1_id:
                raise ValueError(
                    f"Datenkorrektur erforderlich: Ungueltiges Self-Match in der Turnier-DB "
                    f"(match_id={m_row.id}, round_id={r_row.id}, player_id={m_row.player1_id})."
                )
            p2_key = "null" if m_row.player2_id is None else str(m_row.player2_id)
            matches.append({"wins": {
                str(m_row.player1_id): m_row.player1_wins,
                p2_key: m_row.player2_wins,
                "draws": m_row.draws,
            }})
        rounds.append({
            "round_number": r_row.round_number,
            "matches": matches,
            "message_pairings": r_row.message_id_pairings,
            "message_standings": r_row.message_id_standings,
        })

    winner = {"player_id": swiss_t_row.winner_player_id} if swiss_t_row.winner_player_id else None
    return {
        "rounds_count": swiss_t_row.rounds_count,
        "players": players,
        "rounds": rounds,
        "winner": winner,
    }


async def load_tournaments(bot) -> dict[str, "SpelltableTournament"]:
    tournaments: dict[str, "SpelltableTournament"] = {}
    db = SessionLocal()
    users_dirty = False
    known_users: dict[int, DiscordUser] = {}
    try:
        for user_row in db.query(DiscordUser).all():
            known_users[user_row.user_id] = user_row

        tournament_rows = db.query(TournamentRecord).all()
        for t_row in tournament_rows:
            try:
                guild = await bot.fetch_guild(t_row.guild_id)
                tournament = SpelltableTournament(guild, t_row.name, t_row.organizer_id, bot)
                tournament.description = t_row.description
                tournament.time = t_row.start_time
                tournament.message_id = t_row.message_id
                tournament.channel_id = t_row.channel_id
                tournament.max_participants = t_row.max_participants
                tournament.max_rounds = t_row.max_rounds
                tournament.days_per_match = t_row.days_per_match if t_row.days_per_match is not None else 7
                tournament.cancelled = t_row.cancelled

                # Participants
                participant_rows = (
                    db.query(Participant)
                    .filter(Participant.tournament_id == t_row.id, Participant.state != "waitlist")
                    .all()
                )
                for p_row in participant_rows:
                    tournament.users[p_row.user_id] = ParticipationState(p_row.state)

                # Waitlist ordered by insertion order
                waitlist_rows = (
                    db.query(Participant)
                    .filter(Participant.tournament_id == t_row.id, Participant.state == "waitlist")
                    .order_by(Participant.id.asc())
                    .all()
                )
                tournament.waitlist = [w.user_id for w in waitlist_rows]

                # Swiss tournament
                swiss_t_row = (
                    db.query(SwissTournamentRecord)
                    .filter(SwissTournamentRecord.tournament_id == t_row.id)
                    .first()
                )
                if swiss_t_row:
                    swiss_json = _build_swiss_json(db, swiss_t_row)
                    tournament.swiss_tournament = swiss_mtg.SwissTournament.deserialize(swiss_json)

                # Sync users table on load: add missing IDs and refresh known names
                tracked_user_ids = set(tournament.users.keys()) | set(tournament.waitlist) | {tournament.organizer_id}
                if tournament.swiss_tournament:
                    tracked_user_ids.update(player.player_id for player in tournament.swiss_tournament.players)

                for user_id in tracked_user_ids:
                    member = await tournament.get_member(user_id)
                    user_row = known_users.get(user_id)
                    if user_row is None:
                        user_row = DiscordUser(user_id=user_id)
                        db.add(user_row)
                        known_users[user_id] = user_row
                        users_dirty = True
                        row_changed = True
                    else:
                        row_changed = False

                    username = getattr(member, "name", None) if member else None
                    display_name = getattr(member, "display_name", None) if member else None

                    if username and user_row.username != username:
                        user_row.username = username
                        users_dirty = True
                        row_changed = True
                    if display_name and user_row.display_name != display_name:
                        user_row.display_name = display_name
                        users_dirty = True
                        row_changed = True

                    if row_changed:
                        user_row.updated_at = datetime.utcnow()

                tournament_id = f"{t_row.guild_id}/{t_row.channel_id}/{t_row.message_id}"
                tournaments[tournament_id] = tournament
            except Exception as e:
                message = str(e)
                if "Datenkorrektur erforderlich" in message:
                    link_log.error(f"Turnier konnte nicht geladen werden: {t_row.name}. {message}")
                    # Notify admin user about data integrity issue
                    try:
                        admin_user = await bot.fetch_user(699263016113405993)
                        if admin_user:
                            await admin_user.send(f"⚠️ **Turnier-Datenkorrektur erforderlich**\n\nTurnier: {t_row.name}\n\n{message}")
                    except Exception as dm_error:
                        log.debug(f"Konnte Benachrichtigung nicht senden: {dm_error}")
                else:
                    log.error(f"Failed to load tournament id={t_row.id} ({t_row.name}): {e}")

        if users_dirty:
            db.commit()
    finally:
        db.close()

    return tournaments
    
async def generate_tournament_message(tournaments: list["SpelltableTournament"]) -> str:
    # Sort tournaments by start date, placing those with `time=None` first
    tournaments = sorted(
        tournaments,
        key=lambda tournament: tournament.time if tournament.time is not None else datetime.min.replace(tzinfo=timezone)
    )

    # Separate ongoing and upcoming
    ongoing = []
    upcoming = []

    for tourney in tournaments:
        start = tourney.time
        end = tourney.calc_end() if start else None
        if tourney.swiss_tournament:
            ongoing.append((tourney, end))
        else:
            upcoming.append((tourney, end))

    async def format_tournament(t:SpelltableTournament, end):
        organizer = await t.organizer
        t_message = await t.message

        end_field = (
            f"> 🗓️ **Ende:** {discord.utils.format_dt(end, 'F')} ({discord.utils.format_dt(end, 'R')})\n"
            if end and end != t.time
            else ""
        )
        return (
            f"> 🏆 **{t.title}** {t_message.jump_url}\n"
            f"> #️⃣ Format: {t_message.channel.parent.mention} ({t_message.channel.parent.name})\n"
            f"> 🗓️ **Start:** {discord.utils.format_dt(t.time, 'F') if t.time else 'TBD'} ({discord.utils.format_dt(t.time, 'R') if t.time else 'TBD'})\n"
            f"{end_field}"
            f"> 👥 **Organisator:** {organizer.mention}"
        )

    # Build message parts
    msg = "## 🧙‍♂️ **RR Discord Turniere**\n\n"

    msg += "### 🗓️ **Geplante Turniere**\n"
    if upcoming:
        for tourney, end in upcoming:
            msg += await format_tournament(tourney, end) + "\n\n"
    else:
        msg += "> _Keine Turniere bisher geplant._\n\n"

    msg += "### 🔥 **Aktuell laufende Turniere**\n"
    if ongoing:
        for tourney, end in ongoing:
            msg += await format_tournament(tourney, end) + "\n\n"
    else:
        msg += "> _Aktuell laufen keine Turniere._\n\n"

    msg += f"---\n💬 Möchtest du auch ein Turnier erstellen? Verwende den Befehl </erstelle_turnier:{env.CREATE_TOURNAMENT_COMMAND_ID}> im passenden Kanal und es wird ein Thread für das Turnier erstellt!"

    return msg

async def update_tournament_message(bot:discord.Bot, trigger_guild:discord.Guild|None = None):
    if trigger_guild and trigger_guild.id != env.GUILD_ID:
        return  # Only update for the main guild
    guild:discord.Guild = bot.get_guild(env.GUILD_ID)
    log.debug(f"Updating tournament message")
    rr_tournaments:list["SpelltableTournament"] = [t for t in active_tournaments.values() if t.guild.id == guild.id]
    # Remove concluded or cancelled tournaments from rr_tournaments
    rr_tournaments = [
        tourney for tourney in rr_tournaments
        if not (tourney.swiss_tournament and (tourney.swiss_tournament.winner or tourney.cancelled))
    ]
    tourney_list_message = await generate_tournament_message(rr_tournaments)
    calendar_img = generate_calendar(rr_tournaments)
    calendar_file = None
    if calendar_img:
        calendar_file = discord.File(calendar_img, filename=calendar_img)
    calendar_message = None
    if env.SPELLTABLE_CALENDAR_MESSAGE_ID:
        channel:discord.TextChannel = guild.get_channel(env.SPELLTABLE_CALENDAR_CHANNEL_ID)
        try:
            calendar_message = await channel.fetch_message(env.SPELLTABLE_CALENDAR_MESSAGE_ID)
        except discord.NotFound:
            log.warning("Calendar message not found, creating a new one")

    if calendar_message:
        if calendar_file:
            await calendar_message.edit(content=tourney_list_message, attachments=[], file=calendar_file)
        else:
            await calendar_message.edit(content=tourney_list_message, attachments=[])
    else:
        if calendar_file:
            calendar_message = await guild.get_channel(env.SPELLTABLE_CALENDAR_CHANNEL_ID).send(tourney_list_message, file=calendar_file)
        else:
            calendar_message = await guild.get_channel(env.SPELLTABLE_CALENDAR_CHANNEL_ID).send(tourney_list_message)
        env.SPELLTABLE_CALENDAR_MESSAGE_ID = calendar_message.id
        env.save_to_env("SPELLTABLE_CALENDAR_MESSAGE_ID", calendar_message.id)
    link_log.debug(f"Tournament Overview Message updated: {calendar_message.jump_url}")

    tournament_count = len(rr_tournaments)
    activity_name = f"Veranstaltet {tournament_count} Turnier{'e' if tournament_count != 1 else ''}"
    activity = discord.CustomActivity(name=activity_name)
    await bot.change_presence(activity=activity)

async def use_custom_try(purpose:str, func, tournament:"SpelltableTournament"):
    try:
        await func()  # Execute the passed function
    except Exception as e:
        tourney_message = await tournament.message
        tb = traceback.format_exc()
        short_tb = tb[-1500:]  # Reserve room for code block markdown (10 characters)
        error_str = f"Beim {purpose} für das Turnier {tourney_message.jump_url} ist ein Fehler aufgetreten:"
        organizer = await tournament.organizer
        print(short_tb)
        await organizer.send(f"{error_str}\n```{short_tb}```")
        log.error(f"{error_str}\n```{short_tb}```")

class SpelltableTournament(Serializable):
    def __init__(self, guild:discord.Guild, title:str, organizer_id:int, bot:discord.Bot):
        self.title = title
        self.description = None
        self.time:datetime = None
        self.organizer_id = organizer_id
        self.users:dict[int,ParticipationState] = {} 
        self.message_id:int = None
        self.channel_id:int = None
        self.swiss_tournament:swiss_mtg.SwissTournament = None
        self.max_participants = None
        self.waitlist:list[int] = []
        self.guild = guild
        self.max_rounds = None
        self.days_per_match = 7
        self.bot:discord.Bot = bot
        self.cancelled = None

        if env.DEBUG:
            for user_id in test_participants:
                self.users[user_id] = ParticipationState.PARTICIPATE

        self._organizer:discord.Member|None = None
        self._message:discord.Message|None = None

        self.members:dict[int, discord.Member] = {}

    async def get_member(self, user_id) -> discord.Member:
        if user_id in self.members:
            return self.members[user_id]
        try:
            user = await discord.utils.get_or_fetch(self.guild, "member", user_id)
            self.members[user_id] = user
            return user
        except:
            if env.DEBUG:
                try:
                    user = await discord.utils.get_or_fetch(self.bot, "user", user_id)
                    return user
                except discord.errors.NotFound: 
                    return None
            link_log.debug(f"User with ID {user_id} (<@{user_id}>) not found in guild {self.guild.id} for tournament {self.title}.")
            try:
                user = await discord.utils.get_or_fetch(self.bot, "user", user_id)
                self.members[user_id] = user
                return user
            except discord.errors.NotFound:
                link_log.debug(f"User ID {user_id} does not exist on Discord (Unknown User).")
                return None

    @property
    async def organizer(self) -> discord.Member|None:
        if not self._organizer:
            try:
                self._organizer = await discord.utils.get_or_fetch(self.guild, "member", self.organizer_id)
            except:
                return None
        return self._organizer
    
    @organizer.setter
    def organizer(self, member:discord.Member):
        self._organizer = member
        self.organizer_id = member.id
    
    @property
    async def message(self) -> discord.Message|None:
        if not self._message:
            if not self.channel_id:
                return None
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
                # Support both Thread (new tournaments) and TextChannel (legacy tournaments)
                try:
                    self._message = await channel.fetch_message(self.message_id)
                except Exception as e:
                    log.error(f"Could not fetch message {self.message_id} from channel {self.channel_id}: {e}")
                    return None
            except discord.errors.Forbidden:
                log.error(f"Bot does not have permission to access channel {self.channel_id} in guild {self.guild.id} ({self.guild.name}).")
                return None
        return self._message
    
    @message.setter
    def message(self, message:discord.Message):
        self._message = message
        self.message_id = message.id
        self.channel_id = message.channel.id

    async def get_id(self):
        message = await self.message
        if message:
            return f"{message.guild.id}/{message.channel.id}/{message.id}"
        else:
            raise Exception("Message not found")
    
    def get_users_by_state(self, state:ParticipationState):
        return [user for user in self.users if self.users[user] == state]

    @classmethod
    async def deserialize(cls, data, bot): #, organizer, message):
        organizer_id = int(data["organizer_id"])
        guild = await bot.fetch_guild(data["guild_id"])

        tournament = cls(guild, data["name"], organizer_id, bot)
        tournament.description = data["description"]
        if "time" in data and data["time"]:
            tournament.time = datetime.fromisoformat(data["time"])

        tournament.max_rounds = data["max_rounds"]

        tournament.users = {int(k): v for k, v in data["users"].items()}
        tournament.message_id = int(data["message_id"])
        tournament.channel_id = int(data["channel_id"])
        tournament.days_per_match = int(data["days_per_match"])
        tournament.waitlist = data["waitlist"] if "waitlist" in data else []

        if "max_participants" in data and data["max_participants"]:
            tournament.max_participants = data["max_participants"]

        if "tournament" in data and data["tournament"]:
            swiss_tournament_data = data['tournament']
            tournament.swiss_tournament = swiss_mtg.SwissTournament.deserialize(swiss_tournament_data)

        tournament.cancelled = data.get("cancelled", False)

        return tournament
    
    async def serialize(self):
        message = await self.message
        organizer = await self.organizer
        return {
            "name": self.title,
            "organizer_id": organizer.id,
            "description": self.description,
            "time": self.time.isoformat() if self.time else None,
            "users": self.users,
            "waitlist": self.waitlist,
            "guild_id": message.guild.id,
            "message_id": message.id,
            "channel_id": message.channel.id,
            "max_participants": self.max_participants,
            "max_rounds": self.max_rounds,
            "tournament": self.swiss_tournament,
            "days_per_match": self.days_per_match,
            "cancelled": self.cancelled if self.cancelled else False,
        }
    
    async def check_waitlist(self, participants):
        if self.waitlist and len(participants) < self.max_participants:
            first_in_line_user_id = self.waitlist.pop(0)
            self.users[first_in_line_user_id] = ParticipationState.PARTICIPATE
            new_participant:discord.Member = await self.get_member(first_in_line_user_id)
            tournament_message = await self.message
            if not tournament_message:
                raise Exception("Tournament has no message, shouldn't happen")
            await new_participant.send(f"Es wurde ein Platz frei im Turnier frei und du wurdest nachgerückt. {tournament_message.jump_url}")

    async def user_state(self, userid:int, state:ParticipationState):
        # remove user
        self.waitlist.remove(userid) if userid in self.waitlist else None
        self.users.pop(userid, None) # Remove user from the dictionary if they exist

        # and set new
        message_str = None
        participants = self.get_users_by_state(ParticipationState.PARTICIPATE)
        if state == ParticipationState.PARTICIPATE and self.max_participants and len(participants) >= self.max_participants:
            self.waitlist.append(userid)
            message_str = f"Das Turnier ist bereits voll. Du wurdest auf die Liste der Nachrücker gesetzt.\nWird ein Platz frei, wirst du automatisch nachgerückt und benachrichtigt."
        else:
            if state != ParticipationState.DECLINE:
                self.users[userid] = state

        await self.check_waitlist(participants)
        tournament_message = await self.message
        link_log.info(f"Benutzer <@{userid}> hat den Status auf {state.name} für das Turnier {tournament_message.jump_url} gesetzt")
        await self.save_tournament()
        message = await self.message
        if message:
            embed = await self.to_embed()
            await message.edit(embed=embed)
        else:
            raise Exception("Message not found")
        return message_str

    def calc_round_count_and_text(self):
        participants = self.get_users_by_state(ParticipationState.PARTICIPATE)
        rec_round_count = swiss_mtg.recommended_rounds(len(participants))
        if self.max_rounds:
            current_round_count = min(self.max_rounds, rec_round_count)
            return current_round_count, f"Abhängig von der Spielerzahl. Aber Maximal {self.max_rounds}\nAktuell: {current_round_count}"
        else:
            return rec_round_count, f"Abhängig von der Spielerzahl.\nAktuell: {rec_round_count}"

    def calc_end(self):
        start = self.time
        days_per_match = self.days_per_match
        if days_per_match:
            round_count, text = self.calc_round_count_and_text()
            return start + timedelta(days=days_per_match*round_count)
        else:
            return start

    async def to_embed(self):
        embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=discord.Color.from_rgb(37, 88, 79)
        )
        organizer = await self.organizer
        if not organizer:
            raise Exception("Organizer not found")
        if organizer:
            embed.set_author(name=organizer.display_name, icon_url=organizer.avatar.url if organizer.avatar else None)
        if self.time:
            date_format_character = "D" if self.days_per_match else "F"
            embed.add_field(name="Start", value=discord.utils.format_dt(self.time, date_format_character)+"\n"+discord.utils.format_dt(self.time, 'R'), inline=True)

        round_count, text = self.calc_round_count_and_text()
        embed.add_field(name="Anzahl Runden", value=text, inline=True)
        
        embed.add_field(name=f"Tage pro Runde", value=f"{self.days_per_match if self.days_per_match > 0 else 'Wird am Stück gespielt'}", inline=True)

        waitlist = self.waitlist
        tentative = self.get_users_by_state(ParticipationState.TENTATIVE)

        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # await self.guild.chunk()

        participants = self.get_users_by_state(ParticipationState.PARTICIPATE)
        participant_members:list[discord.User] = []
        for uid in participants:
            member = await self.get_member(uid)
            if member:
                participant_members.append(member)

        participant_members.sort(key=lambda member: member.display_name.lower())
        embed.add_field(name=f"✅ Teilnehmer ({len(participants)}{f'/{self.max_participants}' if self.max_participants else ''})", value="\n".join([f"{p.display_name}" for p in participant_members]), inline=True)
        if self.max_participants:
            waitlist_members = [await self.get_member(uid) for uid in waitlist]
            # Filter out None values if some IDs weren't found
            waitlist_members = [m for m in waitlist_members if m is not None]
            embed.add_field(name=f"⌚ Nachrücker ({len(waitlist)})", value="\n".join([f"{p.display_name}" for p in waitlist_members]), inline=True)
        
        tentative_members = [await self.get_member(uid) for uid in tentative]
        # Filter out None values if some IDs weren't found
        tentative_members = [m for m in tentative_members if m is not None]
        embed.add_field(name=f"❓ Vielleicht ({len(tentative)})", value="\n".join([f"{p.display_name}" for p in tentative_members]), inline=True)
        return embed

    async def get_message(self, message_id) -> discord.Message|None:
        if message_id:
            tourney_message = await self.message
            if not tourney_message:
                return None
            try:
                return await tourney_message.channel.fetch_message(message_id)
            except discord.errors.NotFound:
                log.warning(
                    f"Referenced tournament message {message_id} was not found in channel {tourney_message.channel.id} for tournament {self.title}."
                )
                return None
            except discord.errors.Forbidden:
                log.warning(
                    f"Missing permission to fetch referenced tournament message {message_id} in channel {tourney_message.channel.id} for tournament {self.title}."
                )
                return None
        return None
    
    async def get_pairings(self, round=None) -> str:
        """
        Returns the pairings for the given round as a formatted string.
        If no round is provided, it uses the current round of the Swiss tournament.
        """
        if round is None:
            round = self.swiss_tournament.current_round()

        matchups:dict[str, str] = {}
        for match in round.matches:
            # Handle BYE
            if match.is_bye():
                player = match.player1 or match.player2
                if player:
                    mention = f"<@{player.player_id}>"
                    if player.dropped:
                        mention = f"~~{mention}~~"
                    player_member = await self.get_member(player.player_id)
                    matchups[player_member.display_name] = f"{mention} hat ein BYE"
                continue

            # Normal match
            p1, p2 = match.player1, match.player2
            if not (p1 and p2):
                continue
            
            mention_p1 = f"<@{p1.player_id}>"
            if p1.dropped:
                mention_p1 = f"~~{mention_p1}~~"
            
            mention_p2 = f"<@{p2.player_id}>"
            if p2.dropped:
                mention_p2 = f"~~{mention_p2}~~"
            
            player1_member = await self.get_member(p1.player_id)
            player2_member = await self.get_member(p2.player_id)
            matchups[player1_member.display_name] = f"{mention_p1} vs {mention_p2}"
            matchups[player2_member.display_name] = f"{mention_p2} vs {mention_p1}"

        if not matchups:
            return "Keine Paarungen verfügbar. Ein Neustart des Bots, sollte den Fehler beheben. <@356120044754698252>" # pings NudelForce

        for_message = "\n".join(matchups[player_name] for player_name in sorted(matchups, key=str.lower))

        return for_message
    
    async def save_tournament(self: "SpelltableTournament"):
        db = SessionLocal()
        try:
            # Find or create tournament record by unique message_id
            t_row = db.query(TournamentRecord).filter(TournamentRecord.message_id == self.message_id).first()
            if t_row is None:
                t_row = TournamentRecord()
                db.add(t_row)

            t_row.name = self.title
            t_row.organizer_id = self.organizer_id
            t_row.description = self.description
            t_row.start_time = self.time
            t_row.guild_id = self.guild.id
            t_row.channel_id = self.channel_id
            t_row.message_id = self.message_id
            t_row.max_participants = self.max_participants
            t_row.max_rounds = self.max_rounds
            t_row.days_per_match = self.days_per_match
            t_row.cancelled = bool(self.cancelled) if self.cancelled is not None else False
            db.flush()

            # Replace participants (delete + re-insert preserves in-memory order)
            db.query(Participant).filter(Participant.tournament_id == t_row.id).delete(synchronize_session=False)
            registered_user_ids = set(self.users.keys()) | set(self.waitlist)
            for user_id, state in self.users.items():
                db.add(Participant(tournament_id=t_row.id, user_id=user_id, state=str(state)))
            for user_id in self.waitlist:
                db.add(Participant(tournament_id=t_row.id, user_id=user_id, state="waitlist"))

            if self.swiss_tournament:
                # Ensure all registered users exist in users table once tournament has started
                for user_id in registered_user_ids:
                    existing_user = (
                        db.query(DiscordUser)
                        .filter(DiscordUser.user_id == user_id)
                        .first()
                    )
                    if existing_user is None:
                        db.add(DiscordUser(user_id=user_id, updated_at=datetime.utcnow()))

                # Upsert cached Discord display names
                for user_id, member in self.members.items():
                    existing_user = (
                        db.query(DiscordUser)
                        .filter(DiscordUser.user_id == user_id)
                        .first()
                    )
                    if existing_user is None:
                        existing_user = DiscordUser(user_id=user_id)
                        db.add(existing_user)
                    existing_user.display_name = member.display_name
                    existing_user.username = member.name
                    existing_user.updated_at = datetime.utcnow()

            # Sync Swiss tournament data
            if self.swiss_tournament:
                swiss_t_row = (
                    db.query(SwissTournamentRecord)
                    .filter(SwissTournamentRecord.tournament_id == t_row.id)
                    .first()
                )
                if swiss_t_row is None:
                    swiss_t_row = SwissTournamentRecord(tournament_id=t_row.id)
                    db.add(swiss_t_row)

                winner = self.swiss_tournament.winner
                swiss_t_row.rounds_count = self.swiss_tournament.rounds_count
                swiss_t_row.winner_player_id = winner.player_id if winner else None
                db.flush()

                # Delete rounds + matches bottom-up, then players
                round_ids = [
                    r[0] for r in db.query(SwissRound.id)
                    .filter(SwissRound.swiss_tournament_id == swiss_t_row.tournament_id)
                    .all()
                ]
                if round_ids:
                    db.query(SwissMatch).filter(
                        SwissMatch.swiss_round_id.in_(round_ids)
                    ).delete(synchronize_session=False)
                db.query(SwissRound).filter(
                    SwissRound.swiss_tournament_id == swiss_t_row.tournament_id
                ).delete(synchronize_session=False)
                db.query(SwissPlayer).filter(
                    SwissPlayer.swiss_tournament_id == swiss_t_row.tournament_id
                ).delete(synchronize_session=False)

                for player in self.swiss_tournament.players:
                    deck = player.deck
                    db.add(SwissPlayer(
                        swiss_tournament_id=swiss_t_row.tournament_id,
                        player_id=player.player_id,
                        dropped=player.dropped,
                        deck_link=deck.link if deck else None,
                        deck_archetype=deck.archetype if deck else None,
                    ))

                for round_obj in self.swiss_tournament.rounds:
                    round_row = SwissRound(
                        swiss_tournament_id=swiss_t_row.tournament_id,
                        round_number=round_obj.round_number,
                        message_id_pairings=round_obj.message_id_pairings,
                        message_id_standings=round_obj.message_id_standings,
                    )
                    db.add(round_row)
                    db.flush()

                    for match in round_obj.matches:
                        p1 = match.player1
                        p2 = match.player2
                        db.add(SwissMatch(
                            swiss_round_id=round_row.id,
                            player1_id=p1.player_id if p1 else None,
                            player2_id=p2.player_id if p2 else None,
                            player1_wins=int(match.wins.get(p1, 0) or 0),
                            player2_wins=int(match.wins.get(p2, 0) or 0),
                            draws=int(match.wins.get("draws", 0) or 0),
                        ))
            else:
                # Remove Swiss data if tournament no longer has it
                swiss_t_row = (
                    db.query(SwissTournamentRecord)
                    .filter(SwissTournamentRecord.tournament_id == t_row.id)
                    .first()
                )
                if swiss_t_row:
                    round_ids = [
                        r[0] for r in db.query(SwissRound.id)
                        .filter(SwissRound.swiss_tournament_id == swiss_t_row.tournament_id)
                        .all()
                    ]
                    if round_ids:
                        db.query(SwissMatch).filter(
                            SwissMatch.swiss_round_id.in_(round_ids)
                        ).delete(synchronize_session=False)
                    db.query(SwissRound).filter(
                        SwissRound.swiss_tournament_id == swiss_t_row.tournament_id
                    ).delete(synchronize_session=False)
                    db.query(SwissPlayer).filter(
                        SwissPlayer.swiss_tournament_id == swiss_t_row.tournament_id
                    ).delete(synchronize_session=False)
                    db.delete(swiss_t_row)

            db.commit()
        except Exception as e:
            db.rollback()
            log.error(f"Failed to save tournament {self.title}: {e}")
            raise
        finally:
            db.close()

        await update_tournament_message(self.bot, trigger_guild=self.guild)


    async def standings_to_image(self, round=None, include_metagame=False) -> str:
        if round is None:
            round = self.swiss_tournament.current_round()
        players = self.swiss_tournament.players
        swiss_mtg.sort_players_by_standings(players)

        rows = []
        for rank, player in enumerate(players):
            member = await self.get_member(player.player_id)
            player_display_name = member.display_name if member else f"User({player.player_id})"
            rows.append([
                rank+1,
                (player_display_name, player.dropped),
                player.calculate_match_points(),
                player.get_match_results(),
                f"{player.calculate_opponent_match_win_percentage():.4%}",
                f"{player.calculate_game_win_percentage():.4%}",
                f"{player.calculate_opponent_game_win_percentage():.4%}",
            ])

        data = {
            "headers": ["Rang", "Name", "Punkte", "Matches", "OMW", "GW", "OGW"],
            "rows": rows
        }
        if include_metagame:
            for row, player in zip(data["rows"], players):
                archetype = player.deck.archetype if player.deck and player.deck.archetype else "?"
                row.append(archetype)
            data["headers"].append("Deck")

        id = await self.get_id()
        filename = f'tmp/{id.replace("/", "_")}_standings_round_{round.round_number}.png'
        table_to_image.generate_image(data, filename, "assets/beleren.ttf")

        return filename

    
    async def metagame_to_image(self) -> str:
        swiss_mtg.sort_players_by_standings(self.swiss_tournament.players)
        players:list[swiss_mtg.Player] = self.swiss_tournament.players
        rows = []

        for rank, player in enumerate(players):
            member = await self.get_member(player.player_id)
            player_display_name = member.display_name if member else f"User({player.player_id})"
            archetype = player.deck.archetype if player.deck and player.deck.archetype else "?"
            rows.append([
                rank+1,
                (player_display_name, player.dropped),
                archetype
            ])

        data = {
            "headers": ["Platz", "Spieler", "Archetyp"],
            "rows": rows
        }

        id = await self.get_id()
        filename = f'tmp/{id.replace("/", "_")}_metagame.png'
        table_to_image.generate_image(data, filename, "assets/beleren.ttf")
        return filename
    

    async def metagame_message_text(self) -> str:
        metagame_message_text = "🏆Finales Ergebnis\nDecks:\n"
        players:list[swiss_mtg.Player] = self.swiss_tournament.players
        for rank, player in enumerate(players):
            archetype = player.deck.archetype if player.deck and player.deck.archetype else "?"
            link = player.deck.link if player.deck else None
            player_user = await self.get_member(player.player_id)
            url_text = f"[{archetype}]({link})" if link else archetype
            metagame_message_text += f"{rank+1}. {player_user.mention} - {url_text}\n"

        return metagame_message_text
    
    async def create_metagame_embed(self) -> discord.Embed:
        """Create an embed with final standings and deck lists."""
        embed = discord.Embed(
            title="🏆 Finales Ergebnis",
            color=discord.Color.from_rgb(37, 88, 79)
        )
        
        players:list[swiss_mtg.Player] = self.swiss_tournament.players
        deck_list = ""
        
        for rank, player in enumerate(players, 1):
            archetype = player.deck.archetype if player.deck and player.deck.archetype else "?"
            link = player.deck.link if player.deck else None
            player_user = await self.get_member(player.player_id)
            url_text = f"[{archetype}]({link})" if link else archetype
            line = f"{rank}. {player_user.mention} - {url_text}\n"
            
            # Discord embed description limit is 4096 chars
            if len(deck_list) + len(line) > 4000:
                deck_list += "\n_(Weitere Platzierungen siehe Bild)_"
                break
            deck_list += line
        
        embed.description = deck_list
        return embed
    
    async def update_metagame(self):
        if not (self.swiss_tournament and self.swiss_tournament.winner):
            return
        try:
            metagame_standings_image = await self.standings_to_image(include_metagame=True)
            attachment_name = Path(metagame_standings_image).name
            metagame_file = discord.File(metagame_standings_image, filename=attachment_name)
            message = await self.message
            embed = await self.create_metagame_embed()
            
            standings_message = await self.get_message(self.swiss_tournament.current_round().message_id_standings)
            if standings_message:
                # Two-step update so stale image attachments are guaranteed to be removed.
                # Retry logic for transient connection errors
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await standings_message.edit(content="", embeds=[], attachments=[], suppress=False)
                        await standings_message.edit(content="", embeds=[embed], attachments=[], file=metagame_file, suppress=False)
                        return
                    except discord.errors.Forbidden:
                        # Bot doesn't have permission to edit this message (likely authored by another user)
                        logging.warning(f"Cannot edit metagame standings message: Bot does not have permission (message may be authored by another user)")
                        return
                    except (aiohttp.ClientOSError, aiohttp.ClientConnectionError, discord.errors.HTTPException) as e:
                        if attempt < max_retries - 1:
                            # Exponential backoff: 1s, 2s, 4s
                            wait_time = 2 ** attempt
                            logging.warning(f"Failed to update metagame (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            # Give up after max retries
                            logging.error(f"Failed to update metagame after {max_retries} attempts: {e}")
                            raise
            else:
                standings_message = await message.channel.send(embeds=[embed], file=metagame_file, suppress=False)
                self.swiss_tournament.current_round().message_id_standings = standings_message.id
                await self.save_tournament()
        except Exception as e:
            # Log the error but don't crash the bot during on_ready
            logging.error(f"Error updating metagame: {e}", exc_info=True)

    async def pairings_to_image(self, round:swiss_mtg.Round|None=None) -> str:
        if round is None:
            round = self.swiss_tournament.current_round()
        id = await self.get_id()

        rows = []

        for match in round.matches:
            # Handle BYE
            if match.is_bye():
                player = match.player1 or match.player2
                if player:
                    p_mp = player.calculate_match_points(round.round_number-1)
                    member = await self.get_member(player.player_id)
                    player_display_name = member.display_name if member else f"User({player.player_id})"
                    rows.append([(f"{player_display_name} ({p_mp})", player.dropped), "BYE", "2 - 0"])
                continue

            # Normal match
            p1, p2 = match.player1, match.player2
            if not (p1 and p2):
                continue

            if match.is_finished():
                win_p1, win_p2, draws = match.wins.values()
                score_p1 = f"{win_p1} - {win_p2}" + (f" - {draws}" if draws else "")
                score_p2 = f"{win_p2} - {win_p1}" + (f" - {draws}" if draws else "")
            else:
                score_p1 = score_p2 = "Ausstehend"

            # Fetch display names
            member_p1 = await self.get_member(p1.player_id)
            member_p2 = await self.get_member(p2.player_id)
            p1_display_name = member_p1.display_name if member_p1 else f"User({p1.player_id})"
            p2_display_name = member_p2.display_name if member_p2 else f"User({p2.player_id})"

            # Add both player perspectives
            p1_mp = p1.calculate_match_points(round.round_number-1)
            p2_mp = p2.calculate_match_points(round.round_number-1)
            rows.append([(f"{p1_display_name} ({p1_mp})", p1.dropped), (f"{p2_display_name} ({p2_mp})", p2.dropped), score_p1])
            rows.append([(f"{p2_display_name} ({p2_mp})", p2.dropped), (f"{p1_display_name} ({p1_mp})", p1.dropped), score_p2])

        # Sort rows by player name (first element of the tuple in column 0)
        rows.sort(key=lambda row: row[0][0].lower())

        data = {
            "headers": ["Spieler", "Gegner", "Match Ergebnis (S-N-U)"],
            "rows": rows
        }

        filename = f'tmp/{id.replace("/", "_")}_pairings_round_{round.round_number}_expanded.png'
        table_to_image.generate_image(data, filename, "assets/beleren.ttf")
        return filename