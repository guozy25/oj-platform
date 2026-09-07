from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.errors import APIError
from app.db.database import Database
from app.models.judge import LanguageCreate


@dataclass(frozen=True, slots=True)
class LanguageConfiguration:
    name: str
    file_ext: str
    compile_cmd: str | None
    run_cmd: str
    time_limit: float | None
    memory_limit: int | None


class LanguageService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def register(self, language: LanguageCreate, created_by: str) -> dict[str, str]:
        try:
            await self.database.execute(
                """
                INSERT INTO languages (
                    name, file_ext, compile_cmd, run_cmd,
                    time_limit, memory_limit, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    language.name,
                    language.file_ext,
                    language.compile_cmd,
                    language.run_cmd,
                    language.time_limit,
                    language.memory_limit,
                    created_by,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "language already exists") from exc
        return {"name": language.name}

    async def get(self, name: str) -> LanguageConfiguration:
        row = await self.database.fetch_one(
            """
            SELECT name, file_ext, compile_cmd, run_cmd, time_limit, memory_limit
            FROM languages
            WHERE name = ? COLLATE NOCASE
            """,
            (name,),
        )
        if row is None:
            raise APIError(404, "language not found")
        return LanguageConfiguration(
            name=row["name"],
            file_ext=row["file_ext"],
            compile_cmd=row["compile_cmd"],
            run_cmd=row["run_cmd"],
            time_limit=row["time_limit"],
            memory_limit=row["memory_limit"],
        )

    async def list_names(self) -> dict[str, list[str]]:
        rows = await self.database.fetch_all("SELECT name FROM languages ORDER BY name")
        return {"name": [row["name"] for row in rows]}
