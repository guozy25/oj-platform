from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import APIError
from app.db.database import Database
from app.models.auth import CurrentUser
from app.services.passwords import verify_password


class AuthenticationService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def login(self, username: str, password: str) -> CurrentUser:
        row = await self.database.fetch_one(
            """
            SELECT user_id, username, password_hash, role
            FROM users
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        )
        password_matches = row is not None and await asyncio.to_thread(
            verify_password, password, row["password_hash"]
        )
        if not password_matches:
            raise APIError(401, "invalid username or password")
        if row["role"] == "banned":
            raise APIError(403, "user is banned")

        session_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(seconds=self.settings.session_ttl_seconds)
        await self.database.execute(
            """
            INSERT INTO sessions (session_id, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, row["user_id"], created_at.isoformat(), expires_at.isoformat()),
        )
        return CurrentUser(
            user_id=row["user_id"],
            username=row["username"],
            role=row["role"],
            session_id=session_id,
        )

    async def get_current_user(self, session_id: str) -> CurrentUser:
        now = datetime.now(timezone.utc).isoformat()
        row = await self.database.fetch_one(
            """
            SELECT u.user_id, u.username, u.role
            FROM sessions AS s
            JOIN users AS u ON u.user_id = s.user_id
            WHERE s.session_id = ? AND s.expires_at > ?
            """,
            (session_id, now),
        )
        if row is None:
            await self.database.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            raise APIError(401, "not logged in")
        if row["role"] == "banned":
            raise APIError(403, "user is banned")
        return CurrentUser(
            user_id=row["user_id"],
            username=row["username"],
            role=row["role"],
            session_id=session_id,
        )

    async def logout(self, session_id: str) -> None:
        await self.database.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
