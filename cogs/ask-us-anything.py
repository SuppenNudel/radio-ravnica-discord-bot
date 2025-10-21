from discord.ext import commands, tasks
from discord.commands import option, slash_command
from ezcord import log, Cog
from discord import Bot
import discord
import modules.notion as notion
from datetime import datetime
import os
import traceback
from enum import Enum
from modules import env
import random
import logging
import re

link_log = logging.getLogger("link_logger")

EMOJI_THUMBS_UP = "👍"
EMOJI_NOTEPAD = "🗒️"
EMOJI_CANCEL = "❌"

class AuaStatus(Enum):
    SEEN = "Gesehen"
    RECORDED = "Aufgenommen"
    NOT_STARTED = "Not started"
    REJECTED = "Abgelehnt"

# aua stands for ask us anything
def create_aua_payload(message_text, author:discord.User, date:datetime, url, status:None|AuaStatus=None):
    builder = (
        notion.NotionPayloadBuilder()
        .add_title("Text", message_text)
        .add_text("Author", author.display_name)
        .add_text("Author ID", str(author.id))
        .add_date("Date", date)
        .add_url("Discord Link", url)
    )
    if status:
        builder.add_status("Status", status.value)
    return builder.build()

emoji_to_status = {
    EMOJI_THUMBS_UP: AuaStatus.RECORDED,
    EMOJI_NOTEPAD: AuaStatus.SEEN,
    EMOJI_CANCEL: AuaStatus.REJECTED
}

async def analyse_reactions(reactions:list[discord.Reaction], aua_managers):
    for emoji, status in emoji_to_status.items():
        for reaction in reactions:
            if str(reaction.emoji) == emoji:
                # Fetch the users who reacted with the target emoji
                async for user in reaction.users():
                    if user.id in aua_managers:
                        return status
    return None


class AskUsAnything(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.db_id_aua = env.AUA_DATABASE_ID
        channel_aua = os.getenv("CHANNEL_AUA")
        if channel_aua:
            self.channel_id_aua = int(channel_aua)
        else:
            raise Exception(".env/CHANNEL_AUA not defined")
        aua_managers_raw = os.getenv("AUA_MANAGERS")

        self.aua_managers = []
        if aua_managers_raw:
            self.aua_managers = [int(x) for x in aua_managers_raw.split(",")]
        

    @Cog.listener()
    async def on_ready(self):
        self.check_to_post_threads.start()
        log.debug(self.__class__.__name__ + " is ready")

    async def write_or_update_notion(self, message_text, author, date:datetime, url, status:AuaStatus|None=None, initial_response:discord.Interaction|None=None, current_message=None):
        if status == None:
            status = AuaStatus.NOT_STARTED

        payload = create_aua_payload(
            message_text=message_text,
            author=author,
            date=date,
            url=url,
            status=status
        )
        filter = notion.NotionFilterBuilder().add_url_filter("Discord Link", notion.URLCondition.EQUALS, url).build()
        log.debug(f"Adding or Updating Notion entry for {url}")
        notion.add_or_update_entry(self.db_id_aua, payload, filter)

    @slash_command(guild_ids=[env.GUILD_ID], description="Hole eine zufällige 'Gesehen' Frage aus der Datenbank")
    @discord.default_permissions(manage_guild=True)
    @commands.has_role("Moderator")
    async def random_aua_question(self, ctx:discord.commands.ApplicationContext):
        await ctx.defer(ephemeral=True)
        filter = (
            notion.NotionFilterBuilder()
            .add_status_filter("Status", notion.StatusCondition.EQUALS, AuaStatus.SEEN.value)
            .add_url_filter("Discord Link", notion.URLCondition.CONTAINS, "https://discord.com/channels/783441128119730236")
            .build()
        )
        entries = notion.get_all_entries(self.db_id_aua, filter=filter)
        if not entries:
            await ctx.respond("Keine passende Frage gefunden.", ephemeral=True)
            return
        entry = random.choice(entries)

        question = entry.get_text_property("Text")
        author = entry.get_text_property("Author")
        author_id = entry.get_text_property("Author ID")
        url = entry.get_url_property("Discord Link")
        await ctx.respond(f"**Frage:** {question}\n**Von:** <@{author_id}>({author})\n{url}", ephemeral=True)

    @slash_command(guild_ids=[env.GUILD_ID])
    @discord.default_permissions(manage_guild=True)
    @commands.has_role("Moderator")
    @option(name="limit", input_type=int)
    @option(name="starting_message_id", input_type=str)
    async def grab_aua_posts(self, ctx:discord.commands.ApplicationContext, limit:int=100, starting_message_id:str|None=None):
        if ctx.author.id not in self.aua_managers:
            await ctx.respond("Lass das mal lieber Cedric oder Robin machen :)", ephemeral=True)
            return
        
        if ctx.channel_id != self.channel_id_aua:
            await ctx.respond("Nur im ask-us-anything Kanal bitte", ephemeral=True)
            return
        

        initial_response = await ctx.respond(f"Werde die letzten {limit} Nachrichten analysieren...", ephemeral=True)
        if not initial_response:
            raise Exception("Unable to send response")

        if not isinstance(initial_response, discord.Interaction):
            raise Exception("initial_response is not discord.Interaction")

        channel = ctx.channel

        if not isinstance(channel, discord.TextChannel):
            raise Exception(f"Not a text channel but {type(channel)}")

        start_message = None
        if starting_message_id:
            start_message = await channel.fetch_message(int(starting_message_id))

        counter = 0
        async for message in channel.history(limit=limit, before=start_message):
            try:
                author = message.author
                counter += 1
                current_message = f"Analysiere Nachricht {counter} von {limit}"
                if author.bot:
                    await initial_response.edit_original_response(content=f"{current_message}: Nachricht ist von einem Bot")
                else:
                    message_url = message.jump_url
                    message_text = message.clean_content
                    message_created_at:datetime = message.created_at
                    status = await analyse_reactions(message.reactions, self.aua_managers)

                    await self.write_or_update_notion(
                        status=status,
                        author=author,
                        date=message_created_at,
                        message_text=message_text,
                        url=message_url,
                        initial_response=initial_response,
                        current_message=current_message
                    )
            except Exception as e:
                log.error(f"An error occured while trying to Analyse message: {str(e)}\n{traceback.format_exc()}")

        log.debug(f"Found {counter} messages")
        if initial_response:
            if isinstance(initial_response, discord.Interaction):
                await initial_response.edit_original_response(content=f"{counter} Nachrichten wurden analysiert")
            else:
                log.debug(f"Can't edit_original_response of {initial_response}")

    @Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        channel = self.bot.get_channel(payload.channel_id)
        if not channel or channel.id != self.channel_id_aua or not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception as e:
            log.error(f"Could not fetch edited message: {e}")
            return

        if message.author.bot:
            return
        if message.thread:
            # If the message is already in a thread, do not update Notion
            log.debug(f"Message {message.jump_url} is in a thread, skipping Notion update.")
            return

        log.debug(f"Message edited in {channel.mention} ({channel.name})")
        message_text = message.clean_content
        author = message.author
        date = message.created_at
        url = message.jump_url

        await self.write_or_update_notion(
            message_text=message_text,
            author=author,
            date=date,
            url=url
        )

    @Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        channel = self.bot.get_channel(payload.channel_id)
        if not channel or channel.id != self.channel_id_aua or not isinstance(channel, discord.TextChannel):
            return

        # We can't get author or content from deleted message, but we can use the message id and channel
        url = f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}"

        log.debug(f"Message deleted in {channel.mention} ({channel.name})")

        # Remove entry from Notion if exists
        filter = notion.NotionFilterBuilder().add_url_filter("Discord Link", notion.URLCondition.EQUALS, url).build()
        aua_entries = notion.get_all_entries(self.db_id_aua, filter=filter)
        if aua_entries and len(aua_entries) == 1:
            entry = aua_entries[0]
            notion.remove_entry(entry)
            log.info(f"Deleted Notion entry for message: {url}")
        else:
            log.debug(f"Could not find Notion entry for deleted message: {url}")

    # when user posts message in #ask-us-anything
    # write to notion
    @Cog.listener()
    async def on_message(self, message:discord.Message):
        if message.author.bot:
            return
        
        if message.channel.id != self.channel_id_aua:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return
        
        if message.channel:
            log.debug(f"Received member message in {message.channel.mention} ({message.channel.name})")
        else:
            log.debug(f"Received member message in a {type(message.channel)} channel -> {message.channel}")

        message_text = message.clean_content
        author = message.author
        date = message.created_at
        url = message.jump_url
        
        await self.write_or_update_notion(message_text=message_text,
            author=author,
            date=date,
            url=url)
        
    # when robin reacts with 🗒️
    # update notion with "seen"

    # when robin reacts with 👍
    # update notion with "recorded"
    @Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id != self.channel_id_aua:
            return

        if str(payload.emoji) not in emoji_to_status:
            # ignore reaction
            return
        
        if not payload.guild_id:
            raise Exception("Reaction payload does not have guild_id")
        
        if not payload.channel_id:
            raise Exception("Reaction payload does not have channel_id")
        
        # if not payload.message_id:
        #     raise Exception("Reaction payload does not have channel_id")

        # Get the guild, channel, and message where the reaction was added
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            raise Exception("Guild not found")
        
        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise Exception("Channel is not a Text Channel")
        
        # Get the user who added the reaction
        user = guild.get_member(payload.user_id)
        if not user:
            log.error(f"User with id {payload.user_id} not found")
            return
        
        if user.bot: # Ignore bot reactions
            return
        
        if not user.id in self.aua_managers: # Only act on robins reactions
            return
        
        message = await channel.fetch_message(payload.message_id)

        log.debug(f"Received reaction from aua_manager")
        
        log.debug("Will act on reaction...")

        url = message.jump_url
        status = emoji_to_status[str(payload.emoji)]

        message_text = message.clean_content
        author = message.author
        date = message.created_at

        await self.write_or_update_notion(
            message_text=message_text,
            author=author,
            date=date,
            url=url,
            status=status)
        
    @tasks.loop(minutes=60)
    async def check_to_post_threads(self):
        filter = (
            notion.NotionFilterBuilder()
            .add_text_filter("Discord Link", notion.TextCondition.CONTAINS, f"/{self.channel_id_aua}/")
            .add_status_filter("Status", notion.StatusCondition.EQUALS, AuaStatus.RECORDED.value)
            .add_text_filter("Thread ID", notion.TextCondition.IS_EMPTY, True)
            .add_checkbox_filter("Timestamp überprüft", notion.CheckboxCondition.EQUALS, True)
            .build()
        )
        entries = notion.get_all_entries(self.db_id_aua, filter=filter)
        for entry in entries:
            discord_link = entry.get_url_property("Discord Link")
            log.debug(f"{entry} ist bereit als Thread gepostet zu werden. Werde Thread erstellen...")
            
            # Extract message ID from the Discord link
            try:
                parts = discord_link.strip().split("/")
                message_id = int(parts[-1])
                channel_id = int(parts[-2])
            except Exception as e:
                log.error(f"Could not parse Discord link: {discord_link} ({e})")
                continue

            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.error(f"Channel {channel_id} not found or not a TextChannel")
                continue

            try:
                message = await channel.fetch_message(message_id)
            except Exception as e:
                log.error(f"Could not fetch message {message_id} in channel {channel_id}: {e}")
                continue

            # Create a thread from the message if not already in a thread
            if not message.thread:
                video_page_id = entry.get_property("Video")[0]['id']
                video_page:notion.Entry = notion.get_page_by_id(video_page_id)
                if not video_page:
                    log.error(f"Could not find Notion page with id: {video_page_id}")
                    continue
                question_text = entry.get_text_property("Text").replace("\n", " ")
                upload_date = video_page.get_date_property("Upload Date")['start']
                comment_text_raw = video_page.get_formula_property("Comment Text")
                # Split at timestamps like 00:00, 12:34, etc.
                comment_text = re.split(r"\d{1,2}:\d{2}", comment_text_raw)
                # Remove empty strings and strip whitespace
                comment_text = [c.strip() for c in comment_text if c.strip()]

                # Find the index where question_text is contained in comment_text
                index = next((i for i, comment in enumerate(comment_text) if question_text in comment), -1)
                if index < 0:
                    video_page_public_url = video_page.public_url
                    link_log.error(f"Could not find question text {discord_link} in comments for {video_page_public_url}\n>>> {comment_text_raw}")
                    continue
                question_no = index + 1 if index != -1 else "unbekannt"

                thread_name = f"{upload_date.strftime('%d.%m.%Y')} - Frage {question_no}"
                try:
                    thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
                    # Update Notion entry with the Thread ID
                    thread_id = str(thread.id)
                    update_payload = (
                        notion.NotionPayloadBuilder()
                        .add_text("Thread ID", thread_id)
                        .build()
                    )
                    # Post the content of property "Youtube" as a message in the thread
                    filter = notion.NotionFilterBuilder().add_url_filter("Discord Link", notion.URLCondition.EQUALS, discord_link).build()
                    notion.add_or_update_entry(self.db_id_aua, update_payload, filter)

                    youtube_timestamped_url = entry.get_formula_property("Youtube")
                    if youtube_timestamped_url:
                        await thread.send(youtube_timestamped_url)
                    log.debug(f"Thread '{thread_name}' created for message {message.jump_url}")
                except Exception as e:
                    log.error(f"An error occurred during thread creation for message {message.jump_url}")
            else:
                log.debug(f"Thread already exists for message {message.jump_url}")

def setup(bot:Bot):
    bot.add_cog(AskUsAnything(bot))
