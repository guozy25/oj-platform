from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

import psutil

from app.core.config import Settings
from app.db.database import Database
from app.repositories.problems import ProblemRepository
from app.services.languages import LanguageConfiguration, LanguageService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    peak_memory_mb: float
    timed_out: bool = False
    memory_exceeded: bool = False
    output_exceeded: bool = False


def _linux_memory_limiter(memory_limit_mb: int):
    def apply_limit() -> None:
        import resource

        limit_bytes = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply_limit


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with suppress(ProcessLookupError):
            process.kill()


async def _monitor_memory(
    process: asyncio.subprocess.Process,
    memory_limit_mb: int,
    state: dict[str, float | bool],
) -> None:
    try:
        monitored = psutil.Process(process.pid)
        while process.returncode is None:
            processes = [monitored]
            with suppress(psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                processes.extend(monitored.children(recursive=True))
            total_bytes = 0
            for child in processes:
                with suppress(psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    total_bytes += child.memory_info().rss
            memory_mb = total_bytes / (1024 * 1024)
            state["peak_memory_mb"] = max(float(state["peak_memory_mb"]), memory_mb)
            if memory_mb > memory_limit_mb:
                state["memory_exceeded"] = True
                _kill_process_group(process)
                return
            await asyncio.sleep(0.01)
    except (psutil.Error, PermissionError):
        logger.debug("Memory monitor stopped before process accounting was available")


async def run_process(
    command: list[str],
    *,
    cwd: Path,
    input_text: str,
    timeout_seconds: float,
    memory_limit_mb: int,
    max_output_bytes: int,
) -> ProcessResult:
    preexec_fn = _linux_memory_limiter(memory_limit_mb) if sys.platform == "linux" else None
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        preexec_fn=preexec_fn,
    )
    memory_state: dict[str, float | bool] = {
        "peak_memory_mb": 0.0,
        "memory_exceeded": False,
    }
    monitor_task = asyncio.create_task(_monitor_memory(process, memory_limit_mb, memory_state))
    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(input_text.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        _kill_process_group(process)
        stdout_bytes, stderr_bytes = await process.communicate()
    except asyncio.CancelledError:
        _kill_process_group(process)
        await process.communicate()
        raise
    finally:
        monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await monitor_task

    elapsed = time.perf_counter() - started
    output_exceeded = len(stdout_bytes) > max_output_bytes or len(stderr_bytes) > max_output_bytes
    stdout = stdout_bytes[:max_output_bytes].decode("utf-8", errors="replace")
    stderr = stderr_bytes[:max_output_bytes].decode("utf-8", errors="replace")
    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=elapsed,
        peak_memory_mb=float(memory_state["peak_memory_mb"]),
        timed_out=timed_out,
        memory_exceeded=bool(memory_state["memory_exceeded"]),
        output_exceeded=output_exceeded,
    )


def expand_command(template: str, source_path: Path, executable_path: Path) -> list[str]:
    values = {"src": str(source_path), "exe": str(executable_path)}
    return [token.format(**values) for token in shlex.split(template)]


def normalize_output(output: str) -> str:
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _looks_like_memory_error(stderr: str) -> bool:
    lowered = stderr.lower()
    markers = (
        "memoryerror",
        "std::bad_alloc",
        "cannot allocate memory",
        "failed to allocate",
        "failed to map segment",
        "not enough memory",
        "out of memory",
    )
    return any(marker in lowered for marker in markers)


def _safe_message(message: str, working_directory: Path, limit: int = 4_000) -> str:
    return message.replace(str(working_directory), "<submission>")[:limit]


class JudgeService:
    def __init__(
        self,
        database: Database,
        problem_repository: ProblemRepository,
        settings: Settings,
    ) -> None:
        self.database = database
        self.problem_repository = problem_repository
        self.settings = settings

    async def judge_submission(self, submission_id: str) -> None:
        working_directory: Path | None = None
        try:
            row = await self.database.fetch_one(
                """
                SELECT submission_id, problem_id, language, code
                FROM submissions
                WHERE submission_id = ?
                """,
                (submission_id,),
            )
            if row is None:
                return

            problem = await self.problem_repository.get(row["problem_id"])
            language = await LanguageService(self.database).get(row["language"])
            working_directory = Path(
                await asyncio.to_thread(
                    mkdtemp,
                    prefix=f"submission-{submission_id}-",
                    dir=self.settings.runtime_dir,
                )
            )
            source_path = working_directory / f"Main{language.file_ext}"
            executable_path = working_directory / "program"
            await asyncio.to_thread(source_path.write_text, row["code"], encoding="utf-8")

            await self._evaluate(
                submission_id,
                problem,
                language,
                source_path,
                executable_path,
                working_directory,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Judge task failed for submission %s", submission_id)
            await self._mark_error(submission_id)
        finally:
            if working_directory is not None:
                await asyncio.to_thread(shutil.rmtree, working_directory, True)

    async def _evaluate(
        self,
        submission_id: str,
        problem,
        language: LanguageConfiguration,
        source_path: Path,
        executable_path: Path,
        working_directory: Path,
    ) -> None:
        time_limit = (
            problem.time_limit
            if problem.time_limit is not None
            else language.time_limit
            if language.time_limit is not None
            else self.settings.default_time_limit
        )
        memory_limit = (
            problem.memory_limit
            if problem.memory_limit is not None
            else language.memory_limit
            if language.memory_limit is not None
            else self.settings.default_memory_limit
        )

        compile_info = None
        if language.compile_cmd is not None:
            compile_result = await run_process(
                expand_command(language.compile_cmd, source_path, executable_path),
                cwd=working_directory,
                input_text="",
                timeout_seconds=self.settings.compile_time_limit,
                memory_limit_mb=max(512, memory_limit),
                max_output_bytes=self.settings.max_program_output_bytes,
            )
            compile_message = _safe_message(
                compile_result.stderr or compile_result.stdout, working_directory
            )
            if (
                compile_result.returncode != 0
                or compile_result.timed_out
                or compile_result.memory_exceeded
                or compile_result.output_exceeded
            ):
                if compile_result.timed_out:
                    compile_message = "compilation timed out"
                elif compile_result.memory_exceeded:
                    compile_message = "compilation exceeded memory limit"
                elif compile_result.output_exceeded:
                    compile_message = "compiler output exceeded limit"
                details = [
                    {"id": index, "result": "CE", "time": 0.0, "memory": 0.0}
                    for index in range(1, len(problem.testcases) + 1)
                ]
                await self._store_result(
                    submission_id,
                    score=0,
                    counts=len(problem.testcases) * 10,
                    compile_info={"result": "CE", "message": compile_message},
                    run_info={"result": "finished", "message": "0 test cases finished"},
                    error_info="",
                    details=details,
                )
                return
            compile_info = {"result": "success", "message": compile_message}

        run_command = expand_command(language.run_cmd, source_path, executable_path)
        details = []
        run_messages: list[str] = []
        score = 0
        for index, testcase in enumerate(problem.testcases, start=1):
            result = await run_process(
                run_command,
                cwd=working_directory,
                input_text=testcase.input,
                timeout_seconds=time_limit,
                memory_limit_mb=memory_limit,
                max_output_bytes=self.settings.max_program_output_bytes,
            )
            if result.memory_exceeded or _looks_like_memory_error(result.stderr):
                verdict = "MLE"
                run_messages.append(f"test case {index}: memory limit exceeded")
            elif result.timed_out:
                verdict = "TLE"
                run_messages.append(f"test case {index}: time limit exceeded")
            elif result.output_exceeded:
                verdict = "RE"
                run_messages.append(f"test case {index}: output limit exceeded")
            elif result.returncode != 0:
                verdict = "RE"
                message = _safe_message(result.stderr, working_directory)
                run_messages.append(f"test case {index}: runtime error\n{message}")
            elif normalize_output(result.stdout) == normalize_output(testcase.output):
                verdict = "AC"
                score += 10
            else:
                verdict = "WA"
            details.append(
                {
                    "id": index,
                    "result": verdict,
                    "time": round(result.elapsed_seconds, 6),
                    "memory": round(result.peak_memory_mb, 3),
                }
            )

        await self._store_result(
            submission_id,
            score=score,
            counts=len(problem.testcases) * 10,
            compile_info=compile_info,
            run_info={
                "result": "finished",
                "message": "\n".join(run_messages)
                if run_messages
                else f"{len(problem.testcases)} test cases finished",
            },
            error_info="",
            details=details,
        )

    async def _store_result(
        self,
        submission_id: str,
        *,
        score: int,
        counts: int,
        compile_info: dict | None,
        run_info: dict,
        error_info: str,
        details: list[dict],
    ) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                "DELETE FROM testcase_results WHERE submission_id = ?", (submission_id,)
            )
            await connection.executemany(
                """
                INSERT INTO testcase_results (
                    submission_id, testcase_id, result, time_seconds, memory_mb
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        submission_id,
                        detail["id"],
                        detail["result"],
                        detail["time"],
                        detail["memory"],
                    )
                    for detail in details
                ],
            )
            await connection.execute(
                """
                UPDATE submissions
                SET status = 'success', score = ?, counts = ?, compile_info = ?,
                    run_info = ?, error_info = ?, updated_at = datetime('now')
                WHERE submission_id = ?
                """,
                (
                    score,
                    counts,
                    json.dumps(compile_info, ensure_ascii=False),
                    json.dumps(run_info, ensure_ascii=False),
                    error_info,
                    submission_id,
                ),
            )
            await connection.commit()

    async def _mark_error(self, submission_id: str) -> None:
        await self.database.execute(
            """
            UPDATE submissions
            SET status = 'error', score = NULL, counts = NULL,
                compile_info = NULL, run_info = NULL,
                error_info = 'judge internal error', updated_at = datetime('now')
            WHERE submission_id = ?
            """,
            (submission_id,),
        )
