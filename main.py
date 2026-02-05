import asyncio
from manager import TelegramMediaManager

if __name__ == '__main__':
    manager = TelegramMediaManager()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(manager.start())
