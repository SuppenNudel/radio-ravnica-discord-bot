

from datetime import datetime, timedelta
from ezcord import log
import json
import logging
import traceback
import discord
from enum import StrEnum, auto
from modules import swiss_mtg, table_to_image
from modules.util.generate_calendar_image import generate_calendar
from modules.serializable import Serializable
from modules import env
import os
import pytz

timezone = pytz.timezone("Europe/Berlin")

class ParticipationState(StrEnum):
    PARTICIPATE = auto()
    TENTATIVE = auto()
    DECLINE = auto()

link_log = logging.getLogger("link_logger")

test_participants = []
TOURNAMENTS_FOLDER = "tournaments"

if env.DEBUG:
    with open("test_participants.txt", "r", encoding="utf-8") as file:
        test_participants = [int(line.strip()) for line in file]  # Splits by spaces

active_tournaments:dict[str, "SpelltableTournament"] = {}

async def save_tournaments():
    for tournament in active_tournaments.values():
        await tournament.save_tournament()


async def load_tournaments(bot) -> dict[str, "SpelltableTournament"]:
    tournaments = {}

    if not os.path.exists(TOURNAMENTS_FOLDER):
        os.makedirs(TOURNAMENTS_FOLDER)  # Ensure the folder exists

    for filename in os.listdir(TOURNAMENTS_FOLDER):
        if filename.endswith(".json"):
            file_path = os.path.join(TOURNAMENTS_FOLDER, filename)
            with open(file_path, "r") as file:
                json_data = file.read()
                try:
                    raw_dict = json.loads(json_data)
                    tournament = await SpelltableTournament.deserialize(raw_dict, bot)
                    tournament_id = filename[:-5].replace("_", "/")  # Convert back to original ID format
                    tournaments[tournament_id] = tournament
                except Exception as e:
                    print(f"Failed to load {filename}: {e}")

    return tournaments
        
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Serializable):
            mapping = obj.serialize()
            return mapping
        return super().default(obj)
    
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
    log.info(f"Updating tournament message")
    rr_tournaments:list["SpelltableTournament"] = [t for t in active_tournaments.values() if t.guild.id == guild.id]
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
    link_log.info(f"Tournament Overview Message updated: {calendar_message.jump_url}")

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
            raise Exception(f"User with ID {user_id} not found in guild {self.guild.id}.")

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
                # thread:discord.Thread = await discord.utils.get_or_fetch(self.guild, "channel", self.channel_id)
                if isinstance(channel, discord.Thread):
                    thread = channel
                else:
                    raise TypeError(f"Expected Thread, got {type(channel)}")
                try:
                    # thread does not have get_message method, so we need to fetch the message
                    self._message = await thread.fetch_message(self.message_id)
                except Exception as e:
                    log.error(e)
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
            "cancelled": self.cancelled if self.cancelled else False
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

        await self.guild.chunk()

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
            return await tourney_message.channel.fetch_message(message_id)
        return None

    async def update_pairings(self, round:swiss_mtg.Round):
        pairings_message:discord.Message = await self.get_message(round.message_id_pairings)
        link_log.info(f"updating pairings {pairings_message.jump_url}")
        pairings_image = await self.pairings_to_image(round)
        pairings_file = discord.File(pairings_image, filename=pairings_image)
        if pairings_message:
            await pairings_message.edit(file=pairings_file, attachments=[], content=pairings_message.content or "Kein Inhalt verfügbar.")
        else:
            raise Exception("Pairings message not found.")

        await self.save_tournament()
    
    async def get_pairings(self, round=None) -> str:
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
        # Ensure the directory exists
        os.makedirs(TOURNAMENTS_FOLDER, exist_ok=True)
        concluded_folder = os.path.join(TOURNAMENTS_FOLDER, "concluded")
        os.makedirs(concluded_folder, exist_ok=True)

        # Convert tournament ID (slashes to underscores) for filename
        tournament_id = await self.get_id()
        filename = tournament_id.replace("/", "_") + ".json"
        file_path = os.path.join(TOURNAMENTS_FOLDER, filename)

        serialized = await self.serialize()
        try:
            with open(file_path, "w") as file:
                json.dump(serialized, file, cls=CustomJSONEncoder, indent=4)
            
            # If the tournament is concluded, move the file to the concluded folder
            if (self.swiss_tournament and self.swiss_tournament.winner) or self.cancelled:
                concluded_path = os.path.join(concluded_folder, filename)
                os.rename(file_path, concluded_path)
                del active_tournaments[tournament_id]
                print(f"Tournament {tournament_id} has been concluded and moved to {concluded_path}")
        except Exception as e:
            print(f"Error saving tournament {tournament_id}: {e}")
        await update_tournament_message(self.bot, trigger_guild=self.guild)


    async def standings_to_image(self, round=None) -> str:
        if round is None:
            round = self.swiss_tournament.current_round()
        players = self.swiss_tournament.players
        swiss_mtg.sort_players_by_standings(players)

        rows = [[
            rank+1,
            (player.name, player.dropped),
            player.calculate_match_points(),
            player.get_match_results(),
            f"{player.calculate_opponent_match_win_percentage():.4%}",
            f"{player.calculate_game_win_percentage():.4%}",
            f"{player.calculate_opponent_game_win_percentage():.4%}",
        ] for rank, player in enumerate(players)]

        data = {
            "headers": ["Rang", "Name", "Punkte", "Matches", "OMW", "GW", "OGW"],
            "rows": rows
        }
        id = await self.get_id()
        filename = f'tmp/{id.replace("/", "_")}_standings_round_{round.round_number}.png'
        table_to_image.generate_image(data, filename, "assets/beleren.ttf")

        return filename
    

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
                    rows.append([(f"{player.name} ({p_mp})", player.dropped), "BYE", "2 - 0"])
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

            # Add both player perspectives
            p1_mp = p1.calculate_match_points(round.round_number-1)
            p2_mp = p2.calculate_match_points(round.round_number-1)
            rows.append([(f"{p1.name} ({p1_mp})", p1.dropped), (f"{p2.name} ({p2_mp})", p2.dropped), score_p1])
            rows.append([(f"{p2.name} ({p2_mp})", p2.dropped), (f"{p1.name} ({p1_mp})", p1.dropped), score_p2])

        # Sort rows by player name (first element of the tuple in column 0)
        rows.sort(key=lambda row: row[0][0].lower())

        data = {
            "headers": ["Spieler", "Gegner", "Match Ergebnis (S-N-U)"],
            "rows": rows
        }

        filename = f'tmp/{id.replace("/", "_")}_pairings_round_{round.round_number}_expanded.png'
        table_to_image.generate_image(data, filename, "assets/beleren.ttf")
        return filename