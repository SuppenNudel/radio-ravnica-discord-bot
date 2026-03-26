"""
Hall of Fame Discord Cog - Provides commands to view tournament winners and participant standings.
"""

import discord
from ezcord import Cog, log
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Option
from modules.hall_of_fame import get_hall_of_fame, HallOfFame
from modules.spelltable.tournament_model import active_tournaments, ParticipationState
from modules import env

class HallOfFameCog(Cog):
    """Commands for viewing the hall of fame."""
    
    def __init__(self, bot: Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.info(self.__class__.__name__ + " is ready")

    @slash_command(description="Zeige die Hall of Fame der Turniersieger und Top-Spieler")
    async def hall_of_fame(
        self,
        ctx: ApplicationContext,
        sort_by: Option(
            str,
            description="Sortierungskriterium für die Rangliste",
            choices=["Tournament Winners", "Match Wins", "Match Winrate", "Tournament Participations", "Top 3 Finishes", "Top 8 Finishes"],
            default="Tournament Winners"
        ) = "Tournament Winners"
    ):
        """Display the hall of fame with various sorting options."""
        await ctx.defer(ephemeral=True)
        
        hof = get_hall_of_fame()
        
        # Map display names to sort keys
        sort_map = {
            "Tournament Winners": "winners",
            "Match Wins": "match_wins",
            "Match Winrate": "match_winrate",
            "Tournament Participations": "tournaments",
            "Top 3 Finishes": "top3s",
            "Top 8 Finishes": "top8s"
        }
        
        sort_key = sort_map.get(sort_by, "winners")
        top_participants = hof.get_top_participants(limit=20, sort_by=sort_key)
        
        if not top_participants:
            await ctx.followup.send("Die Hall of Fame ist noch leer. Beende ein Turnier, um sie zu füllen!", ephemeral=True)
            return
        
        # Build the embed
        embed = discord.Embed(
            title="🏆 Hall of Fame - Turnier-Rangliste",
            color=discord.Color.gold()
        )
        embed.set_footer(text="Sortierungskriterium: " + sort_by)
        
        # Build ranking display
        ranking_text = ""
        for idx, participant in enumerate(top_participants, 1):
            medal = self._get_medal(idx)
            match_record = participant.get_match_record()
            match_wr = f"{participant.get_match_win_percentage()*100:.1f}%"
            
            # Build stat line based on sort criteria
            if sort_key == "winners":
                stat_line = f"🥇 **{participant.tournament_winners}** Siege | {match_record} ({match_wr})"
            elif sort_key == "match_wins":
                stat_line = f"⚔️ **{participant.total_match_wins}** Matches | {participant.tournament_winners} Turniersiege"
            elif sort_key == "match_winrate":
                stat_line = f"🎯 **{match_wr}** Winrate | {match_record} ({participant.total_match_wins} Siege)"
            elif sort_key == "tournaments":
                stat_line = f"🎮 **{participant.tournament_participations}** Turniere | {participant.tournament_winners} Siege"
            elif sort_key == "top3s":
                stat_line = f"🥈 **{participant.tournament_top3s}** Top 3 | {participant.tournament_top8s} Top 8"
            elif sort_key == "top8s":
                stat_line = f"🥉 **{participant.tournament_top8s}** Top 8 | {participant.tournament_top3s + participant.tournament_winners} Top 3+"
            else:
                stat_line = f"{participant.tournament_winners} Siege | {participant.tournament_top3s} Top 3"
            
            ranking_text += f"{medal} {participant.username}\n{stat_line}\n\n"
        
        embed.description = ranking_text
        
        # Add summary stats
        total_participants = len(hof.participants)
        total_tournaments = len(hof.tournaments)
        embed.add_field(
            name="📊 Statistiken",
            value=f"**{total_participants}** Spieler\n**{total_tournaments}** Turniere abgeschlossen",
            inline=True
        )
        
        # Add info about most dominant stats
        if hof.participants:
            most_wins = max(hof.participants.values(), key=lambda x: x.total_match_wins)
            best_wr = max(hof.participants.values(), key=lambda x: x.get_match_win_percentage() if x.total_match_wins + x.total_match_losses + x.total_draws > 0 else 0)
            most_tournaments = max(hof.participants.values(), key=lambda x: x.tournament_participations)
            
            embed.add_field(
                name="🎪 Highlights",
                value=f"**Meiste Matchsiege:** {most_wins.username} ({most_wins.total_match_wins})\n"
                      f"**Beste Winrate:** {best_wr.username} ({best_wr.get_match_win_percentage()*100:.1f}%)\n"
                      f"**Aktivster Spieler:** {most_tournaments.username} ({most_tournaments.tournament_participations} Turniere)",
                inline=False
            )
        
        await ctx.followup.send(embed=embed, ephemeral=True)
    
    @slash_command(description="Zeige deine persönliche Statistik in der Hall of Fame")
    async def my_stats(self, ctx: ApplicationContext):
        """Display personal tournament statistics."""
        await ctx.defer(ephemeral=True)
        
        hof = get_hall_of_fame()
        stats = hof.get_user_stats(ctx.author.id)
        
        if not stats:
            embed = discord.Embed(
                title="📊 Deine Statistik",
                description=f"Hallo {ctx.author.mention}! Du bist noch nicht in der Hall of Fame.\n\n"
                           "Nimm an einem Turnier teil, um deine Statistiken zu sammeln! ⚔️",
                color=discord.Color.blue()
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return
        
        embed = discord.Embed(
            title="📊 Deine Statistik",
            color=discord.Color.blue()
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
        
        # Match statistics
        embed.add_field(
            name="⚔️ Match Statistik",
            value=f"Record: **{stats['match_record']}**\n"
                  f"Winrate: **{stats['match_winrate']}**",
            inline=True
        )
        
        # Tournament statistics
        embed.add_field(
            name="🏆 Turnier Statistik",
            value=f"Teilnahmen: **{stats['tournament_participations']}**\n"
                  f"Siege: **{stats['tournament_winners']}**\n"
                  f"Top 3: **{stats['tournament_top3s']}**\n"
                  f"Top 8: **{stats['tournament_top8s']}**",
            inline=True
        )
        
        # Recent tournament placements (concluded only — must have results)
        concluded_placements = [p for p in stats['tournament_placements'] if p.get('standings_url') or p.get('match_record')]
        if concluded_placements:
            placements_text = ""
            shown = 0
            for placement in concluded_placements[:5]:
                medal = self._get_medal(placement['placement'])
                title = placement['tournament']
                url = placement.get('standings_url', '')
                tournament_link = f"[{title}]({url})" if url else f"**{title}**"
                line = f"{medal} {tournament_link} - Platz {placement['placement']} ({placement['match_record']})\n"
                if len(placements_text) + len(line) > 1024:
                    break
                placements_text += line
                shown += 1
            remaining = len(concluded_placements) - shown
            if remaining > 0:
                placements_text += f"*... und {remaining} weitere*"

            embed.add_field(
                name="📋 Letzte Turniere",
                value=placements_text,
                inline=False
            )

        # Ongoing tournaments the user is currently registered for
        ongoing_texts = []
        for tournament in active_tournaments.values():
            if tournament.cancelled:
                continue
            user_state = tournament.users.get(ctx.author.id)
            if user_state == ParticipationState.PARTICIPATE:
                msg_url = f"https://discord.com/channels/{tournament.guild.id}/{tournament.channel_id}/{tournament.message_id}"
                ongoing_texts.append(f"⚔️ [{tournament.title}]({msg_url})")

        if ongoing_texts:
            embed.add_field(
                name="🎮 Aktive Turniere",
                value="\n".join(ongoing_texts),
                inline=False
            )
        
        await ctx.followup.send(embed=embed, ephemeral=True)
    
    @slash_command(description="Zeige die Top Spieler in einer bestimmten Kategorie")
    async def top_players(
        self,
        ctx: ApplicationContext,
        category: Option(
            str,
            description="Kategorie für Ranking",
            choices=["Turniersiege", "Matchsiege", "Winrate", "Turnierteilnahmen", "Top 3 Finishes"],
            default="Turniersiege"
        ) = "Turniersiege"
    ):
        """Display top players in a specific category."""
        await ctx.defer(ephemeral=True)
        
        hof = get_hall_of_fame()
        
        category_map = {
            "Turniersiege": "winners",
            "Matchsiege": "match_wins",
            "Winrate": "match_winrate",
            "Turnierteilnahmen": "tournaments",
            "Top 3 Finishes": "top3s"
        }
        
        sort_key = category_map.get(category, "winners")
        top_participants = hof.get_top_participants(limit=10, sort_by=sort_key)
        
        if not top_participants:
            await ctx.followup.send("Keine Daten verfügbar!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"🏆 Top 10 - {category}",
            color=discord.Color.gold()
        )
        
        ranking_text = ""
        for idx, participant in enumerate(top_participants, 1):
            medal = self._get_medal(idx)
            
            if sort_key == "winners":
                value = f"{participant.tournament_winners} Siege"
            elif sort_key == "match_wins":
                value = f"{participant.total_match_wins} Matches ({participant.get_match_record()})"
            elif sort_key == "match_winrate":
                value = f"{participant.get_match_win_percentage()*100:.1f}% ({participant.get_match_record()})"
            elif sort_key == "tournaments":
                value = f"{participant.tournament_participations} Turniere"
            elif sort_key == "top3s":
                value = f"{participant.tournament_top3s} Top 3 Finishes"
            else:
                value = f"{participant.tournament_winners} Siege"
            
            ranking_text += f"{medal} **{idx}.** {participant.username} - {value}\n"
        
        embed.description = ranking_text
        await ctx.followup.send(embed=embed, ephemeral=True)
    
    @staticmethod
    def _get_medal(placement: int) -> str:
        """Get medal emoji for a placement."""
        if placement == 1:
            return "🥇"
        elif placement == 2:
            return "🥈"
        elif placement == 3:
            return "🥉"
        else:
            return f"#{placement}"


def setup(bot: Bot):
    bot.add_cog(HallOfFameCog(bot))
