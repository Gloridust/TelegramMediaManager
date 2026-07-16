import asyncio

from manager import TelegramMediaManager


def main():
    manager = TelegramMediaManager()
    try:
        asyncio.run(manager.start())
    except KeyboardInterrupt:
        print("\nShutting down (Ctrl+C).")


if __name__ == '__main__':
    main()
