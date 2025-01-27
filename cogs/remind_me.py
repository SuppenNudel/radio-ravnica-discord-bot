from discord.ext import commands, tasks
from discord import Bot, Message
from discord.commands import message_command, slash_command
from ezcord.emb import EzContext
from ezcord import log
import discord
from datetime import datetime
import dateparser
import modules.notion as notion
import os
import pytz
from discord.ui import Modal
from discord.utils import format_dt

timezone = pytz.timezone("Europe/Berlin")

DB_ID_REMIND_ME = os.getenv("DATABASE_ID_REMIND_ME")
DB_FIELD_DATE = "Timestamp"
DB_FIELD_USER = "User"
DB_FIELD_MESSAGE = "Message"
DB_FIELD_CHANNEL = "Channel"
DB_FIELD_REASON = "Reason"
DB_FIELD_GUILD = "Guild"

def save_reminder_request(user, date, reason, guild_id, channel_id, message_id):
    user_id = user.id
    payload_builder = (notion.NotionPayloadBuilder()
        .add_date(DB_FIELD_DATE, start=date)
        .add_text(DB_FIELD_CHANNEL, str(channel_id))
        .add_text(DB_FIELD_USER, str(user_id))
        .add_text(DB_FIELD_GUILD, str(guild_id))
    )
    if message_id:
        payload_builder.add_title(DB_FIELD_MESSAGE, str(message_id))
    if reason:
        payload_builder.add_text(DB_FIELD_REASON, reason)
    payload = payload_builder.build()
    notion.add_to_database(DB_ID_REMIND_ME, payload)

async def handle_input(interaction: discord.Interaction|EzContext, followup_message, time_input, reason, user:discord.member.Member|discord.User, message=None):
    if not time_input:
        await interaction.followup.edit_message(followup_message.id, content=r"Ohne Angaben kann ich nichts machen ¯\_(ツ)_/¯")
        return
    
    parsed_date = dateparser.parse(time_input, settings={'RETURN_AS_TIMEZONE_AWARE': True})
    if not parsed_date:
        raise ValueError(f"Ich konnte deine Zeitangabe nicht interpretieren: {time_input}")
    if parsed_date < datetime.now(tz=timezone):
        raise ValueError(f"Interpretierter Zeitpunkt: {format_dt(parsed_date, style='R')}\nIch kann dich nicht in der Vergangenheit erinnern. Zeitreisen wurden noch nicht erfunden.")

    if message:
        guild_id = message.guild.id
        channel_id = message.channel.id
        message_id = message.id
    else:
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        message_id = None
    
    save_reminder_request(user, parsed_date, reason, guild_id, channel_id, message_id)
    await interaction.followup.edit_message(followup_message.id, content=f"✅ {user.mention}, ich werde dich {format_dt(parsed_date, style='R')} erinnern (am {format_dt(parsed_date, style='F')})")

class ReminderModal(Modal):
    def __init__(self, user, message:Message, time_input=None, reason=None):
        super().__init__(title = "Erstellung einer Erinnerung")

        self.add_item(discord.ui.InputText(
            label="Wann soll ich dich erinnern?",
            placeholder="z.B. in 10 Minuten, morgen um 17 Uhr, am 24.12.2025 um 12:00 Uhr",
            required=True,
            value=f"{time_input} <- bitte anpassen" if time_input else None,
        ))
        self.add_item(discord.ui.InputText(
            label="Grund",
            placeholder="Eine Gedächtnisstütze, warum du erinnert werden wolltest",
            required=False,
            style=discord.InputTextStyle.long,
            value=reason,
        ))

        self.user:discord.Member = user
        self.message = message
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        followup_message  = await interaction.followup.send(
            "⌛ Ich arbeite an deiner Anfrage, das kann kurz dauern...", ephemeral=True
        )
        if followup_message is None:
            log.error("followup_message is None")
            return
        time_input = self.children[0].value
        reason = self.children[1].value

        try:
            await handle_input(interaction, followup_message, time_input, reason, self.user, message=self.message)
        except Exception as e:
            await interaction.followup.edit_message(followup_message.id, content=f"❌ Fehler\n{str(e)}\nKlicke auf den Button unten, um es erneut zu versuchen.", view=ReopenModalView(self.user, self.message, time_input, reason))

class ReopenModalButton(discord.ui.Button):
    def __init__(self, user, message, time_input=None, reason=None):
        super().__init__(label="Erneut versuchen", style=discord.ButtonStyle.primary)
        self.user = user
        self.message = message
        self.time_input = time_input
        self.reason = reason

    async def callback(self, interaction: discord.Interaction):
        modal = ReminderModal(self.user, self.message, self.time_input, self.reason)
        await interaction.response.send_modal(modal)

class ReopenModalView(discord.ui.View):
    def __init__(self, user, message, time_input=None, reason=None):
        super().__init__()
        self.add_item(ReopenModalButton(user, message, time_input, reason))

class RemindMe(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        
    @slash_command(description="Erstelle eine Erinnerung")
    async def erinnere_mich(self, ctx:EzContext, wann, grund):
        await ctx.response.defer(ephemeral=True)
        followup_message  = await ctx.followup.send(
            "⌛ Ich arbeite an deiner Anfrage, das kann kurz dauern...", ephemeral=True
        )
        reason = grund
        if followup_message is None:
            log.error("followup_message is None")
            return
        try:
            await handle_input(ctx, followup_message, wann, reason, ctx.author)
        except Exception as e:
            await ctx.followup.edit_message(followup_message.id, content=f"❌ Fehler:\n{str(e)}\nBitte versuche es noch einmal!")

    @message_command(name="Erstelle Erinnerung...")
    async def remind_me(self, ctx:EzContext, message:Message):        
        # get message id
        # save user that used the interaction
        # save to database
        modal = ReminderModal(user=ctx.author, message=message)
        await ctx.send_modal(modal)

    async def send_reminder_message(self, guild, message_id, channel_id, user_id, reason=None):
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

            description = None
            timestamp = None
            author = None
            message = None
            if message_id:
                # Fetch the message by its ID
                message = await channel.fetch_message(message_id)
                description = message.content if message.content else "No content"
                timestamp = message.created_at
                author = message.author

            # Fetch the user by ID
            user = await bot.fetch_user(user_id)

            # Create an embed to forward the message
            embed = discord.Embed(
                # title="Link zur Nachricht",
                description=description,
                color=discord.Color.blurple(),  # You can change the embed color
                # url=message.jump_url,
                timestamp=timestamp
            )
            if author:
                avatar_url = None
                if hasattr(author, "guild_avatar"):
                    if author.guild_avatar:
                        avatar_url = author.guild_avatar.url
                if not avatar_url:
                    if hasattr(author, "avatar"):
                        if author.avatar:
                            avatar_url = author.avatar.url
                embed.set_author(name=f"von {message.author.name}", icon_url=avatar_url)#, url=message.author.jump_url)

            guild_icon_url = guild.icon.url if guild.icon else None
            embed.set_footer(text=guild.name, icon_url=guild_icon_url)

            content = "Hey, du wolltest "+ (f"an diese Nachricht erinnert werden {message.jump_url}" if message else "erinnert werden")
            if reason:
                content += f"\nGrund: {reason}"
            
            if message:
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
        for guild in self.bot.guilds:
            filter_date = datetime.now(tz=timezone)
            filter = (notion.NotionFilterBuilder()
                    .add_date_filter(property_name=DB_FIELD_DATE, value=filter_date.strftime("%Y-%m-%d %H:%M"), condition=notion.DateCondition.ON_OR_BEFORE)
                    .add_text_filter(property_name=DB_FIELD_GUILD, value=str(guild.id), condition=notion.TextCondition.EQUALS)
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
                success = await self.send_reminder_message(guild, message, channel, user, reason=reason)
                if success:
                    # remove from database
                    notion.remove_entry(my_entry)

def setup(bot:Bot):
    bot.add_cog(RemindMe(bot))
