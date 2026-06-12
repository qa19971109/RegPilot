from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import LOG_DIR as DEFAULT_LOG_DIR


_RESTORED_JOB_FAILED_TEXT = "\u9636\u6bb5\uff1a\u4efb\u52a1\u5931\u8d25"
_RESTORED_JOB_STOP_REQUEST_TEXT = "\u7528\u6237\u8bf7\u6c42\u505c\u6b62"
_RESTORED_JOB_QUEUED_TEXT = "\u4efb\u52a1\u5df2\u6392\u961f"
_RESTORED_JOB_STARTED_TEXT = "\u4efb\u52a1\u5f00\u59cb\u6267\u884c"
_RESTORED_STAGE_FAILED = "\u4efb\u52a1\u5931\u8d25"
_RESTORED_STAGE_STOPPED = "\u4efb\u52a1\u5df2\u505c\u6b62"


class JobCancelledError(RuntimeError):
    pass


class JobStore:
    MAX_OUTPUT_CHARS = 200_000

    def __init__(
        self,
        *,
        restore: bool = False,
        log_dir_getter: Callable[[], Path] | None = None,
        prune_callback: Callable[[], None] | None = None,
        error_translator: Callable[[Any], str] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._log_dir_getter = log_dir_getter or (lambda: DEFAULT_LOG_DIR)
        self._prune_callback = prune_callback or (lambda: None)
        self._error_translator = error_translator or (lambda value: str(value or ""))
        try:
            self._jobs_log_dir().mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if restore:
            self._restore_from_disk()

    def _jobs_log_dir(self) -> Path:
        return Path(self._log_dir_getter()) / "jobs"

    def _trim_output(self, output: str) -> str:
        text = str(output or "")
        if len(text) <= self.MAX_OUTPUT_CHARS:
            return text
        return "... output truncated ...\n" + text[-self.MAX_OUTPUT_CHARS:]

    def _repair_restored_output(self, output: str) -> str:
        text = str(output or "")
        try:
            repaired = text.encode("latin1").decode("utf-8")
        except Exception:
            return text
        if repaired.count("阶段") > text.count("阶段") or repaired.count("手机") > text.count("手机"):
            return repaired
        return text

    def _restore_log_files(self) -> list[Path]:
        try:
            return sorted(path for path in self._jobs_log_dir().glob("*.log") if path.is_file())
        except Exception:
            return []

    def _parse_restored_log_name(self, path: Path) -> tuple[str, str, str, str] | None:
        match = re.match(r"^(\d{8}-\d{6})-(job-(\d+))-(.+)\.log$", path.name)
        if not match:
            return None
        timestamp_text, raw_job_id, counter_text, raw_kind = match.groups()
        return timestamp_text, raw_job_id, counter_text, raw_kind

    def _restored_started_at(self, timestamp_text: str) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(timestamp_text, "%Y%m%d-%H%M%S"))
        except Exception:
            return ""

    def _restored_finished_at(self, path: Path) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
        except Exception:
            return ""

    def _read_restored_output(self, path: Path) -> str:
        try:
            output = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            output = ""
        return self._repair_restored_output(output)

    def _restored_status(self, output: str) -> str:
        lowered = output.lower()
        if _RESTORED_JOB_FAILED_TEXT in output or "traceback" in lowered:
            return "failed"
        if _RESTORED_JOB_STOP_REQUEST_TEXT in output or "job_stopped_by_user" in lowered:
            return "stopped"
        if _RESTORED_JOB_QUEUED_TEXT in output and _RESTORED_JOB_STARTED_TEXT not in output:
            return "stopped"
        if not output.strip():
            return "queued"
        return "done"

    def _restore_job_id(self, raw_job_id: str, timestamp_text: str) -> str:
        if raw_job_id in self._jobs:
            return f"{raw_job_id}-{timestamp_text}"
        return raw_job_id

    def _update_counter_from_restored_job(self, counter_text: str) -> None:
        try:
            self._counter = max(self._counter, int(counter_text))
        except Exception:
            pass

    def _restored_job(
        self,
        *,
        path: Path,
        job_id: str,
        raw_kind: str,
        status: str,
        started_at: str,
        finished_at: str,
        output: str,
    ) -> dict[str, Any]:
        return {
            "id": job_id,
            "kind": raw_kind,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at if status not in {"queued", "running", "stopping"} else "",
            "result": None,
            "error": None,
            "output": self._trim_output(output),
            "log_path": str(path),
            "stop_requested": False,
            "meta": {
                "stage": "",
                "current_phone": "",
            },
        }

    def _apply_restored_status_stage(self, job: dict[str, Any], status: str) -> None:
        if status == "failed":
            job["meta"] = {**(job.get("meta") or {}), "stage": str((job.get("meta") or {}).get("stage") or _RESTORED_STAGE_FAILED)}
            return
        if status != "stopped":
            return
        stage = str((job.get("meta") or {}).get("stage") or "")
        if not stage or _RESTORED_JOB_QUEUED_TEXT in stage or _RESTORED_JOB_STOP_REQUEST_TEXT in stage:
            stage = _RESTORED_STAGE_STOPPED
        job["meta"] = {**(job.get("meta") or {}), "stage": stage}

    def _restore_from_disk(self) -> None:
        for path in self._restore_log_files():
            parsed = self._parse_restored_log_name(path)
            if not parsed:
                continue
            timestamp_text, raw_job_id, counter_text, raw_kind = parsed
            output = self._read_restored_output(path)
            status = self._restored_status(output)
            job_id = self._restore_job_id(raw_job_id, timestamp_text)
            self._update_counter_from_restored_job(counter_text)
            job = self._restored_job(
                path=path,
                job_id=job_id,
                raw_kind=raw_kind,
                status=status,
                started_at=self._restored_started_at(timestamp_text),
                finished_at=self._restored_finished_at(path),
                output=output,
            )
            self._update_meta_from_output(job)
            self._apply_restored_status_stage(job, status)
            self._jobs[job_id] = job

    def create(self, kind: str) -> str:
        with self._lock:
            self._counter += 1
            job_id = f"job-{self._counter}"
            log_dir = self._jobs_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(kind or "job"))
            log_path = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{job_id}-{safe_kind}.log"
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "started_at": started_at,
                "finished_at": "",
                "result": None,
                "error": None,
                "output": "",
                "log_path": str(log_path),
                "stop_requested": False,
                "meta": {
                    "stage": "",
                    "current_phone": "",
                },
            }
            return job_id

    def finish(self, job_id: str, *, result: Any = None, error: Any = None, output: str = "") -> None:
        with self._lock:
            job = self._jobs[job_id]
            result_failed = isinstance(result, dict) and result.get("ok") is False
            job["status"] = "failed" if error or result_failed else "done"
            job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["result"] = result
            job["error"] = error
            job["output"] = self._trim_output(output)
            if result_failed:
                stage = self._error_translator(result.get("error") or result.get("message") or "任务失败")
                job["meta"] = {**(job.get("meta") or {}), "stage": f"结束：{stage}"}
            elif error:
                stage = self._error_translator((error or {}).get("message") if isinstance(error, dict) else error)
                job["meta"] = {**(job.get("meta") or {}), "stage": f"结束：{stage or '任务异常'}"}
        self._prune_callback()

    def _append_output_locked(self, job: dict[str, Any], chunk: str) -> None:
        job["output"] = self._trim_output(str(job.get("output") or "") + chunk)
        log_path = str(job.get("log_path") or "")
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(chunk)
            except Exception:
                pass
        self._update_meta_from_output(job)

    def append_output(self, job_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            job = self._jobs[job_id]
            self._append_output_locked(job, chunk)

    def request_stop(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise ValueError("job_not_found")
            status = str(job.get("status") or "")
            if status == "stopping":
                return {"ok": True, "job_id": job_id, "status": "stopping", "message": "停止请求已发送"}
            if status not in {"queued", "running"}:
                return {"ok": False, "job_id": job_id, "status": job.get("status") or "unknown", "message": "任务当前不在运行中"}
            job["stop_requested"] = True
            job["status"] = "stopping"
            job["meta"] = {**(job.get("meta") or {}), "stage": "用户请求停止任务"}
            self._append_output_locked(job, "阶段：用户请求停止任务\n")
            return {"ok": True, "job_id": job_id, "status": "stopping", "message": "已发送停止请求，等待当前步骤结束"}

    def should_stop(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id) or {}
            return bool(job.get("stop_requested"))

    def raise_if_stop_requested(self, job_id: str) -> None:
        if self.should_stop(job_id):
            raise JobCancelledError("job_stopped_by_user")

    def _update_meta_from_output(self, job: dict[str, Any]) -> None:
        output = str(job.get("output") or "")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return
        stage = str(job.get("meta", {}).get("stage") or "")
        current_phone = str(job.get("meta", {}).get("current_phone") or "")
        for line in lines[-30:]:
            if "Flow stage:" in line:
                stage = line.split("Flow stage:", 1)[1].strip()
            elif line.startswith("阶段："):
                stage = line.split("阶段：", 1)[1].strip()
            elif "CPA phone:" in line:
                phone = line.split("CPA phone:", 1)[1].strip()
                if phone:
                    current_phone = phone
                    stage = "phone active"
            elif "已创建邮箱：" in line:
                stage = "mailbox created"
            elif "邮箱验证码发送结果" in line or "send_otp" in line:
                stage = "waiting email code"
            elif "已收到邮箱验证码：" in line:
                stage = "email code received"
            elif "账号创建结果" in line or "create_account" in line:
                stage = "creating account"
        job["meta"] = {
            "stage": stage,
            "current_phone": current_phone,
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(list(self._jobs.values())))

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if str(job.get("status") or "") != "stopping":
                job["status"] = "running"

    def mark_stopped(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = "stopped"
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
