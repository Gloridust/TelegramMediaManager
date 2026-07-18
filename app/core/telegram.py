"""Telethon client lifecycle: the user client, the optional bot client, proxy
wiring, and a web-driven QR + 2FA login state machine.

The rest of the app never constructs a ``TelegramClient`` directly — it goes
through ``TelegramManager`` so that proxy settings, session storage and the login
flow are handled in exactly one place.
"""

import asyncio
import io
import os
import time

import qrcode
from telethon import TelegramClient, errors

from app.config import Keys, MihomoConfig, Paths


def _proxy_tuple(mode, ptype, host, port):
    """Build the python-socks proxy tuple Telethon expects, or None."""
    if mode == "off" or not host or not port:
        return None
    if mode == "mihomo":
        return ("socks5", MihomoConfig.PROXY_HOST, MihomoConfig.PROXY_PORT)
    ptype = (ptype or "socks5").lower()
    if ptype not in ("socks5", "socks4", "http"):
        ptype = "socks5"
    return (ptype, host, int(port))


class TelegramManager:
    """Owns the Telegram clients and their auth flow."""

    def __init__(self, store):
        self.store = store
        self.user_client: TelegramClient | None = None
        self.bot_client: TelegramClient | None = None
        self._api_id = 0
        self._api_hash = ""

        # QR / 2FA login state machine (single flow at a time).
        self._qr = None
        self._qr_url = None
        self._login_task = None
        self._login_state = "idle"  # idle|waiting|need_password|success|error
        self._login_error = None
        self._password_future: asyncio.Future | None = None

    # ------------------------------------------------------------------ #
    # Credentials & proxy
    # ------------------------------------------------------------------ #
    async def credentials_ready(self):
        api_id = await self.store.get_setting(Keys.API_ID)
        api_hash = await self.store.get_setting(Keys.API_HASH)
        return bool(api_id and api_hash)

    async def _load_proxy(self):
        mode = await self.store.get_setting(Keys.PROXY_MODE, "off")
        ptype = await self.store.get_setting(Keys.PROXY_TYPE, "socks5")
        host = await self.store.get_setting(Keys.PROXY_HOST)
        port = await self.store.get_setting(Keys.PROXY_PORT)
        try:
            port = int(port) if port else None
        except ValueError:
            port = None
        return _proxy_tuple(mode, ptype, host, port)

    def _session_path(self, name):
        return os.path.join(Paths.SESSIONS_DIR, name)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def build_user_client(self):
        """(Re)create and connect the user client with current creds + proxy."""
        api_id = await self.store.get_setting(Keys.API_ID)
        api_hash = await self.store.get_setting(Keys.API_HASH)
        if not (api_id and api_hash):
            raise RuntimeError("Telegram API credentials are not configured yet.")
        self._api_id, self._api_hash = int(api_id), api_hash

        if self.user_client:
            try:
                await self.user_client.disconnect()
            except Exception:
                pass

        proxy = await self._load_proxy()
        self.user_client = TelegramClient(
            self._session_path("user_session"), self._api_id, self._api_hash, proxy=proxy
        )
        await self.user_client.connect()
        return self.user_client

    async def build_bot_client(self):
        """(Re)create and start the bot client, if a token is configured."""
        token = await self.store.get_setting(Keys.BOT_TOKEN)
        if not token:
            return None
        proxy = await self._load_proxy()
        self.bot_client = TelegramClient(
            self._session_path("bot_session"), self._api_id, self._api_hash, proxy=proxy
        )
        await self.bot_client.start(bot_token=token)
        self.bot_client.parse_mode = None  # filenames with _ / * break markdown edits
        return self.bot_client

    async def disconnect(self):
        for client in (self.user_client, self.bot_client):
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def is_authorized(self):
        if not self.user_client:
            return False
        try:
            return await self.user_client.is_user_authorized()
        except Exception:
            return False

    async def me(self):
        if not await self.is_authorized():
            return None
        try:
            u = await self.user_client.get_me()
            return {
                "id": u.id,
                "name": (u.first_name or "") + (f" {u.last_name}" if u.last_name else ""),
                "username": u.username,
                "phone": u.phone,
            }
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # QR + 2FA login (driven by the web panel via polling)
    # ------------------------------------------------------------------ #
    async def start_login(self):
        """Begin (or restart) a QR login. Returns the current status dict."""
        if self._login_state == "waiting" and self._qr_url:
            return self.login_status()
        if not self.user_client:
            await self.build_user_client()
        if await self.is_authorized():
            self._login_state = "success"
            return self.login_status()

        self._login_error = None
        self._login_state = "waiting"
        self._qr = await self.user_client.qr_login()
        self._qr_url = self._qr.url
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        self._login_task = asyncio.create_task(self._login_loop())
        return self.login_status()

    async def _login_loop(self):
        """Wait for the QR to be scanned, refreshing it as it expires."""
        try:
            for _ in range(20):  # ~10 minutes worth of 30s windows
                try:
                    await self._qr.wait(timeout=30)
                    self._login_state = "success"
                    self._qr_url = None
                    return
                except asyncio.TimeoutError:
                    try:
                        await self._qr.recreate()
                    except Exception:
                        self._qr = await self.user_client.qr_login()
                    self._qr_url = self._qr.url
                    continue
                except errors.SessionPasswordNeededError:
                    self._login_state = "need_password"
                    self._qr_url = None
                    self._password_future = asyncio.get_event_loop().create_future()
                    try:
                        password = await asyncio.wait_for(self._password_future, timeout=300)
                    except asyncio.TimeoutError:
                        self._login_state = "error"
                        self._login_error = "两步验证密码输入超时"
                        return
                    try:
                        await self.user_client.sign_in(password=password)
                        self._login_state = "success"
                    except Exception as e:
                        self._login_state = "error"
                        self._login_error = f"两步验证失败：{e}"
                    return
            self._login_state = "error"
            self._login_error = "二维码已过期，请重试"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._login_state = "error"
            self._login_error = str(e)

    async def submit_password(self, password):
        if self._login_state != "need_password" or not self._password_future:
            return False
        if not self._password_future.done():
            self._password_future.set_result(password)
        return True

    def login_status(self):
        return {
            "state": self._login_state,
            "url": self._qr_url,
            "error": self._login_error,
        }

    def qr_png(self):
        """Render the current QR login URL to PNG bytes (for <img> in the panel)."""
        if not self._qr_url:
            return None
        img = qrcode.make(self._qr_url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def logout(self):
        """Log the user client out and forget the session."""
        if self.user_client:
            try:
                await self.user_client.log_out()
            except Exception:
                pass
        self._login_state = "idle"
        self._qr_url = None
