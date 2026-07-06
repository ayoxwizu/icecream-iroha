"""
cogs/events.py

Admin-only configuration for three server-event messages: boost, welcome,
and leave. Each is set up the same way (a modal asking for a channel ID and
a message) and each can be independently disabled.

    /set boost message      - configure the message sent when someone
                               boosts the server
    /boost message disable  - turn the boost message off

    /set welcome message    - configure the message sent when someone
                               joins the server
    /welcome message disable - turn the welcome message off

    /set leave message      - configure the message sent when someone
                               leaves the server
    /leave message disable  - turn the leave message off

All six commands require Administrator.

Supports the same text placeholders as the rest of bot 1: {username},
{servername}, plus {embed:name} to attach one of the saved embeds.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import mongo
from utils.placeholders import (
    EVENT_PLACEHOLDER_GUIDE,
    build_preview_embed,
    extract_embed_reference,
    resolve_text,
)

log = logging.getLogger("cogs.events")

# Maps our internal event_type key to a human label used in modal titles/replies.
EVENT_LABELS = {
    "boost": "Boost",
    "welcome": "Welcome",
    "leave": "Leave",
}


# ---------------------------------------------------------------------------
# Modal - the form used by all three "/set ... message" commands
# ---------------------------------------------------------------------------

class EventMessageModal(discord.ui.Modal):
    channel_id_input = discord.ui.TextInput(
        label="Channel ID",
        placeholder="e.g. 1234567890123456",
        required=True,
        max_length=25,
    )
    message_input = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="Welcome to {servername}, {username}! {embed:welcome}",
        required=True,
        max_length=1900,
    )

    def __init__(self, *, event_type: str, guild_id: int, author_id: int):
        super().__init__(title=f"New {EVENT_LABELS[event_type]} Message")
        self.event_type = event_type
        self.guild_id = guild_id
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        raw_channel_id = str(self.channel_id_input.value).strip()
        if not raw_channel_id.isdigit():
            await interaction.response.send_message(
                "That doesn't look like a valid channel ID. "
                "Enable Developer Mode in Discord, then right-click the "
                "channel and choose **Copy Channel ID**.",
                ephemeral=True,
            )
            return

        channel_id = int(raw_channel_id)
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                channel = None

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                f"I couldn't find a text channel with ID `{channel_id}` in this server.",
                ephemeral=True,
            )
            return

        message_text = str(self.message_input.value)
        clean_text, embed_name = extract_embed_reference(message_text)
        embed_note = ""

        if embed_name and not await mongo.get_embed(self.guild_id, embed_name):
            embed_note = (
                f"\n\n\u26a0\ufe0f Heads up: no saved embed named **{embed_name}** "
                f"exists yet, so `{{embed:{embed_name}}}` won't attach anything "
                f"until you create one with `/embed create`."
            )

        await mongo.save_event_message(
            self.guild_id, self.event_type, channel.id, message_text, self.author_id
        )

        label = EVENT_LABELS[self.event_type]
        await interaction.response.send_message(
            f"\u2705 {label} message set. It will be sent in {channel.mention}."
            f"{embed_note}",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# View - the "Open Form" entry point, shown alongside the placeholder guide
# ---------------------------------------------------------------------------

class OpenEventFormView(discord.ui.View):
    def __init__(self, *, event_type: str, owner_id: int, guild_id: int):
        super().__init__(timeout=180)
        self.event_type = event_type
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use this button.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Open Form", style=discord.ButtonStyle.success, emoji="\U0001F4CC")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            EventMessageModal(
                event_type=self.event_type, guild_id=self.guild_id, author_id=self.owner_id
            )
        )
        button.disabled = True
        await interaction.edit_original_response(view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------
    # "/set <event> message" group
    # -----------------------------------------------------------------
    set_group = app_commands.Group(
        name="set",
        description="Configure server event messages",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )
    set_boost_group = app_commands.Group(
        name="boost", description="Configure the boost message", parent=set_group
    )
    set_welcome_group = app_commands.Group(
        name="welcome", description="Configure the welcome message", parent=set_group
    )
    set_leave_group = app_commands.Group(
        name="leave", description="Configure the leave message", parent=set_group
    )

    # -----------------------------------------------------------------
    # "/<event> message disable" groups (one top-level group per event)
    # -----------------------------------------------------------------
    boost_group = app_commands.Group(
        name="boost",
        description="Manage the boost message",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )
    boost_message_group = app_commands.Group(
        name="message", description="Manage the boost message", parent=boost_group
    )

    welcome_group = app_commands.Group(
        name="welcome",
        description="Manage the welcome message",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )
    welcome_message_group = app_commands.Group(
        name="message", description="Manage the welcome message", parent=welcome_group
    )

    leave_group = app_commands.Group(
        name="leave",
        description="Manage the leave message",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )
    leave_message_group = app_commands.Group(
        name="message", description="Manage the leave message", parent=leave_group
    )

    async def _open_set_form(self, interaction: discord.Interaction, event_type: str):
        label = EVENT_LABELS[event_type]
        intro = (
            f"### Configure the {label.lower()} message\n"
            f"Pick a channel and a message. {EVENT_PLACEHOLDER_GUIDE}"
        )
        view = OpenEventFormView(
            event_type=event_type, owner_id=interaction.user.id, guild_id=interaction.guild_id
        )
        await interaction.response.send_message(content=intro, view=view, ephemeral=True)

    async def _disable(self, interaction: discord.Interaction, event_type: str):
        label = EVENT_LABELS[event_type]
        doc = await mongo.get_event_message(interaction.guild_id, event_type)
        if not doc:
            await interaction.response.send_message(
                f"No {label.lower()} message has been set up yet. Use `/set {event_type} message` first.",
                ephemeral=True,
            )
            return

        if not doc.get("enabled", True):
            await interaction.response.send_message(
                f"The {label.lower()} message is already disabled.", ephemeral=True
            )
            return

        await mongo.set_event_message_enabled(interaction.guild_id, event_type, False)
        await interaction.response.send_message(
            f"\u2705 {label} message disabled. Run `/set {event_type} message` again to re-enable it.",
            ephemeral=True,
        )

    @staticmethod
    async def _admin_only_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    # -- /set boost message --------------------------------------------------
    @set_boost_group.command(name="message", description="Configure the boost message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_boost_message(self, interaction: discord.Interaction):
        await self._open_set_form(interaction, "boost")

    @set_boost_message.error
    async def set_boost_message_error(self, interaction, error):
        await self._admin_only_error(interaction, error)

    # -- /set welcome message -------------------------------------------------
    @set_welcome_group.command(name="message", description="Configure the welcome message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_welcome_message(self, interaction: discord.Interaction):
        await self._open_set_form(interaction, "welcome")

    @set_welcome_message.error
    async def set_welcome_message_error(self, interaction, error):
        await self._admin_only_error(interaction, error)

    # -- /set leave message ----------------------------------------------------
    @set_leave_group.command(name="message", description="Configure the leave message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_leave_message(self, interaction: discord.Interaction):
        await self._open_set_form(interaction, "leave")

    @set_leave_message.error
    async def set_leave_message_error(self, interaction, error):
        await self._admin_only_error(interaction, error)

    # -- /boost message disable ----------------------------------------------
    @boost_message_group.command(name="disable", description="Disable the boost message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def boost_message_disable(self, interaction: discord.Interaction):
        await self._disable(interaction, "boost")

    @boost_message_disable.error
    async def boost_message_disable_error(self, interaction, error):
        await self._admin_only_error(interaction, error)

    # -- /welcome message disable ---------------------------------------------
    @welcome_message_group.command(name="disable", description="Disable the welcome message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_message_disable(self, interaction: discord.Interaction):
        await self._disable(interaction, "welcome")

    @welcome_message_disable.error
    async def welcome_message_disable_error(self, interaction, error):
        await self._admin_only_error(interaction, error)

    # -- /leave message disable -----------------------------------------------
    @leave_message_group.command(name="disable", description="Disable the leave message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def leave_message_disable(self, interaction: discord.Interaction):
        await self._disable(interaction, "leave")

    @leave_message_disable.error
    async def leave_message_disable_error(self, interaction, error):
        await self._admin_only_error(interaction, error)

    # -----------------------------------------------------------------
    # Actually firing the messages
    # -----------------------------------------------------------------
    async def _send_event_message(
        self, guild: discord.Guild, member: discord.Member, event_type: str
    ) -> None:
        doc = await mongo.get_event_message(guild.id, event_type)
        if not doc or not doc.get("enabled", True):
            return

        channel = guild.get_channel(doc["channel_id"])
        if channel is None:
            try:
                channel = await guild.fetch_channel(doc["channel_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        clean_text, embed_name = extract_embed_reference(doc.get("message"))
        resolved_text = resolve_text(clean_text, member, guild) or None

        embed_obj = None
        if embed_name:
            embed_doc = await mongo.get_embed(guild.id, embed_name)
            if embed_doc:
                embed_obj = build_preview_embed(embed_doc, member, guild)

        if not resolved_text and not embed_obj:
            return

        try:
            await channel.send(content=resolved_text, embed=embed_obj)
        except discord.Forbidden:
            log.warning(
                "Missing permission to send the %s message in guild %s channel %s",
                event_type, guild.id, doc["channel_id"],
            )
        except discord.HTTPException:
            log.exception("Failed to send the %s message in guild %s", event_type, guild.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._send_event_message(member.guild, member, "welcome")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._send_event_message(member.guild, member, "leave")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # A member starts boosting exactly when premium_since flips from
        # None to a timestamp. Ignore any other update (nickname, roles, etc.)
        # and ignore someone re-boosting without ever having stopped.
        if before.premium_since is None and after.premium_since is not None:
            await self._send_event_message(after.guild, after, "boost")


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
