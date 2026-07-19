"""Telegram bot controller.

Reuses the shared ``DownloadEngine`` so anything done from the bot shows up in the
web panel and vice-versa. Deliberately small: link/forward downloads, channel
downloads, status, cancel, resume, and an inline folder browser for switching the
download directory. Rich file management lives in the panel.
"""

import asyncio
import os

from telethon import Button, TelegramClient, errors, events


class BotController:
    def __init__(self, services):
        self.services = services
        self.engine = services.engine
        self.tg = services.tg
        self.client: TelegramClient | None = None
        self.admin_id = 0
        self._run_task = None

        # Folder-browser state (separate from the download dir until "set").
        self.browse_dir = None
        self._browse_entries = []
        self._browse_msg = None      # (chat_id, message_id) of the browser message
        self._input_state = None     # None | 'mkdir'

    async def start(self):
        admin = await self.services.store.get_setting("tg_admin_id")
        self.admin_id = int(admin) if admin else 0
        self.client = await self.tg.build_bot_client()
        if not self.client:
            raise RuntimeError("bot token not configured")

        chats = self.admin_id or None
        self.client.add_event_handler(self._on_message, events.NewMessage(chats=chats))
        self.client.add_event_handler(self._on_callback, events.CallbackQuery(chats=chats))

        self._run_task = asyncio.create_task(self.client.run_until_disconnected())
        if self.admin_id:
            try:
                await self.client.send_message(
                    self.admin_id,
                    "🤖 机器人已连接。Web 面板为主控制台；这里可快速下载与切换目录。",
                    buttons=self._keyboard(),
                )
            except Exception:
                pass

    async def stop(self):
        if self._run_task:
            self._run_task.cancel()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None

    def _keyboard(self):
        return [
            [Button.text("📊 状态", resize=True), Button.text("📁 目录", resize=True)],
            [Button.text("📥 任务", resize=True), Button.text("▶️ 继续", resize=True)],
            [Button.text("🛑 取消", resize=True)],
        ]

    def _admin_only(self, event):
        return not self.admin_id or event.chat_id == self.admin_id

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    async def _on_message(self, event):
        if not self._admin_only(event):
            return
        text = (event.message.text or "").strip()
        has_media = event.message.media is not None

        # Reply-keyboard buttons.
        if text == "📊 状态":
            return await event.reply(await self._status_text())
        if text == "📁 目录":
            return await self._open_browser(event)
        if text == "📥 任务":
            return await event.reply(await self._tasks_text())
        if text == "▶️ 继续":
            r = await self.engine.resume()
            return await event.reply(f"▶️ 已重新入队 {r['queued']} 个任务。")
        if text == "🛑 取消":
            r = await self.engine.cancel_all()
            return await event.reply(f"🛑 已清除队列 {r['drained']} 项。")

        is_command = text.startswith("/")
        if is_command:
            self._input_state = None

        # Pending text input (new folder name).
        if self._input_state == "mkdir" and text and not is_command and not has_media:
            self._input_state = None
            return await self._handle_mkdir_input(event, text)

        cmd = text.split()[0].lower() if text else ""
        if cmd == "/start":
            return await event.reply("🎬 发送 t.me 链接下载，或转发媒体到这里。",
                                     buttons=self._keyboard())
        if cmd == "/status":
            return await event.reply(await self._status_text())
        if cmd == "/folders":
            return await self._open_browser(event)
        if cmd == "/tasks":
            return await event.reply(await self._tasks_text())
        if cmd == "/cancel":
            r = await self.engine.cancel_all()
            return await event.reply(f"🛑 已清除队列 {r['drained']} 项。")
        if cmd == "/resume":
            r = await self.engine.resume()
            return await event.reply(f"▶️ 已重新入队 {r['queued']} 个任务。")

        if "t.me/" in text and not has_media:
            notice = await event.reply("🔍 正在解析链接…")
            r = await self.engine.download_link(text)
            if r.get("ok"):
                await notice.edit(f"📥 已加入 {r['count']} 个媒体到下载队列\n📁 {r['folder']}")
            else:
                await notice.edit(f"❌ {r.get('error')}")
            return

        if has_media:
            await self.engine.download_message(event.message, use_user=False)
            return await event.reply("📥 已加入下载队列。")

    async def _handle_mkdir_input(self, event, name):
        path = self.engine.make_dir(self.browse_dir or self.engine.current_dir, name)
        if not path:
            return await event.reply("❌ 无效的文件夹名。")
        await self.engine.set_current_dir(path)
        self.browse_dir = path
        await event.reply(f"✅ 已创建并设为下载目录：\n📁 {self.engine._rel(path)}")
        if self._browse_msg:
            await self._redraw_browser()

    # ------------------------------------------------------------------ #
    # Folder browser
    # ------------------------------------------------------------------ #
    def _browser_content(self):
        eng = self.engine
        target, names = eng.list_dir(self.browse_dir or eng.current_dir)
        self.browse_dir = target
        self._browse_entries = names[:30]

        rows, row = [], []
        for i, name in enumerate(self._browse_entries):
            label = name if len(name) <= 18 else name[:17] + "…"
            row.append(Button.inline(f"📁 {label}", f"d:cd:{i}".encode()))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        nav = []
        if os.path.realpath(target) != os.path.realpath(eng.root_path):
            nav.append(Button.inline("⬆️ 上级", b"d:up"))
        nav.append(Button.inline("🏠 根目录", b"d:home"))
        rows.append(nav)
        rows.append([Button.inline("➕ 新建", b"d:mkdir"),
                     Button.inline("✅ 设为下载目录", b"d:use")])
        rows.append([Button.inline("🔄 刷新", b"d:refresh")])

        is_cur = os.path.realpath(target) == os.path.realpath(eng.current_dir)
        summary = f"共 {len(names)} 个子文件夹" if names else "（空文件夹）"
        if len(names) > 30:
            summary += "，仅显示前 30 个"
        text = (
            "🗂 文件夹浏览\n\n"
            f"📍 正在浏览：{eng._rel(target)}{'  ⬅️ 当前下载目录' if is_cur else ''}\n"
            f"📥 下载目录：{eng._rel(eng.current_dir)}\n\n"
            f"{summary}\n点 📁 进入，或「设为下载目录」。"
        )
        return text, rows

    async def _open_browser(self, event):
        self.browse_dir = self.engine.current_dir
        text, rows = self._browser_content()
        msg = await event.reply(text, buttons=rows)
        self._browse_msg = (msg.chat_id, msg.id)

    async def _redraw_browser(self):
        text, rows = self._browser_content()
        try:
            await self.client.edit_message(self._browse_msg[0], self._browse_msg[1], text, buttons=rows)
        except errors.RPCError:
            pass

    async def _on_callback(self, event):
        if not self._admin_only(event):
            try:
                await event.answer()
            except errors.RPCError:
                pass
            return
        data = event.data.decode("utf-8")
        toast = None
        try:
            if data.startswith("d:"):
                toast = await self._folder_callback(event, data[2:])
        finally:
            try:
                await event.answer(toast)
            except errors.RPCError:
                pass

    async def _folder_callback(self, event, action):
        eng = self.engine
        self._browse_msg = (event.chat_id, event.message_id)
        if action == "up":
            parent = os.path.dirname(self.browse_dir or eng.current_dir)
            if eng._is_within_root(parent):
                self.browse_dir = parent
        elif action == "home":
            self.browse_dir = eng.root_path
        elif action.startswith("cd:"):
            idx = int(action[3:])
            if 0 <= idx < len(self._browse_entries):
                target = os.path.join(self.browse_dir, self._browse_entries[idx])
                if eng._is_within_root(target) and os.path.isdir(target):
                    self.browse_dir = target
        elif action == "use":
            await eng.set_current_dir(self.browse_dir)
            await self._redraw_browser()
            return "已设为下载目录"
        elif action == "mkdir":
            self._input_state = "mkdir"
            await event.respond("✏️ 请发送新文件夹名称（将建在当前浏览目录下并设为下载目录）。")
            return "等待输入名称"
        # up / home / cd / refresh → redraw
        text, rows = self._browser_content()
        try:
            await event.edit(text, buttons=rows)
        except errors.RPCError:
            pass
        return None

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    async def _status_text(self):
        me = await self.tg.me()
        counts = await self.services.store.counts()
        return (
            "📊 系统状态\n\n"
            f"用户端：{'✅ ' + me['name'] if me else '❌ 未登录'}\n"
            f"下载中：{self.engine.active_count} · 队列：{self.engine.queue_size}\n"
            f"并发：{self.engine.max_concurrent}\n"
            f"累计完成：{counts.get('done', 0)} · 失败：{counts.get('failed', 0)}\n"
            f"下载目录：{self.engine._rel(self.engine.current_dir)}"
        )

    async def _tasks_text(self):
        pending = await self.services.store.pending_jobs()
        failed = await self.services.store.failed_jobs()
        lines = [f"📥 待处理 {len(pending)} · 失败 {len(failed)}"]
        for j in pending[:8]:
            lines.append(f"• {j['origin']} · {j.get('title') or j['msg_id']}")
        return "\n".join(lines) if len(lines) > 1 else "🎉 没有待处理的任务。"
