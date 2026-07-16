"""
cogs/rename.py

Admin-only server-management commands, ported from the standalone
rename-bot script into a cog so it can run inside the shared launcher
alongside the embed/autoresponder/sticky/events bot and the queue bot.

    /renamech <old_channel> <new_name>   - rename a channel
    /renamerole <old_role> <new_name>    - rename a role
    /renamecat <old_category> <new_name> - rename a category
    /delcategory <category>              - delete a category (channels
                                            inside are moved out, not deleted)
    /viewall <channel> [role]            - toggle channel visibility for
                                            @everyone, or a single role
    /messageall <channel> [role]         - toggle Send Messages for
                                            @everyone, or a single role
    /help                                - short description of the above

Every successful rename/delete/toggle is logged to MongoDB via
self.rename_logs (a Motor collection handed in from RenameBot).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("cogs.rename")


def is_admin():
    """Check decorator: only members with Administrator permission can run."""
    def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)


COMMAND_HELP = [
    ("/renamech", "Rename a channel. Pick the old channel from a list, give it a new name."),
    ("/renamerole", "Rename a role. Pick the old role from a list, give it a new name."),
    ("/renamecat", "Rename a category. Pick the old category from a list, give it a new name."),
    ("/delcategory", "Delete a category. Pick it from a list. Channels inside are moved out, not deleted."),
    ("/viewall", "Toggle whether @everyone (or one specific role) can see a channel."),
    ("/messageall", "Toggle whether @everyone (or one specific role) can send messages in a channel."),
]


class RenameCog(commands.Cog):
    def __init__(self, bot: commands.Bot, rename_logs):
        self.bot = bot
        self.rename_logs = rename_logs

    # -----------------------------------------------------------------
    # logging helpers
    # -----------------------------------------------------------------
    async def log_rename(self, kind: str, interaction: discord.Interaction,
                          old_name: str, new_name: str, target_id: int):
        try:
            await self.rename_logs.insert_one({
                "type": kind,  # "channel", "role", or "category"
                "guild_id": interaction.guild_id,
                "guild_name": interaction.guild.name if interaction.guild else None,
                "target_id": target_id,
                "old_name": old_name,
                "new_name": new_name,
                "renamed_by_id": interaction.user.id,
                "renamed_by_name": str(interaction.user),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception:
            log.exception("Failed to write rename log to MongoDB")

    async def log_deletion(self, interaction: discord.Interaction, kind: str,
                            name: str, target_id: int):
        try:
            await self.rename_logs.insert_one({
                "type": f"{kind}_delete",
                "guild_id": interaction.guild_id,
                "guild_name": interaction.guild.name if interaction.guild else None,
                "target_id": target_id,
                "name": name,
                "deleted_by_id": interaction.user.id,
                "deleted_by_name": str(interaction.user),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception:
            log.exception("Failed to write deletion log to MongoDB")

    async def log_visibility(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel,
                              target: discord.Role, was_visible: bool, now_visible: bool):
        try:
            await self.rename_logs.insert_one({
                "type": "visibility_toggle",
                "guild_id": interaction.guild_id,
                "guild_name": interaction.guild.name if interaction.guild else None,
                "target_id": channel.id,
                "channel_name": channel.name,
                "role_id": target.id,
                "role_name": target.name,
                "was_visible": was_visible,
                "now_visible": now_visible,
                "toggled_by_id": interaction.user.id,
                "toggled_by_name": str(interaction.user),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception:
            log.exception("Failed to write visibility log to MongoDB")

    async def log_send_toggle(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel,
                               target: discord.Role, was_allowed: bool, now_allowed: bool):
        try:
            await self.rename_logs.insert_one({
                "type": "send_messages_toggle",
                "guild_id": interaction.guild_id,
                "guild_name": interaction.guild.name if interaction.guild else None,
                "target_id": channel.id,
                "channel_name": channel.name,
                "role_id": target.id,
                "role_name": target.name,
                "was_allowed": was_allowed,
                "now_allowed": now_allowed,
                "toggled_by_id": interaction.user.id,
                "toggled_by_name": str(interaction.user),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception:
            log.exception("Failed to write send-messages log to MongoDB")

    # -----------------------------------------------------------------
    # /renamech
    # -----------------------------------------------------------------
    @app_commands.command(name="renamech", description="Rename an existing channel (admin only)")
    @app_commands.describe(
        old_channel="Pick the channel you want to rename",
        new_name="The new name for the channel",
    )
    @is_admin()
    async def renamech(self, interaction: discord.Interaction,
                        old_channel: discord.abc.GuildChannel, new_name: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        old_name = old_channel.name
        bot_member = interaction.guild.me

        perms = old_channel.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.followup.send(
                f"⚠️ I don't have **Manage Channels** permission for {old_channel.mention}.",
                ephemeral=True,
            )
            return

        try:
            await old_channel.edit(name=new_name, reason=f"Renamed by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I lack permission to rename that channel.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Discord rejected that name: {e.text}", ephemeral=True)
            return

        await self.log_rename("channel", interaction, old_name, new_name, old_channel.id)
        await interaction.followup.send(
            f"✅ Renamed channel **#{old_name}** → **#{new_name}**", ephemeral=True
        )

    # -----------------------------------------------------------------
    # /renamecat
    # -----------------------------------------------------------------
    @app_commands.command(name="renamecat", description="Rename an existing category (admin only)")
    @app_commands.describe(
        old_category="Pick the category you want to rename",
        new_name="The new name for the category",
    )
    @is_admin()
    async def renamecat(self, interaction: discord.Interaction,
                         old_category: discord.CategoryChannel, new_name: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        old_name = old_category.name
        bot_member = interaction.guild.me

        perms = old_category.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.followup.send(
                f"⚠️ I don't have **Manage Channels** permission for the **{old_name}** category.",
                ephemeral=True,
            )
            return

        try:
            await old_category.edit(name=new_name, reason=f"Renamed by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I lack permission to rename that category.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Discord rejected that name: {e.text}", ephemeral=True)
            return

        await self.log_rename("category", interaction, old_name, new_name, old_category.id)
        await interaction.followup.send(
            f"✅ Renamed category **{old_name}** → **{new_name}**", ephemeral=True
        )

    # -----------------------------------------------------------------
    # /delcategory
    # -----------------------------------------------------------------
    @app_commands.command(name="delcategory", description="Delete a category (admin only)")
    @app_commands.describe(category="Pick the category you want to delete")
    @is_admin()
    async def delcategory(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await interaction.response.defer(ephemeral=True, thinking=True)

        old_name = category.name
        old_id = category.id
        bot_member = interaction.guild.me

        perms = category.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.followup.send(
                f"⚠️ I don't have **Manage Channels** permission for the **{old_name}** category.",
                ephemeral=True,
            )
            return

        child_count = len(category.channels)

        try:
            await category.delete(reason=f"Deleted by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I lack permission to delete that category.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Discord rejected that request: {e.text}", ephemeral=True)
            return

        await self.log_deletion(interaction, "category", old_name, old_id)

        note = f" ({child_count} channel(s) inside were moved out, not deleted.)" if child_count else ""
        await interaction.followup.send(f"🗑️ Deleted category **{old_name}**.{note}", ephemeral=True)

    # -----------------------------------------------------------------
    # /renamerole
    # -----------------------------------------------------------------
    @app_commands.command(name="renamerole", description="Rename an existing role (admin only)")
    @app_commands.describe(
        old_role="Pick the role you want to rename",
        new_name="The new name for the role",
    )
    @is_admin()
    async def renamerole(self, interaction: discord.Interaction, old_role: discord.Role, new_name: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        old_name = old_role.name
        bot_member = interaction.guild.me

        if not bot_member.guild_permissions.manage_roles:
            await interaction.followup.send(
                "⚠️ I don't have **Manage Roles** permission in this server.", ephemeral=True
            )
            return

        if old_role.is_default():
            await interaction.followup.send("⚠️ The `@everyone` role can't be renamed.", ephemeral=True)
            return

        if old_role >= bot_member.top_role:
            await interaction.followup.send(
                f"⚠️ I can't edit **{old_role.name}** because it's positioned "
                f"above (or equal to) my own top role. Move my role higher and try again.",
                ephemeral=True,
            )
            return

        try:
            await old_role.edit(name=new_name, reason=f"Renamed by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I lack permission to rename that role.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Discord rejected that name: {e.text}", ephemeral=True)
            return

        await self.log_rename("role", interaction, old_name, new_name, old_role.id)
        await interaction.followup.send(
            f"✅ Renamed role **{old_name}** → **{new_name}**", ephemeral=True
        )

    # -----------------------------------------------------------------
    # /viewall
    # -----------------------------------------------------------------
    @app_commands.command(name="viewall", description="Toggle whether everyone (or a specific role) can see a channel (admin only)")
    @app_commands.describe(
        channel="Pick the channel to toggle visibility for",
        role="Optional: only toggle visibility for this role instead of @everyone",
    )
    @is_admin()
    async def viewall(self, interaction: discord.Interaction,
                       channel: discord.abc.GuildChannel, role: discord.Role = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        target_role = role or guild.default_role
        bot_member = guild.me

        perms = channel.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.followup.send(
                f"⚠️ I don't have **Manage Channels** permission for {channel.mention}.",
                ephemeral=True,
            )
            return

        if role is not None and role >= bot_member.top_role:
            await interaction.followup.send(
                f"⚠️ I can't set permissions for **{role.name}** because it's positioned "
                f"above (or equal to) my own top role.",
                ephemeral=True,
            )
            return

        overwrite = channel.overwrites_for(target_role)
        currently_visible = overwrite.view_channel is not False
        new_visible = not currently_visible
        overwrite.view_channel = new_visible

        try:
            await channel.set_permissions(
                target_role, overwrite=overwrite, reason=f"Visibility toggled by {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I lack permission to edit that channel's permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Discord rejected that change: {e.text}", ephemeral=True)
            return

        await self.log_visibility(interaction, channel, target_role, currently_visible, new_visible)

        who = "everyone" if role is None else f"**{target_role.name}**"
        if new_visible:
            await interaction.followup.send(f"👁️ **{channel.mention}** is now **visible** to {who}.", ephemeral=True)
        else:
            await interaction.followup.send(f"🙈 **{channel.mention}** is now **hidden** from {who}.", ephemeral=True)

    # -----------------------------------------------------------------
    # /messageall
    # -----------------------------------------------------------------
    @app_commands.command(name="messageall", description="Toggle whether everyone (or a specific role) can send messages in a channel (admin only)")
    @app_commands.describe(
        channel="Pick the channel to toggle messaging permission for",
        role="Optional: only toggle messaging permission for this role instead of @everyone",
    )
    @is_admin()
    async def messageall(self, interaction: discord.Interaction,
                          channel: discord.abc.GuildChannel, role: discord.Role = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        target_role = role or guild.default_role
        bot_member = guild.me

        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel,
                                     discord.StageChannel, discord.ForumChannel,
                                     discord.Thread)):
            await interaction.followup.send(
                f"⚠️ **{channel.mention}** doesn't support a send-messages permission.", ephemeral=True
            )
            return

        perms = channel.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.followup.send(
                f"⚠️ I don't have **Manage Channels** permission for {channel.mention}.", ephemeral=True
            )
            return

        if role is not None and role >= bot_member.top_role:
            await interaction.followup.send(
                f"⚠️ I can't set permissions for **{role.name}** because it's positioned "
                f"above (or equal to) my own top role.",
                ephemeral=True,
            )
            return

        overwrite = channel.overwrites_for(target_role)
        currently_allowed = overwrite.send_messages is not False
        new_allowed = not currently_allowed
        overwrite.send_messages = new_allowed

        try:
            await channel.set_permissions(
                target_role, overwrite=overwrite,
                reason=f"Send-messages permission toggled by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I lack permission to edit that channel's permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Discord rejected that change: {e.text}", ephemeral=True)
            return

        await self.log_send_toggle(interaction, channel, target_role, currently_allowed, new_allowed)

        who = "Everyone" if role is None else f"**{target_role.name}**"
        if new_allowed:
            await interaction.followup.send(f"💬 {who} can now **send messages** in {channel.mention}.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"🔇 {who} is now **blocked from sending messages** in {channel.mention}.", ephemeral=True
            )

    # -----------------------------------------------------------------
    # /help
    # -----------------------------------------------------------------
    @app_commands.command(name="help", description="Show a short description of every admin command")
    @is_admin()
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Admin Commands", color=discord.Color.blurple())
        for name, desc in COMMAND_HELP:
            embed.add_field(name=name, value=desc, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------
    # shared error handler for every command in this cog
    # -----------------------------------------------------------------
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     error: app_commands.AppCommandError):
        if isinstance(error, (app_commands.CheckFailure, app_commands.MissingPermissions)):
            msg = "🚫 You need **Administrator** permission to use this command."
        else:
            log.exception("Unhandled app command error", exc_info=error)
            msg = "⚠️ Something went wrong while running that command."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RenameCog(bot, bot.rename_logs))
