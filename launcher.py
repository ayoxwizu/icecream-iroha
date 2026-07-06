"""
launcher.py

Runs BOTH bots (the embed/autoresponder/sticky bot and the ice-cream queue
bot) as two concurrent tasks inside a single Python process, so they can
share one Railway service.

Required environment variables:
    TOKEN1      - Discord bot token for the embed/autoresponder/sticky bot
    TOKEN2      - Discord bot token for the queue bot
    GUILD_ID2   - the single guild the queue bot is allowed to run in
                  (required - see main.py/QueueBot, it leaves any other guild)
    MONGO_URI1  - MongoDB connection string used by bot 1 (db/mongo.py)
    MONGO_URI2  - MongoDB connection string used by bot 2 (database.py)

Optional:
    GUILD_ID1        - sync bot 1's slash commands to a single guild instantly
                        during dev, instead of waiting for a global sync
    MONGO_DB_NAME    - overrides the queue bot's database name (default "queue_bot")

Run with:  python launcher.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("launcher")

# Importing these only defines the classes/module-level constants - neither
# module's own `if __name__ == "__main__"` block runs on import, so it's
# safe to import both here without them fighting over which one starts.
from bot import EmbedBot  # noqa: E402  (embed / autoresponder / sticky bot)
from main import QueueBot  # noqa: E402  (ice-cream queue bot)


async def run_forever() -> None:
    token1 = os.environ["TOKEN1"]
    token2 = os.environ["TOKEN2"]
    guild_id2 = int(os.environ["GUILD_ID2"])

    bot1 = EmbedBot()
    bot2 = QueueBot(guild_id=guild_id2)

    async with bot1, bot2:
        # asyncio.gather runs both bots' event loops concurrently. If either
        # bot crashes, gather raises and the whole process exits - which is
        # what we want on Railway, since a crashed process gets restarted
        # and brings both bots back up together.
        await asyncio.gather(
            bot1.start(token1),
            bot2.start(token2),
        )


if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        log.info("Shutting down.")
