import os

from dotenv import load_dotenv

load_dotenv()

import discord
from discord.ext import commands

from db import mongo

TOKEN = os.getenv("TOKEN1")
GUILD_ID = os.getenv("GUILD_ID1")  # optional, for instant per-guild sync during dev

intents = discord.Intents.default()
intents.members = True  # needed to resolve mentions/avatars reliably
intents.message_content = True  # needed to read message text for autoresponders


class EmbedBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await mongo.ensure_indexes()
        await self.load_extension("cogs.embed")
        await self.load_extension("cogs.autoresponder")
        await self.load_extension("cogs.sticky")

        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s) globally")

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")


bot = EmbedBot()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("TOKEN1 is not set. Copy .env.example to .env and fill it in.")
    bot.run(TOKEN)
