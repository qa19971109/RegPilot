from __future__ import annotations

import re
from typing import Any

from .oauth_token_flow import HERO_SMS_MAX_RETRY_COUNT, HERO_SMS_RESEND_AFTER_SECONDS
from .sms_provider_config import normalize_sms_provider
from .task_payload_config import positive_int_from_renamed_payload


__all__ = [
    "is_sms_inventory_error",
    "phone_signup_entry_error",
    "phone_signup_probe_is_login_password",
    "safe_register_failure_summary",
    "sms_wait_progress_message",
    "sms_retry_count_from_payload",
    "sms_retry_exhausted_message",
    "unwrap_sms_retry_error",
]


def phone_signup_entry_error(*items: Any) -> str:
    joined = " ".join(str(item or "") for item in items)
    if "authorize_hydra_invalid_request" in joined:
        return "authorize_hydra_invalid_request"
    if "Your session has ended" in joined:
        return "session_ended"
    if "/error?" in joined and "AuthApiFailure" in joined:
        return "auth_api_failure"
    return ""


def sms_retry_exhausted_message(provider: str, attempts: int, error: str) -> str:
    normalized = normalize_sms_provider(provider or "hero_sms", strict=False)
    return f"{normalized}_retry_exhausted_after_{max(1, int(attempts or 1))}_attempts: {str(error or '').strip() or 'unknown_error'}"


def unwrap_sms_retry_error(error: str) -> str:
    text = str(error or "").strip()
    match = re.match(r"^(?:hero_sms|smsbower|5sim)_retry_exhausted_after_\d+_attempts:\s*(.+)$", text)
    return match.group(1).strip() if match else text


def is_sms_inventory_error(error: str) -> bool:
    text = str(error or "").upper()
    return "NO_BALANCE" in text or "NO_NUMBERS" in text


def sms_retry_count_from_payload(payload: dict[str, Any], auto_retry: bool, *, default_retry_count: int = HERO_SMS_MAX_RETRY_COUNT) -> int:
    if not auto_retry:
        return 1
    try:
        return positive_int_from_renamed_payload(payload, "sms_retry_count", "hero_sms_retry_count", default_retry_count)
    except (TypeError, ValueError):
        return default_retry_count


def safe_register_failure_summary(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    error_body = body.get("error") if isinstance(body.get("error"), dict) else {}
    text = re.sub(r"\s+", " ", str(info.get("text") or "")).strip()
    text = re.sub(r"\+?\d{6,20}", "[phone]", text)
    text = re.sub(r"(?i)(password|token|secret|key)[\"'=:\s]+[^,}\s\"]+", r"\1=[redacted]", text)
    code = str(body.get("code") or error_body.get("code") or "").strip()
    message = str(body.get("message") or error_body.get("message") or body.get("error") or "").strip()
    if not code and re.search(r"invalid_auth_step|Invalid authorization step", text, re.I):
        code = "invalid_auth_step"
    if not message and code == "invalid_auth_step":
        message = "Invalid authorization step."
    fields = [
        f"status={info.get('status')}",
    ]
    if code:
        fields.append(f"code={code[:80]}")
    if message:
        fields.append(f"message={message[:160]}")
    elif text:
        fields.append(f"message={text[:160]}")
    if code == "invalid_auth_step":
        fields.append("action=replace_phone_or_environment")
    if info.get("sentinel_error"):
        fields.append(f"sentinel_error={str(info.get('sentinel_error'))[:180]}")
    else:
        fields.append(f"sentinel_present={bool(info.get('sentinel_token_present'))}")
    return " ".join(fields)


def sms_wait_progress_message(info: dict[str, Any], *, default_resend_after_seconds: int = HERO_SMS_RESEND_AFTER_SECONDS) -> str:
    elapsed = int(info.get("elapsed") or 0)
    remaining = int(info.get("remaining") or 0)
    if info.get("resent"):
        after_resend_elapsed = int(info.get("after_resend_elapsed") or 0)
        after_resend_limit = int(info.get("timeout_after_resend") or 0)
        if after_resend_limit > 0:
            return (
                "\u9636\u6bb5\uff1a\u7b49\u5f85\u77ed\u4fe1\u9a8c\u8bc1\u7801\u4e2d"
                f"\uff08\u91cd\u53d1\u540e\u5df2\u7b49\u5f85 {after_resend_elapsed}/{after_resend_limit} \u79d2\uff0c\u603b\u8ba1 {elapsed} \u79d2\uff0c\u5269\u4f59\u7ea6 {remaining} \u79d2\uff09"
            )
        return f"\u9636\u6bb5\uff1a\u7b49\u5f85\u77ed\u4fe1\u9a8c\u8bc1\u7801\u4e2d\uff08\u91cd\u53d1\u540e\u5df2\u7b49\u5f85 {after_resend_elapsed} \u79d2\uff0c\u603b\u8ba1 {elapsed} \u79d2\uff0c\u5269\u4f59\u7ea6 {remaining} \u79d2\uff09"
    resend_after_seconds = max(1, int(info.get("resend_after_seconds") or default_resend_after_seconds))
    resend_remaining = max(0, resend_after_seconds - elapsed)
    return f"\u9636\u6bb5\uff1a\u7b49\u5f85\u77ed\u4fe1\u9a8c\u8bc1\u7801\u4e2d\uff08\u5df2\u7b49\u5f85 {elapsed}/{resend_after_seconds} \u79d2\uff0c\u8ddd\u79bb\u81ea\u52a8\u91cd\u53d1\u7ea6 {resend_remaining} \u79d2\uff09"


def phone_signup_probe_is_login_password(probe: dict[str, Any]) -> bool:
    final_url = str(probe.get("final_url") or probe.get("url") or "").strip()
    title = str(probe.get("title") or "").strip().lower()
    text = str(probe.get("text") or "").lower()
    return "/log-in/password" in final_url or "enter your password" in title or "log-in/password" in text
