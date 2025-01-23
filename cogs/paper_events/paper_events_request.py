from ezcord import Cog, log
import discord
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, Embed, Color
from modules import gmaps, notion
import os
from discord.utils import format_dt

gmaps_token = os.getenv("GMAPS_TOKEN")

DB_PAPER_EVENTS_ID = "f05d532cf91f4f9cbce38e27dc85b522"

class PaperEventsRequest(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.gmaps = gmaps.DistanceCalculator(gmaps_token=gmaps_token)

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

        
    def get_paper_events(self, plz, land):
        filter = (
            notion.NotionFilterBuilder()
            .add_checkbox_filter("For Test", notion.CheckboxCondition.EQUALS, False)
            .add_number_filter("in ~ Tagen", notion.NumberCondition.GREATER_THAN, 0)
            .build())

        events = {}

        all_entries = notion.get_all_entries(DB_PAPER_EVENTS_ID, filter=filter)
        destinations = []
        for entry in all_entries:
            myEntry = notion.Entry(entry)
            address = myEntry.get_formula_property("Google Maps")
            events[myEntry.id] = {
                "address": address,
                "entry": myEntry
            }
            destinations.append(address)

        results = self.gmaps.get_distances(f"{plz} {land if land else ''}", destinations)

        # Attach distance and duration values to the events
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
            entry = event["entry"]
            row = [
                entry.get_text_property("Event Titel"),
                event["address"],
                event["distance"]["text"],
                event["duration"]["text"]
            ]
            table.append(row)

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
            entry = event["entry"]
            title = entry.get_text_property("Event Titel")
            address = event["address"]
            date = entry.get_date_property("Start (und Ende)")
            distance = event["distance"]["text"]
            duration = event["duration"]["text"]

            start_datetime = date['start']

            start = f"{format_dt(start_datetime, style='F')}\n{format_dt(start_datetime, style='R')}"

            entry:notion.Entry = event["entry"]
            url = entry.get_formula_property("Link")
            embed = Embed(title=title, color=Color.blue(), url=url)
            embed.add_field(name="Start", value=start, inline=False)
            embed.add_field(name="Adresse", value=address, inline=False)
            embed.add_field(name="Entfernung", value=distance, inline=True)
            embed.add_field(name="Fahrtzeit", value=duration, inline=True)

            embeds.append(embed)

        return embeds

    @slash_command(description="Finde Veranstaltungen in deiner Nähe")
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
