"""Telegram bot controller.

Reuses the shared ``DownloadEngine`` so anything done from the bot shows up in the
web panel and vice-versa. Deliberately small: link/forward downloads, channel
downloads, status, cancel, resume. Rich management lives in the panel.
"""

from telethon import Button, TelegramClient, errors, events


class BotController:
    def __init__(self, services):
        self.services = services
        self.engine = services.engine
        self.tg = services.tg
        self.client: TelegramClient | None = None
        self.admin_id = 0
        self._run_task = None

    async def start(self):
        admin = await self.services.store.get_setting("tg_admin_id")
        self.admin_id = int(admin) if admin else 0
        self.client = await self.tg.build_bot_client()
        if not self.client:
            raise RuntimeError("bot token not configured")

        chats = self.admin_id or None
        self.client.add_event_handler(self._on_message, events.NewMessage(chats=chats))
        self.client.add_event_handler(self._on_callback, events.CallbackQuery(chats=chats))

        import asyncio
        self._run_task = asyncio.create_task(self.client.run_until_disconnected())
        if self.admin_id:
            try:
                await self.client.send_message(
                    self.admin_id,
                    "🤖 机器人已连接。Web 面板为主控制台；这里可快速下载。",
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
            [Button.text("📊 状态", resize=True), Button.text("📥 任务", resize=True)],
            [Button.text("▶️ 继续", resize=True), Button.text("🛑 取消", resize=True)],
        ]

    def _admin_only(self, event):
        return not self.admin_id or event.chat_id == self.admin_id

    async def _on_message(self, event):
        if not self._admin_only(event):
            return
        text = (event.message.text or "").strip()
        has_media = event.message.media is not None

        if text == "📊 状态":
            return await event.reply(await self._status_text())
        if text == "📥 任务":
            return await event.reply(await self._tasks_text())
        if text == "▶️ 继续":
            r = await self.engine.resume()
            return await event.reply(f"▶️ 已重新入队 {r['queued']} 个任务。")
        if text == "🛑 取消":
            r = await self.engine.cancel_all()
            return await event.reply(f"🛑 已清除队列 {r['drained']} 项。")

        cmd = text.split()[0].lower() if text else ""
        if cmd == "/start":
            return await event.reply("🎬 发送 t.me 链接下载，或转发媒体到这里。",
                                     buttons=self._keyboard())
        if cmd == "/status":
            return await event.reply(await self._status_text())
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
                n = r["count"]
                await notice.edit(f"📥 已加入 {n} 个媒体到下载队列\n📁 {r['folder']}")
            else:
                await notice.edit(f"❌ {r.get('error')}")
            return

        if has_media:
            await self.engine.download_message(event.message, use_user=False)
            return await event.reply("📥 已加入下载队列。")

    async def _on_callback(self, event):
        try:
            await event.answer()
        except errors.RPCError:
            pass

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
