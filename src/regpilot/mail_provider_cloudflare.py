from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CloudflareTempEmailDeps:
    normalize_base_url: Callable[[str], str]
    first_non_empty: Callable[..., str]
    request: Callable[..., Any]
    extract_text_from_raw_email: Callable[[str], str]
    extract_code: Callable[[str], str | None]
    record_code_match: Callable[..., None]
    to_timestamp_ms: Callable[[Any], int]
    random_local_part: Callable[[], str]


def normalize_domain(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    value = value.replace("@", "", 1) if value.startswith("@") else value
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0]
    return value if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", value, re.I) else ""


def headers(provider: dict[str, Any], *, json_body: bool = False, deps: CloudflareTempEmailDeps) -> dict[str, str]:
    out = {"Accept": "application/json"}
    admin_auth = deps.first_non_empty(provider.get("admin_auth"), provider.get("adminAuth"), provider.get("cloudflare_temp_email_admin_auth"))
    custom_auth = deps.first_non_empty(provider.get("custom_auth"), provider.get("customAuth"), provider.get("cloudflare_temp_email_custom_auth"))
    if admin_auth:
        out["x-admin-auth"] = admin_auth
    if custom_auth:
        out["x-custom-auth"] = custom_auth
    if json_body:
        out["Content-Type"] = "application/json"
    return out


def request(
    provider: dict[str, Any],
    method: str,
    path: str,
    *,
    timeout: int = 30,
    json_body: dict | None = None,
    params: dict | None = None,
    deps: CloudflareTempEmailDeps,
):
    base_url = deps.normalize_base_url(str(provider.get("base_url") or provider.get("baseUrl") or ""))
    if not base_url:
        raise RuntimeError("Cloudflare Temp Email 未配置 base_url")
    url = f"{base_url}{path if path.startswith('/') else '/' + path}"
    response = deps.request(
        method,
        url,
        timeout=timeout,
        headers=headers(provider, json_body=json_body is not None, deps=deps),
        json=json_body,
        params=params,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        if isinstance(payload, dict):
            detail = deps.first_non_empty(payload.get("message"), payload.get("error"), payload.get("msg"), response.text)
        else:
            detail = str(response.text or "").strip()
        raise RuntimeError(f"Cloudflare Temp Email 请求失败: {detail or f'HTTP {response.status_code}'}")
    return payload


def get_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "messages", "mails", "results", "rows"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def extract_text(row: dict[str, Any], *, deps: CloudflareTempEmailDeps) -> str:
    raw_message = deps.first_non_empty(row.get("raw"), row.get("source"), row.get("mime"), row.get("message"))
    parsed = deps.extract_text_from_raw_email(raw_message)
    if parsed:
        return parsed
    raw = deps.first_non_empty(row.get("text"), row.get("preview"), row.get("body"), raw_message)
    return re.sub(r"\s+", " ", raw).strip()


def create_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30, *, deps: CloudflareTempEmailDeps) -> dict[str, Any]:
    admin_auth = deps.first_non_empty(provider.get("admin_auth"), provider.get("adminAuth"), provider.get("cloudflare_temp_email_admin_auth"))
    if not admin_auth:
        raise RuntimeError("Cloudflare Temp Email 未配置 admin_auth")
    domain = normalize_domain(str(provider.get("domain") or ""))
    if not domain:
        raise RuntimeError("Cloudflare Temp Email 未配置有效 domain")
    requested_name = str(username or deps.random_local_part()).strip().lower()
    payload = {
        "enablePrefix": True,
        "enableRandomSubdomain": bool(provider.get("use_random_subdomain") or provider.get("useRandomSubdomain")),
        "name": requested_name,
        "domain": domain,
    }
    data = request(provider, "post", "/admin/new_address", json_body=payload, timeout=request_timeout, deps=deps)
    email = deps.first_non_empty(
        data.get("address"),
        data.get("email"),
        (data.get("data") or {}).get("address") if isinstance(data.get("data"), dict) else "",
        (data.get("data") or {}).get("email") if isinstance(data.get("data"), dict) else "",
    ).lower()
    if not email:
        raise RuntimeError(f"Cloudflare Temp Email 未返回可用邮箱地址: {data}")
    return {
        "provider": "cloudflare-temp-email",
        "email": email,
        "base_url": deps.normalize_base_url(str(provider.get("base_url") or provider.get("baseUrl") or "")),
        "admin_auth": admin_auth,
        "custom_auth": deps.first_non_empty(provider.get("custom_auth"), provider.get("customAuth"), provider.get("cloudflare_temp_email_custom_auth")),
        "domain": domain,
    }


def _mailbox_provider(mailbox: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": mailbox.get("base_url") or "",
        "admin_auth": mailbox.get("admin_auth") or "",
        "custom_auth": mailbox.get("custom_auth") or "",
    }


def _mail_row_time_value(row: dict[str, Any], deps: CloudflareTempEmailDeps) -> str:
    return deps.first_non_empty(
        row.get("receivedDateTime"),
        row.get("received_at"),
        row.get("created_at"),
        row.get("createdAt"),
        row.get("updated_at"),
        row.get("date"),
    )


def _sorted_mail_rows(payload: Any, deps: CloudflareTempEmailDeps) -> list[dict[str, Any]]:
    rows = get_rows(payload)
    rows.sort(key=lambda item: _mail_row_time_value(item, deps), reverse=True)
    return rows


def _new_mail_message_id(row: dict[str, Any], seen: set[str], deps: CloudflareTempEmailDeps) -> tuple[str, bool]:
    message_id = deps.first_non_empty(row.get("id"), row.get("mail_id"), row.get("mailId"))
    if message_id and message_id in seen:
        return message_id, False
    if message_id:
        seen.add(message_id)
    return message_id, True


def _mail_row_received_at_ms(row: dict[str, Any], deps: CloudflareTempEmailDeps) -> int:
    return deps.to_timestamp_ms(_mail_row_time_value(row, deps))


def _mail_row_matches_wait_window(row: dict[str, Any], email: str, after_ts_ms: int, deps: CloudflareTempEmailDeps) -> tuple[bool, int]:
    received_at_ms = _mail_row_received_at_ms(row, deps)
    if after_ts_ms and received_at_ms and received_at_ms < max(0, after_ts_ms - 2000):
        return False, received_at_ms
    row_email = deps.first_non_empty(row.get("address"), row.get("mail_address"), row.get("email"), row.get("recipient")).lower()
    if row_email and row_email != email:
        return False, received_at_ms
    return True, received_at_ms


def _mail_row_code_text(row: dict[str, Any], deps: CloudflareTempEmailDeps) -> str:
    return "\n".join(
        [
            deps.first_non_empty(row.get("subject")),
            deps.first_non_empty(row.get("from"), row.get("sender"), row.get("mail_from")),
            extract_text(row, deps=deps),
        ]
    )


def _code_from_mail_row(
    mailbox: dict[str, Any],
    row: dict[str, Any],
    *,
    message_id: str,
    received_at_ms: int,
    deps: CloudflareTempEmailDeps,
) -> str:
    text = _mail_row_code_text(row, deps)
    code = deps.extract_code(text)
    if code:
        deps.record_code_match(
            mailbox,
            code=code,
            message_id=message_id,
            received_at_ms=received_at_ms,
            provider="cloudflare-temp-email",
            preview=text,
        )
    return str(code or "")


def wait_for_code(mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30, *, deps: CloudflareTempEmailDeps) -> str | None:
    email = str(mailbox.get("email") or "").strip().lower()
    if not email:
        return None
    after_ts_ms = int(mailbox.get("_code_after_ts") or 0)
    provider = _mailbox_provider(mailbox)
    started = time.time()
    seen: set[str] = set()
    while time.time() - started <= timeout:
        payload = request(
            provider,
            "get",
            "/admin/mails",
            params={"limit": 20, "offset": 0, "address": email},
            timeout=request_timeout,
            deps=deps,
        )
        for row in _sorted_mail_rows(payload, deps):
            message_id, is_new = _new_mail_message_id(row, seen, deps)
            if not is_new:
                continue
            matches, received_at_ms = _mail_row_matches_wait_window(row, email, after_ts_ms, deps)
            if not matches:
                continue
            code = _code_from_mail_row(mailbox, row, message_id=message_id, received_at_ms=received_at_ms, deps=deps)
            if code:
                return code
        time.sleep(max(1, interval))
    return None
