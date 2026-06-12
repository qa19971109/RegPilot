from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse


@dataclass(frozen=True)
class HotmailApiDeps:
    request: Callable[..., Any]
    first_non_empty: Callable[..., str]
    record_code_match: Callable[..., None]
    to_timestamp_ms: Callable[[Any], int]


def normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if re.match(r"^https?:/[^/]", value):
        scheme, rest = value.split(":/", 1)
        value = f"{scheme}://{rest}"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"invalid_hotmail_api_base_url: {value}")
    return value.rstrip("/")


def split_csv(value: Any, default: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,\n]+", str(value or ""))
    out = [str(item or "").strip() for item in items if str(item or "").strip()]
    return out or list(default or [])


def create_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30) -> dict[str, Any]:
    from . import microsoft_mail_pool

    base_url = normalize_base_url(str(provider.get("base_url") or provider.get("baseUrl") or ""))
    if not base_url:
        raise RuntimeError("Hotmail/Outlook 邮箱服务未配置 API 地址")
    alias_enabled = bool(provider.get("alias_enabled") or provider.get("aliasEnabled"))
    alias_max = max(1, min(50, int(provider.get("alias_max_per_account") or provider.get("aliasMaxPerAccount") or 5)))
    account = microsoft_mail_pool.claim_account(alias_enabled=alias_enabled, alias_max=alias_max)
    return {
        "provider": "hotmail-api",
        "email": str(account.get("registration_email") or account.get("email") or "").strip().lower(),
        "base_email": str(account.get("base_email") or account.get("email") or "").strip().lower(),
        "password": str(account.get("password") or ""),
        "client_id": str(account.get("client_id") or ""),
        "refresh_token": str(account.get("refresh_token") or ""),
        "microsoft_account_id": str(account.get("id") or ""),
        "base_url": base_url,
        "mailboxes": split_csv(provider.get("mailboxes"), ["INBOX", "Junk"]),
        "top": max(1, min(30, int(provider.get("top") or 10))),
        "sender_filters": split_csv(provider.get("sender_filters") or provider.get("senderFilters"), ["openai", "noreply", "no-reply"]),
        "subject_filters": split_csv(provider.get("subject_filters") or provider.get("subjectFilters"), ["code", "verification", "验证码"]),
        "required_keywords": split_csv(provider.get("required_keywords") or provider.get("requiredKeywords"), []),
        "alias_source": "outlook-plus" if alias_enabled else "microsoft-account",
        "request_timeout": request_timeout,
    }


def api_request(mailbox: dict[str, Any], path: str, payload: dict[str, Any], *, timeout: int = 30, deps: HotmailApiDeps) -> dict[str, Any]:
    base_url = normalize_base_url(str(mailbox.get("base_url") or ""))
    if not base_url:
        raise RuntimeError("Hotmail/Outlook 邮箱服务未配置 API 地址")
    response = deps.request(
        "POST",
        f"{base_url}{path if path.startswith('/') else '/' + path}",
        timeout=timeout,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=payload,
    )
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
        detail = ""
        if isinstance(data, dict):
            detail = deps.first_non_empty(data.get("error"), data.get("message"), data.get("detail"))
        raise RuntimeError(f"Hotmail/Outlook 邮箱 API 请求失败: {detail or response.text or response.status_code}")
    return data if isinstance(data, dict) else {}


def wait_for_code(mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30, *, deps: HotmailApiDeps) -> str | None:
    base_email = str(mailbox.get("base_email") or mailbox.get("email") or "").strip().lower()
    client_id = str(mailbox.get("client_id") or "").strip()
    refresh_token = str(mailbox.get("refresh_token") or "").strip()
    if not base_email or not client_id or not refresh_token:
        return None
    started = time.time()
    last_token = refresh_token
    while time.time() - started <= timeout:
        payload = {
            "email": base_email,
            "clientId": client_id,
            "refreshToken": last_token,
            "mailboxes": split_csv(mailbox.get("mailboxes"), ["INBOX", "Junk"]),
            "top": max(1, min(30, int(mailbox.get("top") or 10))),
            "senderFilters": split_csv(mailbox.get("sender_filters") or mailbox.get("senderFilters"), ["openai", "noreply", "no-reply"]),
            "subjectFilters": split_csv(mailbox.get("subject_filters") or mailbox.get("subjectFilters"), ["code", "verification", "验证码"]),
            "requiredKeywords": split_csv(mailbox.get("required_keywords") or mailbox.get("requiredKeywords"), []),
            "excludeCodes": [str(item) for item in (mailbox.get("_exclude_codes") or []) if item],
            "filterAfterTimestamp": int(mailbox.get("_code_after_ts") or 0),
        }
        data = api_request(mailbox, "/code", payload, timeout=request_timeout, deps=deps)
        next_token = str(data.get("nextRefreshToken") or "").strip()
        if next_token:
            last_token = next_token
            mailbox["refresh_token"] = next_token
        code = str(data.get("code") or "").strip()
        if code:
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            received_at_ms = deps.to_timestamp_ms(message.get("receivedTimestamp") or message.get("receivedDateTime"))
            preview = "\n".join(
                [
                    str(message.get("subject") or ""),
                    str(((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""),
                    str(message.get("bodyPreview") or ""),
                    str(((message.get("body") or {}).get("content") if isinstance(message.get("body"), dict) else "") or ""),
                ]
            )
            deps.record_code_match(
                mailbox,
                code=code,
                message_id=str(message.get("id") or ""),
                received_at_ms=received_at_ms,
                provider="hotmail-api",
                preview=preview,
            )
            return code
        time.sleep(max(1, interval))
    return None
