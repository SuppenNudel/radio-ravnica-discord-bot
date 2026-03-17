from ezcord import Cog, log
from typing import Annotated
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Option
import discord
from modules import date_time_interpretation
import os, re, shutil
from modules import swiss_mtg
from modules import env
import logging

from discord.ext import tasks
from datetime import datetime, time

from modules.spelltable.tournament_model import TOURNAMENTS_FOLDER, SpelltableTournament, load_tournaments, active_tournaments, update_tournament_message
from modules.spelltable.common_views import SubmitDeckView, KickPlayerModal, ParticipationState, StartNextRoundView, FinishTournamentView, next_round, update_hall_of_fame
from modules.spelltable import common_views

link_log = logging.getLogger("link_logger")

IS_DEBUG = env.DEBUG

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
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.TENTATIVE)

    @discord.ui.button(label="Absagen", style=discord.ButtonStyle.danger, emoji="❌")
    async def leave_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            await interaction.respond("Der Interaction User ist None. Das sollte nicht passieren.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.tournament.user_state(interaction.user.id, ParticipationState.DECLINE)

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️")
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

    @discord.ui.button(label="Spieler rauswerfen", style=discord.ButtonStyle.danger, emoji="🚷")
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
        await interaction.response.send_modal(KickPlayerModal(self.tournament))

    @discord.ui.button(label="Starte Turnier", style=discord.ButtonStyle.primary, emoji="▶️")
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
        await interaction.followup.send("Starte Turnier...", ephemeral=True)
        
        message = await self.tournament.message
        if message:
            await message.edit(view=None)
        else:
            await interaction.followup.send("Turnier Nachricht nicht gefunden", ephemeral=True)
            return
        
        players = []
        participants = self.tournament.get_users_by_state(ParticipationState.PARTICIPATE)
        for participant_id in participants:
            players.append(swiss_mtg.Player(participant_id))

        swiss_tournament = swiss_mtg.SwissTournament(players, max_rounds=self.tournament.max_rounds if self.tournament.max_rounds else None)
        self.tournament.swiss_tournament = swiss_tournament
        await next_round(self.tournament, interaction)
    
    
    @discord.ui.button(label="Turnier abbrechen", style=discord.ButtonStyle.danger, emoji="🛑")
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
        
        self.view.submit_button.disabled = not bool(self.tournament.time)
        await interaction.edit_original_response(embed=await self.tournament.to_embed(), view=self.view)

class EditTournamentView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await EditTournamentView.create(...)' instead")

    async def _init(self, tournament: SpelltableTournament, channel: discord.abc.Messageable):
        super().__init__()
        self.tournament = tournament
        self.channel = channel

        # Dynamically set the disabled state of the "Abschicken" button
        self.submit_button.disabled = not bool(self.tournament.time)

        self.days_per_match_callback.label = (
            "Turnier am Stück spielen" if self.tournament.days_per_match else "Eine Woche pro Runde"
        )

    @classmethod
    async def create(cls, tournament: SpelltableTournament, channel: discord.abc.Messageable):
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
    async def submit_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        self.disable_all_items()
        await interaction.edit(view=self)
        # Save into database
        participation_view = await ParticipationView.create(self.tournament)

        existing_message = await self.tournament.message
        if existing_message:
            # Edit existing message
            await self.tournament.save_tournament()
            await existing_message.edit(embed=await self.tournament.to_embed(), view=participation_view)
            await interaction.edit_original_response(content="Turnier wurde bearbeitet", embed=None, view=None)
        else:
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.respond("Threads können nur in Textkanälen erstellt werden.", ephemeral=True)
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
            message = await thread.send(embed=await self.tournament.to_embed(), view=participation_view)
            self.tournament.message = message
            await message.edit(view=participation_view)
            active_tournaments[await self.tournament.get_id()] = self.tournament
            await self.tournament.save_tournament()
            await interaction.followup.send(
                f"Prima! Das Turnier `{self.tournament.title}` wurde erstellt: {message.jump_url}", ephemeral=True
            )
            link_log.info(f"Ein Turnier wurde erstellt: {message.jump_url}")

class SpelltableTournamentManager(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.schedule_midnight_update.start()

    @Cog.listener()
    async def on_ready(self):
        loaded_tournaments = await load_tournaments(self.bot)

        global active_tournaments
        for message_path, tournament in loaded_tournaments.items():
            # Skip loading cancelled tournaments
            if tournament.cancelled:
                try:
                    await common_views.remove_tournament_views(tournament)
                except Exception:
                    log.exception(f"Failed to clear views for cancelled tournament `{tournament.title}` during startup")
                continue
            try:
                active_tournaments[message_path] = tournament
                tournament_message = await tournament.message
                if not tournament_message:
                    log.warning(f"Turnier Nachricht nicht gefunden: {message_path}")
                    continue

                # Unarchive thread if needed before any edits
                if tournament_message and isinstance(tournament_message.channel, discord.Thread) and tournament_message.channel.archived:
                    await tournament_message.channel.edit(archived=False)

                if not tournament.swiss_tournament:
                    # tournament has not been started yet
                    # sign-up is still open
                    view = await ParticipationView.create(tournament)
                    await tournament_message.edit(view=view)
                    continue

                # tournament has been started
                swiss_tournament = tournament.swiss_tournament
                tournament_concluded = swiss_tournament.winner is not None
                current_round = swiss_tournament.current_round()

                if tournament_concluded:
                    # Tournament is concluded
                    
                    # unarchive thread if needed
                    if tournament_message and isinstance(tournament_message.channel, discord.Thread) and tournament_message.channel.archived:
                        await tournament_message.channel.edit(archived=False)

                    # Update standings with latest deck submissions
                    await tournament.update_metagame()
                    
                    # activate deck submission button
                    if current_round.message_id_standings:
                        message = await tournament.get_message(current_round.message_id_standings)
                        if message:
                            submit_deck_view = await SubmitDeckView.create(tournament)
                            if message.embeds:
                                await message.edit(embed=message.embeds[0], view=submit_deck_view)
                            else:
                                await message.edit(view=submit_deck_view)
                        else:
                            link_log.error(f"Could not find standings message for concluded tournament: {tournament_message.jump_url}")

                    await update_hall_of_fame(tournament)
                    continue

                if current_round.message_id_standings:
                    # standings have been posted
                    message = await tournament.get_message(current_round.message_id_standings)
                    if message and isinstance(message.channel, discord.Thread) and message.channel.archived:
                        await message.channel.edit(archived=False)
                    if message:
                        is_last_round = current_round.round_number >= swiss_tournament.rounds_count
                        if is_last_round:
                            view = await FinishTournamentView.create(tournament)
                        else:
                            view = await StartNextRoundView.create(current_round, tournament)
                        # Preserve existing content and embed when adding view
                        if message.embeds:
                            await message.edit(embed=message.embeds[0], view=view)
                        else:
                            await message.edit(view=view)
                if current_round.message_id_pairings:
                    # pairings have been posted
                    message = await tournament.get_message(current_round.message_id_pairings)
                    if message and isinstance(message.channel, discord.Thread) and message.channel.archived:
                        await message.channel.edit(archived=False)
                    await common_views.update_pairings(tournament, current_round)
                else:
                    # pairings have not been posted yet
                    await common_views.update_pairings(tournament, current_round)                

                link_log.info(f"Turnier wurde geladen: {tournament_message.jump_url}")
            except discord.errors.NotFound:
                file_path = TOURNAMENTS_FOLDER+"/"+(message_path.replace("/", "_"))+".json"
                failed_tournaments_folder = TOURNAMENTS_FOLDER + "/failed"
                
                # Create failed tournaments folder if it doesn't exist
                if not os.path.exists(failed_tournaments_folder):
                    os.makedirs(failed_tournaments_folder)
                
                # Move the file instead of deleting it
                failed_file_path = os.path.join(failed_tournaments_folder, os.path.basename(file_path))
                shutil.move(file_path, failed_file_path)
                log.warning(f"Turnier konnte nicht geladen werden, weil vermutlich der entprechende Channel gelöscht wurde. Datei verschoben zu {failed_file_path}")
        
        await update_tournament_message(self.bot)

        log.debug(self.__class__.__name__ + " is ready")

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

    # @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(
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
            and ctx.channel.category.name in ("Spelltable Turniere")
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

def setup(bot:Bot):
    bot.add_cog(SpelltableTournamentManager(bot))
