from app.core.errors import APIError
from app.models.problems import ProblemInput, ProblemModel
from app.repositories.problems import ProblemRepository


class ProblemService:
    def __init__(self, repository: ProblemRepository) -> None:
        self.repository = repository

    async def list_problems(self) -> list[dict[str, str]]:
        problems = await self.repository.list()
        return [{"id": problem.id, "title": problem.title} for problem in problems]

    async def get_problem(self, problem_id: str) -> dict:
        problem = await self.repository.get(problem_id)
        return problem.to_api_dict()

    async def create_problem(self, problem: ProblemInput) -> dict[str, str]:
        stored_problem = ProblemModel.model_validate(problem.model_dump())
        await self.repository.create(stored_problem)
        return {"id": problem.id}

    async def update_problem(self, problem_id: str, problem: ProblemInput) -> dict[str, str]:
        if problem_id != problem.id:
            raise APIError(400, "problem id does not match request path")
        existing = await self.repository.get(problem_id)
        stored_problem = ProblemModel.model_validate(
            {**problem.model_dump(), "public_cases": existing.public_cases}
        )
        await self.repository.update(problem_id, stored_problem)
        return {"id": problem.id}

    async def delete_problem(self, problem_id: str) -> dict[str, str]:
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
