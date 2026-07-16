import asyncio
import mimetypes
import os
import re
import time
import traceback

import qrcode
from telethon import TelegramClient, events, errors, utils, Button

from config import Config
from store import Store

PART_SUFFIX = '.part'
# telethon 只有在 offset 对齐 4096 时才走高效的直接下载路径。
CHUNK_ALIGN = 4096
REQUEST_SIZE = 512 * 1024
STATE_DB = 'state.db'
MAX_WORKERS = 8


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
        self._kb_installed = False   # 常驻键盘本进程内是否已下发

        # Pending text-input state: None | 'channel_link' | 'mkdir'
        self.input_state = None

        # Download infrastructure
        self.download_queue = asyncio.Queue()
        self.worker_tasks = []
        self.channel_task = None
        self._fs_lock = asyncio.Lock()
        self._active_paths = set()   # 正在写入的最终文件名，防止同名并发
        self._active_count = 0       # 正在下载（已出队）的任务数

        # Persistent state
        self.store = Store(STATE_DB)
        self._resume_pending = []    # 启动时发现的未完成任务，等管理员确认
        self._root_missing = None    # 上次的根目录已失效时记下它，启动后告警

    # ================================================================== #
    # Lifecycle
    # ================================================================== #
    async def start(self):
        Config.validate()
        await self._load_settings()
        os.makedirs(self.root_path, exist_ok=True)

        print("Starting bot...")
        await self.bot.start(bot_token=Config.BOT_TOKEN)
        # 关闭 Markdown 解析：文件名/路径含 '_'、'*' 会导致编辑消息报错。
        self.bot.parse_mode = None

        self._spawn_workers(Config.MAX_CONCURRENT_DOWNLOADS)

        print("Registering handlers...")
        self.bot.add_event_handler(self.bot_message_handler, events.NewMessage(chats=self.admin_id))
        self.bot.add_event_handler(self.bot_callback_handler, events.CallbackQuery(chats=self.admin_id))

        print("Connecting user client...")
        await self.user_client.connect()

        authorized = await self.user_client.is_user_authorized()
        # 启动就把常驻键盘下发过去，用户不必先打一次 /start。
        try:
            await self.bot.send_message(
                self.admin_id,
                "✅ 系统已启动，用户端已就绪。" if authorized
                else "⚠️ 系统已启动，但用户端尚未登录，请点下方「🎬 菜单」→「📷 登录用户端」，或发送 /login。",
                buttons=self._reply_keyboard(),
            )
            self._kb_installed = True
        except Exception as e:
            print(f"Could not notify admin: {e}")

        if authorized:
            print("User client already authorized.")
            await self._announce_resume()
        else:
            print("User client not authorized.")

        try:
            # run_until_disconnected() issues GetState, which fails on an
            # unauthorized client — only wait on the user client once it has a
            # valid session; otherwise the bot alone keeps the loop alive.
            waiters = [self.bot.run_until_disconnected()]
            if authorized:
                waiters.append(self.user_client.run_until_disconnected())
            await asyncio.gather(*waiters)
        finally:
            await self._shutdown()

    async def _shutdown(self):
        for task in self.worker_tasks:
            task.cancel()
        if self.channel_task and not self.channel_task.done():
            self.channel_task.cancel()
        # 等任务真正结束，否则退出时会刷 "Task was destroyed but it is pending"。
        pending = [t for t in self.worker_tasks + [self.channel_task] if t]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.store.close()
        for client in (self.bot, self.user_client):
            try:
                await client.disconnect()
            except Exception:
                pass

    # ================================================================== #
    # Portable paths
    # ================================================================== #
    # 数据库里一律存「相对根目录 + POSIX 分隔符」的路径，不存绝对路径。
    # 这样整个下载树被搬到别的位置（换盘符、Docker 卷重新挂载、迁移到另一台
    # 机器）后，只要 /setroot 指到新位置，未完成的任务就仍然指向正确的文件。
    def _store_folder(self, folder):
        """绝对路径 → 入库表示。"""
        try:
            rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(self.root_path))
        except ValueError:
            # Windows 上跨盘符无法求相对路径，只能退回绝对路径。
            return os.path.abspath(folder)
        if rel.startswith('..'):
            return os.path.abspath(folder)  # 根目录之外，无法移植
        return '.' if rel == '.' else rel.replace(os.sep, '/')

    def _resolve_folder(self, stored):
        """入库表示 → 当前根目录下的绝对路径。"""
        if os.path.isabs(stored):
            return stored  # 旧数据或根目录外的路径，原样使用
        if stored in ('.', ''):
            return self.root_path
        return os.path.join(self.root_path, *stored.split('/'))

    async def _migrate_folders(self):
        """把历史遗留的绝对路径改写成相对表示（只做一次）。"""
        if await self.store.get_setting('folders_relative') == '1':
            return
        for old in await self.store.all_folders():
            if not os.path.isabs(old):
                continue
            new = self._store_folder(old)
            if new != old:
                print(f"Migrating stored path: {old} -> {new}")
                await self.store.rewrite_folder(old, new)
        await self.store.set_setting('folders_relative', '1')

    # ================================================================== #
    # Settings & worker pool
    # ================================================================== #
    async def _load_settings(self):
        """恢复上次的根目录/下载目录/并发数。目录失效则回退到默认值。"""
        root = await self.store.get_setting('root_path')
        if root and os.path.isdir(root):
            self.root_path = os.path.abspath(root)
        elif root:
            # 盘符没挂上 / 目录被搬走。此时回退到默认根目录会把文件下到错误的
            # 位置，所以要明确报出来，让用户用 /setroot 指到新位置。
            self._root_missing = root
            print(f"WARNING: saved root {root} is gone; falling back to {self.root_path}")
        self.current_dir = self.root_path
        cur = await self.store.get_setting('current_dir')
        if cur and os.path.isdir(cur) and self._is_within_root(cur):
            self.current_dir = os.path.abspath(cur)
        self.browse_dir = self.current_dir

        conc = await self.store.get_setting('max_concurrent')
        if conc:
            try:
                Config.MAX_CONCURRENT_DOWNLOADS = max(1, min(MAX_WORKERS, int(conc)))
            except ValueError:
                pass

        await self._migrate_folders()

    def _spawn_workers(self, n):
        for i in range(len(self.worker_tasks), n):
            self.worker_tasks.append(asyncio.create_task(self.download_worker(i)))
            print(f"Started download worker {i + 1}")

    async def _set_concurrency(self, n):
        """调整并发数。增加立即生效；减少时用毒丸让空闲 worker 自然退出，
        绝不打断正在进行的下载。"""
        n = max(1, min(MAX_WORKERS, n))
        Config.MAX_CONCURRENT_DOWNLOADS = n
        await self.store.set_setting('max_concurrent', n)
        alive = [t for t in self.worker_tasks if not t.done()]
        self.worker_tasks = alive
        if n > len(alive):
            self._spawn_workers(n)
        elif n < len(alive):
            for _ in range(len(alive) - n):
                await self.download_queue.put(None)
        return n

    # ================================================================== #
    # Resume after restart
    # ================================================================== #
    async def _announce_resume(self):
        """启动时若发现未完成的任务，让管理员决定是否续传。"""
        if self._root_missing:
            await self._notify_admin(
                f"⚠️ 上次的工作根目录已不存在：\n📁 {self._root_missing}\n\n"
                f"已临时回退到：\n📁 {self.root_path}\n\n"
                "如果你把下载目录搬到了别处，请先用 /setroot <新路径> 指过去，"
                "未完成的任务会自动跟着重新定位；否则文件会下载到上面这个临时目录。"
            )
        jobs = await self.store.pending_jobs()
        channels = await self.store.running_channels()
        if not jobs and not channels:
            return
        self._resume_pending = jobs
        lines = ["🔄 检测到上次未完成的任务："]
        if jobs:
            lines.append(f"• 待续传媒体：{len(jobs)} 个")
        for ch in channels:
            lines.append(f"• 频道「{ch['title']}」已扫描到消息 {ch['cursor']}，可继续")
        lines.append("\n已下载的部分会保留，续传从断点继续。")
        await self.bot.send_message(
            self.admin_id, "\n".join(lines),
            buttons=[[Button.inline("▶️ 继续下载", b"resume"),
                      Button.inline("🗑 放弃并清空", b"resume_drop")]],
        )

    async def _do_resume(self, chat_id):
        """把持久化的 pending 任务重新取回消息对象并入队。"""
        jobs = await self.store.pending_jobs()
        channels = await self.store.running_channels()
        if not jobs and not channels:
            await self.bot.send_message(chat_id, "没有需要续传的任务。")
            return

        status = await self.bot.send_message(chat_id, f"🔄 正在恢复 {len(jobs)} 个媒体任务…")

        # 同一会话+目录的任务合并成一批，减少 get_messages 往返。
        groups = {}
        for j in jobs:
            groups.setdefault((j['chat_id'], j['folder'], j['use_user']), []).append(j)

        queued, lost = 0, 0
        for (cid, stored_folder, use_user), items in groups.items():
            folder = self._resolve_folder(stored_folder)
            client = self.user_client if use_user else self.bot
            try:
                entity = await self._resolve_entity(cid) if use_user else cid
                msgs = await client.get_messages(entity, ids=[j['msg_id'] for j in items])
            except Exception as e:
                print(f"Resume: cannot fetch {cid}: {e}")
                for j in items:
                    await self.store.mark_job(j['id'], 'failed', error=f"恢复失败：{e}")
                lost += len(items)
                continue

            found = [(j, m) for j, m in zip(items, msgs) if m and m.media]
            for j, m in zip(items, msgs):
                if not (m and m.media):
                    await self.store.mark_job(j['id'], 'failed', error="消息已不存在或无媒体")
                    lost += 1
            if not found:
                continue
            batch = _Batch(status, len(found), folder)
            for j, m in found:
                await self._enqueue(m, bool(use_user), folder, batch,
                                    origin=j['origin'], job_id=j['id'])
                queued += 1

        resumed_channel = None
        if channels and not (self.channel_task and not self.channel_task.done()):
            ch = channels[0]
            try:
                chat = await self._resolve_entity(ch['entity_id'])
                notice = await self.bot.send_message(chat_id, f"🔄 正在继续频道：{ch['title']}")
                self.channel_task = asyncio.create_task(
                    self._run_channel_download(chat, ch['title'],
                                               self._resolve_folder(ch['folder']), notice, ch))
                resumed_channel = ch['title']
            except Exception as e:
                await self.bot.send_message(chat_id, f"⚠️ 频道「{ch['title']}」恢复失败：{e}")

        parts = [f"✅ 已重新入队 {queued} 个媒体任务。"]
        if lost:
            parts.append(f"⚠️ {lost} 个消息已失效，标记为失败。")
        if resumed_channel:
            parts.append(f"📂 频道「{resumed_channel}」已从断点继续。")
        if len(channels) > 1:
            parts.append(f"ℹ️ 另有 {len(channels) - 1} 个频道任务待恢复，完成后可再次点击继续。")
        await self._safe_edit(status, "\n".join(parts))

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
            item = await self.download_queue.get()
            if item is None:  # 毒丸：并发数被调低，本 worker 退出
                self.download_queue.task_done()
                print(f"Worker {worker_id} stopping")
                return
            message, use_user_client, folder, batch, job_id = item
            single = batch.total == 1
            self._active_count += 1
            try:
                client = self.user_client if use_user_client else self.bot
                folder = folder or self.current_dir
                disp = self._display_name(message)

                callback = None
                if single:
                    await self._safe_edit(batch.status, f"⬇️ 开始下载：{disp}")
                    callback = self._make_progress_cb(batch.status, disp, folder)

                out, skipped = await self._download_with_floodwait(client, message, folder, callback)
                await self.store.mark_job(job_id, 'skipped' if skipped else 'done',
                                          filename=os.path.basename(out))

                async with batch.lock:
                    if skipped:
                        batch.skipped += 1
                    else:
                        batch.done += 1
                    await self._update_batch(batch, single=single,
                                             name=os.path.basename(out), skipped=skipped)
            except asyncio.CancelledError:
                # 进程正在退出：留在 pending，重启后自动续传。
                raise
            except Exception as e:
                print(f"Worker {worker_id} error: {e}")
                await self.store.mark_job(job_id, 'failed', error=str(e))
                async with batch.lock:
                    batch.failed += 1
                    await self._update_batch(batch, single=single, error=str(e))
            finally:
                self._active_count -= 1
                self.download_queue.task_done()

    async def _update_batch(self, batch, single, name=None, skipped=False, error=None):
        # 终态才挂菜单按钮：下载过程中每隔两秒刷新一次，挂上去会不停闪。
        if single:
            rel = self._rel(batch.folder)
            menu = self._menu_only_button()
            if error:
                await self._safe_edit(batch.status, f"❌ 下载失败：{error}", buttons=menu)
            elif skipped:
                await self._safe_edit(batch.status, f"⏭ 已存在，跳过：{name}\n📁 {rel}", buttons=menu)
            else:
                await self._safe_edit(batch.status, f"✅ 已保存：{name}\n📁 {rel}", buttons=menu)
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
                f"成功 {batch.done} · 跳过 {batch.skipped} · 失败 {batch.failed}（共 {batch.total}）",
                buttons=self._menu_only_button(),
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
        queued = self.download_queue.qsize()
        busy = f"⬇️ {self._active_count} 下载中 · ⏳ {queued} 排队" if (self._active_count or queued) else "💤 空闲"
        text = (
            "🎬 Telegram 媒体管理器\n\n"
            f"📥 当前下载目录：{rel}\n"
            f"📊 {busy}\n\n"
            "• 直接发送消息链接（t.me/…）即可下载受限媒体\n"
            "• 转发媒体到这里可直接下载\n"
            "• 中断的下载会自动断点续传"
        )
        buttons = [
            [Button.inline(f"🗂 文件夹管理（{self._short(self._rel(self.current_dir), 16)}）", b"dirs")],
            [Button.inline("📥 任务列表", b"tasks"), Button.inline("📊 状态", b"status")],
            [Button.inline("📂 下载整个频道", b"dl_channel"), Button.inline("🛑 取消任务", b"cancel")],
            [Button.inline("⚙️ 设置", b"settings"), Button.inline("📷 登录用户端", b"login")],
            [Button.inline("❓ 帮助", b"help")],
        ]
        return text, buttons

    # 常驻键盘的按钮点击后会发回这段文字，靠它反查要执行的动作。
    KB_MENU = "🎬 菜单"
    KB_FOLDERS = "🗂 文件夹"
    KB_TASKS = "📥 任务"
    KB_STATUS = "📊 状态"

    def _reply_keyboard(self):
        """输入框上方的常驻键盘，省得每次手打 /start。"""
        return [
            [Button.text(self.KB_MENU, resize=True), Button.text(self.KB_FOLDERS, resize=True)],
            [Button.text(self.KB_TASKS, resize=True), Button.text(self.KB_STATUS, resize=True)],
        ]

    async def _install_keyboard(self, chat_id):
        """下发一次常驻键盘。客户端会一直记住它，所以每个进程发一次就够。"""
        if self._kb_installed:
            return
        self._kb_installed = True
        try:
            await self.bot.send_message(
                chat_id, "⌨️ 快捷菜单已启用，随时点输入框上方的按钮即可，无需再输入 /start。",
                buttons=self._reply_keyboard(),
            )
        except Exception as e:
            print(f"Could not install keyboard: {e}")
            self._kb_installed = False

    async def show_menu(self, event):
        await self._install_keyboard(event.chat_id)
        text, buttons = self._menu_content()
        await event.reply(text, buttons=buttons)

    async def _tasks_content(self):
        counts = await self.store.counts()
        pending = await self.store.pending_jobs()
        failed = await self.store.failed_jobs()
        channels = await self.store.running_channels()

        lines = [
            "📥 任务列表\n",
            f"⬇️ 下载中：{self._active_count}",
            f"⏳ 队列中：{self.download_queue.qsize()}",
            f"✅ 已完成：{counts.get('done', 0)} · ⏭ 跳过：{counts.get('skipped', 0)}",
            f"❌ 失败：{len(failed)}",
        ]
        if channels:
            lines.append("")
            for ch in channels:
                lines.append(f"📂 频道「{ch['title']}」进行中（已扫描 {ch['seen']} 个媒体）")
        if pending:
            lines.append(f"\n未完成任务 {len(pending)} 个，最近几个：")
            for j in pending[:5]:
                lines.append(f"  • {j['origin']} · 消息 {j['msg_id']}")
        if failed:
            lines.append(f"\n失败任务 {len(failed)} 个，最近几个：")
            for j in failed[:5]:
                lines.append(f"  • 消息 {j['msg_id']}：{self._short(j['error'] or '未知错误', 40)}")
        if not pending and not failed and not channels and not self._active_count:
            lines.append("\n🎉 当前没有待处理的任务。")

        buttons = []
        if failed:
            buttons.append([Button.inline(f"🔁 重试失败（{len(failed)}）", b"retry_failed")])
        if pending or channels:
            buttons.append([Button.inline("▶️ 继续未完成任务", b"resume")])
        buttons.append([Button.inline("🔄 刷新", b"tasks"), Button.inline("◀️ 返回菜单", b"menu")])
        return "\n".join(lines), buttons

    def _settings_content(self):
        text = (
            "⚙️ 设置\n\n"
            f"并发下载数：{Config.MAX_CONCURRENT_DOWNLOADS}（上限 {MAX_WORKERS}）\n"
            f"工作根目录：{self.root_path}\n"
            f"当前下载目录：{self._rel(self.current_dir)}\n\n"
            "调低并发不会打断正在下载的文件。\n"
            "所有设置会自动保存，重启后依然生效。"
        )
        buttons = [
            [Button.inline("➖ 并发", b"conc_dec"),
             Button.inline(f"{Config.MAX_CONCURRENT_DOWNLOADS} 并发", b"noop"),
             Button.inline("➕ 并发", b"conc_inc")],
            [Button.inline("🗂 文件夹管理", b"dirs")],
            [Button.inline("◀️ 返回菜单", b"menu")],
        ]
        return text, buttons

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

    async def _edit_or_send(self, event, text, buttons):
        """就地更新按钮消息；失败（如消息过旧）则退回发送新消息。"""
        try:
            await self.bot.edit_message(event.chat_id, event.message_id, text, buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
        except Exception:
            await self.bot.send_message(event.chat_id, text, buttons=buttons)

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
                await self.store.set_setting('current_dir', self.current_dir)
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
            elif data == 'tasks':
                text, buttons = await self._tasks_content()
                await self._edit_or_send(event, text, buttons)
            elif data == 'settings':
                text, buttons = self._settings_content()
                await self._edit_or_send(event, text, buttons)
            elif data in ('conc_inc', 'conc_dec'):
                delta = 1 if data == 'conc_inc' else -1
                n = await self._set_concurrency(Config.MAX_CONCURRENT_DOWNLOADS + delta)
                toast = f"并发下载数已设为 {n}"
                text, buttons = self._settings_content()
                await self._edit_or_send(event, text, buttons)
            elif data == 'retry_failed':
                n = await self.store.requeue_failed()
                toast = f"已将 {n} 个失败任务重新排队" if n else "没有失败的任务"
                if n:
                    await self._do_resume(event.chat_id)
                text, buttons = await self._tasks_content()
                await self._edit_or_send(event, text, buttons)
            elif data == 'resume':
                await self._do_resume(event.chat_id)
            elif data == 'resume_drop':
                n = await self.store.clear_unfinished()
                self._resume_pending = []
                toast = f"已放弃 {n} 个未完成任务"
                await self._edit_or_send(event, f"🗑 已清空 {n} 个未完成任务的记录。\n"
                                                "注意：已下载的 .part 片段仍保留在磁盘上。", None)
            elif data == 'noop':
                pass
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
    def _kb_actions(self):
        return {
            self.KB_MENU: self.show_menu,
            self.KB_FOLDERS: self._kb_open_folders,
            self.KB_TASKS: self._kb_open_tasks,
            self.KB_STATUS: self._kb_open_status,
        }

    async def _kb_open_folders(self, event):
        self.browse_dir = self.current_dir
        await self._draw_browser(event.chat_id)

    async def _kb_open_tasks(self, event):
        text, buttons = await self._tasks_content()
        await event.reply(text, buttons=buttons)

    async def _kb_open_status(self, event):
        await event.reply(await self._status_text(), buttons=self._menu_only_button())

    async def bot_message_handler(self, event):
        text = (event.message.text or '').strip()
        has_media = event.message.media is not None
        is_command = text.startswith('/')

        # 0) 常驻键盘按钮最优先：它们是导航动作，绝不能被当成 2FA 密码或
        #    文件夹名吞掉（用户在等待输入时误点是很常见的）。
        if not has_media and text in self._kb_actions():
            self.input_state = None
            await self._kb_actions()[text](event)
            return

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
        if cmd == '/tasks':
            text, buttons = await self._tasks_content()
            await event.reply(text, buttons=buttons)
            return
        if cmd == '/settings':
            text, buttons = self._settings_content()
            await event.reply(text, buttons=buttons)
            return
        if cmd == '/resume':
            if not await self._require_user_client(event):
                return
            await self._do_resume(event.chat_id)
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
        await self.store.set_setting('current_dir', path)
        await event.reply(f"✅ 已创建并切换下载目录：\n📁 {self._rel(path)}",
                          buttons=self._menu_only_button())
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
            await self.store.set_setting('root_path', new_path)
            await self.store.set_setting('current_dir', new_path)
            msg = f"✅ 工作根目录已设为：\n📁 {new_path}"
            # 任务里存的是相对路径，所以换根目录 = 整个下载树跟着搬家。
            pending = await self.store.pending_jobs()
            if pending:
                msg += (f"\n\nℹ️ {len(pending)} 个未完成任务会自动指向新根目录下的对应位置。"
                        "\n如果你只是想换个下载位置、而不是搬走了整个目录树，"
                        "请先用 /tasks 处理完这些任务。")
            await event.reply(msg)
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
            "/tasks — 任务列表与重试失败\n"
            "/settings — 并发数等设置\n"
            "/resume — 继续未完成的下载\n"
            "/login — 扫码登录用户端\n"
            "/download_channel — 批量下载整个频道\n"
            "/setroot <路径> — 设置工作根目录\n"
            "/status — 查看状态\n"
            "/cancel — 取消频道下载并清空队列\n\n"
            "用法：\n"
            "• 发送 t.me 消息链接 → 下载受限媒体（含相册）\n"
            "• 转发媒体到这里 → 直接下载\n"
            "• 重复发送同一链接会自动跳过已下载的文件\n\n"
            "断点续传：\n"
            "• 下载中的文件存为 .part，中断后从断点继续，不会重下\n"
            "• 重启程序会提示恢复上次未完成的任务\n"
            "• 频道下载会记录扫描进度，续传时不必从头再扫"
        )

    async def _status_text(self):
        is_auth = await self.user_client.is_user_authorized()
        auth = "✅ 已登录" if is_auth else "❌ 未登录"
        channel = "运行中" if (self.channel_task and not self.channel_task.done()) else "空闲"
        counts = await self.store.counts()
        return (
            "📊 系统状态\n\n"
            f"用户端：{auth}\n"
            f"正在下载：{self._active_count}\n"
            f"队列中待下载：{self.download_queue.qsize()}\n"
            f"并发下载数：{Config.MAX_CONCURRENT_DOWNLOADS}\n"
            f"频道下载任务：{channel}\n"
            f"累计完成：{counts.get('done', 0)} · 跳过 {counts.get('skipped', 0)} · 失败 {counts.get('failed', 0)}\n"
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
                    await self._enqueue(m, True, self.current_dir, batch, origin='album')
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
        ch_job = await self.store.start_channel(utils.get_peer_id(chat), title,
                                                self._store_folder(folder))
        await notice.edit(f"🚀 开始下载频道：{title}\n📁 {self._rel(folder)}")
        self.channel_task = asyncio.create_task(
            self._run_channel_download(chat, title, folder, notice, ch_job))

    async def _run_channel_download(self, chat, title, folder, status_msg, ch_job=None):
        sem = asyncio.Semaphore(Config.MAX_CONCURRENT_DOWNLOADS)
        stats = {'done': 0, 'skipped': 0, 'failed': 0, 'media': 0}
        last_edit = 0.0
        tasks = []
        rel = self._rel(folder)
        job_row_id = ch_job['id'] if ch_job else None
        # cursor 记录已扫描到的最旧消息 id；iter_messages 默认从新到旧。
        cursor = int(ch_job['cursor']) if ch_job and ch_job['cursor'] else 0
        seen_base = int(ch_job['seen']) if ch_job and ch_job['seen'] else 0
        if cursor:
            await self._safe_edit(status_msg, f"⏭ 从上次中断处继续：{title}（消息 {cursor} 之前）\n📁 {rel}")

        async def handle(message, job_id):
            async with sem:
                try:
                    out, skipped = await self._download_with_floodwait(self.user_client, message, folder)
                    stats['skipped' if skipped else 'done'] += 1
                    await self.store.mark_job(job_id, 'skipped' if skipped else 'done',
                                              filename=os.path.basename(out))
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    stats['failed'] += 1
                    await self.store.mark_job(job_id, 'failed', error=str(ex))
                    print(f"Channel download error (msg {message.id}): {ex}")

        try:
            kwargs = {'offset_id': cursor} if cursor else {}
            async for message in self.user_client.iter_messages(chat, **kwargs):
                if not message.media:
                    # 无媒体的消息也要推进游标，否则重启后会反复扫描它们。
                    cursor = message.id
                    continue
                stats['media'] += 1
                job_id = await self.store.add_job(utils.get_peer_id(chat), message.id,
                                                  self._store_folder(folder),
                                                  use_user=True, origin='channel')
                tasks.append(asyncio.create_task(handle(message, job_id)))
                cursor = message.id
                if len(tasks) >= Config.MAX_CONCURRENT_DOWNLOADS * 4:
                    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    tasks = list(pending)
                now = time.time()
                if now - last_edit > 4:
                    last_edit = now
                    # 游标只在这里持久化：即使它比实际完成的进度超前，未完成的
                    # 消息仍以 pending 记录在 jobs 表中，重启时会先补做。
                    await self.store.update_channel(job_row_id, cursor=cursor,
                                                    seen=seen_base + stats['media'])
                    await self._safe_edit(
                        status_msg,
                        f"📥 正在下载：{title}\n✅ {stats['done']} · ⏭ {stats['skipped']} · ❌ {stats['failed']}\n📁 {rel}"
                    )
            if tasks:
                await asyncio.gather(*tasks)
            await self.store.update_channel(job_row_id, cursor=cursor,
                                            seen=seen_base + stats['media'], status='done')
            await self._safe_edit(
                status_msg,
                f"✅ 频道下载完成：{title}\n📁 {rel}\n"
                f"成功 {stats['done']} · 跳过 {stats['skipped']} · 失败 {stats['failed']}（共 {stats['media']} 个媒体）",
                buttons=self._menu_only_button(),
            )
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.store.update_channel(job_row_id, cursor=cursor,
                                            seen=seen_base + stats['media'], status='cancelled')
            await self._safe_edit(
                status_msg,
                f"🛑 已取消：{title}\n已保存 {stats['done']} · 跳过 {stats['skipped']}。\n"
                "（进度已记录，重新下载该频道会从这里继续）"
            )
            raise
        except Exception as e:
            traceback.print_exc()
            await self.store.update_channel(job_row_id, cursor=cursor,
                                            seen=seen_base + stats['media'])
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
        pills = []
        while True:
            try:
                item = self.download_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.download_queue.task_done()
            if item is None:  # 毒丸不属于用户任务，稍后放回
                pills.append(item)
                continue
            _, _, _, batch, job_id = item
            await self.store.mark_job(job_id, 'cancelled')
            drained += 1
            if id(batch) not in seen:
                seen.add(id(batch))
                await self._safe_edit(batch.status, "🛑 已取消（移出队列）。")

        for pill in pills:
            await self.download_queue.put(pill)

        responder = getattr(event, 'respond', None) or event.reply
        await responder(
            "🛑 已请求取消。\n"
            f"频道下载：{'取消中' if cancelled_channel else '无'}\n"
            f"已清除队列项：{drained}\n"
            "注意：正在下载中的文件会先完成，已下载的部分会保留以便续传。",
            buttons=self._menu_only_button(),
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
        """确定性命名 + 幂等跳过 + 断点续传。返回 (filepath, skipped)。

        数据先写入 `<name>.part`，完成后原子改名为最终文件名。中断时 .part 会
        被保留，下次从已有字节数继续，因此重启/断网不会丢失已下载的部分。
        """
        os.makedirs(folder, exist_ok=True)
        name = self._build_filename(message)
        filepath = os.path.join(folder, name)
        part = filepath + PART_SUFFIX

        if skip_existing and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return filepath, True

        # 同一文件不允许两个 worker 同时写，否则 .part 会互相覆盖。
        async with self._fs_lock:
            if skip_existing and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return filepath, True
            if filepath in self._active_paths:
                return filepath, True
            self._active_paths.add(filepath)

        try:
            total = getattr(getattr(message, 'file', None), 'size', None) or 0
            offset = 0
            if os.path.exists(part):
                size = os.path.getsize(part)
                # 未对齐到 4096 的尾巴丢掉：telethon 只能从对齐位置高效续传，
                # 且最后一块可能是写了一半的脏数据。
                offset = size - (size % CHUNK_ALIGN)
                if offset != size:
                    with open(part, 'r+b') as fh:
                        fh.truncate(offset)

            if total and offset >= total:
                os.replace(part, filepath)
                return filepath, False

            if offset and callback:
                await callback(offset, total or offset)

            written = offset
            with open(part, 'ab') as fh:
                async for chunk in client.iter_download(message, offset=offset,
                                                        request_size=REQUEST_SIZE):
                    fh.write(chunk)
                    written += len(chunk)
                    if callback:
                        await callback(min(written, total) if total else written, total or written)

            os.replace(part, filepath)
            return filepath, False
        except BaseException:
            # 故意保留 .part —— 这正是下次续传的依据。
            raise
        finally:
            async with self._fs_lock:
                self._active_paths.discard(filepath)

    async def _enqueue(self, message, use_user_client, folder, batch, origin='link', job_id=None):
        if job_id is None:
            job_id = await self.store.add_job(
                getattr(message, 'chat_id', None) or 0, message.id, self._store_folder(folder),
                use_user=use_user_client, origin=origin,
            )
        await self.download_queue.put((message, use_user_client, folder, batch, job_id))

    async def download_media(self, message, event, use_user_client=False, folder=None):
        folder = folder or self.current_dir
        status = await event.reply("⏳ 已加入下载队列…")
        batch = _Batch(status, 1, folder)
        await self._enqueue(message, use_user_client, folder, batch, origin='forward')

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
        """展示用路径。统一成 '/' 分隔，避免 Windows 上出现 '🏠/a\\b' 这种混排。"""
        root = os.path.realpath(self.root_path)
        p = os.path.realpath(path)
        if p == root:
            return "🏠 根目录"
        if p.startswith(root + os.sep):
            return "🏠/" + os.path.relpath(p, root).replace(os.sep, '/')
        return path.replace(os.sep, '/')

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

    def _menu_only_button(self):
        return [[Button.inline("🎬 打开菜单", b"menu")]]

    async def _safe_edit(self, msg, text, buttons=None):
        if not msg:
            return
        try:
            await msg.edit(text, buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
        except errors.FloodWaitError:
            pass
        except Exception:
            pass
