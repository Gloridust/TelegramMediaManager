"""The download engine.

Ported from the original bot's proven logic (byte-level resume, portable paths,
persistent jobs, channel cursors) but decoupled from any UI: instead of editing
Telegram messages it updates the store and publishes events on the bus, so the
web panel and the bot both observe the same progress.
"""

import asyncio
import mimetypes
import os
import re
import time

from telethon import errors, utils

from app.config import Keys, Paths

PART_SUFFIX = ".part"
CHUNK_ALIGN = 4096          # telethon only fast-paths downloads from a 4K-aligned offset
REQUEST_SIZE = 512 * 1024
MAX_WORKERS = 8


class DownloadEngine:
    def __init__(self, store, tg, bus):
        self.store = store
        self.tg = tg
        self.bus = bus

        self.root_path = Paths.DOWNLOADS_DIR
        self.current_dir = self.root_path

        self.download_queue: asyncio.Queue = asyncio.Queue()
        self.worker_tasks: list[asyncio.Task] = []
        self.channel_task: asyncio.Task | None = None
        self.max_concurrent = 2

        self._fs_lock = asyncio.Lock()
        self._resume_lock = asyncio.Lock()
        self._active_paths: set[str] = set()
        self._active_count = 0
        self._root_missing = None

    @property
    def user_client(self):
        return self.tg.user_client

    # ================================================================== #
    # Lifecycle
    # ================================================================== #
    async def start(self):
        Paths.ensure()
        await self._load_settings()
        os.makedirs(self.root_path, exist_ok=True)
        self._spawn_workers(self.max_concurrent)

    async def stop(self):
        for t in self.worker_tasks:
            t.cancel()
        if self.channel_task and not self.channel_task.done():
            self.channel_task.cancel()
        pending = [t for t in self.worker_tasks + [self.channel_task] if t]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _load_settings(self):
        root = await self.store.get_setting(Keys.ROOT_PATH)
        if root and os.path.isdir(root):
            self.root_path = os.path.abspath(root)
        elif root:
            self._root_missing = root
            print(f"WARNING: saved root {root} is gone; falling back to {self.root_path}")
        self.current_dir = self.root_path
        cur = await self.store.get_setting(Keys.CURRENT_DIR)
        if cur and os.path.isdir(cur) and self._is_within_root(cur):
            self.current_dir = os.path.abspath(cur)

        conc = await self.store.get_setting(Keys.MAX_CONCURRENT, "2")
        try:
            self.max_concurrent = max(1, min(MAX_WORKERS, int(conc)))
        except (ValueError, TypeError):
            self.max_concurrent = 2

        await self._migrate_folders()

    # ================================================================== #
    # Worker pool
    # ================================================================== #
    def _spawn_workers(self, n):
        for i in range(len(self.worker_tasks), n):
            self.worker_tasks.append(asyncio.create_task(self.download_worker(i)))

    async def set_concurrency(self, n):
        """Adjust concurrency live. Raising spawns workers immediately; lowering
        enqueues poison pills so surplus workers retire when idle — never
        interrupting a download in progress."""
        n = max(1, min(MAX_WORKERS, n))
        self.max_concurrent = n
        await self.store.set_setting(Keys.MAX_CONCURRENT, n)
        alive = [t for t in self.worker_tasks if not t.done()]
        self.worker_tasks = alive
        if n > len(alive):
            self._spawn_workers(n)
        elif n < len(alive):
            for _ in range(len(alive) - n):
                await self.download_queue.put(None)
        return n

    async def download_worker(self, worker_id):
        while True:
            item = await self.download_queue.get()
            if item is None:  # poison pill: concurrency lowered
                self.download_queue.task_done()
                return
            message, use_user, folder, job_id, title = item
            self._active_count += 1
            try:
                client = self.user_client if use_user else self.tg.bot_client
                folder = folder or self.current_dir
                disp = title or self._display_name(message)
                self._emit("job_start", job_id=job_id, name=disp, folder=self._rel(folder))
                cb = self._progress_cb(job_id, disp)
                out, skipped = await self._download_with_floodwait(client, message, folder, cb)
                await self.store.mark_job(job_id, "skipped" if skipped else "done",
                                          filename=os.path.basename(out))
                self._emit("job_done", job_id=job_id, name=os.path.basename(out),
                           skipped=skipped, folder=self._rel(folder))
            except asyncio.CancelledError:
                raise  # process exiting: stays pending, resumes on restart
            except Exception as e:
                print(f"Worker {worker_id} error: {e}")
                await self.store.mark_job(job_id, "failed", error=str(e))
                self._emit("job_failed", job_id=job_id, error=str(e))
            finally:
                self._active_count -= 1
                self.download_queue.task_done()

    def _progress_cb(self, job_id, disp):
        st = {"last": 0.0, "last_t": time.time(), "last_b": 0}

        async def cb(current, total):
            now = time.time()
            if now - st["last"] < 1.0 and current != total:
                return
            dt = now - st["last_t"]
            speed = (current - st["last_b"]) / dt if dt > 0 else 0
            st["last"] = now
            if current != total:
                st["last_t"], st["last_b"] = now, current
            await self.store.update_progress(job_id, current, total or current)
            self._emit("job_progress", job_id=job_id, name=disp,
                       downloaded=current, total=total or current, speed=max(0, speed))

        return cb

    def _emit(self, kind, **data):
        self.bus.publish({"type": kind, "ts": time.time(), **data})

    # ================================================================== #
    # Enqueue API (called by web + bot)
    # ================================================================== #
    async def _enqueue(self, message, use_user, folder, origin="link", job_id=None, title=None):
        if job_id is None:
            job_id = await self.store.add_job(
                getattr(message, "chat_id", None) or 0, message.id,
                self._store_folder(folder), use_user=use_user, origin=origin, title=title,
            )
        await self.download_queue.put((message, use_user, folder, job_id, title))
        return job_id

    async def download_link(self, link, folder=None):
        """Resolve a t.me link, enqueue its media (single or album). Returns a
        summary dict for the caller to report."""
        if not await self.tg.is_authorized():
            return {"ok": False, "error": "用户端尚未登录"}
        folder = folder or self.current_dir
        entity_ref, msg_id = self._parse_link(link)
        if entity_ref is None or msg_id is None:
            return {"ok": False, "error": "无法解析有效的消息链接"}
        try:
            entity = await self._resolve_entity(entity_ref)
        except Exception as e:
            return {"ok": False, "error": f"无法访问该会话：{e}"}
        try:
            message = await self.user_client.get_messages(entity, ids=msg_id)
        except Exception as e:
            return {"ok": False, "error": f"获取消息失败：{e}"}
        if not message:
            return {"ok": False, "error": "未找到该消息"}

        if message.grouped_id:
            album = await self._fetch_album(entity, message)
            items = [m for m in album if m and m.media]
            if items:
                for m in items:
                    await self._enqueue(m, True, folder, origin="album",
                                        title=self._display_name(m))
                return {"ok": True, "count": len(items), "album": True,
                        "folder": self._rel(folder)}
        if message.media:
            await self._enqueue(message, True, folder, origin="link",
                                title=self._display_name(message))
            return {"ok": True, "count": 1, "album": False, "folder": self._rel(folder)}
        return {"ok": False, "error": "该消息没有可下载的媒体"}

    async def download_message(self, message, use_user=False, folder=None):
        """Enqueue a message object directly (e.g. a forward to the bot)."""
        folder = folder or self.current_dir
        return await self._enqueue(message, use_user, folder, origin="forward",
                                   title=self._display_name(message))

    # ================================================================== #
    # Channel bulk download
    # ================================================================== #
    async def start_channel(self, link, folder=None):
        if self.channel_task and not self.channel_task.done():
            return {"ok": False, "error": "已有频道下载在进行中，请先取消"}
        if not await self.tg.is_authorized():
            return {"ok": False, "error": "用户端尚未登录"}
        entity_ref, _ = self._parse_link(link)
        if entity_ref is None:
            return {"ok": False, "error": "无法解析有效的频道链接"}
        try:
            chat = await self._resolve_entity(entity_ref)
        except Exception as e:
            return {"ok": False, "error": f"无法访问该频道：{e}"}
        base = folder or self.current_dir
        title = self._sanitize_filename(getattr(chat, "title", None) or str(entity_ref))
        folder = os.path.join(base, title)
        os.makedirs(folder, exist_ok=True)
        ch_job = await self.store.start_channel(utils.get_peer_id(chat), title,
                                                self._store_folder(folder))
        self.channel_task = asyncio.create_task(
            self._run_channel_download(chat, title, folder, ch_job))
        return {"ok": True, "title": title, "folder": self._rel(folder)}

    async def _run_channel_download(self, chat, title, folder, ch_job=None):
        sem = asyncio.Semaphore(self.max_concurrent)
        stats = {"done": 0, "skipped": 0, "failed": 0, "media": 0}
        tasks = []
        job_row_id = ch_job["id"] if ch_job else None
        cursor = int(ch_job["cursor"]) if ch_job and ch_job["cursor"] else 0
        seen_base = int(ch_job["seen"]) if ch_job and ch_job["seen"] else 0
        last_emit = 0.0

        async def handle(message, job_id):
            async with sem:
                try:
                    out, skipped = await self._download_with_floodwait(self.user_client, message, folder)
                    stats["skipped" if skipped else "done"] += 1
                    await self.store.mark_job(job_id, "skipped" if skipped else "done",
                                              filename=os.path.basename(out))
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    stats["failed"] += 1
                    await self.store.mark_job(job_id, "failed", error=str(ex))

        try:
            self._emit("channel_start", title=title, folder=self._rel(folder))
            kwargs = {"offset_id": cursor} if cursor else {}
            async for message in self.user_client.iter_messages(chat, **kwargs):
                if not message.media:
                    cursor = message.id  # advance past non-media too
                    continue
                stats["media"] += 1
                job_id = await self.store.add_job(utils.get_peer_id(chat), message.id,
                                                  self._store_folder(folder),
                                                  use_user=True, origin="channel",
                                                  title=self._display_name(message))
                tasks.append(asyncio.create_task(handle(message, job_id)))
                cursor = message.id
                if len(tasks) >= self.max_concurrent * 4:
                    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    tasks = list(pending)
                now = time.time()
                if now - last_emit > 2:
                    last_emit = now
                    await self.store.update_channel(job_row_id, cursor=cursor,
                                                    seen=seen_base + stats["media"])
                    self._emit("channel_progress", title=title, **stats)
            if tasks:
                await asyncio.gather(*tasks)
            await self.store.update_channel(job_row_id, cursor=cursor,
                                            seen=seen_base + stats["media"], status="done")
            self._emit("channel_done", title=title, **stats)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.store.update_channel(job_row_id, cursor=cursor,
                                            seen=seen_base + stats["media"], status="cancelled")
            self._emit("channel_cancelled", title=title, **stats)
            raise
        except Exception as e:
            await self.store.update_channel(job_row_id, cursor=cursor,
                                            seen=seen_base + stats["media"])
            self._emit("channel_error", title=title, error=str(e))
        finally:
            self.channel_task = None

    # ================================================================== #
    # Cancellation & resume
    # ================================================================== #
    async def cancel_all(self):
        cancelled_channel = bool(self.channel_task and not self.channel_task.done())
        if cancelled_channel:
            self.channel_task.cancel()
        drained, pills = 0, []
        while True:
            try:
                item = self.download_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.download_queue.task_done()
            if item is None:
                pills.append(item)
                continue
            await self.store.mark_job(item[3], "cancelled")
            drained += 1
        for pill in pills:
            await self.download_queue.put(pill)
        self._emit("cancelled", drained=drained, channel=cancelled_channel)
        return {"ok": True, "drained": drained, "channel": cancelled_channel}

    async def resume(self):
        """Re-enqueue persisted pending jobs and continue channel scans.

        Serialized so overlapping triggers (startup + post-login) can't
        double-enqueue the same jobs."""
        async with self._resume_lock:
            return await self._resume()

    async def _resume(self):
        jobs = await self.store.pending_jobs()
        channels = await self.store.running_channels()
        if not jobs and not channels:
            return {"ok": True, "queued": 0, "lost": 0, "channel": None}

        groups = {}
        for j in jobs:
            groups.setdefault((j["chat_id"], j["folder"], j["use_user"]), []).append(j)

        queued, lost = 0, 0
        for (cid, stored_folder, use_user), items in groups.items():
            folder = self._resolve_folder(stored_folder)
            client = self.user_client if use_user else self.tg.bot_client
            if not client:
                continue
            try:
                entity = await self._resolve_entity(cid) if use_user else cid
                msgs = await client.get_messages(entity, ids=[j["msg_id"] for j in items])
            except Exception as e:
                for j in items:
                    await self.store.mark_job(j["id"], "failed", error=f"恢复失败：{e}")
                lost += len(items)
                continue
            for j, m in zip(items, msgs):
                if m and m.media:
                    await self._enqueue(m, bool(use_user), folder, origin=j["origin"],
                                        job_id=j["id"], title=j.get("title"))
                    queued += 1
                else:
                    await self.store.mark_job(j["id"], "failed", error="消息已不存在或无媒体")
                    lost += 1

        resumed = None
        if channels and not (self.channel_task and not self.channel_task.done()):
            ch = channels[0]
            try:
                chat = await self._resolve_entity(ch["entity_id"])
                self.channel_task = asyncio.create_task(
                    self._run_channel_download(chat, ch["title"],
                                               self._resolve_folder(ch["folder"]), ch))
                resumed = ch["title"]
            except Exception:
                pass
        self._emit("resumed", queued=queued, lost=lost, channel=resumed)
        return {"ok": True, "queued": queued, "lost": lost, "channel": resumed}

    # ================================================================== #
    # Download primitive (resumable)
    # ================================================================== #
    async def _download_with_floodwait(self, client, message, folder, callback=None):
        try:
            return await self._safe_download(client, message, folder, callback)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            return await self._safe_download(client, message, folder, callback)

    async def _safe_download(self, client, message, folder, callback=None, skip_existing=True):
        """Deterministic naming + idempotent skip + byte-level resume.

        Data streams into ``<name>.part`` and is renamed atomically on completion.
        An interrupted ``.part`` is deliberately kept and resumed from its size,
        aligned down to 4096 (telethon only fast-paths from an aligned offset, and
        the trailing chunk may be a half-written write)."""
        os.makedirs(folder, exist_ok=True)
        name = self._build_filename(message)
        filepath = os.path.join(folder, name)
        part = filepath + PART_SUFFIX

        if skip_existing and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return filepath, True

        async with self._fs_lock:
            if skip_existing and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return filepath, True
            if filepath in self._active_paths:
                return filepath, True
            self._active_paths.add(filepath)

        try:
            total = getattr(getattr(message, "file", None), "size", None) or 0
            offset = 0
            if os.path.exists(part):
                size = os.path.getsize(part)
                offset = size - (size % CHUNK_ALIGN)
                if offset != size:
                    with open(part, "r+b") as fh:
                        fh.truncate(offset)

            if total and offset >= total:
                os.replace(part, filepath)
                return filepath, False

            if offset and callback:
                await callback(offset, total or offset)

            written = offset
            with open(part, "ab") as fh:
                async for chunk in client.iter_download(message, offset=offset,
                                                        request_size=REQUEST_SIZE):
                    fh.write(chunk)
                    written += len(chunk)
                    if callback:
                        await callback(min(written, total) if total else written, total or written)

            os.replace(part, filepath)
            return filepath, False
        finally:
            async with self._fs_lock:
                self._active_paths.discard(filepath)

    # ================================================================== #
    # Link parsing & resolution
    # ================================================================== #
    def _parse_link(self, link):
        link = link.strip().split("?", 1)[0].rstrip("/")
        if "t.me/" not in link:
            return None, None
        tail = link.split("t.me/", 1)[1]
        parts = [p for p in tail.split("/") if p]
        if not parts:
            return None, None
        if parts[0] == "c":
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

    # ================================================================== #
    # Filenames & paths
    # ================================================================== #
    def _sanitize_filename(self, name):
        name = os.path.basename(str(name))
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().strip(".")
        return name or "file"

    def _safe_subdir_name(self, name):
        name = name.strip()
        if not name or "/" in name or "\\" in name:
            return None
        cleaned = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip().strip(".")
        if cleaned in ("", ".", ".."):
            return None
        return cleaned

    def _display_name(self, message):
        f = getattr(message, "file", None)
        if f and f.name:
            return self._sanitize_filename(f.name)
        ext = (f.ext if f and f.ext else "") or ""
        return f"media_{message.id}{ext}"

    def _build_filename(self, message):
        base = None
        f = getattr(message, "file", None)
        if f and f.name:
            base = f.name
        if not base:
            ext = (f.ext if f and f.ext else "") or ""
            if not ext:
                try:
                    ext = utils.get_extension(message.media) or ""
                except Exception:
                    ext = ""
            if not ext and f and getattr(f, "mime_type", None):
                ext = mimetypes.guess_extension(f.mime_type) or ""
            base = f"media_{message.id}{ext}"
        base = self._sanitize_filename(base)
        chat_id = getattr(message, "chat_id", None) or 0
        return f"{chat_id}_{message.id}_{base}"

    # --- portable path helpers (stored root-relative, POSIX separators) --- #
    def _store_folder(self, folder):
        try:
            rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(self.root_path))
        except ValueError:
            return os.path.abspath(folder)
        if rel.startswith(".."):
            return os.path.abspath(folder)
        return "." if rel == "." else rel.replace(os.sep, "/")

    def _resolve_folder(self, stored):
        if os.path.isabs(stored):
            return stored
        if stored in (".", ""):
            return self.root_path
        return os.path.join(self.root_path, *stored.split("/"))

    async def _migrate_folders(self):
        if await self.store.get_setting(Keys.FOLDERS_RELATIVE) == "1":
            return
        for old in await self.store.all_folders():
            if not os.path.isabs(old):
                continue
            new = self._store_folder(old)
            if new != old:
                await self.store.rewrite_folder(old, new)
        await self.store.set_setting(Keys.FOLDERS_RELATIVE, "1")

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
            return "/"
        if p.startswith(root + os.sep):
            return "/" + os.path.relpath(p, root).replace(os.sep, "/")
        return path.replace(os.sep, "/")

    # ================================================================== #
    # Folder management (used by the web file browser)
    # ================================================================== #
    async def set_current_dir(self, path):
        target = os.path.realpath(path)
        if not self._is_within_root(target) or not os.path.isdir(target):
            return False
        self.current_dir = target
        await self.store.set_setting(Keys.CURRENT_DIR, target)
        return True

    async def set_root(self, path):
        new_path = os.path.abspath(path)
        os.makedirs(new_path, exist_ok=True)
        self.root_path = new_path
        self.current_dir = new_path
        await self.store.set_setting(Keys.ROOT_PATH, new_path)
        await self.store.set_setting(Keys.CURRENT_DIR, new_path)

    def list_dir(self, path):
        """List immediate subfolders of a directory (browser view)."""
        target = os.path.realpath(path)
        if not self._is_within_root(target) or not os.path.isdir(target):
            target = self.root_path
        try:
            names = sorted(d for d in os.listdir(target)
                           if os.path.isdir(os.path.join(target, d)))
        except OSError:
            names = []
        return target, names

    def make_dir(self, parent, name):
        safe = self._safe_subdir_name(name)
        if not safe:
            return None
        path = os.path.join(parent, safe)
        if not self._is_within_root(path):
            return None
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def active_count(self):
        return self._active_count

    @property
    def queue_size(self):
        return self.download_queue.qsize()

    @property
    def root_missing(self):
        return self._root_missing
