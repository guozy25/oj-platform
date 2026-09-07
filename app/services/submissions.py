from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.errors import APIError
from app.db.database import Database
from app.models.judge import SubmissionCreate
from app.repositories.problems import ProblemRepository
from app.services.languages import LanguageService


class SubmissionService:
    def __init__(self, database: Database, problem_repository: ProblemRepository) -> None:
        self.database = database
        self.problem_repository = problem_repository

    async def create(self, submission: SubmissionCreate, user_id: str) -> dict[str, str]:
        # The order follows the API contract: parameter validation has already run,
        # then rate limiting precedes resource existence checks.
        since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        recent = await self.database.fetch_one(
            """
            SELECT COUNT(*) AS total
            FROM submissions
            WHERE user_id = ? AND created_at >= ?
            """,
            (user_id, since),
        )
        if recent["total"] >= 3:
            raise APIError(429, "submission rate limit exceeded")

        await self.problem_repository.get(submission.problem_id)
        await LanguageService(self.database).get(submission.language)

        submission_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self.database.execute(
            """
            INSERT INTO submissions (
                submission_id, user_id, problem_id, language, code, status,
                score, counts, compile_info, run_info, error_info, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                submission_id,
                user_id,
                submission.problem_id,
                submission.language,
                submission.code,
                now,
                now,
            ),
        )
        return {"submission_id": submission_id, "status": "pending"}
