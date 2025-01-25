from discord.ext import commands, tasks
from discord import Bot, Message
from discord.commands import message_command
from ezcord.emb import EzContext
from ezcord import log
import discord
from datetime import datetime
import arrow
import dateparser
import modules.notion as notion
import os
import pytz
import locale
from discord.ui import Modal

timezone = pytz.timezone("Europe/Berlin")
locale.setlocale(locale.LC_TIME, "de")

DB_ID_REMIND_ME = os.getenv("DATABASE_ID_REMIND_ME")
DB_FIELD_DATE = "Timestamp"
DB_FIELD_USER = "User"
DB_FIELD_MESSAGE = "Message"
DB_FIELD_CHANNEL = "Channel"
DB_FIELD_REASON = "Reason"
DB_FIELD_GUILD = "Guild"

def save_reminder_request(user, referenced_message, channel_id, date, reason, guild_id):
    user_id = user.id
    payload = (notion.NotionPayloadBuilder()
        .add_title(DB_FIELD_MESSAGE, str(referenced_message))
        .add_date(DB_FIELD_DATE, start=date)
        .add_text(DB_FIELD_CHANNEL, str(channel_id))
        .add_text(DB_FIELD_USER, str(user_id))
        .add_text(DB_FIELD_REASON, reason)
        .add_text(DB_FIELD_GUILD, str(guild_id))
    ).build()
    notion.add_to_database(DB_ID_REMIND_ME, payload)

class MyModal(Modal):
    def __init__(self, user, message_id=None, channel_id=None, guild_id=None, *args, **kwargs):
        super().__init__(
            *args,
            **kwargs
        )

        self.add_item(discord.ui.InputText(
            label="Wann soll ich dich erinnern? In...",
            placeholder="2 stunden - 1 minute - 5 jahre 2 monate 2 minuten"
        ))
        self.add_item(discord.ui.InputText(
            label="Grund",
            placeholder="Eine Gedächtnisstütze, warum du erinnert werden wolltest",
            required=False,
        ))

        self.user:discord.Member = user
        self.channel_id = channel_id
        self.message_id = message_id
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        user_input = self.children[0].value
        if user_input:
            parsed_date = dateparser.parse(user_input)
            if parsed_date:
                arrow_object = arrow.get(parsed_date)
                relative_time = arrow_object.humanize(locale="de")
            else:
                log.error(f"Error parsing date '{user_input}'")
                # TODO handle error
                return

            reason = self.children[1].value
            save_reminder_request(self.user, self.message_id, self.channel_id, parsed_date, reason, self.guild_id)

            await interaction.response.send_message(f"{self.user.mention}, ich werde dich in {relative_time} erinnern (am {parsed_date.strftime('%A, %d. %B %Y')} um {parsed_date.strftime('%H:%M:%S')})", ephemeral=True)
        else:
            await interaction.response.send_message(r"Ohne Angaben kann ich nichts machen ¯\_(ツ)_/¯", ephemeral=True)

class RemindMe(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.guild = os.getenv("GUILD")
        if not self.guild:
            raise Exception("GUILD env var is not defined")

    @message_command(name="Erinnere mich in/am ...")
    async def remind_me(self, ctx:EzContext, message:Message):
        # get message id
        # save user that used the interaction
        # save to database
        modal = MyModal(title = "Erstellung einer Erinnerung", user=ctx.author, message_id=message.id, channel_id=message.channel.id, guild_id=self.guild)
        await ctx.send_modal(modal)

    async def send_reminder_message(self, message_id, channel_id, user_id, reason=None):
        bot = self.bot
        try:
            # Fetch the channel
            channel = bot.get_channel(channel_id)
            if channel is None:
                log.debug("couln't 'get' channel. Fetching channel")
                channel = await bot.fetch_channel(channel_id)
            
            if channel is None:
                log.debug("Fetching channel also didn't return a channel back")
            
            log.debug(f"got channel: {channel.name}")

            # Fetch the message by its ID
            message = await channel.fetch_message(message_id)

            # Fetch the user by ID
            user = await bot.fetch_user(user_id)

            # Create an embed to forward the message
            embed = discord.Embed(
                # title="Link zur Nachricht",
                description=message.content if message.content else "No content",
                color=discord.Color.blurple(),  # You can change the embed color
                # url=message.jump_url,
                timestamp=message.created_at
            )
            author = message.author
            avatar_url = None
            if hasattr(author, "guild_avatar"):
                if author.guild_avatar:
                    avatar_url = author.guild_avatar.url
            if not avatar_url:
                if hasattr(author, "avatar"):
                    if author.avatar:
                        avatar_url = author.avatar.url
            
            embed.set_author(name=f"von {message.author.name}", icon_url=avatar_url)#, url=message.author.jump_url)
            guild_icon_url = message.guild.icon.url if message.guild.icon else None
            embed.set_footer(text=message.guild.name, icon_url=guild_icon_url)

            content = f"Hey, du wolltest an diese Nachricht erinnert werden {message.jump_url}"
            if reason:
                content += f"\nGrund: {reason}"
                
            # Attachments
            if message.attachments:
                for attachment in message.attachments:
                    file = await attachment.to_file()
                    embed.set_image(url=attachment.url)  # If the attachment is an image
                    # Optionally, you can also send the file with the embed if you want
                    await user.send(
                        content=content,
                        embed=embed,
                        file=file
                    )
                return

            # Send the embed to the user via DM
            await user.send(content=content, embed=embed)
            log.debug(f"Message forwarded to {user.name} via DM.")
            return True
        except discord.NotFound:
            log.error("Message or channel not found!")
        except discord.Forbidden as e:
            log.error("I don't have permission to access this channel or DM the user!")
        except discord.HTTPException as e:
            log.error(f"An error occurred: {e}")
        return False

    @commands.Cog.listener()
    async def on_ready(self):
        self.check_reminders.start()
        log.debug(self.__class__.__name__ + " is ready")

    @tasks.loop(minutes=5)
    async def check_reminders(self):
        filter_date = datetime.now(tz=timezone)
        filter = (notion.NotionFilterBuilder()
                  .add_date_filter(property_name=DB_FIELD_DATE, value=filter_date.strftime("%Y-%m-%d %H:%M"), condition=notion.DateCondition.ON_OR_BEFORE)
                  .add_text_filter(property_name=DB_FIELD_GUILD, value=self.guild, condition=notion.TextCondition.EQUALS)
                  .build())
        entries = notion.get_all_entries(DB_ID_REMIND_ME, filter=filter)
        for entry in entries:
            my_entry = notion.Entry(entry)
            timestamp = my_entry.get_date_property(DB_FIELD_DATE)
            if timestamp['start'] > filter_date:
                # noch zu früh
                continue
            user = my_entry.get_text_property(DB_FIELD_USER)
            message = my_entry.get_text_property(DB_FIELD_MESSAGE)
            channel = my_entry.get_text_property(DB_FIELD_CHANNEL)
            reason = my_entry.get_text_property(DB_FIELD_REASON)
            success = await self.send_reminder_message(message, channel, user, reason=reason)
            if success:
                # remove from database
                notion.remove_entry(my_entry)

def setup(bot:Bot):
    bot.add_cog(RemindMe(bot))
