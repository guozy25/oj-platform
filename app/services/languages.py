from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import DEFAULT_ALLOWED_LANGUAGE_EXECUTABLES
from app.core.errors import APIError
from app.db.database import Database
from app.models.judge import LanguageCreate, command_tokens

UNSAFE_EXECUTABLE_OPTIONS = {
    "python": ("-c", "-m"),
    "python3": ("-c", "-m"),
    "pypy3": ("-c", "-m"),
    "node": ("-e", "--eval", "-p", "--print", "-r", "--require", "--import"),
    "ruby": ("-e",),
    "php": ("-r",),
    "gcc": ("-fplugin", "-specs", "-wrapper"),
    "g++": ("-fplugin", "-specs", "-wrapper"),
    "clang": ("-fplugin", "-load"),
    "clang++": ("-fplugin", "-load"),
    "go": ("-toolexec",),
    "java": ("-javaagent", "-agentlib", "-agentpath"),
    "javac": ("-processor", "-processorpath", "--processor-path"),
}


@dataclass(frozen=True, slots=True)
class LanguageConfiguration:
    name: str
    file_ext: str
    compile_cmd: str | None
    run_cmd: str
    time_limit: float | None
    memory_limit: int | None


class LanguageService:
    def __init__(
        self,
        database: Database,
        allowed_executables: Iterable[str] = DEFAULT_ALLOWED_LANGUAGE_EXECUTABLES,
    ) -> None:
        self.database = database
        self.allowed_executables = frozenset(allowed_executables)

    def _validate_command(self, command: str, *, allow_generated_executable: bool) -> None:
        try:
            tokens = command_tokens(command)
        except ValueError as exc:
            raise APIError(400, "unsafe language command") from exc

        executable = tokens[0]
        if executable == "{exe}":
            if not allow_generated_executable:
                raise APIError(400, "unsafe language command")
            return
        if executable not in self.allowed_executables:
            raise APIError(400, "language executable is not allowed")

        unsafe_options = UNSAFE_EXECUTABLE_OPTIONS.get(executable, ())
        for argument in tokens[1:]:
            if any(argument.startswith(option) for option in unsafe_options):
                raise APIError(400, "unsafe language command option")

    def _validate_configuration(
        self, compile_cmd: str | None, run_cmd: str
    ) -> None:
        if compile_cmd is not None:
            self._validate_command(compile_cmd, allow_generated_executable=False)
        self._validate_command(run_cmd, allow_generated_executable=compile_cmd is not None)

    async def register(self, language: LanguageCreate, created_by: str) -> dict[str, str]:
        self._validate_configuration(language.compile_cmd, language.run_cmd)
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

    async def get_for_execution(self, name: str) -> LanguageConfiguration:
        configuration = await self.get(name)
        self._validate_configuration(configuration.compile_cmd, configuration.run_cmd)
        return configuration

    async def list_names(self) -> dict[str, list[str]]:
        rows = await self.database.fetch_all("SELECT name FROM languages ORDER BY name")
        return {"name": [row["name"] for row in rows]}
