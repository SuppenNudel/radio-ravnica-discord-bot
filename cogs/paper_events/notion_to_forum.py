import discord
from discord.ext import commands, tasks
import os
from ezcord import log
from discord import Bot
from datetime import datetime
import logging
from typing import Literal
import requests
import modules.notion as notion
import modules.favicon as favicon
from modules import gmaps, env, ics

link_log = logging.getLogger("link_logger")

STATE_TAGS = env.STATE_TAGS
DEBUG = env.DEBUG
GUILD_ID = env.GUILD_ID
EVENT_DATABASE_ID = env.EVENT_DATABASE_ID
AREA_DATABASE_ID = env.AREA_DATABASE_ID
CHANNEL_PAPER_EVENTS_ID = env.CHANNEL_PAPER_EVENTS_ID

def get_timestamp_style(timestamp1: datetime, timestamp2: datetime) -> Literal["t", "D"]:
    """
    Checks if two timestamps are on the same date.
    Returns "t" for short time if on the same date,
    otherwise returns "D" for long date and time.
    """
    if timestamp1.date() == timestamp2.date():
        return "t"  # Same date: short time
    else:
        return "D"  # Different dates: long date and time

class PaperEventsNotionToForum(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        log.debug(f"Readying {self.__class__.__name__}...")
        
        self.paper_event_channel:discord.ForumChannel = await discord.utils.get_or_fetch(self.bot, "channel", CHANNEL_PAPER_EVENTS_ID)
        if not type(self.paper_event_channel) == discord.ForumChannel:
            raise Exception(f"Channel is not of type Forum Channel, but {type(self.paper_event_channel)}")
        
        self.guild = await discord.utils.get_or_fetch(self.bot, "guild", GUILD_ID)

        if not self.check.is_running():
            self.check.start()

    @tasks.loop(minutes=5)
    async def check(self): # checks for entries that need to be posted on discord        
        today = datetime.now()
        filter = (
            notion.NotionFilterBuilder()
            .add_date_filter("Start (und Ende)", notion.DateCondition.ON_OR_AFTER, today)
            .add_url_filter("Link", notion.URLCondition.IS_EMPTY, True)
        ).build()
        entries = notion.get_all_entries(database_id=EVENT_DATABASE_ID, filter=filter)
        for entry in entries:
            try:
                is_test_entry = entry.get_checkbox_property("For Test")
                if (DEBUG and is_test_entry) or ((not DEBUG) and (not is_test_entry)):
                    author_text = entry.get_text_property("Author")
                    if not author_text:
                        raise Exception(f"No Author value")
                    author = self.guild.get_member_named(author_text)
                    if not author:
                        # reject
                        update_properties = notion.NotionPayloadBuilder().add_status("Status", "Author not found").build()
                        update_response = notion.update_entry(entry.id, update_properties=update_properties)
                        log.error(f"Author not found: {author_text}")
                        continue

                    event = PaperEvent(entry, author)
                    tag = discord.utils.get(self.paper_event_channel.available_tags, name=event.tag_name)

                    log.debug(f"Going to create post in paper_event_channe: {[event.title, event.content, event.embeds, [tag], event.files]}")
                    forum_post = await self.paper_event_channel.create_thread(name=event.title, content=event.content, embeds=event.embeds, applied_tags=[tag], files=event.files)
                    channel_id = forum_post.id
                    discord_link = forum_post.jump_url
                    link_log.info(f"Created forum post: {discord_link}")
                    update_properties = (
                        notion.NotionPayloadBuilder()
                        .add_text("Thread ID", str(channel_id))
                        .add_text("Server ID", str(GUILD_ID))
                        .add_status("Status", "on Discord")
                    )
                    if event.area_page_id:
                        update_properties.add_relation("(Bundes)land", event.area_page_id)
                    update_response = notion.update_entry(page_id=entry.id, update_properties=update_properties.build())
                    link_log.info(f"Updated Notion page: {update_response['url']}")
            except Exception as e:
                log.error(f"Beim Lesen eines Events aus Notion ist ein Fehler passiert {e.args}")

class PaperEvent():
    async def __init__(self, entry:notion.Entry, author:discord.Member):
        title = entry.get_text_property("Event Titel")
        freitext = entry.get_text_property("Freitext")

        formate = entry.get_multi_select_property("Format(e)")
        formate_joined = ', '.join(formate)

        url = entry.get_url_property("URL")
        custom_image = entry.get_file_property("Eigenes Bild Datei")
        if not custom_image:
            custom_image = entry.get_url_property("Eigenes Bild URL")
        entry_fee = entry.get_number_property("Gebühr")
        location = entry.get_formula_property("Google Maps")
        store = entry.get_text_property("Name des Ladens")
        city = entry.get_text_property("Stadt")
        event_type = entry.get_status_property("Event Typ")

        date = entry.get_date_property("Start (und Ende)")
        start_datetime:datetime = date['start']
        end_datetime:datetime = date['end']

        location = gmaps.get_location(location)
        country = location.country
        country_short = country['short_name']
        area_name = country['long_name']
        self.tag_name = "nicht DACH"
        if country_short == 'DE':
            state = location.state
            state_short = state['short_name']
            area_name = state['long_name']
            if state_short in STATE_TAGS:
                self.tag_name = STATE_TAGS[state_short]
        else:
            if country_short in STATE_TAGS:
                self.tag_name = STATE_TAGS[country_short]
        
        geo_city_long_name = location.city['long_name']
        filter = notion.NotionFilterBuilder().add_text_filter("Name", notion.TextCondition.EQUALS, area_name).build()
        area_response = notion.get_all_entries(database_id=AREA_DATABASE_ID, filter=filter)

        self.area_page_id = None
        if area_response:
            self.area_page_id = area_response[0]['id']
        
        if len(formate) == 1:
            if formate[0] in title:
                thread_title = f"{f'{formate[0]} {event_type}' if event_type else f'{title}'} @ {store} in {geo_city_long_name}"    
            else:
                thread_title = f"{f'{formate[0]} {event_type}' if event_type else f'{formate[0]} {title}'} @ {store} in {geo_city_long_name}"
        else:
            thread_title = f"{f'{title} + {event_type}' if event_type else title} @ {store} in {geo_city_long_name}"

        self.title = f"{start_datetime.strftime('%d.%m.%Y')} - {thread_title}"

        self.content = ""
        if freitext:
            quoted_freitext = '\n'.join([f"> {line}" for line in freitext.split('\n')])
            self.content = f"{quoted_freitext}\n\n"

        self.content += f"Danke an {author.mention} für's Posten!"
        
        ics_file_name = "tmp/event.ics"
        ics.create_ics_file(ics_file_name, title, start_datetime, end_datetime, description=freitext, location=location)

        self.embeds = []
        fields = []
        fields.append(discord.EmbedField(
            name="Start",
            value=f"{discord.utils.format_dt(start_datetime, style='F')}\n{discord.utils.format_dt(start_datetime, style='R')}",
            inline=True
        ))

        if end_datetime and end_datetime != start_datetime:
            fields.append(discord.EmbedField(name="Ende", value=discord.utils.format_dt(end_datetime, style=get_timestamp_style(start_datetime, end_datetime)), inline=True))
        if entry_fee:
            formatted_fee = f"{entry_fee:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            fields.append(discord.EmbedField(name="Startgebühr", value=f"{formatted_fee} €", inline=True))
        fields.append(discord.EmbedField(name="Format(e)", value=formate_joined, inline=False))
        if event_type:
            if type(event_type) == str:
                fields.append(discord.EmbedField(name="Event Typ", value=event_type, inline=False))
            else:
                raise Exception(f"handling for event_type as {type(event_type)} not implemented")

        event_embed = discord.Embed(title=title, fields=fields)
        thumbnail_url = custom_image
        file_thumb = None

        if url:
            event_embed.url = url
        if url and not thumbnail_url:
            thumbnail_url = favicon.get_favicon_url(url)
            if thumbnail_url:
                favicon.convert_ico_to_png(thumbnail_url)
                thumbnail_url = f"attachment://icon.png"
                file_thumb = discord.File("tmp/icon.png", filename="icon.png")
            else:
                # phblthp
                thumbnail_url = "https://cards.scryfall.io/art_crop/front/e/c/ec8e4142-7c46-4d2f-aaa6-6410f323d9f0.jpg"
        if thumbnail_url:
            event_embed.set_thumbnail(url=thumbnail_url)

        self.embeds.append(event_embed)
        # self.embeds.append(discord.Embed(title=title, image="https://upload.wikimedia.org/wikipedia/commons/4/45/Notion_app_logo.png", url=public_url))

        # Create a discord.File object from the downloaded file
        file_maps = discord.File(location.file_path, filename=location.file_name)

        self.files = [file_maps]
        if file_thumb:
            self.files.append(file_thumb)
        google_embed = discord.Embed(
                title="Google Maps",
                url=location.get_search_url(),
                image=f"attachment://{location.file_name}",
                fields=[
                    discord.EmbedField(name="Laden", value=store, inline=True),
                    discord.EmbedField(name="Adresse", value=location.formatted_address, inline=True)
                ]
            )
        
        self.embeds.append(google_embed)
        self.files.append(discord.File(ics_file_name, filename=ics_file_name))

def setup(bot: Bot):
    bot.add_cog(PaperEventsNotionToForum(bot))
