from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


__all__ = [
    "CpaUsageDecision",
    "cpa_usage_decision_from_probe",
    "rounded_percent",
]


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _codex_window_used_percent(window: Any) -> float | None:
    if not isinstance(window, dict):
        return None
    return _normalize_number(window.get("used_percent", window.get("usedPercent")))


def _codex_window_seconds(window: Any) -> float | None:
    if not isinstance(window, dict):
        return None
    return _normalize_number(window.get("limit_window_seconds", window.get("limitWindowSeconds")))


def _codex_rate_limit_windows(rate_limit: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(rate_limit, dict):
        return None, None
    primary = rate_limit.get("primary_window", rate_limit.get("primaryWindow"))
    secondary = rate_limit.get("secondary_window", rate_limit.get("secondaryWindow"))
    raw_windows = [item if isinstance(item, dict) else None for item in (primary, secondary)]
    five_hour = None
    weekly = None
    for window in raw_windows:
        seconds = _codex_window_seconds(window)
        if seconds == 18000 and five_hour is None:
            five_hour = window
        elif seconds == 604800 and weekly is None:
            weekly = window
    if five_hour is None and raw_windows[0] is not weekly:
        five_hour = raw_windows[0]
    if weekly is None and raw_windows[1] is not five_hour:
        weekly = raw_windows[1]
    return five_hour, weekly


def _codex_rate_limit_used_percent(rate_limit: Any) -> float | None:
    if not isinstance(rate_limit, dict):
        return None
    values = [
        value
        for value in (
            _codex_window_used_percent(rate_limit.get("primary_window", rate_limit.get("primaryWindow"))),
            _codex_window_used_percent(rate_limit.get("secondary_window", rate_limit.get("secondaryWindow"))),
        )
        if value is not None
    ]
    return max(values) if values else None


def _codex_rate_limit_reached(rate_limit: Any) -> bool:
    if not isinstance(rate_limit, dict):
        return False
    if rate_limit.get("allowed") is False:
        return True
    if _truthy_flag(rate_limit.get("limit_reached")) or _truthy_flag(rate_limit.get("limitReached")):
        return True
    return any(
        value is not None and value >= 100
        for value in (
            _codex_window_used_percent(rate_limit.get("primary_window", rate_limit.get("primaryWindow"))),
            _codex_window_used_percent(rate_limit.get("secondary_window", rate_limit.get("secondaryWindow"))),
        )
    )


def _percent_label(value: float | None) -> str:
    return "--" if value is None else f"{value:.1f}%"


@dataclass(frozen=True)
class CpaUsageDecision:
    status_code: int
    used_percent: float | None
    weekly_used_percent: float | None
    five_hour_used_percent: float | None
    action: str
    usage_state: str
    recommended_action: str
    error_text: str

    @property
    def ok(self) -> bool:
        return self.action in {"cpa_keep", "cpa_usage_available"} and not self.error_text

    @property
    def usage_message(self) -> str:
        return f"weekly_used={_percent_label(self.weekly_used_percent)}; five_hour_used={_percent_label(self.five_hour_used_percent)}"


def rounded_percent(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


def cpa_usage_decision_from_probe(probe: dict[str, Any], *, was_disabled: bool) -> CpaUsageDecision:
    status_code = int(probe.get("status_code") or 0)
    usage_payload = probe.get("payload") if isinstance(probe.get("payload"), dict) else {}
    rate_limit = usage_payload.get("rate_limit", usage_payload.get("rateLimit")) if isinstance(usage_payload, dict) else None
    five_hour_window, weekly_window = _codex_rate_limit_windows(rate_limit)
    weekly_used_percent = _codex_window_used_percent(weekly_window)
    five_hour_used_percent = _codex_window_used_percent(five_hour_window)
    used_percent = weekly_used_percent if weekly_used_percent is not None else _codex_rate_limit_used_percent(rate_limit)
    threshold = 100.0
    weekly_over_threshold = weekly_used_percent is not None and weekly_used_percent >= threshold
    five_hour_over_threshold = five_hour_used_percent is not None and five_hour_used_percent >= threshold
    body_text = str(probe.get("body_text") or "").lower()
    quota_error = (
        status_code == 402
        or any(pattern in body_text for pattern in ("quota exhausted", "limit reached", "payment_required"))
        or _codex_rate_limit_reached(rate_limit)
        or (used_percent is not None and used_percent >= threshold)
    )
    action = "cpa_keep"
    usage_state = "available"
    recommended_action = ""
    error_text = ""
    if status_code == 401:
        action = "cpa_auth_invalid"
        usage_state = "unauthorized"
        error_text = str(probe.get("error") or "unauthorized")
    elif weekly_window is not None and weekly_used_percent is not None:
        if weekly_over_threshold:
            usage_state = "limit_reached"
            if was_disabled:
                action = "cpa_keep"
            else:
                action = "cpa_usage_limit_reached"
                recommended_action = "disable"
        elif was_disabled:
            action = "cpa_usage_available"
            recommended_action = "enable"
        elif five_hour_over_threshold:
            action = "cpa_keep"
            usage_state = "five_hour_limit_reached"
        else:
            action = "cpa_keep"
    elif quota_error:
        usage_state = "limit_reached"
        if not was_disabled:
            action = "cpa_usage_limit_reached"
            recommended_action = "disable"
    elif status_code == 200 and was_disabled:
        action = "cpa_usage_available"
        recommended_action = "enable"
    elif status_code < 200 or status_code >= 300:
        action = "cpa_probe_failed"
        usage_state = "unknown"
        error_text = str(probe.get("error") or f"HTTP {status_code}")
    return CpaUsageDecision(
        status_code=status_code,
        used_percent=used_percent,
        weekly_used_percent=weekly_used_percent,
        five_hour_used_percent=five_hour_used_percent,
        action=action,
        usage_state=usage_state,
        recommended_action=recommended_action,
        error_text=error_text,
    )
