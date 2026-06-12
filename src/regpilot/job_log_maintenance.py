from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


__all__ = [
    "job_log_max_bytes",
    "prune_job_logs",
]


LoadWebuiConfig = Callable[[], dict[str, Any]]
MaxBytes = Callable[[], int]


def job_log_max_bytes(load_webui_config: LoadWebuiConfig, *, default_mb: float = 100) -> int:
    try:
        config = load_webui_config()
        logs = config.get("logs") if isinstance(config.get("logs"), dict) else {}
        mb = float(logs.get("job_log_max_mb") or default_mb)
        return max(1, int(mb * 1024 * 1024))
    except Exception:
        return int(default_mb * 1024 * 1024)


def prune_job_logs(log_dir: Path, max_bytes: MaxBytes) -> None:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        files = [path for path in log_dir.glob("*.log") if path.is_file()]
        total = sum(path.stat().st_size for path in files)
        limit = max_bytes()
        if total <= limit:
            return
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except Exception:
                continue
            if total <= limit:
                break
    except Exception:
        pass
