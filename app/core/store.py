"""Crash-safe persistence for the whole application.

Everything that must survive a restart lives here in one SQLite database under the
data volume: download jobs, channel-scan cursors, application settings, the panel
admin account and its web sessions. SQLite (WAL mode) is used over a JSON file
because rows are updated one at a time while downloads are in flight — a
rewrite-the-whole-file approach loses data if the process dies mid-write.

The store is intentionally UI-agnostic: the web API and the Telegram bot both
read and write the same tables, which is what keeps their views consistent.
"""

import asyncio
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    msg_id     INTEGER NOT NULL,
    folder     TEXT    NOT NULL,
    use_user   INTEGER NOT NULL DEFAULT 1,
    origin     TEXT    NOT NULL DEFAULT 'link',
    status     TEXT    NOT NULL DEFAULT 'pending',
    filename   TEXT,
    title      TEXT,
    size       INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at REAL,
    updated_at REAL,
    UNIQUE(chat_id, msg_id, folder)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS channel_jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  INTEGER NOT NULL,
    title      TEXT,
    folder     TEXT    NOT NULL,
    cursor     INTEGER NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'running',
    seen       INTEGER NOT NULL DEFAULT 0,
    created_at REAL,
    updated_at REAL,
    UNIQUE(entity_id, folder)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    REAL,
    last_login    REAL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL,
    expires_at REAL,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON web_sessions(expires_at);
"""


class Store:
    """Async-friendly wrapper around a small SQLite database.

    Writes are tiny and infrequent relative to network I/O, so they run on the
    event-loop thread behind a lock rather than in a thread pool.
    """

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL survives an abrupt kill far better than the default rollback journal.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._lock = asyncio.Lock()

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    # Settings (key/value) + typed config helpers
    # ---------------------------------------------------------------- #
    async def get_setting(self, key, default=None):
        async with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    async def set_setting(self, key, value):
        async with self._lock:
            self._db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self._db.commit()

    async def get_json(self, key, default=None):
        raw = await self.get_setting(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

    async def set_json(self, key, value):
        await self.set_setting(key, json.dumps(value, ensure_ascii=False))

    async def all_settings(self):
        async with self._lock:
            rows = self._db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---------------------------------------------------------------- #
    # Users & web sessions
    # ---------------------------------------------------------------- #
    async def has_admin(self):
        async with self._lock:
            row = self._db.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return row["n"] > 0

    async def create_user(self, username, password_hash):
        async with self._lock:
            cur = self._db.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)",
                (username, password_hash, time.time()),
            )
            self._db.commit()
            return cur.lastrowid

    async def get_user(self, username):
        async with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id):
        async with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    async def set_password(self, user_id, password_hash):
        async with self._lock:
            self._db.execute(
                "UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id)
            )
            self._db.commit()

    async def touch_login(self, user_id):
        async with self._lock:
            self._db.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), user_id))
            self._db.commit()

    async def create_session(self, token, user_id, ttl_seconds, user_agent=""):
        now = time.time()
        async with self._lock:
            self._db.execute(
                "INSERT INTO web_sessions(token, user_id, created_at, expires_at, user_agent) "
                "VALUES(?, ?, ?, ?, ?)",
                (token, user_id, now, now + ttl_seconds, user_agent[:200]),
            )
            self._db.execute("DELETE FROM web_sessions WHERE expires_at < ?", (now,))
            self._db.commit()

    async def get_session(self, token):
        async with self._lock:
            row = self._db.execute(
                "SELECT * FROM web_sessions WHERE token=? AND expires_at > ?", (token, time.time())
            ).fetchone()
        return dict(row) if row else None

    async def delete_session(self, token):
        async with self._lock:
            self._db.execute("DELETE FROM web_sessions WHERE token=?", (token,))
            self._db.commit()

    async def delete_user_sessions(self, user_id):
        async with self._lock:
            self._db.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
            self._db.commit()

    # ---------------------------------------------------------------- #
    # Media jobs
    # ---------------------------------------------------------------- #
    async def add_job(self, chat_id, msg_id, folder, use_user=True, origin="link", title=None):
        """Register a job as pending. Returns its row id.

        A job already marked done/skipped is left untouched so re-sending the
        same link does not resurrect finished work.
        """
        now = time.time()
        async with self._lock:
            self._db.execute(
                "INSERT INTO jobs(chat_id, msg_id, folder, use_user, origin, title, status, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?) "
                "ON CONFLICT(chat_id, msg_id, folder) DO UPDATE SET "
                "  status=CASE WHEN jobs.status IN ('done','skipped') THEN jobs.status ELSE 'pending' END, "
                "  title=COALESCE(excluded.title, jobs.title), "
                "  updated_at=excluded.updated_at",
                (int(chat_id), int(msg_id), folder, 1 if use_user else 0, origin, title, now, now),
            )
            self._db.commit()
            # lastrowid is unreliable when the upsert takes the UPDATE branch.
            row = self._db.execute(
                "SELECT id FROM jobs WHERE chat_id=? AND msg_id=? AND folder=?",
                (int(chat_id), int(msg_id), folder),
            ).fetchone()
            return row["id"] if row else None

    async def mark_job(self, job_id, status, filename=None, error=None):
        if job_id is None:
            return
        async with self._lock:
            self._db.execute(
                "UPDATE jobs SET status=?, filename=COALESCE(?, filename), error=?, updated_at=? WHERE id=?",
                (status, filename, error, time.time(), job_id),
            )
            self._db.commit()

    async def update_progress(self, job_id, downloaded, size):
        if job_id is None:
            return
        async with self._lock:
            self._db.execute(
                "UPDATE jobs SET downloaded=?, size=?, status='running', updated_at=? WHERE id=?",
                (int(downloaded), int(size), time.time(), job_id),
            )
            self._db.commit()

    async def get_job(self, job_id):
        async with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    async def pending_jobs(self):
        async with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE status IN ('pending','running') ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    async def failed_jobs(self):
        async with self._lock:
            rows = self._db.execute("SELECT * FROM jobs WHERE status='failed' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    async def recent_jobs(self, limit=50):
        async with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    async def requeue_failed(self):
        """Flip every failed job back to pending. Returns the count."""
        async with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET status='pending', error=NULL, updated_at=? WHERE status='failed'",
                (time.time(),),
            )
            self._db.commit()
            return cur.rowcount

    async def counts(self):
        async with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    async def clear_unfinished(self):
        """Drop pending/running/failed jobs — used when the admin declines a resume."""
        async with self._lock:
            cur = self._db.execute("DELETE FROM jobs WHERE status IN ('pending','running','failed')")
            self._db.execute("UPDATE channel_jobs SET status='cancelled' WHERE status='running'")
            self._db.commit()
            return cur.rowcount

    async def clear_history(self):
        """Remove finished job rows (keeps unfinished work). Returns the count."""
        async with self._lock:
            cur = self._db.execute("DELETE FROM jobs WHERE status IN ('done','skipped','cancelled')")
            self._db.commit()
            return cur.rowcount

    # ---------------------------------------------------------------- #
    # Channel scans
    # ---------------------------------------------------------------- #
    async def start_channel(self, entity_id, title, folder):
        now = time.time()
        async with self._lock:
            self._db.execute(
                "INSERT INTO channel_jobs(entity_id, title, folder, cursor, status, seen, created_at, updated_at) "
                "VALUES(?, ?, ?, 0, 'running', 0, ?, ?) "
                "ON CONFLICT(entity_id, folder) DO UPDATE SET status='running', title=excluded.title, "
                "  updated_at=excluded.updated_at",
                (int(entity_id), title, folder, now, now),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM channel_jobs WHERE entity_id=? AND folder=?",
                (int(entity_id), folder),
            ).fetchone()
        return dict(row) if row else None

    async def update_channel(self, job_id, cursor=None, seen=None, status=None):
        sets, params = ["updated_at=?"], [time.time()]
        if cursor is not None:
            sets.append("cursor=?")
            params.append(int(cursor))
        if seen is not None:
            sets.append("seen=?")
            params.append(int(seen))
        if status is not None:
            sets.append("status=?")
            params.append(status)
        params.append(job_id)
        async with self._lock:
            self._db.execute(f"UPDATE channel_jobs SET {', '.join(sets)} WHERE id=?", params)
            self._db.commit()

    async def running_channels(self):
        async with self._lock:
            rows = self._db.execute(
                "SELECT * FROM channel_jobs WHERE status='running' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- #
    # Migration helpers (absolute -> root-relative folder paths)
    # ---------------------------------------------------------------- #
    async def all_folders(self):
        async with self._lock:
            rows = self._db.execute(
                "SELECT folder FROM jobs UNION SELECT folder FROM channel_jobs"
            ).fetchall()
        return [r["folder"] for r in rows]

    async def rewrite_folder(self, old, new):
        """Repoint every row from `old` to `new`.

        UPDATE OR REPLACE, not plain UPDATE: rewriting can collide with a row that
        already uses the new value, and a bare UPDATE would abort on the UNIQUE
        constraint. Dropping the older duplicate is the right outcome — both rows
        describe the same message in the same directory.
        """
        if old == new:
            return
        async with self._lock:
            self._db.execute("UPDATE OR REPLACE jobs SET folder=? WHERE folder=?", (new, old))
            self._db.execute("UPDATE OR REPLACE channel_jobs SET folder=? WHERE folder=?", (new, old))
            self._db.commit()
