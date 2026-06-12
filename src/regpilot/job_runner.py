from __future__ import annotations

import io
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable, Type

from .logging_utils import reset_log_context, set_log_context


_JOB_QUEUED_STAGE = "\u9636\u6bb5\uff1a\u4efb\u52a1\u5df2\u6392\u961f\uff0c\u7b49\u5f85\u524d\u4e00\u4e2a\u4efb\u52a1\u5b8c\u6210\n"
_JOB_STARTED_STAGE = "\u9636\u6bb5\uff1a\u4efb\u52a1\u5f00\u59cb\u6267\u884c\n"
_JOB_FAILED_STAGE = "\u9636\u6bb5\uff1a\u4efb\u52a1\u5931\u8d25\uff1a{error}\n"


class JobOutputStream(io.TextIOBase):
    def __init__(self, jobs: Any, job_id: str) -> None:
        super().__init__()
        self._jobs = jobs
        self._job_id = job_id
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        self._jobs.raise_if_stop_requested(self._job_id)
        text = str(s or "")
        if not text:
            return 0
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._jobs.append_output(self._job_id, line + "\n")
                self._jobs.raise_if_stop_requested(self._job_id)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                self._jobs.append_output(self._job_id, self._buffer)
                self._buffer = ""


def _job_output_text(jobs: Any, job_id: str) -> str:
    for job in jobs.list():
        if job.get("id") == job_id:
            return str(job.get("output") or "")
    return ""


def _acquire_execution_lock(jobs: Any, job_id: str, execution_lock: threading.Lock) -> bool:
    locked = execution_lock.acquire(blocking=False)
    if locked:
        return True
    jobs.append_output(job_id, _JOB_QUEUED_STAGE)
    while not locked:
        jobs.raise_if_stop_requested(job_id)
        locked = execution_lock.acquire(timeout=0.2)
    return True


def _execute_job_func(
    jobs: Any,
    job_id: str,
    stdout: JobOutputStream,
    execution_lock: threading.Lock,
    func: Callable[..., Any],
    func_args: tuple[Any, ...],
    func_kwargs: dict[str, Any],
) -> Any:
    locked = False
    try:
        jobs.raise_if_stop_requested(job_id)
        locked = _acquire_execution_lock(jobs, job_id, execution_lock)
        jobs.raise_if_stop_requested(job_id)
        jobs.mark_running(job_id)
        jobs.append_output(job_id, _JOB_STARTED_STAGE)
        with redirect_stdout(stdout), redirect_stderr(stdout):
            return func(*func_args, **func_kwargs)
    finally:
        if locked:
            execution_lock.release()


def _finish_cancelled_job(
    jobs: Any,
    job_id: str,
    stdout: JobOutputStream,
    exc: BaseException,
) -> None:
    stdout.flush()
    jobs.finish(
        job_id,
        result={"ok": False, "stopped": True, "message": str(exc)},
        error=None,
        output=_job_output_text(jobs, job_id),
    )
    jobs.mark_stopped(job_id)


def _finish_failed_job(
    jobs: Any,
    job_id: str,
    stdout: JobOutputStream,
    exc: Exception,
    error_translator: Callable[[Any], str],
) -> None:
    jobs.append_output(job_id, _JOB_FAILED_STAGE.format(error=error_translator(exc)))
    stdout.flush()
    jobs.finish(
        job_id,
        error={"message": str(exc), "traceback": traceback.format_exc()},
        output=_job_output_text(jobs, job_id),
    )


def _run_job_target(
    jobs: Any,
    job_id: str,
    execution_lock: threading.Lock,
    func: Callable[..., Any],
    func_args: tuple[Any, ...],
    func_kwargs: dict[str, Any],
    error_translator: Callable[[Any], str],
    cancelled_error_type: Type[BaseException],
) -> None:
    stdout = JobOutputStream(jobs, job_id)
    log_tokens = set_log_context(task_id=job_id)
    try:
        result = _execute_job_func(jobs, job_id, stdout, execution_lock, func, func_args, func_kwargs)
        stdout.flush()
        jobs.finish(job_id, result=result, output=_job_output_text(jobs, job_id))
    except cancelled_error_type as exc:
        _finish_cancelled_job(jobs, job_id, stdout, exc)
    except Exception as exc:
        _finish_failed_job(jobs, job_id, stdout, exc, error_translator)
    finally:
        reset_log_context(log_tokens)


def run_job(
    jobs: Any,
    execution_lock: threading.Lock,
    kind: str,
    func: Callable[..., Any],
    *args: Any,
    error_translator: Callable[[Any], str],
    cancelled_error_type: Type[BaseException],
    **kwargs: Any,
) -> dict[str, str]:
    job_id = jobs.create(kind)
    target_args = (
        jobs,
        job_id,
        execution_lock,
        func,
        args,
        kwargs,
        error_translator,
        cancelled_error_type,
    )
    threading.Thread(target=_run_job_target, args=target_args, daemon=True).start()
    return {"ok": True, "job_id": job_id}
