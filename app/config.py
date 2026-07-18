"""Runtime configuration.

Two tiers, deliberately separated:

* **Infrastructure** (data dir, bind address, mihomo endpoint) comes from
  environment variables set by the container/compose. These wire the process to
  its surroundings and rarely change.
* **User configuration** (Telegram API credentials, proxy choice, download
  concurrency, …) lives in the SQLite store and is edited entirely through the
  web panel. There is intentionally **no required .env** for the user — the
  first-run setup wizard writes everything into the data volume.
"""

import os

APP_NAME = "TelegramMediaManager"


def _env_bool(key, default=False):
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Paths:
    """Filesystem layout inside the data volume."""

    DATA_DIR = os.path.abspath(os.getenv("TMM_DATA_DIR", "./data"))
    DB_PATH = os.path.join(DATA_DIR, "state.db")
    SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
    # Default download target. On NAS this is bind-mounted to a share.
    DOWNLOADS_DIR = os.path.abspath(os.getenv("TMM_DOWNLOADS_DIR", os.path.join(DATA_DIR, "downloads")))
    # mihomo writes its generated config here; shared with the sidecar via a volume.
    MIHOMO_DIR = os.path.abspath(os.getenv("TMM_MIHOMO_DIR", os.path.join(DATA_DIR, "mihomo")))

    @classmethod
    def ensure(cls):
        for d in (cls.DATA_DIR, cls.SESSIONS_DIR, cls.DOWNLOADS_DIR, cls.MIHOMO_DIR):
            os.makedirs(d, exist_ok=True)


class WebConfig:
    """How the HTTP server binds. Defaults to all interfaces *inside the
    container*; exposure is controlled by Docker port mapping, and the panel is
    meant to sit behind the LAN / a reverse proxy — never raw on the internet."""

    HOST = os.getenv("TMM_HOST", "0.0.0.0")
    PORT = int(os.getenv("TMM_PORT", "36091"))
    SESSION_TTL = int(os.getenv("TMM_SESSION_TTL", str(7 * 24 * 3600)))  # 7 days
    # Set true when served over HTTPS so the session cookie gets the Secure flag.
    SECURE_COOKIE = _env_bool("TMM_SECURE_COOKIE", False)
    # Brute-force protection on the login endpoint.
    LOGIN_MAX_ATTEMPTS = int(os.getenv("TMM_LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_WINDOW = int(os.getenv("TMM_LOGIN_WINDOW", "300"))  # seconds


class MihomoConfig:
    """Connection to the mihomo (Clash Meta) sidecar."""

    ENABLED = _env_bool("TMM_MIHOMO_ENABLED", True)
    # Reachable as the compose service name from inside the app container.
    CONTROLLER = os.getenv("TMM_MIHOMO_CONTROLLER", "http://mihomo:9090")
    SECRET = os.getenv("TMM_MIHOMO_SECRET", "")
    # The mixed (socks+http) port mihomo listens on; Telethon dials this.
    PROXY_HOST = os.getenv("TMM_MIHOMO_HOST", "mihomo")
    PROXY_PORT = int(os.getenv("TMM_MIHOMO_PORT", "7890"))


# Store keys for user configuration (edited via the panel). Centralised so the
# API layer and services never scatter magic strings.
class Keys:
    API_ID = "tg_api_id"
    API_HASH = "tg_api_hash"
    BOT_TOKEN = "tg_bot_token"
    ADMIN_ID = "tg_admin_id"
    BOT_ENABLED = "bot_enabled"

    ROOT_PATH = "root_path"
    CURRENT_DIR = "current_dir"
    MAX_CONCURRENT = "max_concurrent"
    FOLDERS_RELATIVE = "folders_relative"  # migration flag
    UI_LANG = "ui_lang"  # 'zh' | 'en' — language for server-generated messages

    PROXY_MODE = "proxy_mode"        # 'off' | 'mihomo' | 'external'
    PROXY_TYPE = "proxy_type"        # 'socks5' | 'http'
    PROXY_HOST = "proxy_host"
    PROXY_PORT = "proxy_port"
    SUBSCRIPTION_URL = "subscription_url"
    PROXY_SELECTED = "proxy_selected"  # selected node name in mihomo


DEFAULTS = {
    Keys.MAX_CONCURRENT: "2",
    Keys.PROXY_MODE: "off",
    Keys.PROXY_TYPE: "socks5",
    Keys.BOT_ENABLED: "0",
}
