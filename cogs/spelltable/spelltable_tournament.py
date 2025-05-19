from ezcord import Cog, log
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Embed, Color, Option
import discord
from modules import date_time_interpretation
from datetime import datetime
import os, json, re
from modules import swiss_mtg
from modules import env
from modules import table_to_image
from enum import StrEnum, auto
from modules.serializable import Serializable
import logging
from typing import Literal
import traceback

link_log = logging.getLogger("link_logger")

test_participants = []

IS_DEBUG = env.DEBUG
BOT = None

if IS_DEBUG:
    with open("test_participants.txt", "r", encoding="utf-8") as file:
        test_participants = [int(line.strip()) for line in file]  # Splits by spaces

EMOJI_PATTERN = re.compile("[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\U0001F600-\U0001F64F]+", flags=re.UNICODE)
TOURNAMENTS_FOLDER = "tournaments"

active_tournaments:dict[str, "SpelltableTournament"] = {}

async def load_tournaments(guild:discord.Guild) -> dict[str, "SpelltableTournament"]:
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
                    tournament = await SpelltableTournament.deserialize(raw_dict, guild)
                    tournament_id = filename[:-5].replace("_", "/")  # Convert back to original ID format
                    tournaments[tournament_id] = tournament
                except Exception as e:
                    print(f"Failed to load {filename}: {e}")

    return tournaments

async def save_tournaments():
    for tournament in active_tournaments.values():
        await save_tournament(tournament)
        
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Serializable):
            mapping = obj.serialize()
            return mapping
        return super().default(obj)

async def save_tournament(tournament: "SpelltableTournament"):
    # Ensure the directory exists
    os.makedirs(TOURNAMENTS_FOLDER, exist_ok=True)
    concluded_folder = os.path.join(TOURNAMENTS_FOLDER, "concluded")
    os.makedirs(concluded_folder, exist_ok=True)

    # Convert tournament ID (slashes to underscores) for filename
    tournament_id = await tournament.get_id()
    filename = tournament_id.replace("/", "_") + ".json"
    file_path = os.path.join(TOURNAMENTS_FOLDER, filename)

    serialized = await tournament.serialize()
    try:
        with open(file_path, "w") as file:
            json.dump(serialized, file, cls=CustomJSONEncoder, indent=4)
        
        # If the tournament is concluded, move the file to the concluded folder
        if tournament.swiss_tournament and tournament.swiss_tournament.winner:
            concluded_path = os.path.join(concluded_folder, filename)
            os.rename(file_path, concluded_path)
            print(f"Tournament {tournament_id} has been concluded and moved to {concluded_path}")
    except Exception as e:
        print(f"Error saving tournament {tournament_id}: {e}")

class ParticipationState(StrEnum):
    PARTICIPATE = auto()
    TENTATIVE = auto()
    DECLINE = auto()

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
    def __init__(self, guild:discord.Guild, title:str, organizer_id:int):
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

        if IS_DEBUG:
            for user_id in test_participants:
                self.users[user_id] = ParticipationState.PARTICIPATE

        self._organizer:discord.Member|None = None
        self._message:discord.Message|None = None

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
            thread:discord.Thread = await discord.utils.get_or_fetch(self.guild, "channel", self.channel_id)
            try:
                # thread does not have get_message method, so we need to fetch the message
                self._message = await thread.fetch_message(self.message_id)
            except Exception as e:
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
    async def deserialize(cls, data, guild:discord.Guild): #, organizer, message):
        organizer_id = int(data["organizer_id"])

        tournament = cls(guild, data["name"], organizer_id)
        tournament.description = data["description"]
        if "time" in data and data["time"]:
            tournament.time = datetime.fromisoformat(data["time"])

        tournament.max_rounds = data["max_rounds"]

        tournament.users = {int(k): v for k, v in data["users"].items()}
        tournament.message_id = int(data["message_id"])
        tournament.channel_id = int(data["channel_id"])
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
            "days_per_match": self.days_per_match
        }
    
    async def check_waitlist(self, participants):
        if self.waitlist and len(participants) < self.max_participants:
            first_in_line_user_id = self.waitlist.pop(0)
            self.users[first_in_line_user_id] = ParticipationState.PARTICIPATE
            new_participant:discord.Member = await get_member(first_in_line_user_id, self)
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

        await save_tournament(self)
        message = await self.message
        if message:
            embed = await self.to_embed()
            await message.edit(embed=embed)
        else:
            raise Exception("Message not found")
        return message_str

    async def to_embed(self):
        embed = Embed(
            title=self.title,
            description=self.description,
            color=Color.from_rgb(37, 88, 79)
        )
        organizer = await self.organizer
        if not organizer:
            raise Exception("Organizer not found")
        if organizer:
            embed.set_author(name=organizer.display_name, icon_url=organizer.avatar.url if organizer.avatar else None)
        if self.time:
            date_format_character = "D" if self.days_per_match else "F"
            embed.add_field(name="Start", value=discord.utils.format_dt(self.time, date_format_character)+"\n"+discord.utils.format_dt(self.time, 'R'), inline=True)

        participants = self.get_users_by_state(ParticipationState.PARTICIPATE)
        rec_round_count = swiss_mtg.recommended_rounds(len(participants))
        if self.max_rounds:
            current_round_count = min(self.max_rounds, rec_round_count)
            embed.add_field(name="Anzahl Runden", value=f"Abhängig von der Spielerzahl. Aber Maximal {self.max_rounds}\nAktuell: {current_round_count}", inline=True)
        else:
            embed.add_field(name="Anzahl Runden", value=f"Abhängig von der Spielerzahl.\nAktuell: {rec_round_count}", inline=True)
        
        embed.add_field(name=f"Tage pro Runde", value=f"{self.days_per_match if self.days_per_match > 0 else 'Wird am Stück gespielt'}", inline=True)

        waitlist = self.waitlist
        tentative = self.get_users_by_state(ParticipationState.TENTATIVE)

        embed.add_field(name="\u200b", value="\u200b", inline=False)

        await self.guild.chunk()

        participant_members:list[discord.User] = []
        for uid in participants:
            member = self.guild.get_member(uid)
            if not member:
                member = await BOT.get_or_fetch_user(uid)
            if member:
                participant_members.append(member)

        embed.add_field(name=f"✅ Teilnehmer ({len(participants)}{f'/{self.max_participants}' if self.max_participants else ''})", value="\n".join([f"{p.display_name}" for p in participant_members]), inline=True)
        if self.max_participants:
            waitlist_members = [self.guild.get_member(uid) for uid in waitlist]
            # Filter out None values if some IDs weren't found
            waitlist_members = [m for m in waitlist_members if m is not None]
            embed.add_field(name=f"⌚ Nachrücker ({len(waitlist)})", value="\n".join([f"{p.display_name}" for p in waitlist_members]), inline=True)
        
        tentative_members = [self.guild.get_member(uid) for uid in tentative]
        # Filter out None values if some IDs weren't found
        tentative_members = [m for m in tentative_members if m is not None]
        embed.add_field(name=f"❓ Vielleicht ({len(tentative)})", value="\n".join([f"{p.display_name}" for p in tentative_members]), inline=True)
        return embed

    async def get_message(self, message_id) -> discord.Message|None:
        if message_id:
            tourney_message = await self.message
            return await tourney_message.channel.fetch_message(message_id)
        return None

    async def update_standings(self, interaction: discord.Interaction):
        current_round = self.swiss_tournament.current_round()
        if not current_round.is_concluded():
            # as long as the current round has not concluded, don't post standings
            return
        message_standings = await self.get_message(current_round.message_id_standings)

        content = f"Platzierungen nach der {self.swiss_tournament.current_round().round_number}. Runde"
        standings_image = await standings_to_image(self)
        standings_file = discord.File(standings_image, filename=standings_image)

        if current_round.round_number >= self.swiss_tournament.rounds_count:
            # letzte Runde
            view = await FinishTournamentView.create(self)
        else:
            view = await StartNextRoundView.create(current_round, self)

        if message_standings:
            await message_standings.edit(content=content, view=view, attachments=[], file=standings_file)
        else:
            async def do_the_thing():
                message_standings = await interaction.followup.send(content=content, view=view, file=standings_file)
                current_round.message_id_standings = message_standings.id
            await use_custom_try("Platzierungen Senden", do_the_thing, self)

        await save_tournament(self)

    async def update_pairings(self, round:swiss_mtg.Round):
        pairings_message:discord.Message = await self.get_message(round.message_id_pairings)
        link_log.info(f"updating pairings {pairings_message.jump_url}")
        pairings_image = await pairings_to_image(self)
        pairings_file = discord.File(pairings_image, filename=pairings_image)
        if pairings_message:
            await pairings_message.edit(file=pairings_file, attachments=[], content="Paarungen aktualisiert." or "Kein Inhalt verfügbar.")
        else:
            raise Exception("Pairings message not found.")

        await save_tournament(self)
    
    async def next_round(self, interaction:discord.Interaction):
        async def do_the_thing():
            previous_round = self.swiss_tournament.current_round()
            round = self.swiss_tournament.pair_players()
            await interaction.followup.send(f"Berechne Paarungen für Runde {round.round_number} ...", ephemeral=True)
            reportMatchView = await ReportMatchView.create(round, self)
            pairings_image = await pairings_to_image(self)
            pairings_file = discord.File(pairings_image, filename=pairings_image)
            try:
                new_pairings_message:discord.Message = await interaction.followup.send(content=f"Paarungen für die {round.round_number}. Runde:\n\n{self.get_pairings()}", file=pairings_file, view=reportMatchView)
                await new_pairings_message.pin()

                if previous_round:
                    previous_pairings_message = await new_pairings_message.channel.fetch_message(previous_round.message_id_pairings)
                    await previous_pairings_message.unpin()
            except discord.errors.HTTPException as e:
                log.error(f"Failed to send pairings message: {e}")
                await interaction.followup.send("Fehler beim Senden der Paarungen. Bitte versuche es später erneut.", ephemeral=True)
                previous_standings_message = await self.get_message(previous_round.message_id_standings)
                standings_image = await standings_to_image(self, previous_round)
                standings_file = discord.File(standings_image, filename=standings_image)
                await previous_standings_message.edit(attachments=[], file=standings_file, view=await StartNextRoundView.create(previous_round, self))
                return

            if not isinstance(new_pairings_message, discord.Message):
                raise TypeError("Expected a discord.Message, but got None or an invalid type.")
            round.message_id_pairings = new_pairings_message.id
            await save_tournament(self)

            # message players direcly
            for match in round.matches:
                try:
                    user1 = await self.guild.fetch_member(match.player1.player_id)
                except discord.NotFound:
                    user1 = None
                if match.is_bye():
                    # bye
                    if user1:
                        try:
                            await user1.send(f"Du hast ein BYE in der {round.round_number}. Runde im Turnier `{self.title}`: {new_pairings_message.jump_url}")
                        except discord.Forbidden:
                            log.error(f"Could not send message to {user1.display_name} ({user1.id})")
                else:
                    try:
                        user2 = await self.guild.fetch_member(match.player2.player_id)
                    except discord.NotFound:
                        user2 = None
                    if user1:
                        try:
                            await user1.send(f"Du spielst gegen <@{match.player2.player_id}> in der {round.round_number}. Runde im Turnier `{self.title}`: {new_pairings_message.jump_url}")
                        except discord.Forbidden:
                            log.error(f"Could not send message to {user1.display_name} ({user1.id})")
                    if user2:
                        try:
                            await user2.send(f"Du spielst gegen <@{match.player1.player_id}> in der {round.round_number}. Runde im Turnier `{self.title}`: {new_pairings_message.jump_url}")
                        except discord.Forbidden:
                            log.error(f"Could not send message to {user2.display_name} ({user2.id})")
    
        await use_custom_try("Nächste Runde Erstellen", do_the_thing, self)
    
    def get_pairings(self, round=None) -> str:
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
                    matchups[player.name] = f"{mention} hat ein BYE"
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
            
            matchups[p1.name] = f"{mention_p1} vs {mention_p2}"
            matchups[p2.name] = f"{mention_p2} vs {mention_p1}"

        if not matchups:
            return "Keine Paarungen verfügbar. Ein Neustart des Bots, sollte den Fehler beheben. <@356120044754698252>" # pings NudelForce

        for_message = "\n".join(matchups[name] for name in sorted(matchups, key=str.lower))

        return for_message
    

async def pairings_to_image(tournament: SpelltableTournament, round=None) -> str:
    if round is None:
        round = tournament.swiss_tournament.current_round()
    id = await tournament.get_id()

    rows = []

    for match in round.matches:
        # Handle BYE
        if match.is_bye():
            player = match.player1 or match.player2
            if player:
                rows.append([(f"{player.name} ({player.calculate_match_points()-3})", player.dropped), "BYE", "2 - 0"])
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
        rows.append([(p1.name, p1.dropped), (p2.name, p2.dropped), score_p1])
        rows.append([(p2.name, p2.dropped), (p1.name, p1.dropped), score_p2])

    # Sort rows by player name (first element of the tuple in column 0)
    rows.sort(key=lambda row: row[0][0].lower())

    data = {
        "headers": ["Spieler", "Gegner", "Match Ergebnis (S-N-U)"],
        "rows": rows
    }

    filename = f'tmp/{id.replace("/", "_")}_pairings_round_{round.round_number}_expanded.png'
    table_to_image.generate_image(data, filename, "beleren.ttf")
    return filename


async def standings_to_image(tournament:SpelltableTournament, round=None) -> str:
    if round is None:
        round = tournament.swiss_tournament.current_round()
    players = tournament.swiss_tournament.players
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
    id = await tournament.get_id()
    filename = f'tmp/{id.replace("/", "_")}_standings_round_{round.round_number}.png'
    table_to_image.generate_image(data, filename, "beleren.ttf")

    return filename

class StartNextRoundView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await StartNextRoundView.create(...)' instead")

    async def _init(self, round:swiss_mtg.Round, tournament:SpelltableTournament):
        super().__init__(timeout=None)
        self.tournament = tournament
        self.previous_round:swiss_mtg.Round = round

    @classmethod
    async def create(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(round, tournament)
        return instance

    @classmethod
    async def join_button_id(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        return f"start_next_round_{await tournament.get_id()}_{round.round_number}"
    
    @discord.ui.button(label="Nächste Runde", style=discord.ButtonStyle.success, emoji="➡️")
    async def next_round_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        async def do_the_thing():
            if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
                await interaction.respond("Nur der Turn Organisator darf dies tun.", ephemeral=True)
                return
            await interaction.response.defer()

            previous_standings_message = await self.tournament.get_message(self.previous_round.message_id_standings)
            previous_pairings_message = await self.tournament.get_message(self.previous_round.message_id_pairings)

            standings_image = await standings_to_image(self.tournament, self.previous_round)
            standings_file = discord.File(standings_image, filename=standings_image)
            await previous_standings_message.edit(attachments=[], file=standings_file, view=None) #content=previous_pairings_message.content, view=None, attachments=previous_pairings_message.attachments)

            pairings_image = await pairings_to_image(self.tournament, self.previous_round)
            pairings_file = discord.File(pairings_image, filename=pairings_image)
            await previous_pairings_message.edit(attachments=[], file=pairings_file, view=None)

            try:
                await self.tournament.next_round(interaction)
            except Exception as e:
                await previous_standings_message.edit(attachments=[], file=standings_file, view=await StartNextRoundView.create(self.previous_round, self.tournament))

        await use_custom_try("Nächste Runde Erstellen", do_the_thing, self.tournament)

class FinishTournamentView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await FinishTournamentView.create(...)' instead")

    async def _init(self, tournament: SpelltableTournament):
        super().__init__(timeout=None)
        self.tournament = tournament

    @classmethod
    async def create(cls, tournament: SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(tournament)
        return instance

    @discord.ui.button(label="Turnier abschließen", style=discord.ButtonStyle.success, emoji="🏆")
    async def finish_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id:
            await interaction.respond("Nur der Turnier-Organisator darf dies tun.", ephemeral=True)
            return

        swiss_mtg.sort_players_by_standings(self.tournament.swiss_tournament.players)
        winner = None
        for player in self.tournament.swiss_tournament.players:
            if not player.dropped:
                winner = player
                break

        if winner:
            content = f"🎉 Das Turnier ist abgeschlossen! 🎉\nHerzlichen Glückwunsch an <@{winner.player_id}> für den großartigen Sieg! 🏆\nVielen Dank an alle Teilnehmer für ein spannendes und unterhaltsames Turnier! 🎊"

            # Remove views from the last round pairings and standings messages
            current_round = self.tournament.swiss_tournament.current_round()
            pairings_message = await self.tournament.get_message(current_round.message_id_pairings)
            standings_message = await self.tournament.get_message(current_round.message_id_standings)

            if pairings_message:
                await pairings_message.edit(view=None)
            if standings_message:
                await standings_message.edit(view=None)

            await interaction.response.send_message(content, ephemeral=False)
            link_log.info(f"Turnier abgeschlossen: `{self.tournament.title}`, Gewinner: <@{winner.player_id}>")
            self.tournament.swiss_tournament.winner = winner
            await save_tournament(self.tournament)
        else:
            await interaction.respond("Kein Gewinner gefunden. Das sollte nicht passieren.", ephemeral=True)


members:dict[int, discord.Member] = {}

async def get_member(user_id, tournament:SpelltableTournament) -> discord.Member:
    if user_id in members:
        return members[user_id]
    try:
        user = await discord.utils.get_or_fetch(tournament.guild, "member", user_id)
        members[user_id] = user
        return user
    except:
        if IS_DEBUG:
            try:
                user = await discord.utils.get_or_fetch(BOT, "user", user_id)
                return user
            except discord.errors.NotFound: 
                return None
        raise Exception(f"User with ID {user_id} not found in guild {tournament.guild.id}.")

class ReportMatchModal(discord.ui.Modal):
    def __init__(self):
        raise RuntimeError("Use 'await ReportMatchModal.create(...)' instead")

    async def _init(self, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match):
        super().__init__(title="Report Match Result")
        self.tournament = tournament
        self.match = match
        self.round = round

        self.player1_user = await get_member(match.player1.player_id, tournament)
        self.player2_user = await get_member(match.player2.player_id, tournament)
        self.p1_score = discord.ui.InputText(
            label=f"Player 1 - {self.player1_user.display_name}",
            placeholder="",
            required=True,
            value=match.wins[match.player1] or "0" if match.is_finished() else ""
        )
        self.add_item(self.p1_score)
        self.p2_score = discord.ui.InputText(
            label=f"Player 2 - {self.player2_user.display_name}",
            placeholder="",
            required=True,
            value=match.wins[match.player2] or "0" if match.is_finished() else ""
        )
        self.add_item(self.p2_score)
        self.draw_score = discord.ui.InputText(
            label=f"Draws",
            placeholder="",
            required=False,
            value=match.wins["draws"]
        )
        self.add_item(self.draw_score)

    @classmethod
    async def create(cls, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match):
        instance = object.__new__(cls)
        await instance._init(tournament, round, match)
        return instance

    async def callback(self, interaction: discord.Interaction):
        try:
            self.match.set_result(int(self.p1_score.value), int(self.p2_score.value), int(self.draw_score.value) if self.draw_score.value else 0)
        except ValueError as e:
            await interaction.respond(f"Dein Match Resultat {int(self.p1_score.value)}-{int(self.p2_score.value)}-{int(self.draw_score.value) if self.draw_score.value else 0} ist invalide: {e}", ephemeral=True)
            return

        msg_text = f"Match result submitted: {self.player1_user.mention} vs {self.player2_user.mention} → {self.p1_score.value}-{self.p2_score.value}-{self.draw_score.value if self.draw_score.value else 0}"
        await interaction.response.send_message(
            msg_text,
            ephemeral=True
        )

        if IS_DEBUG:
            swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

        link_log.info(f"{msg_text} in {interaction.message.jump_url}")
        await self.tournament.update_pairings(self.round)
        await self.tournament.update_standings(interaction)
        # TODO Enable "Runde beenden" Button (only usable by TO/Manager)
        # after that button is clicked, calculate Standings and disable Report Match Result Button

class ConfirmKickView(discord.ui.View):
    def __init__(self, tournament:SpelltableTournament, user_to_kick:discord.Member):
        super().__init__(timeout=None)
        self.tournament = tournament
        self.user_to_kick = user_to_kick

    @discord.ui.button(label="Rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷")
    async def kick_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.tournament.swiss_tournament:
            # tournament ongoing
            player_to_drop = self.tournament.swiss_tournament.player_by_id(self.user_to_kick.id)
            if player_to_drop:
                player_to_drop.dropped = True
                await self.tournament.update_standings(interaction)
                await self.tournament.update_pairings(self.tournament.swiss_tournament.current_round())
            else:
                log.error(f"Player with id {self.user_to_kick.id} in Swiss Tournament not found")
        else:
            # tournament registration is still going on
            await self.tournament.user_state(self.user_to_kick.id, ParticipationState.DECLINE)
        orig_response = await interaction.original_response()
        if self.tournament.swiss_tournament:
            message_id_pairings = self.tournament.swiss_tournament.current_round().message_id_pairings
            pairings_message:discord.Message = await self.tournament.get_message(message_id_pairings)
            link_log.info(f"User {self.user_to_kick.mention} was kicked from tournament {pairings_message.jump_url}")
        else:
            tourney_message = await self.tournament.message
            link_log.info(f"User {self.user_to_kick.mention} was kicked from tournament {tourney_message.jump_url}")
        await orig_response.edit(content=f"{self.user_to_kick.mention} wurde aus dem Turnier entfernt", view=None)

class KickPlayerModal(discord.ui.Modal):
    def __init__(self, tournament:SpelltableTournament):
        super().__init__(title="Spieler rauswerfen")
        self.tournament = tournament

        self.kick_input = discord.ui.InputText(
            label="Name oder User ID",
            placeholder="z.B. NudelForce oder 356120044754698252",
            required=True
        )
        self.add_item(self.kick_input)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = None
        user_name = None
        user_to_kick = None
        try:
            user_id:int = int(self.kick_input.value)
        except:
            user_name:str = self.kick_input.value
        if self.tournament.swiss_tournament:
            participant_ids = [player.player_id for player in self.tournament.swiss_tournament.get_active_players()]
        else:
            participant_ids = self.tournament.get_users_by_state(ParticipationState.PARTICIPATE)
        if user_id:
            if user_id in participant_ids:
                user_to_kick:discord.Member = await get_member(user_id, self.tournament)
            else:
                await interaction.respond(f"Kein User mit der ID `{user_id}` ist für das Turnier angemeldet.", ephemeral=True)
                return
        elif user_name:
            for participant_id in participant_ids:
                member = await get_member(participant_id, self.tournament)
                if user_name.lower() in member.display_name.lower():
                    user_to_kick = member
                    break
        else:
            await interaction.respond(f"Unerwarteter Fehler.", ephemeral=True)
        if user_to_kick:
            # lasse bestätigen
            await interaction.respond(f"Diesen Spieler rauswerfen? {user_to_kick.mention}", view=ConfirmKickView(self.tournament, user_to_kick), ephemeral=True)
        else:
            if user_id:
                await interaction.respond(f"User mit der ID `{user_id}` nicht gefunden.", ephemeral=True)
            elif user_name:
                await interaction.respond(f"Teilnehmer mit dem Namen `{user_name}` nicht gefunden.", ephemeral=True)
            else:
                await interaction.respond(f"Unerwarteter Fehler.", ephemeral=True)

class ConfirmDropModal(discord.ui.Modal):
    def __init__(self, player:swiss_mtg.Player, tournament:SpelltableTournament):
        super().__init__(title="Bestätige deine Turnierausscheidung")
        self.player = player
        self.tournament = tournament

        self.drop_input = discord.ui.InputText(
            label=f"Tippe DROP",
            placeholder="DROP",
            required=True,
        )
        self.add_item(self.drop_input)

    async def callback(self, interaction: discord.Interaction):
        if self.drop_input.value != "DROP":
            await interaction.respond("Turnier ausscheidung fehlgeschlagen", ephemeral=True)
            return
        self.player.dropped = True
        
        # update matchups, i.e. strike through dropped player
        current_round = self.tournament.swiss_tournament.current_round()
        await self.tournament.update_pairings(current_round)

        # update standings, i.e. strike through dropped player
        await self.tournament.update_standings(interaction)

        message_pairings = await self.tournament.get_message(current_round.message_id_pairings)

        link_log.info(f"User {interaction.user.mention} dropped from tournament {message_pairings.jump_url}")
        await interaction.response.send_message("Du wurdest aus dem Turnier entfernt", ephemeral=True)

async def simulate_on_not_playing(tournament:SpelltableTournament, round:swiss_mtg.Round, interaction:discord.Interaction):
    if IS_DEBUG:
        swiss_mtg.simulate_remaining_matches(tournament.swiss_tournament)

        await tournament.update_pairings(round)
        await tournament.update_standings(interaction)

class ReportMatchView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await ReportMatchView.create(...)' instead")
    
    async def _init(self, round:swiss_mtg.Round, tournament:SpelltableTournament):
        super().__init__(timeout=None)
        self.round = round
        self.tournament = tournament

    @classmethod
    async def create(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(round, tournament)
        return instance

    @discord.ui.button(label="Report Match Result", style=discord.ButtonStyle.primary)
    async def report_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        the_match = None
        for match in self.round.matches:
            players_in_match = []
            if match.player1:
                players_in_match.append(match.player1.player_id)
            if match.player2:
                players_in_match.append(match.player2.player_id)

            if interaction.user.id in players_in_match:
                the_match = match
                break
        if not the_match:
            await interaction.respond("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
            
            if IS_DEBUG:
                await simulate_on_not_playing(self.tournament, self.round, interaction)
            return
        
        if the_match.is_bye():
            await interaction.respond("Du hast diese Runde ein Bye", ephemeral=True)

            if IS_DEBUG:
                await simulate_on_not_playing(self.tournament, self.round, interaction)
            return
        
        await interaction.response.send_modal(await ReportMatchModal.create(self.tournament, self.round, the_match))

    @discord.ui.button(label="DROP", style=discord.ButtonStyle.danger)
    async def drop_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        player = self.tournament.swiss_tournament.player_by_id(interaction.user.id)
        if not player:
            await interaction.respond("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
            return
        if not player in self.tournament.swiss_tournament.get_active_players():
            await interaction.respond("Du bist bereits aus diesem Turnier ausgetreten", ephemeral=True)
            return
        
        await interaction.response.send_modal(ConfirmDropModal(player, self.tournament))
    
    @discord.ui.button(label="Spieler rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷")
    async def kick_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        await interaction.response.send_modal(KickPlayerModal(self.tournament))

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

    @discord.ui.button(label="Teilnehmen", style=discord.ButtonStyle.success, emoji="✅")
    async def join_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await self.tournament.user_state(interaction.user.id, ParticipationState.PARTICIPATE)
        if message:
            await interaction.respond(message, ephemeral=True)

    # no use - either participate or tentative
    # @discord.ui.button(label="Warteliste", style=discord.ButtonStyle.primary, emoji="⌚")
    # async def waitlist_button(self, button: discord.ui.Button, interaction: discord.Interaction):
    #     await interaction.response.defer(ephemeral=True)
    #     await self.tournament.user_state(interaction.user.id, ParticipationState.WAITLIST)

    @discord.ui.button(label="Vielleicht", style=discord.ButtonStyle.primary, emoji="❓")
    async def tentative_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.TENTATIVE)

    @discord.ui.button(label="Absagen", style=discord.ButtonStyle.danger, emoji="❌")
    async def leave_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.DECLINE)

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        
        message = await self.tournament.message
        if message:
            if type(message.channel) == discord.TextChannel:
                raise Exception("EditTournamentView only works in threads")
            view = await EditTournamentView.create(self.tournament, message.channel)
            await interaction.respond(view=view, ephemeral=True)
        else:
            await interaction.respond("Turnier Nachricht nicht gefunden", ephemeral=True)

    @discord.ui.button(label="Spieler rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷")
    async def kick_button(self, button:discord.ui.Button, interaction:discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        await interaction.response.send_modal(KickPlayerModal(self.tournament))

    @discord.ui.button(label="Starte Turnier", style=discord.ButtonStyle.primary, emoji="▶️")
    async def start_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if type(interaction.user) != discord.Member:
            await interaction.respond("Du bist kein Mitglied auf diesem Server!", ephemeral=True)
            return
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht der Turnier-Organisator!", ephemeral=True)
            return
        await interaction.respond("Starte Turnier...", ephemeral=True)
        
        message = await self.tournament.message
        if message:
            await message.edit(view=None)
        else:
            await interaction.respond("Turnier Nachricht nicht gefunden", ephemeral=True)
            return
        
        players = []
        participants = self.tournament.get_users_by_state(ParticipationState.PARTICIPATE)
        for participant_id in participants:
            member = await get_member(participant_id, self.tournament)
            player_name = EMOJI_PATTERN.sub("", member.display_name).strip()
            players.append(swiss_mtg.Player(player_name, participant_id))

        swiss_tournament = swiss_mtg.SwissTournament(players, max_rounds=self.tournament.max_rounds if self.tournament.max_rounds else None)
        self.tournament.swiss_tournament = swiss_tournament
        await self.tournament.next_round(interaction)

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
        new_value = self.input.value
        try:
            if self.parse:
                new_value = self.parse(self.input.value)
        except ValueError:
            await interaction.respond(f"Konnte die Eingabe `{self.input.value}` nicht auswerten.", ephemeral=True)
            return

        setattr(self.tournament, self.key, new_value)
        
        await interaction.response.edit_message(embed=await self.tournament.to_embed(), view=self.view)

class EditTournamentView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await EditTournamentView.create(...)' instead")

    async def _init(self, tournament:SpelltableTournament, channel:discord.TextChannel):
        super().__init__()
        self.tournament = tournament
        self.channel = channel

        self.days_per_match_callback.label = "Turnier am Stück spielen" if self.tournament.days_per_match else "Eine Woche pro Runde"
    
    @classmethod
    async def create(cls, tournament:SpelltableTournament, channel:discord.TextChannel):
        instance = object.__new__(cls)
        await instance._init(tournament, channel)
        return instance

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
            placeholder="Die/Das Startzeit/-datum für das Turnier",
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

        # input = discord.ui.InputText(
        #     label="Tage pro Runde",
        #     placeholder="Wie viele Tage man für seine Runde Zeit hat. 0 für Turnier wird am Stück ausgetragen",
        #     required=True,
        #     value=self.tournament.days_per_match if self.tournament.days_per_match else "",
        #     style=discord.InputTextStyle.long,
        # )

        # await interaction.response.send_modal(EnterTextModal(input, "days_per_match", self.tournament, self))

    @discord.ui.button(label="Abschicken", style=discord.ButtonStyle.success, emoji="✅")
    async def submit_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        await interaction.response.defer()
        self.disable_all_items()
        await interaction.edit(view=self)
        #  save into database
        participationView = await ParticipationView.create(self.tournament)
        
        existing_message = await self.tournament.message
        if existing_message:
            # edit
            await save_tournament(self.tournament)
            await existing_message.edit(embed=await self.tournament.to_embed(), view=participationView)
            await interaction.edit_original_response(content="Turnier wurde bearbeitet", embed=None, view=None)
        else:
            if not type(interaction.channel) == discord.TextChannel:
                await interaction.respond("Threads können nur in Textkanälen erstellt werden.", ephemeral=True)
                return
            # create
            thread = await interaction.channel.create_thread(name=self.tournament.title, type=discord.ChannelType.public_thread)
            organizer = await self.tournament.organizer
            await interaction.channel.send(f"{organizer.mention} hat ein Turnier erstellt. Macht doch mit! :) {thread.mention}")
            message = await thread.send(embed=await self.tournament.to_embed(), view=participationView)
            self.tournament.message = message
            await message.edit(view=participationView)            
            active_tournaments[await self.tournament.get_id()] = self.tournament
            await save_tournament(self.tournament)
            await interaction.followup.send(f"Prima! Das Turnier `{self.tournament.title}` wurde erstellt: {message.jump_url}", ephemeral=True)
            link_log.info(f"Ein Turnier wurde erstellt: {message.jump_url}")

class SpelltableTournamentManager(Cog):
    def __init__(self, bot:Bot):
        global BOT
        BOT = bot

    @Cog.listener()
    async def on_ready(self):
        if not BOT:
            raise Exception("BOT is None")
        
        guild:discord.Guild = BOT.get_guild(env.GUILD_ID)
        loaded_tournaments = await load_tournaments(guild)

        global active_tournaments
        for message_path, tournament in loaded_tournaments.items():
            try:
                active_tournaments[message_path] = tournament
                tournament_message = await tournament.message

                if tournament.swiss_tournament:
                    # tournament has been started started
                    current_round = tournament.swiss_tournament.current_round()
                    if current_round.message_id_standings:
                        # standings have been posted
                        message = await tournament.get_message(current_round.message_id_standings)
                        if current_round.round_number < tournament.swiss_tournament.rounds_count:
                            view = await StartNextRoundView.create(current_round, tournament)
                        else:
                            view = None
                            
                        if message.channel.archived:
                            await message.channel.edit(archived=False)
                        await message.edit(view=view)
                    if current_round.message_id_pairings:
                        # pairings have been posted
                        view = await ReportMatchView.create(current_round, tournament)
                        message = await tournament.get_message(current_round.message_id_pairings)
                        
                        if message.channel.archived:
                            await message.channel.edit(archived=False)
                        await message.edit(view=view, content=f"Paarungen für die {current_round.round_number}. Runde:\n\n{tournament.get_pairings()}")
                else:
                    view = await ParticipationView.create(tournament)
                    await tournament_message.edit(view=view)
                

                link_log.info(f"Turnier wurde geladen: {tournament_message.jump_url} <@{tournament.organizer_id}>")
            except discord.errors.NotFound:
                file_path = TOURNAMENTS_FOLDER+"/"+(message_path.replace("/", "_"))+".json"
                log.warning(f"Turnier konnte nicht geladen werden, weil vermutlich der entprechende Channel gelöscht wurde. Lösche Datei {file_path}")
                os.remove(file_path)

        log.debug(self.__class__.__name__ + " is ready")

    # @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(
        self,
        ctx:ApplicationContext,
        titel:Option(str, description="Der Titel, den das Turnier tragen soll")
    ):
        if type(ctx.channel) != discord.TextChannel:
            await ctx.respond("Dieser Befehl kann nur in einem Textkanal ausgeführt werden.", ephemeral=True)
            return
        if type(ctx.author) != discord.Member:
            await ctx.respond("Du bist kein Mitglied dieses Servers!", ephemeral=True)
            return
        if ctx.guild is None:
            await ctx.respond("Dieser Befehl kann nur in einem Server verwendet werden.", ephemeral=True)
            return
        
        # Defer the interaction response to avoid timeout
        await ctx.defer(ephemeral=True)

        tournament = SpelltableTournament(ctx.guild, titel, ctx.author.id)
        tournament.organizer = ctx.author
        view = await EditTournamentView.create(tournament, ctx.channel)
        await ctx.followup.send(
            embed=await tournament.to_embed(),
            view=view,
            ephemeral=True
        )

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
