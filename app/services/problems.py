from app.core.errors import APIError
from app.models.problems import ProblemModel
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

    async def create_problem(self, problem: ProblemModel) -> dict[str, str]:
        await self.repository.create(problem)
        return {"id": problem.id}

    async def update_problem(self, problem_id: str, problem: ProblemModel) -> dict[str, str]:
        if problem_id != problem.id:
            raise APIError(400, "problem id does not match request path")
        await self.repository.update(problem_id, problem)
        return {"id": problem.id}

    async def delete_problem(self, problem_id: str) -> dict[str, str]:
        await self.repository.delete(problem_id)
        return {"id": problem_id}
