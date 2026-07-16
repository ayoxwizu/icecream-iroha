"""
rename_bot.py

Bot 3: the channel/role/category rename & permission-toggle bot, restructured
from its original standalone script into the same class + cog shape as
EmbedBot (bot.py) and QueueBot (main.py), so launcher.py can run all three
side by side in one process.

Required environment variables (kept separate from bots 1 and 2 so nothing
collides on Railway):
    TOKEN3      - Discord bot token for this bot
    GUILD_ID3   - the single guild this bot is allowed to run in (it leaves
                  any other guild - see RenameBot.on_ready / on_guild_join)
    MONGO_URI3  - MongoDB connection string used for the rename/audit log

Optional:
    MONGO_DB_NAME3 - overrides the database name (default "rename_bot")
"""

from __future__ import annotations

import logging
import os

import certifi
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger("rename-bot")

INTENTS = discord.Intents.default()
# No message_content / members intent needed - slash commands plus native
# channel/role picker arguments don't require privileged intents.


class RenameBot(commands.Bot):
    def __init__(self, guild_id: int):
        super().__init__(command_prefix="!", intents=INTENTS)

        mongo_uri = os.environ["MONGO_URI3"]
        db_name = os.getenv("MONGO_DB_NAME3", "rename_bot")
        self._mongo_client = AsyncIOMotorClient(mongo_uri, tlsCAFile=certifi.where())
        self.rename_logs = self._mongo_client[db_name]["rename_logs"]

        # The single guild this bot is allowed to operate in - mirrors the
        # lockdown pattern used by QueueBot in main.py.
        self.guild_id = guild_id

    async def setup_hook(self):
        await self.load_extension("cogs.rename")

        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s", len(synced), self.guild_id)
        except discord.Forbidden:
            log.error(
                "Could not sync commands to guild %s: missing access. "
                "Make sure this bot was invited with the 'applications.commands' "
                "scope and is actually a member of that server.",
                self.guild_id,
            )

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)
        for guild in list(self.guilds):
            if guild.id != self.guild_id:
                log.warning("Leaving unauthorized guild %s (%s)", guild.id, guild.name)
                await guild.leave()

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id != self.guild_id:
            log.warning("Declining unauthorized guild %s (%s)", guild.id, guild.name)
            await guild.leave()
