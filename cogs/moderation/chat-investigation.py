import os
import asyncio
import re
import discord
from discord.ext import commands
from discord import Bot
from discord.commands import message_command
from ezcord import Cog, log
from google import genai
from google.api_core import exceptions
from modules import env

MAX_MESSAGES = 200
GEMINI_MODEL = "gemini-3.1-flash-lite"


class ChatInvestigationModal(discord.ui.Modal):
    def __init__(self, cog: "ChatInvestigation", start_message: discord.Message):
        super().__init__(title="Chat-Analyse")
        self.cog = cog
        self.start_message = start_message
        self.second_message_input = discord.ui.InputText(
            label="Zweite Nachricht (optional)",
            placeholder="Message-ID oder Discord-Link",
            required=False,
            style=discord.InputTextStyle.singleline,
        )
        self.add_item(self.second_message_input)

    async def callback(self, interaction: discord.Interaction):
        raw_reference = self.second_message_input.value.strip() if self.second_message_input.value else ""
        if not raw_reference:
            end_message_id = self.start_message.id
        else:
            parsed_reference = self.cog._parse_message_reference(raw_reference)
            if parsed_reference is None:
                await interaction.response.send_message(
                    "❌ Ungültige Eingabe. Bitte gib eine Message-ID oder einen Discord-Nachrichtenlink an.",
                    ephemeral=True,
                )
                return

            channel_id, end_message_id = parsed_reference
            if channel_id is not None and channel_id != self.start_message.channel.id:
                await interaction.response.send_message(
                    "❌ Die zweite Nachricht muss aus demselben Kanal stammen wie die ausgewählte Nachricht.",
                    ephemeral=True,
                )
                return

        await self.cog._run_chat_analysis(interaction, self.start_message.id, end_message_id)


class ChatInvestigation(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = None
            log.warning("GEMINI_API_KEY ist nicht gesetzt. /chat_analysieren wird nicht funktionieren.")

    @message_command(name="Chat analysieren", guild_ids=[env.GUILD_ID])
    @discord.default_permissions(manage_messages=True)
    @commands.has_role("Moderator")
    async def chat_analysieren(self, ctx: discord.ApplicationContext, message: discord.Message):
        await ctx.send_modal(ChatInvestigationModal(self, message))

    async def _run_chat_analysis(self, interaction: discord.Interaction, start_id: int, end_id: int):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            # Interaction token already expired (10062) — nothing we can do
            return

        if self.client is None:
            await interaction.followup.send("❌ GEMINI_API_KEY ist nicht konfiguriert.", ephemeral=True)
            return

        # Ensure chronological order
        if start_id > end_id:
            start_id, end_id = end_id, start_id

        channel = interaction.channel
        guild = interaction.guild

        if channel is None or guild is None:
            await interaction.followup.send("❌ Dieser Befehl kann nur in einem Server-Kanal genutzt werden.", ephemeral=True)
            return

        try:
            start_msg = await channel.fetch_message(start_id)
        except discord.NotFound:
            await interaction.followup.send(f"❌ Nachricht mit ID `{start_id}` wurde in diesem Kanal nicht gefunden.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu lesen.", ephemeral=True)
            return

        if start_id == end_id:
            all_messages = [start_msg]
        else:
            try:
                end_msg = await channel.fetch_message(end_id)
            except discord.NotFound:
                await interaction.followup.send(f"❌ Nachricht mit ID `{end_id}` wurde in diesem Kanal nicht gefunden.", ephemeral=True)
                return
            except discord.Forbidden:
                await interaction.followup.send("❌ Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu lesen.", ephemeral=True)
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
            await interaction.followup.send("ℹ️ Keine Textnachrichten in diesem Bereich gefunden.", ephemeral=True)
            return

        # Build chat text with message IDs so the model can cite exact messages
        chat_lines = []
        for msg in text_messages:
            chat_lines.append(f"[MID:{msg.id}] {msg.author.display_name}: {msg.content}")
        
        chat_text = "\n".join(chat_lines)

        # Limit prompt size to avoid exceeding Gemini's free-tier input limits
        if len(chat_text) > 30000:
            chat_text = chat_text[:30000] + "\n... [abgeschnitten]"

        prompt = (
            "Analysiere den folgenden Discord-Chat-Verlauf auf aggressiven, feindseligen oder toxischen Ton. "
            "Gib für JEDE beteiligte Person max. 3 Sätze: Name, ob die Person problematisch ist (zwischen 1 und 10), Grund, und die Message IDs der Nachrichten die diese Bewertung rechtfertigen. "
            "Verwende die exakten Namen und Message IDs aus dem Chat und nenne Message IDs IMMER im Format [MID:<id>]. \n\n"
            f"Chat-Verlauf:\n{chat_text}"
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            result_text = response.text
            result_text = self._postprocess_response(result_text, text_messages, guild)
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
                result_text = self._postprocess_response(result_text, text_messages, guild)
            except Exception as retry_error:
                log.error(f"Gemini API Fehler (retry) bei /chat_analysieren: {retry_error}")
                await interaction.followup.send(
                    f"❌ KI-Quota überschritten. Bitte versuche es in {retry_after:.0f} Sekunden erneut.",
                    ephemeral=True,
                )
                return
        except Exception as e:
            log.error(f"Gemini API Fehler bei /chat_analysieren: {e}")
            await interaction.followup.send(f"❌ Fehler bei der KI-Analyse: {e}", ephemeral=True)
            return

        header = "🔍 Chat-Analyse: Aggressiver Ton"
        footer = (
            f"\n\n{len(all_messages)} Nachricht(en) analysiert "
            f"({len(text_messages)} mit Text) • Powered by Gemini {GEMINI_MODEL}"
        )

        full_text = f"{header}\n\n{result_text}{footer}"
        max_len = 1900  # Keep margin below Discord's 2000-char content limit

        chunks = [full_text[i : i + max_len] for i in range(0, len(full_text), max_len)]
        for chunk in chunks:
            await interaction.followup.send(content=chunk, ephemeral=True)

    @Cog.listener()
    async def on_ready(self):
        log.info(self.__class__.__name__ + " is ready")

    def _extract_retry_seconds(self, error_msg: str) -> float:
        """Extract retry delay from Gemini error message (e.g., 'Please retry in 31.226591759s.')"""
        match = re.search(r"retry in ([\d.]+)s", error_msg, re.IGNORECASE)
        return float(match.group(1)) if match else 60.0

    def _parse_message_reference(self, value: str) -> tuple[int | None, int] | None:
        """Parse message reference from raw ID or Discord message link."""
        cleaned = value.strip()

        if re.fullmatch(r"\d{17,20}", cleaned):
            return None, int(cleaned)

        link_match = re.search(r"discord\.com/channels/(\d+)/(\d+)/(\d+)", cleaned)
        if link_match:
            _guild_id, channel_id, message_id = link_match.groups()
            return int(channel_id), int(message_id)

        return None

    def _postprocess_response(self, text: str, all_messages: list, guild: discord.Guild) -> str:
        """Replace message IDs with message links and usernames with mentions."""
        # Build a mapping of display names to mentions for quick lookup
        name_to_mention = {}
        for msg in all_messages:
            name_to_mention[msg.author.display_name] = msg.author.mention

        known_message_ids = {str(msg.id) for msg in all_messages}
        channel_id = all_messages[0].channel.id if all_messages else None
        
        # Replace [MID:<id>] tags with direct Discord message links.
        def replace_tagged_message_id(match):
            msg_id = match.group(1)
            if msg_id in known_message_ids and channel_id is not None:
                url = f"https://discord.com/channels/{guild.id}/{channel_id}/{msg_id}"
                return f"[MID:{msg_id}]({url})"
            return match.group(0)
        
        text = re.sub(r'\[MID:(\d{17,20})\]', replace_tagged_message_id, text)

        # Fallback: if the model emits a bare message ID, link it as well.
        def replace_bare_message_id(match):
            msg_id = match.group(1)
            if msg_id in known_message_ids and channel_id is not None:
                url = f"https://discord.com/channels/{guild.id}/{channel_id}/{msg_id}"
                return f"[{msg_id}]({url})"
            return match.group(0)

        text = re.sub(r'(?<![\w\[/])\b(\d{17,20})\b(?![\w\]])', replace_bare_message_id, text)
        
        # Replace usernames with mentions (but be careful not to replace in links)
        for display_name, mention in name_to_mention.items():
            # Only replace if it's a whole word and not inside markdown links
            text = re.sub(rf'\b{re.escape(display_name)}\b(?![\]\)])', mention, text)
        
        return text

def setup(bot: Bot):
    bot.add_cog(ChatInvestigation(bot))
