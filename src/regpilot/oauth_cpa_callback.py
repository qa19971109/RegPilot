from __future__ import annotations

import time
from typing import Any, Callable


def submit_callback_to_cpa_management(
    cpa_url: str,
    cpa_management_key: str,
    callback_or_code: str,
    expected_state: str = "",
) -> dict[str, Any]:
    from .reauthorize import _submit_callback_to_cpa

    return _submit_callback_to_cpa(
        callback_or_code,
        cpa_url=cpa_url,
        cpa_management_key=cpa_management_key,
        expected_state=expected_state,
    )


def submit_callback_to_cpa_with_retry(
    cpa_url: str,
    cpa_management_key: str,
    callback_or_code: str,
    *,
    expected_state: str = "",
    max_attempts: int = 3,
    retry_delay: float = 2.0,
    submit_fn: Callable[[str, str, str, str], Any] = submit_callback_to_cpa_management,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    attempts = max(1, int(max_attempts or 1))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            result = submit_fn(cpa_url, cpa_management_key, callback_or_code, expected_state)
            if isinstance(result, dict):
                result["submit_attempts"] = attempt
                return result
            return {"ok": True, "message": str(result or ""), "raw": result, "submit_attempts": attempt}
        except Exception as exc:
            last_error = str(exc)
            if attempt >= attempts:
                break
            sleep_fn(max(0.0, float(retry_delay or 0.0)))
    raise RuntimeError(f"cpa_callback_submit_failed: {last_error or 'unknown'}")
