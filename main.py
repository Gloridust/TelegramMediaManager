"""Entry point. Launches the web panel (which owns the download engine and,
optionally, the Telegram bot).

    python main.py

Configuration is done entirely through the web panel on first run — no .env
required. Environment variables only wire up infrastructure (data dir, port);
see .env.example.
"""

from app.main import run

if __name__ == "__main__":
    run()
