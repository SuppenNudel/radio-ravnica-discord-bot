from discord.ext import commands, tasks
from discord.commands import option, slash_command
from ezcord import Bot, log
import discord
import modules.notion as notion
from datetime import datetime
import os
from typing import Literal
import traceback


# aua stands for ask us anything
def create_aua_payload(message_text, author:discord.User, date:datetime, url, status:None|Literal["Aufgenommen", "Gesehen"]=None):
    builder = (
        notion.NotionPayloadBuilder()
        .add_title("Text", message_text)
        .add_text("Author", author.display_name)
        .add_number("Author ID", author.id)
        .add_date("Date", date)
        .add_url("Discord Link", url)
    )
    if status:
        builder.add_status("Status", status)
    return builder.build()

async def analyse_reactions(reactions, aua_managers):
    emoji_to_status = {
        notion.EMOJI_THUMBS_UP: notion.STATUS_RECORDED,
        notion.EMOJI_NOTEPAD: notion.STATUS_SEEN
    }

    for emoji, status in emoji_to_status.items():
        for reaction in reactions:
            if str(reaction.emoji) == emoji:
                # Fetch the users who reacted with the target emoji
                async for user in reaction.users():
                    if user.id in aua_managers:
                        return status
    return None


class AskUsAnything(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot:Bot = bot
        self.database_id = os.getenv("DATABASE_ID_AUA")
        channel_aua = os.getenv("CHANNEL_AUA")
        if channel_aua:
            self.channel_id_aua = int(channel_aua)
        aua_managers_raw = os.getenv("AUA_MANAGERS")

        self.aua_managers = []
        if aua_managers_raw:
            self.aua_managers = [int(x) for x in aua_managers_raw.split(",")]
        

    @commands.Cog.listener()
    async def on_ready(self):
        log.info("ask-us-anything started")

    async def write_or_update_notion(self, message_text, author, date:datetime, url, status:str|None=notion.STATUS_NOT_STARTED, initial_response=None, current_message=None):
        # check entry
        query_response = notion.get_entries(self.database_id, filter=
            {
                "property": "Discord Link",  # Replace with the name of your unique identifier property
                "url": {
                    "equals": url
                }
            }
        )
        entry_exists = query_response['results']

        if entry_exists:
            log.debug("Entry exits already")
            if initial_response:
                await initial_response.edit_original_response(content=f"{current_message}: Eintrag existiert")

            entry = entry_exists[0]

            existing_status = entry['properties']['Status']['status']['name']
            if existing_status == status:
                log.debug("No need to update, status already same")
                if initial_response:
                    await initial_response.edit_original_response(content=f"{current_message}: No need to update, status already same")
                return
            # write if not exists
            entry_id = entry['id']
            update_properties = notion.NotionPayloadBuilder().add_status("Status", status).build()
            # update status
            update_response = notion.update_entry(entry_id, update_properties=update_properties)
            log.debug(f"Update response: {update_response["url"]}")
            if initial_response:
                await initial_response.edit_original_response(content=f"{current_message}: Updated Entry to {status}")
        else:
            log.debug("Creating database entry...")
            if initial_response:
                await initial_response.edit_original_response(content=f"{current_message}: Creating database entry...")
            payload = create_aua_payload(message_text=message_text, author=author, date=date, url=url, status=status)

            response = notion.add_to_database(self.database_id, payload)
            log.debug(f"Created database entry on message for '{message_text}': {response["url"]}")
            if initial_response:
                await initial_response.edit_original_response(content=f"{current_message}: Created database entry with status {status}")

    @slash_command()
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

        channel = ctx.channel

        start_message = None
        if starting_message_id:
            start_message = await ctx.channel.fetch_message(int(starting_message_id))

        counter = 0
        async for message in channel.history(limit=limit, before=start_message):
            try:
                author = message.author
                counter += 1
                current_message = f"Analysiere Nachricht {counter} von {limit}"
                if author.bot:
                    if initial_response:
                        if type(initial_response) == discord.Interaction:
                            await initial_response.edit_original_response(content=f"{current_message}: Nachricht ist von einem Bot")
                        else:
                            log.debug(f"Can't edit_original_response of {initial_response}")
                else:
                    message_url = message.jump_url
                    message_text = message.clean_content
                    message_created_at:datetime = message.created_at
                    status = await analyse_reactions(message.reactions, self.aua_managers)

                    await self.write_or_update_notion(
                        status=status, author=author, date=message_created_at, message_text=message_text, url=message_url, initial_response=initial_response, current_message=current_message
                    )
            except Exception as e:
                log.error(traceback.format_exc())
                try:
                    log.error(f"An error occured while trying to Analyse message: {str(e)}")
                except:
                    log.error(f"An error occured while trying to Analyse message: {type(e)}")

        log.debug(f"Found {counter} messages")
        if initial_response:
            if type(initial_response) == discord.Interaction:
                await initial_response.edit_original_response(content=f"{counter} Nachrichten wurden analysiert")
            else:
                log.debug(f"Can't edit_original_response of {initial_response}")

    # when user posts message in #ask-us-anything
    # write to notion
    @commands.Cog.listener()
    async def on_message(self, message:discord.Message):
        if message.author.bot:
            return
        
        if message.channel.id != self.channel_id_aua:
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
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id != self.channel_id_aua:
            return

        # Get the guild, channel, and message where the reaction was added
        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        # Get the user who added the reaction
        user = guild.get_member(payload.user_id)
        if user.bot: # Ignore bot reactions
            return
        
        if not user.id in self.aua_managers: # Only act on robins reactions
            return
        
        log.debug(f"Received reaction from aua_manager")
        
        if not str(payload.emoji) in [notion.EMOJI_NOTEPAD, notion.EMOJI_THUMBS_UP]:
            log.info(f"Reaction with {payload.emoji} ignored")
            return
        
        log.debug("Will act on reaction...")

        url = message.jump_url
        status = None
        if str(payload.emoji) == notion.EMOJI_NOTEPAD:
            # update to "Seen"
            status = notion.STATUS_SEEN

        if str(payload.emoji) == notion.EMOJI_THUMBS_UP:
            # update to "Aufgenommen"
            status = notion.STATUS_RECORDED

        message_text = message.clean_content
        author = message.author
        date = message.created_at

        await self.write_or_update_notion(
            message_text=message_text,
            author=author,
            date=date,
            url=url,
            status=status)

def setup(bot):
    bot.add_cog(AskUsAnything(bot))
