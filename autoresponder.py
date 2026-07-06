"""
/autoresponder add - admin-only slash command that opens a simple, guided
form for creating an autoresponder: a trigger phrase plus a response that
supports the same text placeholders as embeds ({username}, {user},
{servername}), minus the image-only ones ({serveravatar}/{useravatar},
which don't make sense in a plain chat message). To attach one of your
saved embeds to the response, just include {embed:name} anywhere in the
response text.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from db import mongo
from utils.placeholders import (
    AUTORESPONDER_PLACEHOLDER_GUIDE,
    build_preview_embed,
    extract_embed_reference,
    resolve_text,
)

VALID_MATCH_TYPES = ("exact", "contains")


def parse_match_type(value: str) -> str:
    value = (value or "").strip().lower()
    return value if value in VALID_MATCH_TYPES else "contains"


def parse_yes_no(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in ("yes", "y", "true", "1")


# ---------------------------------------------------------------------------
# Modal - the actual "form"
# ---------------------------------------------------------------------------

class AutoresponderModal(discord.ui.Modal, title="New Autoresponder"):
    trigger_input = discord.ui.TextInput(
        label="Trigger word or phrase",
        placeholder="e.g. hello, !rules, good morning",
        required=True,
        max_length=100,
    )
    match_type_input = discord.ui.TextInput(
        label="Match type: exact or contains",
        placeholder="contains",
        required=False,
        max_length=10,
        default="contains",
    )
    response_input = discord.ui.TextInput(
        label="Response message",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "Hey {username}, welcome to {servername}! {embed:welcome}"
        ),
        required=True,
        max_length=1900,
    )
    case_sensitive_input = discord.ui.TextInput(
        label="Case sensitive? (yes/no)",
        placeholder="no",
        required=False,
        max_length=3,
        default="no",
    )

    def __init__(self, guild_id: int, author_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        trigger = str(self.trigger_input.value).strip().lower()

        if await mongo.autoresponder_trigger_exists(self.guild_id, trigger):
            await interaction.response.send_message(
                f"An autoresponder for **{trigger}** already exists. "
                f"Delete it first if you want to replace it.",
                ephemeral=True,
            )
            return

        data = {
            "response": str(self.response_input.value),
            "match_type": parse_match_type(str(self.match_type_input.value)),
            "case_sensitive": parse_yes_no(str(self.case_sensitive_input.value)),
        }
        await mongo.save_autoresponder(self.guild_id, trigger, data, self.author_id)

        # Build a quick "here's what this will actually look like" preview,
        # resolved against the person who just created it, so they can
        # confirm it works before moving on.
        clean_text, embed_name = extract_embed_reference(data["response"])
        preview_text = resolve_text(clean_text, interaction.user, interaction.guild)

        preview_embed = None
        embed_note = ""
        if embed_name:
            embed_doc = await mongo.get_embed(self.guild_id, embed_name)
            if embed_doc:
                preview_embed = build_preview_embed(embed_doc, interaction.user, interaction.guild)
            else:
                embed_note = (
                    f"\n\n\u26a0\ufe0f Heads up: no saved embed named **{embed_name}** "
                    f"exists yet, so `{{embed:{embed_name}}}` won't attach anything "
                    f"until you create one with `/embed create`."
                )

        summary = (
            f"\u2705 Autoresponder created!\n\n"
            f"**Trigger:** `{trigger}` ({data['match_type']}"
            f"{', case sensitive' if data['case_sensitive'] else ''})\n"
            f"**Preview of the response you'll see:**{embed_note}"
        )

        await interaction.response.send_message(
            content=summary + (f"\n\n{preview_text}" if preview_text else ""),
            embed=preview_embed,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# View - the "Open Form" entry point, shown alongside the placeholder guide
# ---------------------------------------------------------------------------

class OpenAutoresponderFormView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int):
        super().__init__(timeout=180)
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

    @discord.ui.button(label="Open Form", style=discord.ButtonStyle.success, emoji="\U0001F4DD")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AutoresponderModal(guild_id=self.guild_id, author_id=self.owner_id)
        )
        button.disabled = True
        # interaction.message.edit() 404s here because the original message is
        # ephemeral (it isn't a normal fetchable channel message). Use the
        # interaction's webhook-based edit instead, which works for ephemeral
        # responses and doesn't require a fresh "response" slot (send_modal
        # already used this interaction's response).
        await interaction.edit_original_response(view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AutoresponderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    autoresponder_group = app_commands.Group(
        name="autoresponder",
        description="Create and manage autoresponders",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    @autoresponder_group.command(name="add", description="Create a new autoresponder (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add(self, interaction: discord.Interaction):
        intro = (
            "### Create an autoresponder\n"
            "Set a trigger phrase, then a response that sends whenever someone "
            "says it. Click **Open Form** below to get started.\n\n"
            f"{AUTORESPONDER_PLACEHOLDER_GUIDE}"
        )
        view = OpenAutoresponderFormView(owner_id=interaction.user.id, guild_id=interaction.guild_id)
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

    @autoresponder_group.command(name="list", description="List all autoresponders in this server (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_autoresponders_cmd(self, interaction: discord.Interaction):
        docs = await mongo.list_autoresponders(interaction.guild_id)

        if not docs:
            await interaction.response.send_message(
                "No autoresponders have been created in this server yet. "
                "Use `/autoresponder add` to make one.",
                ephemeral=True,
            )
            return

        docs = sorted(docs, key=lambda d: d["trigger"])
        lines = []
        for i, doc in enumerate(docs, start=1):
            match_type = doc.get("match_type", "contains")
            case_note = ", case sensitive" if doc.get("case_sensitive") else ""
            lines.append(f"`{i}.` **{doc['trigger']}** \u2014 {match_type}{case_note}")

        embed = discord.Embed(
            title="Autoresponders",
            description="\n".join(lines),
            color=discord.Color.dark_gold(),
        )
        embed.set_footer(text=f"{interaction.guild.name} \u2022 {len(docs)} saved")

        await interaction.response.send_message(embed=embed)

    @list_autoresponders_cmd.error
    async def list_autoresponders_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    async def trigger_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        docs = await mongo.list_autoresponders(interaction.guild_id)
        triggers = sorted(doc["trigger"] for doc in docs)
        current = current.lower()
        matches = [t for t in triggers if current in t.lower()]
        return [app_commands.Choice(name=t, value=t) for t in matches[:25]]

    @autoresponder_group.command(name="delete", description="Delete an autoresponder")
    @app_commands.describe(trigger="the autoresponder to delete")
    @app_commands.autocomplete(trigger=trigger_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_autoresponder_cmd(self, interaction: discord.Interaction, trigger: str):
        exists = await mongo.autoresponder_trigger_exists(interaction.guild_id, trigger)
        if not exists:
            await interaction.response.send_message(
                f"No autoresponder for **{trigger}** was found in this server.",
                ephemeral=True,
            )
            return

        await mongo.delete_autoresponder(interaction.guild_id, trigger)
        await interaction.response.send_message(
            f"\U0001F5D1\ufe0f Deleted autoresponder **{trigger}**.",
            ephemeral=True,
        )

    @delete_autoresponder_cmd.error
    async def delete_autoresponder_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    # -- actually firing autoresponders -------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        docs = await mongo.list_autoresponders(message.guild.id)
        if not docs:
            return

        content = message.content
        for doc in docs:
            trigger = doc["trigger"]
            case_sensitive = doc.get("case_sensitive", False)
            haystack = content if case_sensitive else content.lower()
            needle = trigger if case_sensitive else trigger.lower()

            matched = (
                haystack == needle
                if doc.get("match_type") == "exact"
                else needle in haystack
            )
            if not matched:
                continue

            clean_text, embed_name = extract_embed_reference(doc.get("response"))
            resolved_text = resolve_text(clean_text, message.author, message.guild) or None

            embed_obj = None
            if embed_name:
                embed_doc = await mongo.get_embed(message.guild.id, embed_name)
                if embed_doc:
                    embed_obj = build_preview_embed(embed_doc, message.author, message.guild)

            if resolved_text or embed_obj:
                await message.channel.send(content=resolved_text, embed=embed_obj)
            break  # only fire the first matching autoresponder per message


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoresponderCog(bot))
