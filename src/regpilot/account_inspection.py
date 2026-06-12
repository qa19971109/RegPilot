from __future__ import annotations

import requests
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .accounts_store import count_accounts, delete_account, get_account, list_accounts, upsert_account
from . import account_inspection_cpa_actions, account_inspection_targets, cpa_management
from .account_inspection_results import (
    AccountInspectionSummary,
    inspection_item_from_result,
    summarize_inspection_items,
)
from .account_inspection_runner import AccountInspectionRunDeps, run_account_inspection
from .cpa_usage import (
    cpa_usage_decision_from_probe as _cpa_usage_decision_from_probe,
    rounded_percent as _rounded_percent,
)
from .reauthorize import auto_reauthorize_account_with_email_otp


@dataclass(frozen=True)
class AccountInspectionDeps:
    prefer_proxy: Callable[[str], str]
    prefer_codex2api_url: Callable[[str], str]
    prefer_codex2api_admin_key: Callable[[str], str]
    prefer_codex2api_proxy_url: Callable[[str], str]
    refresh_account_tokens: Callable[[dict[str, Any]], dict[str, Any]]
    zh_job_message: Callable[[Any], str]


_DEPS: AccountInspectionDeps | None = None


def configure_account_inspection(deps: AccountInspectionDeps) -> None:
    global _DEPS
    _DEPS = deps


def _deps() -> AccountInspectionDeps:
    if _DEPS is None:
        raise RuntimeError("account_inspection dependencies are not configured")
    return _DEPS


def _prefer_proxy(explicit_proxy: str) -> str:
    return _deps().prefer_proxy(explicit_proxy)


def _prefer_codex2api_url(explicit: str) -> str:
    return _deps().prefer_codex2api_url(explicit)


def _prefer_codex2api_admin_key(explicit: str) -> str:
    return _deps().prefer_codex2api_admin_key(explicit)


def _prefer_codex2api_proxy_url(explicit: str) -> str:
    return _deps().prefer_codex2api_proxy_url(explicit)


def _refresh_account_tokens(item: dict[str, Any]) -> dict[str, Any]:
    return _deps().refresh_account_tokens(item)


def _zh_job_message(message: Any) -> str:
    return _deps().zh_job_message(message)


class AccountInspectionRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list)
    account_id: str = ""
    model: str = "gpt-5.5"
    prompt: str = "Reply exactly with: CPA_AUTH_TEST_OK"
    threads: int = 3
    use_cpa_test: bool = True
    codex2api_url: str = ""
    codex2api_admin_key: str = ""
    codex2api_proxy_url: str = ""
    proxy: str = ""
    wait_timeout: int = 60
    wait_interval: int = 2
    request_timeout: int = 30
    auto_reauthorize: bool = False
    sms_provider: str = ""
    sms_api_key: str = ""
    hero_sms_api_key: str = ""
    smsbower_api_key: str = ""
    fivesim_api_key: str = ""
    hero_sms_base_url: str = ""
    smsbower_base_url: str = ""
    hero_sms_country: str = ""
    hero_sms_service: str = ""
    hero_sms_min_price: float | str = 0.0
    hero_sms_max_price: float | str = 0.0
    sms_wait_timeout: int | None = None
    sms_wait_interval: int | None = None
    sms_resend_after_seconds: int | None = None
    sms_timeout_after_resend_seconds: int | None = None
    sms_release_after_seconds: int | None = None
    sms_auto_retry: bool | None = None
    sms_retry_count: int | None = None
    hero_sms_wait_timeout: int | None = None
    hero_sms_wait_interval: int | None = None
    hero_sms_resend_after_seconds: int | None = None
    hero_sms_timeout_after_resend_seconds: int | None = None
    hero_sms_release_after_seconds: int | None = None
    hero_sms_auto_retry: bool | None = None
    hero_sms_retry_count: int | None = None
    allow_phone_verification: bool = False


class AccountInspectionCpaActionRequest(BaseModel):
    account_id: str = ""
    auth_index: str = ""
    name: str = ""
    action: str
    codex2api_url: str = ""
    codex2api_admin_key: str = ""


def _response_output_text(data: dict[str, Any]) -> str:
    text = str(data.get("output_text") or data.get("text") or "").strip()
    if text:
        return text
    parts: list[str] = []
    for output in data.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if isinstance(content, dict):
                value = str(content.get("text") or "").strip()
                if value:
                    parts.append(value)
    return "\n".join(parts).strip()


def _cpa_base_url(value: str) -> str:
    return cpa_management.cpa_base_url(value)


def _cpa_headers(admin_key: str) -> dict[str, str]:
    return cpa_management.cpa_headers(admin_key)


def _cpa_request(method: str, base_url: str, admin_key: str, path: str, *, json_body: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    return cpa_management.cpa_request(method, base_url, admin_key, path, json_body=json_body, timeout=timeout)


def _cpa_auth_files(base_url: str, admin_key: str, timeout: int = 30) -> list[dict[str, Any]]:
    return cpa_management.cpa_auth_files(base_url, admin_key, timeout=timeout, request_fn=_cpa_request)


def _is_inspectable_cpa_auth_file(auth_file: dict[str, Any]) -> bool:
    return cpa_management.is_inspectable_cpa_auth_file(auth_file)


def _cpa_auth_file_disabled(auth_file: dict[str, Any]) -> bool:
    return cpa_management.cpa_auth_file_disabled(auth_file)


def _cpa_auth_provider(auth_file: dict[str, Any]) -> str:
    return cpa_management.cpa_auth_provider(auth_file)


def _cpa_codex_account_id(auth_file: dict[str, Any]) -> str:
    return cpa_management.cpa_codex_account_id(auth_file)


def _account_cpa_auth_file(account: dict[str, Any], auth_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    return cpa_management.account_cpa_auth_file(account, auth_files)


def _cpa_auth_file_display_email(auth_file: dict[str, Any]) -> str:
    return cpa_management.cpa_auth_file_display_email(auth_file)


def _inspection_accounts_from_cpa_auth_files(auth_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    local_accounts = account_inspection_targets.local_accounts_for_inspection(
        count_accounts=count_accounts,
        list_accounts=list_accounts,
    )
    return cpa_management.cpa_inspection_accounts_from_auth_files(auth_files, local_accounts)


CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_INSPECTION_USER_AGENT = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"


def _cpa_api_call_error_message(result: dict[str, Any]) -> str:
    return cpa_management.cpa_api_call_error_message(result)


def _cpa_codex_usage_probe(account: dict[str, Any], payload: AccountInspectionRequest, auth_file: dict[str, Any]) -> dict[str, Any]:
    auth_index = str(auth_file.get("auth_index") or auth_file.get("authIndex") or "").strip()
    if not auth_index:
        return {"status_code": 0, "has_status_code": False, "payload": None, "body_text": "", "error": "missing_auth_index"}
    headers = {
        "Authorization": "Bearer $TOKEN$",
        "Content-Type": "application/json",
        "User-Agent": CODEX_INSPECTION_USER_AGENT,
    }
    account_id = _cpa_codex_account_id(auth_file)
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
    data = _cpa_request(
        "POST",
        _prefer_codex2api_url(payload.codex2api_url),
        _prefer_codex2api_admin_key(payload.codex2api_admin_key),
        "/v0/management/api-call",
        json_body={"authIndex": auth_index, "method": "GET", "url": CODEX_USAGE_URL, "header": headers},
        timeout=max(1, int(payload.request_timeout or 30)),
    )
    return cpa_management.cpa_api_call_probe_result(data)


def _inspection_latency_ms(started: float) -> int:
    return int((time.time() - started) * 1000)


def _cpa_auth_missing_result(account_id: str, email: str, started: float) -> dict[str, Any]:
    return {
        "ok": False,
        "account_id": account_id,
        "email": email,
        "status_code": 0,
        "latency_ms": _inspection_latency_ms(started),
        "error": "cpa_auth_file_not_found",
        "action": "cpa_auth_missing",
    }


def _cpa_probe_failed_result(
    *,
    account_id: str,
    email: str,
    auth_index: str,
    auth_name: str,
    auth_disabled: bool,
    started: float,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "account_id": account_id,
        "email": email,
        "auth_index": auth_index,
        "auth_name": auth_name,
        "auth_disabled": auth_disabled,
        "status_code": 0,
        "latency_ms": _inspection_latency_ms(started),
        "error": error,
        "action": "cpa_probe_failed",
        "usage_state": "unknown",
        "recommended_action": "",
        "inspection_source": "cpa_quota",
    }


def _cpa_usage_decision_result(
    *,
    account_id: str,
    email: str,
    auth_file: dict[str, Any],
    auth_disabled: bool,
    started: float,
    probe: dict[str, Any],
) -> dict[str, Any]:
    decision = _cpa_usage_decision_from_probe(probe, was_disabled=auth_disabled)
    return {
        "ok": decision.ok,
        "account_id": account_id,
        "email": email,
        "auth_index": str(auth_file.get("auth_index") or ""),
        "auth_name": str(auth_file.get("name") or ""),
        "auth_disabled": auth_disabled,
        "status_code": decision.status_code,
        "latency_ms": _inspection_latency_ms(started),
        "used_percent": _rounded_percent(decision.used_percent),
        "weekly_used_percent": _rounded_percent(decision.weekly_used_percent),
        "five_hour_used_percent": _rounded_percent(decision.five_hour_used_percent),
        "text": decision.usage_message,
        "error": decision.error_text,
        "action": decision.action,
        "usage_state": decision.usage_state,
        "recommended_action": decision.recommended_action,
        "inspection_source": "cpa_quota",
    }


def _cpa_auth_test(account: dict[str, Any], payload: AccountInspectionRequest, auth_files: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.time()
    account_id = str(account.get("id") or "")
    email = str(account.get("email") or "")
    auth_file = _account_cpa_auth_file(account, auth_files)
    if not auth_file:
        return _cpa_auth_missing_result(account_id, email, started)
    auth_index = str(auth_file.get("auth_index") or "")
    auth_name = str(auth_file.get("name") or "")
    was_disabled = _cpa_auth_file_disabled(auth_file)
    try:
        probe = _cpa_codex_usage_probe(account, payload, auth_file)
    except HTTPException as exc:
        return _cpa_probe_failed_result(
            account_id=account_id,
            email=email,
            auth_index=auth_index,
            auth_name=auth_name,
            auth_disabled=was_disabled,
            started=started,
            error=str(exc.detail or exc),
        )
    if not probe.get("has_status_code"):
        return _cpa_probe_failed_result(
            account_id=account_id,
            email=email,
            auth_index=auth_index,
            auth_name=auth_name,
            auth_disabled=was_disabled,
            started=started,
            error="response_missing_status_code",
        )
    return _cpa_usage_decision_result(
        account_id=account_id,
        email=email,
        auth_file=auth_file,
        auth_disabled=was_disabled,
        started=started,
        probe=probe,
    )


def _codex_account_failure_result(
    *,
    account_id: str,
    email: str,
    started: float,
    status_code: int,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "account_id": account_id,
        "email": email,
        "status_code": status_code,
        "latency_ms": _inspection_latency_ms(started),
        "error": error,
    }


def _codex_account_test_body(payload: AccountInspectionRequest) -> dict[str, Any]:
    return {
        "model": str(payload.model or "").strip() or "gpt-5.5",
        "input": str(payload.prompt or "").strip() or "Reply exactly with: CPA_AUTH_TEST_OK",
        "stream": False,
        "max_output_tokens": 32,
    }


def _post_codex_account_test(access_token: str, payload: AccountInspectionRequest, body: dict[str, Any]) -> Any:
    proxy = _prefer_proxy(payload.proxy)
    return requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=body,
        timeout=max(1, int(payload.request_timeout or 30)),
        proxies={"http": proxy, "https": proxy} if proxy else None,
    )


def _response_json_dict(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _codex_response_error_text(response: Any, data: dict[str, Any]) -> str:
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    error_text = str(error.get("message") or error.get("code") or "").strip() if isinstance(error, dict) else ""
    if not error_text and response.status_code >= 400:
        error_text = str(getattr(response, "text", "") or "").strip()[:500]
    return error_text


def _codex_account_test(account: dict[str, Any], payload: AccountInspectionRequest) -> dict[str, Any]:
    started = time.time()
    account_id = str(account.get("id") or "")
    email = str(account.get("email") or "")
    try:
        refreshed = _refresh_account_tokens(account)
    except HTTPException as exc:
        detail = str(exc.detail or "")
        return _codex_account_failure_result(
            account_id=account_id,
            email=email,
            started=started,
            status_code=401 if detail.startswith("refresh_token_failed:") else int(exc.status_code or 0),
            error=detail or "refresh_token_failed",
        )
    access_token = str(refreshed.get("access_token") or "").strip()
    if not access_token:
        return _codex_account_failure_result(
            account_id=account_id,
            email=email,
            started=started,
            status_code=0,
            error="account_has_no_access_token",
        )
    body = _codex_account_test_body(payload)
    try:
        response = _post_codex_account_test(access_token, payload, body)
    except requests.RequestException as exc:
        return _codex_account_failure_result(account_id=account_id, email=email, started=started, status_code=0, error=str(exc))
    data = _response_json_dict(response)
    return {
        "ok": 200 <= int(response.status_code or 0) < 300,
        "account_id": account_id,
        "email": email,
        "model": body["model"],
        "status_code": int(response.status_code or 0),
        "latency_ms": _inspection_latency_ms(started),
        "text": _response_output_text(data),
        "error": _codex_response_error_text(response, data),
    }


def _inspection_needs_reauthorize(result: dict[str, Any]) -> bool:
    if int(result.get("status_code") or 0) == 401:
        return True
    text = str(result.get("error") or "").lower()
    return "401" in text and ("unauthorized" in text or "invalid" in text)


def _message_requires_delete_mark(message: Any) -> bool:
    text = str(message or "").lower()
    if "manual_phone_verification_required" in text:
        return True
    if "phone_verification_required" in text:
        return True
    return False


def _mark_account_delete_pending(account: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    item = dict(account or {})
    if not item.get("id"):
        return item
    tags = [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()]
    if "待删除" not in tags:
        tags.append("待删除")
    item["status"] = "delete_pending"
    item["last_error"] = reason or "manual_phone_verification_required"
    item["tags"] = tags
    item["usable_for_reauth"] = False
    return upsert_account(item)


def _inspection_account_ids(payload: AccountInspectionRequest) -> list[str]:
    return account_inspection_targets.inspection_account_ids(payload.account_ids, payload.account_id)


def _accounts_for_inspection(payload: AccountInspectionRequest) -> list[dict[str, Any]]:
    return account_inspection_targets.accounts_for_inspection(
        account_ids=payload.account_ids,
        account_id=payload.account_id,
        get_account=get_account,
        count_accounts=count_accounts,
        list_accounts=list_accounts,
    )


def _inspection_item_from_result(account: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return inspection_item_from_result(account, result)


def _summarize_inspection_items(items: list[dict[str, Any]]) -> AccountInspectionSummary:
    return summarize_inspection_items(items)


def _run_account_inspection(payload: AccountInspectionRequest, sms_values: dict[str, Any]) -> dict[str, Any]:
    return run_account_inspection(
        payload,
        sms_values,
        AccountInspectionRunDeps(
            inspection_account_ids=_inspection_account_ids,
            accounts_for_inspection=_accounts_for_inspection,
            inspection_accounts_from_cpa_auth_files=_inspection_accounts_from_cpa_auth_files,
            cpa_auth_files=_cpa_auth_files,
            cpa_auth_test=_cpa_auth_test,
            codex_account_test=_codex_account_test,
            inspection_item_from_result=_inspection_item_from_result,
            summarize_inspection_items=_summarize_inspection_items,
            inspection_needs_reauthorize=_inspection_needs_reauthorize,
            message_requires_delete_mark=_message_requires_delete_mark,
            mark_account_delete_pending=_mark_account_delete_pending,
            get_account=get_account,
            auto_reauthorize_account_with_email_otp=auto_reauthorize_account_with_email_otp,
            prefer_proxy=_prefer_proxy,
            prefer_codex2api_url=_prefer_codex2api_url,
            prefer_codex2api_admin_key=_prefer_codex2api_admin_key,
            prefer_codex2api_proxy_url=_prefer_codex2api_proxy_url,
            zh_job_message=_zh_job_message,
        ),
    )


def _resolve_cpa_action_target(payload: AccountInspectionCpaActionRequest, auth_files: list[dict[str, Any]]) -> dict[str, str]:
    return account_inspection_cpa_actions.resolve_cpa_action_target(
        account_id=payload.account_id,
        auth_index=payload.auth_index,
        name=payload.name,
        auth_files=auth_files,
        get_account=get_account,
        account_cpa_auth_file=_account_cpa_auth_file,
    )


CpaAuthActionContext = account_inspection_cpa_actions.CpaAuthActionContext


def _normalize_cpa_auth_action(action: Any) -> str:
    return account_inspection_cpa_actions.normalize_cpa_auth_action(action)


def _cpa_auth_action_context(payload: AccountInspectionCpaActionRequest) -> CpaAuthActionContext:
    cpa_url = _prefer_codex2api_url(payload.codex2api_url)
    cpa_key = _prefer_codex2api_admin_key(payload.codex2api_admin_key)
    auth_files = _cpa_auth_files(cpa_url, cpa_key)
    return account_inspection_cpa_actions.cpa_auth_action_context(
        action=payload.action,
        account_id=payload.account_id,
        auth_index=payload.auth_index,
        name=payload.name,
        cpa_url=cpa_url,
        cpa_key=cpa_key,
        auth_files=auth_files,
        get_account=get_account,
        account_cpa_auth_file=_account_cpa_auth_file,
    )


def _sync_local_account_after_cpa_status(account_id: str, action: str) -> None:
    account_inspection_cpa_actions.sync_local_account_after_cpa_status(
        account_id=account_id,
        action=action,
        get_account=get_account,
        upsert_account=upsert_account,
    )


def _run_cpa_auth_status_action(context: CpaAuthActionContext, payload: AccountInspectionCpaActionRequest) -> dict[str, Any]:
    return account_inspection_cpa_actions.run_cpa_auth_status_action(
        context=context,
        account_id=payload.account_id,
        cpa_request=_cpa_request,
        sync_local_status=_sync_local_account_after_cpa_status,
    )


def _run_cpa_auth_delete_action(context: CpaAuthActionContext, payload: AccountInspectionCpaActionRequest) -> dict[str, Any]:
    return account_inspection_cpa_actions.run_cpa_auth_delete_action(
        context=context,
        account_id=payload.account_id,
        cpa_request=_cpa_request,
        delete_account=delete_account,
    )


def _run_cpa_auth_action(payload: AccountInspectionCpaActionRequest) -> dict[str, Any]:
    context = _cpa_auth_action_context(payload)
    if context.action in {"enable", "disable"}:
        return _run_cpa_auth_status_action(context, payload)
    return _run_cpa_auth_delete_action(context, payload)
