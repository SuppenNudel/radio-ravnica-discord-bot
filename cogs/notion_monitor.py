import discord
from discord.ext import commands, tasks
import os
from ezcord import log
from discord import Bot
from discord.utils import format_dt
import googlemaps.client
from notion_client import Client
from datetime import datetime
import logging
import googlemaps
import json
from typing import Literal
import requests
import modules.notion as notion
import modules.favicon as favicon

state_tags = json.loads(os.getenv("STATE_TAGS", "{}")) # {} is the default

# Retrieve the boolean value
def get_bool_from_env(key: str, default: bool = False) -> bool:
    value = os.getenv(key, str(default)).lower()  # Default to 'false' if key is not found
    return value in ("true", "1", "yes", "on")

def get_timestamp_style(timestamp1: datetime, timestamp2: datetime) -> Literal["t", "F", "D"]:
    """
    Checks if two timestamps are on the same date.
    Returns "t" for short time if on the same date,
    otherwise returns "F" for long date and time.
    """
    if timestamp1.date() == timestamp2.date():
        return "t"  # Same date: short time
    else:
        return "D"  # DiDferent dates: long date and time

class NotionMonitor(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        guild_id = os.getenv("GUILD")
        if not guild_id:
            raise Exception(".env/GUILD not defined")
        self.guild_id = int(guild_id)
        self.notion = Client(auth=os.getenv("NOTION_TOKEN"))
        self.event_database_id = os.getenv("EVENT_DATABASE_ID")
        if not self.event_database_id:
            raise Exception(".env/EVENT_DATABASE_ID not defined")
        
        self.area_database_id = os.getenv("AREA_DATABASE_ID")
        
        channel_paper_events = os.getenv("CHANNEL_PAPER_EVENTS")
        if not channel_paper_events:
            raise Exception(".env/CHANNEL_PAPER_EVENTS not defined")
        self.channel_paper_event_id = int(channel_paper_events)
        self.gmaps_token = os.getenv("GMAPS_TOKEN")
        self.gmaps:googlemaps.client.Client = googlemaps.Client(key=self.gmaps_token)

    @commands.Cog.listener()
    async def on_ready(self):
        self.paper_event_channel = self.bot.get_channel(self.channel_paper_event_id)
        if not type(self.paper_event_channel) == discord.ForumChannel:
            raise Exception(f"Channel is not of type Forum Channel, but {type(self.paper_event_channel)}")
        if not self.check.is_running():
            self.check.start()

    @tasks.loop(minutes=5)
    async def check(self): # checks for entries that need to be posted on discord
        if not type(self.paper_event_channel) == discord.ForumChannel:
            raise Exception(f"Channel is not of type Forum Channel, but {type(self.paper_event_channel)}")#
        
        if not self.event_database_id:
            raise Exception("event_database_id not defined")
        
        today = datetime.now().strftime("%Y-%m-%d")
        filter = (
            notion.NotionFilterBuilder()
            .add_date_filter("Start (und Ende)", notion.DateCondition.ON_OR_AFTER, today)
            .add_url_filter("Link", notion.URLCondition.IS_EMPTY, True)
        ).build()
        entries = notion.get_all_entries(database_id=self.event_database_id, filter=filter)
        for entry in entries:
            my_entry = notion.Entry(entry)
            debug = get_bool_from_env('DEBUG')
            is_test_entry = my_entry.get_checkbox_property("For Test")
            if (debug and is_test_entry) or ((not debug) and (not is_test_entry)):
                event = Event(self, my_entry)

                if not event.author:
                    # reject
                    update_properties = notion.NotionPayloadBuilder().add_status("Status", "Author not found").build()
                    update_response = notion.update_entry(my_entry.id, update_properties=update_properties)
                    continue

                tag = discord.utils.get(self.paper_event_channel.available_tags, name=event.tag_name)

                log.debug(f"Going to create post in paper_event_channe: {[event.title, event.content, event.embeds, [tag], event.files]}")
                forum_post = await self.paper_event_channel.create_thread(name=event.title, content=event.content, embeds=event.embeds, applied_tags=[tag], files=event.files)
                channel_id = forum_post.id
                discord_link = forum_post.jump_url
                logging.getLogger("link_logger").info(f"Created forum post: {discord_link}")
                update_properties = (
                    notion.NotionPayloadBuilder()
                    .add_text("Thread ID", str(channel_id))
                    .add_text("Server ID", str(self.guild_id))
                    .add_status("Status", "on Discord")
                )
                if event.area_page_id:
                    update_properties.add_relation("(Bundes)land", event.area_page_id)
                update_response = notion.update_entry(page_id=my_entry.id, update_properties=update_properties.build())
                logging.getLogger("link_logger").info(f"Updated Notion page: {update_response['url']}")

class Event():
    def __init__(self, cog:NotionMonitor, entry:notion.Entry):
        author = entry.get_text_property("Author")
        log.info(f"Author Value: {author}")
        guild:discord.Guild | None = cog.bot.get_guild(cog.guild_id)
        log.info(f"Guild : {guild}")
        if guild:
            self.author = guild.get_member_named(author)
            log.info(f"Author: {self.author}")
        else:
            raise Exception(f"Guild not found {cog.guild_id}")
        
        # verify
        if not self.author:
            return
        
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

        geocode_results = cog.gmaps.geocode(location, language='de')
        country = [obj for obj in geocode_results[0]['address_components'] if 'country' in obj.get('types', [])][0]
        country_short = country['short_name']
        area_name = country['long_name']
        self.tag_name = "nicht DACH"
        if country_short == 'DE':
            state = [obj for obj in geocode_results[0]['address_components'] if 'administrative_area_level_1' in obj.get('types', [])][0]
            state_short = state['short_name']
            area_name = state['long_name']
            if state_short in state_tags:
                self.tag_name = state_tags[state_short]
        else:
            if country_short in state_tags:
                self.tag_name = state_tags[country_short]
        
        geo_city = [obj for obj in geocode_results[0]['address_components'] if 'locality' in obj.get('types', [])][0]
        geo_city_long_name = geo_city['long_name']
        filter = notion.NotionFilterBuilder().add_text_filter("Name", notion.TextCondition.EQUALS, area_name).build()
        area_response = notion.get_all_entries(database_id=cog.area_database_id, filter=filter)

        self.area_page_id = None
        if area_response:
            self.area_page_id = area_response[0]['id']
        
        if len(formate) == 1:
            self.title = f"{start_datetime.strftime("%d.%m.%Y")} - {f"{formate[0]} {event_type}" if event_type else f"{formate[0]} {title}"} @ {store} in {geo_city_long_name}"
        else:
            self.title = f"{start_datetime.strftime("%d.%m.%Y")} - {f"{title} + {event_type}" if event_type else title} @ {store} in {geo_city_long_name}"

        self.content = ""
        if freitext:
            quoted_freitext = '\n'.join([f"> {line}" for line in freitext.split('\n')])
            self.content = f"{quoted_freitext}\n\n"
        self.content = f"{self.content}Danke an {self.author.mention} für's Posten"
        self.embeds = []
        fields = []
        fields.append(discord.EmbedField(name="Start", value=f"{format_dt(start_datetime, style="F")}\n{format_dt(start_datetime, style="R")}", inline=True))
        if end_datetime and end_datetime != start_datetime:
            fields.append(discord.EmbedField(name="Ende", value=format_dt(end_datetime, style=get_timestamp_style(start_datetime, end_datetime)), inline=True))
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
                file_thumb = discord.File("icon.png", filename="icon.png")
            else:
                thumbnail_url = "https://cards.scryfall.io/art_crop/front/e/c/ec8e4142-7c46-4d2f-aaa6-6410f323d9f0.jpg"
        if thumbnail_url:
            event_embed.set_thumbnail(url=thumbnail_url)

        self.embeds.append(event_embed)
        # self.embeds.append(discord.Embed(title=title, image="https://upload.wikimedia.org/wikipedia/commons/4/45/Notion_app_logo.png", url=public_url))

        location_coord = geocode_results[0]['geometry']['location']
        long = location_coord['lng']
        lat = location_coord['lat']
        google_map_url = f"https://maps.googleapis.com/maps/api/staticmap?center=50.6,11&zoom=6&size=600x640&markers=color:red%257label:S%7C{lat},{long}&language=de&key={cog.gmaps_token}"

        response = requests.get(google_map_url)
    
        # Save the file locally
        file_path = "google_map.png"
        with open(file_path, "wb") as file_maps:
            file_maps.write(response.content)
        
        # Create a discord.File object from the downloaded file
        file_maps = discord.File(file_path, filename=file_path)

        self.files = [file_maps]
        if file_thumb:
            self.files.append(file_thumb)
        google_embed = discord.Embed(
                title="Google Maps",
                url=f"https://www.google.com/maps/search/{location.replace(' ', '%20')}",
                image=f"attachment://{file_path}",
                fields=[
                    discord.EmbedField(name="Laden", value=store, inline=True),
                    discord.EmbedField(name="Adresse", value=geocode_results[0]['formatted_address'], inline=True)
                ]
            )
        
        self.embeds.append(google_embed)

def setup(bot: Bot):
    bot.add_cog(NotionMonitor(bot))
