"""Application service container.

Holds the single instances of the store, event bus, Telegram manager, download
engine and proxy manager, and owns their startup/shutdown ordering. Both the web
API and the Telegram bot receive the same ``Services`` object, which is what
keeps their state consistent.
"""

from app.config import DEFAULTS, Keys, Paths
from app.core.downloader import DownloadEngine
from app.core.events import EventBus
from app.core.proxy import ProxyManager
from app.core.store import Store
from app.core.telegram import TelegramManager


class Services:
    def __init__(self):
        Paths.ensure()
        self.store = Store(Paths.DB_PATH)
        self.bus = EventBus()
        self.tg = TelegramManager(self.store)
        self.engine = DownloadEngine(self.store, self.tg, self.bus)
        self.proxy = ProxyManager(self.store)
        self.bot = None

    async def startup(self):
        for k, v in DEFAULTS.items():
            if await self.store.get_setting(k) is None:
                await self.store.set_setting(k, v)

        # The queue-based worker pool is always running; it is harmless before
        # login (jobs simply wait) and ready the moment a client connects.
        await self.engine.start()

        if await self.tg.credentials_ready():
            try:
                await self.tg.build_user_client()
                if await self.tg.is_authorized():
                    await self.engine.resume()
            except Exception as e:
                print(f"User client init failed: {e}")

        await self.maybe_start_bot()

    async def maybe_start_bot(self):
        """Start the Telegram bot controller if enabled and configured."""
        if self.bot:
            return
        enabled = (await self.store.get_setting(Keys.BOT_ENABLED)) == "1"
        token = await self.store.get_setting(Keys.BOT_TOKEN)
        if enabled and token and await self.tg.credentials_ready():
            from app.bot.controller import BotController
            self.bot = BotController(self)
            try:
                await self.bot.start()
            except Exception as e:
                print(f"Bot start failed: {e}")
                self.bot = None

    async def stop_bot(self):
        if self.bot:
            await self.bot.stop()
            self.bot = None

    async def reconnect_user(self):
        """Rebuild the user client — used after credential or proxy changes."""
        if await self.tg.credentials_ready():
            await self.tg.build_user_client()

    async def shutdown(self):
        await self.engine.stop()
        if self.bot:
            await self.bot.stop()
        await self.tg.disconnect()
        self.store.close()
