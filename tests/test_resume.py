"""Byte-level resume: an interrupted transfer must continue from its offset and
produce a byte-identical file, never re-downloading from zero."""

import asyncio
import os
import tempfile

from app.core.downloader import DownloadEngine, PART_SUFFIX, CHUNK_ALIGN

TOTAL = 300_000
DATA = bytes((i * 7) % 256 for i in range(TOTAL))


class _FakeFile:
    name = "movie.bin"; ext = ".bin"; size = TOTAL; mime_type = "application/octet-stream"


class _FakeMessage:
    id = 42; chat_id = -100123; media = object(); file = _FakeFile(); grouped_id = None


class _FakeClient:
    def __init__(self, die_after=None):
        self.die_after = die_after
        self.requested_offset = None
        self.bytes_sent = 0

    def iter_download(self, message, offset=0, request_size=512 * 1024):
        self.requested_offset = offset
        outer = self

        async def gen():
            pos, sent = offset, 0
            while pos < TOTAL:
                chunk = DATA[pos:pos + 65536]
                if outer.die_after is not None and sent + len(chunk) > outer.die_after:
                    raise ConnectionError("network died")
                yield chunk
                pos += len(chunk); sent += len(chunk); outer.bytes_sent = sent
                await asyncio.sleep(0)
        return gen()


def _engine(root):
    eng = DownloadEngine.__new__(DownloadEngine)
    eng._fs_lock = asyncio.Lock()
    eng._active_paths = set()
    eng.root_path = root
    return eng


def test_resume_from_offset():
    async def run():
        tmp = tempfile.mkdtemp()
        try:
            eng = _engine(tmp)
            msg = _FakeMessage()
            final = os.path.join(tmp, eng._build_filename(msg))
            part = final + PART_SUFFIX

            # Interrupt partway.
            try:
                await eng._safe_download(_FakeClient(die_after=100_000), msg, tmp)
                assert False, "expected interruption"
            except ConnectionError:
                pass
            assert not os.path.exists(final)
            assert os.path.exists(part) and os.path.getsize(part) > 0

            # Resume.
            c2 = _FakeClient()
            out, skipped = await eng._safe_download(c2, msg, tmp)
            assert not skipped
            assert c2.requested_offset > 0
            assert c2.requested_offset % CHUNK_ALIGN == 0
            assert c2.bytes_sent < TOTAL  # only the tail was fetched
            assert os.path.exists(out) and not os.path.exists(part)
            assert open(out, "rb").read() == DATA

            # Completed file is skipped without any network call.
            c3 = _FakeClient()
            _, skipped3 = await eng._safe_download(c3, msg, tmp)
            assert skipped3 and c3.requested_offset is None
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    asyncio.run(run())
