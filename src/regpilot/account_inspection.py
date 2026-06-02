from __future__ import annotations

import json
import math
import re
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .accounts_store import count_accounts, delete_account, get_account, list_accounts, upsert_account
from .register_core import _decode_jwt_payload
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
    base = str(value or "").strip().rstrip("/")
    return re.sub(r"/?v0/management/?$", "", base, flags=re.IGNORECASE)


def _cpa_headers(admin_key: str) -> dict[str, str]:
    key = str(admin_key or "").strip()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["X-Management-Key"] = key
    return headers


def _cpa_request(method: str, base_url: str, admin_key: str, path: str, *, json_body: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    url = _cpa_base_url(base_url)
    if not url:
        raise HTTPException(status_code=400, detail="codex2api_url_required")
    key = str(admin_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="codex2api_admin_key_required")
    target = url + path
    try:
        response = requests.request(
            method.upper(),
            target,
            headers=_cpa_headers(key),
            json=json_body,
            timeout=max(1, int(timeout or 30)),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"cpa_request_failed:{exc}") from exc
    try:
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if response.status_code >= 400:
        detail = str(data.get("error") or data.get("message") or getattr(response, "text", "") or response.status_code).strip()
        raise HTTPException(status_code=400, detail=f"cpa_http_{response.status_code}:{detail}")
    return data


def _cpa_auth_files(base_url: str, admin_key: str, timeout: int = 30) -> list[dict[str, Any]]:
    data = _cpa_request("GET", base_url, admin_key, "/v0/management/auth-files", timeout=timeout)
    files = data.get("files") if isinstance(data.get("files"), list) else []
    return [item for item in files if isinstance(item, dict) and _is_inspectable_cpa_auth_file(item)]


def _is_inspectable_cpa_auth_file(auth_file: dict[str, Any]) -> bool:
    for key in ("name", "id", "path", "file", "file_name", "filename"):
        value = str(auth_file.get(key) or "").strip().replace("\\", "/")
        if value and value.rsplit("/", 1)[-1].lower() == "usage-stats.json":
            return False
    return True


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _cpa_auth_file_disabled(auth_file: dict[str, Any]) -> bool:
    status = str(auth_file.get("status") or auth_file.get("state") or "").strip().lower()
    return _truthy_flag(auth_file.get("disabled")) or status in {"disabled", "inactive"}


def _cpa_auth_provider(auth_file: dict[str, Any]) -> str:
    raw = str(auth_file.get("provider") or auth_file.get("type") or auth_file.get("typo") or "").strip().lower()
    key = raw.replace("_", "-")
    if key in {"x-ai", "grok"}:
        return "xai"
    return key


def _cpa_codex_account_id(auth_file: dict[str, Any]) -> str:
    def direct_candidate(value: Any) -> str:
        if isinstance(value, dict):
            for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
        return ""

    for value in (
        auth_file,
        auth_file.get("metadata") if isinstance(auth_file.get("metadata"), dict) else None,
        auth_file.get("attributes") if isinstance(auth_file.get("attributes"), dict) else None,
    ):
        candidate = direct_candidate(value)
        if candidate:
            return candidate

    for value in (
        auth_file.get("id_token"),
        (auth_file.get("metadata") or {}).get("id_token") if isinstance(auth_file.get("metadata"), dict) else None,
        (auth_file.get("attributes") or {}).get("id_token") if isinstance(auth_file.get("attributes"), dict) else None,
    ):
        if isinstance(value, dict):
            candidate = direct_candidate(value)
            if candidate:
                return candidate
        if isinstance(value, str) and value.strip():
            try:
                payload = _decode_jwt_payload(value)
            except Exception:
                payload = {}
            candidate = direct_candidate(payload if isinstance(payload, dict) else {})
            if candidate:
                return candidate
    return ""


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


def _usage_limit_reached_message(text: Any) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    return any(
        marker in value
        for marker in (
            "usage_limit_reached",
            "the usage limit has been reached",
            "insufficient_quota",
            "rate_limit_exceeded",
            "quota exhausted",
            "quota exceeded",
        )
    )


def _cpa_auth_quota_exceeded(auth_file: dict[str, Any], model: str = "") -> bool:
    def quota_exceeded(node: Any) -> bool:
        return isinstance(node, dict) and _truthy_flag(node.get("exceeded"))

    if quota_exceeded(auth_file.get("quota")):
        return True
    states = auth_file.get("model_states")
    if not isinstance(states, dict):
        return False
    candidates = [model]
    base_model = re.sub(r"[-:]?\d{4}.*$", "", model).strip("-_:") if model else ""
    if base_model and base_model not in candidates:
        candidates.append(base_model)
    for key in candidates:
        state = states.get(key) if key else None
        if isinstance(state, dict) and quota_exceeded(state.get("quota")):
            return True
    return False


def _account_cpa_auth_file(account: dict[str, Any], auth_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    direct = account.get("_cpa_auth_file")
    if isinstance(direct, dict):
        return direct
    email = str(account.get("email") or "").strip().lower()
    account_id = str(account.get("id") or "").strip().lower()
    if not email and not account_id:
        return None
    mailbox = account.get("mailbox") if isinstance(account.get("mailbox"), dict) else {}
    candidates = {
        email,
        str(mailbox.get("email") or "").strip().lower(),
        str(mailbox.get("bind_email") or "").strip().lower(),
        account_id,
    }
    candidates = {item for item in candidates if item}
    normalized_email = re.sub(r"[^a-z0-9]+", "_", email).strip("_") if email else ""
    for item in auth_files:
        fields = {
            str(item.get("email") or "").strip().lower(),
            str(item.get("account") or "").strip().lower(),
            str(item.get("id") or "").strip().lower(),
            str(item.get("name") or "").strip().lower(),
            str(item.get("auth_index") or "").strip().lower(),
        }
        if fields & candidates:
            return item
        haystack = " ".join(fields)
        if email and email in haystack:
            return item
        if normalized_email and normalized_email in re.sub(r"[^a-z0-9]+", "_", haystack).strip("_"):
            return item
    return None


def _cpa_auth_file_display_email(auth_file: dict[str, Any]) -> str:
    for key in ("email", "account", "label", "name", "id", "auth_index"):
        value = str(auth_file.get(key) or "").strip()
        if value:
            return value
    return ""


def _inspection_accounts_from_cpa_auth_files(auth_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = count_accounts()
    local_accounts = list_accounts(limit=max(1, min(10000, total or 10000)), offset=0)
    targets: list[dict[str, Any]] = []
    used_local_ids: set[str] = set()
    for auth_file in auth_files:
        provider = _cpa_auth_provider(auth_file)
        if provider and provider != "codex":
            continue
        matched = next((account for account in local_accounts if _account_cpa_auth_file(account, [auth_file])), None)
        if matched:
            account = dict(matched)
            used_local_ids.add(str(account.get("id") or ""))
        else:
            auth_index = str(auth_file.get("auth_index") or auth_file.get("id") or auth_file.get("name") or "").strip()
            account = {
                "id": "",
                "email": _cpa_auth_file_display_email(auth_file) or auth_index,
                "status": "cpa_only",
                "source": "cpa",
                "mailbox": {},
                "tags": [],
                "usable_for_reauth": False,
            }
        account["_cpa_auth_file"] = auth_file
        targets.append(account)
    return targets


CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_INSPECTION_USER_AGENT = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"


def _cpa_api_call_error_message(result: dict[str, Any]) -> str:
    status_code = int(result.get("status_code", result.get("statusCode", 0)) or 0)
    body = result.get("body")
    message = ""
    if isinstance(body, str):
        text = body.strip()
        try:
            parsed = json.loads(text) if text else None
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            body = parsed
        elif text:
            message = text
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "").strip()
        elif isinstance(error, str):
            message = error.strip()
        if not message:
            message = str(body.get("message") or "").strip()
    if not message:
        message = str(result.get("bodyText") or "").strip()
    if status_code and message:
        return f"{status_code} {message}".strip()
    if status_code:
        return f"HTTP {status_code}"
    return message or "Request failed"


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
    raw_status = data.get("status_code", data.get("statusCode"))
    has_status = raw_status is not None and str(raw_status).strip() != ""
    status_code = int(raw_status or 0)
    body = data.get("body")
    body_text = str(data.get("bodyText") or "")
    parsed = None
    if isinstance(body, dict):
        parsed = body
        body_text = json.dumps(body, ensure_ascii=False)
    elif isinstance(body, str) and body.strip():
        body_text = body
        try:
            decoded = json.loads(body)
            parsed = decoded if isinstance(decoded, dict) else None
        except Exception:
            parsed = None
    return {"status_code": status_code, "has_status_code": has_status, "payload": parsed, "body_text": body_text, "error": _cpa_api_call_error_message(data)}


def _cpa_auth_test(account: dict[str, Any], payload: AccountInspectionRequest, auth_files: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.time()
    account_id = str(account.get("id") or "")
    email = str(account.get("email") or "")
    auth_file = _account_cpa_auth_file(account, auth_files)
    if not auth_file:
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": "cpa_auth_file_not_found",
            "action": "cpa_auth_missing",
        }
    auth_index = str(auth_file.get("auth_index") or "")
    auth_name = str(auth_file.get("name") or "")
    was_disabled = _cpa_auth_file_disabled(auth_file)
    try:
        probe = _cpa_codex_usage_probe(account, payload, auth_file)
    except HTTPException as exc:
        error_text = str(exc.detail or exc)
        status_code = 0
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "auth_index": auth_index,
            "auth_name": auth_name,
            "auth_disabled": was_disabled,
            "status_code": status_code,
            "latency_ms": int((time.time() - started) * 1000),
            "error": error_text,
            "action": "cpa_probe_failed",
            "usage_state": "unknown",
            "recommended_action": "",
            "inspection_source": "cpa_quota",
        }
    if not probe.get("has_status_code"):
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "auth_index": auth_index,
            "auth_name": auth_name,
            "auth_disabled": was_disabled,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": "response_missing_status_code",
            "action": "cpa_probe_failed",
            "usage_state": "unknown",
            "recommended_action": "",
            "inspection_source": "cpa_quota",
        }
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
    usage_message = f"weekly_used={_percent_label(weekly_used_percent)}; five_hour_used={_percent_label(five_hour_used_percent)}"
    return {
        "ok": action in {"cpa_keep", "cpa_usage_available"} and not error_text,
        "account_id": account_id,
        "email": email,
        "auth_index": str(auth_file.get("auth_index") or ""),
        "auth_name": str(auth_file.get("name") or ""),
        "auth_disabled": was_disabled,
        "status_code": status_code,
        "latency_ms": int((time.time() - started) * 1000),
        "used_percent": None if used_percent is None else round(float(used_percent), 3),
        "weekly_used_percent": None if weekly_used_percent is None else round(float(weekly_used_percent), 3),
        "five_hour_used_percent": None if five_hour_used_percent is None else round(float(five_hour_used_percent), 3),
        "text": usage_message,
        "error": error_text,
        "action": action,
        "usage_state": usage_state,
        "recommended_action": recommended_action,
        "inspection_source": "cpa_quota",
    }


def _codex_account_test(account: dict[str, Any], payload: AccountInspectionRequest) -> dict[str, Any]:
    started = time.time()
    account_id = str(account.get("id") or "")
    email = str(account.get("email") or "")
    try:
        refreshed = _refresh_account_tokens(account)
    except HTTPException as exc:
        detail = str(exc.detail or "")
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "status_code": 401 if detail.startswith("refresh_token_failed:") else int(exc.status_code or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": detail or "refresh_token_failed",
        }
    access_token = str(refreshed.get("access_token") or "").strip()
    if not access_token:
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": "account_has_no_access_token",
        }
    proxy = _prefer_proxy(payload.proxy)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    body = {
        "model": str(payload.model or "").strip() or "gpt-5.5",
        "input": str(payload.prompt or "").strip() or "Reply exactly with: CPA_AUTH_TEST_OK",
        "stream": False,
        "max_output_tokens": 32,
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=body,
            timeout=max(1, int(payload.request_timeout or 30)),
            proxies=proxies,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }
    try:
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    error_text = str(error.get("message") or error.get("code") or "").strip() if isinstance(error, dict) else ""
    if not error_text and response.status_code >= 400:
        error_text = str(getattr(response, "text", "") or "").strip()[:500]
    return {
        "ok": 200 <= int(response.status_code or 0) < 300,
        "account_id": account_id,
        "email": email,
        "model": body["model"],
        "status_code": int(response.status_code or 0),
        "latency_ms": int((time.time() - started) * 1000),
        "text": _response_output_text(data),
        "error": error_text,
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
    ids = [str(item).strip() for item in (payload.account_ids or []) if str(item).strip()]
    single = str(payload.account_id or "").strip()
    if single and single not in ids:
        ids.append(single)
    return ids


def _accounts_for_inspection(payload: AccountInspectionRequest) -> list[dict[str, Any]]:
    ids = _inspection_account_ids(payload)
    if ids:
        return [item for item in (get_account(account_id) for account_id in ids) if item]
    total = count_accounts()
    return list_accounts(limit=max(1, min(10000, total or 10000)), offset=0)


def _run_account_inspection(payload: AccountInspectionRequest, sms_values: dict[str, Any]) -> dict[str, Any]:
    selected_ids = _inspection_account_ids(payload)
    accounts_to_check: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    checked_count = 0
    ok_count = 0
    failed_count = 0
    unauthorized_count = 0
    reauthorized_count = 0
    delete_marked_count = 0
    auth_files: list[dict[str, Any]] = []
    use_cpa_test = bool(payload.use_cpa_test)
    if use_cpa_test:
        cpa_url = _prefer_codex2api_url(payload.codex2api_url)
        cpa_key = _prefer_codex2api_admin_key(payload.codex2api_admin_key)
        print(f"阶段：巡检使用 CPA 测试接口：{cpa_url or '-'}")
        try:
            auth_files = _cpa_auth_files(cpa_url, cpa_key, timeout=max(1, int(payload.request_timeout or 30)))
            print(f"阶段：已读取 CPA auth files：{len(auth_files)} 个")
        except HTTPException as exc:
            message = str(exc.detail or "cpa_auth_files_load_failed")
            print(f"阶段：读取 CPA auth files 失败：{message}")
            return {"ok": False, "message": message, "checked_count": 0, "items": []}
        accounts_to_check = _accounts_for_inspection(payload) if selected_ids else _inspection_accounts_from_cpa_auth_files(auth_files)
    else:
        accounts_to_check = _accounts_for_inspection(payload)
    target_source = "selected_accounts" if selected_ids else ("cpa_auth_files" if use_cpa_test else "account_pool")
    print(f"阶段：账户巡检开始，目标来源 {target_source}，目标 {len(accounts_to_check)} 个")
    if not accounts_to_check:
        return {"ok": True, "message": "no_accounts", "checked_count": 0, "items": [], "target_source": target_source}
    workers = max(1, min(50, int(payload.threads or 1)))
    reauthorize_lock = Lock()

    def inspect_one(account: dict[str, Any]) -> dict[str, Any]:
        account_id = str(account.get("id") or "")
        email = str(account.get("email") or "").strip() or account_id
        print(f"阶段：巡检账号：{email}")
        try:
            result = _cpa_auth_test(account, payload, auth_files) if use_cpa_test else _codex_account_test(account, payload)
        except HTTPException as exc:
            result = {
                "ok": False,
                "account_id": account_id,
                "email": email,
                "status_code": 0,
                "latency_ms": 0,
                "error": str(exc.detail or exc),
            }
        item = {
            "account_id": account_id,
            "email": email,
            "ok": bool(result.get("ok")),
            "auth_index": str(result.get("auth_index") or ""),
            "auth_name": str(result.get("auth_name") or ""),
            "auth_disabled": bool(result.get("auth_disabled")),
            "usage_state": str(result.get("usage_state") or ""),
            "recommended_action": str(result.get("recommended_action") or ""),
            "used_percent": result.get("used_percent"),
            "weekly_used_percent": result.get("weekly_used_percent"),
            "five_hour_used_percent": result.get("five_hour_used_percent"),
            "status_code": int(result.get("status_code") or 0),
            "latency_ms": int(result.get("latency_ms") or 0),
            "message": str(result.get("error") or result.get("text") or ""),
            "action": str(result.get("action") or "checked"),
        }
        if result.get("ok"):
            print(f"阶段：巡检通过：{email}（HTTP {item['status_code']}）")
            return item
        if not _inspection_needs_reauthorize(result):
            if item["action"] == "checked":
                item["action"] = "failed_no_reauthorize"
            print(f"阶段：巡检失败但非 401，不触发重新授权：{email}（HTTP {item['status_code']}）")
            return item
        if not account_id:
            item["action"] = "cpa_unauthorized_no_local_account"
            item["message"] = "CPA auth file returned 401, but no matching RegPilot account was found for automatic reauthorization"
            print(f"阶段：巡检发现 401，但未匹配到本地账号，无法自动重新授权：{email}")
            return item
        item["action"] = "reauthorize_started"
        print(f"阶段：巡检发现 401，开始重新授权：{email}")
        with reauthorize_lock:
            print(f"阶段：巡检重新授权进入串行队列：{email}")
            outcome = auto_reauthorize_account_with_email_otp(
                account_id,
                codex2api_url=_prefer_codex2api_url(payload.codex2api_url),
                codex2api_admin_key=_prefer_codex2api_admin_key(payload.codex2api_admin_key),
                codex2api_proxy_url=_prefer_codex2api_proxy_url(payload.codex2api_proxy_url),
                proxy=_prefer_proxy(payload.proxy),
                wait_timeout=payload.wait_timeout,
                wait_interval=payload.wait_interval,
                request_timeout=payload.request_timeout,
                allow_phone_verification=bool(payload.allow_phone_verification),
                **sms_values,
            )
        item["reauthorize_ok"] = bool(outcome.ok)
        item["reauthorize_message"] = str(outcome.message or "")
        item["message"] = str(outcome.message or item["message"])
        if outcome.ok:
            item["action"] = "reauthorized"
            print(f"阶段：重新授权完成：{email}")
        elif _message_requires_delete_mark(outcome.message):
            marked = _mark_account_delete_pending(outcome.account or get_account(account_id), str(outcome.message or "manual_phone_verification_required"))
            item["action"] = "delete_pending"
            item["status"] = str(marked.get("status") or "")
            print(f"阶段：重新授权提示需要手机二次验证，已标记待删除：{email}")
        else:
            item["action"] = "reauthorize_failed"
            print(f"阶段：重新授权失败，未标记删除：{email}：{_zh_job_message(outcome.message)}")
        return item

    if workers == 1 or len(accounts_to_check) <= 1:
        items = [inspect_one(account) for account in accounts_to_check]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(inspect_one, account) for account in accounts_to_check]
            for future in as_completed(futures):
                items.append(future.result())

    checked_count = len(items)
    neutral_actions = {"cpa_auth_disabled", "cpa_keep"}
    for item in items:
        if item.get("ok") or item.get("action") == "reauthorized":
            ok_count += 1
        elif item.get("action") not in neutral_actions:
            failed_count += 1
        if item.get("action") in {"reauthorized", "reauthorize_failed", "delete_pending"} or int(item.get("status_code") or 0) == 401:
            unauthorized_count += 1
        if item.get("action") == "reauthorized":
            reauthorized_count += 1
        if item.get("action") == "delete_pending":
            delete_marked_count += 1
    print(f"阶段：账户巡检结束：通过 {ok_count}，失败 {failed_count}，401 {unauthorized_count}，重授权 {reauthorized_count}，待删除 {delete_marked_count}")
    return {
        "ok": True,
        "message": "account_inspection_finished",
        "checked_count": checked_count,
        "target_source": target_source,
        "threads": workers,
        "use_cpa_test": use_cpa_test,
        "ok_count": ok_count,
        "failed_count": failed_count,
        "unauthorized_count": unauthorized_count,
        "reauthorized_count": reauthorized_count,
        "delete_marked_count": delete_marked_count,
        "items": items,
    }


def _resolve_cpa_action_target(payload: AccountInspectionCpaActionRequest, auth_files: list[dict[str, Any]]) -> dict[str, str]:
    auth_index = str(payload.auth_index or "").strip()
    name = str(payload.name or "").strip()
    if auth_index or name:
        return {"auth_index": auth_index, "name": name}
    account = get_account(payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account_not_found")
    auth_file = _account_cpa_auth_file(account, auth_files)
    if not auth_file:
        raise HTTPException(status_code=400, detail="cpa_auth_file_not_found")
    return {
        "auth_index": str(auth_file.get("auth_index") or ""),
        "name": str(auth_file.get("name") or ""),
    }


def _run_cpa_auth_action(payload: AccountInspectionCpaActionRequest) -> dict[str, Any]:
    action = str(payload.action or "").strip().lower()
    if action not in {"enable", "disable", "delete"}:
        raise HTTPException(status_code=400, detail="invalid_cpa_auth_action")
    cpa_url = _prefer_codex2api_url(payload.codex2api_url)
    cpa_key = _prefer_codex2api_admin_key(payload.codex2api_admin_key)
    auth_files = _cpa_auth_files(cpa_url, cpa_key)
    target = _resolve_cpa_action_target(payload, auth_files)
    name = target.get("name") or target.get("auth_index") or ""
    auth_index = target.get("auth_index") or ""
    if action in {"enable", "disable"}:
        body = {"name": name or auth_index, "disabled": action == "disable"}
        data = _cpa_request("PATCH", cpa_url, cpa_key, "/v0/management/auth-files/status", json_body=body)
        if payload.account_id:
            account = get_account(payload.account_id)
            if account:
                updated = dict(account)
                updated["status"] = "cpa_disabled" if action == "disable" else "authorized"
                updated["last_error"] = "cpa_auth_disabled" if action == "disable" else ""
                upsert_account(updated)
        return {"ok": True, "action": action, "auth_index": auth_index, "name": name, "result": data}
    delete_name = name or auth_index
    if not delete_name:
        raise HTTPException(status_code=400, detail="cpa_auth_file_not_found")
    data = _cpa_request("DELETE", cpa_url, cpa_key, f"/v0/management/auth-files?name={quote(delete_name)}")
    local_account_deleted = False
    if payload.account_id:
        local_account_deleted = delete_account(payload.account_id)
    return {
        "ok": True,
        "action": action,
        "auth_index": auth_index,
        "name": name,
        "result": data,
        "local_account_deleted": local_account_deleted,
    }
