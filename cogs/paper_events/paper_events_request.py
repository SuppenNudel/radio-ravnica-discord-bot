from ezcord import Cog, log
import discord
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Embed, Color
from modules import gmaps, env, paper_events_db
from datetime import datetime
import os
from discord.utils import format_dt

gmaps_token = os.getenv("GMAPS_TOKEN")

class PaperEventsRequest(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")
        
    def get_paper_events(self, plz, land):
        conn = paper_events_db.connect()
        paper_events_db.init_db(conn)
        events_list = paper_events_db.list_upcoming_events(
            conn, server_id=str(env.GUILD_ID)
        )

        events = {}
        destinations = []
        for row in events_list:
            address = row.location_address or f"{row.location_name or ''} {row.location_city or ''}".strip()
            events[row.id] = {
                "address": address,
                "row": row,
            }
            destinations.append(address)

        if not destinations:
            return events

        results = gmaps.get_distances(f"{plz} {land if land else ''}", destinations)

        for address, data in results.items():
            for event_id, event in events.items():
                if event["address"] == address:
                    event["distance"] = data["distance"]
                    event["duration"] = data["duration"]

        return events
    
    def events_to_ascii_table(self, events):
        # Define the table headers
        headers = ["Event Titel", "Adresse", "Entfernung", "Dauer"]
        table = [headers]

        # Add each event to the table
        for event in events.values():
            row: paper_events_db.EventRow = event["row"]
            table_row = [
                row.title,
                event["address"],
                event["distance"]["text"],
                event["duration"]["text"],
            ]
            table.append(table_row)

        # Calculate column widths
        col_widths = [max(len(str(item)) for item in col) for col in zip(*table)]

        # Create the ASCII table
        ascii_table = ""
        for row in table:
            ascii_table += " | ".join(f"{item:<{col_widths[i]}}" for i, item in enumerate(row)) + "\n"
            if row == headers:
                ascii_table += "-+-".join("-" * col_widths[i] for i in range(len(headers))) + "\n"

        return ascii_table
    
    def events_to_embeds(self, events):
        embeds = []

        for event in events.values():
            row: paper_events_db.EventRow = event["row"]
            title = row.title
            address = event["address"]
            start_datetime = datetime.fromisoformat(row.start_at)
            distance = event["distance"]["text"]
            duration = event["duration"]["text"]

            start = f"{format_dt(start_datetime, style='F')}\n{format_dt(start_datetime, style='R')}"

            embed = Embed(title=title, color=env.RR_GREEN, url=row.url)
            embed.add_field(name="Start", value=start, inline=False)
            embed.add_field(name="Adresse", value=address, inline=False)
            embed.add_field(name="Entfernung", value=distance, inline=True)
            embed.add_field(name="Fahrtzeit", value=duration, inline=True)

            embeds.append(embed)

        return embeds

    @slash_command(
        description="Finde Veranstaltungen in deiner Nähe",
        guild_ids=[env.GUILD_ID]
    )
    async def events_in_meiner_nähe(self, ctx:ApplicationContext, plz:int, land:str="Deutschland"):
        initial_response = await ctx.respond(
            f"Ich schicke dir eine Privatnachricht, wenn ich soweit bin.",
            ephemeral=True
        )
        if type(initial_response) == discord.Interaction:
            initial_response_casted:discord.Interaction = initial_response
        try:
            dm_channel = await ctx.user.create_dm()
            
            paper_events = self.get_paper_events(plz, land)

            # Sort paper_events by distance
            sorted_paper_events = sorted(paper_events.items(), key=lambda item: item[1]['distance']['value'])

            # Convert back to dictionary if needed
            sorted_paper_events = dict(sorted_paper_events)

            # asciitable = self.events_to_ascii_table(sorted_paper_events)
            # message = await dm_channel.send(f"```{asciitable}```")

            embeds = self.events_to_embeds(sorted_paper_events)

            # Send embeds in batches of 10
            batch_size = 10
            for i in range(0, len(embeds), batch_size):
                batch = embeds[i:i + batch_size]
                last_message = await dm_channel.send("Hier sind alle kommenden Events nach Entfernung sortiert:", embeds=batch)

            await initial_response_casted.edit_original_response(content=f"Ich habe dir die Veranstaltungen per Privatnachricht geschickt. {last_message.jump_url}")
        except Exception as e:
            await initial_response_casted.edit_original_response(content=f"An error occurred: {e}")

def setup(bot:Bot):
    bot.add_cog(PaperEventsRequest(bot))
