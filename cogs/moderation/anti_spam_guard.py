import os
import re
from ezcord import log
import logging
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import discord
from discord import Bot
from discord.commands import slash_command
from ezcord import Cog
from modules import env


link_log = logging.getLogger("link_logger")


URL_PATTERN = re.compile(r"(https?://\S+|discord\.gg/\S+|discord\.com/invite/\S+)", re.IGNORECASE)

# Detection thresholds
BURST_WINDOW_SECONDS = 20
BURST_MESSAGE_THRESHOLD = 6

DUPLICATE_WINDOW_SECONDS = 60
DUPLICATE_THRESHOLD = 4

LINK_WINDOW_SECONDS = 30
LINK_THRESHOLD = 3

MENTION_WINDOW_SECONDS = 30
MENTION_THRESHOLD = 6

ACTION_COOLDOWN_SECONDS = 60 * 60 * 24
TIMEOUT_DURATION = timedelta(days=1)


@dataclass
class MessageSignal:
    created_at: datetime
    channel_id: int
    message_id: int
    normalized_content: str
    link_count: int
    mention_count: int


class BanSuggestionView(discord.ui.View):
    def __init__(
        self,
        target_user_id: int,
        moderator_role_id: int | None,
        trigger_message_refs: list[tuple[int, int]],
    ):
        super().__init__(timeout=60 * 60 * 24)
        self.target_user_id = target_user_id
        self.moderator_role_id = moderator_role_id
        self.trigger_message_refs = trigger_message_refs

    async def _delete_trigger_messages(self, guild: discord.Guild) -> int:
        deleted = 0

        for channel_id, message_id in self.trigger_message_refs:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue

            try:
                await channel.get_partial_message(message_id).delete()
                deleted += 1
            except discord.NotFound:
                continue
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue

        return deleted

    def _is_moderator(self, user: discord.abc.User, guild: discord.Guild) -> bool:
        member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
        if not isinstance(member, discord.Member):
            return False

        if member.guild_permissions.ban_members or member.guild_permissions.administrator:
            return True

        if self.moderator_role_id is None:
            return False

        return any(role.id == self.moderator_role_id for role in member.roles)

    @discord.ui.button(label="Nutzer bannen", style=discord.ButtonStyle.danger, emoji="🔨")
    async def ban_user(self, button: discord.ui.Button, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Dieser Button funktioniert nur auf einem Server.", ephemeral=True)
            return

        if not self._is_moderator(interaction.user, guild):
            await interaction.response.send_message(
                "Nur Moderator:innen mit Bann-Rechten dürfen diese Aktion ausführen.",
                ephemeral=True,
            )
            return

        target_member = guild.get_member(self.target_user_id)
        ban_reason = (
            f"Ban bestätigt durch Anti-Spam-Vorschlag. "
            f"Ausgelöst von {interaction.user} ({interaction.user.id})."
        )

        try:
            if target_member is not None:
                await guild.ban(target_member, reason=ban_reason, delete_message_seconds=60 * 60)
            else:
                await guild.ban(discord.Object(id=self.target_user_id), reason=ban_reason, delete_message_seconds=60 * 60)
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ich habe keine Berechtigung, diesen Nutzer zu bannen.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            await interaction.response.send_message(
                f"Bann fehlgeschlagen: {error}",
                ephemeral=True,
            )
            return

        button.disabled = True
        button.label = "Gebannt"

        if interaction.message:
            await interaction.message.edit(view=self)

        deleted_count = await self._delete_trigger_messages(guild)

        await interaction.response.send_message(
            (
                f"Nutzer <@{self.target_user_id}> wurde gebannt. "
                f"{deleted_count} auslösende Spam-Nachricht(en) wurden gelöscht."
            ),
            ephemeral=True,
        )


class AntiSpamGuard(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self._message_signals: dict[tuple[int, int], deque[MessageSignal]] = defaultdict(deque)
        self._last_action_at: dict[tuple[int, int], datetime] = {}

    def _normalize_content(self, content: str) -> str:
        return " ".join(content.lower().split())

    def _collect_reasons_and_trigger_signals(
        self, signals: deque[MessageSignal]
    ) -> tuple[list[str], list[MessageSignal]]:
        now = discord.utils.utcnow()

        burst_signals = [
            signal
            for signal in signals
            if (now - signal.created_at).total_seconds() <= BURST_WINDOW_SECONDS
        ]
        burst_count = len(burst_signals)

        duplicate_signals = [
            signal
            for signal in signals
            if signal.normalized_content
            and (now - signal.created_at).total_seconds() <= DUPLICATE_WINDOW_SECONDS
        ]
        duplicate_counter = Counter(signal.normalized_content for signal in duplicate_signals)
        duplicate_count = max(duplicate_counter.values(), default=0)
        duplicate_texts = {
            text
            for text, count in duplicate_counter.items()
            if count >= DUPLICATE_THRESHOLD
        }

        link_signals = [
            signal
            for signal in signals
            if signal.link_count > 0 and (now - signal.created_at).total_seconds() <= LINK_WINDOW_SECONDS
        ]
        link_count = sum(signal.link_count for signal in link_signals)

        mention_signals = [
            signal
            for signal in signals
            if signal.mention_count > 0
            and (now - signal.created_at).total_seconds() <= MENTION_WINDOW_SECONDS
        ]
        mention_count = sum(signal.mention_count for signal in mention_signals)

        reasons: list[str] = []
        trigger_signal_ids: set[int] = set()

        if burst_count >= BURST_MESSAGE_THRESHOLD:
            reasons.append(
                f"Nachrichten-Burst: {burst_count} Nachrichten in {BURST_WINDOW_SECONDS}s"
            )
            trigger_signal_ids.update(signal.message_id for signal in burst_signals)

        if duplicate_count >= DUPLICATE_THRESHOLD:
            reasons.append(
                f"Duplikat-Text: gleiche Nachricht {duplicate_count}x in {DUPLICATE_WINDOW_SECONDS}s"
            )
            trigger_signal_ids.update(
                signal.message_id
                for signal in duplicate_signals
                if signal.normalized_content in duplicate_texts
            )

        if link_count >= LINK_THRESHOLD:
            reasons.append(
                f"Link-Spam: {link_count} Link(s) in {LINK_WINDOW_SECONDS}s"
            )
            trigger_signal_ids.update(signal.message_id for signal in link_signals)

        if mention_count >= MENTION_THRESHOLD:
            reasons.append(
                f"Mention-Spam: {mention_count} Mentions in {MENTION_WINDOW_SECONDS}s"
            )
            trigger_signal_ids.update(signal.message_id for signal in mention_signals)

        trigger_signals = [
            signal for signal in signals if signal.message_id in trigger_signal_ids
        ]

        return reasons, trigger_signals

    async def _react_to_trigger_messages(
        self, guild: discord.Guild, trigger_signals: list[MessageSignal]
    ) -> None:
        for signal in trigger_signals:
            channel = guild.get_channel(signal.channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue

            try:
                await channel.get_partial_message(signal.message_id).add_reaction("👍")
            except discord.NotFound:
                continue
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue

    def _prune_old_signals(self, key: tuple[int, int], signals: deque[MessageSignal]) -> None:
        now = discord.utils.utcnow()
        keep_seconds = max(
            BURST_WINDOW_SECONDS,
            DUPLICATE_WINDOW_SECONDS,
            LINK_WINDOW_SECONDS,
            MENTION_WINDOW_SECONDS,
        )

        while signals and (now - signals[0].created_at).total_seconds() > keep_seconds:
            signals.popleft()

        if not signals:
            self._message_signals.pop(key, None)

    def _find_moderator_role(self, guild: discord.Guild) -> discord.Role | None:
        moderator_role_id_raw = os.getenv("MODERATOR_ROLE_ID")
        if moderator_role_id_raw:
            try:
                moderator_role_id = int(moderator_role_id_raw)
                role = guild.get_role(moderator_role_id)
                if role:
                    return role
            except ValueError:
                link_log.warning("Invalid MODERATOR_ROLE_ID env var: %s", moderator_role_id_raw)

        for role_name in ("Moderator", "Moderators", "Mod"):
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                return role

        return None

    def _find_mod_alert_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        channel_id_raw = os.getenv("MOD_ALERT_CHANNEL_ID")
        if channel_id_raw:
            try:
                channel_id = int(channel_id_raw)
                channel = guild.get_channel(channel_id)
                if channel and channel.permissions_for(guild.me).send_messages:
                    return channel
            except ValueError:
                link_log.warning("Invalid MOD_ALERT_CHANNEL_ID env var: %s", channel_id_raw)

        preferred_names = ("mod-alerts", "moderation", "mod-log", "mods")
        for name in preferred_names:
            channel = discord.utils.get(guild.text_channels, name=name)
            if channel and channel.permissions_for(guild.me).send_messages:
                return channel

        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            return guild.system_channel

        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                return channel

        return None

    async def _apply_timeout(self, member: discord.Member, reason: str) -> bool:
        try:
            await member.timeout_for(TIMEOUT_DURATION, reason=reason)
            return True
        except AttributeError:
            timeout_until = discord.utils.utcnow() + TIMEOUT_DURATION
            try:
                await member.edit(timed_out_until=timeout_until, reason=reason)
                return True
            except discord.Forbidden:
                return False
            except discord.HTTPException:
                return False
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            return False

    async def _notify_moderators(
        self,
        message: discord.Message,
        member: discord.Member,
        reasons: list[str],
        timeout_applied: bool,
        moderator_role: discord.Role | None,
        trigger_signals: list[MessageSignal],
    ) -> None:
        guild = message.guild
        if guild is None:
            return

        alert_channel = self._find_mod_alert_channel(guild)
        if alert_channel is None:
            link_log.warning("[anti-spam] No writable alert channel found in guild %s", guild.id)
            return

        summary = "\n".join(f"- {reason}" for reason in reasons)
        timeout_status = "Ja" if timeout_applied else "Nein (fehlende Berechtigung oder API-Fehler)"
        trigger_message_count = len({signal.message_id for signal in trigger_signals})

        embed = discord.Embed(
            title="Verdacht auf Bot/Spam-Verhalten",
            description=(
                f"rr-bot empfiehlt einen Bann für {member.mention} ({member.id}).\n\n"
                f"**Erkannte Signale:**\n{summary}\n\n"
                f"**Bot-Aktionen bisher:**\n"
                f"- Timeout (24h) angewendet: {timeout_status}\n"
                f"- Markierte Auslöser-Nachrichten (👍): {trigger_message_count}\n"
                f"- Bei Bann-Button: Diese Nachrichten werden gelöscht\n\n"
                f"**Auslösende Nachricht:** [Zum Kontext springen]({message.jump_url})"
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )

        embed.set_footer(text="Button nur von Moderator:innen mit Bann-Recht nutzbar")

        mention_prefix = moderator_role.mention if moderator_role else "@moderation"
        view = BanSuggestionView(
            target_user_id=member.id,
            moderator_role_id=moderator_role.id if moderator_role else None,
            trigger_message_refs=[
                (signal.channel_id, signal.message_id) for signal in trigger_signals
            ],
        )

        await alert_channel.send(
            content=f"{mention_prefix} Anti-Spam-Erkennung hat einen Bann-Vorschlag erstellt.",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    @slash_command(guild_ids=[env.GUILD_ID], description="Zeigt die aktuellen Anti-Spam-Einstellungen")
    async def anti_spam_settings(self, ctx: discord.commands.ApplicationContext):
        guild = ctx.guild
        if guild is None:
            await ctx.respond("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
            return

        invoker = ctx.user if isinstance(ctx.user, discord.Member) else guild.get_member(ctx.user.id)
        moderator_role = self._find_moderator_role(guild)

        can_view = False
        if isinstance(invoker, discord.Member):
            can_view = (
                invoker.guild_permissions.manage_messages
                or invoker.guild_permissions.manage_guild
                or invoker.guild_permissions.administrator
                or (moderator_role is not None and any(role.id == moderator_role.id for role in invoker.roles))
            )

        if not can_view:
            await ctx.respond(
                "Nur Moderator:innen oder Admins dürfen diese Einstellungen einsehen.",
                ephemeral=True,
            )
            return

        alert_channel = self._find_mod_alert_channel(guild)

        configured_role = os.getenv("MODERATOR_ROLE_ID") or "nicht gesetzt"
        configured_channel = os.getenv("MOD_ALERT_CHANNEL_ID") or "nicht gesetzt"
        resolved_role = moderator_role.mention if moderator_role else "nicht gefunden"
        resolved_channel = alert_channel.mention if isinstance(alert_channel, discord.TextChannel) else "nicht gefunden"

        embed = discord.Embed(
            title="Anti-Spam Guard Einstellungen",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Erkennung",
            value=(
                f"Nachrichten-Burst: {BURST_MESSAGE_THRESHOLD} in {BURST_WINDOW_SECONDS}s\n"
                f"Duplikat-Text: {DUPLICATE_THRESHOLD}x in {DUPLICATE_WINDOW_SECONDS}s\n"
                f"Link-Spam: {LINK_THRESHOLD} Links in {LINK_WINDOW_SECONDS}s\n"
                f"Mention-Spam: {MENTION_THRESHOLD} Mentions in {MENTION_WINDOW_SECONDS}s"
            ),
            inline=False,
        )
        embed.add_field(
            name="Automatische Aktion",
            value=(
                f"Timeout-Dauer: {int(TIMEOUT_DURATION.total_seconds() // 3600)}h\n"
                f"Aktion-Cooldown pro Nutzer: {int(ACTION_COOLDOWN_SECONDS // 3600)}h\n"
                "Bei Treffer: Timeout + Moderator-Ping + Bann-Button"
            ),
            inline=False,
        )
        embed.add_field(
            name="Routing",
            value=(
                f"MODERATOR_ROLE_ID: {configured_role}\n"
                f"MOD_ALERT_CHANNEL_ID: {configured_channel}\n"
                f"Aufgelöste Mod-Rolle: {resolved_role}\n"
                f"Aufgelöster Alert-Kanal: {resolved_channel}"
            ),
            inline=False,
        )

        await ctx.respond(embed=embed, ephemeral=True)

    @Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if message.author.bot or message.webhook_id:
            return

        if not isinstance(message.author, discord.Member):
            return

        member = message.author

        if member.guild_permissions.manage_messages or member.guild_permissions.administrator:
            return

        key = (message.guild.id, member.id)
        now = discord.utils.utcnow()

        signals = self._message_signals[key]
        normalized_content = self._normalize_content(message.content or "")
        link_count = len(URL_PATTERN.findall(message.content or ""))
        mention_count = len(message.raw_mentions)

        signals.append(
            MessageSignal(
                created_at=now,
                channel_id=message.channel.id,
                message_id=message.id,
                normalized_content=normalized_content,
                link_count=link_count,
                mention_count=mention_count,
            )
        )

        self._prune_old_signals(key, signals)
        reasons, trigger_signals = self._collect_reasons_and_trigger_signals(signals)

        if not reasons:
            return

        last_action_at = self._last_action_at.get(key)
        if last_action_at and (now - last_action_at).total_seconds() < ACTION_COOLDOWN_SECONDS:
            return

        self._last_action_at[key] = now

        await self._react_to_trigger_messages(message.guild, trigger_signals)

        moderator_role = self._find_moderator_role(message.guild)
        timeout_reason = f"Automatische Anti-Spam-Erkennung: {'; '.join(reasons)}"
        timeout_applied = await self._apply_timeout(member, timeout_reason)

        link_log.warning(
            "[anti-spam] user_id=%s guild_id=%s timeout_applied=%s trigger_messages=%s reasons=%s",
            member.id,
            message.guild.id,
            timeout_applied,
            len(trigger_signals),
            reasons,
        )

        await self._notify_moderators(
            message=message,
            member=member,
            reasons=reasons,
            timeout_applied=timeout_applied,
            moderator_role=moderator_role,
            trigger_signals=trigger_signals,
        )

    @Cog.listener()
    async def on_ready(self):
        log.info(self.__class__.__name__ + " is ready")

def setup(bot: Bot):
    bot.add_cog(AntiSpamGuard(bot))
