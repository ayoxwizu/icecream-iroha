"""
cogs/config_cog.py

Implements /config as a guided, multi-step setup wizard:

  1. Modal:  "How many processes do you want (max 5)" -> integer input + Submit
  2. Modal:  one text field per process -> user enters "Name | emoji" per field + Submit
  3. View:   two buttons -> "Embed" or "Plain Text"

  If Plain Text:
    4. Modal: body text -> proceeds straight to step 6.

  If Embed:
    4. Panel: buttons -> "Edit Basic Info", "Edit Author", "Edit Footer",
              "Edit Images", "Continue". Each Edit button opens a modal that
              updates the stored config; Continue moves on once description
              is set.
    5. View:  "Attach a plain message alongside the embed?" Yes / No.
              Yes opens a modal for that plain message text.

Everything is saved to MongoDB at the end via Database.upsert_config. The
process switcher style (buttons vs dropdown) is NOT chosen here anymore -
it's set (and can be changed any time, without rerunning /config) via
/queuechannel in queue_cog.py.

IMPORTANT: Discord does not allow opening a modal directly from inside
another modal's on_submit - modals can only be triggered by a slash command
or a component (button/select). Every modal->modal transition below goes
through an intermediate button.
"""

import discord
from discord import app_commands
from discord.ext import commands

from placeholders import is_valid_image_field

MAX_PROCESSES = 5

PLACEHOLDER_HELP = (
    "**Placeholders you can use (everywhere - body, title, author, footer):**\n"
    "`{username}` - mentions the user\n"
    "`{user}` - the user's account username, no mention\n"
    "`{user_name}` - the user's display name, no mention\n"
    "`{channel}` - mentions the channel\n"
    "`{process}` - the current process name\n"
    "`{order}` - whatever text/number was passed to `/queue` (e.g. `burger`, `3`)\n"
    "`{servername}` - the server's name\n\n"
    "**Embed image/icon fields only** (author icon, footer icon, image, thumbnail):\n"
    "`{useravatar}` - the user's avatar\n"
    "`{serverravatar}` (or `{serveravatar}`) - the server's icon\n"
    "(or just paste a raw image URL instead)\n\n"
    "The first process you entered in step 2 is the default process shown "
    "when `/queue` is first posted. Use `/queue channel` to set the default "
    "channel and the process switcher style (buttons or dropdown) - server "
    "admins (Manage Server permission) can re-run it any time to change "
    "either without going through `/config` again."
)


def parse_process_field(raw: str) -> tuple[str, str]:
    """
    Parses a single "Name | emoji" field into (name, emoji).
    Falls back gracefully if the user forgot the separator or the emoji.
    """
    raw = raw.strip()
    if "|" in raw:
        name, emoji = raw.split("|", 1)
        return name.strip(), emoji.strip()
    parts = raw.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return raw, ""


# ---------------------------------------------------------------------------
# Step 1 - process count
# ---------------------------------------------------------------------------

class ProcessCountModal(discord.ui.Modal, title="Step 1 - Process Count"):
    count_input = discord.ui.TextInput(
        label=f"How many processes do you want? (max {MAX_PROCESSES})",
        placeholder="e.g. 3",
        min_length=1,
        max_length=1,
        required=True,
    )

    def __init__(self, cog: "ConfigCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.count_input.value.strip()
        if not raw.isdigit() or not (1 <= int(raw) <= MAX_PROCESSES):
            await interaction.response.send_message(
                f"Please enter a whole number between 1 and {MAX_PROCESSES}. Run `/config` again to retry.",
                ephemeral=True,
            )
            return

        count = int(raw)
        view = ContinueToDetailsView(self.cog, count)
        await interaction.response.send_message(
            f"Got it - {count} process(es). Click below to enter their names and emojis.",
            view=view,
            ephemeral=True,
        )


class ContinueToDetailsView(discord.ui.View):
    def __init__(self, cog: "ConfigCog", count: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.count = count

    @discord.ui.button(label="Enter Process Details", style=discord.ButtonStyle.primary, emoji="📝")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProcessDetailsModal(self.cog, self.count))
        self.stop()


# ---------------------------------------------------------------------------
# Step 2 - process details
# ---------------------------------------------------------------------------

class ProcessDetailsModal(discord.ui.Modal, title="Step 2 - Process Details"):
    def __init__(self, cog: "ConfigCog", count: int):
        super().__init__()
        self.cog = cog
        self.count = count
        self.fields_inputs: list[discord.ui.TextInput] = []

        for i in range(count):
            field = discord.ui.TextInput(
                label=f"Process {i + 1}: name + emoji",
                placeholder="e.g. Cooking | 🍳",
                required=True,
                max_length=100,
            )
            self.fields_inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        processes = []
        for field in self.fields_inputs:
            name, emoji = parse_process_field(field.value)
            processes.append({"name": name, "emoji": emoji})

        self.cog.pending[interaction.guild_id] = {"processes": processes}

        view = StyleChoiceView(self.cog)
        summary = "\n".join(f"{p['emoji']} {p['name']}" for p in processes)
        await interaction.response.send_message(
            f"**Processes saved:**\n{summary}\n\n"
            "Step 3 - Do you want the queue body to be an **embed** or **plain text**?",
            view=view,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Step 3 - embed vs plain text
# ---------------------------------------------------------------------------

class StyleChoiceView(discord.ui.View):
    def __init__(self, cog: "ConfigCog"):
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(label="Embed", style=discord.ButtonStyle.primary, emoji="🧩")
    async def embed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["style"] = "embed"
        view = EmbedPanelView(self.cog)
        await interaction.response.send_message(
            "**Step 4 - Build your embed.** Edit each section below, then hit "
            "**Continue** once you've at least set a description.\n\n"
            + view.summary(self.cog.pending[interaction.guild_id]),
            view=view,
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="Plain Text", style=discord.ButtonStyle.secondary, emoji="📝")
    async def plain_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.pending.setdefault(interaction.guild_id, {})["style"] = "plain"
        await interaction.response.send_modal(PlainBodyModal(self.cog))
        self.stop()


class PlainBodyModal(discord.ui.Modal, title="Step 4 - Plain Text Body"):
    def __init__(self, cog: "ConfigCog"):
        super().__init__()
        self.cog = cog
        self.body_input = discord.ui.TextInput(
            label="Body text",
            style=discord.TextStyle.paragraph,
            placeholder="e.g. {username} joined the {process} queue in {channel}!",
            required=True,
            max_length=1000,
        )
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        data = self.cog.pending.setdefault(guild_id, {})
        data["body_template"] = self.body_input.value
        await finish_config(self.cog, interaction)


# ---------------------------------------------------------------------------
# Step 4 (embed path) - the embed builder panel
# ---------------------------------------------------------------------------

class EmbedPanelView(discord.ui.View):
    def __init__(self, cog: "ConfigCog"):
        super().__init__(timeout=300)
        self.cog = cog

    def summary(self, data: dict) -> str:
        lines = [
            f"**Title:** {data.get('embed_title') or '*(none)*'}",
            f"**Description:** {data.get('body_template') or '*(not set yet - required)*'}",
            f"**Color:** {data.get('embed_color') or '*(default)*'}",
            f"**Author:** {data.get('embed_author_name') or '*(none)*'}"
            + (f" / icon: `{data['embed_author_icon']}`" if data.get('embed_author_icon') else ""),
            f"**Footer:** {data.get('embed_footer_text') or '*(none)*'}"
            + (f" / icon: `{data['embed_footer_icon']}`" if data.get('embed_footer_icon') else ""),
            f"**Image:** {data.get('embed_image') or '*(none)*'}",
            f"**Thumbnail:** {data.get('embed_thumbnail') or '*(none)*'}",
        ]
        return "\n".join(lines)

    @discord.ui.button(label="Edit Basic Info", style=discord.ButtonStyle.primary, row=0)
    async def edit_basic(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.get(interaction.guild_id, {})
        await interaction.response.send_modal(BasicInfoModal(self.cog, data))

    @discord.ui.button(label="Edit Author", style=discord.ButtonStyle.secondary, row=0)
    async def edit_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.get(interaction.guild_id, {})
        await interaction.response.send_modal(AuthorModal(self.cog, data))

    @discord.ui.button(label="Edit Footer", style=discord.ButtonStyle.secondary, row=0)
    async def edit_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.get(interaction.guild_id, {})
        await interaction.response.send_modal(FooterModal(self.cog, data))

    @discord.ui.button(label="Edit Images", style=discord.ButtonStyle.secondary, row=1)
    async def edit_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.get(interaction.guild_id, {})
        await interaction.response.send_modal(ImagesModal(self.cog, data))

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success, emoji="➡️", row=1)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.get(interaction.guild_id, {})
        if not data.get("body_template"):
            await interaction.response.send_message(
                "Please set a description first via **Edit Basic Info**.", ephemeral=True
            )
            return

        view = PlainAttachChoiceView(self.cog)
        await interaction.response.send_message(
            "Step 5 - Do you want to attach a **plain message** alongside the embed?",
            view=view,
            ephemeral=True,
        )
        self.stop()


def _after_edit_message(cog: "ConfigCog", data: dict) -> tuple[str, "EmbedPanelView"]:
    view = EmbedPanelView(cog)
    return "✅ Updated. Keep editing or hit **Continue**.\n\n" + view.summary(data), view


class BasicInfoModal(discord.ui.Modal, title="Edit Basic Info"):
    def __init__(self, cog: "ConfigCog", data: dict):
        super().__init__()
        self.cog = cog
        self.title_input = discord.ui.TextInput(
            label="Embed title",
            placeholder="e.g. Queue: {process}",
            default=data.get("embed_title", ""),
            required=False,
            max_length=256,
        )
        self.description_input = discord.ui.TextInput(
            label="Description (the embed body)",
            style=discord.TextStyle.paragraph,
            placeholder="e.g. {username} you're #{order} for {process} in {channel}!",
            default=data.get("body_template", ""),
            required=True,
            max_length=2000,
        )
        self.color_input = discord.ui.TextInput(
            label="Color (hex, no #)",
            placeholder="e.g. 5865F2",
            default=data.get("embed_color", ""),
            required=False,
            max_length=6,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["embed_title"] = self.title_input.value
        data["body_template"] = self.description_input.value
        color_raw = self.color_input.value.strip().lstrip("#")
        if color_raw:
            try:
                int(color_raw, 16)
                data["embed_color"] = color_raw
            except ValueError:
                await interaction.response.send_message(
                    "That color isn't valid hex - skipped saving it. Try again via Edit Basic Info.",
                    ephemeral=True,
                )
                return
        content, view = _after_edit_message(self.cog, data)
        await interaction.response.send_message(content, view=view, ephemeral=True)


class AuthorModal(discord.ui.Modal, title="Edit Author"):
    def __init__(self, cog: "ConfigCog", data: dict):
        super().__init__()
        self.cog = cog
        self.name_input = discord.ui.TextInput(
            label="Author name",
            placeholder="e.g. {user_name}",
            default=data.get("embed_author_name", ""),
            required=False,
            max_length=256,
        )
        self.icon_input = discord.ui.TextInput(
            label="Author icon",
            placeholder="{useravatar}, {serverravatar}, or a URL",
            default=data.get("embed_author_icon", ""),
            required=False,
            max_length=500,
        )
        self.add_item(self.name_input)
        self.add_item(self.icon_input)

    async def on_submit(self, interaction: discord.Interaction):
        icon = self.icon_input.value
        if not is_valid_image_field(icon):
            await interaction.response.send_message(
                f"`{icon}` isn't a valid icon - use `{{useravatar}}`, `{{serverravatar}}` (or `{{serveravatar}}`), "
                "or a URL starting with `http://`/`https://`. Nothing was saved, try again "
                "via **Edit Author**.",
                ephemeral=True,
            )
            return
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["embed_author_name"] = self.name_input.value
        data["embed_author_icon"] = icon
        content, view = _after_edit_message(self.cog, data)
        await interaction.response.send_message(content, view=view, ephemeral=True)


class FooterModal(discord.ui.Modal, title="Edit Footer"):
    def __init__(self, cog: "ConfigCog", data: dict):
        super().__init__()
        self.cog = cog
        self.text_input = discord.ui.TextInput(
            label="Footer text",
            placeholder="e.g. Queue System",
            default=data.get("embed_footer_text", ""),
            required=False,
            max_length=2048,
        )
        self.icon_input = discord.ui.TextInput(
            label="Footer icon",
            placeholder="{useravatar}, {serverravatar}, or a URL",
            default=data.get("embed_footer_icon", ""),
            required=False,
            max_length=500,
        )
        self.add_item(self.text_input)
        self.add_item(self.icon_input)

    async def on_submit(self, interaction: discord.Interaction):
        icon = self.icon_input.value
        if not is_valid_image_field(icon):
            await interaction.response.send_message(
                f"`{icon}` isn't a valid icon - use `{{useravatar}}`, `{{serverravatar}}` (or `{{serveravatar}}`), "
                "or a URL starting with `http://`/`https://`. Nothing was saved, try again "
                "via **Edit Footer**.",
                ephemeral=True,
            )
            return
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["embed_footer_text"] = self.text_input.value
        data["embed_footer_icon"] = icon
        content, view = _after_edit_message(self.cog, data)
        await interaction.response.send_message(content, view=view, ephemeral=True)


class ImagesModal(discord.ui.Modal, title="Edit Images"):
    def __init__(self, cog: "ConfigCog", data: dict):
        super().__init__()
        self.cog = cog
        self.image_input = discord.ui.TextInput(
            label="Main image",
            placeholder="{useravatar}, {serverravatar}, or a URL",
            default=data.get("embed_image", ""),
            required=False,
            max_length=500,
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="Thumbnail",
            placeholder="{useravatar}, {serverravatar}, or a URL",
            default=data.get("embed_thumbnail", ""),
            required=False,
            max_length=500,
        )
        self.add_item(self.image_input)
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: discord.Interaction):
        image = self.image_input.value
        thumbnail = self.thumbnail_input.value
        bad = next((v for v in (image, thumbnail) if not is_valid_image_field(v)), None)
        if bad is not None:
            await interaction.response.send_message(
                f"`{bad}` isn't a valid image - use `{{useravatar}}`, `{{serverravatar}}` (or `{{serveravatar}}`), "
                "or a URL starting with `http://`/`https://`. Nothing was saved, try again "
                "via **Edit Images**.",
                ephemeral=True,
            )
            return
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["embed_image"] = image
        data["embed_thumbnail"] = thumbnail
        content, view = _after_edit_message(self.cog, data)
        await interaction.response.send_message(content, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Step 5 (embed path only) - attach a plain message alongside the embed?
# ---------------------------------------------------------------------------

class PlainAttachChoiceView(discord.ui.View):
    def __init__(self, cog: "ConfigCog"):
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["attach_plain_message"] = True
        await interaction.response.send_modal(PlainMessageModal(self.cog))
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["attach_plain_message"] = False
        await finish_config(self.cog, interaction)
        self.stop()


class PlainMessageModal(discord.ui.Modal, title="Plain Message Text"):
    def __init__(self, cog: "ConfigCog"):
        super().__init__()
        self.cog = cog
        self.text_input = discord.ui.TextInput(
            label="Plain message (sent alongside the embed)",
            style=discord.TextStyle.paragraph,
            placeholder="e.g. {username} you're up next for {process}!",
            required=True,
            max_length=1500,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        data = self.cog.pending.setdefault(interaction.guild_id, {})
        data["plain_message_template"] = self.text_input.value
        await finish_config(self.cog, interaction)


# ---------------------------------------------------------------------------
# Final step - save the config (switcher style is no longer chosen here;
# it's set via /queuechannel instead)
# ---------------------------------------------------------------------------

async def finish_config(cog: "ConfigCog", interaction: discord.Interaction):
    guild_id = interaction.guild_id
    data = cog.pending.pop(guild_id, {})

    await cog.db.upsert_config(guild_id, data)

    await interaction.response.send_message(
        "✅ Configuration saved! Run `/queue channel` to set the default "
        "channel and pick the process switcher style (buttons or dropdown), "
        "then `/queue <order> <user> [channel]` to try it out.\n\n"
        + PLACEHOLDER_HELP,
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# The cog
# ---------------------------------------------------------------------------

class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db):
        self.bot = bot
        self.db = db
        # Temporary in-memory state while a guild is mid-wizard: {guild_id: {...}}
        self.pending: dict[int, dict] = {}

    @app_commands.command(name="config", description="Set up the queue bot for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ProcessCountModal(self))

    @config.error
    async def config_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to run this command.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot, bot.db))
