from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import DATA_DIR
from .json_store import write_json_atomic
from .jwt_utils import decode_jwt_payload
from .register_core import RegistrationResult, platform_oauth_client_id
from .registration_callback import extract_oauth_callback_params_from_url


DEFAULT_SUB2API_EXPORT_NAME = "export_accounts.json"
DEFAULT_ACCOUNT_ARCHIVE_NAME = "last_sub2api_account_archive.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def extract_account_identity(result: RegistrationResult, organization_id: str = "", plan_type: str = "free") -> dict[str, Any]:
    access_payload = decode_jwt_payload(result.access_token or "") or {}
    id_payload = decode_jwt_payload(result.id_token or "") or {}
    auth_payload = access_payload.get("https://api.openai.com/auth") or {}
    profile_payload = access_payload.get("https://api.openai.com/profile") or {}
    account_id = ""
    mailbox = result.mailbox or {}
    if isinstance(mailbox, dict):
        account_id = str(mailbox.get("account_id") or "").strip()
    return {
        "email": str(result.email or profile_payload.get("email") or id_payload.get("email") or "").strip(),
        "chatgpt_account_id": account_id,
        "chatgpt_user_id": str(auth_payload.get("user_id") or "").strip(),
        "client_id": str(access_payload.get("client_id") or platform_oauth_client_id).strip(),
        "expires_at": int(access_payload.get("exp") or 0),
        "organization_id": str(organization_id or "").strip(),
        "plan_type": str(plan_type or "free").strip() or "free",
        "name": str(id_payload.get("name") or "").strip(),
    }


def normalize_sub2api_origin(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            parsed = urlparse(f"http://{value}")
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return ""


def request_codex2api_json(
    codex2api_url: str,
    *,
    path: str,
    admin_key: str,
    method: str = "POST",
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    origin = normalize_sub2api_origin(codex2api_url)
    if not origin:
        raise ValueError("invalid_codex2api_url")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Admin-Key": str(admin_key or "").strip(),
    }
    response = requests.request(
        method.upper(),
        f"{origin}{path}",
        headers=headers,
        json=body,
        timeout=max(5, int(timeout or 30)),
        verify=False,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("message") or payload.get("detail") or payload.get("error") or payload.get("reason") or "").strip()
        raise RuntimeError(detail or f"codex2api_http_{response.status_code}")
    return payload


def extract_codex2api_account_id(data: Any, *, email: str = "", name: str = "") -> int:
    targets = {str(email or "").strip().lower(), str(name or "").strip().lower()} - {""}
    candidates: list[Any] = []
    if isinstance(data, dict):
        for key in ("account", "item", "data"):
            value = data.get(key)
            if isinstance(value, dict):
                candidates.append(value)
            elif isinstance(value, list):
                candidates.extend(value)
        for key in ("accounts", "items", "success"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        candidates.append(data)
    elif isinstance(data, list):
        candidates.extend(data)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id") or item.get("account_id")
        if not raw_id:
            continue
        item_email = str(item.get("email") or "").strip().lower()
        item_name = str(item.get("name") or "").strip().lower()
        if not targets or item_email in targets or item_name in targets:
            try:
                return int(raw_id)
            except Exception:
                continue
    return 0


def find_codex2api_account_id(codex2api_url: str, *, admin_key: str, email: str = "", name: str = "") -> int:
    data = request_codex2api_json(codex2api_url, path="/api/admin/accounts", admin_key=admin_key, method="GET", body=None)
    return extract_codex2api_account_id(data, email=email, name=name)


def refresh_codex2api_account(codex2api_url: str, *, admin_key: str, account_id: int) -> dict[str, Any]:
    if int(account_id or 0) <= 0:
        return {"ok": False, "message": "codex2api_account_id_missing"}
    data = request_codex2api_json(
        codex2api_url,
        path=f"/api/admin/accounts/{int(account_id)}/refresh",
        admin_key=admin_key,
        method="POST",
        body={},
        timeout=60,
    )
    return {"ok": True, "message": str((data or {}).get("message") or "Codex2API account refreshed"), "raw": data}


def import_result_to_codex2api(
    result: RegistrationResult,
    *,
    codex2api_url: str,
    admin_key: str,
    account_name: str = "",
    proxy_url: str = "",
) -> dict[str, Any]:
    refresh_token = str(result.refresh_token or "").strip()
    access_token = str(result.access_token or "").strip()
    identity = extract_account_identity(result)
    name = str(account_name or identity.get("email") or result.email or "codex-account").strip()
    if access_token:
        payload = {"name": name, "access_token": access_token}
        if str(proxy_url or "").strip():
            payload["proxy_url"] = str(proxy_url).strip()
        data = request_codex2api_json(
            codex2api_url,
            path="/api/admin/accounts/at",
            admin_key=admin_key,
            method="POST",
            body=payload,
        )
        return {
            "ok": True,
            "message": str((data or {}).get("message") or "Codex2API access token account imported"),
            "success": (data or {}).get("success"),
            "failed": (data or {}).get("failed"),
            "duplicate": (data or {}).get("duplicate"),
            "mode": "access_token",
            "raw": data,
        }
    if refresh_token:
        payload = {"name": name, "refresh_token": refresh_token}
        if str(proxy_url or "").strip():
            payload["proxy_url"] = str(proxy_url).strip()
        data = request_codex2api_json(
            codex2api_url,
            path="/api/admin/accounts",
            admin_key=admin_key,
            method="POST",
            body=payload,
        )
        refresh_result = {"ok": False, "message": "codex2api_refresh_not_attempted"}
        account_id = extract_codex2api_account_id(data, email=str(identity.get("email") or result.email or ""), name=name)
        if not account_id:
            account_id = find_codex2api_account_id(codex2api_url, admin_key=admin_key, email=str(identity.get("email") or result.email or ""), name=name)
        if account_id:
            refresh_result = refresh_codex2api_account(codex2api_url, admin_key=admin_key, account_id=account_id)
        return {
            "ok": bool(refresh_result.get("ok")),
            "message": str((refresh_result or {}).get("message") or (data or {}).get("message") or "Codex2API refresh token account imported"),
            "success": (data or {}).get("success"),
            "failed": (data or {}).get("failed"),
            "duplicate": (data or {}).get("duplicate"),
            "account_id": account_id,
            "refresh": refresh_result,
            "mode": "refresh_token",
            "raw": data,
        }
    raise RuntimeError("codex2api_token_missing")


def build_sub2api_import_payload(
    result: RegistrationResult,
    *,
    concurrency: int = 10,
    priority: int = 1,
    rate_multiplier: int = 1,
    auto_pause_on_expired: bool = True,
    organization_id: str = "",
    plan_type: str = "free",
    privacy_mode: str = "training_off",
    account_name: str = "",
) -> dict[str, Any]:
    identity = extract_account_identity(result, organization_id=organization_id, plan_type=plan_type)
    email = identity["email"]
    return {
        "exported_at": utc_now_iso(),
        "proxies": [],
        "accounts": [
            {
                "name": account_name or email,
                "platform": "openai",
                "type": "oauth",
                "credentials": {
                    "access_token": result.access_token,
                    "chatgpt_account_id": identity["chatgpt_account_id"],
                    "chatgpt_user_id": identity["chatgpt_user_id"],
                    "client_id": identity["client_id"],
                    "email": email,
                    "expires_at": identity["expires_at"],
                    "id_token": result.id_token,
                    "organization_id": identity["organization_id"],
                    "plan_type": identity["plan_type"],
                    "refresh_token": result.refresh_token,
                },
                "extra": {
                    "email": email,
                    "openai_oauth_responses_websockets_v2_enabled": False,
                    "openai_oauth_responses_websockets_v2_mode": "off",
                    "privacy_mode": privacy_mode,
                },
                "concurrency": int(concurrency),
                "priority": int(priority),
                "rate_multiplier": int(rate_multiplier),
                "auto_pause_on_expired": bool(auto_pause_on_expired),
            }
        ],
    }


def build_account_archive(
    prepared: Any,
    result: RegistrationResult,
    *,
    callback_url: str,
    phone_number: str = "",
    phone_country: str = "CO",
    hero_sms_order_id: str = "",
    hero_sms_price: float | None = None,
    email_password: str = "",
    mail_provider_name: str = "",
    organization_id: str = "",
    plan_type: str = "free",
) -> dict[str, Any]:
    identity = extract_account_identity(result, organization_id=organization_id, plan_type=plan_type)
    callback_params = extract_oauth_callback_params_from_url(callback_url) or {}
    return {
        "created_at": utc_now_iso(),
        "platform": "openai",
        "signup_method": "oauth_manual_or_browser_assist",
        "phone": {
            "country": phone_country,
            "phone_number": phone_number,
            "provider": "hero-sms" if phone_number or hero_sms_order_id else "",
            "order_id": hero_sms_order_id,
            "price": hero_sms_price,
        },
        "email": {
            "address": identity["email"],
            "password": email_password,
            "mail_provider": mail_provider_name,
        },
        "oauth": {
            "authorize_url": prepared.authorize_url,
            "callback_url": callback_url,
            "code": str(callback_params.get("code") or "").strip(),
            "state": prepared.state,
            "redirect_uri": prepared.redirect_uri,
            "client_id": prepared.client_id,
            "device_id": prepared.device_id,
        },
        "tokens": {
            "access_token": result.access_token,
            "refresh_token": result.refresh_token,
            "id_token": result.id_token,
        },
        "profile": {
            "chatgpt_user_id": identity["chatgpt_user_id"],
            "chatgpt_account_id": identity["chatgpt_account_id"],
            "client_id": identity["client_id"],
            "organization_id": identity["organization_id"],
            "expires_at": identity["expires_at"],
            "plan_type": identity["plan_type"],
            "name": identity["name"],
        },
    }


def save_sub2api_export(payload: dict[str, Any], filename: str = DEFAULT_SUB2API_EXPORT_NAME) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    write_json_atomic(path, payload)
    return path


def save_account_archive(archive: dict[str, Any], filename: str = DEFAULT_ACCOUNT_ARCHIVE_NAME) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    write_json_atomic(path, archive)
    return path
