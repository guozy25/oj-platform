from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from app.core.config import Settings
from app.services.passwords import hash_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin', 'banned')),
    join_time TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase
ON users(username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS role_change_logs (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id TEXT NOT NULL REFERENCES users(user_id),
    target_user_id TEXT NOT NULL REFERENCES users(user_id),
    old_role TEXT NOT NULL,
    new_role TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_role_change_logs_actor ON role_change_logs(actor_user_id);
CREATE INDEX IF NOT EXISTS idx_role_change_logs_target ON role_change_logs(target_user_id);

CREATE TABLE IF NOT EXISTS languages (
    name TEXT PRIMARY KEY,
    file_ext TEXT NOT NULL,
    compile_cmd TEXT,
    run_cmd TEXT NOT NULL,
    time_limit REAL,
    memory_limit INTEGER,
    created_by TEXT REFERENCES users(user_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    problem_id TEXT NOT NULL,
    language TEXT NOT NULL REFERENCES languages(name),
    code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'success', 'error')),
    score INTEGER,
    counts INTEGER,
    compile_info TEXT,
    run_info TEXT,
    error_info TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id);
CREATE INDEX IF NOT EXISTS idx_submissions_problem ON submissions(problem_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);

CREATE TABLE IF NOT EXISTS testcase_results (
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
    testcase_id INTEGER NOT NULL,
    result TEXT NOT NULL,
    time_seconds REAL NOT NULL,
    memory_mb REAL NOT NULL,
    PRIMARY KEY (submission_id, testcase_id)
);

CREATE TABLE IF NOT EXISTS access_logs (
    access_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    problem_id TEXT NOT NULL,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
    action TEXT NOT NULL DEFAULT 'view_logs' CHECK (action = 'view_logs'),
    accessed_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_logs_user ON access_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_access_logs_problem ON access_logs(problem_id);

CREATE TABLE IF NOT EXISTS ai_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'completed', 'cancelled', 'failed')
    ),
    requirement TEXT NOT NULL,
    problem_id TEXT,
    progress TEXT,
    result TEXT,
    usage TEXT,
    error_info TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_tasks_user ON ai_tasks(user_id);
"""


class Database:
    """Small async SQLite wrapper shared by repositories and services."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path: Path = settings.database_path

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.settings.ensure_directories)
        async with self.connection() as connection:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.executescript(SCHEMA)
            await connection.commit()
        await self.ensure_initial_admin()
        await self.ensure_default_languages()

    async def ensure_initial_admin(self) -> None:
        existing = await self.fetch_one(
            "SELECT user_id FROM users WHERE username = ?",
            (self.settings.initial_admin_username,),
        )
        if existing is not None:
            return

        now = datetime.now(timezone.utc)
        password_hash = await asyncio.to_thread(hash_password, self.settings.initial_admin_password)
        async with self.connection() as connection:
            await connection.execute(
                """
                INSERT OR IGNORE INTO users (
                    user_id, username, password_hash, role, join_time, created_at
                ) VALUES (?, ?, ?, 'admin', ?, ?)
                """,
                (
                    str(uuid4()),
                    self.settings.initial_admin_username,
                    password_hash,
                    now.date().isoformat(),
                    now.isoformat(),
                ),
            )
            await connection.commit()

    async def ensure_default_languages(self) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        defaults = (
            (
                "python",
                ".py",
                None,
                "python3 {src}",
                self.settings.default_time_limit,
                self.settings.default_memory_limit,
                created_at,
            ),
            (
                "cpp",
                ".cpp",
                "g++ -O2 -std=c++14 {src} -o {exe}",
                "{exe}",
                self.settings.default_time_limit,
                self.settings.default_memory_limit,
                created_at,
            ),
        )
        async with self.connection() as connection:
            await connection.executemany(
                """
                INSERT OR IGNORE INTO languages (
                    name, file_ext, compile_cmd, run_cmd,
                    time_limit, memory_limit, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                defaults,
            )
            await connection.commit()

    async def fetch_one(self, query: str, parameters: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.connection() as connection:
            cursor = await connection.execute(query, tuple(parameters))
            return await cursor.fetchone()

    async def fetch_all(self, query: str, parameters: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.connection() as connection:
            cursor = await connection.execute(query, tuple(parameters))
            return list(await cursor.fetchall())

    async def execute(self, query: str, parameters: Iterable[Any] = ()) -> int:
        async with self.connection() as connection:
            cursor = await connection.execute(query, tuple(parameters))
            await connection.commit()
            return cursor.rowcount
