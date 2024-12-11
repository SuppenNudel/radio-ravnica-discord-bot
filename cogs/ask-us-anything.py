from discord.ext import commands, tasks
from discord.commands import option
from ezcord import Bot, log
import discord
from modules.notion import add_to_database, check_entry, update_entry
from datetime import datetime
import os

def create_notion_payload(message_text, author, date:datetime, url, status=None):
    properties = {
        "Text": {  # Title property
            "title": [{"text": {"content": message_text}}]
        },
        "Author": {  # Text property
            "rich_text": [{"text": {"content": author.name}}]
        },
        "Author ID": {  # Number property
            "number": author.id
        },
        "Date": {  # Date property
            "date": {"start": date.isoformat()}
        },
        "Discord Link": {  # URL property
            "url": url
        }
    }
    if status:
        properties["Status"] = {  # Status property
            "type": "status",
            "status": {"name": status}
        }
    return properties

class AskUsAnything(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot:Bot = bot
        self.database_id = os.getenv("DATABASE_ID_AUA")
        self.channel_id_aua = int(os.getenv("CHANNEL_AUA"))
        aua_managers_raw = os.getenv("AUA_MANAGERS")

        self.aua_managers = []
        if aua_managers_raw:
            self.aua_managers = [int(x) for x in aua_managers_raw.split(",")]
        

    @commands.Cog.listener()
    async def on_ready(self):
        log.info("ask-us-anything started")

    # when user posts message in #ask-us-anything
    # write to notion
    @commands.Cog.listener()
    async def on_message(self, message:discord.Message):
        if message.author.bot:
            return
        
        if message.channel.id != self.channel_id_aua:
            return
        
        log.debug(f"Received member message in #{message.channel.name}")

        message_text = message.clean_content
        author = message.author
        date = message.created_at
        url = message.jump_url
        
        notion_payload = create_notion_payload(
            message_text=message_text,
            author=author,
            date=date,
            url=url)
        
        response = add_to_database(self.database_id, notion_payload)
        log.info(f"Created database entry on message for '{message_text}': {response["url"]}")

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
        
        if not str(payload.emoji) in ["🗒️", "👍"]:
            log.info(f"Reaction with {payload.emoji} ignored")
            return
        
        log.debug("Will act on reaction...")

        url = message.jump_url
        query_response = check_entry(self.database_id, filter=
            {
                "property": "Discord Link",  # Replace with the name of your unique identifier property
                "url": {
                    "equals": url
                }
            }
        )
        status = None
        if str(payload.emoji) == "🗒️":
            # update to "Seen"
            status = "Gesehen"

        if str(payload.emoji) == "👍":
            # update to "Aufgenommen"
            status = "Aufgenommen"

        entry_exists = query_response['results']
        if entry_exists:
            log.debug("Entry exits already")

            entry = entry_exists[0]

            existing_status = entry['properties']['Status']['status']['name']
            if existing_status == status:
                log.debug("No need to update, status already same")
                return
            # write if not exists
            entry_id = entry['id']
            if status:
                update_properties = {
                    "Status": {
                        "type": "status",
                        "status": {
                            "name": status
                        }
                    }
                }
                # update status
                update_response = update_entry(entry_id, update_properties=update_properties)
                log.debug(f"Update response: {update_response["url"]}")
            else:
                log.debug("Wrong emoji")
        else:
            log.debug("Creating database entry")
            message_text = message.clean_content
            author = message.author
            date = message.created_at
            
            notion_payload = create_notion_payload(
                message_text=message_text,
                author=author,
                date=date,
                url=url,
                status=status)

            response = add_to_database(self.database_id, notion_payload)
            log.debug(f"Created database entry on message for '{message_text}': {response["url"]}")


    @commands.slash_command()
    @option("message_id")
    async def get_reactions(self, ctx, message_id: int):
        pass

def setup(bot):
    bot.add_cog(AskUsAnything(bot))
