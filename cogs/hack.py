from ezcord import log, Cog
from discord import Bot
import discord

class Hack(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is bein readied")
        # Get the forum channel by its ID (replace with your forum channel ID)
        forum_channel:discord.ForumChannel = await discord.utils.get_or_fetch(self.bot, "channel", 1207651082491142154)

        # message = await discord.utils.get_or_fetch(self.bot, "message", 1326543832904499286)
        
        # Fetch the message you want to edit using its ID
        thread = forum_channel.get_thread(1326543832904499286)
        message = await thread.fetch_message(1326543832904499286)
        if message.embeds:
            # Get the first embed in the message
            embed = message.embeds[0]
            
            # Open the file and create a discord.File object
            file = discord.File("tmp/pauper_party_2.jpg", filename="pauper_party_2.jpg")
            gmaps_file = discord.File("tmp/staticmap.png", filename="staticmap.png")
            
            # Modify the embed's thumbnail using the uploaded file URL
            embed.set_thumbnail(url=f"attachment://pauper_party_2.jpg")

            maps_embed = discord.Embed(
                title="Google Maps",
                url="https://www.google.com/maps/search/Schlachthof%20Lahr",
                image=f"attachment://staticmap.png",
                fields=[
                    discord.EmbedField(name="Veranstaltungsort", value="Schlachthof - Jugend & Kultur", inline=False),
                    discord.EmbedField(name="Adresse", value="Dreyspringstraße 16, 77933 Lahr/Schwarzwald", inline=False)
                ]
            )
            
            # Edit the message to update the embed and include the file
            await message.edit(embeds=[embed, maps_embed], files=[file, gmaps_file])


def setup(bot:Bot):
    bot.add_cog(Hack(bot))

