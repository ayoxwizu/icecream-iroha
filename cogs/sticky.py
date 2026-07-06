"""
/sticky add - admin-only slash command that opens a simple form asking for
a channel and a message. Whenever anyone posts in that channel, the bot
deletes its own previous sticky post there and resends the message, so it
always stays as the most recent message in the channel.

Supports the same text placeholders as autoresponders ({username}, {user},
{servername}), plus {embed:name} to attach one of your saved embeds.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from db import mongo
from utils.cache import TTLCache, run_with_timeout
from utils.placeholders import (
    STICKY_PLACEHOLDER_GUIDE,
    build_preview_embed,
    extract_embed_reference,
    resolve_text,
)


# ---------------------------------------------------------------------------
# Modal - the form
# ---------------------------------------------------------------------------

class StickyModal(discord.ui.Modal, title="New Sticky Message"):
    channel_id_input = discord.ui.TextInput(
        label="Channel ID",
        placeholder="e.g. 1234567890123456",
        required=True,
        max_length=25,
    )
    message_input = discord.ui.TextInput(
        label="Sticky message",
        style=discord.TextStyle.paragraph,
        placeholder="Welcome to {servername}! Read the rules {embed:rules}",
        required=True,
        max_length=1900,
    )

    def __init__(self, bot: commands.Bot, guild_id: int, author_id: int):
        super().__init__()
        self.bot = bot
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

        if await mongo.sticky_exists(self.guild_id, channel.id):
            await interaction.response.send_message(
                f"{channel.mention} already has a sticky message. "
                f"Delete it first if you want to replace it.",
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

        resolved_text = resolve_text(clean_text, interaction.user, interaction.guild) or None
        embed_obj = None
        if embed_name:
            embed_doc = await mongo.get_embed(self.guild_id, embed_name)
            if embed_doc:
                embed_obj = build_preview_embed(embed_doc, interaction.user, interaction.guild)

        if not resolved_text and not embed_obj:
            await interaction.response.send_message(
                "The sticky message ended up empty after removing the embed "
                "reference. Add some text or fix the embed name and try again.",
                ephemeral=True,
            )
            return

        try:
            sent = await channel.send(content=resolved_text, embed=embed_obj)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I don't have permission to send messages in {channel.mention}.",
                ephemeral=True,
            )
            return

        await mongo.save_sticky(
            self.guild_id,
            channel.id,
            {"message": message_text, "last_message_id": sent.id},
            self.author_id,
        )

        await interaction.response.send_message(
            f"\u2705 Sticky message created in {channel.mention}."
            f"{embed_note}",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# View - the "Open Form" entry point, shown alongside the placeholder guide
# ---------------------------------------------------------------------------

class OpenStickyFormView(discord.ui.View):
    def __init__(self, bot: commands.Bot, owner_id: int, guild_id: int):
        super().__init__(timeout=180)
        self.bot = bot
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
            StickyModal(bot=self.bot, guild_id=self.guild_id, author_id=self.owner_id)
        )
        button.disabled = True
        await interaction.edit_original_response(view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class StickyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}
        self._sticky_cache = TTLCache(ttl_seconds=15.0)

    def _lock_for(self, channel_id: int) -> asyncio.Lock:
        lock = self._locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[channel_id] = lock
        return lock

    sticky_group = app_commands.Group(
        name="sticky",
        description="Create and manage sticky messages",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    @sticky_group.command(name="add", description="Create a new sticky message (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add(self, interaction: discord.Interaction):
        intro = (
            "### Create a sticky message\n"
            "Pick a channel and a message. Whenever someone posts in that "
            "channel, I'll delete my last sticky post there and resend it, "
            "so it always stays at the bottom.\n\n"
            f"{STICKY_PLACEHOLDER_GUIDE}"
        )
        view = OpenStickyFormView(bot=self.bot, owner_id=interaction.user.id, guild_id=interaction.guild_id)
        await interaction.response.send_message(content=intro, view=view, ephemeral=True)

    @add.error
    async def add_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    @sticky_group.command(name="list", description="List all sticky messages in this server (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_stickies_cmd(self, interaction: discord.Interaction):
        docs = await mongo.list_stickies(interaction.guild_id)

        if not docs:
            await interaction.response.send_message(
                "No sticky messages have been created in this server yet. "
                "Use `/sticky add` to make one.",
                ephemeral=True,
            )
            return

        lines = []
        for i, doc in enumerate(docs, start=1):
            channel = interaction.guild.get_channel(doc["channel_id"])
            channel_label = channel.mention if channel else f"`#{doc['channel_id']}` (channel not found)"
            preview = doc.get("message", "").strip().replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:57] + "..."
            lines.append(f"`{i}.` {channel_label} \u2014 {preview}")

        embed = discord.Embed(
            title="Sticky Messages",
            description="\n".join(lines),
            color=discord.Color.dark_purple(),
        )
        embed.set_footer(text=f"{interaction.guild.name} \u2022 {len(docs)} saved")

        await interaction.response.send_message(embed=embed)

    @list_stickies_cmd.error
    async def list_stickies_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    async def channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        docs = self._sticky_cache.get(interaction.guild_id)
        if docs is None:
            docs = await run_with_timeout(
                mongo.list_stickies(interaction.guild_id), timeout=2.0, default=[]
            )
            self._sticky_cache.set(interaction.guild_id, docs)

        current = current.lower()
        choices = []
        for doc in docs:
            channel = interaction.guild.get_channel(doc["channel_id"])
            label = f"#{channel.name}" if channel else f"unknown ({doc['channel_id']})"
            if current in label.lower() or current in str(doc["channel_id"]):
                choices.append(app_commands.Choice(name=label, value=str(doc["channel_id"])))
        return choices[:25]

    @sticky_group.command(name="delete", description="Delete a sticky message")
    @app_commands.describe(channel="the channel whose sticky message you want to remove")
    @app_commands.autocomplete(channel=channel_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_sticky_cmd(self, interaction: discord.Interaction, channel: str):
        if not channel.isdigit():
            await interaction.response.send_message(
                "Please pick a channel from the autocomplete list.",
                ephemeral=True,
            )
            return

        channel_id = int(channel)
        doc = await mongo.get_sticky(interaction.guild_id, channel_id)
        if not doc:
            await interaction.response.send_message(
                "No sticky message was found for that channel.",
                ephemeral=True,
            )
            return

        last_id = doc.get("last_message_id")
        target_channel = interaction.guild.get_channel(channel_id)
        if last_id and target_channel:
            try:
                old = await target_channel.fetch_message(last_id)
                await old.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        await mongo.delete_sticky(interaction.guild_id, channel_id)

        channel_label = target_channel.mention if target_channel else f"`#{channel_id}`"
        await interaction.response.send_message(
            f"\U0001F5D1\ufe0f Deleted the sticky message for {channel_label}.",
            ephemeral=True,
        )

    @delete_sticky_cmd.error
    async def delete_sticky_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    # -- actually maintaining the sticky -------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.id == self.bot.user.id:
            return

        doc = await mongo.get_sticky(message.guild.id, message.channel.id)
        if not doc:
            return

        async with self._lock_for(message.channel.id):
            # Re-fetch in case another task already handled this burst of
            # messages and updated last_message_id underneath us.
            doc = await mongo.get_sticky(message.guild.id, message.channel.id)
            if not doc:
                return

            last_id = doc.get("last_message_id")
            if last_id:
                try:
                    old = await message.channel.fetch_message(last_id)
                    await old.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            clean_text, embed_name = extract_embed_reference(doc.get("message"))
            resolved_text = resolve_text(clean_text, message.author, message.guild) or None

            embed_obj = None
            if embed_name:
                embed_doc = await mongo.get_embed(message.guild.id, embed_name)
                if embed_doc:
                    embed_obj = build_preview_embed(embed_doc, message.author, message.guild)

            if not resolved_text and not embed_obj:
                return

            try:
                sent = await message.channel.send(content=resolved_text, embed=embed_obj)
            except discord.Forbidden:
                return

            await mongo.update_sticky_message_id(message.guild.id, message.channel.id, sent.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(StickyCog(bot))
