from __future__ import annotations

import json
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

    async def get_submission(
        self, submission_id: str, current_user_id: str, current_role: str
    ) -> dict:
        row = await self.database.fetch_one(
            """
            SELECT submission_id, user_id, status, score, counts,
                   compile_info, run_info, error_info
            FROM submissions
            WHERE submission_id = ?
            """,
            (submission_id,),
        )
        if row is None:
            raise APIError(404, "submission not found")
        if current_role != "admin" and row["user_id"] != current_user_id:
            raise APIError(403, "permission denied")
        return self._detail(row)

    async def list_submissions(
        self,
        *,
        user_id: str | None,
        problem_id: str | None,
        status: str | None,
        page: int | None,
        page_size: int | None,
    ) -> dict:
        conditions: list[str] = []
        parameters: list[str | int] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            parameters.append(user_id)
        if problem_id is not None:
            conditions.append("problem_id = ?")
            parameters.append(problem_id)
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)

        where_clause = " WHERE " + " AND ".join(conditions)
        total_row = await self.database.fetch_one(
            "SELECT COUNT(*) AS total FROM submissions" + where_clause,
            parameters,
        )
        query = (
            "SELECT submission_id, status, score, counts "
            "FROM submissions" + where_clause + " ORDER BY created_at DESC, submission_id DESC"
        )
        query_parameters = list(parameters)
        if page_size is not None:
            effective_page = page or 1
            query += " LIMIT ? OFFSET ?"
            query_parameters.extend([page_size, (effective_page - 1) * page_size])

        rows = await self.database.fetch_all(query, query_parameters)
        return {
            "total": total_row["total"],
            "submissions": [self._summary(row) for row in rows],
        }

    async def prepare_rejudge(self, submission_id: str) -> dict[str, str]:
        existing = await self.database.fetch_one(
            "SELECT submission_id FROM submissions WHERE submission_id = ?",
            (submission_id,),
        )
        if existing is None:
            raise APIError(404, "submission not found")

        now = datetime.now(timezone.utc).isoformat()
        async with self.database.connection() as connection:
            await connection.execute(
                "DELETE FROM testcase_results WHERE submission_id = ?", (submission_id,)
            )
            await connection.execute(
                """
                UPDATE submissions
                SET status = 'pending', score = NULL, counts = NULL,
                    compile_info = NULL, run_info = NULL, error_info = NULL,
                    updated_at = ?
                WHERE submission_id = ?
                """,
                (now, submission_id),
            )
            await connection.commit()
        return {"submission_id": submission_id, "status": "pending"}

    @staticmethod
    def _summary(row) -> dict:
        data = {"submission_id": row["submission_id"], "status": row["status"]}
        if row["status"] == "success":
            data.update({"score": row["score"], "counts": row["counts"]})
        return data

    @staticmethod
    def _detail(row) -> dict:
        return {
            "submission_id": row["submission_id"],
            "status": row["status"],
            "score": row["score"],
            "counts": row["counts"],
            "compile_info": json.loads(row["compile_info"])
            if row["compile_info"] is not None
            else None,
            "run_info": json.loads(row["run_info"]) if row["run_info"] is not None else None,
            "error_info": row["error_info"],
        }
