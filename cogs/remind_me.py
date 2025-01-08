from discord.ext import commands, tasks
from discord import Bot, Message
from discord.commands import message_command
from ezcord.emb import EzContext
import discord
from datetime import datetime
import pendulum
import locale
import modules.notion as notion
import os

# locale.setlocale(locale.LC_TIME, "de")

DB_ID_REMIND_ME = os.getenv("DATABASE_ID_REMIND_ME")
DB_FIELD_DATE = "Timestamp"
DB_FIELD_USER = "User"
DB_FIELD_MESSAGE = "Message"
DB_FIELD_CHANNEL = "Channel"
DB_FIELD_REASON = "Reason"

def parse_time_input(input_str: str, base_date: datetime = None) -> pendulum.DateTime:
    """
    Parses a user input string for absolute or relative time and returns a pendulum.DateTime object.
    
    :param input_str: The input string (e.g., "2025-01-07", "2 Stunden", "4 Monate", "2 Jahre 5 Monate").
    :param base_date: The base datetime to calculate relative times (defaults to now).
    :return: A pendulum.DateTime object for the resulting time.
    """
    if base_date is None:
        base_date = pendulum.now()

    # Handle absolute date and datetime formats
    try:
        # Parse as ISO format first
        return pendulum.parse(input_str)
    except ValueError:
        pass

    # Define relative time units mapping
    units = {
        "s": "hours",
        "stunde": "hours",
        "stunden": "hours",
        "m": "minutes",
        "minute": "minutes",
        "minuten": "minutes",
        "t": "days",
        "tag": "days",
        "tage": "days",
        "M": "months",
        "monat": "months",
        "monate": "months",
        "monaten": "months",
        "j": "years",
        "jahr": "years",
        "jahre": "years",
        "jahren": "years",
    }

    # Split input into chunks and process each relative time
    words = input_str.lower().split()
    total_time = {}

    i = 0
    while i < len(words):
        try:
            amount = int(words[i])  # Try to parse the current word as an integer
            unit = words[i + 1]  # Next word should be a time unit
            if unit not in units:
                raise ValueError(f"Invalid time unit: {unit}")
            # Accumulate the time
            unit_key = units[unit]
            total_time[unit_key] = total_time.get(unit_key, 0) + amount
            i += 2  # Move to the next pair of amount and unit
        except (ValueError, IndexError):
            raise ValueError(f"Could not parse time input: {input_str}")

    # Add all accumulated time to the base_date
    return base_date.add(**total_time)

def format_relative_time(target_date: pendulum.DateTime, base_date: pendulum.DateTime = None) -> str:
    """
    Formats the difference between two dates into a detailed relative human-readable string.
    
    :param target_date: The future or past datetime to describe.
    :param base_date: The base datetime to calculate the difference from (defaults to now).
    :return: A string like "2 Jahren, 4 Monaten und 5 Tagen".
    """
    if base_date is None:
        base_date = pendulum.now()

    # Calculate the difference
    diff = base_date.diff(target_date)

    # Extract the components (ensure positive values)
    years = abs(diff.years)
    months = abs(diff.months)
    days = abs(diff.days - years * 365 - months * 30)  # Remaining days after years/months
    hours = abs(diff.hours)
    minutes = abs(diff.minutes)

    # Build a human-readable string
    parts = []
    if years:
        parts.append(f"{years} Jahr{'en' if years > 1 else ''}")
    if months:
        parts.append(f"{months} Monat{'en' if months > 1 else ''}")
    if days:
        parts.append(f"{days} Tag{'en' if days > 1 else ''}")
    if hours:
        parts.append(f"{hours} Stunde{'n' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} Minute{'n' if minutes != 1 else ''}")

    # Join the parts into a readable format
    if not parts:
        return "0 Minuten"  # Default if no differences
    result = ", ".join(parts[:-1]) + (f" und {parts[-1]}" if len(parts) > 1 else parts[0])
    return result

def save_reminder_request(user, referenced_message, channel_id, date, reason):
    user_id = user.id
    payload = (notion.NotionPayloadBuilder()
        .add_title(DB_FIELD_MESSAGE, str(referenced_message))
        .add_date(DB_FIELD_DATE, start=date)
        .add_text(DB_FIELD_CHANNEL, str(channel_id))
        .add_text(DB_FIELD_USER, str(user_id))
        .add_text(DB_FIELD_REASON, reason)
    ).build()
    notion.add_to_database(DB_ID_REMIND_ME, payload)

class MyModal(discord.ui.Modal):
    def __init__(self, user=None, message_id=None, channel_id=None, *args, **kwargs):
        super().__init__(
            discord.ui.InputText(
                label="Wann soll ich dich erinnern? In...",
                placeholder="2 stunden - 1 minute - 5 jahre 2 monate 2 minuten"
            ),
            discord.ui.InputText(
                label="Grund",
                placeholder="Eine Gedächtnisstütze, warum du erinnert werden wolltest",
                required=False,
            ),
            *args,
            **kwargs
        )

        self.user:discord.Member = user
        self.channel_id = channel_id
        self.message_id = message_id
    
    async def callback(self, interaction: discord.Interaction):
        user_input = self.children[0].value
        if user_input:
            now = pendulum.now()
            parsed_date = parse_time_input(user_input, base_date=now)
            relative_time = format_relative_time(parsed_date, base_date=now)

            reason = self.children[1].value
            save_reminder_request(self.user, self.message_id, self.channel_id, parsed_date, reason)

            await interaction.respond(f"{self.user.mention}, ich werde dich in {relative_time} erinnern (am {parsed_date.strftime("%A, %d. %B %Y")} um {parsed_date.strftime("%H:%M:%S")})", ephemeral=True)
        else:
            await interaction.respond(r"Ohne Angaben kann ich nichts machen ¯\_(ツ)_/¯", ephemeral=True)

class RemindMe(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @message_command(name="Erinnere mich in/am ...")
    async def remind_me(self, ctx:EzContext, message:Message):
        # get message id
        # save user that used the interaction
        # save to database
        modal = MyModal(title = "Erstellung einer Erinnerung", user=ctx.author, message_id=message.id, channel_id=message.channel.id)
        await ctx.send_modal(modal)

    async def send_reminder_message(self, message_id, channel_id, user_id, reason=None):
        bot = self.bot
        try:
            # Fetch the channel
            channel = bot.get_channel(channel_id)
            if channel is None:
                print("couln't get channel. Fetching channel")
                channel = await bot.fetch_channel(channel_id)
            
            if channel is None:
                print("Fetching channel also didn't return a channel back")
            
            print(f"have channel: {channel.name}")

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

            content = f"Hey, du wolltest an diese Nachricht erinnert werden {message.jump_url}\nGrund: {reason}"
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
            print(f"Message forwarded to {user.name} via DM.")
            return True
        except discord.NotFound:
            print("Message or channel not found!")
        except discord.Forbidden as e:
            print("I don't have permission to access this channel or DM the user!")
            print(e)
        except discord.HTTPException as e:
            print(f"An error occurred: {e}")
        return False

    @commands.Cog.listener()
    async def on_ready(self):
        self.check_reminders.start()

    @tasks.loop(seconds=60)
    async def check_reminders(self):
        filter = (notion.NotionFilterBuilder()
                  .add_date_filter(property_name=DB_FIELD_DATE, value=datetime.now().strftime("%Y-%m-%d"), condition=notion.DateCondition.ON_OR_BEFORE)
                  .build())
        entries = notion.get_all_entries(DB_ID_REMIND_ME, filter=filter)
        for entry in entries:
            my_entry = notion.Entry(entry)
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
