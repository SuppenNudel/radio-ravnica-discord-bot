from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, InteractionContextType, IntegrationType
import discord
from modules import notion, env, gmaps
import modules.paper_events_common as pe_common

CHANNEL_PAPER_EVENTS_ID = env.CHANNEL_PAPER_EVENTS_ID
EVENT_DATABASE_ID = env.EVENT_DATABASE_ID
DEBUG = env.DEBUG

SPIKE_ART_CROP = "https://cards.scryfall.io/art_crop/front/b/0/b0e90b22-6f43-4e9a-a236-f33191768813.jpg"

def check_factory(user):
    def check(m:discord.Message|discord.Interaction):
        if type(m) == discord.Message:
            return m.author == user and isinstance(m.channel, discord.DMChannel)
        elif type(m) == discord.Interaction:
            return m.user == user and isinstance(m.channel, discord.DMChannel)
    return check

class SubmitButton(discord.ui.Button):
    """Custom button for submitting the tournament."""
    def __init__(self):
        super().__init__(label="Turnier einsenden", style=discord.ButtonStyle.primary, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        """Handles the submission when clicked."""
        view: EditTourneyView = self.view  # Get the parent view
        view.disable_all_items()
        view.stop()
        await interaction.followup.edit_message(view.message.id, view=view)

        # Ensure all mandatory fields are filled
        if not all(field.value for field in view.event.fields.values() if field.mandatory):
            await interaction.response.send_message("Not all mandatory fields are filled!", ephemeral=True)
            return
        
        if type(interaction.channel) == discord.DMChannel:
            await view.send_forum_post(interaction)
        else:
            raise Exception("interaction.channel is not a DMChannel")  

## TODO let the user cancel
# class CancelView(discord.ui.View):
#     def __init__(self):
#         super().__init__()
#         self.add_item(SubmitButton())

class FieldSelect(discord.ui.Select):
    """Custom select menu handling field selection."""
    def __init__(self, fields:dict[pe_common.FieldName, pe_common.InputField]):
        self.fields = fields
        super().__init__(
            placeholder="Wähle zum Bearbeiten...",
            min_values=1,
            max_values=1,
            options=self.get_options()
        )

    def get_options(self):
        """Generates the options dynamically based on field status."""
        return [
            discord.SelectOption(label=field.label(self.fields), value=field.name.name, emoji=field.custom_icon, description=field.description)
            for field in self.fields.values()
        ]

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not self.view and not type(self.view) == EditTourneyView:
            raise Exception("view is None")
        view:EditTourneyView = self.view
        self.view.disable_all_items()
        self.view.stop()
        await interaction.followup.edit_message(view.message.id, view=view)
        # await interaction.followup.edit_message(interaction.message.id, view=self) # oder einfach löschen

        selection = self.values[0]
        if not type(selection) == str:
            raise Exception("non-string types not supported yet")
        field_name = pe_common.FieldName[selection]
        if not field_name:
            raise Exception(f"no FieldName {selection}")
        selected_field:pe_common.InputField = view.event.fields[field_name]

        user:discord.User = interaction.user
        bot:Bot = interaction.client
        try:
            ui_item = selected_field.field_type.ui_item
            if ui_item:
                new_view = discord.ui.View(timeout=None)
                new_view.add_item(ui_item())
                interaction_message = await interaction.followup.send(f"{selected_field.field_type.text} für {selected_field.name.value}:", view=new_view) #, view=CancelView()
                wait_response:discord.interactions.Interaction = await bot.wait_for("interaction", check=check_factory(user))
                new_view.disable_all_items()
                new_view.stop()
                await wait_response.response.edit_message(view=new_view)
                selected_field.value = wait_response
            else:
                await interaction.followup.send(f"{selected_field.field_type.text} für {selected_field.name.value}:") #, view=CancelView()
                wait_response = await bot.wait_for("message", check=check_factory(user))
                selected_field.value = wait_response
            await self.view.event.send_preview(user)
            await user.send(view=EditTourneyView(view.event))
        except TimeoutError as e:
            await user.send("Du hast dir leider zu viel Zeit gelassen took too long to respond. Bitte versuche es noch einmal mit dem `/poste_turnier` Befehl.")

class EditTourneyView(discord.ui.View):
    def __init__(self, event:pe_common.PaperEvent):
        super().__init__()
        self.event = event
        self.select_menu = FieldSelect(self.event.fields)

        # Submit Button
        self.submit_button = SubmitButton()
        self.add_item(self.select_menu)
        self.add_item(self.submit_button)

        self.update_submit_button()

    async def send_forum_post(self, interaction:discord.Interaction):
        guild = self.event.guild
        forum_channel = await discord.utils.get_or_fetch(guild, "channel", CHANNEL_PAPER_EVENTS_ID, default=None)

        if not isinstance(forum_channel, discord.ForumChannel):
            await interaction.followup.send(content="This is not a forum channel!")
            return
        
        thread = await self.event.create_and_send_thread(forum_channel)
        notion_result = self.save_tourney_in_notion(thread)

        await interaction.followup.send(f"Forum Post erstellt: {thread.jump_url}\n[Notion Eintrag]({notion_result['public_url']}) erstellt")


    def update_submit_button(self):
        """Enable or disable the submit button based on mandatory field completion."""
        all_mandatory_filled = all(field.value for field in self.event.fields.values() if field.mandatory)
        at_least_one_conditional_filled = self.event.fields[pe_common.FieldName.TITLE].value or self.event.fields[pe_common.FieldName.TYPE].value
        is_valid = all_mandatory_filled and at_least_one_conditional_filled
        self.submit_button.disabled = not is_valid

    def save_tourney_in_notion(self, thread):
        payload = notion.NotionPayloadBuilder()
        payload.add_title("Event Titel", self.event.build_title())
        payload.add_checkbox("For Test", DEBUG)
        payload.add_text("Author", self.event.author.display_name if self.event.author.display_name else self.event.author.name)
        payload.add_text("Author ID", str(self.event.author.id))
        if self.event.fields[pe_common.FieldName.TYPE].value:
            payload.add_select("Event Typ", self.event.fields[pe_common.FieldName.TYPE].value[0])
        if self.event.fields[pe_common.FieldName.FORMATS].value:
            payload.add_multiselect("Format(e)", self.event.fields[pe_common.FieldName.FORMATS].value)
        payload.add_date(
            "Start (und Ende)",
            start=self.event.fields[pe_common.FieldName.START].value,
            end=self.event.fields[pe_common.FieldName.END].value
        )
        payload.add_text("Freitext", self.event.fields[pe_common.FieldName.DESCRIPTION].value or "")
        if self.event.fields[pe_common.FieldName.FEE].value:
            payload.add_number("Gebühr", self.event.fields[pe_common.FieldName.FEE].value)
        location:gmaps.Location = self.event.fields[pe_common.FieldName.LOCATION].value
        payload.add_text("Name des Ladens", location.name or "")
        payload.add_text("Stadt", location.city['long_name'])
        payload.add_relation("(Bundes)land", location.get_area_page_id())
        if self.event.fields[pe_common.FieldName.URL].value:
            payload.add_url("URL", self.event.fields[pe_common.FieldName.URL].value)
        payload.add_text("Server ID", str(thread.guild.id))
        payload.add_text("Thread ID", str(thread.id))
        return notion.add_to_database(database_id=EVENT_DATABASE_ID, payload=payload.build())
    async def on_timeout(self):
        self.disable_all_items()
        self.stop()
        await self.message.edit(view=self)

class PaperEventSubmit(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    # Weitere Checks https://gist.github.com/Painezor/eb2519022cd2c907b56624105f94b190
    @slash_command(
        description="Reiche ein Paper Turnier/Event ein",
        integration_type={IntegrationType.user_install},
        contexts={InteractionContextType.bot_dm} #, InteractionContextType.private_channel
    )
    async def poste_turnier(self, ctx:ApplicationContext):
        try:
            user = ctx.author
            embed = discord.Embed(image=SPIKE_ART_CROP, color=env.SPIKE_RED)
            direct_message = await user.send(
                content="# Lass uns zusammen eine Veranstaltung erstellen 💪",
                embed=embed
            )
            
            await ctx.send_response(f"Lass uns zusammen in den Direktnachrichten das Turnier erstellen: {direct_message.jump_url}", ephemeral=True)
            await user.send("Was möchtest du bearbeiten?", view=EditTourneyView(pe_common.PaperEvent(ctx.guild, user)))
        except discord.Forbidden:
            await ctx.send_response("Ich konnte dich nicht direkt anschreiben! Bitte erlaube Direktnachrichten von Mitgliedern des Servers.", ephemeral=True)

    @Cog.listener()
    async def on_application_command_error(self, ctx:ApplicationContext, error):
        log.error(error)
        await ctx.respond(f"Es ist ein Fehler aufgetreten: ```{error}```", ephemeral=True)
        raise error


def setup(bot:Bot):
    bot.add_cog(PaperEventSubmit(bot))
