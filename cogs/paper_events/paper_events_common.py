import discord
from datetime import datetime
from typing import Literal
import discord
from modules import gmaps, env

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

class PaperEvent():
    def __init__(
            self,
            author:discord.Member,
            title=None,
            format=None,
            event_type=None,
            formats=None,
            url=None,
            location:gmaps.Location|None=None
        ):
        self.title = title
        self.format = format
        self.event_type = event_type
        self.author = author
        self.formats = formats
        self.url = url
        self.location = location

    def build_title(self):
        if self.title:
            return self.title
        else:
            return f"{self.format} {self.event_type}"
        
    def construct_content(self):
        pass

    def construct_gmaps_embed(self) -> discord.Embed|None:
        if not self.location:
            return None
        embed = discord.Embed(
            color=env.RR_GREEN,
            title="Google Maps",
            url=f"https://www.google.com/maps/search/{self.location.formatted_address.replace(' ', '%20')}",
            # image=f"attachment://{file_path}",
            fields=[
                # discord.EmbedField(name="Laden", value=store, inline=True),
                discord.EmbedField(name="Adresse", value=self.location.formatted_address, inline=True)
            ]
        )
        return embed

    def construct_event_embed(self):
        embed = discord.Embed(
            title=self.build_title(),
            color=env.RR_GREEN,
            url=self.url
        )
        return embed

    async def make_forum_post(self, channel:discord.ForumChannel):
        tag = discord.utils.get(channel.available_tags, name=event.tag_name)
        forum_post = await channel.create_thread(
            name=self.title,
            content=event.content,
            embeds=[self.construct_event_embed(), self.construct_gmaps_embed()],
            applied_tags=[tag],
            files=event.files
        )
