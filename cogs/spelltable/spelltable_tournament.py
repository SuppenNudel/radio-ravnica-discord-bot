from ezcord import Cog, log
from discord.ext.commands import slash_command, has_role
from discord import ApplicationContext, Bot, Embed, Color, Option
import discord
from modules import date_time_interpretation
from discord.utils import format_dt
from datetime import datetime
import os, json, re
from modules import swiss_mtg
from modules import env
from modules import table_to_image
from enum import StrEnum, auto
from modules.serializable import Serializable
import logging

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

    # Convert tournament ID (slashes to underscores) for filename
    tournament_id = await tournament.get_id()
    filename = tournament_id.replace("/", "_") + ".json"
    file_path = os.path.join(TOURNAMENTS_FOLDER, filename)

    serialized = await tournament.serialize()
    try:
        with open(file_path, "w") as file:
            json.dump(serialized, file, cls=CustomJSONEncoder, indent=4)
    except Exception as e:
        print(f"Error saving tournament {tournament_id}: {e}")

class ParticipationState(StrEnum):
    PARTICIPATE = auto()
    TENTATIVE = auto()
    DECLINE = auto()

class SpelltableTournament(Serializable):
    def __init__(self, guild:discord.Guild, title:str, organizer_id:int, max_participants:int=None):
        self.title = title
        self.description = None
        self.time:datetime = None
        self.organizer_id = organizer_id
        self.users:dict[int,ParticipationState] = {} 
        self.message_id:int = None
        self.channel_id:int = None
        self.swiss_tournament:swiss_mtg.SwissTournament = None
        self.max_participants = max_participants
        self.waitlist:list[int] = []
        self.guild = guild

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
            "tournament": self.swiss_tournament,
            "max_participants": self.max_participants
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
            message_str = f"Das Tunier ist bereits voll. Du wurdest auf die Liste der Nachrücker gesetzt.\nWird ein Platz frei, wirst du automatisch nachgerückt und benachrichtigt."
        else:
            if state != ParticipationState.DECLINE:
                self.users[userid] = state

        await self.check_waitlist(participants)

        await save_tournament(self)
        message = await self.message
        if message:
            await message.edit(embed=await self.to_embed())
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
            embed.add_field(name="Start", value=discord.utils.format_dt(self.time, "F")+"\n"+discord.utils.format_dt(self.time, 'R'), inline=False)

        participants = self.get_users_by_state(ParticipationState.PARTICIPATE)
        waitlist = self.waitlist
        tentative = self.get_users_by_state(ParticipationState.TENTATIVE)
        # declined = self.get_users_by_state(ParticipationState.DECLINE)

        embed.add_field(name=f"✅ Teilnehmer ({len(participants)}{f'/{self.max_participants}' if self.max_participants else ''})", value="\n".join([f"<@{p}>" for p in participants]), inline=True)
        embed.add_field(name=f"⌚ Nachrücker ({len(waitlist)})", value="\n".join([f"<@{p}>" for p in waitlist]), inline=True)
        # embed.add_field(name="\u200B", value="\u200B", inline=False)
        embed.add_field(name=f"❓ Vielleicht ({len(tentative)})", value="\n".join([f"<@{p}>" for p in tentative]), inline=True)
        # embed.add_field(name=f"❌ Abgelehnt ({len(declined)})", value="\n".join([f"<@{p}>" for p in declined]), inline=True)
        return embed

    async def update_standings(self, interaction:discord.Interaction):
        current_round = self.swiss_tournament.current_round()
        if not current_round.is_concluded():
            # as long as the current round has not concluded, don't post standings
            return
        message_standings = await get_round_message(current_round, self, standings_messages)
        if current_round.round_number >= self.swiss_tournament.max_rounds:
            swiss_mtg.sort_players_by_standings(self.swiss_tournament.players)
            winner = None
            for player in self.swiss_tournament.players:
                if not player.dropped:
                    winner = player
                    break
            content = f"Finales Ergebnis!\nHerzlichen Glückwunsch <@{winner.player_id}> für den Sieg"

            image = await image_from_standings(self)
            file = discord.File(image, filename=image)
            if message_standings:
                await message_standings.edit(file=file, attachments=[], content=content)
            else:
                try:
                    message_standings = await interaction.followup.send(file=file, content=content)
                    current_round.message_id_standings = message_standings.id
                except Exception as e:
                    log.error(e, f"Tried sending followup with content={content}, file={file}")
        else:
            start_next_round_view = await StartNextRoundView.create(current_round, self)
            content = f"Platzierungen nach der {self.swiss_tournament.current_round().round_number}. Runde"
            image = await image_from_standings(self)
            file = discord.File(image, filename=image)
            if message_standings:
                await message_standings.edit(content=content, view=start_next_round_view, attachments=[], file=file)
            else:
                try:
                    message_standings = await interaction.followup.send(content=content, view=start_next_round_view, file=file)
                    current_round.message_id_standings = message_standings.id
                except Exception as e:
                    log.error(e, f"Tried sending followup with content={content}, view={start_next_round_view}, file={file}")


        await save_tournament(self)

    async def update_pairings(self, round):
        new_embed = format_pairings(round)
        pairings_message = await get_round_message(round, self, pairings_messages)
        await pairings_message.edit(embed=new_embed)

        await save_tournament(self)
    
    async def next_round(self, interaction:discord.Interaction):
        round = self.swiss_tournament.pair_players()
        await interaction.followup.send(f"Berechne Paarungen für Runde {round.round_number} ...", ephemeral=True)
        reportMatchView = await ReportMatchView.create(round, self)
        new_pairings_message:discord.message.Message = await interaction.followup.send(embed=format_pairings(round), view=reportMatchView)
        pairings_messages[round] = new_pairings_message
        round.message_id_pairings = new_pairings_message.id
        await save_tournament(self)

def format_standings_by_tournament(round_no:int, players:list[swiss_mtg.Player]) -> Embed:
    swiss_mtg.sort_players_by_standings(players)
    embed = Embed(
        title=f"Platzierungen nach der {round_no}. Runde",
        # description=f"Die Spieler wurden zufällig gepaart. Viel Erfolg!\n\n{'\n'.join(bye_pairings)}",
        color=Color.from_rgb(37, 88, 79),
        fields=[
            discord.EmbedField(
                name="Platz",
                value="\n".join([f"{rank}" for rank in range(1, len(players)+1)]),
                inline=True
            ),
            discord.EmbedField(
                name="Spieler",
                value="\n".join([f"<@{player.player_id}>" for player in players]),
                inline=True
            ),
            discord.EmbedField(
                name="Match Punkte",
                value="\n".join([f"{player.calculate_match_points()}" for player in players]),
                inline=True
            ),
        ]
    )

    return embed

def format_standings(tournament:swiss_mtg.SwissTournament) -> str:
    round = tournament.current_round()
    final_round = round.round_number == tournament.max_rounds
    players = list({player for match in round.matches for player in (match.player1, match.player2) if player is not None})
    swiss_mtg.sort_players_by_standings(players)

    max_length = max(len(player.name) for player in players) + 2

    header = f"{'Rang':<5}{'Name':<{max_length}}{'Punkte':<8}{'Matches':<10}"
    if final_round:
        header += f"{'OMW':<12}{'GW':<12}{'OGW':<12}"
    txt = ""
    for rank, player in enumerate(players):
        txt += f"\n{rank+1:<5}{player.name:<{max_length}}{player.calculate_match_points():<8}{player.get_match_results():<10}"
        if final_round:
            txt += f"{player.calculate_opponent_match_win_percentage():<12.4%}{player.calculate_game_win_percentage():<12.4%}{player.calculate_opponent_game_win_percentage():<12.4%}"

    return f"Platzierungen nach der {round.round_number}. Runde\n```{header}{txt}```"

async def image_from_standings(tournament:SpelltableTournament) -> str:
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
    filename = f'tournaments/{id.replace("/", "_")}_standings_round_{round.round_number}.png'
    table_to_image.generate_image(data, filename)

    return filename

pairings_messages:dict[swiss_mtg.Round, discord.Message] = {}
standings_messages:dict[swiss_mtg.Round, discord.Message] = {}

async def get_round_message(round:swiss_mtg.Round, tournament:SpelltableTournament, message_map:dict[swiss_mtg.Round, discord.Message]) -> discord.Message:
    message = None
    if round in message_map:
        message = message_map[round]
    else:
        tourney_message = await tournament.message
        if not tourney_message:
            raise Exception("Tournament has no thread. Shouldn't happen since the SwissTournament is already started.")
        message_id = None
        if message_map == pairings_messages:
            message_id = round.message_id_pairings
        elif message_map == standings_messages:
            message_id = round.message_id_standings
        else:
            raise Exception("Invalid message map")
        try:
            # thread does not have get_message method, so we need to fetch the message
            message = await tourney_message.channel.fetch_message(message_id)
            message_map[round] = message
        except Exception as e:
            return None # does not exist (yet)
            # raise Exception(f"Message with ID {message_id} not found in channel {tourney_message.channel.id}.")
    return message

class StartNextRoundView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await StartNextRoundView.create(...)' instead")

    async def _init(self, round:swiss_mtg.Round, tournament:SpelltableTournament):
        super().__init__(timeout=None)
        self.tournament = tournament
        self.previous_round:swiss_mtg.Round = round

        self.next_round_button.custom_id = await StartNextRoundView.join_button_id(round, tournament)

    @classmethod
    async def create(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(round, tournament)
        return instance

    @classmethod
    async def join_button_id(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        return f"start_next_round_{await tournament.get_id()}_{round.round_number}"
    
    @discord.ui.button(label="Nächte Runde", style=discord.ButtonStyle.success, emoji="➡️", custom_id="start_next_round_placeholder")
    async def next_round_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id:
            await interaction.respond("Nur der Turn Organisator darf dies tun.")
            return
        await interaction.response.defer()
        previous_pairings_message = await get_round_message(self.previous_round, self.tournament, pairings_messages)
        previous_standings_message = await get_round_message(self.previous_round, self.tournament, standings_messages)
        await previous_pairings_message.edit(view=None)
        await previous_standings_message.edit(view=None)

        await self.tournament.next_round(interaction)

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

        await interaction.response.send_message(
            f"Match result submitted: **{self.player1_user.mention}** vs **{self.player2_user.mention}** → {self.p1_score.value}-{self.p2_score.value}-{self.draw_score.value if self.draw_score.value else 0}",
            ephemeral=True
        )

        if IS_DEBUG:
            swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

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
        self.player.dropped = True
        
        # update matchups, i.e. strike through dropped player
        current_round = self.tournament.swiss_tournament.current_round()
        await self.tournament.update_pairings(current_round)

        # update standings, i.e. strike through dropped player
        await self.tournament.update_standings(interaction)

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

        tournament_id = await tournament.get_id()
        report_button_id = f"report_{tournament_id}_{round.round_number}"
        drop_id = f"drop_{tournament_id}_{round.round_number}"
        self.report_button.custom_id = report_button_id
        self.drop_button.custom_id = drop_id
    
    @classmethod
    async def create(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(round, tournament)
        return instance

    @discord.ui.button(label="Report Match Result", style=discord.ButtonStyle.primary, custom_id="report_match_placeholder")
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
            await interaction.response.send_message("Du bist nicht Teilnehmer in diesem Turnier", ephemeral=True)
            
            if IS_DEBUG:
                await simulate_on_not_playing(self.tournament, self.round, interaction)
            return
        
        if the_match.is_bye():
            await interaction.response.send_message("Du hast diese Runde ein Bye", ephemeral=True)

            if IS_DEBUG:
                await simulate_on_not_playing(self.tournament, self.round, interaction)
            return
        
        await interaction.response.send_modal(await ReportMatchModal.create(self.tournament, self.round, the_match))

    @discord.ui.button(label="DROP", style=discord.ButtonStyle.danger, custom_id="drop_placeholder")
    async def drop_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        player = self.tournament.swiss_tournament.player_by_id(interaction.user.id)
        if not player:
            await interaction.response.send_message("Du bist nicht Teilnehmer in diesem Turnier", ephemeral=True)
            return
        if not player in self.tournament.swiss_tournament.get_active_players():
            await interaction.respond("Du bist bereits aus diesem Turnier ausgetreten", ephemeral=True)
            return
        
        await interaction.response.send_modal(ConfirmDropModal(player, self.tournament))
    
    @discord.ui.button(label="Spieler rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷", custom_id="kick_placeholder")
    async def kick_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id:
            await interaction.respond("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return
        await interaction.response.send_modal(KickPlayerModal(self.tournament))


def format_pairings(round:swiss_mtg.Round) -> Embed:
    # bye_pairings = [f"<@{match.player1.player_id}> bekommt das Bye" for match in round.matches if match.is_bye()]
    
    results = []
    for match in round.matches:
        if match.is_finished():
            if match.is_bye():
                win, loss, draw = 2, 0, 0
            else:
                win, loss, draw = match.wins.values()
            results.append(f"{win} - {loss} - {draw}")
        else:
            results.append("Ausstehend")
    embed = Embed(
        title=f"Paarungen für die {round.round_number}. Runde",
        # description=f"Die Spieler wurden zufällig gepaart. Viel Erfolg!\n\n{'\n'.join(bye_pairings)}",
        color=Color.from_rgb(37, 88, 79),
        fields=[
            discord.EmbedField(
                name="Spieler 1",
                value="\n".join([
                    f"{'~~' if match.player1 and match.player1.dropped else ''}<@{match.player1.player_id}>{'~~' if match.player1 and match.player1.dropped else ''}"
                    for match in round.matches
                ]),
                inline=True
            ),
            discord.EmbedField(
                name="Spieler 2",
                value="\n".join([
                    f"{'~~' if match.player2 and match.player2.dropped else ''}<@{match.player2.player_id}>{'~~' if match.player2 and match.player2.dropped else ''}" 
                    if match.player2 else "BYE" 
                    for match in round.matches
                ]),
                inline=True
            ),
            discord.EmbedField(
                name="Match Ergebnis (S-N-U)",
                value="\n".join(results),
                inline=True
            ),
        ]
    )
    return embed

class ParticipationView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await ParticipationView.create(...)' instead")

    async def _init(self, tournament:SpelltableTournament):
        super().__init__(timeout=None)  # Ensure persistence
        self.tournament = tournament
        message = await tournament.message
        if message:
            # Placeholder message ID (it gets updated after sending the message)
            await self.update_button_ids(message.id)
    
    @classmethod
    async def create(cls, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(tournament)
        return instance

    @discord.ui.button(label="Teilnehmen", style=discord.ButtonStyle.success, emoji="✅", custom_id="join_placeholder")
    async def join_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await self.tournament.user_state(interaction.user.id, ParticipationState.PARTICIPATE)
        if message:
            await interaction.respond(message, ephemeral=True)

    # no use - either participate or tentative
    # @discord.ui.button(label="Warteliste", style=discord.ButtonStyle.primary, emoji="⌚", custom_id="waitlist_placeholder")
    # async def waitlist_button(self, button: discord.ui.Button, interaction: discord.Interaction):
    #     await interaction.response.defer(ephemeral=True)
    #     await self.tournament.user_state(interaction.user.id, ParticipationState.WAITLIST)

    @discord.ui.button(label="Vielleicht", style=discord.ButtonStyle.primary, emoji="❓", custom_id="tentative_placeholder")
    async def tentative_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.TENTATIVE)

    @discord.ui.button(label="Absagen", style=discord.ButtonStyle.danger, emoji="❌", custom_id="leave_placeholder")
    async def leave_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.DECLINE)

    @discord.ui.button(label="Bearbeiten (noch nicht möglich)", style=discord.ButtonStyle.primary, emoji="✏️" , custom_id="edit_placeholder", disabled=True)
    async def edit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id:
            await interaction.response.send_message("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return
        
        message = await self.tournament.message
        if message:
            if type(message.channel) == discord.TextChannel:
                raise Exception("EditTournamentView only works in threads")
            view = await EditTournamentView.create(self.tournament, message.channel)
            await interaction.respond(view=view, ephemeral=True)
        else:
            await interaction.response.send_message("Turnier Nachricht nicht gefunden", ephemeral=True)

    @discord.ui.button(label="Spieler rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷", custom_id="kick_placeholder")
    async def kick_button(self, button:discord.ui.Button, interaction:discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id:
            await interaction.respond("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return
        await interaction.response.send_modal(KickPlayerModal(self.tournament))

    @discord.ui.button(label="Starte Turnier", style=discord.ButtonStyle.primary, emoji="▶️", custom_id="start_placeholder")
    async def start_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if type(interaction.user) != discord.Member:
            await interaction.respond("Du bist kein Mitglied auf diesem Server!", ephemeral=True)
            return
        if interaction.user.id != self.tournament.organizer_id:
            await interaction.respond("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return
        await interaction.respond("Starte Turnier...", ephemeral=True)
        
        message = await self.tournament.message
        if message:
            await message.edit(view=None)
        else:
            await interaction.response.send_message("Turnier Nachricht nicht gefunden", ephemeral=True)
            return
        
        players = []
        participants = self.tournament.get_users_by_state(ParticipationState.PARTICIPATE)
        for participant_id in participants:
            member = await get_member(participant_id, self.tournament)
            player_name = EMOJI_PATTERN.sub("", member.display_name).strip()
            players.append(swiss_mtg.Player(player_name, participant_id))

        swiss_tournament = swiss_mtg.SwissTournament(players)
        self.tournament.swiss_tournament = swiss_tournament
        await self.tournament.next_round(interaction)

    async def update_button_ids(self, message_id: int):
        """ Update button custom IDs after the message is created """
        self.message_id = message_id
        tournament_id = await self.tournament.get_id()
        # Update custom IDs for the buttons
        self.join_button.custom_id = f"join_{tournament_id}"
        self.leave_button.custom_id = f"leave_{tournament_id}"
        self.tentative_button.custom_id = f"tentative_{tournament_id}"
        # self.waitlist_button.custom_id = f"waitlist_{tournament_id}"
        self.edit_button.custom_id = f"edit_{tournament_id}"
        self.start_button.custom_id = f"start_{tournament_id}"
        self.kick_button.custom_id = f"kick_{tournament_id}"

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
        if self.parse:
            new_value = self.parse(self.input.value)

        setattr(self.tournament, self.key, new_value)
        
        await interaction.response.edit_message(embed=await self.tournament.to_embed(), view=self.view)

class EditTournamentView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await EditTournamentView.create(...)' instead")
    
    async def _init(self, tournament:SpelltableTournament, channel:discord.TextChannel):
        super().__init__()
        self.tournament = tournament
        self.channel = channel
    
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
          
    @discord.ui.button(label="Zeit", style=discord.ButtonStyle.primary, emoji="🕑")
    async def time_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        input = discord.ui.InputText(
            label="Start",
            placeholder="Die Startzeit für das Turnier",
            required=True,
            value=str(self.tournament.time) if self.tournament.time else "",
        )

        def parse(input_value):
            return date_time_interpretation.parse_date(input_value)

        await interaction.response.send_modal(EnterTextModal(input, "time", self.tournament, self, parse))

    @discord.ui.button(label="Abschicken", style=discord.ButtonStyle.success, emoji="✅")
    async def submit_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        if not type(interaction.channel) == discord.TextChannel:
            await interaction.response.send_message("Threads can only be created in text channels.", ephemeral=True)
            return
        await interaction.response.defer()
        self.disable_all_items()
        await interaction.edit(view=self)
        #  save into database
        participationView = await ParticipationView.create(self.tournament)
        thread = await interaction.channel.create_thread(name=self.tournament.title)
        message = await thread.send(embed=await self.tournament.to_embed(), view=participationView)
        self.tournament.message = message
        await participationView.update_button_ids(message.id)
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
                view = await ParticipationView.create(tournament)
                BOT.add_view(view) # Reattach buttons
                # message = await tournament.message
                # await message.edit(view=view) # to update the buttons status (enabled/disabled)
                active_tournaments[message_path] = tournament
                message = await tournament.message
                link_log.info(f"Turnier wurde geladen: {message.jump_url}")
            except discord.errors.NotFound:
                log.warning("Turnier konnte nicht geladen werden, weil vermutlich der entprechende Channel gelöscht wurde")

        # Reattach buttons
        for key, tournament in active_tournaments.items():
            if tournament.swiss_tournament and type(tournament.swiss_tournament) == swiss_mtg.SwissTournament:
                if tournament.swiss_tournament.rounds:
                    for round in tournament.swiss_tournament.rounds:
                        BOT.add_view(await ReportMatchView.create(round, tournament))
                        BOT.add_view(await StartNextRoundView.create(round, tournament))

        log.debug(self.__class__.__name__ + " is ready")

    @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(
        self,
        ctx:ApplicationContext,
        titel:Option(str, description="Der Titel, den das Turnier tragen soll"),
        max_teilnehmer:Option(int, description="Maximale Anzahl an Teilnehmern", required=False, default=None),
    ):
        if type(ctx.channel) != discord.TextChannel:
            await ctx.respond("Dieser Befehl kann nur in einem Textkanal verwendet werden.", ephemeral=True)
            return
        if type(ctx.author) != discord.Member:
            await ctx.respond("Du bist kein Mitglied dieses Servers!", ephemeral=True)
            return
        if ctx.guild is None:
            await ctx.respond("Dieser Befehl kann nur in einem Server verwendet werden.", ephemeral=True)
            return
        tournament = SpelltableTournament(ctx.guild, titel, ctx.author.id, max_teilnehmer)
        tournament.organizer = ctx.author
        view = await EditTournamentView.create(tournament, ctx.channel)
        await ctx.respond(
            embed=await tournament.to_embed(),
            view=view,
            ephemeral=True)

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
