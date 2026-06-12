from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .json_store import write_json_atomic


PARTIAL_HERO_PHONE_BIND_RESULT_NAME = "hero_phone_bind_partial_result.json"


@dataclass
class PhoneFlowFailure:
    code: str = ""
    message: str = ""
    retryable: bool = False
    recovery_action: str = "stop"


PHONE_FLOW_SIGNUP_VERIFIED_STAGES = frozenset({
    "signup_sms_verified",
    "oauth_phone_verified",
    "awaiting_callback",
    "awaiting_cpa_callback",
    "callback_fetched",
    "cpa_callback_fetched",
    "callback_submitted",
})

PHONE_FLOW_OAUTH_VERIFIED_STAGES = frozenset({
    "oauth_phone_verified",
    "awaiting_callback",
    "awaiting_cpa_callback",
    "callback_fetched",
    "cpa_callback_fetched",
    "callback_submitted",
})


def classify_phone_flow_error(raw_error: str) -> PhoneFlowFailure:
    message = str(raw_error or "").strip()
    lowered = message.lower()
    if not message:
        return PhoneFlowFailure(code="unknown", message="", retryable=False, recovery_action="stop")
    if "whatsapp_channel_detected" in lowered:
        return PhoneFlowFailure(code="unexpected_delivery_channel", message=message, retryable=True, recovery_action="replace_phone")
    if "sms_code_timeout" in lowered or "hero_sms_code_timeout" in lowered or "phone_otp_timeout" in lowered:
        return PhoneFlowFailure(code="sms_timeout", message=message, retryable=True, recovery_action="resend_or_replace_phone")
    if lowered.startswith("validate_phone_signup_otp_") or "phone_otp_validate_failed" in lowered:
        return PhoneFlowFailure(code="sms_rejected", message=message, retryable=True, recovery_action="replace_phone")
    if "add_phone_send_failed" in lowered:
        return PhoneFlowFailure(code="phone_submission_failed", message=message, retryable=True, recovery_action="replace_phone")
    if "cpa_callback_submit_failed" in lowered:
        return PhoneFlowFailure(code="cpa_callback_submit_failed", message=message, retryable=True, recovery_action="retry_callback_submit")
    if "callback_submit_failed" in lowered:
        return PhoneFlowFailure(code="callback_submit_failed", message=message, retryable=True, recovery_action="retry_callback_submit")
    if "callback_not_reached" in lowered or "callback_not_ready" in lowered or "cpa_callback_not_reached" in lowered or "cpa_callback_not_ready" in lowered:
        return PhoneFlowFailure(code="callback_not_produced", message=message, retryable=True, recovery_action="retry_callback_fetch")
    if "bind_email" in lowered or "add_email" in lowered:
        retryable = "timeout" in lowered or "required" in lowered
        return PhoneFlowFailure(code="bind_email_failed", message=message, retryable=retryable, recovery_action="retry_bind_email" if retryable else "stop")
    if (
        "continue_url" in lowered
        or "create_account_" in lowered
        or "session_establishment_failed" in lowered
        or "page_shape" in lowered
        or "unexpected_page" in lowered
    ):
        return PhoneFlowFailure(code="page_shape_unexpected", message=message, retryable=False, recovery_action="stop")
    return PhoneFlowFailure(code="phone_signup_failed", message=message, retryable=False, recovery_action="stop")


def build_phone_flow_runtime(
    *,
    phone_number: str = "",
    activation_id: str = "",
    provider: str = "",
    stage: str = "partial",
    purpose: str = "signup",
    status: str = "partial",
    bind_email: str = "",
    callback_url: str = "",
    callback_source: str = "",
    import_submit_ok: bool | None = None,
    import_submit_message: str = "",
    last_error: str = "",
    error_code: str = "",
    error_retryable: bool = False,
    recovery_action: str = "stop",
) -> dict[str, Any]:
    normalized_stage = str(stage or status or "partial").strip() or "partial"
    resolved_provider = str(provider or "").strip() or ("hero_sms" if phone_number or activation_id else "")
    return {
        "phone_number": str(phone_number or "").strip(),
        "activation_id": str(activation_id or "").strip(),
        "provider": resolved_provider,
        "stage": normalized_stage,
        "status": str(status or normalized_stage or "partial").strip() or "partial",
        "purpose": str(purpose or "signup").strip() or "signup",
        "bind_email": str(bind_email or "").strip(),
        "signup_verified": normalized_stage in PHONE_FLOW_SIGNUP_VERIFIED_STAGES,
        "oauth_verified": normalized_stage in PHONE_FLOW_OAUTH_VERIFIED_STAGES,
        "callback": {
            "url": str(callback_url or "").strip(),
            "source": str(callback_source or "").strip(),
        },
        "import_submit_ok": import_submit_ok,
        "import_submit_message": str(import_submit_message or "").strip(),
        "error": {
            "code": str(error_code or "").strip(),
            "message": str(last_error or "").strip(),
            "retryable": bool(error_retryable),
            "recovery_action": str(recovery_action or "stop").strip() or "stop",
        },
    }


def set_phone_flow_stage(
    phone_flow: dict[str, Any],
    stage: str,
    *,
    status: str | None = None,
    bind_email: str | None = None,
    callback_url: str | None = None,
    callback_source: str | None = None,
    import_submit_ok: bool | None = None,
    import_submit_message: str | None = None,
    cpa_submit_ok: bool | None = None,
    cpa_submit_message: str | None = None,
    last_error: str | None = None,
    error_code: str | None = None,
    error_retryable: bool | None = None,
    recovery_action: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    phone_flow["stage"] = str(stage or phone_flow.get("stage") or "partial").strip() or "partial"
    if status is not None:
        phone_flow["status"] = str(status or "").strip() or phone_flow["stage"]
    else:
        phone_flow["status"] = str(phone_flow.get("status") or phone_flow["stage"]).strip() or phone_flow["stage"]
    if bind_email is not None:
        phone_flow["bind_email"] = str(bind_email or "").strip()
    if purpose is not None:
        phone_flow["purpose"] = str(purpose or "signup").strip() or "signup"
    callback = dict(phone_flow.get("callback") or {})
    if callback_url is not None:
        callback["url"] = str(callback_url or "").strip()
    if callback_source is not None:
        callback["source"] = str(callback_source or "").strip()
    phone_flow["callback"] = callback
    legacy_import_ok = phone_flow.get("import_submit_ok") if "import_submit_ok" in phone_flow else phone_flow.get("cpa_submit_ok")
    legacy_import_message = phone_flow.get("import_submit_message") if "import_submit_message" in phone_flow else phone_flow.get("cpa_submit_message")
    resolved_import_submit_ok = import_submit_ok if import_submit_ok is not None else cpa_submit_ok
    resolved_import_submit_message = import_submit_message if import_submit_message is not None else cpa_submit_message
    if resolved_import_submit_ok is not None or "import_submit_ok" not in phone_flow:
        phone_flow["import_submit_ok"] = resolved_import_submit_ok if resolved_import_submit_ok is not None else legacy_import_ok
    if resolved_import_submit_message is not None:
        phone_flow["import_submit_message"] = str(resolved_import_submit_message or "").strip()
    elif "import_submit_message" not in phone_flow and legacy_import_message is not None:
        phone_flow["import_submit_message"] = str(legacy_import_message or "").strip()
    error = dict(phone_flow.get("error") or {})
    if last_error is not None:
        error["message"] = str(last_error or "").strip()
    if error_code is not None:
        error["code"] = str(error_code or "").strip()
    if error_retryable is not None:
        error["retryable"] = bool(error_retryable)
    if recovery_action is not None:
        error["recovery_action"] = str(recovery_action or "stop").strip() or "stop"
    if not error:
        error = {"code": "", "message": "", "retryable": False, "recovery_action": "stop"}
    phone_flow["error"] = error
    normalized_stage = str(phone_flow.get("stage") or "partial").strip() or "partial"
    phone_flow["signup_verified"] = normalized_stage in PHONE_FLOW_SIGNUP_VERIFIED_STAGES
    phone_flow["oauth_verified"] = normalized_stage in PHONE_FLOW_OAUTH_VERIFIED_STAGES
    return phone_flow


def snapshot_phone_flow_attempt(phone_flow: dict[str, Any], *, note: str = "") -> dict[str, Any]:
    callback = dict(phone_flow.get("callback") or {})
    error = dict(phone_flow.get("error") or {})
    return {
        "stage": str(phone_flow.get("stage") or "").strip(),
        "status": str(phone_flow.get("status") or "").strip(),
        "purpose": str(phone_flow.get("purpose") or "").strip(),
        "phone_number": str(phone_flow.get("phone_number") or "").strip(),
        "activation_id": str(phone_flow.get("activation_id") or "").strip(),
        "provider": str(phone_flow.get("provider") or "").strip(),
        "bind_email": str(phone_flow.get("bind_email") or "").strip(),
        "callback_url": str(callback.get("url") or "").strip(),
        "callback_source": str(callback.get("source") or "").strip(),
        "import_submit_ok": phone_flow.get("import_submit_ok", phone_flow.get("cpa_submit_ok")),
        "import_submit_message": str(phone_flow.get("import_submit_message", phone_flow.get("cpa_submit_message") or "") or "").strip(),
        "error_code": str(error.get("code") or "").strip(),
        "last_error": str(error.get("message") or "").strip(),
        "recovery_action": str(error.get("recovery_action") or "").strip(),
        "note": str(note or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_partial_hero_phone_bind_result(
    *,
    phone_flow: dict[str, Any],
    password: str,
    note: str = "",
    data_dir: Path = DATA_DIR,
) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / PARTIAL_HERO_PHONE_BIND_RESULT_NAME
    previous_attempts: list[dict[str, Any]] = []
    if path.exists():
        try:
            previous_payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(previous_payload, dict) and isinstance(previous_payload.get("attempts"), list):
                previous_attempts = [item for item in previous_payload.get("attempts") or [] if isinstance(item, dict)]
        except Exception:
            previous_attempts = []
    normalized_flow = build_phone_flow_runtime(
        phone_number=str(phone_flow.get("phone_number") or "").strip(),
        activation_id=str(phone_flow.get("activation_id") or "").strip(),
        provider=str(phone_flow.get("provider") or "").strip(),
        stage=str(phone_flow.get("stage") or "partial").strip() or "partial",
        purpose=str(phone_flow.get("purpose") or "signup").strip() or "signup",
        status=str(phone_flow.get("status") or phone_flow.get("stage") or "partial").strip() or "partial",
        bind_email=str(phone_flow.get("bind_email") or "").strip(),
        callback_url=str((phone_flow.get("callback") or {}).get("url") or "").strip(),
        callback_source=str((phone_flow.get("callback") or {}).get("source") or "").strip(),
        import_submit_ok=phone_flow.get("import_submit_ok", phone_flow.get("cpa_submit_ok")),
        import_submit_message=str(phone_flow.get("import_submit_message", phone_flow.get("cpa_submit_message") or "") or "").strip(),
        last_error=str((phone_flow.get("error") or {}).get("message") or "").strip(),
        error_code=str((phone_flow.get("error") or {}).get("code") or "").strip(),
        error_retryable=bool((phone_flow.get("error") or {}).get("retryable") or False),
        recovery_action=str((phone_flow.get("error") or {}).get("recovery_action") or "stop").strip() or "stop",
    )
    callback = dict(normalized_flow.get("callback") or {})
    error = dict(normalized_flow.get("error") or {})
    attempts = (previous_attempts + [snapshot_phone_flow_attempt(normalized_flow, note=note)])[-50:]
    payload = {
        "ok": bool(callback.get("url")),
        "status": normalized_flow.get("status"),
        "stage": normalized_flow.get("stage"),
        "provider": normalized_flow.get("provider"),
        "verification_purpose": normalized_flow.get("purpose"),
        "phone_number": normalized_flow.get("phone_number"),
        "password": str(password or "").strip(),
        "activation_id": normalized_flow.get("activation_id"),
        "email": normalized_flow.get("bind_email"),
        "callback_url": callback.get("url") or "",
        "callback_source": callback.get("source") or "",
        "import_submit_ok": normalized_flow.get("import_submit_ok"),
        "import_submit_message": normalized_flow.get("import_submit_message") or "",
        "last_error": error.get("message") or "",
        "error_code": error.get("code") or "",
        "error_retryable": bool(error.get("retryable") or False),
        "recovery_action": error.get("recovery_action") or "stop",
        "phone_flow": normalized_flow,
        "attempts": attempts,
        "attempt_count": len(attempts),
        "note": str(note or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(path, payload)
    return str(path)
