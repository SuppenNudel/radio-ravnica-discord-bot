"""
Hall of Fame Discord Cog - Provides commands to view tournament winners and participant standings.
"""

import discord
from ezcord import Cog, log
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Option
from datetime import datetime
from dateutil.relativedelta import relativedelta
from modules.hall_of_fame import get_hall_of_fame, HallOfFame, FORMAT_CHOICES
from modules.spelltable.tournament_model import active_tournaments, ParticipationState
from modules import env
from modules.db import SessionLocal, Tournament as TournamentRecord


BASE_TIME_FILTER_CHOICES = ["Alle Zeit", "Letzte 12 Monate"]


def _get_available_tournament_years() -> list[int]:
    """Return distinct tournament years from DB (descending)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(TournamentRecord.start_time)
            .filter(TournamentRecord.start_time.isnot(None))
            .all()
        )
        years = sorted({row[0].year for row in rows if row[0]}, reverse=True)
        return years
    except Exception:
        return []
    finally:
        db.close()


TIME_FILTER_CHOICES = BASE_TIME_FILTER_CHOICES + [str(y) for y in _get_available_tournament_years()]


def _parse_time_filter(time_filter: str | None) -> tuple[datetime | None, datetime | None]:
    """Convert a time_filter choice string into (time_from, time_to) naive UTC datetimes."""
    if time_filter == "Letzte 12 Monate":
        return datetime.utcnow() - relativedelta(months=12), None
    if time_filter and time_filter.isdigit() and len(time_filter) == 4:
        year = int(time_filter)
        return datetime(year, 1, 1), datetime(year + 1, 1, 1)
    return None, None


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
        statistik: Option(
            str,
            description="Statistik für die Rangliste",
            choices=["Turniersiege", "Matchsiege", "Match-Winrate", "Turnierteilnahmen", "Top-3-Platzierungen", "Top-8-Platzierungen"],
            default="Turniersiege"
        ) = "Turniersiege",
        zeitraum: Option(
            str,
            description="Zeitraum filtern",
            choices=TIME_FILTER_CHOICES,
            default="Alle Zeit"
        ) = "Alle Zeit",
        format: Option(
            str,
            description="Nach Format filtern",
            choices=FORMAT_CHOICES,
            required=False
        ) = None,
    ):
        """Display the hall of fame with various sorting options."""
        await ctx.defer(ephemeral=True)
        
        time_from, time_to = _parse_time_filter(zeitraum)
        hof = get_hall_of_fame(time_from=time_from, time_to=time_to, format_filter=format or None)
        
        # Map display names to sort keys
        sort_map = {
            "Turniersiege": "winners",
            "Matchsiege": "match_wins",
            "Match-Winrate": "match_winrate",
            "Turnierteilnahmen": "tournaments",
            "Top-3-Platzierungen": "top3s",
            "Top-8-Platzierungen": "top8s"
        }
        
        sort_key = sort_map.get(statistik, "winners")
        top_participants = hof.get_top_participants(limit=20, sort_by=sort_key)
        
        if not top_participants:
            await ctx.followup.send("Die Hall of Fame ist noch leer. Beende ein Turnier, um sie zu füllen!", ephemeral=True)
            return
        
        # Build the embed
        embed = discord.Embed(
            title="🏆 Hall of Fame - Turnier-Rangliste",
            color=discord.Color.gold()
        )
        footer_parts = [f"Gezeigte Statistik: {statistik}"]
        if zeitraum and zeitraum != "Alle Zeit":
            footer_parts.append(f"Zeitraum: {zeitraum}")
        if format:
            footer_parts.append(f"Format: {format}")
        embed.set_footer(text=" | ".join(footer_parts))
        
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
    async def my_stats(
        self,
        ctx: ApplicationContext,
        zeitraum: Option(
                str,
                description="Zeitraum filtern",
            choices=TIME_FILTER_CHOICES,
                default="Alle Zeit"
            ) = "Alle Zeit",
            format: Option(
                str,
                description="Nach Format filtern",
                choices=FORMAT_CHOICES,
                required=False
            ) = None,
        ):
        """Display personal tournament statistics."""
        await ctx.defer(ephemeral=True)

        time_from, time_to = _parse_time_filter(zeitraum)
        hof = get_hall_of_fame(time_from=time_from, time_to=time_to, format_filter=format or None)
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
        footer_parts = []
        if zeitraum and zeitraum != "Alle Zeit":
            footer_parts.append(f"Zeitraum: {zeitraum}")
        if format:
            footer_parts.append(f"Format: {format}")
        if footer_parts:
            embed.set_footer(text=" | ".join(footer_parts))
        
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

        # Upcoming tournaments the user has registered for (not yet started)
        upcoming_texts = []
        ongoing_texts = []
        for tournament in active_tournaments.values():
            if tournament.cancelled:
                continue
            if tournament.swiss_tournament and tournament.swiss_tournament.winner:
                continue
            user_state = tournament.users.get(ctx.author.id)
            if user_state not in (ParticipationState.PARTICIPATE, ParticipationState.TENTATIVE, ParticipationState.WAITLIST):
                continue
            msg_url = f"https://discord.com/channels/{tournament.guild.id}/{tournament.channel_id}/{tournament.message_id}"
            if not tournament.swiss_tournament:
                # tournament hasn't started yet → upcoming
                emoji = "<?>"  # Shouldn't happen, but just in case
                if user_state == ParticipationState.PARTICIPATE:
                    emoji = "✅"
                elif user_state == ParticipationState.TENTATIVE:
                    emoji = "❓"
                elif user_state == ParticipationState.WAITLIST:
                    emoji = "⌚"
                start_text = (
                    f"{discord.utils.format_dt(tournament.time, 'F')} ({discord.utils.format_dt(tournament.time, 'R')})"
                    if tournament.time
                    else "TBD"
                )
                upcoming_texts.append(
                    f"{emoji} [{tournament.title}]({msg_url}) | {start_text}"
                )
            else:
                # started but not concluded → ongoing
                ongoing_texts.append(f"⚔️ [{tournament.title}]({msg_url})")

        def _truncated_field(texts: list[str]) -> str:
            value = ""
            for i, text in enumerate(texts):
                candidate = value + text + "\n"
                remaining = len(texts) - i - 1
                suffix = f"*... und {remaining} weitere*" if remaining else ""
                if len(candidate) + len(suffix) > 1024:
                    value += f"*... und {len(texts) - i} weitere*"
                    break
                value = candidate
            return value.rstrip("\n")

        if upcoming_texts:
            embed.add_field(
                name="🗓️ Bevorstehende Turniere",
                value=_truncated_field(upcoming_texts),
                inline=False
            )

        if ongoing_texts:
            embed.add_field(
                name="🎮 Aktive Turniere",
                value=_truncated_field(ongoing_texts),
                inline=False
            )
        
        # Recent tournament placements (concluded only — must have results)
        concluded_placements = [p for p in stats['tournament_placements'] if p.get('standings_url') or p.get('match_record')]
        if concluded_placements:
            lines = []
            for placement in concluded_placements[:5]:
                medal = self._get_medal(placement['placement'])
                title = placement['tournament']
                url = placement.get('standings_url', '')
                tournament_link = f"[{title}]({url})" if url else f"**{title}**"
                lines.append(f"{medal} {tournament_link} - Platz {placement['placement']} ({placement['match_record']})")

            placements_text = ""
            shown = 0
            for line in lines:
                suffix = f"\n*... und {len(concluded_placements) - shown - 1} weitere*" if shown < len(concluded_placements) - 1 else ""
                candidate = placements_text + line + "\n"
                if len(candidate) + len(suffix) > 1024:
                    placements_text += f"*... und {len(concluded_placements) - shown} weitere*"
                    break
                placements_text = candidate
                shown += 1

            embed.add_field(
                name="📋 Letzte Turniere",
                value=placements_text,
                inline=False
            )
        
        await ctx.followup.send(embed=embed, ephemeral=True)
    
    @slash_command(description="Zeige die Top Spieler in einer bestimmten Kategorie")
    async def top_players(
        self,
        ctx: ApplicationContext,
        kategorie: Option(
            str,
            description="Kategorie für Ranking",
            choices=["Turniersiege", "Matchsiege", "Winrate", "Turnierteilnahmen", "Top-3-Platzierungen"],
            default="Turniersiege"
        ) = "Turniersiege",
        zeitraum: Option(
            str,
            description="Zeitraum filtern",
            choices=TIME_FILTER_CHOICES,
            default="Alle Zeit"
        ) = "Alle Zeit",
        format: Option(
            str,
            description="Nach Format filtern",
            choices=FORMAT_CHOICES,
            required=False
        ) = None,
    ):
        """Display top players in a specific category."""
        await ctx.defer(ephemeral=True)
        
        time_from, time_to = _parse_time_filter(zeitraum)
        hof = get_hall_of_fame(time_from=time_from, time_to=time_to, format_filter=format or None)
        
        category_map = {
            "Turniersiege": "winners",
            "Matchsiege": "match_wins",
            "Winrate": "match_winrate",
            "Turnierteilnahmen": "tournaments",
            "Top-3-Platzierungen": "top3s"
        }
        
        sort_key = category_map.get(kategorie, "winners")
        top_participants = hof.get_top_participants(limit=10, sort_by=sort_key)
        
        if not top_participants:
            await ctx.followup.send("Keine Daten verfügbar!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"🏆 Top 10 - {kategorie}",
            color=discord.Color.gold()
        )
        footer_parts = [f"Kategorie: {kategorie}"]
        if zeitraum and zeitraum != "Alle Zeit":
            footer_parts.append(f"Zeitraum: {zeitraum}")
        if format:
            footer_parts.append(f"Format: {format}")
        embed.set_footer(text=" | ".join(footer_parts))
        
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
