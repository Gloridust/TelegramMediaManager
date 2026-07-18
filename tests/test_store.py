"""Persistence: jobs, channel cursors, settings and the admin account survive a
'restart' (a fresh Store over the same file)."""

import asyncio
import os
import shutil
import tempfile

from app.core.security import hash_password, verify_password
from app.core.store import Store


def test_store_persistence():
    async def run():
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "state.db")
        try:
            s = Store(db)
            a = await s.add_job(-100, 1, "/dl", origin="link")
            await s.add_job(-100, 2, "/dl", origin="link")
            c = await s.add_job(-100, 3, "/dl", origin="channel")
            await s.mark_job(a, "done", filename="a.mp4")
            await s.mark_job(c, "failed", error="boom")

            # Re-adding a finished job must not resurrect it.
            assert await s.add_job(-100, 1, "/dl") == a
            assert (await s.counts()).get("done") == 1

            ch = await s.start_channel(-100, "News", "/dl/News")
            await s.update_channel(ch["id"], cursor=555, seen=12)

            # Admin account + password hashing.
            uid = await s.create_user("admin", hash_password("supersecret1"))
            assert await s.has_admin()
            s.close()

            # ---- restart ----
            s2 = Store(db)
            pending = await s2.pending_jobs()
            assert [j["msg_id"] for j in pending] == [2]
            assert [j["msg_id"] for j in await s2.failed_jobs()] == [3]
            chans = await s2.running_channels()
            assert len(chans) == 1 and chans[0]["cursor"] == 555

            user = await s2.get_user("admin")
            assert user["id"] == uid
            assert verify_password("supersecret1", user["password_hash"])
            assert not verify_password("wrong", user["password_hash"])

            # retry-failed requeues.
            assert await s2.requeue_failed() == 1
            assert sorted(j["msg_id"] for j in await s2.pending_jobs()) == [2, 3]

            # Web sessions expire / delete correctly.
            await s2.create_session("tok", uid, 3600)
            assert (await s2.get_session("tok"))["user_id"] == uid
            await s2.delete_session("tok")
            assert await s2.get_session("tok") is None
            s2.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    asyncio.run(run())


def test_portable_paths():
    """A moved download tree resolves to real files, not the stale absolute path."""
    async def run():
        base = tempfile.mkdtemp()
        try:
            from app.core.downloader import DownloadEngine
            old_root = os.path.join(base, "old")
            new_root = os.path.join(base, "new")
            os.makedirs(os.path.join(old_root, "News"), exist_ok=True)

            eng = DownloadEngine.__new__(DownloadEngine)
            eng.root_path = old_root
            stored = eng._store_folder(os.path.join(old_root, "News"))
            assert stored == "News" and "\\" not in stored

            shutil.move(old_root, new_root)
            eng.root_path = new_root
            assert eng._resolve_folder(stored) == os.path.join(new_root, "News")
            assert eng._store_folder(new_root) == "."
        finally:
            shutil.rmtree(base, ignore_errors=True)

    asyncio.run(run())
