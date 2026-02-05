import asyncio
import os
import qrcode
from telethon import TelegramClient, events, errors, utils
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
        
        # State for commands
        self.waiting_for_channel_link = False

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

        if text == '/download_channel' or text == '/download_chanel':
            if not await self.user_client.is_user_authorized():
                await event.reply("User client not logged in. Cannot download channels.")
                return
            self.waiting_for_channel_link = True
            await event.reply("Please send a link to any message in the channel you want to download (e.g., https://t.me/channel/123).")
            return

        if text.startswith('/setpath'):
            path = text.split(' ', 1)[1] if len(text.split()) > 1 else "downloads"
            self.download_path = path
            await event.reply(f"Download path set to: {self.download_path}")
            return
        
        if 't.me/' in text:
            if self.waiting_for_channel_link:
                self.waiting_for_channel_link = False
                await self.start_channel_download(text, event)
            else:
                await self.handle_link(text, event)
            return
        
        # If waiting for link but got something else (and not a command handled above)
        if self.waiting_for_channel_link and not text.startswith('/'):
            self.waiting_for_channel_link = False
            await event.reply("Invalid link or operation cancelled. Please send /download_channel again.")
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

    async def start_channel_download(self, link, event):
        await event.reply(f"Analyzing channel from link: {link}...")
        try:
            # Clean link
            link = link.split('?')[0]
            parts = link.rstrip('/').split('/')
            
            if len(parts) < 2:
                raise ValueError("Invalid link format")

            entity = parts[-2]
            
            # Resolve entity
            if entity.isdigit():
                 try:
                     chat_id = int(f"-100{entity}")
                     entity = chat_id
                 except:
                     pass
            
            # Get Chat object to get title
            chat = await self.user_client.get_entity(entity)
            chat_title = getattr(chat, 'title', str(entity)).replace('/', '_')
            
            # Create specific folder
            channel_path = os.path.join(self.download_path, chat_title)
            os.makedirs(channel_path, exist_ok=True)
            
            status_msg = await event.reply(f"Started downloading channel: {chat_title}\nSaving to: {channel_path}\nThis may take a while...")
            
            count = 0
            # Iterate over all messages
            # Note: Albums (grouped media) are returned as separate messages with the same grouped_id.
            # We simply iterate and download all of them.
            async for message in self.user_client.iter_messages(chat):
                if message.media:
                    try:
                        # Optional: Check if we already have this file to skip
                        # Telethon's download_media handles unique filenames usually, but doesn't skip if exists by default unless we check.
                        # For simplicity, we just download.
                        
                        await self.user_client.download_media(message, file=channel_path)
                        count += 1
                        if count % 10 == 0:
                            await status_msg.edit(f"Downloading {chat_title}...\nDownloaded: {count} files so far.")
                    except Exception as e:
                        print(f"Error downloading message {message.id}: {e}")
            
            await status_msg.edit(f"✅ Download complete for {chat_title}!\nTotal files: {count}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            await event.reply(f"Error starting channel download: {str(e)}")

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
            
            # Check for grouped media (albums)
            if message and message.grouped_id:
                # Get all messages in the group
                # We can't easily get "all messages with this grouped_id" directly without searching around
                # But typically they are adjacent. Telethon doesn't have a direct "get_album" method.
                # A robust way is to fetch surrounding messages.
                # However, a simpler heuristic: fetch messages around this ID.
                # Or just iterate messages in the chat with a limit and filter by grouped_id? No, that's inefficient for old messages.
                
                # Best approach for single link:
                # 1. We have one message.
                # 2. Fetch a few messages before and after (e.g., 10)
                # 3. Filter by the same grouped_id.
                
                surrounding = await self.user_client.get_messages(entity, min_id=msg_id-10, max_id=msg_id+10)
                # Note: get_messages with min/max might return empty if not found.
                # Actually, simply getting a range of IDs is safer.
                ids_to_check = list(range(msg_id - 9, msg_id + 10))
                candidates = await self.user_client.get_messages(entity, ids=ids_to_check)
                
                album = [m for m in candidates if m and m.grouped_id == message.grouped_id]
                
                # Ensure the original message is included (it should be in candidates)
                if not any(m.id == message.id for m in album):
                     album.append(message)
                
                # Deduplicate by ID
                album_unique = {m.id: m for m in album}.values()
                
                if album_unique:
                    await event.reply(f"Found album with {len(album_unique)} items. Downloading all...")
                    for m in album_unique:
                        if m.media:
                            await self.download_media(m, event, use_user_client=True)
                    return

            if message and message.media:
                await self.download_media(message, event, use_user_client=True)
            else:
                await event.reply("Message not found or has no media.")
                
        except Exception as e:
            await event.reply(f"Error processing link: {str(e)}")

    async def _safe_download(self, client, message, folder):
        """
        Download media with custom filename generation and cleanup on failure.
        """
        try:
            # Generate filename
            filename = message.file.name
            if not filename:
                ext = utils.get_extension(message.media) or ''
                filename = f"media_{message.id}{ext}"
            
            # Ensure unique filename
            filepath = os.path.join(folder, filename)
            base, ext = os.path.splitext(filename)
            i = 1
            while os.path.exists(filepath):
                filepath = os.path.join(folder, f"{base}_{i}{ext}")
                i += 1
                
            # Download
            await client.download_media(message, file=filepath)
            return filepath
            
        except Exception as e:
            # Cleanup partial file
            if 'filepath' in locals() and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
            raise e

    async def download_media(self, message, event, use_user_client=False):
        status_msg = await event.reply("Downloading...")
        try:
            path = os.path.join(self.download_path, "")
            os.makedirs(path, exist_ok=True)
            
            client = self.user_client if use_user_client else self.bot
            
            # Download using safe method
            out = await self._safe_download(client, message, path)
            
            await status_msg.edit(f"Saved to: {out}")
        except Exception as e:
            await status_msg.edit(f"Download failed: {str(e)}")

