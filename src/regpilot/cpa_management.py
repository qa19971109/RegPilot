from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests
from fastapi import HTTPException

from .jwt_utils import decode_jwt_payload


__all__ = [
    "account_cpa_auth_file",
    "cpa_auth_files",
    "cpa_auth_file_disabled",
    "cpa_auth_file_display_email",
    "cpa_auth_provider",
    "cpa_api_call_error_message",
    "cpa_api_call_probe_result",
    "cpa_base_url",
    "cpa_codex_account_id",
    "cpa_headers",
    "cpa_inspection_accounts_from_auth_files",
    "cpa_request",
    "is_inspectable_cpa_auth_file",
]


def cpa_base_url(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    return re.sub(r"/?v0/management/?$", "", base, flags=re.IGNORECASE)


def cpa_headers(admin_key: str) -> dict[str, str]:
    key = str(admin_key or "").strip()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["X-Management-Key"] = key
    return headers


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def cpa_auth_file_disabled(auth_file: dict[str, Any]) -> bool:
    status = str(auth_file.get("status") or auth_file.get("state") or "").strip().lower()
    return _truthy_flag(auth_file.get("disabled")) or status in {"disabled", "inactive"}


def cpa_auth_provider(auth_file: dict[str, Any]) -> str:
    raw = str(auth_file.get("provider") or auth_file.get("type") or auth_file.get("typo") or "").strip().lower()
    key = raw.replace("_", "-")
    if key in {"x-ai", "grok"}:
        return "xai"
    return key


def cpa_codex_account_id(auth_file: dict[str, Any]) -> str:
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
                payload = decode_jwt_payload(value)
            except Exception:
                payload = {}
            candidate = direct_candidate(payload if isinstance(payload, dict) else {})
            if candidate:
                return candidate
    return ""


def account_cpa_auth_file(account: dict[str, Any], auth_files: list[dict[str, Any]]) -> dict[str, Any] | None:
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


def cpa_auth_file_display_email(auth_file: dict[str, Any]) -> str:
    for key in ("email", "account", "label", "name", "id", "auth_index"):
        value = str(auth_file.get(key) or "").strip()
        if value:
            return value
    return ""


def cpa_inspection_accounts_from_auth_files(auth_files: list[dict[str, Any]], local_accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for auth_file in auth_files:
        provider = cpa_auth_provider(auth_file)
        if provider and provider != "codex":
            continue
        matched = next((account for account in local_accounts if account_cpa_auth_file(account, [auth_file])), None)
        if matched:
            account = dict(matched)
        else:
            auth_index = str(auth_file.get("auth_index") or auth_file.get("id") or auth_file.get("name") or "").strip()
            account = {
                "id": "",
                "email": cpa_auth_file_display_email(auth_file) or auth_index,
                "status": "cpa_only",
                "source": "cpa",
                "mailbox": {},
                "tags": [],
                "usable_for_reauth": False,
            }
        account["_cpa_auth_file"] = auth_file
        targets.append(account)
    return targets


def cpa_api_call_error_message(result: dict[str, Any]) -> str:
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


def cpa_api_call_probe_result(data: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "status_code": status_code,
        "has_status_code": has_status,
        "payload": parsed,
        "body_text": body_text,
        "error": cpa_api_call_error_message(data),
    }


def cpa_request(method: str, base_url: str, admin_key: str, path: str, *, json_body: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    url = cpa_base_url(base_url)
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
            headers=cpa_headers(key),
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


def is_inspectable_cpa_auth_file(auth_file: dict[str, Any]) -> bool:
    for key in ("name", "id", "path", "file", "file_name", "filename"):
        value = str(auth_file.get(key) or "").strip().replace("\\", "/")
        if value and value.rsplit("/", 1)[-1].lower() == "usage-stats.json":
            return False
    return True


def cpa_auth_files(
    base_url: str,
    admin_key: str,
    timeout: int = 30,
    *,
    request_fn: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    request = request_fn or cpa_request
    data = request("GET", base_url, admin_key, "/v0/management/auth-files", timeout=timeout)
    files = data.get("files") if isinstance(data.get("files"), list) else []
    return [item for item in files if isinstance(item, dict) and is_inspectable_cpa_auth_file(item)]
