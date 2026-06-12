from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class ReauthorizeCpaOAuthDeps:
    callback_params_from_url: Callable[[str], dict[str, str] | None]
    normalize_origin: Callable[[str], str]
    request_codex2api_json: Callable[..., dict[str, Any]]
    requests_get: Callable[..., Any]
    requests_post: Callable[..., Any]


def _cpa_management_origin(cpa_url: str, deps: ReauthorizeCpaOAuthDeps) -> str:
    origin = deps.normalize_origin(cpa_url)
    if not origin:
        raise ValueError("invalid_cpa_url")
    return origin


def _cpa_management_key(cpa_management_key: str) -> str:
    management_key = str(cpa_management_key or "").strip()
    if not management_key:
        raise RuntimeError("cpa_management_key_missing")
    return management_key


def _cpa_management_headers(management_key: str, *, include_content_type: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {management_key}",
        "X-Management-Key": management_key,
    }
    if include_content_type:
        headers["Content-Type"] = "application/json"
    return headers


def _response_json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return {}


def _response_error_detail(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    return str(data.get("message") or data.get("detail") or data.get("error") or data.get("reason") or "").strip()


def _cpa_data_value(data: Any, *keys: str) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in keys:
            value = nested.get(key)
            if value:
                return str(value)
    return ""


def submit_callback_to_cpa(
    callback_or_code: str,
    *,
    cpa_url: str,
    cpa_management_key: str,
    expected_state: str = "",
    deps: ReauthorizeCpaOAuthDeps,
) -> dict[str, Any]:
    params = deps.callback_params_from_url(callback_or_code) or {}
    code = str(params.get("code") or "").strip()
    state = str(params.get("state") or expected_state or "").strip()
    if not code:
        raise RuntimeError("cpa_callback_code_missing")
    if expected_state and state and state != expected_state:
        raise RuntimeError("cpa_callback_state_mismatch")
    origin = _cpa_management_origin(cpa_url, deps)
    management_key = _cpa_management_key(cpa_management_key)
    response = deps.requests_post(
        f"{origin}/v0/management/oauth-callback",
        headers=_cpa_management_headers(management_key, include_content_type=True),
        json={"provider": "codex", "redirect_url": str(callback_or_code or "").strip()},
        timeout=60,
        verify=False,
    )
    data = _response_json(response)
    if response.status_code >= 400:
        detail = _response_error_detail(data)
        raise RuntimeError(detail or f"cpa_oauth_callback_http_{response.status_code}")
    message = ""
    if isinstance(data, dict):
        message = str(data.get("message") or data.get("status_message") or data.get("detail") or "").strip()
    return {"ok": True, "message": message or "CPA callback submitted", "raw": data}


def exchange_callback_with_codex2api(
    callback_or_code: str,
    *,
    codex2api_url: str,
    codex2api_admin_key: str,
    session_id: str,
    expected_state: str = "",
    deps: ReauthorizeCpaOAuthDeps,
) -> dict[str, Any]:
    params = deps.callback_params_from_url(callback_or_code) or {}
    code = str(params.get("code") or "").strip()
    state = str(params.get("state") or expected_state or "").strip()
    if not code:
        raise RuntimeError("codex2api_callback_code_missing")
    data = deps.request_codex2api_json(
        codex2api_url,
        path="/api/admin/oauth/exchange-code",
        admin_key=codex2api_admin_key,
        method="POST",
        body={"session_id": session_id, "code": code, "state": state},
        timeout=60,
    )
    return {"ok": True, "message": str((data or {}).get("message") or "Codex2API OAuth 账号添加成功"), "raw": data}


def start_cpa_oauth(
    *,
    cpa_url: str,
    cpa_management_key: str,
    email: str = "",
    proxy_url: str = "",
    deps: ReauthorizeCpaOAuthDeps,
) -> dict[str, str]:
    origin = _cpa_management_origin(cpa_url, deps)
    management_key = _cpa_management_key(cpa_management_key)
    proxies = {"http": proxy_url, "https": proxy_url} if str(proxy_url or "").strip() else None
    response = deps.requests_get(
        f"{origin}/v0/management/codex-auth-url",
        headers=_cpa_management_headers(management_key),
        timeout=60,
        verify=False,
        proxies=proxies,
    )
    data = _response_json(response)
    if response.status_code >= 400:
        detail = _response_error_detail(data)
        raise RuntimeError(detail or f"cpa_codex_auth_url_http_{response.status_code}")
    if not isinstance(data, dict):
        data = {}
    auth_url = _cpa_data_value(data, "url", "auth_url", "authUrl").strip()
    if not auth_url.startswith("http"):
        raise RuntimeError("cpa_auth_url_missing")
    parsed = urlparse(auth_url)
    query = parse_qs(parsed.query)
    state = (_cpa_data_value(data, "state", "auth_state", "authState") or (query.get("state") or [""])[0] or "").strip()
    return {
        "authorize_url": auth_url,
        "session_id": "",
        "state": state,
        "client_id": str((query.get("client_id") or [""])[0] or ""),
        "redirect_uri": str((query.get("redirect_uri") or [""])[0] or "http://localhost:1455/auth/callback"),
        "cpa_management_origin": origin,
        "email": str(email or "").strip(),
    }


def start_codex2api_oauth(
    *,
    codex2api_url: str,
    codex2api_admin_key: str,
    email: str,
    proxy_url: str = "",
    deps: ReauthorizeCpaOAuthDeps,
) -> dict[str, str]:
    body: dict[str, Any] = {"email": email, "login_hint": email}
    if str(proxy_url or "").strip():
        body["proxy_url"] = str(proxy_url).strip()
    data = deps.request_codex2api_json(
        codex2api_url,
        path="/api/admin/oauth/generate-auth-url",
        admin_key=codex2api_admin_key,
        method="POST",
        body=body,
        timeout=30,
    )
    auth_url = str((data or {}).get("auth_url") or "").strip()
    session_id = str((data or {}).get("session_id") or "").strip()
    if not auth_url or not session_id:
        raise RuntimeError("codex2api_oauth_generate_failed")
    parsed = urlparse(auth_url)
    q = parse_qs(parsed.query)
    return {
        "authorize_url": auth_url,
        "session_id": session_id,
        "state": str((q.get("state") or [""])[-1] or ""),
        "client_id": str((q.get("client_id") or [""])[-1] or ""),
        "redirect_uri": str((q.get("redirect_uri") or [""])[-1] or "http://localhost:1455/auth/callback"),
    }
