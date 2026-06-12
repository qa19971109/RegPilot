from __future__ import annotations

import re
from typing import Any

from . import task_error_messages


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lk = str(key).lower()
            if (
                lk in {"token", "access_token", "refresh_token", "id_token"}
                or (lk.endswith("_token") and not lk.startswith("has_"))
                or "password" in lk
                or "cookie" in lk
                or "secret" in lk
                or "api_key" in lk
                or lk.endswith("_auth")
                or lk.endswith("_key")
                or lk in {"key", "admin_auth", "custom_auth", "access_token", "refresh_token", "id_token"}
            ):
                out[key] = "***" if item else ""
            else:
                out[key] = _redact_sensitive(item)
        return out
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    patterns = [
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;&)}]+", r"\1[hidden]"),
        (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [hidden]"),
        (r"(?i)(api[_-]?key|sms[_-]?api[_-]?key|hero[_-]?sms[_-]?api[_-]?key|smsbower[_-]?api[_-]?key|mail[_-]?api[_-]?key)(\s*[=:]\s*)[^\s,;&)}]+", r"\1\2[hidden]"),
        (r"(?i)(password|cookie|cookies|admin[_-]?auth|custom[_-]?auth|admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token)(\s*[=:]\s*)[^\s,;&)}]+", r"\1\2[hidden]"),
        (r'(?i)("(?:api[_-]?key|sms[_-]?api[_-]?key|hero[_-]?sms[_-]?api[_-]?key|smsbower[_-]?api[_-]?key|mail[_-]?api[_-]?key|password|cookie|cookies|admin[_-]?auth|custom[_-]?auth|admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token)"\s*:\s*")[^"]*(")', r"\1[hidden]\2"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _strip_visible_debug_output(output: str) -> str:
    lines: list[str] = []
    for line in str(output or "").splitlines():
        if line.startswith("Flow debug summary:") or line.startswith("Flow debug encode failed:"):
            continue
        lines.append(_redact_sensitive_text(line))
    return "\n".join(lines)


def _zh_job_message(message: Any) -> str:
    return task_error_messages.zh_job_message(message)


def _safe_job(job: dict[str, Any]) -> dict[str, Any]:
    safe = _redact_sensitive(job)
    if isinstance(safe, dict) and "output" in safe:
        safe["output"] = _strip_visible_debug_output(str(safe.get("output") or ""))
    error = safe.get("error") if isinstance(safe, dict) else None
    if isinstance(error, dict) and "traceback" in error:
        error["traceback"] = "[hidden; see server logs]"
    return safe
