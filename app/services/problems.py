from app.core.errors import APIError
from app.db.database import Database
from app.models.problems import ProblemInput, ProblemModel
from app.repositories.problems import ProblemRepository

RESOURCE_LIMIT_FIELDS = frozenset(
    {"code_length_limit", "time_limit", "memory_limit"}
)


class ProblemService:
    def __init__(self, database: Database, repository: ProblemRepository) -> None:
        self.database = database
        self.repository = repository

    async def list_problems(self) -> list[dict[str, str]]:
        problems = await self.repository.list()
        return [{"id": problem.id, "title": problem.title} for problem in problems]

    async def get_problem(self, problem_id: str) -> dict:
        problem = await self.repository.get(problem_id)
        return problem.to_api_dict()

    @staticmethod
    def _ensure_resource_limit_permission(problem: ProblemInput, role: str) -> None:
        if role != "admin" and RESOURCE_LIMIT_FIELDS & problem.model_fields_set:
            raise APIError(403, "only teachers can set problem resource limits")

    async def create_problem(self, problem: ProblemInput, role: str) -> dict[str, str]:
        self._ensure_resource_limit_permission(problem, role)
        stored_problem = ProblemModel.model_validate(problem.model_dump())
        await self.repository.create(stored_problem)
        return {"id": problem.id}

    async def update_problem(
        self, problem_id: str, problem: ProblemInput, role: str
    ) -> dict[str, str]:
        self._ensure_resource_limit_permission(problem, role)
        if problem_id != problem.id:
            raise APIError(400, "problem id does not match request path")
        existing = await self.repository.get(problem_id)
        problem_data = problem.model_dump()
        if role != "admin":
            for field in RESOURCE_LIMIT_FIELDS:
                problem_data[field] = getattr(existing, field)
        stored_problem = ProblemModel.model_validate(
            {**problem_data, "public_cases": existing.public_cases}
        )
        await self.repository.update(problem_id, stored_problem)
        return {"id": problem.id}

    async def delete_problem(self, problem_id: str) -> dict[str, str]:
        # Confirm that the configuration exists before removing database records.
        # Deleting submissions cascades to testcase results and Step-5 access logs.
        await self.repository.get(problem_id)
        await self.database.execute(
            "DELETE FROM submissions WHERE problem_id = ?",
            (problem_id,),
        )
        await self.repository.delete(problem_id)
        return {"id": problem_id}

    async def update_log_visibility(
        self, problem_id: str, public_cases: bool
    ) -> dict[str, str | bool]:
        problem = await self.repository.get(problem_id)
        updated_problem = problem.model_copy(update={"public_cases": public_cases})
        await self.repository.update(problem_id, updated_problem)
        return {"problem_id": problem_id, "public_cases": public_cases}

    async def get_log_visibility(self, problem_id: str) -> dict[str, str | bool]:
        problem = await self.repository.get(problem_id)
        return {"problem_id": problem_id, "public_cases": problem.public_cases}
