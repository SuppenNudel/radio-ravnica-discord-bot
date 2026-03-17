from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, InteractionContextType, IntegrationType
import discord
from modules import env, paper_events_db
import modules.paper_events_common as pe_common
import traceback
from datetime import datetime
import logging

link_log = logging.getLogger("link_logger")

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
        # reconnect edit views from SQLite
        conn = paper_events_db.connect()
        paper_events_db.init_db(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM paper_events
            WHERE thread_id IS NOT NULL
                            AND server_id = ?
              AND start_at >= datetime('now', '-1 day')
            ORDER BY start_at ASC
            """,
                        [str(env.GUILD_ID)],
        ).fetchall()

        for row in rows:
            server_id = int(row["server_id"])
            author_id_raw = row["author_id"]
            author_id = int(author_id_raw) if author_id_raw else None
            thread_id = int(row["thread_id"])

            start_at_raw = row["start_at"]
            end_at_raw = row["end_at"]
            try:
                start_at = datetime.fromisoformat(start_at_raw) if start_at_raw else None
                end_at = datetime.fromisoformat(end_at_raw) if end_at_raw else None
            except Exception:
                start_at = None
                end_at = None

            event_reference = end_at or start_at
            if event_reference is not None:
                now = datetime.now(tz=event_reference.tzinfo)
                if event_reference < now:
                    link_log.debug(f"Skipping view reconnect for past event thread {thread_id}")
                    continue

            try:
                guild = await discord.utils.get_or_fetch(self.bot, "guild", server_id)
                if not guild:
                    link_log.error(f"Guild not found for https://discord.com/channels/{server_id}/{thread_id}")
                    continue

                author = None
                if author_id:
                    try:
                        author = await discord.utils.get_or_fetch(guild, "member", author_id)
                    except discord.NotFound:
                        link_log.warning(
                            f"Author not found in guild for https://discord.com/channels/{server_id}/{thread_id} author <@{author_id}>; using fallback"
                        )

                paper_event = pe_common.PaperEvent(guild, author)

                try:
                    thread:discord.Thread = await discord.utils.get_or_fetch(guild, "channel", thread_id)
                except discord.NotFound:
                    link_log.warning(f"Thread not found while reconnecting view: https://discord.com/channels/{server_id}/{thread_id}")
                    continue

                paper_event.thread = thread
                paper_event.fill_fields_from_db_row(row)

                try:
                    message: discord.Message = await thread.fetch_message(thread_id)
                except discord.NotFound:
                    link_log.warning(f"Starter message not found while reconnecting view: https://discord.com/channels/{server_id}/{thread_id}")
                    continue

                view = pe_common.EditPostView(paper_event)

                # Unarchive thread temporarily if needed to edit the message
                if thread.archived:
                    await thread.edit(archived=False)
                    link_log.debug(f"Temporarily unarchived thread {thread_id} to edit message")

                await message.edit(view=view)
            except discord.errors.HTTPException as e:
                if e.code == 50083:  # Thread is archived
                    link_log.warning(f"Thread {thread_id} is archived and could not be unarchived: {e}")
                else:
                    link_log.error(f"HTTP error when editing message in thread {thread_id}: {e}")
            except Exception as e:
                link_log.error(f"Error reconnecting view for thread {thread_id}: {e}")
        log.debug(self.__class__.__name__ + " is ready")

    # Weitere Checks https://gist.github.com/Painezor/eb2519022cd2c907b56624105f94b190
    @slash_command(
        guild_ids=[env.GUILD_ID],
        description="Reiche ein Paper Turnier/Event ein",
        integration_type={IntegrationType.user_install},
    )
    async def poste_turnier(self, ctx:ApplicationContext):
        try:
            if not ctx.guild:
                await ctx.send_response("Dieser Befehl kann nur auf einem Server verwendet werden.", ephemeral=True)
                return

            user = ctx.author
            paper_event = pe_common.PaperEvent(ctx.guild, user)
            embed = discord.Embed(image=SPIKE_ART_CROP, color=env.SPIKE_RED)
            await ctx.send_response(
                content=(
                    "# Lass uns zusammen eine Veranstaltung erstellen 💪\n"
                    "Mit * markierte Felder sind Pflichtfelder."
                ),
                embed=embed,
                view=pe_common.EditTourneyView(paper_event),
                ephemeral=True,
            )
        except Exception as e:
            log.error(traceback.format_exc())
            try:
                await ctx.respond(f"Es ist ein Fehler aufgetreten: ```{repr(e)}```", ephemeral=True)
            except discord.errors.NotFound:
                log.error("Could not respond to error - interaction expired")


def setup(bot:Bot):
    bot.add_cog(PaperEventSubmit(bot))
