import discord
from discord.ext import commands, tasks
import os
from ezcord import log
from ezcord.times import dc_timestamp, set_utc, convert_dt
from notion_client import Client
from datetime import datetime
import logging
import googlemaps
import json

state_tags = json.loads(os.getenv("STATE_TAGS", "{}"))

class Event():
    def __init__(self, cog, properties, public_url):
        if properties['Event Titel']['title']:
            title = properties['Event Titel']['title'][0]['plain_text']
        
        freitext = None
        if properties['Freitext']['rich_text']:
            freitext = properties['Freitext']['rich_text'][0]['plain_text']

        if properties['Format(e)']['multi_select']:
            format = properties['Format(e)']['multi_select'][0]['name']

        if properties['Start (und Ende)']['date']:
            start = properties['Start (und Ende)']['date']['start']
            end = properties['Start (und Ende)']['date']['end']

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

        geocode_results = cog.gmaps.geocode(location, language='de')
        country = [obj for obj in geocode_results[0]['address_components'] if 'country' in obj.get('types', [])][0]
        country_short = country['short_name']
        if country_short == 'DE':
            state = [obj for obj in geocode_results[0]['address_components'] if 'administrative_area_level_1' in obj.get('types', [])][0]
            state_short = state['short_name']
            self.tag_name = state_tags[state_short]
        else:
            self.tag_name = state_tags[country_short]

        self.content = f"""Format(e): {format}
Datum: <t:{int(start_datetime.timestamp())}:f> (<t:{int(start_datetime.timestamp())}:R>) {" bis <t:"+str(int(datetime.fromisoformat(end).timestamp()))+":f>" if end else ""}
Startgebühr: {f"{entry_fee} €" if entry_fee else "-"}
URL: {url}{f"\n\n{freitext}" if freitext else ""}

{f"[{location}](https://www.google.com/maps/search/{location.replace(' ', '%20')})"}

[Notion Eintrag]({public_url})"""
        
        self.title = f"{start_datetime.strftime("%d.%m.%Y")} - {f"{format} {event_type}" if event_type else f"{format} {title}"} @ {store} in {city}"

        self.embeds = []
        self.embeds.append(discord.Embed(image=f"https://www.google.com/s2/favicons?sz=256&domain_url={url}", fields=[discord.embeds.EmbedField(name="URL", value=url)]))
        self.embeds.append(discord.Embed(image=f"https://www.google.com/s2/favicons?sz=256&domain_url=https://www.notion.so/", fields=[discord.embeds.EmbedField(name="URL", value=public_url)]))

class NotionMonitor(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.notion = Client(auth=os.environ["NOTION_TOKEN"])
        self.database_id = os.environ["DATABASE_ID"]
        self.channel_paper_event_id = int(os.getenv("CHANNEL_PAPER_EVENTS"))

        self.gmaps:googlemaps.Client = googlemaps.Client(key=os.environ["GMAPS_TOKEN"])

    @commands.Cog.listener()
    async def on_ready(self):
        self.check.start()

    @tasks.loop(seconds=60)
    async def check(self):
        today = datetime.now().strftime("%Y-%m-%d")
        response = self.notion.databases.query(
            **{
                    "database_id": self.database_id,
                    "filter": {
                        "property": "Start (und Ende)",
                        "date": {
                            "on_or_after": today,
                        },
                    },
                }
        )
        for entry in response['results']:
            properties = entry['properties']
            discord_url = properties['Discord Link']
            if discord_url['url']:
                # already posted
                continue
            else:
                public_url = entry['public_url']
                event = Event(self, properties, public_url)

                paper_event_channel:discord.ForumChannel = self.bot.get_channel(self.channel_paper_event_id)

                tag = discord.utils.get(paper_event_channel.available_tags, name=event.tag_name)

                forum_post = await paper_event_channel.create_thread(name=event.title, content=event.content, embeds=None, applied_tags=[tag])
                discord_link = forum_post.jump_url
                logging.getLogger("link_logger").info(f"Created forum post: {discord_link}")
                update_response = self.notion.pages.update(
                    **{
                        "page_id": entry['id'],
                        "properties": {
                            "Discord Link": {  # Replace with the name of your property
                                "url": discord_link
                            }
                        }
                    }
                )
                logging.getLogger("link_logger").info(f"Updated Notion page: {update_response['url']}")

def setup(bot: discord.Bot):
    bot.add_cog(NotionMonitor(bot))
