import discord
import logging
from modules import swiss_mtg
from modules.spelltable.tournament_model import SpelltableTournament, use_custom_try, ParticipationState
from modules import env
from ezcord import log

link_log = logging.getLogger("link_logger")

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
        await interaction.response.send_modal(SubmitDeckModal(self.tournament, interaction.user))

class SubmitDeckModal(discord.ui.DesignerModal):
    def __init__(self, tournament: SpelltableTournament, player: discord.User):
        super().__init__(title="Deck einreichen")
        self.tournament = tournament
        self.player = player

        tournament_player:swiss_mtg.Player = tournament.swiss_tournament.player_by_id(self.player.id)
        deck:swiss_mtg.Deck = tournament_player.deck if tournament_player and tournament_player.deck else None

        self.decklist_input = discord.ui.InputText(
            placeholder="https://moxfield.com/decks/...",
            value=deck.link if deck else "",
            required=False,
            min_length=0
        )
        # self.archetype_input = discord.ui.Select(
        #     placeholder="Wähle einen Archetyp...",
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
        self.deck_name = discord.ui.InputText(
            placeholder="Mono-Red Aggro, Jund Wildfire, ...",
            required=False,
            min_length=0
        )
        # self.add_item(discord.ui.Label("Archetyp", self.archetype_input, description="Wähle den Archetyp deines Decks aus."))
        self.add_item(discord.ui.Label("Archetyp", self.deck_name, description="Wie sich das Deck nennt, das du gespielt hast"))
        self.add_item(discord.ui.Label("Deckliste", self.decklist_input, description="Ein Link zu deiner Deckliste, z.B. Moxfield oder mtggoldfish."))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        decklist = self.decklist_input.value.strip()
        # Save the decklist to the tournament/player (implement as needed)
        player:swiss_mtg.Player = self.tournament.swiss_tournament.player_by_id(self.player.id)
        if not player:
            await interaction.followup.send("Du bist kein Teilnehmer in diesem Turnier.", ephemeral=True)
            return
        archetype = None
        # archetype = self.archetype_input.values[0] if self.archetype_input.values and len(self.archetype_input.values) == 1 else None
        archetype = self.deck_name.value if self.deck_name.value else archetype
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
            submit_deck_view = await SubmitDeckView.create(self.tournament)
            if standings_message:
                await standings_message.edit(content="🏆Finales Ergebnis", view=submit_deck_view)


            await interaction.response.send_message(content, ephemeral=False)
            link_log.info(f"Turnier abgeschlossen: `{self.tournament.title}`, Gewinner: <@{winner.player_id}>")
            self.tournament.swiss_tournament.winner = winner
            await self.tournament.save_tournament()

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

class ReportMatchAdminModal(discord.ui.DesignerModal):
    def __init__(self):
        raise RuntimeError("Use 'await ReportMatchAdminModal.create(...)' instead")

    async def _init(self, tournament:SpelltableTournament, round:swiss_mtg.Round):
        super().__init__(title="Report Match Result")
        self.tournament = tournament
        self.round = round

        # self.round.matches.sort(key=lambda m: (m.player1.player_id if m.player1 else 0, m.player2.player_id if m.player2 else 0))
        match_options = []
        for i,match in enumerate(self.round.matches):
            if match.is_bye():
                continue
            label = f"{match.player1.name if match.player1 else 'BYE'} vs {match.player2.name if match.player2 else 'BYE'}"
            match_options.append(discord.SelectOption(label=label, value=str(i)))
        
        self.match = discord.ui.StringSelect(
            placeholder="Wähle ein Match aus...",
            options=match_options,
            max_values=1,
            min_values=1,
            required=True
        )

        self.p1_score = discord.ui.InputText(
            placeholder="0,1,2",
            required=True
        )
        self.p2_score = discord.ui.InputText(
            placeholder="0,1,2",
            required=True
        )
        self.draw_score = discord.ui.InputText(
            placeholder="0,1,2",
            required=False,
            value="0",
            min_length=0
        )

        self.message = discord.ui.InputText(
            placeholder="Was ist der Grund dieses Resultat zu setzen/überscheiben?",
            required=True
        )

        self.add_item(discord.ui.Label(f"Welches Match möchtest du setzen?", self.match))
        self.add_item(discord.ui.Label("Spieler 1 Siege", self.p1_score))
        self.add_item(discord.ui.Label("Spieler 2 Siege", self.p2_score))
        self.add_item(discord.ui.Label("Unentschieden", self.draw_score))
        self.add_item(discord.ui.Label("Nachricht an die Spieler", self.message))

    @classmethod
    async def create(cls, tournament:SpelltableTournament, round:swiss_mtg.Round):
        instance = object.__new__(cls)
        await instance._init(tournament, round)
        return instance

    async def callback(self, interaction: discord.Interaction):
        score1 = int(self.p1_score.value) if self.p1_score.value else 0
        score2 = int(self.p2_score.value) if self.p2_score.value else 0
        draw_score = int(self.draw_score.value) if self.draw_score.value else 0
        match_index = int(self.match.values[0])
        match = self.round.matches[match_index]
        try:
            match.set_result(score1, score2, draw_score)
        except ValueError as e:
            await interaction.respond(f"Das Match Resultat {score1}-{score2}-{draw_score} ist invalide: {e}", ephemeral=True)
            return
        
        await interaction.response.defer()
        message_to_opponend = f"Nachricht von der Turnierorganisation:\n> {self.message.value}"
        message_result = await interaction.followup.send(f"<@{match.player1.player_id}> {score1} - {score2} <@{match.player2.player_id}>{f' ({draw_score} Unentschieden)' if draw_score else ''}\n{message_to_opponend}", wait=True)

        if env.DEBUG:
            swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

        log_text = f"Match result submitted: <@{match.player1.player_id}> vs <@{match.player2.player_id}> → {score1}-{score2}-{draw_score}"
        link_log.info(f"{log_text} in {message_result.jump_url}")
        await update_pairings(self.tournament, self.round)
        await update_standings(self.tournament, interaction)

class ReportMatchModal(discord.ui.Modal):
    def __init__(self):
        raise RuntimeError("Use 'await ReportMatchModal.create(...)' instead")

    async def _init(self, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match):
        super().__init__(title="Report Match Result")
        self.tournament = tournament
        self.match = match
        self.round = round

        self.player1_user = await tournament.get_member(match.player1.player_id)
        self.player2_user = await tournament.get_member(match.player2.player_id)
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
            value=match.wins["draws"],
            min_length=0
        )
        self.add_item(self.draw_score)

        self.personalized_message = discord.ui.InputText(
            label=f"Nachricht an deinen Gegner",
            placeholder="Schreib eine nette Nachricht, wenn du möchtest :)",
            required=False,
            min_length=0
        )
        self.add_item(self.personalized_message)

    @classmethod
    async def create(cls, tournament:SpelltableTournament, round:swiss_mtg.Round, match:swiss_mtg.Match):
        instance = object.__new__(cls)
        await instance._init(tournament, round, match)
        return instance

    async def callback(self, interaction: discord.Interaction):
        score1 = int(self.p1_score.value) if self.p1_score.value else 0
        score2 = int(self.p2_score.value) if self.p2_score.value else 0
        draw_score = int(self.draw_score.value) if self.draw_score.value else 0
        try:
            self.match.set_result(score1, score2, draw_score)
        except ValueError as e:
            await interaction.respond(f"Dein Match Resultat {score1}-{score2}-{draw_score} ist invalide: {e}", ephemeral=True)
            return
        
        await interaction.response.defer()
        message_to_opponend = f"{interaction.user.mention} lässt ausrichten:\n> {self.personalized_message.value}" if self.personalized_message.value else "GGs!"
        gg_message = await interaction.followup.send(f"{self.player1_user.mention} {score1} - {score2} {self.player2_user.mention}{f' ({draw_score} Unentschieden)' if draw_score else ''}\n{message_to_opponend}", wait=True)

        if env.DEBUG:
            swiss_mtg.simulate_remaining_matches(self.tournament.swiss_tournament)

        log_text = f"Match result submitted: {self.player1_user.mention} vs {self.player2_user.mention} → {score1}-{score2}-{draw_score}"
        link_log.info(f"{log_text} in {gg_message.jump_url}")
        await update_pairings(self.tournament, self.round)
        await update_standings(self.tournament, interaction)


async def update_pairings(tournament:SpelltableTournament, round:swiss_mtg.Round):
    # make sure the pairings message is up to date
    
    view = await ReportMatchView.create(round, tournament)
    pairings_message = None
    if round.message_id_pairings:
        pairings_message:discord.Message = await tournament.get_message(round.message_id_pairings)
    if pairings_message:
        link_log.info(f"updating pairings {pairings_message.jump_url}")
        pairings_image = await tournament.pairings_to_image(round)
        pairings_file = discord.File(pairings_image, filename=pairings_image)
        await pairings_message.edit(file=pairings_file, attachments=[], content=pairings_message.content or "Kein Inhalt verfügbar.", view=view)
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


async def update_standings(tournament:SpelltableTournament, interaction: discord.Interaction):
    current_round = tournament.swiss_tournament.current_round()
    if not current_round.is_concluded():
        # as long as the current round has not concluded, don't post standings
        return
    message_standings = await tournament.get_message(current_round.message_id_standings)

    is_last_round = current_round.round_number >= tournament.swiss_tournament.rounds_count

    content = f"Platzierungen nach der {current_round.round_number}.{' (und letzten)' if is_last_round else ''} Runde"
    standings_image = await tournament.standings_to_image()
    standings_file = discord.File(standings_image, filename=standings_image)

    if is_last_round:
        # letzte Runde
        view = await FinishTournamentView.create(tournament)
    else:
        view = await StartNextRoundView.create(current_round, tournament)

    if message_standings:
        await message_standings.edit(content=content, view=view, attachments=[], file=standings_file)
    else:
        async def do_the_thing():
            message_standings = await interaction.followup.send(content=content, view=view, file=standings_file)
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
        await interaction.response.send_message("Das Turnier wird abgebrochen...", ephemeral=True)
        tourney_message = await self.tournament.message
        self.tournament.cancelled = self.cancel_input.value
        cancel_message = await tourney_message.channel.send(f"🛑 Das Turnier `{self.tournament.title}` wurde abgebrochen:\n> {self.cancel_input.value}")
        link_log.info(f"Turnier `{self.tournament.title}` {cancel_message.jump_url} wurde abgebrochen")
        await interaction.message.edit(view=None)
        await self.tournament.save_tournament()

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

    @discord.ui.button(label="Ergebnis übermitteln", style=discord.ButtonStyle.primary, emoji="⚔️")
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
            
            if env.DEBUG:
                await self.simulate_on_not_playing(self.tournament, self.round, interaction)
            return
        
        if the_match.is_bye():
            await interaction.respond("Du hast diese Runde ein Bye", ephemeral=True)

            if env.DEBUG:
                await self.simulate_on_not_playing(self.tournament, self.round, interaction)
            return
        
        await interaction.response.send_modal(await ReportMatchModal.create(self.tournament, self.round, the_match))

    @discord.ui.button(label="Ergebnis setzen (Admin)", style=discord.ButtonStyle.secondary, emoji="🔏")
    async def admin_report_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        official_tournament_roles = ["Moderator", "Admin"]
        if interaction.user.id != self.tournament.organizer_id and not any(role.name in official_tournament_roles for role in interaction.user.roles):
            await interaction.respond("Nur der Turnier-Organisator oder ein Moderator darf dies tun.", ephemeral=True)
            return
        await interaction.response.send_modal(await ReportMatchAdminModal.create(self.tournament, self.round))

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

async def post_pairings(round:swiss_mtg.Round, tournament:SpelltableTournament, previous_round:swiss_mtg.Round=None, interaction:discord.Interaction=None):
    reportMatchView = await ReportMatchView.create(round, tournament)
    pairings_image = await tournament.pairings_to_image()
    pairings_file = discord.File(pairings_image, filename=pairings_image)
    pairings = await tournament.get_pairings()
    if interaction:
        new_pairings_message:discord.Message = await interaction.followup.send(content=f"Paarungen für die {round.round_number}. Runde:\n\n{pairings}", file=pairings_file, view=reportMatchView)
    else:
        message_to_reply_to = None
        if round.round_number == 1:
            # first round, post in tournament channel
            message_to_reply_to = await tournament.message
        else:
            if not previous_round:
                previous_round = tournament.swiss_tournament.rounds[round.round_number - 2]
            message_to_reply_to = await tournament.get_message(previous_round.message_id_pairings)
        new_pairings_message:discord.Message = await message_to_reply_to.reply(content=f"Paarungen für die {round.round_number}. Runde:\n\n{pairings}", file=pairings_file, view=reportMatchView)
    
    if previous_round:
        previous_pairings_message = await new_pairings_message.channel.fetch_message(previous_round.message_id_pairings)
        await previous_pairings_message.unpin()
    await new_pairings_message.pin()
    round.message_id_pairings = new_pairings_message.id
    return new_pairings_message

async def next_round(tournament:SpelltableTournament, interaction:discord.Interaction):
    async def do_the_thing():
        previous_round = tournament.swiss_tournament.current_round()
        round = tournament.swiss_tournament.pair_players()
        try:
            new_pairings_message:discord.Message = await post_pairings(round, tournament, previous_round, interaction)
        except discord.errors.HTTPException as e:
            log.error(f"Failed to send pairings message: {e}")
            await interaction.followup.send("Fehler beim Senden der Paarungen. Bitte versuche es später erneut.", ephemeral=True)
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
            # only Moderator or Organizer
            if interaction.user.id != self.tournament.organizer_id and not any(role.name == "Moderator" for role in interaction.user.roles):
                await interaction.respond("Nur der Turn Organisator darf dies tun.", ephemeral=True)
                return
            await interaction.response.defer()

            # update the previous round's messages
            previous_standings_message = await self.tournament.get_message(self.previous_round.message_id_standings)
            previous_pairings_message = await self.tournament.get_message(self.previous_round.message_id_pairings)

            standings_image = await self.tournament.standings_to_image(self.previous_round)
            standings_file = discord.File(standings_image, filename=standings_image)
            await previous_standings_message.edit(attachments=[], file=standings_file, view=None) #content=previous_pairings_message.content, view=None, attachments=previous_pairings_message.attachments)

            pairings_image = await self.tournament.pairings_to_image(self.previous_round)
            pairings_file = discord.File(pairings_image, filename=pairings_image)
            await previous_pairings_message.edit(attachments=[], file=pairings_file, view=None)

            try:
                await next_round(self.tournament, interaction)
            except Exception as e:
                await previous_standings_message.edit(attachments=[], file=standings_file, view=await StartNextRoundView.create(self.previous_round, self.tournament))

        await use_custom_try("Nächste Runde Erstellen", do_the_thing, self.tournament)
