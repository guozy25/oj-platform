from __future__ import annotations

import asyncio

from fastapi import FastAPI

from app.repositories.problems import ProblemRepository
from app.services.judge import JudgeService


async def schedule_judge(
    application: FastAPI,
    submission_id: str,
    repository: ProblemRepository | None = None,
) -> asyncio.Task[None]:
    existing = application.state.judge_tasks.get(submission_id)
    if existing is not None and not existing.done():
        existing.cancel()
        await asyncio.gather(existing, return_exceptions=True)

    problem_repository = repository or ProblemRepository(application.state.settings.problems_dir)
    judge = JudgeService(
        application.state.database,
        problem_repository,
        application.state.settings,
    )
    task = asyncio.create_task(judge.judge_submission(submission_id))
    application.state.judge_tasks[submission_id] = task
    application.state.background_tasks.add(task)

    def task_finished(completed: asyncio.Task[None]) -> None:
        application.state.background_tasks.discard(completed)
        if application.state.judge_tasks.get(submission_id) is completed:
            application.state.judge_tasks.pop(submission_id, None)

    task.add_done_callback(task_finished)
    return task
