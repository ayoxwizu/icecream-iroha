
"""
cogs/queue_cog.py

All commands here live under the /queue group, since Discord does not allow
a slash command to take its own arguments AND have subcommands at the same
time - so the old bare "/queue" and "/queuechannel" commands are now the
"post" and "channel" subcommands of that group, alongside the new
staff-role management commands.

/queue post <order> <user> <channel>
    Posts the configured queue message (embed, optionally with an attached
    plain message, or plain text only) to the FIXED channel set via
    /queue channel, addressed to <user>, with {order} resolved and {process}
    defaulting to the FIRST process configured in /config. The <channel>
    argument here is NOT where the message is sent - it's only used to
    resolve the {channel} placeholder inside the message text/embed.

    The message comes with a process switcher attached - either buttons or a
    dropdown menu, depending on what was chosen via /queue channel. Only
    members with Manage Server can use the switcher; everyone else gets a
    polite ephemeral refusal. The switcher always has one extra trailing
    entry, "Clear Queue" - picking it sends a non-ephemeral "Clearing the
    queue !" notice, then after 3 seconds deletes both that notice and the
    queue message itself.

    Usable by server admins (Manage Server) AND by anyone holding one of the
    roles registered via /queue staff role.

/queue channel <channel> <interaction_style>
    Admin-only (Manage Server). Sets the FIXED channel /queue post always
    posts to, and the process switcher style (now required, not optional -
    pick Buttons or Dropdown every time you run this). Required before
    /queue post can be used at all.

/queue staff role <role>
    Admin-only (Manage Server). Toggles a role's staff status: if the role
    isn't currently staff, it's added; if it already is, it's removed.
    Staff members can use /queue post without needing Manage Server.

/queue staff list
    Admin-only (Manage Server). Lists the roles currently granted staff
    access to /queue post.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from placeholders import render_text, resolve_image_field


def build_rendered_message(config: dict, *, member: discord.Member,
                            channel: discord.abc.GuildChannel, process_name: str,
                            order: str) -> dict:
    """Returns kwargs ready to splat into send_message / edit_message: {'content':..., 'embed':...}."""

    def render(template: str) -> str:
        return render_text(
            template, member=member, channel=channel, process_name=process_name, order=order
        )

    if config["style"] == "embed":
        description = render(config.get("body_template", ""))
        embed = discord.Embed(description=description)

        title = config.get("embed_title")
        if title:
            embed.title = render(title)

        color_hex = config.get("embed_color")
        if color_hex:
            try:
                embed.color = discord.Color(int(color_hex, 16))
            except ValueError:
                pass

        author_name = config.get("embed_author_name")
        if author_name:
            embed.set_author(
                name=render(author_name),
                icon_url=resolve_image_field(config.get("embed_author_icon"), member=member, guild=member.guild),
            )

        footer_text = config.get("embed_footer_text")
        if footer_text:
            embed.set_footer(
                text=render(footer_text),
                icon_url=resolve_image_field(config.get("embed_footer_icon"), member=member, guild=member.guild),
            )

        image_url = resolve_image_field(config.get("embed_image"), member=member, guild=member.guild)
        if image_url:
            embed.set_image(url=image_url)

        thumbnail_url = resolve_image_field(config.get("embed_thumbnail"), member=member, guild=member.guild)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        content = None
        if config.get("attach_plain_message") and config.get("plain_message_template"):
            content = render(config["plain_message_template"])

        return {"content": content, "embed": embed}

    return {"content": render(config.get("body_template", "")), "embed": None}


def is_admin(interaction: discord.Interaction) -> bool:
    perms = interaction.user.guild_permissions
    return perms.manage_guild or perms.administrator


def is_staff(interaction: discord.Interaction, config: dict | None) -> bool:
    """True if the invoking member holds one of the configured staff roles."""
    if not config:
        return False
    staff_role_ids = config.get("staff_role_ids") or []
    if not staff_role_ids:
        return False
    member_role_ids = {role.id for role in interaction.user.roles}
    return not set(staff_role_ids).isdisjoint(member_role_ids)


class ProcessSwitcherMixin:
    """Shared admin-only gate for both the button and dropdown variants."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (is_admin(interaction) or is_staff(interaction, self.config)):
            await interaction.response.send_message(
                "**Please refrain from using those buttons ><**",
                ephemeral=True,
            )
            asyncio.create_task(self._delete_after(interaction, delay=3))
            return False
        return True

    @staticmethod
    async def _delete_after(interaction: discord.Interaction, *, delay: float):
        await asyncio.sleep(delay)
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

    async def apply_process(self, interaction: discord.Interaction, process_name: str):
        rendered = build_rendered_message(
            self.config,
            member=self.target_member,
            channel=self.placeholder_channel,
            process_name=process_name,
            order=self.order,
        )
        await interaction.response.edit_message(**rendered, view=self)

    async def clear_queue(self, interaction: discord.Interaction):
        """Called when the trailing 'Clear Queue' button/option is picked.

        Sends a non-ephemeral notice, then after 3 seconds deletes both that
        notice and the queue message itself.
        """
        queue_message = interaction.message
        await interaction.response.send_message("**Clearing the queue !**", ephemeral=False)
        notice = await interaction.original_response()
        asyncio.create_task(self._clear_after(notice, queue_message, delay=3))

    @staticmethod
    async def _clear_after(notice_message: discord.Message, queue_message: discord.Message, *, delay: float):
        await asyncio.sleep(delay)
        try:
            await notice_message.delete()
        except discord.HTTPException:
            pass
        try:
            await queue_message.delete()
        except discord.HTTPException:
            pass


class QueueButtonsView(ProcessSwitcherMixin, discord.ui.View):
    def __init__(self, config: dict, target_member: discord.Member,
                 placeholder_channel: discord.abc.GuildChannel, order: str):
        super().__init__(timeout=None)
        self.config = config
        self.target_member = target_member
        self.placeholder_channel = placeholder_channel
        self.order = order

        for process in config["processes"]:
            button = discord.ui.Button(
                label=process["name"],
                emoji=process["emoji"] or None,
                style=discord.ButtonStyle.secondary,
            )
            button.callback = self._make_callback(process["name"])
            self.add_item(button)

        clear_button = discord.ui.Button(
            label="Clear Queue",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
        )
        clear_button.callback = self.clear_queue
        self.add_item(clear_button)

    def _make_callback(self, process_name: str):
        async def callback(interaction: discord.Interaction):
            await self.apply_process(interaction, process_name)
        return callback


class QueueDropdown(discord.ui.Select):
    CLEAR_VALUE = "__clear_queue__"

    def __init__(self, parent_view: "QueueDropdownView", config: dict):
        options = [
            discord.SelectOption(label=p["name"], value=p["name"], emoji=p["emoji"] or None)
            for p in config["processes"]
        ]
        options.append(
            discord.SelectOption(label="Clear Queue", value=self.CLEAR_VALUE, emoji="🗑️")
        )
        super().__init__(placeholder="Switch process...", options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == self.CLEAR_VALUE:
            await self.parent_view.clear_queue(interaction)
        else:
            await self.parent_view.apply_process(interaction, self.values[0])


class QueueDropdownView(ProcessSwitcherMixin, discord.ui.View):
    def __init__(self, config: dict, target_member: discord.Member,
                 placeholder_channel: discord.abc.GuildChannel, order: str):
        super().__init__(timeout=None)
        self.config = config
        self.target_member = target_member
        self.placeholder_channel = placeholder_channel
        self.order = order
        self.add_item(QueueDropdown(self, config))


class QueueCog(commands.Cog):
    queue_group = app_commands.Group(name="queue", description="Queue posting and configuration")
    staff_subgroup = app_commands.Group(
        name="staff", description="Manage which roles can use /queue post", parent=queue_group
    )

    def __init__(self, bot: commands.Bot, db):
        self.bot = bot
        self.db = db

    # -----------------------------------------------------------------
    # /queue post
    # -----------------------------------------------------------------
    @queue_group.command(name="post", description="Post a queue message for a process")
    @app_commands.describe(
        order="The order text/number to display (e.g. 'burger' or '3')",
        user="The user this queue entry is for",
        channel="Channel to reference via the {channel} placeholder (the message itself always posts to the channel set via /queue channel)",
    )
    async def post(self, interaction: discord.Interaction, order: str, user: discord.Member,
                    channel: discord.TextChannel):
        config = await self.db.get_config(interaction.guild_id)

        if not (is_admin(interaction) or is_staff(interaction, config)):
            await interaction.response.send_message(
                "You need to be a server admin (Manage Server) or hold a configured staff role "
                "to use this. Ask an admin to add your role with `/queue staff role`.",
                ephemeral=True,
            )
            return

        if not config or not config.get("processes") or not config.get("body_template"):
            await interaction.response.send_message(
                "This server hasn't been configured yet. Run `/config` first.", ephemeral=True
            )
            return

        if "queue_channel_id" not in config:
            await interaction.response.send_message(
                "No queue channel has been set yet. Run `/queue channel` first "
                "(it also sets the process switcher style) - then you can use `/queue post`.",
                ephemeral=True,
            )
            return

        target_channel = interaction.guild.get_channel(config["queue_channel_id"])
        if target_channel is None:
            await interaction.response.send_message(
                "The channel set via `/queue channel` no longer exists. "
                "Run `/queue channel` again to set a new one.",
                ephemeral=True,
            )
            return

        default_process = config["processes"][0]["name"]
        rendered = build_rendered_message(
            config, member=user, channel=channel, process_name=default_process, order=order
        )

        if config.get("interaction_style") == "dropdown":
            view = QueueDropdownView(config, user, channel, order)
        else:
            view = QueueButtonsView(config, user, channel, order)

        await target_channel.send(**rendered, view=view)
        await interaction.response.send_message(
            f"✅ Queue message posted in {target_channel.mention}.", ephemeral=True
        )
        await interaction.channel.send(f"**{user.mention} you have been added to the queue :3**")

    # -----------------------------------------------------------------
    # /queue channel
    # -----------------------------------------------------------------
    @queue_group.command(name="channel", description="Set the fixed channel and process switcher style for /queue post")
    @app_commands.describe(
        channel="The channel /queue post messages will always be posted to",
        interaction_style="How the process switcher works on /queue post messages",
    )
    @app_commands.choices(interaction_style=[
        app_commands.Choice(name="Interactive Buttons", value="buttons"),
        app_commands.Choice(name="Dropdown Menu", value="dropdown"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel,
                       interaction_style: app_commands.Choice[str]):
        await self.db.upsert_config(interaction.guild_id, {
            "queue_channel_id": channel.id,
            "interaction_style": interaction_style.value,
        })

        await interaction.response.send_message(
            f"✅ Queue messages will now always be sent to {channel.mention}.\n"
            f"Process switcher set to **{interaction_style.name}**.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------
    # /queue staff role
    # -----------------------------------------------------------------
    @staff_subgroup.command(name="role", description="Toggle a role's staff access to /queue post")
    @app_commands.describe(role="The role to add as staff, or remove if it's already staff")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staff_role(self, interaction: discord.Interaction, role: discord.Role):
        config = await self.db.get_config(interaction.guild_id) or {}
        staff_role_ids = list(config.get("staff_role_ids") or [])

        if role.id in staff_role_ids:
            staff_role_ids.remove(role.id)
            message = f"✅ {role.mention} removed from the staff list."
        else:
            staff_role_ids.append(role.id)
            message = f"✅ {role.mention} added to the staff list."

        await self.db.upsert_config(interaction.guild_id, {"staff_role_ids": staff_role_ids})
        await interaction.response.send_message(message, ephemeral=True)

    # -----------------------------------------------------------------
    # /queue staff list
    # -----------------------------------------------------------------
    @staff_subgroup.command(name="list", description="Show the roles currently granted staff access to /queue post")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staff_list(self, interaction: discord.Interaction):
        config = await self.db.get_config(interaction.guild_id) or {}
        staff_role_ids = config.get("staff_role_ids") or []

        if not staff_role_ids:
            await interaction.response.send_message(
                "No staff roles configured yet. Use `/queue staff role` to add one.", ephemeral=True
            )
            return

        lines = []
        for role_id in staff_role_ids:
            role = interaction.guild.get_role(role_id)
            lines.append(role.mention if role else f"*(deleted role - id {role_id})*")

        await interaction.response.send_message(
            "**Staff roles (can use `/queue post`):**\n" + "\n".join(lines), ephemeral=True
        )

    # -----------------------------------------------------------------
    # shared error handler for the admin-gated subcommands
    # -----------------------------------------------------------------
    @channel.error
    @staff_role.error
    @staff_list.error
    async def admin_only_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to run this command.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(QueueCog(bot, bot.db))
