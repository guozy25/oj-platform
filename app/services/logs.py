from __future__ import annotations

from datetime import datetime, timezone

from app.core.errors import APIError
from app.db.database import Database
from app.models.auth import CurrentUser
from app.repositories.problems import ProblemRepository


class LogService:
    def __init__(self, database: Database, problem_repository: ProblemRepository) -> None:
        self.database = database
        self.problem_repository = problem_repository

    async def get_submission_log(self, submission_id: str, current_user: CurrentUser) -> dict:
        submission = await self.database.fetch_one(
            """
            SELECT submission_id, user_id, problem_id, score, counts
            FROM submissions
            WHERE submission_id = ?
            """,
            (submission_id,),
        )
        if submission is None:
            raise APIError(404, "submission not found")

        public_cases = await self._has_public_cases(submission["problem_id"])
        is_admin = current_user.role == "admin"
        is_owner = submission["user_id"] == current_user.user_id
        if not (is_admin or is_owner or public_cases):
            await self._record_access(current_user, submission, 403)
            raise APIError(403, "permission denied")

        data = {"score": submission["score"], "counts": submission["counts"]}
        if is_admin or public_cases:
            rows = await self.database.fetch_all(
                """
                SELECT testcase_id, result, time_seconds, memory_mb
                FROM testcase_results
                WHERE submission_id = ?
                ORDER BY testcase_id
                """,
                (submission_id,),
            )
            data["details"] = [
                {
                    "id": row["testcase_id"],
                    "result": row["result"],
                    "time": row["time_seconds"],
                    "memory": row["memory_mb"],
                }
                for row in rows
            ]

        await self._record_access(current_user, submission, 200)
        return data

    async def list_access_logs(
        self,
        *,
        user_id: str | None,
        problem_id: str | None,
        page: int | None,
        page_size: int | None,
    ) -> list[dict[str, str]]:
        conditions: list[str] = []
        parameters: list[str | int] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            parameters.append(user_id)
        if problem_id is not None:
            conditions.append("problem_id = ?")
            parameters.append(problem_id)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            "SELECT user_id, problem_id, action, accessed_at, status "
            "FROM access_logs" + where_clause + " ORDER BY accessed_at DESC, access_id DESC"
        )
        if page_size is not None:
            effective_page = page or 1
            query += " LIMIT ? OFFSET ?"
            parameters.extend([page_size, (effective_page - 1) * page_size])

        rows = await self.database.fetch_all(query, parameters)
        return [
            {
                "user_id": row["user_id"],
                "problem_id": row["problem_id"],
                "action": row["action"],
                "time": row["accessed_at"],
                "status": row["status"],
            }
            for row in rows
        ]

    async def _has_public_cases(self, problem_id: str) -> bool:
        try:
            problem = await self.problem_repository.get(problem_id)
        except APIError:
            # A missing or corrupt problem configuration must never make private
            # testcase details visible.
            return False
        return problem.public_cases

    async def _record_access(self, current_user: CurrentUser, submission, status: int) -> None:
        await self.database.execute(
            """
            INSERT INTO access_logs (
                user_id, problem_id, submission_id, action, accessed_at, status
            ) VALUES (?, ?, ?, 'view_logs', ?, ?)
            """,
            (
                current_user.user_id,
                submission["problem_id"],
                submission["submission_id"],
                datetime.now(timezone.utc).isoformat(),
                str(status),
            ),
        )
