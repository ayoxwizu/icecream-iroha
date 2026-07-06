"""
/embed create - admin-only slash command that opens an interactive embed
builder ("wizard"), matching the button-driven flow of: edit basic info,
edit author, edit footer, edit images, then submit/cancel. Finished embeds
are saved to MongoDB so they can be referenced elsewhere (e.g. an
autoresponder or greet message) as {embed:name}.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from db import mongo
from utils.placeholders import PLACEHOLDER_GUIDE, build_preview_embed

EMPTY_DRAFT = {
    "title": "",
    "description": "",
    "color": None,
    "author_text": "",
    "author_image": "",
    "footer_text": "",
    "footer_image": "",
    "image": "",
    "thumbnail": "",
    "timestamp": False,
}


def parse_hex_color(value: str) -> int | None:
    if not value:
        return None
    value = value.strip().lstrip("#")
    if not value:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def parse_yes_no(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in ("yes", "y", "true", "1")


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class BasicInfoModal(discord.ui.Modal, title="Edit basic information"):
    def __init__(self, view: "EmbedWizardView"):
        super().__init__()
        self.view = view
        self.title_input = discord.ui.TextInput(
            label="Title",
            required=False,
            max_length=256,
            default=view.draft.get("title", ""),
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            default=view.draft.get("description", ""),
        )
        self.color_input = discord.ui.TextInput(
            label="Hex Color",
            placeholder="#5865F2",
            required=False,
            max_length=7,
            default=(f"#{view.draft['color']:06X}" if view.draft.get("color") else ""),
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.draft["title"] = str(self.title_input.value)
        self.view.draft["description"] = str(self.description_input.value)
        parsed = parse_hex_color(str(self.color_input.value))
        self.view.draft["color"] = parsed
        await self.view.refresh(interaction)


class AuthorModal(discord.ui.Modal, title="Edit author"):
    def __init__(self, view: "EmbedWizardView"):
        super().__init__()
        self.view = view
        self.author_text_input = discord.ui.TextInput(
            label="Author Text",
            required=False,
            max_length=256,
            default=view.draft.get("author_text", ""),
        )
        self.author_image_input = discord.ui.TextInput(
            label="Author Image (optional)",
            placeholder="https://cdn.example.com/img.png or {useravatar}",
            required=False,
            default=view.draft.get("author_image", ""),
        )
        self.add_item(self.author_text_input)
        self.add_item(self.author_image_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.draft["author_text"] = str(self.author_text_input.value)
        self.view.draft["author_image"] = str(self.author_image_input.value)
        await self.view.refresh(interaction)


class FooterModal(discord.ui.Modal, title="Edit footer"):
    def __init__(self, view: "EmbedWizardView"):
        super().__init__()
        self.view = view
        self.footer_text_input = discord.ui.TextInput(
            label="Footer Text",
            required=False,
            max_length=2048,
            default=view.draft.get("footer_text", ""),
        )
        self.footer_image_input = discord.ui.TextInput(
            label="Footer Image (optional)",
            placeholder="https://cdn.example.com/img.png or {serveravatar}",
            required=False,
            default=view.draft.get("footer_image", ""),
        )
        self.timestamp_input = discord.ui.TextInput(
            label="Timestamp? (yes/no)",
            required=False,
            max_length=3,
            default=("yes" if view.draft.get("timestamp") else "no"),
        )
        self.add_item(self.footer_text_input)
        self.add_item(self.footer_image_input)
        self.add_item(self.timestamp_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.draft["footer_text"] = str(self.footer_text_input.value)
        self.view.draft["footer_image"] = str(self.footer_image_input.value)
        self.view.draft["timestamp"] = parse_yes_no(str(self.timestamp_input.value))
        await self.view.refresh(interaction)


class ImagesModal(discord.ui.Modal, title="Edit images"):
    def __init__(self, view: "EmbedWizardView"):
        super().__init__()
        self.view = view
        self.main_image_input = discord.ui.TextInput(
            label="Main Image",
            placeholder="https://cdn.example.com/img.png or {serveravatar}",
            required=False,
            default=view.draft.get("image", ""),
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="Thumbnail",
            placeholder="https://cdn.example.com/img.png or {useravatar}",
            required=False,
            default=view.draft.get("thumbnail", ""),
        )
        self.add_item(self.main_image_input)
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.draft["image"] = str(self.main_image_input.value)
        self.view.draft["thumbnail"] = str(self.thumbnail_input.value)
        await self.view.refresh(interaction)


# ---------------------------------------------------------------------------
# Wizard view
# ---------------------------------------------------------------------------

class EmbedWizardView(discord.ui.View):
    def __init__(self, *, guild_id: int, name: str, owner_id: int, draft: dict | None = None):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.name = name
        self.owner_id = owner_id
        self.draft = draft if draft is not None else dict(EMPTY_DRAFT)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who started this wizard can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Re-render the wizard message after a modal submission."""
        embed = build_preview_embed(self.draft, interaction.user, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="edit basic information (color / title / description)", style=discord.ButtonStyle.secondary, row=0)
    async def edit_basic(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BasicInfoModal(self))

    @discord.ui.button(label="edit author", style=discord.ButtonStyle.secondary, row=1)
    async def edit_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AuthorModal(self))

    @discord.ui.button(label="edit footer", style=discord.ButtonStyle.secondary, row=1)
    async def edit_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FooterModal(self))

    @discord.ui.button(label="edit images", style=discord.ButtonStyle.secondary, row=1)
    async def edit_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ImagesModal(self))

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success, row=2)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await mongo.save_embed(self.guild_id, self.name, self.draft, self.owner_id)
        for child in self.children:
            child.disabled = True
        embed = build_preview_embed(self.draft, interaction.user, interaction.guild)
        await interaction.response.edit_message(
            content=(
                f"\u2705 Saved embed **{self.name}**. Reference it elsewhere with "
                f"`{{embed:{self.name}}}`."
            ),
            embed=embed,
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="\u274c Embed creation cancelled. Nothing was saved.",
            embed=None,
            view=self,
        )
        self.stop()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class EmbedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    embed_group = app_commands.Group(
        name="embed",
        description="Create and manage custom embeds",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    @embed_group.command(name="create", description="Open the embed builder wizard (admin only)")
    @app_commands.describe(name="A short name to reference this embed later, e.g. welcome")
    @app_commands.checks.has_permissions(administrator=True)
    async def create(self, interaction: discord.Interaction, name: str):
        name = name.strip().lower()
        if not name:
            await interaction.response.send_message("Please provide a valid name.", ephemeral=True)
            return

        if await mongo.embed_name_exists(interaction.guild_id, name):
            await interaction.response.send_message(
                f"An embed named **{name}** already exists in this server. Choose a different name.",
                ephemeral=True,
            )
            return

        view = EmbedWizardView(
            guild_id=interaction.guild_id,
            name=name,
            owner_id=interaction.user.id,
        )
        embed = build_preview_embed(view.draft, interaction.user, interaction.guild)

        intro = (
            f"Created a new embed draft named `{name}`.\n"
            f"You can reference it with `{{embed:{name}}}` once you hit Submit "
            f"(e.g. in an autoresponder or greet/leave/boost message).\n\n"
            f"{PLACEHOLDER_GUIDE}"
        )

        await interaction.response.send_message(
            content=intro, embed=embed, view=view, ephemeral=True
        )

    @create.error
    async def create_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    @embed_group.command(name="list", description="List all saved embeds in this server (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_embeds_cmd(self, interaction: discord.Interaction):
        docs = await mongo.list_embeds(interaction.guild_id)

        if not docs:
            await interaction.response.send_message(
                "No embeds have been created in this server yet. Use `/embed create` to make one.",
                ephemeral=True,
            )
            return

        docs = sorted(docs, key=lambda d: d["name"])
        lines = [f"`{i}.` **{doc['name']}**" for i, doc in enumerate(docs, start=1)]

        embed = discord.Embed(
            title="Saved Embeds",
            description="\n".join(lines),
            color=discord.Color.dark_teal(),
        )
        embed.set_footer(text=f"{interaction.guild.name} \u2022 {len(docs)} saved")

        await interaction.response.send_message(embed=embed)

    @list_embeds_cmd.error
    async def list_embeds_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    async def embed_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        docs = await mongo.list_embeds(interaction.guild_id)
        names = sorted(doc["name"] for doc in docs)
        current = current.lower()
        matches = [name for name in names if current in name.lower()]
        return [app_commands.Choice(name=name, value=name) for name in matches[:25]]

    @embed_group.command(name="show", description="Preview a saved embed (admin only)")
    @app_commands.describe(embed="name of the embed to show")
    @app_commands.autocomplete(embed=embed_name_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def show_embed_cmd(self, interaction: discord.Interaction, embed: str):
        doc = await mongo.get_embed(interaction.guild_id, embed)
        if not doc:
            await interaction.response.send_message(
                f"No embed named **{embed}** was found in this server.",
                ephemeral=True,
            )
            return

        preview = build_preview_embed(doc, interaction.user, interaction.guild)
        await interaction.response.send_message(embed=preview)

    @show_embed_cmd.error
    async def show_embed_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error

    @embed_group.command(name="delete", description="Delete a saved embed")
    @app_commands.describe(embed="name of the embed to delete")
    @app_commands.autocomplete(embed=embed_name_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_embed_cmd(self, interaction: discord.Interaction, embed: str):
        exists = await mongo.embed_name_exists(interaction.guild_id, embed)
        if not exists:
            await interaction.response.send_message(
                f"No embed named **{embed}** was found in this server.",
                ephemeral=True,
            )
            return

        await mongo.delete_embed(interaction.guild_id, embed)
        await interaction.response.send_message(
            f"\U0001F5D1\ufe0f Deleted embed **{embed}**.",
            ephemeral=True,
        )

    @delete_embed_cmd.error
    async def delete_embed_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedCog(bot))
