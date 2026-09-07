from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from app.core.errors import APIError
from app.models.problems import PROBLEM_ID_PATTERN, ProblemModel


def _json_text(problem: ProblemModel) -> str:
    return json.dumps(problem.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def _write_temporary_file(path: Path, content: str) -> Path:
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def _create_sync(path: Path, content: str) -> None:
    temporary_path = _write_temporary_file(path, content)
    try:
        # A hard link creates the destination only if it does not already exist.
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _update_sync(path: Path, content: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    temporary_path = _write_temporary_file(path, content)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_sync(path: Path) -> object:
    with path.open(encoding="utf-8") as problem_file:
        return json.load(problem_file)


class ProblemRepository:
    """Asynchronous facade over one-JSON-file-per-problem storage."""

    def __init__(self, problems_dir: Path) -> None:
        self.problems_dir = problems_dir

    def _path_for(self, problem_id: str) -> Path:
        # IDs reaching the repository normally came from ProblemModel. Path parameters
        # still need an explicit boundary check before touching the filesystem.
        if len(problem_id) > 64 or re.fullmatch(PROBLEM_ID_PATTERN, problem_id) is None:
            raise APIError(400, "invalid problem id")
        return self.problems_dir / f"{problem_id}.json"

    async def list(self) -> list[ProblemModel]:
        paths = await asyncio.to_thread(
            lambda: sorted(self.problems_dir.glob("*.json"), key=lambda path: path.name)
        )
        problems: list[ProblemModel] = []
        for path in paths:
            problem = await self._read_path(path)
            if path.stem != problem.id:
                raise APIError(500, "stored problem configuration is invalid")
            problems.append(problem)
        return problems

    async def get(self, problem_id: str) -> ProblemModel:
        return await self._read_path(self._path_for(problem_id))

    async def create(self, problem: ProblemModel) -> None:
        path = self._path_for(problem.id)
        try:
            await asyncio.to_thread(_create_sync, path, _json_text(problem))
        except FileExistsError as exc:
            raise APIError(409, "problem id already exists") from exc
        except OSError as exc:
            raise APIError(500, "failed to store problem") from exc

    async def update(self, problem_id: str, problem: ProblemModel) -> None:
        path = self._path_for(problem_id)
        try:
            await asyncio.to_thread(_update_sync, path, _json_text(problem))
        except FileNotFoundError as exc:
            raise APIError(404, "problem not found") from exc
        except OSError as exc:
            raise APIError(500, "failed to store problem") from exc

    async def delete(self, problem_id: str) -> None:
        path = self._path_for(problem_id)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError as exc:
            raise APIError(404, "problem not found") from exc
        except OSError as exc:
            raise APIError(500, "failed to delete problem") from exc

    async def _read_path(self, path: Path) -> ProblemModel:
        try:
            payload = await asyncio.to_thread(_read_sync, path)
            return ProblemModel.model_validate(payload)
        except FileNotFoundError as exc:
            raise APIError(404, "problem not found") from exc
        except (json.JSONDecodeError, ValidationError, OSError, TypeError) as exc:
            raise APIError(500, "stored problem configuration is invalid") from exc
