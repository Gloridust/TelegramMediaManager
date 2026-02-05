import asyncio
import os
import qrcode
from telethon import TelegramClient, events, errors
from telethon.tl.types import Message
from config import Config

class TelegramMediaManager:
    def __init__(self):
        self.bot = TelegramClient('bot_session', Config.API_ID, Config.API_HASH)
        self.user_client = TelegramClient(Config.SESSION_NAME, Config.API_ID, Config.API_HASH)
        self.admin_id = Config.ADMIN_ID
        self.download_path = Config.SAVE_PATH
        
        # State for login flow
        self.login_event = asyncio.Event()
        self.password_future = None

    async def start(self):
        print("Starting Bot...")
        await self.bot.start(bot_token=Config.BOT_TOKEN)
        
        print("Registering Bot Handlers...")
        self.bot.add_event_handler(self.bot_message_handler, events.NewMessage(chats=self.admin_id))
        
        print("Checking User Client Session...")
        # We don't await user_client.start() here because it triggers interactive login if not authorized.
        # We check connection status manually.
        await self.user_client.connect()
        
        if not await self.user_client.is_user_authorized():
            print("User Client not authorized. Waiting for /login command...")
            await self.bot.send_message(self.admin_id, "User client not logged in. Send /login to start QR login.")
        else:
            print("User Client already authorized.")
            await self.bot.send_message(self.admin_id, "System started. User client is ready.")

        # Keep running
        await asyncio.gather(
            self.bot.run_until_disconnected(),
            self.user_client.run_until_disconnected() if await self.user_client.is_user_authorized() else asyncio.sleep(0) # Keep loop alive if needed, or just rely on bot
        )

    async def bot_message_handler(self, event):
        text = event.message.text
        
        if text == '/login':
            if await self.user_client.is_user_authorized():
                await event.reply("User client is already logged in.")
                return
            await event.reply("Starting QR Login flow...")
            asyncio.create_task(self.perform_qr_login(event.chat_id))
            return

        if text.startswith('/setpath'):
            path = text.split(' ', 1)[1] if len(text.split()) > 1 else "downloads"
            self.download_path = path
            await event.reply(f"Download path set to: {self.download_path}")
            return
        
        if 't.me/' in text:
            await self.handle_link(text, event)
            return

        # If it's a password for 2FA
        if self.password_future and not self.password_future.done():
            self.password_future.set_result(text)
            await event.delete() # Security: delete password message
            return

        # Handle forwarded media (simple download via bot if possible)
        if event.message.media:
            await self.download_media(event.message, event)

    async def perform_qr_login(self, chat_id):
        try:
            qr_login = await self.user_client.qr_login()
            # r = await qr_login.next_response() # This method doesn't exist in recent Telethon versions
            
            # Generate QR Image
            # Use qr_login.url directly
            img = qrcode.make(qr_login.url)
            img_path = "qr_login.png"
            img.save(img_path)
            
            qr_msg = await self.bot.send_file(chat_id, img_path, caption="Scan this QR code to login. You have 30 seconds.")
            
            try:
                # Wait for login
                user = await qr_login.wait(timeout=30)
                await self.bot.send_message(chat_id, f"Login successful! Welcome {user.username}")
                await self.user_client.start() # Initialize properly
            except errors.SessionPasswordNeededError:
                await self.bot.send_message(chat_id, "Two-step verification enabled. Please send your password.")
                self.password_future = asyncio.Future()
                password = await self.password_future
                await self.user_client.sign_in(password=password)
                await self.bot.send_message(chat_id, "Login complete with 2FA.")
            except asyncio.TimeoutError:
                await self.bot.send_message(chat_id, "QR Code expired. Try /login again.")
            finally:
                if os.path.exists(img_path):
                    os.remove(img_path)
                    
        except Exception as e:
            import traceback
            traceback.print_exc() # Print full error to console
            await self.bot.send_message(chat_id, f"Login failed: {str(e)}")

    async def handle_link(self, link, event):
        if not await self.user_client.is_user_authorized():
            await event.reply("User client not logged in. Cannot process links.")
            return

        await event.reply(f"Processing link: {link}")
        try:
            # Clean link
            link = link.split('?')[0]
            parts = link.rstrip('/').split('/')
            
            # Formats: 
            # https://t.me/c/1234567890/123 (Private group)
            # https://t.me/username/123 (Public channel/group)
            
            if len(parts) < 2:
                raise ValueError("Invalid link format")

            msg_id = int(parts[-1])
            entity = parts[-2]
            
            # If it's a private group link like /c/1234567890/
            if entity.isdigit():
                 # For /c/ links, the ID is usually the channel ID without -100
                 # Telethon's get_messages usually works with the resolved entity or PeerChannel
                 # We try constructing the peer
                 try:
                     chat_id = int(f"-100{entity}")
                     entity = chat_id
                 except:
                     pass
            
            message = await self.user_client.get_messages(entity, ids=msg_id)
            if message and message.media:
                await self.download_media(message, event, use_user_client=True)
            else:
                await event.reply("Message not found or has no media.")
                
        except Exception as e:
            await event.reply(f"Error processing link: {str(e)}")

    async def download_media(self, message, event, use_user_client=False):
        status_msg = await event.reply("Downloading...")
        try:
            path = os.path.join(self.download_path, "")
            os.makedirs(path, exist_ok=True)
            
            client = self.user_client if use_user_client else self.bot
            
            # Download
            out = await client.download_media(message, file=path)
            
            await status_msg.edit(f"Saved to: {out}")
        except Exception as e:
            await status_msg.edit(f"Download failed: {str(e)}")

