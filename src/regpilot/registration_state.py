from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from .registration_callback import extract_oauth_callback_params_from_url


AUTH_BASE = "https://auth.openai.com"


def registration_continue_url(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    return str(
        body.get("continue_url")
        or info.get("location")
        or info.get("final_url")
        or info.get("url")
        or ""
    ).strip()


def registration_page_context(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    parts = [body, info.get("text") or "", info.get("location") or "", info.get("final_url") or ""]
    return json.dumps(parts, ensure_ascii=False, default=str)


def registration_state_from_info(info: dict[str, Any]) -> dict[str, str]:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    page_type = str(page.get("type") or body.get("page_type") or "").strip()
    url = registration_continue_url(info)
    text = str(info.get("text") or "")
    location = str(info.get("location") or "")
    combined = " ".join([page_type, url, text[:2000], location]).lower()

    callback_url = ""
    for candidate in (url, location, str(info.get("final_url") or "")):
        if extract_oauth_callback_params_from_url(candidate):
            callback_url = candidate
            break
    if callback_url:
        return {"kind": "callback", "url": callback_url, "page_type": page_type}
    if "/error" in combined or "authorize_hydra_invalid_request" in combined:
        return {"kind": "error", "url": url, "page_type": page_type}
    if page_type == "add_email" or "/add-email" in combined:
        return {"kind": "add_email", "url": url or f"{AUTH_BASE}/add-email", "page_type": page_type}
    if page_type == "about_you" or "about-you" in combined or 'name="birthday"' in combined or 'name="age"' in combined:
        return {"kind": "about_you", "url": url or f"{AUTH_BASE}/about-you", "page_type": page_type}
    if page_type == "email_otp_verification" or "email-verification" in combined or "email-otp" in combined:
        return {"kind": "email_otp", "url": url or f"{AUTH_BASE}/email-verification", "page_type": page_type}
    if page_type in {"create_account_password", "login_password", "password"} or "create-account/password" in combined or "/u/signup/password" in combined or "log-in/password" in combined:
        return {"kind": "password", "url": url or f"{AUTH_BASE}/create-account/password", "page_type": page_type}
    if page_type or url:
        return {"kind": "continue", "url": url, "page_type": page_type}
    return {"kind": "unknown", "url": "", "page_type": ""}


def registration_expected_state(registrar: Any, start_info: dict[str, Any], create_info: dict[str, Any]) -> str:
    authorize = create_info.get("authorize") if isinstance(create_info.get("authorize"), dict) else {}
    for value in (
        start_info.get("state") if isinstance(start_info, dict) else "",
        authorize.get("state"),
        getattr(registrar, "last_authorize", {}).get("state") if isinstance(getattr(registrar, "last_authorize", {}), dict) else "",
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def brief_flow_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query, keep_blank_values=True)
        suffix = ""
        if query.get("errorCode"):
            suffix = f"?errorCode={query['errorCode'][-1]}"
        elif query.get("code"):
            suffix = "?code=***"
        elif query.get("state"):
            suffix = "?state=***"
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{suffix}"[:180]
    except Exception:
        return raw[:180]
