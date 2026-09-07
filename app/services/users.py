from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.core.errors import APIError
from app.db.database import Database
from app.services.passwords import hash_password

USER_WITH_COUNTS_QUERY = """
SELECT
    u.user_id,
    u.username,
    u.join_time,
    u.role,
    (
        SELECT COUNT(*)
        FROM submissions AS s
        WHERE s.user_id = u.user_id
    ) AS submit_count,
    (
        SELECT COUNT(DISTINCT s.problem_id)
        FROM submissions AS s
        WHERE s.user_id = u.user_id
          AND s.status = 'success'
          AND s.counts > 0
          AND s.score = s.counts
    ) AS resolve_count
FROM users AS u
"""


def serialize_user(row) -> dict:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "join_time": row["join_time"],
        "role": row["role"],
        "submit_count": row["submit_count"],
        "resolve_count": row["resolve_count"],
    }


class UserService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_user(self, username: str, password: str, role: str = "user") -> dict:
        duplicate = await self.database.fetch_one(
            "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (username,)
        )
        if duplicate is not None:
            raise APIError(400, "username already exists")

        now = datetime.now(timezone.utc)
        user_id = str(uuid4())
        password_hash = await asyncio.to_thread(hash_password, password)
        try:
            await self.database.execute(
                """
                INSERT INTO users (
                    user_id, username, password_hash, role, join_time, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    password_hash,
                    role,
                    now.date().isoformat(),
                    now.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise APIError(400, "username already exists") from exc

        return await self.get_user(user_id)

    async def get_user(self, user_id: str) -> dict:
        row = await self.database.fetch_one(
            USER_WITH_COUNTS_QUERY + " WHERE u.user_id = ?", (user_id,)
        )
        if row is None:
            raise APIError(404, "user not found")
        return serialize_user(row)

    async def update_role(self, user_id: str, role: str, actor_user_id: str) -> dict:
        existing = await self.database.fetch_one(
            "SELECT user_id, role FROM users WHERE user_id = ?", (user_id,)
        )
        if existing is None:
            raise APIError(404, "user not found")

        async with self.database.connection() as connection:
            await connection.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            await connection.execute(
                """
                INSERT INTO role_change_logs (
                    actor_user_id, target_user_id, old_role, new_role, changed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    actor_user_id,
                    user_id,
                    existing["role"],
                    role,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if role == "banned":
                await connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            await connection.commit()
        return {"user_id": user_id, "role": role}

    async def list_users(self, page: int | None, page_size: int | None) -> dict:
        total_row = await self.database.fetch_one("SELECT COUNT(*) AS total FROM users")
        query = USER_WITH_COUNTS_QUERY + " ORDER BY u.created_at, u.user_id"
        parameters: tuple[int, ...] = ()
        if page_size is not None:
            effective_page = page or 1
            query += " LIMIT ? OFFSET ?"
            parameters = (page_size, (effective_page - 1) * page_size)

        rows = await self.database.fetch_all(query, parameters)
        return {
            "total": total_row["total"],
            "users": [serialize_user(row) for row in rows],
        }
