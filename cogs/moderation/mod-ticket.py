from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord.commands import option, slash_command
import discord
from discord import Bot
import logging, os
from modules import env

link_log = logging.getLogger("link_logger")

MOD_TICKETS_NAME = "Mod Tickets"
MOD_TICKETS_ARCHIVE_NAME = "Mod Tickets Archiv"

class ModTicket(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(env.GUILD_ID)
        if guild:
            moderator_role = discord.utils.get(guild.roles, name="Moderator")
            if not moderator_role:
                log.error(f"'Moderator' Rolle nicht gefunden im Guild '{guild.name}' ({guild.id}). Bitte erstelle die Rolle.")
                return
            to_be_created_categories = [MOD_TICKETS_NAME, MOD_TICKETS_ARCHIVE_NAME]

            for category_name in to_be_created_categories:
                # Remove overwrites from category creation, create with no overwrites first
                mod_tickets_category = discord.utils.get(guild.categories, name=category_name)
                if not mod_tickets_category:
                    # Kategorie existiert nicht, also erstellen
                    try:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(view_channel=False),
                            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
                        }
                        for role in guild.roles:
                            if role.name in ["Admin", "Moderator"]:
                                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
                        mod_tickets_category = await guild.create_category(
                            category_name,
                            overwrites=overwrites
                        )
                    except discord.errors.Forbidden as e:
                        log.error(f"Dem Bot fehlen Berechtigungen, um die '{category_name}' Kategorien zu erstellen: {e}")
                        return # Exit if we can't create categories
                    
                    # Check if bot has permission to manage channels in this category
                    bot_member = guild.me
                    perms = mod_tickets_category.permissions_for(bot_member)
                    if not perms.manage_channels:
                        log.warning(f"Dem Bot fehlt die Berechtigung 'manage_channels' in der Kategorie '{category_name}'.")
            
                    
        log.info(self.__class__.__name__ + " is ready")

    @slash_command(guild_ids=[env.GUILD_ID], description="Erstellt einen Kanal als Ticket, in dem du dein Anliegen diskutieren kannst.")
    async def mods_kontaktieren(self, ctx:discord.commands.ApplicationContext):
        # Open modal for title and description
        await ctx.send_modal(ModTicketModal())

    @slash_command(guild_ids=[env.GUILD_ID], description="Schließt das Ticket und verschiebt es ins Archiv.")
    @option(name="grund", input_type=str)
    async def ticket_schliessen(self, ctx:discord.commands.ApplicationContext, grund:str):
        message:discord.interactions.Interaction = await ctx.respond("⏳ Ich verarbeite deine Anfrage...", ephemeral=True)
        ticket = ctx.channel
        if ticket.category and ticket.category.name == MOD_TICKETS_ARCHIVE_NAME:
            await message.edit(content="⚠️ Das Ticket ist bereits im Archiv.")
            return
        if not ticket.category or ticket.category.name != MOD_TICKETS_NAME:
            await message.edit(content="🚫 Dies ist kein gültiges Mod-Ticket.")
            return
        if ticket.category and ticket.category.name == MOD_TICKETS_NAME:
            try:
                await ticket.send(f"Ticket geschlossen von {ctx.user.mention} | Grund: {grund}")
                # Remove permissions for everyone except "Moderator" or "Admin" roles
                archive_category = discord.utils.get(ticket.guild.categories, name=MOD_TICKETS_ARCHIVE_NAME)
                if archive_category:
                    await ticket.edit(category=archive_category)
                else:
                    await ticket.send(f"🚫 {MOD_TICKETS_ARCHIVE_NAME} nicht gefunden. Ticket bleibt in der aktuellen Kategorie.")
                overwrites = {}
                for role in ticket.guild.roles:
                    if role.name in ["Moderator", "Admin"]:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
                    else:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=False)
                await ticket.edit(overwrites=overwrites)
                await message.edit(content="✅ Ticket wurde geschlossen und ins Archiv verschoben sowie die Berechtigungen angepasst.")
            except discord.errors.Forbidden as e:
                await message.edit(content=f"🚫 Dem Bot fehlen Berechtigungen, um den Kanal zu schließen: {e}")

class ModTicketModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Mod-Ticket erstellen")
        self.title_input = discord.ui.InputText(
            label="Titel",
            placeholder="Kurzer Titel für dein Anliegen",
            required=True,
            max_length=100
        )
        self.add_item(self.title_input)
        self.desc_input = discord.ui.InputText(
            label="Beschreibung",
            placeholder="Beschreibe dein Anliegen",
            required=True,
            style=discord.InputTextStyle.long,
            max_length=1000
        )
        self.add_item(self.desc_input)

    async def callback(self, interaction: discord.Interaction):
        titel = self.title_input.value
        beschreibung = self.desc_input.value

        guild = interaction.guild
        category = discord.utils.get(guild.categories, name=MOD_TICKETS_NAME)
        if not category:
            await interaction.response.send_message(f"🚫 '{MOD_TICKETS_NAME}' Kategorie nicht gefunden.", ephemeral=True)
            return

        try:
            channel = await guild.create_text_channel(
                f"{interaction.user.name}-{titel}",
                category=category
            )
        except discord.errors.Forbidden as e:
            await interaction.response.send_message(f"🚫 Dem Bot fehlen Berechtigungen, um den Kanal zu erstellen: {e}", ephemeral=True)
            return
        try:
            await channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        except discord.errors.Forbidden as e:
            await interaction.response.send_message(f"🚫 Dem Bot fehlen Berechtigungen, um die Kanal-Berechtigungen zu setzen: {e}", ephemeral=True)
            return

        moderator_role = discord.utils.get(guild.roles, name="Moderator")
        if not moderator_role:
            await interaction.response.send_message(f"🚫 'Moderator' Rolle nicht gefunden. Bitte kontaktiere einen Admin.", ephemeral=True)
            return
        await channel.send(f"{moderator_role.mention}, {interaction.user.mention} hat ein Ticket erstellt:\n**Titel:** {titel}\n**Beschreibung:**\n{beschreibung}")
        await interaction.response.send_message(f"✅ Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)

def setup(bot:Bot):
    bot.add_cog(ModTicket(bot))
