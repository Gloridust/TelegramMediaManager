import asyncio
import mimetypes
import os
import re
import time
import traceback

import qrcode
from telethon import TelegramClient, events, errors, utils, Button

from config import Config


class _Batch:
    """A group of downloads that share one status message (single file or album)."""
    __slots__ = ('status', 'total', 'folder', 'done', 'skipped', 'failed', 'last_edit', 'lock')

    def __init__(self, status, total, folder):
        self.status = status
        self.total = total
        self.folder = folder
        self.done = 0
        self.skipped = 0
        self.failed = 0
        self.last_edit = 0.0
        self.lock = asyncio.Lock()

    @property
    def finished(self):
        return self.done + self.skipped + self.failed


class TelegramMediaManager:
    def __init__(self):
        self.bot = TelegramClient(Config.BOT_SESSION_NAME, Config.API_ID, Config.API_HASH)
        self.user_client = TelegramClient(Config.SESSION_NAME, Config.API_ID, Config.API_HASH)
        self.admin_id = Config.ADMIN_ID

        # Working directories
        self.root_path = os.path.abspath(Config.SAVE_PATH)   # 工作根目录
        self.current_dir = self.root_path                    # 当前下载目录
        self.browse_dir = self.root_path                     # 浏览器正在浏览的目录
        self._browse_entries = []                            # 上次渲染的子文件夹列表
        self._browser_ref = None                             # (chat_id, message_id) of the browser msg

        # Login flow state
        self.password_future = None
        self._login_in_progress = False

        # Pending text-input state: None | 'channel_link' | 'mkdir'
        self.input_state = None

        # Download infrastructure
        self.download_queue = asyncio.Queue()
        self.worker_tasks = []
        self.channel_task = None
        self._fs_lock = asyncio.Lock()

    # ================================================================== #
    # Lifecycle
    # ================================================================== #
    async def start(self):
        Config.validate()
        os.makedirs(self.root_path, exist_ok=True)

        print("Starting bot...")
        await self.bot.start(bot_token=Config.BOT_TOKEN)
        # 关闭 Markdown 解析：文件名/路径含 '_'、'*' 会导致编辑消息报错。
        self.bot.parse_mode = None

        for i in range(Config.MAX_CONCURRENT_DOWNLOADS):
            self.worker_tasks.append(asyncio.create_task(self.download_worker(i)))
            print(f"Started download worker {i + 1}")

        print("Registering handlers...")
        self.bot.add_event_handler(self.bot_message_handler, events.NewMessage(chats=self.admin_id))
        self.bot.add_event_handler(self.bot_callback_handler, events.CallbackQuery(chats=self.admin_id))

        print("Connecting user client...")
        await self.user_client.connect()

        if await self.user_client.is_user_authorized():
            print("User client already authorized.")
            await self._notify_admin("✅ 系统已启动，用户端已就绪。发送 /start 打开菜单。")
        else:
            print("User client not authorized.")
            await self._notify_admin("⚠️ 系统已启动，但用户端尚未登录。发送 /login 开始扫码登录。")

        try:
            await asyncio.gather(
                self.bot.run_until_disconnected(),
                self.user_client.run_until_disconnected(),
            )
        finally:
            await self._shutdown()

    async def _shutdown(self):
        for task in self.worker_tasks:
            task.cancel()
        if self.channel_task and not self.channel_task.done():
            self.channel_task.cancel()
        for client in (self.bot, self.user_client):
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _notify_admin(self, text):
        try:
            await self.bot.send_message(self.admin_id, text)
        except Exception as e:
            print(f"Could not notify admin: {e}")

    # ================================================================== #
    # Download workers
    # ================================================================== #
    async def download_worker(self, worker_id):
        print(f"Worker {worker_id} ready")
        while True:
            message, use_user_client, folder, batch = await self.download_queue.get()
            single = batch.total == 1
            try:
                client = self.user_client if use_user_client else self.bot
                folder = folder or self.current_dir
                disp = self._display_name(message)

                callback = None
                if single:
                    await self._safe_edit(batch.status, f"⬇️ 开始下载：{disp}")
                    callback = self._make_progress_cb(batch.status, disp, folder)

                out, skipped = await self._download_with_floodwait(client, message, folder, callback)

                async with batch.lock:
                    if skipped:
                        batch.skipped += 1
                    else:
                        batch.done += 1
                    await self._update_batch(batch, single=single,
                                             name=os.path.basename(out), skipped=skipped)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Worker {worker_id} error: {e}")
                async with batch.lock:
                    batch.failed += 1
                    await self._update_batch(batch, single=single, error=str(e))
            finally:
                self.download_queue.task_done()

    async def _update_batch(self, batch, single, name=None, skipped=False, error=None):
        if single:
            rel = self._rel(batch.folder)
            if error:
                await self._safe_edit(batch.status, f"❌ 下载失败：{error}")
            elif skipped:
                await self._safe_edit(batch.status, f"⏭ 已存在，跳过：{name}\n📁 {rel}")
            else:
                await self._safe_edit(batch.status, f"✅ 已保存：{name}\n📁 {rel}")
            return

        now = time.time()
        done_all = batch.finished >= batch.total
        if now - batch.last_edit < 2 and not done_all:
            return
        batch.last_edit = now
        rel = self._rel(batch.folder)
        if done_all:
            await self._safe_edit(
                batch.status,
                f"✅ 相册下载完成\n📁 {rel}\n"
                f"成功 {batch.done} · 跳过 {batch.skipped} · 失败 {batch.failed}（共 {batch.total}）"
            )
        else:
            await self._safe_edit(
                batch.status,
                f"📦 下载中… {batch.finished}/{batch.total}\n"
                f"✅ {batch.done} · ⏭ {batch.skipped} · ❌ {batch.failed}\n📁 {rel}"
            )

    def _make_progress_cb(self, status_msg, disp, folder):
        st = {'last': 0.0, 'last_t': time.time(), 'last_b': 0}
        rel = self._rel(folder)

        async def cb(current, total):
            if not total:
                return
            now = time.time()
            if now - st['last'] < 2.5 and current != total:
                return
            dt = now - st['last_t']
            speed = (current - st['last_b']) / dt if dt > 0 else 0
            st['last'] = now
            if current != total:
                st['last_t'] = now
                st['last_b'] = current
            pct = current * 100 / total
            line = f"⬇️ {disp}\n{self._bar(pct)} {pct:.0f}%\n{self._human(current)} / {self._human(total)}"
            if speed > 0:
                line += f" · {self._human(speed)}/s"
                if current != total:
                    line += f" · 剩余 {self._dur((total - current) / speed)}"
            line += f"\n📁 {rel}"
            await self._safe_edit(status_msg, line)

        return cb

    async def _download_with_floodwait(self, client, message, folder, callback=None):
        try:
            return await self._safe_download(client, message, folder, callback)
        except errors.FloodWaitError as e:
            wait = e.seconds + 1
            print(f"FloodWait: sleeping {wait}s")
            await asyncio.sleep(wait)
            return await self._safe_download(client, message, folder, callback)

    # ================================================================== #
    # Menus & callbacks
    # ================================================================== #
    def _menu_content(self):
        rel = self._short(self._rel(self.current_dir))
        text = (
            "🎬 Telegram 媒体管理器\n\n"
            f"📥 当前下载目录：{rel}\n\n"
            "• 直接发送消息链接（t.me/…）即可下载受限媒体\n"
            "• 转发媒体到这里可直接下载\n"
            "• 用下方按钮管理文件夹与任务"
        )
        buttons = [
            [Button.inline(f"🗂 文件夹管理（{self._short(self._rel(self.current_dir), 16)}）", b"dirs")],
            [Button.inline("📷 登录用户端", b"login"), Button.inline("📊 状态", b"status")],
            [Button.inline("📂 下载整个频道", b"dl_channel"), Button.inline("🛑 取消任务", b"cancel")],
            [Button.inline("❓ 帮助", b"help")],
        ]
        return text, buttons

    async def show_menu(self, event):
        text, buttons = self._menu_content()
        await event.reply(text, buttons=buttons)

    async def _draw_menu(self, chat_id, message_id):
        text, buttons = self._menu_content()
        try:
            await self.bot.edit_message(chat_id, message_id, text, buttons=buttons)
        except Exception:
            await self.bot.send_message(chat_id, text, buttons=buttons)

    def _browser_content(self):
        try:
            names = sorted(
                d for d in os.listdir(self.browse_dir)
                if os.path.isdir(os.path.join(self.browse_dir, d))
            )
        except Exception:
            names = []
        self._browse_entries = names[:24]

        buttons, row = [], []
        for i, name in enumerate(self._browse_entries):
            row.append(Button.inline(f"📁 {self._short(name, 18)}", f"cd:{i}".encode()))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        nav = []
        if not self._is_root(self.browse_dir):
            nav.append(Button.inline("⬆️ 上级", b"up"))
        nav.append(Button.inline("🏠 根目录", b"home"))
        buttons.append(nav)
        buttons.append([Button.inline("➕ 新建文件夹", b"mkdir"),
                        Button.inline("✅ 设为下载目录", b"usehere")])
        buttons.append([Button.inline("🔄 刷新", b"refresh"),
                        Button.inline("◀️ 返回菜单", b"menu")])

        marker = "  ⬅️ 当前下载目录" if os.path.realpath(self.browse_dir) == os.path.realpath(self.current_dir) else ""
        summary = "（空文件夹）" if not names else f"共 {len(names)} 个子文件夹"
        if len(names) > 24:
            summary += "，仅显示前 24 个"
        text = (
            "🗂 文件夹管理\n\n"
            f"📍 正在浏览：{self._rel(self.browse_dir)}{marker}\n"
            f"📥 当前下载目录：{self._rel(self.current_dir)}\n\n"
            f"{summary}\n点击 📁 进入子文件夹，或「设为下载目录」。"
        )
        return text, buttons

    async def _draw_browser(self, chat_id, message_id=None):
        text, buttons = self._browser_content()
        if message_id is not None:
            try:
                await self.bot.edit_message(chat_id, message_id, text, buttons=buttons)
                self._browser_ref = (chat_id, message_id)
                return
            except errors.MessageNotModifiedError:
                self._browser_ref = (chat_id, message_id)
                return
            except Exception:
                pass
        msg = await self.bot.send_message(chat_id, text, buttons=buttons)
        self._browser_ref = (chat_id, msg.id)

    async def bot_callback_handler(self, event):
        data = event.data.decode('utf-8')
        toast = None
        try:
            if data == 'menu':
                await self._draw_menu(event.chat_id, event.message_id)
            elif data == 'dirs':
                self.browse_dir = self.current_dir
                await self._draw_browser(event.chat_id, event.message_id)
            elif data == 'refresh':
                await self._draw_browser(event.chat_id, event.message_id)
            elif data == 'home':
                self.browse_dir = self.root_path
                await self._draw_browser(event.chat_id, event.message_id)
            elif data == 'up':
                parent = os.path.dirname(self.browse_dir)
                if self._is_within_root(parent):
                    self.browse_dir = parent
                await self._draw_browser(event.chat_id, event.message_id)
            elif data.startswith('cd:'):
                idx = int(data[3:])
                if 0 <= idx < len(self._browse_entries):
                    target = os.path.join(self.browse_dir, self._browse_entries[idx])
                    if self._is_within_root(target) and os.path.isdir(target):
                        self.browse_dir = target
                await self._draw_browser(event.chat_id, event.message_id)
            elif data == 'usehere':
                self.current_dir = self.browse_dir
                toast = f"已设为下载目录：{self._rel(self.current_dir)}"
                await self._draw_browser(event.chat_id, event.message_id)
            elif data == 'mkdir':
                self.input_state = 'mkdir'
                self._browser_ref = (event.chat_id, event.message_id)
                await event.respond("✏️ 请发送新文件夹的名称（将创建在当前浏览目录下，并切换为下载目录）。")
            elif data == 'login':
                await self._begin_login(event.chat_id)
            elif data == 'dl_channel':
                if await self._require_user_client_cb(event):
                    self.input_state = 'channel_link'
                    await event.respond("📎 请发送频道内任意一条消息的链接（如 https://t.me/channel/123）。")
            elif data == 'status':
                await event.respond(await self._status_text())
            elif data == 'cancel':
                await self.cancel_all(event)
            elif data == 'help':
                await event.respond(self._help_text())
        except Exception as e:
            print(f"Callback error: {e}")
        finally:
            try:
                await event.answer(toast)
            except Exception:
                pass

    # ================================================================== #
    # Message handling
    # ================================================================== #
    async def bot_message_handler(self, event):
        text = (event.message.text or '').strip()
        has_media = event.message.media is not None
        is_command = text.startswith('/')

        # 1) 2FA 密码优先捕获
        if (self.password_future is not None and not self.password_future.done()
                and text and not is_command):
            self.password_future.set_result(text)
            try:
                await event.delete()
            except Exception:
                pass
            return

        # 2) 命令会取消任何等待中的文本输入
        if is_command:
            self.input_state = None

        # 3) 等待文本输入的状态
        if not is_command and self.input_state == 'mkdir' and text:
            self.input_state = None
            await self._handle_mkdir_input(event, text)
            return
        if not is_command and self.input_state == 'channel_link':
            self.input_state = None
            if 't.me/' in text and not has_media:
                await self.start_channel_download(text, event)
            else:
                await event.reply("已取消。请重新点击「下载整个频道」。")
            return

        # 4) 命令
        cmd = text.split()[0].lower() if text else ''
        if cmd == '/start':
            await self.show_menu(event)
            return
        if cmd == '/help':
            await event.reply(self._help_text())
            return
        if cmd == '/login':
            await self._begin_login(event.chat_id)
            return
        if cmd == '/folders':
            self.browse_dir = self.current_dir
            await self._draw_browser(event.chat_id)
            return
        if cmd in ('/download_channel', '/download_chanel'):
            if not await self._require_user_client(event):
                return
            self.input_state = 'channel_link'
            await event.reply("📎 请发送频道内任意一条消息的链接（如 https://t.me/channel/123）。")
            return
        if cmd == '/setroot' or cmd == '/setpath':
            await self._set_root(event, text)
            return
        if cmd == '/cancel':
            await self.cancel_all(event)
            return
        if cmd == '/status':
            await event.reply(await self._status_text())
            return

        # 5) 链接
        if 't.me/' in text and not has_media:
            await self.handle_link(text, event)
            return

        # 6) 转发/发送的媒体
        if has_media:
            await self.download_media(event.message, event)
            return

    async def _handle_mkdir_input(self, event, name):
        safe = self._safe_subdir_name(name)
        if not safe:
            await event.reply("❌ 无效的文件夹名（不能包含 / \\ 或仅由点组成）。")
            return
        path = os.path.join(self.browse_dir, safe)
        if not self._is_within_root(path):
            await event.reply("❌ 非法路径，已拒绝。")
            return
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            await event.reply(f"❌ 创建失败：{e}")
            return
        self.browse_dir = path
        self.current_dir = path
        await event.reply(f"✅ 已创建并切换下载目录：\n📁 {self._rel(path)}")
        if self._browser_ref:
            await self._draw_browser(*self._browser_ref)

    async def _set_root(self, event, text):
        parts = text.split(' ', 1)
        new_path = parts[1].strip() if len(parts) > 1 else ''
        if not new_path:
            await event.reply("用法：/setroot <路径>（设置工作根目录）")
            return
        try:
            new_path = os.path.abspath(new_path)
            os.makedirs(new_path, exist_ok=True)
            self.root_path = new_path
            self.current_dir = new_path
            self.browse_dir = new_path
            await event.reply(f"✅ 工作根目录已设为：\n📁 {new_path}")
        except Exception as e:
            await event.reply(f"❌ 无效路径：{e}")

    async def _require_user_client(self, event):
        if not await self.user_client.is_user_authorized():
            await event.reply("⚠️ 用户端尚未登录，请先 /login。")
            return False
        return True

    async def _require_user_client_cb(self, event):
        if not await self.user_client.is_user_authorized():
            await event.respond("⚠️ 用户端尚未登录，请先点「登录用户端」。")
            return False
        return True

    def _help_text(self):
        return (
            "📖 使用帮助\n\n"
            "命令：\n"
            "/start — 打开菜单\n"
            "/folders — 文件夹管理（新建/切换下载目录）\n"
            "/login — 扫码登录用户端\n"
            "/download_channel — 批量下载整个频道\n"
            "/setroot <路径> — 设置工作根目录\n"
            "/status — 查看状态\n"
            "/cancel — 取消频道下载并清空队列\n\n"
            "用法：\n"
            "• 发送 t.me 消息链接 → 下载受限媒体（含相册）\n"
            "• 转发媒体到这里 → 直接下载\n"
            "• 重复发送同一链接会自动跳过已下载的文件"
        )

    async def _status_text(self):
        is_auth = await self.user_client.is_user_authorized()
        auth = "✅ 已登录" if is_auth else "❌ 未登录"
        channel = "运行中" if (self.channel_task and not self.channel_task.done()) else "空闲"
        return (
            "📊 系统状态\n\n"
            f"用户端：{auth}\n"
            f"队列中待下载：{self.download_queue.qsize()}\n"
            f"频道下载任务：{channel}\n"
            f"当前下载目录：{self._rel(self.current_dir)}\n"
            f"工作根目录：{self.root_path}"
        )

    # ================================================================== #
    # Login (QR + 2FA)
    # ================================================================== #
    async def _begin_login(self, chat_id):
        if await self.user_client.is_user_authorized():
            await self.bot.send_message(chat_id, "用户端已登录，无需重复登录。")
            return
        if self._login_in_progress:
            await self.bot.send_message(chat_id, "登录正在进行中，请稍候。")
            return
        await self.bot.send_message(chat_id, "正在生成二维码…")
        asyncio.create_task(self.perform_qr_login(chat_id))

    async def perform_qr_login(self, chat_id):
        self._login_in_progress = True
        img_path = "qr_login.png"
        qr_msg = None
        try:
            qr_login = await self.user_client.qr_login()
            for _ in range(3):  # 二维码会过期，自动重建几次
                qrcode.make(qr_login.url).save(img_path)
                caption = ("📷 请用 Telegram 扫码登录：设置 → 设备 → 关联桌面设备。\n"
                           "约 30 秒过期，过期会自动刷新。")
                if qr_msg is None:
                    qr_msg = await self.bot.send_file(chat_id, img_path, caption=caption)
                else:
                    try:
                        await self.bot.edit_message(chat_id, qr_msg, caption, file=img_path)
                    except Exception:
                        qr_msg = await self.bot.send_file(chat_id, img_path, caption=caption)
                try:
                    user = await qr_login.wait(timeout=30)
                    name = user.first_name or user.username or str(user.id)
                    await self.bot.send_message(chat_id, f"✅ 登录成功，欢迎 {name}！发送 /start 打开菜单。")
                    return
                except asyncio.TimeoutError:
                    try:
                        await qr_login.recreate()
                    except Exception:
                        qr_login = await self.user_client.qr_login()
                    continue
                except errors.SessionPasswordNeededError:
                    await self._handle_2fa(chat_id)
                    return
            await self.bot.send_message(chat_id, "⌛ 二维码已过期，请重新 /login。")
        except Exception as e:
            traceback.print_exc()
            await self.bot.send_message(chat_id, f"❌ 登录失败：{e}")
        finally:
            self._login_in_progress = False
            if os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception:
                    pass
            if qr_msg is not None:
                try:
                    await self.bot.delete_messages(chat_id, qr_msg)
                except Exception:
                    pass

    async def _handle_2fa(self, chat_id):
        await self.bot.send_message(chat_id, "🔐 检测到两步验证，请发送你的密码。")
        self.password_future = asyncio.Future()
        try:
            password = await asyncio.wait_for(self.password_future, timeout=120)
        except asyncio.TimeoutError:
            await self.bot.send_message(chat_id, "⌛ 密码输入超时，请重新 /login。")
            return
        finally:
            self.password_future = None
        try:
            user = await self.user_client.sign_in(password=password)
            name = getattr(user, 'first_name', None) or getattr(user, 'username', None) or "用户"
            await self.bot.send_message(chat_id, f"✅ 两步验证登录成功，欢迎 {name}！")
        except Exception as e:
            await self.bot.send_message(chat_id, f"❌ 两步验证登录失败：{e}")

    # ================================================================== #
    # Link parsing & resolution
    # ================================================================== #
    def _parse_link(self, link):
        """解析 t.me 链接，返回 (entity_ref, msg_id)；频道链接 msg_id 为 None。"""
        link = link.strip().split('?', 1)[0].rstrip('/')
        if 't.me/' not in link:
            return None, None
        tail = link.split('t.me/', 1)[1]
        parts = [p for p in tail.split('/') if p]
        if not parts:
            return None, None
        if parts[0] == 'c':
            if len(parts) < 2 or not parts[1].isdigit():
                return None, None
            entity = int(f"-100{parts[1]}")
            if len(parts) < 3:
                return entity, None
            last = parts[-1]
            return entity, (int(last) if last.isdigit() else None)
        username = parts[0]
        if len(parts) < 2:
            return username, None
        last = parts[-1]
        return username, (int(last) if last.isdigit() else None)

    async def _resolve_entity(self, ref):
        try:
            return await self.user_client.get_entity(ref)
        except Exception:
            await self.user_client.get_dialogs()
            return await self.user_client.get_entity(ref)

    async def _fetch_album(self, entity, message, radius=10):
        ids = [i for i in range(message.id - radius, message.id + radius + 1) if i > 0]
        candidates = await self.user_client.get_messages(entity, ids=ids)
        album = [m for m in candidates if m and m.grouped_id == message.grouped_id]
        if not any(m.id == message.id for m in album):
            album.append(message)
        return list({m.id: m for m in album}.values())

    async def handle_link(self, link, event):
        if not await self.user_client.is_user_authorized():
            await event.reply("⚠️ 用户端尚未登录，请先 /login。")
            return
        entity_ref, msg_id = self._parse_link(link)
        if entity_ref is None or msg_id is None:
            await event.reply("❌ 无法解析有效的消息链接。")
            return

        notice = await event.reply("🔍 正在解析链接…")
        try:
            entity = await self._resolve_entity(entity_ref)
        except Exception as e:
            await notice.edit(f"❌ 无法访问该会话：{e}\n请确认你的账号已加入。")
            return
        try:
            message = await self.user_client.get_messages(entity, ids=msg_id)
        except Exception as e:
            await notice.edit(f"❌ 获取消息失败：{e}")
            return
        if not message:
            await notice.edit("❌ 未找到该消息，请确认账号可见。")
            return

        if message.grouped_id:
            album = await self._fetch_album(entity, message)
            items = [m for m in album if m and m.media]
            if items:
                await notice.edit(f"📦 找到相册，共 {len(items)} 个媒体，开始下载…\n📁 {self._rel(self.current_dir)}")
                batch = _Batch(notice, len(items), self.current_dir)
                for m in items:
                    await self._enqueue(m, True, self.current_dir, batch)
                return

        if message.media:
            batch = _Batch(notice, 1, self.current_dir)
            await self._safe_edit(notice, "⏳ 已加入下载队列…")
            await self._enqueue(message, True, self.current_dir, batch)
        else:
            await notice.edit("ℹ️ 该消息没有可下载的媒体。")

    # ================================================================== #
    # Channel bulk download
    # ================================================================== #
    async def start_channel_download(self, link, event):
        if self.channel_task and not self.channel_task.done():
            await event.reply("⚠️ 已有频道下载在进行中，请先 /cancel。")
            return
        entity_ref, _ = self._parse_link(link)
        if entity_ref is None:
            await event.reply("❌ 无法解析有效的频道链接。")
            return

        notice = await event.reply("🔍 正在分析频道…")
        try:
            chat = await self._resolve_entity(entity_ref)
        except Exception as e:
            await notice.edit(f"❌ 无法访问该频道：{e}\n请确认你的账号已加入。")
            return

        title = self._sanitize_filename(getattr(chat, 'title', None) or str(entity_ref))
        folder = os.path.join(self.current_dir, title)
        os.makedirs(folder, exist_ok=True)
        await notice.edit(f"🚀 开始下载频道：{title}\n📁 {self._rel(folder)}")
        self.channel_task = asyncio.create_task(self._run_channel_download(chat, title, folder, notice))

    async def _run_channel_download(self, chat, title, folder, status_msg):
        sem = asyncio.Semaphore(Config.MAX_CONCURRENT_DOWNLOADS)
        stats = {'done': 0, 'skipped': 0, 'failed': 0, 'media': 0}
        last_edit = 0.0
        tasks = []
        rel = self._rel(folder)

        async def handle(message):
            async with sem:
                try:
                    _, skipped = await self._download_with_floodwait(self.user_client, message, folder)
                    stats['skipped' if skipped else 'done'] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    stats['failed'] += 1
                    print(f"Channel download error (msg {message.id}): {ex}")

        try:
            async for message in self.user_client.iter_messages(chat):
                if not message.media:
                    continue
                stats['media'] += 1
                tasks.append(asyncio.create_task(handle(message)))
                if len(tasks) >= Config.MAX_CONCURRENT_DOWNLOADS * 4:
                    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    tasks = list(pending)
                now = time.time()
                if now - last_edit > 4:
                    last_edit = now
                    await self._safe_edit(
                        status_msg,
                        f"📥 正在下载：{title}\n✅ {stats['done']} · ⏭ {stats['skipped']} · ❌ {stats['failed']}\n📁 {rel}"
                    )
            if tasks:
                await asyncio.gather(*tasks)
            await self._safe_edit(
                status_msg,
                f"✅ 频道下载完成：{title}\n📁 {rel}\n"
                f"成功 {stats['done']} · 跳过 {stats['skipped']} · 失败 {stats['failed']}（共 {stats['media']} 个媒体）"
            )
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._safe_edit(
                status_msg,
                f"🛑 已取消：{title}\n已保存 {stats['done']} · 跳过 {stats['skipped']}。"
            )
            raise
        except Exception as e:
            traceback.print_exc()
            await self._safe_edit(status_msg, f"❌ 频道下载出错：{e}")
        finally:
            self.channel_task = None

    # ================================================================== #
    # Cancellation
    # ================================================================== #
    async def cancel_all(self, event):
        cancelled_channel = bool(self.channel_task and not self.channel_task.done())
        if cancelled_channel:
            self.channel_task.cancel()
        self.input_state = None

        drained, seen = 0, set()
        while True:
            try:
                _, _, _, batch = self.download_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.download_queue.task_done()
            drained += 1
            if id(batch) not in seen:
                seen.add(id(batch))
                await self._safe_edit(batch.status, "🛑 已取消（移出队列）。")

        responder = getattr(event, 'respond', None) or event.reply
        await responder(
            "🛑 已请求取消。\n"
            f"频道下载：{'取消中' if cancelled_channel else '无'}\n"
            f"已清除队列项：{drained}\n"
            "注意：正在下载中的文件会先完成。"
        )

    # ================================================================== #
    # Download primitives
    # ================================================================== #
    def _sanitize_filename(self, name):
        name = os.path.basename(str(name))
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip().strip('.')
        return name or "file"

    def _safe_subdir_name(self, name):
        name = name.strip()
        if not name or '/' in name or '\\' in name:
            return None
        cleaned = re.sub(r'[\x00-\x1f<>:"|?*]', '_', name).strip().strip('.')
        if cleaned in ('', '.', '..'):
            return None
        return cleaned

    def _display_name(self, message):
        f = getattr(message, 'file', None)
        if f and f.name:
            return self._sanitize_filename(f.name)
        ext = (f.ext if f and f.ext else '') or ''
        return f"media_{message.id}{ext}"

    def _build_filename(self, message):
        base = None
        f = getattr(message, 'file', None)
        if f and f.name:
            base = f.name
        if not base:
            ext = (f.ext if f and f.ext else '') or ''
            if not ext:
                try:
                    ext = utils.get_extension(message.media) or ''
                except Exception:
                    ext = ''
            if not ext and f and getattr(f, 'mime_type', None):
                ext = mimetypes.guess_extension(f.mime_type) or ''
            base = f"media_{message.id}{ext}"
        base = self._sanitize_filename(base)
        chat_id = getattr(message, 'chat_id', None) or 0
        return f"{chat_id}_{message.id}_{base}"

    async def _safe_download(self, client, message, folder, callback=None, skip_existing=True):
        """确定性命名 + 幂等跳过 + 失败清理。返回 (filepath, skipped)。"""
        os.makedirs(folder, exist_ok=True)
        name = self._build_filename(message)
        filepath = os.path.join(folder, name)

        if skip_existing and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return filepath, True

        async with self._fs_lock:
            if skip_existing and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return filepath, True
            base, ext = os.path.splitext(name)
            i = 1
            while os.path.exists(filepath) and os.path.getsize(filepath) == 0:
                filepath = os.path.join(folder, f"{base}_{i}{ext}")
                i += 1
            open(filepath, 'a').close()  # 占位预留，避免并发同名

        try:
            await client.download_media(message, file=filepath, progress_callback=callback)
            return filepath, False
        except BaseException:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            raise

    async def _enqueue(self, message, use_user_client, folder, batch):
        await self.download_queue.put((message, use_user_client, folder, batch))

    async def download_media(self, message, event, use_user_client=False, folder=None):
        folder = folder or self.current_dir
        status = await event.reply("⏳ 已加入下载队列…")
        batch = _Batch(status, 1, folder)
        await self._enqueue(message, use_user_client, folder, batch)

    # ================================================================== #
    # Path & formatting helpers
    # ================================================================== #
    def _is_root(self, path):
        return os.path.realpath(path) == os.path.realpath(self.root_path)

    def _is_within_root(self, path):
        root = os.path.realpath(self.root_path)
        p = os.path.realpath(path)
        return p == root or p.startswith(root + os.sep)

    def _rel(self, path):
        root = os.path.realpath(self.root_path)
        p = os.path.realpath(path)
        if p == root:
            return "🏠 根目录"
        if p.startswith(root + os.sep):
            return "🏠/" + os.path.relpath(p, root)
        return path

    @staticmethod
    def _short(text, limit=24):
        return text if len(text) <= limit else text[:limit - 1] + "…"

    @staticmethod
    def _human(n):
        n = float(n)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if n < 1024 or unit == 'TB':
                return f"{int(n)}{unit}" if unit == 'B' else f"{n:.1f}{unit}"
            n /= 1024

    @staticmethod
    def _dur(seconds):
        s = int(seconds)
        if s < 60:
            return f"{s}秒"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}分{s}秒"
        h, m = divmod(m, 60)
        return f"{h}时{m}分"

    @staticmethod
    def _bar(pct, width=12):
        filled = int(round(pct / 100 * width))
        filled = max(0, min(width, filled))
        return '█' * filled + '░' * (width - filled)

    async def _safe_edit(self, msg, text):
        if not msg:
            return
        try:
            await msg.edit(text)
        except errors.MessageNotModifiedError:
            pass
        except errors.FloodWaitError:
            pass
        except Exception:
            pass
