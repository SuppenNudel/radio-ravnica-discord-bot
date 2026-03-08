import discord
import logging
import asyncio
from modules import swiss_mtg
from modules.spelltable.tournament_model import SpelltableTournament, use_custom_try, ParticipationState
from modules import env
from modules.hall_of_fame import get_hall_of_fame
from ezcord import log
from datetime import datetime

link_log = logging.getLogger("link_logger")

# Global dictionary to store locks per tournament to prevent race conditions
_tournament_round_locks: dict[str, asyncio.Lock] = {}

class SubmitDeckView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await SubmitDeckView.create(...)' instead")

    async def _init(self, tournament: SpelltableTournament):
        super().__init__(timeout=None)
        self.tournament = tournament

    @classmethod
    async def create(cls, tournament: SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(tournament)
        return instance

    @discord.ui.button(label="Deck einreichen", style=discord.ButtonStyle.primary, emoji="📝")
    async def submit_deck_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Check if user is part of the tournament first
        player = self.tournament.swiss_tournament.player_by_id(interaction.user.id)
        if not player:
            await interaction.response.send_message("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
            return
        
        try:
            await interaction.response.send_modal(SubmitDeckModal(self.tournament, interaction.user, player))
        except discord.errors.NotFound:
            # Interaction expired, try to send error message if possible
            try:
                await interaction.followup.send("Die Anfrage hat zu lange gedauert. Bitte versuche es erneut.", ephemeral=True)
            except:
                pass

class SubmitDeckModal(discord.ui.DesignerModal):
    def __init__(self, tournament: SpelltableTournament, player: discord.User, tournament_player: swiss_mtg.Player = None):
        super().__init__(title="Dein Deck; erst nach Abschluss veröffentlicht")
        self.tournament = tournament
        self.player = player

        # Use the passed tournament_player to avoid redundant lookup
        if tournament_player is None:
            tournament_player = tournament.swiss_tournament.player_by_id(self.player.id)
        
        deck:swiss_mtg.Deck = tournament_player.deck if tournament_player and tournament_player.deck else None

        # self.archetype_input = discord.ui.Select(
        #     placeholder="Wähle einen Archetyp...',
        #     options=[
        #         discord.SelectOption(label="Mono Red Burn", value="Mono Red Burn"),
        #         discord.SelectOption(label="Mono Blue Terror", value="Mono Blue Terror"),
        #         discord.SelectOption(label="Jund Wildfire", value="Jund Wildfire"),
        #         discord.SelectOption(label="Mono Red Synthesizer", value="Mono Red Synthesizer"),
        #         discord.SelectOption(label="Rakdos Madness", value="Rakdos Madness"),
        #         discord.SelectOption(label="Mono Blue Faeries", value="Mono Blue Faeries"),
        #     ],
        #     max_values=1,
        #     min_values=1,
        #     required=False
        # )
        
        # Create Labels and use set_input_text() method for optional fields
        self.deck_name_label = discord.ui.Label(
            "Archetyp", 
            description="Wie sich das Deck nennt, das du gespielt hast"
        )
        if deck and deck.archetype:
            self.deck_name_label.set_input_text(
                placeholder="Mono-Red Aggro, Jund Wildfire, ...",
                value=deck.archetype,
                min_length=0,
                required=False
            )
        else:
            self.deck_name_label.set_input_text(
                placeholder="Mono-Red Aggro, Jund Wildfire, ...",
                min_length=0,
                required=False
            )
        self.add_item(self.deck_name_label)
        
        self.decklist_label = discord.ui.Label(
            "Deckliste", 
            description="Ein Link zu deiner Deckliste, z.B. Moxfield oder mtggoldfish."
        )
        # Only set value if there's a deck with a link
        if deck and deck.link:
            self.decklist_label.set_input_text(
                placeholder="https://moxfield.com/decks/...",
                value=deck.link,
                min_length=0,
                required=False
            )
        else:
            self.decklist_label.set_input_text(
                placeholder="https://moxfield.com/decks/...",
                min_length=0,
                required=False
            )
        self.add_item(self.decklist_label)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Get values from Label items
        decklist = self.decklist_label.item.value.strip() if self.decklist_label.item.value else ""  # type: ignore
        archetype = self.deck_name_label.item.value if self.deck_name_label.item.value else None  # type: ignore
        
        # Save the decklist to the tournament/player (implement as needed)
        player:swiss_mtg.Player = self.tournament.swiss_tournament.player_by_id(self.player.id)
        if not player:
            await interaction.followup.send("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
            return
        
        player.deck = swiss_mtg.Deck.deserialize({"link": decklist, "archetype": archetype})
        await self.tournament.save_tournament()
        await self.tournament.update_metagame()
        await interaction.followup.send("Deine Deckliste wurde erfolgreich eingereicht!", ephemeral=True)

class FinishTournamentView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await FinishTournamentView.create(...)' instead")

    async def _init(self, tournament: SpelltableTournament):
        super().__init__(timeout=None)
        self.tournament:SpelltableTournament = tournament

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
            
            # Set winner first so metagame update can proceed
            self.tournament.swiss_tournament.winner = winner
            
            # Update final standings with deck submissions and add submit deck button
            await self.tournament.update_metagame()
            
            # Fetch the updated message to preserve the embed when adding the button
            standings_message = await self.tournament.get_message(current_round.message_id_standings)
            submit_deck_view = await SubmitDeckView.create(self.tournament)
            if standings_message and standings_message.embeds:
                await standings_message.edit(embed=standings_message.embeds[0], view=submit_deck_view)
            elif standings_message:
                await standings_message.edit(view=submit_deck_view)


            await interaction.response.send_message(content, ephemeral=False)
            link_log.info(f"Turnier abgeschlossen: `{self.tournament.title}`, Gewinner: <@{winner.player_id}>")
            await self.tournament.save_tournament()
            
            # Update hall of fame with tournament results
            await update_hall_of_fame(self.tournament)

            # contact players about tournament end
            for player in self.tournament.swiss_tournament.players:
                try:
                    user = await self.tournament.get_member(player.player_id)
                    if type(user) != discord.Member:
                        user = None
                except discord.NotFound:
                    user = None
                if user:
                    try:
                        await user.send(f"Das Turnier `{self.tournament.title}` ist abgeschlossen! Sieh dir das Endergebnis hier an: {standings_message.jump_url}")
                    except discord.Forbidden:
                        log.error(f"Konnte die Turnierende-Nachricht nicht an {user.mention} ({user.display_name}) senden")
        else:
            await interaction.respond("Kein Gewinner gefunden. Das sollte nicht passieren.", ephemeral=True)

class AdminMatchSelectView(discord.ui.View):
    def __init__(self, tournament:SpelltableTournament, round:swiss_mtg.Round, player_names:dict[int, str]):
        super().__init__(timeout=120)
        self.tournament = tournament
        self.round = round
        self.player_names = player_names
        self.message: discord.Message | None = None

        match_options = []
        for i, match in enumerate(self.round.matches):
            if match.is_bye():
                continue
            p1_label = self.player_names.get(match.player1.player_id, f"Player({match.player1.player_id})") if match.player1 else "BYE"
            p2_label = self.player_names.get(match.player2.player_id, f"Player({match.player2.player_id})") if match.player2 else "BYE"
            match_options.append(discord.SelectOption(label=f"{p1_label} vs {p2_label}", value=str(i)))

        self.match_select = discord.ui.StringSelect(
            placeholder="Wähle ein Match aus...",
            options=match_options,
            max_values=1,
            min_values=1
        )
        self.match_select.callback = self.on_match_selected
        self.add_item(self.match_select)

    async def on_match_selected(self, interaction: discord.Interaction):
        try:
            if not self.match_select.values:
                await interaction.respond("Bitte wähle ein Match aus.", ephemeral=True)
                return

            match_index = int(self.match_select.values[0])
            if match_index < 0 or match_index >= len(self.round.matches):
                await interaction.respond("Ungültiges Match ausgewählt.", ephemeral=True)
                return

            match = self.round.matches[match_index]
            if match.is_bye() or not match.player1 or not match.player2:
                await interaction.respond("Dieses Match kann nicht gesetzt werden.", ephemeral=True)
                return

            p1_name = self.player_names.get(match.player1.player_id, f"Player({match.player1.player_id})")
            p2_name = self.player_names.get(match.player2.player_id, f"Player({match.player2.player_id})")
            await interaction.response.send_modal(ReportMatchAdminModal(self.tournament, self.round, match, p1_name, p2_name))
        except Exception:
            link_log.exception("[admin-report] Failed to open admin result modal after match selection")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Beim Öffnen des Ergebnis-Dialogs ist ein Fehler aufgetreten.", ephemeral=True)
                else:
                    await interaction.respond("Beim Öffnen des Ergebnis-Dialogs ist ein Fehler aufgetreten.", ephemeral=True)
            except discord.NotFound:
                link_log.warning("[admin-report] Could not deliver error to user after match selection: interaction expired")
            except Exception:
                link_log.exception("[admin-report] Failed to deliver fallback error to user after match selection")

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

        if self.message:
            try:
                await self.message.edit(
                    content="Die Match-Auswahl ist abgelaufen. Bitte klicke erneut auf **Ergebnis setzen (Admin)**.",
                    view=self
                )
            except discord.NotFound:
                link_log.info("[admin-report] Match selection timeout message already gone; skipping update")
            except Exception:
                link_log.exception("[admin-report] Failed to update expired match selection view")


class ReportMatchAdminModal(discord.ui.DesignerModal):
    def __init__(self, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match, p1_name:str = None, p2_name:str = None):
        super().__init__(title="Report Match Result")
        self.tournament = tournament
        self.round = round
        self.match = match

        p1_display = p1_name if p1_name else f"Player({match.player1.player_id})"
        p2_display = p2_name if p2_name else f"Player({match.player2.player_id})"

        default_p1 = int(match.wins.get(match.player1, 0) or 0) if match.is_finished() else 0
        default_p2 = int(match.wins.get(match.player2, 0) or 0) if match.is_finished() else 0
        default_draws = int(match.wins.get("draws", 0) or 0) if match.is_finished() else 0

        score_options_p1 = [
            discord.SelectOption(label="0", value="0", default=default_p1 == 0),
            discord.SelectOption(label="1", value="1", default=default_p1 == 1),
            discord.SelectOption(label="2", value="2", default=default_p1 == 2)
        ]

        score_options_p2 = [
            discord.SelectOption(label="0", value="0", default=default_p2 == 0),
            discord.SelectOption(label="1", value="1", default=default_p2 == 1),
            discord.SelectOption(label="2", value="2", default=default_p2 == 2)
        ]

        score_options_draw = [
            discord.SelectOption(label="0", value="0", default=default_draws == 0),
            discord.SelectOption(label="1", value="1", default=default_draws == 1),
            discord.SelectOption(label="2", value="2", default=default_draws == 2)
        ]

        self.p1_score_select = discord.ui.StringSelect(
            placeholder="Siege",
            options=score_options_p1,
            min_values=1,
            max_values=1,
            required=True
        )
        self.add_item(discord.ui.Label(f"Spieler 1 Siege - {p1_display}", self.p1_score_select))

        self.p2_score_select = discord.ui.StringSelect(
            placeholder="Siege",
            options=score_options_p2,
            min_values=1,
            max_values=1,
            required=True
        )
        self.add_item(discord.ui.Label(f"Spieler 2 Siege - {p2_display}", self.p2_score_select))

        self.draw_score_select = discord.ui.StringSelect(
            placeholder="Unentschieden",
            options=score_options_draw,
            min_values=1,
            max_values=1,
            required=True
        )
        self.add_item(discord.ui.Label("Unentschieden", self.draw_score_select))

        self.message_label = discord.ui.Label("Nachricht an die Spieler")
        self.message_label.set_input_text(
            placeholder="Was ist der Grund dieses Resultat zu setzen/überscheiben?",
            required=True
        )
        self.add_item(self.message_label)

    async def callback(self, interaction: discord.Interaction):
        try:
            if not self.p1_score_select.values or not self.p2_score_select.values or not self.draw_score_select.values:
                await interaction.respond("Bitte wähle Siege und Unentschieden aus.", ephemeral=True)
                return

            score1 = int(self.p1_score_select.values[0])
            score2 = int(self.p2_score_select.values[0])
            draw_score = int(self.draw_score_select.values[0])
            self.match.set_result(score1, score2, draw_score)

            await interaction.response.defer()

            p1_member = await self.tournament.get_member(self.match.player1.player_id)
            p2_member = await self.tournament.get_member(self.match.player2.player_id)
            p1_mention = p1_member.mention if p1_member else f"<@{self.match.player1.player_id}>"
            p2_mention = p2_member.mention if p2_member else f"<@{self.match.player2.player_id}>"
            p1_name = p1_member.display_name if p1_member else f"Player({self.match.player1.player_id})"
            p2_name = p2_member.display_name if p2_member else f"Player({self.match.player2.player_id})"

            message_to_opponend = f"Nachricht von der Turnierorganisation:\n> {self.message_label.item.value}"  # type: ignore
            message_result = await interaction.followup.send(f"{p1_mention} {score1} - {score2} {p2_mention}{f' ({draw_score} Unentschieden)' if draw_score else ''}\n{message_to_opponend}", wait=True)

            if env.DEBUG:
                swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

            log_text = f"[admin-report] {p1_name} vs {p2_name} → {score1}-{score2}-{draw_score}"
            link_log.info(f"{log_text} in {message_result.jump_url}")
            await update_pairings(self.tournament, self.round)
            await update_standings(self.tournament, interaction)

        except ValueError as e:
            link_log.warning(f"[admin-report] Invalid score submission: {e}")
            if interaction.response.is_done():
                await interaction.followup.send(f"Ungültiges Match-Ergebnis: {e}", ephemeral=True)
            else:
                await interaction.respond(f"Ungültiges Match-Ergebnis: {e}", ephemeral=True)
        except Exception:
            link_log.exception("[admin-report] Unexpected error while submitting match result")
            if interaction.response.is_done():
                await interaction.followup.send("Beim Setzen des Ergebnisses ist ein Fehler aufgetreten.", ephemeral=True)
            else:
                await interaction.respond("Beim Setzen des Ergebnisses ist ein Fehler aufgetreten.", ephemeral=True)

class ReportMatchModal(discord.ui.DesignerModal):
    def __init__(self, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match, p1_name:str = None, p2_name:str = None):
        super().__init__(title="Report Match Result")
        self.tournament = tournament
        self.match = match
        self.round = round

        # Use provided names or fallback to player IDs
        p1_display = p1_name if p1_name else f"Player({match.player1.player_id})"
        p2_display = p2_name if p2_name else f"Player({match.player2.player_id})"

        default_p1 = int(match.wins.get(match.player1, 0) or 0) if match.is_finished() else 0
        default_p2 = int(match.wins.get(match.player2, 0) or 0) if match.is_finished() else 0
        default_draws = int(match.wins.get("draws", 0) or 0) if match.is_finished() else 0

        score_options_p1 = [
            discord.SelectOption(label="0", value="0", default=default_p1 == 0),
            discord.SelectOption(label="1", value="1", default=default_p1 == 1),
            discord.SelectOption(label="2", value="2", default=default_p1 == 2)
        ]

        score_options_p2 = [
            discord.SelectOption(label="0", value="0", default=default_p2 == 0),
            discord.SelectOption(label="1", value="1", default=default_p2 == 1),
            discord.SelectOption(label="2", value="2", default=default_p2 == 2)
        ]

        score_options_draw = [
            discord.SelectOption(label="0", value="0", default=default_draws == 0),
            discord.SelectOption(label="1", value="1", default=default_draws == 1),
            discord.SelectOption(label="2", value="2", default=default_draws == 2)
        ]

        self.p1_score_select = discord.ui.StringSelect(
            placeholder="Siege",
            options=score_options_p1,
            min_values=1,
            max_values=1,
            required=True
        )
        self.add_item(discord.ui.Label(f"Wins - {p1_display}", self.p1_score_select))

        self.p2_score_select = discord.ui.StringSelect(
            placeholder="Siege",
            options=score_options_p2,
            min_values=1,
            max_values=1,
            required=True
        )
        self.add_item(discord.ui.Label(f"Wins - {p2_display}", self.p2_score_select))

        self.draw_score_select = discord.ui.StringSelect(
            placeholder="Draws",
            options=score_options_draw,
            min_values=1,
            max_values=1,
            required=True
        )
        self.add_item(discord.ui.Label("Draws", self.draw_score_select))

        self.message_label = discord.ui.Label("Nachricht an deinen Gegner")
        self.message_label.set_input_text(
            placeholder="Schreib eine nette Nachricht, wenn du möchtest :)",
            min_length=0,
            required=False
        )
        self.add_item(self.message_label)

    async def callback(self, interaction: discord.Interaction):
        try:
            if not self.p1_score_select.values or not self.p2_score_select.values or not self.draw_score_select.values:
                await interaction.respond("Bitte wähle Siege und Unentschieden aus.", ephemeral=True)
                return

            score1 = int(self.p1_score_select.values[0])
            score2 = int(self.p2_score_select.values[0])
            draw_score = int(self.draw_score_select.values[0])

            self.match.set_result(score1, score2, draw_score)

            await interaction.response.defer()

            p1_member = await self.tournament.get_member(self.match.player1.player_id)
            p2_member = await self.tournament.get_member(self.match.player2.player_id)
            p1_mention = p1_member.mention if p1_member else f"<@{self.match.player1.player_id}>"
            p2_mention = p2_member.mention if p2_member else f"<@{self.match.player2.player_id}>"
            p1_name = p1_member.display_name if p1_member else f"Player({self.match.player1.player_id})"
            p2_name = p2_member.display_name if p2_member else f"Player({self.match.player2.player_id})"

            message_to_opponend = f"{interaction.user.mention} lässt ausrichten:\n> {self.message_label.item.value}" if self.message_label.item.value else "GGs!"  # type: ignore
            gg_message = await interaction.followup.send(f"{p1_mention} {score1} - {score2} {p2_mention}{f' ({draw_score} Unentschieden)' if draw_score else ''}\n{message_to_opponend}", wait=True)

            if env.DEBUG:
                swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

            log_text = f"[player-report] {p1_name} vs {p2_name} → {score1}-{score2}-{draw_score}"
            link_log.info(f"{log_text} in {gg_message.jump_url}")
            await update_pairings(self.tournament, self.round)
            await update_standings(self.tournament, interaction)

        except ValueError as e:
            link_log.warning(f"[player-report] Invalid score submission: {e}")
            if interaction.response.is_done():
                await interaction.followup.send(f"Ungültiges Match-Ergebnis: {e}", ephemeral=True)
            else:
                await interaction.respond(f"Ungültiges Match-Ergebnis: {e}", ephemeral=True)
        except Exception:
            link_log.exception("[player-report] Unexpected error while submitting match result")
            if interaction.response.is_done():
                await interaction.followup.send("Beim Übermitteln deines Ergebnisses ist ein Fehler aufgetreten.", ephemeral=True)
            else:
                await interaction.respond("Beim Übermitteln deines Ergebnisses ist ein Fehler aufgetreten.", ephemeral=True)


async def update_pairings(tournament:SpelltableTournament, round:swiss_mtg.Round):
    # make sure the pairings message is up to date
    
    pairings_message = None
    if round.message_id_pairings:
        pairings_message:discord.Message = await tournament.get_message(round.message_id_pairings)
    if pairings_message:
        link_log.info(f"updating pairings {pairings_message.jump_url}")
        view, embed, pairings_file = await _prepare_pairings_content(round, tournament)
        await pairings_message.edit(content="", embed=embed, attachments=[], file=pairings_file, view=view)
    else:
        pairings_message = await post_pairings(round, tournament)
        log.info(f"Issued new pairings message {pairings_message.jump_url} for tournament {tournament.title}.")
    # else:
    #     # issue new pairing message
    #     pairings_image = await tournament.pairings_to_image(round)
    #     pairings_file = discord.File(pairings_image, filename=pairings_image)
    #     channel = await tournament.guild.fetch_channel(tournament.channel_id)
    #     pairings_message = await channel.send(file=pairings_file, content="Neue Paarungen:")
    #     round.message_id_pairings = pairings_message.id

    await tournament.save_tournament()


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
        await update_pairings(self.tournament, current_round)

        # update standings, i.e. strike through dropped player
        await update_standings(self.tournament, interaction)

        message_pairings = await self.tournament.get_message(current_round.message_id_pairings)

        link_log.info(f"User {interaction.user.mention} dropped from tournament {message_pairings.jump_url}")
        await interaction.response.send_message("Du wurdest aus dem Turnier entfernt", ephemeral=True)


async def update_hall_of_fame(tournament: SpelltableTournament):
    """Update the hall of fame with tournament results."""
    try:
        hof = get_hall_of_fame()
        
        # Get the tournament ID
        message = await tournament.message
        tournament_id = f"{message.guild.id}/{message.channel.id}/{message.id}"
        
        # Sort players by standings
        swiss_mtg.sort_players_by_standings(tournament.swiss_tournament.players)
        
        # Collect results
        results = []
        placement = 1
        for player in tournament.swiss_tournament.players:
            if not player.dropped:
                match_record = player.get_match_results()
                results.append({
                    "user_id": player.player_id,
                    "placement": placement,
                    "match_record": match_record
                })
                placement += 1
        
        # Add tournament to hall of fame
        hof.add_tournament_result(
            tournament_id=tournament_id,
            tournament_title=tournament.title,
            guild_id=message.guild.id,
            completed_date=datetime.now().isoformat(),
            results=results
        )
        
        log.info(f"Hall of fame updated with tournament results: {tournament.title}")
    except Exception as e:
        log.error(f"Failed to update hall of fame for tournament {tournament.title}: {e}")


async def update_standings(tournament:SpelltableTournament, interaction: discord.Interaction):
    current_round = tournament.swiss_tournament.current_round()
    if not current_round.is_concluded():
        # as long as the current round has not concluded, don't post standings
        return
    message_standings = await tournament.get_message(current_round.message_id_standings)

    is_last_round = current_round.round_number >= tournament.swiss_tournament.rounds_count

    standings_image = await tournament.standings_to_image()
    standings_file = discord.File(standings_image, filename=standings_image)
    
    # Create embed for standings
    embed = discord.Embed(
        title=f"🏆 Platzierungen nach der {current_round.round_number}.{' (und letzten)' if is_last_round else ''} Runde",
        color=discord.Color.from_rgb(37, 88, 79)
    )
    embed.set_image(url=f"attachment://{standings_image}")

    if is_last_round:
        # letzte Runde
        view = await FinishTournamentView.create(tournament)
    else:
        view = await StartNextRoundView.create(current_round, tournament)

    if message_standings:
        await message_standings.edit(content=None, embed=embed, view=view, attachments=[], file=standings_file)
    else:
        async def do_the_thing():
            message_standings = await interaction.followup.send(embed=embed, view=view, file=standings_file)
            # dm organizer
            to = await tournament.organizer
            can_do = None
            if type(view) == FinishTournamentView:
                can_do = "das Turnier abschließen"
            elif type(view) == StartNextRoundView:
                can_do = "die nächste Runde starten"
            await to.send(f"🏆 Die Platzierungen für die {tournament.swiss_tournament.current_round().round_number}. Runde des Turniers `{tournament.title}` wurden gepostet.\nDu kannst {can_do}...\n{message_standings.jump_url}")
            current_round.message_id_standings = message_standings.id
        await use_custom_try("Platzierungen Senden", do_the_thing, tournament)

    await tournament.save_tournament()

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
                await update_standings(self.tournament, interaction)
                await update_pairings(self.tournament, self.tournament.swiss_tournament.current_round())
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

class CancelTournamentModal(discord.ui.Modal):
    def __init__(self, tournament:SpelltableTournament):
        super().__init__(title="Turnier abbrechen")
        self.tournament = tournament

        self.cancel_input = discord.ui.InputText(
            label="Tippe einen Grund ein",
            placeholder="z.B. Zu wenig Teilnehmer oder technische Probleme",
            required=True,
        )
        self.add_item(self.cancel_input)

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message("Das Turnier wird abgebrochen...", ephemeral=True)
        except (discord.errors.DiscordServerError, discord.errors.HTTPException) as e:
            log.warning(f"Failed to send initial response for tournament cancellation: {e}")
            # Continue with cancellation even if the response fails
        
        tourney_message = await self.tournament.message
        self.tournament.cancelled = self.cancel_input.value
        cancel_message = await tourney_message.channel.send(f"🛑 Das Turnier `{self.tournament.title}` wurde abgebrochen:\n> {self.cancel_input.value}")
        link_log.info(f"Turnier `{self.tournament.title}` {cancel_message.jump_url} wurde abgebrochen")
        
        try:
            await interaction.message.edit(view=None)
        except (discord.errors.DiscordServerError, discord.errors.HTTPException) as e:
            log.warning(f"Failed to edit message after tournament cancellation: {e}")

        await remove_tournament_views(self.tournament)
        
        await self.tournament.save_tournament()


async def remove_tournament_views(tournament: SpelltableTournament):
    """Remove all persistent component views from a tournament's messages."""
    tournament_message = await tournament.message
    if not tournament_message:
        return

    thread = tournament_message.channel
    if isinstance(thread, discord.Thread) and thread.archived:
        await thread.edit(archived=False)

    messages_to_clear: dict[int, discord.Message] = {tournament_message.id: tournament_message}

    if tournament.swiss_tournament and tournament.swiss_tournament.rounds:
        for round_data in tournament.swiss_tournament.rounds:
            for message_id in (round_data.message_id_pairings, round_data.message_id_standings):
                if not message_id or message_id in messages_to_clear:
                    continue
                try:
                    message = await thread.fetch_message(message_id)
                except discord.NotFound:
                    continue
                messages_to_clear[message_id] = message

    for message in messages_to_clear.values():
        try:
            await message.edit(view=None)
        except discord.NotFound:
            continue
        except (discord.errors.DiscordServerError, discord.errors.HTTPException) as e:
            log.warning(f"Failed to remove view from message {message.id} in tournament `{tournament.title}`: {e}")

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
                user_to_kick:discord.Member = await self.tournament.get_member(user_id)
            else:
                await interaction.respond(f"Kein User mit der ID `{user_id}` ist für das Turnier angemeldet.", ephemeral=True)
                return
        elif user_name:
            for participant_id in participant_ids:
                member = await self.tournament.get_member(participant_id)
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
        await self.tournament.save_tournament()

class ReportMatchView(discord.ui.View):
    def __init__(self):
        raise RuntimeError("Use 'await ReportMatchView.create(...)' instead")
    
    async def _init(self, round:swiss_mtg.Round, tournament:SpelltableTournament):
        super().__init__(timeout=None)
        self.round = round
        self.tournament = tournament
        self.player_match_map: dict[int, swiss_mtg.Match] = {}
        
        # Pre-fetch and cache all player display names for this round
        self.player_names = {}
        for match in self.round.matches:
            if match.player1 and match.player1.player_id not in self.player_names:
                member = await self.tournament.get_member(match.player1.player_id)
                self.player_names[match.player1.player_id] = member.display_name if member else f"Player({match.player1.player_id})"
                self.player_match_map[match.player1.player_id] = match
            if match.player2 and match.player2.player_id not in self.player_names:
                member = await self.tournament.get_member(match.player2.player_id)
                self.player_names[match.player2.player_id] = member.display_name if member else f"Player({match.player2.player_id})"
                self.player_match_map[match.player2.player_id] = match

    @classmethod
    async def create(cls, round:swiss_mtg.Round, tournament:SpelltableTournament):
        instance = object.__new__(cls)
        await instance._init(round, tournament)
        return instance
    
    async def simulate_on_not_playing(self, tournament:SpelltableTournament, round:swiss_mtg.Round, interaction:discord.Interaction):
        if env.DEBUG:
            swiss_mtg.simulate_remaining_matches(tournament.swiss_tournament)

            await update_pairings(tournament, round)
            await update_standings(tournament, interaction)

    @discord.ui.button(label="Ergebnis übermitteln", style=discord.ButtonStyle.primary, emoji="⚔️", row=0)
    async def report_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            the_match = self.player_match_map.get(interaction.user.id)
            if not the_match:
                await interaction.respond("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
                
                if env.DEBUG:
                    await self.simulate_on_not_playing(self.tournament, self.round, interaction)
                return
            
            if the_match.is_bye():
                await interaction.respond("Du hast diese Runde ein Bye", ephemeral=True)

                if env.DEBUG:
                    await self.simulate_on_not_playing(self.tournament, self.round, interaction)
                return
            
            if not the_match.player1 or not the_match.player2:
                await interaction.respond("Dieses Match kann nicht gemeldet werden.", ephemeral=True)
                return

            p1_name = self.player_names.get(the_match.player1.player_id, f"Player({the_match.player1.player_id})")
            p2_name = self.player_names.get(the_match.player2.player_id, f"Player({the_match.player2.player_id})")
            await interaction.response.send_modal(ReportMatchModal(self.tournament, self.round, the_match, p1_name, p2_name))
        except Exception:
            link_log.exception("[player-report] Failed to open report modal")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Beim Öffnen des Ergebnis-Dialogs ist ein Fehler aufgetreten.", ephemeral=True)
                else:
                    await interaction.respond("Beim Öffnen des Ergebnis-Dialogs ist ein Fehler aufgetreten.", ephemeral=True)
            except discord.NotFound:
                link_log.warning("[player-report] Could not deliver error to user: interaction expired")
            except Exception:
                link_log.exception("[player-report] Failed to deliver fallback error to user")

    @discord.ui.button(label="Deck einreichen", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def submit_deck_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        player = self.tournament.swiss_tournament.player_by_id(interaction.user.id)
        if not player:
            await interaction.response.send_message("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
            return

        try:
            await interaction.response.send_modal(SubmitDeckModal(self.tournament, interaction.user, player))
        except discord.errors.NotFound:
            try:
                await interaction.followup.send("Die Anfrage hat zu lange gedauert. Bitte versuche es erneut.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Ergebnis setzen (Admin)", style=discord.ButtonStyle.secondary, emoji="🔏", row=1)
    async def admin_report_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            official_tournament_roles = ["Moderator", "Admin"]
            if interaction.user.id != self.tournament.organizer_id and not any(role.name in official_tournament_roles for role in interaction.user.roles):
                await interaction.respond("Nur der Turnier-Organisator oder ein Moderator darf dies tun.", ephemeral=True)
                return

            player_names = getattr(self, 'player_names', {})
            has_non_bye_matches = any(not match.is_bye() for match in self.round.matches)
            if not has_non_bye_matches:
                await interaction.respond("In dieser Runde gibt es keine setzbaren Matches.", ephemeral=True)
                return

            selection_view = AdminMatchSelectView(self.tournament, self.round, player_names)
            await interaction.response.defer(ephemeral=True)
            selection_message = await interaction.followup.send(
                "Wähle das Match aus, das du bearbeiten möchtest:",
                view=selection_view,
                ephemeral=True,
                wait=True
            )
            if selection_message and isinstance(selection_message, discord.Message):
                selection_view.message = selection_message
        except Exception:
            link_log.exception("[admin-report] Failed to open admin report modal")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Beim Öffnen des Admin-Ergebnis-Dialogs ist ein Fehler aufgetreten.", ephemeral=True)
                else:
                    await interaction.respond("Beim Öffnen des Admin-Ergebnis-Dialogs ist ein Fehler aufgetreten.", ephemeral=True)
            except discord.NotFound:
                link_log.warning("[admin-report] Could not deliver error to user: interaction expired")
            except Exception:
                link_log.exception("[admin-report] Failed to deliver fallback error to user")

    @discord.ui.button(label="Turnier verlassen", style=discord.ButtonStyle.danger, emoji="🚪")
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
            await interaction.respond("Nur der Turnier-Organisator oder ein Moderator darf dies tun.!", ephemeral=True)
            return
        await interaction.response.send_modal(KickPlayerModal(self.tournament))

    @discord.ui.button(label="Turnier abbrechen", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Nur der Turnier-Organisator oder ein Moderator darf dies tun!", ephemeral=True)
            return
        await interaction.response.send_modal(CancelTournamentModal(self.tournament))


async def _prepare_pairings_content(round: swiss_mtg.Round, tournament: SpelltableTournament):
    """Helper function to prepare all pairings content (view, embed, image, file)."""
    view = await ReportMatchView.create(round, tournament)
    pairings_image = await tournament.pairings_to_image(round)
    pairings_file = discord.File(pairings_image, filename=pairings_image)
    pairings = await tournament.get_pairings(round)

    total_rounds = tournament.swiss_tournament.rounds_count
    is_last_round = round.round_number >= total_rounds
    title = (
        f"⚔️ Paarungen für die {round.round_number}. und letzte Runde"
        if is_last_round
        else f"⚔️ Paarungen für die {round.round_number}. Runde (von {total_rounds})"
    )
    embed = discord.Embed(
        title=title,
        description=pairings[:4000] if len(pairings) <= 4000 else pairings[:3950] + "\n\n_(Siehe Bild für vollständige Paarungen)_",
        color=discord.Color.from_rgb(37, 88, 79)
    )
    
    return view, embed, pairings_file


async def post_pairings(round:swiss_mtg.Round, tournament:SpelltableTournament, previous_round:swiss_mtg.Round=None, interaction:discord.Interaction=None):
    view, embed, pairings_file = await _prepare_pairings_content(round, tournament)
    
    if interaction:
        new_pairings_message:discord.Message = await interaction.followup.send(content="", embed=embed, file=pairings_file, view=view)
    else:
        message_to_reply_to = None
        if round.round_number == 1:
            # first round, post in tournament channel
            message_to_reply_to = await tournament.message
        else:
            if not previous_round:
                previous_round = tournament.swiss_tournament.rounds[round.round_number - 2]
            message_to_reply_to = await tournament.get_message(previous_round.message_id_pairings)
        new_pairings_message:discord.Message = await message_to_reply_to.reply(content="", embed=embed, file=pairings_file, view=view)
    
    # Try to pin/unpin messages, but continue if permissions are missing
    try:
        if previous_round:
            previous_pairings_message = await new_pairings_message.channel.fetch_message(previous_round.message_id_pairings)
            try:
                await previous_pairings_message.unpin()
            except discord.errors.Forbidden:
                link_log.warning(f"Missing permission to unpin message {previous_round.message_id_pairings}")
        await new_pairings_message.pin()
    except discord.errors.Forbidden as e:
        link_log.warning(f"Missing permission to pin message in channel {new_pairings_message.channel.id}: {e}")
        if interaction:
            await interaction.followup.send("⚠️ Hinweis: Ich konnte die Paarungen nicht anpinnen. Bitte gib mir 'Nachrichten verwalten' Berechtigung.", ephemeral=True)
    except Exception as e:
        link_log.error(f"Unexpected error while pinning message: {e}")
    
    round.message_id_pairings = new_pairings_message.id
    return new_pairings_message

async def next_round(tournament:SpelltableTournament, interaction:discord.Interaction):
    async def do_the_thing():
        previous_round = tournament.swiss_tournament.current_round()
        round = tournament.swiss_tournament.pair_players()
        try:
            new_pairings_message:discord.Message = await post_pairings(round, tournament, previous_round, interaction)
        except discord.errors.Forbidden as e:
            log.error(f"Missing permissions to send pairings message: {e}")
            error_msg = "❌ Fehlende Berechtigungen zum Senden der Paarungen.\n"
            if e.code == 50013:
                error_msg += "Der Bot benötigt folgende Berechtigungen:\n• Nachrichten senden\n• Dateien anhängen\n• Nachrichten verwalten (zum Anpinnen)"
            await interaction.followup.send(error_msg, ephemeral=True)
            previous_standings_message = await tournament.get_message(previous_round.message_id_standings)
            standings_image = await tournament.standings_to_image(previous_round)
            standings_file = discord.File(standings_image, filename=standings_image)
            await previous_standings_message.edit(attachments=[], file=standings_file, view=await StartNextRoundView.create(previous_round, tournament))
            return
        except discord.errors.HTTPException as e:
            log.error(f"Failed to send pairings message: {e}")
            await interaction.followup.send(f"Fehler beim Senden der Paarungen: {e}\nBitte versuche es später erneut.", ephemeral=True)
            previous_standings_message = await tournament.get_message(previous_round.message_id_standings)
            standings_image = await tournament.standings_to_image(previous_round)
            standings_file = discord.File(standings_image, filename=standings_image)
            await previous_standings_message.edit(attachments=[], file=standings_file, view=await StartNextRoundView.create(previous_round, tournament))
            return

        if not isinstance(new_pairings_message, discord.Message):
            raise TypeError("Expected a discord.Message, but got None or an invalid type.")
        await tournament.save_tournament()

        # message players direcly
        for match in round.matches:
            try:
                user1 = await tournament.get_member(match.player1.player_id)
                if type(user1) != discord.Member:
                    user1 = None
            except discord.NotFound:
                user1 = None
            if match.is_bye():
                # bye
                if user1:
                    try:
                        await user1.send(f"Du hast ein BYE in der {round.round_number}. Runde im Turnier `{tournament.title}`: {new_pairings_message.jump_url}")
                    except discord.Forbidden:
                        log.error(f"Could not send message to {user1.display_name} ({user1.id})")
            else:
                try:
                    user2 = await tournament.get_member(match.player2.player_id)
                    if type(user2) != discord.Member:
                        user2 = None
                except discord.NotFound:
                    user2 = None
                if user1:
                    try:
                        p2_member = await tournament.get_member(match.player2.player_id)
                        await user1.send(f"🤺 Du spielst gegen <@{match.player2.player_id}> ({p2_member.display_name}) in der {round.round_number}. Runde im Turnier `{tournament.title}`: {new_pairings_message.jump_url}")
                    except discord.Forbidden as forbidden:
                        log.error(f"Could not send message to {user1.display_name} ({user1.id})")
                if user2:
                    try:
                        p1_member = await tournament.get_member(match.player1.player_id)
                        await user2.send(f"🤺 Du spielst gegen <@{match.player1.player_id}> ({p1_member.display_name}) in der {round.round_number}. Runde im Turnier `{tournament.title}`: {new_pairings_message.jump_url}")
                    except discord.Forbidden as forbidden:
                        log.error(f"Could not send message to {user2.display_name} ({user2.id})")

    await use_custom_try("Nächste Runde Erstellen", do_the_thing, tournament)


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
            # check if the user even is allowed to start the next round
            # only Organizer
            if interaction.user.id != self.tournament.organizer_id:
                await interaction.respond("Nur der Turnier-Organisator darf dies tun.", ephemeral=True)
                return

            # Acknowledge interaction immediately to avoid timeout during prep work
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.NotFound:
                link_log.warning("[next-round] Interaction expired before defer; aborting next-round start")
                return
            
            # Get or create a lock for this tournament
            tournament_id = await self.tournament.get_id()
            if tournament_id not in _tournament_round_locks:
                _tournament_round_locks[tournament_id] = asyncio.Lock()
            
            lock = _tournament_round_locks[tournament_id]
            
            # Try to acquire the lock without blocking
            if lock.locked():
                await interaction.followup.send(
                    "Die nächste Runde wird bereits gestartet. Bitte warte einen Moment.",
                    ephemeral=True
                )
                return
            
            async with lock:
                # Double-check that the round hasn't already been started
                current_round = self.tournament.swiss_tournament.current_round()
                if current_round and current_round.round_number > self.previous_round.round_number:
                    await interaction.followup.send(
                        "Die nächste Runde wurde bereits gestartet.",
                        ephemeral=True
                    )
                    return

                # Immediately remove the pairings view to prevent further button presses.
                previous_pairings_message = await self.tournament.get_message(self.previous_round.message_id_pairings)
                if previous_pairings_message:
                    await previous_pairings_message.edit(view=None)

                # update the previous round's messages
                previous_standings_message = await self.tournament.get_message(self.previous_round.message_id_standings)

                # Immediately remove the standings view to prevent repeated clicks.
                if previous_standings_message:
                    await previous_standings_message.edit(view=None)

                standings_image = await self.tournament.standings_to_image(self.previous_round)
                standings_file = discord.File(standings_image, filename=standings_image)
                await previous_standings_message.edit(attachments=[], file=standings_file, view=None) #content=previous_pairings_message.content, view=None, attachments=previous_pairings_message.attachments)

                pairings_image = await self.tournament.pairings_to_image(self.previous_round)
                pairings_file = discord.File(pairings_image, filename=pairings_image)
                if previous_pairings_message:
                    await previous_pairings_message.edit(attachments=[], file=pairings_file, view=None)

                try:
                    await next_round(self.tournament, interaction)
                except Exception as e:
                    await previous_standings_message.edit(attachments=[], file=standings_file, view=await StartNextRoundView.create(self.previous_round, self.tournament))

        await use_custom_try("Nächste Runde Erstellen", do_the_thing, self.tournament)
