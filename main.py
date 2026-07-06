"""
main.py
Entrypoint: loads env vars, connects to MongoDB, loads cogs, syncs slash commands.

Run with:  python main.py
"""

import os
import asyncio
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import build_database

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

INTENTS = discord.Intents.default()
# message_content isn't needed since everything here is slash commands / components,
# but enable it if you plan to add prefix commands later.


class QueueBot(commands.Bot):
    def __init__(self, guild_id: int):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.db = build_database()
        # The single guild this bot is allowed to operate in. Commands are
        # only synced here, and on_guild_join / the ready-time sweep below
        # make sure the bot never lingers in - or accumulates config/view
        # state for - any other server.
        self.guild_id = guild_id

    async def setup_hook(self):
        await self.load_extension("cogs.config_cog")
        await self.load_extension("cogs.queue_cog")

        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d commands to guild %s", len(synced), self.guild_id)

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)
        # In case the bot was invited to (or already sitting in) any server
        # other than the configured one, leave it - keeps this from ever
        # serving, or holding queue-view memory for, an unintended guild.
        for guild in list(self.guilds):
            if guild.id != self.guild_id:
                log.warning("Leaving unauthorized guild %s (%s)", guild.id, guild.name)
                await guild.leave()

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id != self.guild_id:
            log.warning("Declining unauthorized guild %s (%s)", guild.id, guild.name)
            await guild.leave()


async def main():
    guild_id = int(os.environ["GUILD_ID2"])  # the one server this bot is allowed to run in
    bot = QueueBot(guild_id)
    token = os.environ["TOKEN2"]
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
