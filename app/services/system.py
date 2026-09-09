from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI


def _delete_problem_files(problems_dir: Path) -> None:
    for path in problems_dir.glob("*.json"):
        path.unlink()


class SystemResetService:
    """Coordinate cleanup of persistent data and in-process task state."""

    def __init__(self, application: FastAPI) -> None:
        self.application = application

    async def reset(self) -> None:
        async with self.application.state.system_reset_lock:
            tasks = list(self.application.state.background_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            self.application.state.background_tasks.clear()
            self.application.state.judge_tasks.clear()
            self.application.state.ai_task_handles.clear()

            await asyncio.to_thread(
                _delete_problem_files, self.application.state.settings.problems_dir
            )
            await self.application.state.database.reset()
            self.application.state.ai_model_configs.clear()
