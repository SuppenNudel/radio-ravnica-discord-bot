import discord
from discord.ext import commands, tasks
import os
from ezcord import log
from discord.utils import format_dt
from notion_client import Client
from datetime import datetime
import logging
import googlemaps
import json
from typing import Literal
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

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
    
def get_favicon_url(url):
    try:
        # Make an HTTP request to get the webpage content
        response = requests.get(url)
        response.raise_for_status()  # Check if the request was successful

        # Parse the HTML content of the page
        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for <link> tag with rel="icon" or rel="shortcut icon"
        favicon_link = soup.find('link', rel=lambda rel: rel and 'icon' in rel)

        if favicon_link and 'href' in favicon_link.attrs:
            # Get the href attribute (favicon URL)
            favicon_url = favicon_link['href']
            # Resolve relative URLs to absolute
            return urljoin(url, favicon_url)
        else:
            return None  # No favicon found

    except requests.exceptions.RequestException as e:
        print(f"Error fetching the URL: {e}")
        return None

class Event():
    def __init__(self, cog, properties, public_url):
        self.author = None
        if properties['Author']['rich_text']:
            author_value = properties['Author']['rich_text'][0]['plain_text']
            log.info(f"Author Value: {author_value}")
            guild_id = int(os.getenv("GUILD"))
            log.info(f"Guild ID: {guild_id}")
            guild:discord.Guild | None = cog.bot.get_guild(guild_id)
            log.info(f"Guild : {guild}")
            if guild:
                self.author = guild.get_member_named(author_value)
                log.info(f"Author: {self.author}")
        
        # verify
        if not self.author:
            return

        if properties['Event Titel']['title']:
            title = properties['Event Titel']['title'][0]['plain_text']
        
        freitext = ""
        if properties['Freitext']['rich_text']:
            freitext = properties['Freitext']['rich_text'][0]['plain_text']

        formate = []
        formate_joined = ""
        if properties['Format(e)']['multi_select']:
            formate = [entry['name'] for entry in properties['Format(e)']['multi_select']]
            formate_joined = ', '.join(formate)

        if properties['Start (und Ende)']['date']:
            start = properties['Start (und Ende)']['date']['start']
            end = properties['Start (und Ende)']['date']['end']

        url = None
        if properties['URL']['url']:
            url = properties['URL']['url']

        entry_fee = None
        if properties['Gebühr']['number']:
            entry_fee = properties['Gebühr']['number']

        if properties['Google Maps']['formula']:
            location = properties['Google Maps']['formula']['string']

        if properties['Name des Ladens']['rich_text']:
            store = properties['Name des Ladens']['rich_text'][0]['plain_text']

        if properties['Stadt']['rich_text']:
            city = properties['Stadt']['rich_text'][0]['plain_text']

        event_type = None
        if properties['Event Typ']['select']:
            event_type = properties['Event Typ']['select']['name']

        start_datetime = datetime.fromisoformat(start)
        end_datetime = None
        if end:
            end_datetime = datetime.fromisoformat(end)

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

        area_response = cog.notion.databases.query(
            database_id=cog.area_database_id,
            filter={
                "property": "Name",
                "rich_text": {
                    "equals": area_name
                }
            }
        )
        self.area_page_id = None
        area_response_results = area_response['results']
        if area_response_results:
            self.area_page_id = area_response_results[0]['id']
        
        if len(formate) == 1:
            self.title = f"{start_datetime.strftime("%d.%m.%Y")} - {f"{formate[0]} {event_type}" if event_type else f"{formate[0]} {title}"} @ {store} in {city}"
        else:
            self.title = f"{start_datetime.strftime("%d.%m.%Y")} - {f"{title} + {event_type}" if event_type else title} @ {store} in {city}"

        self.content = ""
        if freitext:
            quoted_freitext = '\n'.join([f"> {line}" for line in freitext.split('\n')])
            self.content = f"{quoted_freitext}\n\n"
        self.content = f"{self.content}Danke an {self.author.mention} für's Posten"
        self.embeds = []
        fields = []
        fields.append(discord.EmbedField(name="Start", value=f"{format_dt(start_datetime, style="F")}\n{format_dt(start_datetime, style="R")}", inline=True))
        if end_datetime:
            fields.append(discord.EmbedField(name="Ende", value=format_dt(end_datetime, style=get_timestamp_style(start_datetime, end_datetime)), inline=True))
        if entry_fee:
            fields.append(discord.EmbedField(name="Startgebühr", value=f"{entry_fee} €", inline=True))
        fields.append(discord.EmbedField(name="Format(e)", value=formate_joined, inline=False))
        if event_type:
            fields.append(discord.EmbedField(name="Event Typ", value=event_type, inline=False))

        event_embed = discord.Embed(title=title, fields=fields)
        if url:
            event_embed.url = url
            event_embed.thumbnail=get_favicon_url(url)
        self.embeds.append(event_embed)
        # self.embeds.append(discord.Embed(title=title, image="https://upload.wikimedia.org/wikipedia/commons/4/45/Notion_app_logo.png", url=public_url))

        location_coord = geocode_results[0]['geometry']['location']
        long = location_coord['lng']
        lat = location_coord['lat']
        google_map_url = f"https://maps.googleapis.com/maps/api/staticmap?center=50.6,11&zoom=6&size=600x640&markers=color:red%257label:S%7C{lat},{long}&language=de&key={cog.gmaps_token}"

        response = requests.get(google_map_url)
    
        # Save the file locally
        file_path = "google_map.png"
        with open(file_path, "wb") as file:
            file.write(response.content)
        
        # Create a discord.File object from the downloaded file
        file = discord.File(file_path, filename=file_path)

        self.files = [file]
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


class NotionMonitor(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.notion = Client(auth=os.getenv("NOTION_TOKEN"))
        self.event_database_id = os.getenv("EVENT_DATABASE_ID")
        self.area_database_id = os.getenv("AREA_DATABASE_ID")
        self.channel_paper_event_id = int(os.getenv("CHANNEL_PAPER_EVENTS"))
        self.gmaps_token = os.getenv("GMAPS_TOKEN")
        self.gmaps:googlemaps.Client = googlemaps.Client(key=self.gmaps_token)

    @commands.Cog.listener()
    async def on_ready(self):
        self.check.start()

    @tasks.loop(seconds=60)
    async def check(self):
        today = datetime.now().strftime("%Y-%m-%d")
        response = self.notion.databases.query(
            database_id=self.event_database_id,
            filter={
                "property": "Start (und Ende)",
                "date": {
                    "on_or_after": today,
                },
            }
        )
        for entry in response['results']:
            properties = entry['properties']
            discord_url = properties['Discord Link']
            debug = get_bool_from_env('DEBUG')
            if (debug and properties['For Test']['checkbox']) or ((not debug) and (not discord_url['url'])):
                public_url = entry['public_url']
                event = Event(self, properties, public_url)

                if not event.author:
                    # reject
                    update_response = self.notion.pages.update(
                        page_id=entry['id'],
                        properties={
                            "Status": {
                                "type": "status",
                                "status": {
                                    "name": "Author not found"
                                }
                            }
                        }
                    )
                    continue

                paper_event_channel:discord.ForumChannel = self.bot.get_channel(self.channel_paper_event_id)

                tag = discord.utils.get(paper_event_channel.available_tags, name=event.tag_name)

                log.debug(f"Going to create post in paper_event_channe: {[event.title, event.content, event.embeds, [tag], event.files]}")
                forum_post = await paper_event_channel.create_thread(name=event.title, content=event.content, embeds=event.embeds, applied_tags=[tag], files=event.files)
                discord_link = forum_post.jump_url
                logging.getLogger("link_logger").info(f"Created forum post: {discord_link}")

                update_properties = {
                    "Discord Link": {
                        "url": discord_link,
                    },
                    "Status": {
                        "type": "status",
                        "status": {
                            "name": "on Discord"
                        }
                    }
                }
                if event.area_page_id:
                    update_properties['(Bundes)land'] = {
                        "relation": [{"id": event.area_page_id}]
                    }
                update_response = self.notion.pages.update(
                    page_id=entry['id'],
                    properties=update_properties
                )
                logging.getLogger("link_logger").info(f"Updated Notion page: {update_response['url']}")

def setup(bot: discord.Bot):
    bot.add_cog(NotionMonitor(bot))
