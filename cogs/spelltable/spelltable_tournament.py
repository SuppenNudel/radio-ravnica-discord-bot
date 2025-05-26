from ezcord import Cog, log
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Option
import discord
from modules import date_time_interpretation
import os, re
from modules import swiss_mtg
from modules import env
import logging
from datetime import datetime, timedelta
from modules.util import generate_calendar_image

from cogs.spelltable.tournament_model import TOURNAMENTS_FOLDER, SpelltableTournament, get_member, load_tournaments, active_tournaments
from cogs.spelltable.common_views import FinishTournamentView, KickPlayerModal, ParticipationState, ReportMatchView, StartNextRoundView, next_round

link_log = logging.getLogger("link_logger")

IS_DEBUG = env.DEBUG
BOT = None

EMOJI_PATTERN = re.compile("[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\U0001F600-\U0001F64F]+", flags=re.UNICODE)

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
        await next_round(self.tournament, interaction)

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
        
        await interaction.edit_original_response(embed=await self.tournament.to_embed(), view=self.view)

import pytz
timezone = pytz.timezone("Europe/Berlin")

async def generate_tournament_message(tournaments: list[SpelltableTournament]) -> str:
    # now = datetime.now(tz=timezone)

    # Sort tournaments by start date, placing those with `time=None` first
    tournaments = sorted(
        tournaments,
        key=lambda tournament: tournament.time if tournament.time is not None else datetime.min
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
        end = t.calc_end()
        t_message = await t.message
        return (
            f"> 🏆 **{t.title}** {t_message.jump_url}\n"
            f"> 🗓️ **Start:** {discord.utils.format_dt(t.time, "F") if t.time else 'TBD'}\n"
            f"> 🗓️ **Ende:** {discord.utils.format_dt(end, "F") if end else 'TBD'}\n"
            # f"> 🗂 **Format:** {t['format']}\n"
            f"> 👥 **Organisator:** {organizer.mention}"
        )

    # Build message parts
    msg = "## 🧙‍♂️ **RR Discord Turniere – Laufende & Geplante**\n\n"

    msg += "### 🔥 **Aktuell laufende Turniere**\n"
    if ongoing:
        for tourney, end in ongoing:
            msg += await format_tournament(tourney, end) + "\n\n"
    else:
        msg += "> _No tournaments are currently active._\n\n"

    msg += "### 🗓️ **Geplante Turniere**\n"
    if upcoming:
        for tourney, end in upcoming:
            msg += await format_tournament(tourney, end) + "\n\n"
    else:
        msg += "> _Keine Turniere bisher geplant._\n\n"

    msg += "---\n💬 Möchtest du auch ein Turnier erstellen? Verwende den Befehl </erstelle_turnier:1373740873120219288>!"

    return msg

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
            await self.tournament.save_tournament()
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
            await self.tournament.save_tournament()
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
        loaded_tournaments = await load_tournaments(guild, BOT)

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
                            # Tournament is concluded
                            view = await FinishTournamentView.create(tournament)
                            standings_message = await tournament.get_message(current_round.message_id_standings)
                            if standings_message:
                                await standings_message.edit(view=view)
                            
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
        
        tourney_list_message = await generate_tournament_message(list(active_tournaments.values()))
        calendar_img = generate_calendar_image.generate_calendar_month_column(2025, list(active_tournaments.values()))
        calendar_file = discord.File(calendar_img, filename=calendar_img)
        await guild.get_channel(1315427456232063028).send(tourney_list_message, file=calendar_file)

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

        tournament = SpelltableTournament(ctx.guild, titel, ctx.author.id, BOT)
        tournament.organizer = ctx.author
        view = await EditTournamentView.create(tournament, ctx.channel)
        await ctx.followup.send(
            embed=await tournament.to_embed(),
            view=view,
            ephemeral=True
        )

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
