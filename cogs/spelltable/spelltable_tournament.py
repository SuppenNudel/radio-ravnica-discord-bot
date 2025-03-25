from ezcord import Cog, log
from discord.ext.commands import slash_command, has_role
from discord import ApplicationContext, Bot, Embed, Color
import discord
from modules import date_time_interpretation
from discord.utils import format_dt
from datetime import datetime
import os, json
from modules import swiss_mtg
import random

test_participants = [
    270288996666441728,
    356120044754698252,
    408731851771871232,
    200627557157175296,
    474617962129522692,
    1034159825711546398,
    792817438151934003,
    331894482544885760,
    319870394238763008
]

TOURNAMENT_FILE = "tournaments.json"

def load_tournaments():
    if os.path.exists(TOURNAMENT_FILE):
        with open(TOURNAMENT_FILE, "r") as f:
            return json.load(f)
    return {}


active_tournaments = load_tournaments()

def save_tournaments():
    with open(TOURNAMENT_FILE, "w") as f:
        json.dump(active_tournaments, f, indent=4)

class SpelltableTournament():
    def __init__(self, title:str, organizer:discord.Member|discord.User):
        self.title = title
        self.description = None
        self.time:datetime = None
        self.organizer = organizer
        self.participants = []
        self.declined = []
        self.tentative = []
        self.message:discord.Message = None

        self.participants.extend(test_participants)

    @classmethod
    def deserialize(cls, data, organizer, message):
        tournament = cls(data["name"], organizer)
        tournament.description = data["description"]
        tournament.time = datetime.fromisoformat(data["time"])
        tournament.participants = data["participants"]
        tournament.declined = data["declined"]
        tournament.tentative = data["tentative"]
        tournament.message = message
        return tournament
    
    def serialize(self):
        return {
            "name": self.title,
            "organizer_id": self.organizer.id,
            "description": self.description,
            "time": self.time.isoformat(),
            "participants": self.participants,
            "declined": self.declined,
            "tentative": self.tentative,
            "guild_id": self.message.guild.id,
            "message_id": self.message.id,
            "channel_id": self.message.channel.id
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
            formatted_time = discord.utils.format_dt(self.time)
            embed.add_field(name="Start", value=formatted_time, inline=False)
        embed.add_field(name="✅ Teilnehmer", value="\n".join([f"<@{p}>" for p in self.participants]), inline=True)
        embed.add_field(name="❌ Abgelehnt", value="\n".join([f"<@{p}>" for p in self.declined]), inline=True)
        embed.add_field(name="❓ Vielleicht", value="\n".join([f"<@{p}>" for p in self.tentative]), inline=True)
        return embed

class ReportMatchModal(discord.ui.Modal):
    def __init__(self, round:int, pairings, player1: swiss_mtg.Player, player2: swiss_mtg.Player, message):
        super().__init__(title="Report Match Result")
        self.player1 = player1
        self.player2 = player2
        self.round = round
        self.message = message
        self.pairings = pairings

        self.p1_score = discord.ui.InputText(
            label=f"Player 1 - {player1.user.name}",
            placeholder="",
            required=True
        )
        self.add_item(self.p1_score)
        self.p2_score = discord.ui.InputText(
            label=f"Player 2 - {player2.user.name}",
            placeholder="",
            required=True
        )
        self.add_item(self.p2_score)
        self.draw_score = discord.ui.InputText(
            label=f"Draws",
            placeholder="",
            required=False
        )
        self.add_item(self.draw_score)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Match result submitted: **{self.player1.user.mention}** vs **{self.player2.user.mention}** → {self.p1_score.value}-{self.p2_score.value}-{self.draw_score.value if self.draw_score.value else 0}",
            ephemeral=True
        )
        p1_tuple = (self.player2, self.p1_score.value, self.p2_score.value, self.draw_score.value if self.draw_score.value else 0)
        p2_tuple = (self.player1, self.p1_score.value, self.p2_score.value, self.draw_score.value if self.draw_score.value else 0)
        if self.round - 1 < len(self.player1.results):
            # If within bounds, update the existing result
            self.player1.results[self.round - 1] = p1_tuple
        else:
            # If out of bounds, append a new result
            self.player1.results.append(p1_tuple)

        if self.round - 1 < len(self.player2.results):
            # If within bounds, update the existing result
            self.player2.results[self.round - 1] = p2_tuple
        else:
            # If out of bounds, append a new result
            self.player2.results.append(p2_tuple)
        new_embed = format_pairings(self.pairings, self.round)
        await self.message.edit(embed=new_embed)

class ReportMatchView(discord.ui.View):
    def __init__(self, round:int, pairings, message):
        super().__init__(timeout=None)
        self.pairings = pairings
        self.round = round
        self.message = message

    @discord.ui.button(label="Report Match Result", style=discord.ButtonStyle.primary, custom_id="report_match")
    async def report_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        player1 = None
        player2 = None
        for p1, p2 in self.pairings:
            if not p1 or not p2:
                continue
            if p1.name == interaction.user.id or p2.name == interaction.user.id:
                player1 = p1
                player2 = p2
        if not player1 or not player2:
            await interaction.response.send_message("You are not participating in this tournament", ephemeral=True)
            return
        p1_user = await interaction.client.get_or_fetch_user(player1.name)
        p2_user = await interaction.client.get_or_fetch_user(player2.name)
        player1.user = p1_user
        player2.user = p2_user
        await interaction.response.send_modal(ReportMatchModal(self.round, self.pairings, player1, player2, self.message))

def format_pairings(pairings:list[tuple[swiss_mtg.Player, swiss_mtg.Player]], current_round:int) -> Embed:
    bye_pairings = [f"<@{p1.name}> gets a bye" for p1, p2 in pairings if not p2]
    # for p1, p2 in pairings:
    #     if p1 and p2:
    #         p1win = random.randint(0, 2)
    #         p1loss = 3-p1win-random.randint(0,1)
    #         p1.results.append((p2, p1win, p1loss, 0))
    #         p2.results.append((p1, p1loss, p1win, 0))
    
    results = []
    for p1, p2 in pairings:
        if len(p1.results) >= current_round:
            opponent, win, loss, draw = p1.results[current_round-1]
            if opponent and opponent.name != p2.name:
                raise Exception("wrong opponent")
            results.append(f"{win} - {loss} - {draw}")
        else:
            results.append("Ausstehend")
    embed = Embed(
        title=f"Paarungen für die {current_round}. Runde",
        description=f"Die Spieler wurden zufällig gepaart. Viel Erfolg!\n{'\n'.join(bye_pairings)}",
        color=Color.from_rgb(37, 88, 79),
        fields=[
            discord.EmbedField(
                name="Spieler 1",
                value="\n".join([f"<@{p1.name}>" for p1, p2 in pairings]),
                inline=True
            ),
            discord.EmbedField(
                name="Spieler 2",
                value="\n".join([f"<@{p2.name}>" if p2 else "BYE" for p1, p2 in pairings]),
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
    def __init__(self, tournament: SpelltableTournament, guild_id: int, channel_id: int):
        super().__init__(timeout=None)  # Ensure persistence
        self.tournament = tournament
        self.guild_id = guild_id
        self.channel_id = channel_id

        # Placeholder message ID (it gets updated after sending the message)
        if tournament.message:
            self.update_button_ids(tournament.message.id)

    @discord.ui.button(label="Teilnehmen", style=discord.ButtonStyle.success, custom_id="join_placeholder")
    async def join_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, "participate")
        save_tournaments()

    @discord.ui.button(label="Absagen", style=discord.ButtonStyle.danger, custom_id="leave_placeholder")
    async def leave_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, "decline")
        save_tournaments()

    @discord.ui.button(label="Vielleicht", style=discord.ButtonStyle.primary, custom_id="tentative_placeholder")
    async def tentative_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, "tentative")
        save_tournaments()

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, custom_id="edit_placeholder")
    async def edit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer.id:
            await interaction.response.send_message("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return

        await interaction.response.send_message(f"Edit button clicked by {interaction.user.name}", ephemeral=True)

    @discord.ui.button(label="Starte Turnier", style=discord.ButtonStyle.primary, custom_id="start_placeholder")
    async def start_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer.id:
            await interaction.response.send_message("Du bist nicht der Turnierorganisator!", ephemeral=True)
            return

        await interaction.response.send_message(f"Start button clicked by {interaction.user.name}", ephemeral=True)
        tournament = swiss_mtg.Tournament(self.tournament.title, self.tournament.participants)
        pairings = tournament.next_round()

        # modal für match report
        message = await interaction.followup.send(f"Paarungen für die {tournament.current_round}. Runde:",
                                        embed=format_pairings(pairings, tournament.current_round))
        view = ReportMatchView(tournament.current_round, pairings, message)
        await message.edit(view=view)

    def update_button_ids(self, message_id: int):
        """ Update button custom IDs after the message is created """
        self.message_id = message_id
        tournament_id = f"{self.guild_id}/{self.channel_id}/{self.message_id}"
        # Update custom IDs for the buttons
        self.join_button.custom_id = f"join_{tournament_id}"
        self.leave_button.custom_id = f"leave_{tournament_id}"
        self.tentative_button.custom_id = f"tentative_{tournament_id}"
        self.edit_button.custom_id = f"edit_{tournament_id}"
        self.start_button.custom_id = f"start_{tournament_id}"


class SpelltableTournamentManager(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

        for message_path, data in active_tournaments.items():
            channel:discord.TextChannel = await discord.utils.get_or_fetch(self.bot, "channel", data["channel_id"])
            message = await channel.fetch_message(data["message_id"])
            organizer = await self.bot.get_or_fetch_user(data["organizer_id"])
            tournament = SpelltableTournament.deserialize(data, organizer, message)  # Restore tournament object
            view = ParticipationView(tournament, channel.guild.id, channel.id)  # Create a new view
            self.bot.add_view(view)  # Reattach buttons

    @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(self, ctx:ApplicationContext, titel:str):
        if type(ctx.channel) != discord.TextChannel:
            await ctx.respond("Dieser Befehl kann nur in einem Textkanal verwendet werden.", ephemeral=True)
            return
        tournament = SpelltableTournament(titel, ctx.author)
        try:
            user = ctx.author
            direct_message = await user.send(
                content="Lass uns zusammen das Turnier erstellen",
                embed=tournament.to_embed()
            )
            def check(m):
                return m.author == user and isinstance(m.channel, discord.DMChannel)
            
            await ctx.send_response(f"Lass uns zusammen in den Direktnachrichten das Turnier erstellen: {direct_message.jump_url}", ephemeral=True)

            to_fill_out = ["description", "time"]
            for key in to_fill_out:
                await user.send(f"Gib mir ein(e) {key} für das Event:")
                event_response = await self.bot.wait_for("message", check=check, timeout=60)
                value = event_response.content
                if key == "time":
                    value = date_time_interpretation.parse_date(value)
                setattr(tournament, key, value)
                await user.send(embed=tournament.to_embed(), allowed_mentions=discord.AllowedMentions.none())
            
            #  save into database
            participationView = ParticipationView(tournament, ctx.guild.id, ctx.channel.id)
            message = await ctx.channel.send(content="Content", embed=tournament.to_embed(), view=participationView)
            tournament.message = message
            participationView.update_button_ids(message.id)
            await message.edit(view=participationView)
            active_tournaments[f"{message.guild.id}/{message.channel.id}/{message.id}"] = tournament.serialize()
            save_tournaments()
            await user.send(f"Prima! Das Turnier `{tournament.title}` findet {format_dt(tournament.time, 'R')} am {format_dt(tournament.time, 'D')} um {format_dt(tournament.time, 't')} statt. {message.jump_url}")
        except discord.Forbidden:
            await ctx.respond("Ich konnte dir nicht per Direktnachricht schreiben! Bitte erlaube Direktnachrichten von Servermitgliedern.")
        except TimeoutError as e:
            await user.send("Du hast zu lange zum Antworten gebraucht. Bitte verwende nocheinmal `/erstelle_turnier`.")

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
