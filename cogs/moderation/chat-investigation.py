import os
import asyncio
import re
import discord
from discord import Bot
from discord.commands import slash_command, option
from ezcord import Cog, log
from google import genai
from google.api_core import exceptions
from modules import env

MAX_MESSAGES = 200
GEMINI_MODEL = "gemini-3.1-flash-lite"


class ChatInvestigation(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = None
            log.warning("GEMINI_API_KEY ist nicht gesetzt. /chat_analysieren wird nicht funktionieren.")

    @slash_command(guild_ids=[env.GUILD_ID], description="Analysiert einen Chat-Abschnitt auf aggressiven Ton (nur Moderatoren).")
    @discord.default_permissions(manage_messages=True)
    @option("start_message_id", description="ID der ersten Nachricht des zu analysierenden Bereichs")
    @option("end_message_id", description="ID der letzten Nachricht des zu analysierenden Bereichs")
    async def chat_analysieren(
        self,
        ctx: discord.ApplicationContext,
        start_message_id: str,
        end_message_id: str,
    ):
        try:
            await ctx.defer(ephemeral=True)
        except discord.NotFound:
            # Interaction token already expired (10062) — nothing we can do
            return

        if self.client is None:
            await ctx.respond("❌ GEMINI_API_KEY ist nicht konfiguriert.", ephemeral=True)
            return

        try:
            start_id = int(start_message_id.strip())
            end_id = int(end_message_id.strip())
        except ValueError:
            await ctx.respond("❌ Ungültige Nachrichten-IDs. Bitte gib reine Zahlen ein.", ephemeral=True)
            return

        # Ensure chronological order
        if start_id > end_id:
            start_id, end_id = end_id, start_id

        channel = ctx.channel

        try:
            start_msg = await channel.fetch_message(start_id)
        except discord.NotFound:
            await ctx.respond(f"❌ Nachricht mit ID `{start_id}` wurde in diesem Kanal nicht gefunden.", ephemeral=True)
            return
        except discord.Forbidden:
            await ctx.respond("❌ Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu lesen.", ephemeral=True)
            return

        if start_id == end_id:
            all_messages = [start_msg]
        else:
            try:
                end_msg = await channel.fetch_message(end_id)
            except discord.NotFound:
                await ctx.respond(f"❌ Nachricht mit ID `{end_id}` wurde in diesem Kanal nicht gefunden.", ephemeral=True)
                return
            except discord.Forbidden:
                await ctx.respond("❌ Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu lesen.", ephemeral=True)
                return

            middle_messages = []
            async for msg in channel.history(
                after=start_msg,
                before=end_msg,
                limit=MAX_MESSAGES - 2,
                oldest_first=True,
            ):
                middle_messages.append(msg)

            all_messages = [start_msg] + middle_messages + [end_msg]

        text_messages = [msg for msg in all_messages if msg.content.strip()]

        if not text_messages:
            await ctx.respond("ℹ️ Keine Textnachrichten in diesem Bereich gefunden.", ephemeral=True)
            return

        # Build chat text with timestamps and create mapping for post-processing
        timestamp_to_msg = {}  # timestamp -> (message_id, user_mention)
        chat_lines = []
        for msg in text_messages:
            ts = msg.created_at.strftime('%Y-%m-%d %H:%M')
            timestamp_to_msg[ts] = (msg.id, msg.author.mention)
            chat_lines.append(f"[{ts}] {msg.author.display_name}: {msg.content}")
        
        chat_text = "\n".join(chat_lines)

        # Limit prompt size to avoid exceeding Gemini's free-tier input limits
        if len(chat_text) > 30000:
            chat_text = chat_text[:30000] + "\n... [abgeschnitten]"

        prompt = (
            "Analysiere den folgenden Discord-Chat-Verlauf auf aggressiven, feindseligen oder toxischen Ton. "
            "Gib für JEDE beteiligte Person max. 3 Sätze: Name, ob die Person problematisch ist (ja/nein), Grund, und die [YYYY-MM-DD HH:MM] Zeitstempel der Nachrichten die diese Bewertung rechtfertigen. "
            "Verwende die exakten Namen und Zeitstempel aus dem Chat. "
            "Am Ende eine einzeilige Gesamteinschätzung (1-10 Aggression).\n\n"
            f"Chat-Verlauf:\n{chat_text}"
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            result_text = response.text
            result_text = self._postprocess_response(result_text, timestamp_to_msg, text_messages, ctx.guild)
        except exceptions.ResourceExhausted as e:
            # Quota exceeded — try one retry after a short wait
            retry_after = self._extract_retry_seconds(str(e))
            log.warning(f"Gemini API quota exceeded. Retrying in {retry_after}s...")
            await asyncio.sleep(min(retry_after, 2))  # Cap at 2 seconds to not hang Discord
            try:
                response = await self.client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                )
                result_text = response.text
                result_text = self._postprocess_response(result_text, timestamp_to_msg, text_messages, ctx.guild)
            except Exception as retry_error:
                log.error(f"Gemini API Fehler (retry) bei /chat_analysieren: {retry_error}")
                await ctx.respond(
                    f"❌ KI-Quota überschritten. Bitte versuche es in {retry_after:.0f} Sekunden erneut.",
                    ephemeral=True,
                )
                return
        except Exception as e:
            log.error(f"Gemini API Fehler bei /chat_analysieren: {e}")
            await ctx.respond(f"❌ Fehler bei der KI-Analyse: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔍 Chat-Analyse: Aggressiver Ton",
            color=env.SPIKE_RED,
        )

        # Embed description is capped at 4096 chars; if longer, split into fields
        if len(result_text) <= 4096:
            embed.description = result_text
        else:
            chunks = [result_text[i : i + 1024] for i in range(0, min(len(result_text), 5120), 1024)]
            embed.description = chunks[0]
            for chunk in chunks[1:]:
                embed.add_field(name="\u200b", value=chunk, inline=False)

        embed.set_footer(
            text=f"{len(all_messages)} Nachricht(en) analysiert ({len(text_messages)} mit Text) • Powered by Gemini {GEMINI_MODEL}"
        )

        await ctx.respond(embed=embed, ephemeral=True)

    @Cog.listener()
    async def on_ready(self):
        log.info(self.__class__.__name__ + " is ready")

    def _extract_retry_seconds(self, error_msg: str) -> float:
        """Extract retry delay from Gemini error message (e.g., 'Please retry in 31.226591759s.')"""
        match = re.search(r"retry in ([\d.]+)s", error_msg, re.IGNORECASE)
        return float(match.group(1)) if match else 60.0

    def _postprocess_response(self, text: str, timestamp_to_msg: dict, all_messages: list, guild: discord.Guild) -> str:
        """Replace timestamps with message links and usernames with mentions."""
        # Build a mapping of display names to mentions for quick lookup
        name_to_mention = {}
        for msg in all_messages:
            name_to_mention[msg.author.display_name] = msg.author.mention
        
        # Replace timestamps [YYYY-MM-DD HH:MM] with message links
        def replace_timestamp(match):
            ts = match.group(1)
            if ts in timestamp_to_msg:
                msg_id, _ = timestamp_to_msg[ts]
                # Discord message link format
                url = f"https://discord.com/channels/{guild.id}/{all_messages[0].channel.id}/{msg_id}"
                return f"[{ts}]({url})"
            return match.group(0)
        
        text = re.sub(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]', replace_timestamp, text)
        
        # Replace usernames with mentions (but be careful not to replace in links)
        for display_name, mention in name_to_mention.items():
            # Only replace if it's a whole word and not inside markdown links
            text = re.sub(rf'\b{re.escape(display_name)}\b(?![\]\)])', mention, text)
        
        return text

def setup(bot: Bot):
    bot.add_cog(ChatInvestigation(bot))
