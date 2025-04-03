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

test_participants = []

IS_DEBUG = env.DEBUG

if IS_DEBUG:
    with open("test_participants.txt", "r", encoding="utf-8") as file:
        test_participants = [int(line.strip()) for line in file]  # Splits by spaces

EMOJI_PATTERN = re.compile("[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\U0001F600-\U0001F64F]+", flags=re.UNICODE)
TOURNAMENTS_FOLDER = "tournaments"

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "serialize"):  # Check if the object has a serialize() method
            mapping = obj.serialize()
            mapping["class"] = type(obj).__name__
            return mapping
        return super().default(obj)

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

def save_tournaments():
    for tournament in active_tournaments.values():
        save_tournament(tournament)

def save_tournament(tournament: "SpelltableTournament"):
    # Ensure the directory exists
    os.makedirs(TOURNAMENTS_FOLDER, exist_ok=True)

    # Convert tournament ID (slashes to underscores) for filename
    filename = tournament.get_id().replace("/", "_") + ".json"
    file_path = os.path.join(TOURNAMENTS_FOLDER, filename)

    try:
        with open(file_path, "w") as file:
            json.dump(tournament, file, cls=CustomJSONEncoder, indent=4)
    except Exception as e:
        print(f"Error saving tournament {tournament.get_id()}: {e}")

class SpelltableTournament():
    def __init__(self, title:str, organizer:discord.Member|discord.User):
        self.title = title
        self.description = None
        self.time:datetime = None
        self.organizer = organizer
        self.participants:list[int] = []
        self.declined = []
        self.tentative = []
        self.message:discord.message.Message = None
        self.swiss_tournament:swiss_mtg.SwissTournament = None

        if IS_DEBUG:
            self.participants.extend(test_participants)

    def get_id(self):
        return f"{self.message.guild.id}/{self.message.channel.id}/{self.message.id}"

    @classmethod
    async def deserialize(cls, data, guild:discord.Guild): #, organizer, message):
        channel:discord.TextChannel = await discord.utils.get_or_fetch(guild, "channel", data["channel_id"])
        message = await channel.fetch_message(data["message_id"])
        organizer:discord.Member = await discord.utils.get_or_fetch(guild, "member", data["organizer_id"])

        tournament = cls(data["name"], organizer)
        tournament.description = data["description"]
        if "time" in data and data["time"]:
            tournament.time = datetime.fromisoformat(data["time"])
        tournament.participants = data["participants"]
        tournament.declined = data["declined"]
        tournament.tentative = data["tentative"]
        tournament.message = message

        if "tournament" in data and data["tournament"]:
            swiss_tournament_data = data['tournament']
            tournament.swiss_tournament = await swiss_mtg.SwissTournament.deserialize(swiss_tournament_data, channel)
        
        return tournament
    
    def serialize(self):
        return {
            "name": self.title,
            "organizer_id": self.organizer.id,
            "description": self.description,
            "time": self.time.isoformat() if self.time else None,
            "participants": self.participants,
            "declined": self.declined,
            "tentative": self.tentative,
            "guild_id": self.message.guild.id,
            "message_id": self.message.id,
            "channel_id": self.message.channel.id,
            "tournament": self.swiss_tournament
        }

    async def user_state(self, userid, state):
        if userid in self.participants:
            self.participants.remove(userid)
        if userid in self.declined:
            self.declined.remove(userid)
        if userid in self.tentative:
            self.tentative.remove(userid)
        if state == "participate":
            self.participants.append(userid)
        elif state == "decline":
            self.declined.append(userid)
        elif state == "tentative":
            self.tentative.append(userid)
        await self.message.edit(embed=self.to_embed())

    def to_embed(self):
        embed = Embed(
            title=self.title,
            description=self.description,
            color=Color.from_rgb(37, 88, 79)
        )
        if self.organizer:
            embed.set_author(name=self.organizer.display_name, icon_url=self.organizer.avatar.url)
        if self.time:
            embed.add_field(name="Start", value=discord.utils.format_dt(self.time, "F")+"\n"+discord.utils.format_dt(self.time, 'R'), inline=False)
        embed.add_field(name="✅ Teilnehmer", value="\n".join([f"<@{p}>" for p in self.participants]), inline=True)
        embed.add_field(name="❌ Abgelehnt", value="\n".join([f"<@{p}>" for p in self.declined]), inline=True)
        embed.add_field(name="❓ Vielleicht", value="\n".join([f"<@{p}>" for p in self.tentative]), inline=True)
        return embed
    

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

def image_from_standings(tournament:SpelltableTournament) -> str:
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
    id = tournament.get_id()
    filename = f'tournaments/{id.replace("/", "_")}_standings_round_{round.round_number}.png'
    table_to_image.generate_image(data, filename)

    return filename

class StartNextRoundView(discord.ui.View):
    def __init__(self, round, tournament:SpelltableTournament):
        super().__init__(timeout=None)
        self.tournament = tournament
        self.previous_round:swiss_mtg.Round = round

        self.next_round_button.custom_id = StartNextRoundView.join_button_id(round, tournament)

    @classmethod
    def join_button_id(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        return f"start_next_round_{tournament.get_id()}_{round.round_number}"
    
    @discord.ui.button(label="Nächte Runde", style=discord.ButtonStyle.success, emoji="➡️", custom_id="start_next_round_placeholder")
    async def next_round_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user != self.tournament.organizer:
            await interaction.respond("Nur der Turn Organisator darf dies tun.")
            return
        await self.previous_round.message_pairings.edit(view=None)
        await self.previous_round.message_standings.edit(view=None)
        await interaction.respond(f"Berechne Paarungen für Runde {self.previous_round.round_number+1}...", ephemeral=True)
        round = self.tournament.swiss_tournament.pair_players()
        reportMatchView = ReportMatchView(round, self.tournament)
        message_pairings:discord.message.Message = await self.previous_round.message_standings.reply(embed=format_pairings(round), view=reportMatchView)
        round.message_pairings = message_pairings
        save_tournament(self.tournament)

class ReportMatchModal(discord.ui.Modal):
    def __init__(self, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match):
        super().__init__(title="Report Match Result")
        self.tournament = tournament
        self.match = match
        self.round = round

        self.p1_score = discord.ui.InputText(
            label=f"Player 1 - {match.player1.user.name}",
            placeholder="",
            required=True,
            value=match.wins[match.player1] or "0" if match.is_finished() else ""
        )
        self.add_item(self.p1_score)
        self.p2_score = discord.ui.InputText(
            label=f"Player 2 - {match.player2.user.name}",
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

    async def callback(self, interaction: discord.Interaction):
        p1_user:discord.Member = self.match.player1.user
        p2_user:discord.Member = self.match.player2.user
        try:
            self.match.set_result(int(self.p1_score.value), int(self.p2_score.value), int(self.draw_score.value) if self.draw_score.value else 0)
        except ValueError as e:
            await interaction.respond(f"Dein Match Resultat {int(self.p1_score.value)}-{int(self.p2_score.value)}-{int(self.draw_score.value) if self.draw_score.value else 0} ist invalide: {e}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Match result submitted: **{p1_user.mention}** vs **{p2_user.mention}** → {self.p1_score.value}-{self.p2_score.value}-{self.draw_score.value if self.draw_score.value else 0}",
            ephemeral=True
        )

        if IS_DEBUG:
            swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

        new_embed = format_pairings(self.round)
        await self.round.message_pairings.edit(embed=new_embed)
        
        if self.round.is_concluded():
            # table_text = format_standings(self.tournament.swiss_tournament)
            image = image_from_standings(self.tournament)
            file = discord.File(image, filename=image)
            if self.tournament.swiss_tournament.current_round().round_number >= self.tournament.swiss_tournament.max_rounds:
                # tournament finished
                swiss_mtg.sort_players_by_standings(self.tournament.swiss_tournament.players)
                winner = self.tournament.swiss_tournament.players[0]
                content = f"Finales Ergebnis!\nHerzlichen Glückwunsch <@{winner.player_id}> für den Sieg"
                if self.round.message_standings:
                    await self.round.message_standings.edit(file=file, content=content)
                else:
                    message_standings = await interaction.followup.send(file=file, content=content)
                    self.round.message_standings = message_standings
            else:
                content = f"Platzierungen nach der {self.tournament.swiss_tournament.current_round().round_number}. Runde"
                start_next_round_view = StartNextRoundView(self.round, self.tournament)
                if self.round.message_standings:
                    await self.round.message_standings.edit(content=content, view=start_next_round_view, file=file)#, embed=embed)
                else:
                    message_standings = await interaction.followup.send(content=content, view=start_next_round_view, file=file)#, embed=embed)
                    self.round.message_standings = message_standings
            # TODO Enable "Runde beenden" Button (only usable by TO/Manager)
            # after that button is clicked, calculate Standings and disable Report Match Result Button
        save_tournament(self.tournament)

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
        swiss_tournament = self.tournament.swiss_tournament
        text = format_standings(swiss_tournament)

        round = swiss_tournament.current_round()

        if round.message_standings:
            await round.message_standings.edit(content=text) #, embed=embed)
        else:
            pass
            # message_standings = await interaction.followup.send(content=text)#, embed=embed)
            # round.message_standings = message_standings
        
        new_embed = format_pairings(round)
        await round.message_pairings.edit(embed=new_embed)

        save_tournament(self.tournament)
        await interaction.response.send_message("Du wurdest aus dem Turnier entfernt", ephemeral=True)

async def simulate_on_not_playing(tournament, round, interaction):
    if IS_DEBUG:
        swiss_mtg.simulate_remaining_matches(tournament.swiss_tournament)

        new_embed = format_pairings(round)
        await round.message_pairings.edit(embed=new_embed)
        
        if round.is_concluded():
            if tournament.swiss_tournament.current_round().round_number >= tournament.swiss_tournament.max_rounds:
                swiss_mtg.sort_players_by_standings(tournament.swiss_tournament.players)
                winner = tournament.swiss_tournament.players[0]
                content = f"Finales Ergebnis!\nHerzlichen Glückwunsch <@{winner.player_id}> für den Sieg"

                image = image_from_standings(tournament)
                file = discord.File(image, filename=image)
                if round.message_standings:
                    await round.message_standings.edit(file=file, content=content)
                else:
                    message_standings = await interaction.followup.send(file=file, content=content)
                    round.message_standings = message_standings
            else:
                start_next_round_view = StartNextRoundView(round, tournament)
                content = f"Platzierungen nach der {tournament.swiss_tournament.current_round().round_number}. Runde"
                image = image_from_standings(tournament)
                file = discord.File(image, filename=image)
                if round.message_standings:
                    await round.message_standings.edit(content=content, view=start_next_round_view, file=file) #, embed=embed)
                else:
                    message_standings = await interaction.followup.send(content=content, view=start_next_round_view, file=file)#, embed=embed)
                    round.message_standings = message_standings

        save_tournament(tournament)

class ReportMatchView(discord.ui.View):
    def __init__(self, round:swiss_mtg.Round, tournament:SpelltableTournament):
        super().__init__(timeout=None)
        self.round = round
        self.tournament = tournament

        report_button_id = f"report_{tournament.get_id()}_{round.round_number}"
        drop_id = f"drop_{tournament.get_id()}_{round.round_number}"
        print(report_button_id)
        print(drop_id)
        self.report_button.custom_id = report_button_id
        self.drop_button.custom_id = drop_id

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
        
        try:
            p1_user:discord.Member = await discord.utils.get_or_fetch(interaction.guild, "member", the_match.player1.player_id)
        except:
            p1_user:discord.Member = await discord.utils.get_or_fetch(interaction.client, "user", the_match.player1.player_id)
            
        try:
            p2_user:discord.Member = await discord.utils.get_or_fetch(interaction.guild, "member", the_match.player2.player_id)
        except:
            p2_user:discord.Member = await discord.utils.get_or_fetch(interaction.client, "user", the_match.player2.player_id)

        the_match.player1.user = p1_user
        the_match.player2.user = p2_user

        await interaction.response.send_modal(ReportMatchModal(self.tournament, self.round, the_match))

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


def format_pairings(round:swiss_mtg.Round) -> Embed:
    bye_pairings = [f"<@{match.player1.player_id}> bekommt das Bye" for match in round.matches if match.is_bye()]
    
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
    def __init__(self, tournament: SpelltableTournament):
        super().__init__(timeout=None)  # Ensure persistence
        self.tournament = tournament

        # Placeholder message ID (it gets updated after sending the message)
        if tournament.message:
            self.update_button_ids(tournament.message.id)

    @discord.ui.button(label="Teilnehmen", style=discord.ButtonStyle.success, emoji="✅", custom_id="join_placeholder")
    async def join_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, "participate")
        save_tournament(self.tournament)

    @discord.ui.button(label="Absagen", style=discord.ButtonStyle.danger, emoji="❌", custom_id="leave_placeholder")
    async def leave_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, "decline")
        save_tournament(self.tournament)

    @discord.ui.button(label="Vielleicht", style=discord.ButtonStyle.primary, emoji="❓", custom_id="tentative_placeholder")
    async def tentative_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, "tentative")
        save_tournament(self.tournament)

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️" , custom_id="edit_placeholder", disabled=True)
    async def edit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer.id:
            await interaction.response.send_message("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return
        
        view = EditTournamentView(self.tournament, self.tournament.message.channel)
        await interaction.respond(view=view, ephemeral=True)

    @discord.ui.button(label="Starte Turnier", style=discord.ButtonStyle.primary, emoji="▶️", custom_id="start_placeholder")
    async def start_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer.id:
            await interaction.respond("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return
        await interaction.respond("Starte Turnier und berechne erste Runde...", ephemeral=True)
        
        await self.tournament.message.edit(view=None)

        players = []
        for participant_id in self.tournament.participants:
            try:
                member:discord.Member = await discord.utils.get_or_fetch(interaction.guild, "member", participant_id)
            except:
                member = await discord.utils.get_or_fetch(interaction.client, "user", participant_id)

            player_name = EMOJI_PATTERN.sub("", member.display_name).strip()
            players.append(swiss_mtg.Player(player_name, participant_id))

        swiss_tournament = swiss_mtg.SwissTournament(players)
        self.tournament.swiss_tournament = swiss_tournament
        round = swiss_tournament.pair_players()
        # modal für match report
        view = ReportMatchView(round, self.tournament)
        message_pairings:discord.message.Message = await interaction.followup.send(embed=format_pairings(round), view=view)
        round.message_pairings = message_pairings
        save_tournament(self.tournament)

    def update_button_ids(self, message_id: int):
        """ Update button custom IDs after the message is created """
        self.message_id = message_id
        tournament_id = self.tournament.get_id()
        # Update custom IDs for the buttons
        self.join_button.custom_id = f"join_{tournament_id}"
        self.leave_button.custom_id = f"leave_{tournament_id}"
        self.tentative_button.custom_id = f"tentative_{tournament_id}"
        self.edit_button.custom_id = f"edit_{tournament_id}"
        self.start_button.custom_id = f"start_{tournament_id}"

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
        
        if self.tournament.description and self.tournament.time:
            self.view.submit_button.disabled = False

        await interaction.response.edit_message(embed=self.tournament.to_embed(), view=self.view)

class EditTournamentView(discord.ui.View):
    def __init__(self, tournament:SpelltableTournament, channel:discord.TextChannel):
        super().__init__()
        self.tournament = tournament
        self.channel = channel

        self.submit_button = discord.ui.Button(label="Abschicken", style=discord.ButtonStyle.success, emoji="✅")
        self.submit_button.callback = self.submit_callback

        # Abschicken immer erlaubt (Beschreibung und Zeit optional)
        # self.submit_button.disabled = True and not IS_DEBUG

        # if self.tournament.description and self.tournament.time:
        #     self.submit_button.disabled = False
        
        self.add_item(self.submit_button)

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

    async def submit_callback(self, interaction:discord.Interaction):
        if not interaction.channel:
            await interaction.response.send_message("Threads can only be created in text channels.", ephemeral=True)
            return
        self.disable_all_items()
        await interaction.edit(view=self)
        #  save into database
        participationView = ParticipationView(self.tournament)
        thread = await interaction.channel.create_thread(name=self.tournament.title)
        message = await thread.send(embed=self.tournament.to_embed(), view=participationView)
        self.tournament.message = message
        participationView.update_button_ids(message.id)
        await message.edit(view=participationView)            
        active_tournaments[self.tournament.get_id()] = self.tournament
        save_tournament(self.tournament)
        await interaction.respond(f"Prima! Das Turnier `{self.tournament.title}` wurde erstellt: {message.jump_url}", ephemeral=True)

class SpelltableTournamentManager(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        guild:discord.Guild = self.bot.get_guild(env.GUILD_ID)
        loaded_tournaments = await load_tournaments(guild)

        global active_tournaments
        for message_path, tournament in loaded_tournaments.items():
            view = ParticipationView(tournament)
            self.bot.add_view(view) # Reattach buttons
            await tournament.message.edit(view=view) # to update the buttons status (enabled/disabled)
            active_tournaments[message_path] = tournament

        # Reattach buttons
        for key, tournament in active_tournaments.items():
            if tournament.swiss_tournament and type(tournament.swiss_tournament) == swiss_mtg.SwissTournament:
                if tournament.swiss_tournament.rounds:
                    for round in tournament.swiss_tournament.rounds:
                        self.bot.add_view(ReportMatchView(round, tournament))
                        self.bot.add_view(StartNextRoundView(round, tournament))

        log.debug(self.__class__.__name__ + " is ready")

    @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(
        self,
        ctx:ApplicationContext,
        titel:Option(str, description="Der Titel, den das Turnier tragen soll")
    ):
        if type(ctx.channel) != discord.TextChannel:
            await ctx.respond("Dieser Befehl kann nur in einem Textkanal verwendet werden.", ephemeral=True)
            return
        tournament = SpelltableTournament(titel, ctx.author)
        view = EditTournamentView(tournament, ctx.channel)
        await ctx.respond(
            embed=tournament.to_embed(),
            view=view,
            ephemeral=True)

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
