from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, InteractionContextType, IntegrationType
import discord
from modules import notion, env, gmaps
import modules.paper_events_common as pe_common
import traceback
from datetime import datetime

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
            discord.SelectOption(label=field.label(self.fields), value=field.name.name, emoji=field.icon, description=field.description)
            for field in self.fields.values()
        ]

    async def callback(self, interaction: discord.Interaction):
        response_message = await interaction.response.send_message("Anfrage erhalten, einen Augeblick... ⏳")
        if not self.view and not type(self.view) == pe_common.EditTourneyView:
            raise Exception("view is None")
        view:pe_common.EditTourneyView = self.view
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
                await response_message.edit(content=f"{selected_field.field_type.text} für {selected_field.name.value}:", view=new_view) #, view=CancelView()
                wait_response:discord.interactions.Interaction = await bot.wait_for("interaction", check=check_factory(user))
                new_view.disable_all_items()
                new_view.stop()
                await wait_response.response.edit_message(view=new_view)
            else:
                await response_message.edit(content=f"{selected_field.field_type.text} für {selected_field.name.value}:") #, view=CancelView()
                wait_response = await bot.wait_for("message", check=check_factory(user))
            parsing_message = await user.send("Eingabe wird verarbeitet, einen Augeblick... ⏳")
            selected_field.value = wait_response
            # show preview
            embeds = []
            tourney_embed = view.event.construct_event_embed()
            if tourney_embed:
                embeds.append(tourney_embed)
            gmaps_embed = view.event.construct_gmaps_embed()
            if gmaps_embed:
                embeds.append(gmaps_embed)
            
            view.event.construct_gmaps_embed()
            await parsing_message.edit(
                content=view.event.construct_content(preview=True),
                embeds=embeds,
                files=view.event.get_files()
            )
            # await self.view.event.send_preview(user)
        except TimeoutError as e:
            await user.send("Du hast dir leider zu viel Zeit gelassen.")
        except Exception as e:
            log.error(traceback.format_exc())
            await user.send(f"Da ist etwas schief gegangen: {repr(e)}")
        finally:
            await user.send(view=pe_common.EditTourneyView(view.event))

class PaperEventSubmit(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        # reconnect edit views
        filter = (
            notion.NotionFilterBuilder()
            .add_date_filter("Start (und Ende)", notion.DateCondition.ON_OR_AFTER, datetime.now())
            .add_text_filter("Server ID", notion.TextCondition.EQUALS if DEBUG else notion.TextCondition.NOT_EQUAL, "1314528348319514684")
            .build()
            )
        upcoming_events = notion.get_all_entries(EVENT_DATABASE_ID, filter=filter)
        
        for event in upcoming_events:
            server_id = int(event.get_text_property("Server ID"))
            author_id = int(event.get_text_property("Author ID"))
            thread_id = int(event.get_text_property("Thread ID"))
            try:
                guild = await discord.utils.get_or_fetch(self.bot, "guild", server_id)
                author = await discord.utils.get_or_fetch(guild, "member", author_id)
                paper_event = pe_common.PaperEvent(guild, author)
                thread:discord.Thread = await discord.utils.get_or_fetch(guild, "channel", thread_id)
                paper_event.thread = thread

                paper_event.fill_fields_from_notion_entry(event)

                # thread does not have get_message method, so we need to fetch the message
                message:discord.Message = await thread.fetch_message(thread_id)
                view = pe_common.EditPostView(paper_event)
                await message.edit(view=view)

            except discord.NotFound as e:
                log.error(f"Didn't find guild or author {e}")
            # except Exception as e:
            #     log.error(f"Error when trying to parse {event}: {e}")
        log.debug(self.__class__.__name__ + " is ready")

    # Weitere Checks https://gist.github.com/Painezor/eb2519022cd2c907b56624105f94b190
    @slash_command(
        guild_ids=[env.GUILD_ID],
        description="Reiche ein Paper Turnier/Event ein",
        integration_type={IntegrationType.user_install},
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
            await user.send(view=pe_common.EditTourneyView(pe_common.PaperEvent(ctx.guild, user)))
        except discord.Forbidden:
            await ctx.send_response("Ich konnte dich nicht direkt anschreiben! Bitte erlaube Direktnachrichten von Mitgliedern des Servers.", ephemeral=True)

    @Cog.listener()
    async def on_application_command_error(self, ctx:ApplicationContext, error):
        log.error(error)
        await ctx.respond(f"Es ist ein Fehler aufgetreten: ```{error}```", ephemeral=True)
        raise error


def setup(bot:Bot):
    bot.add_cog(PaperEventSubmit(bot))
