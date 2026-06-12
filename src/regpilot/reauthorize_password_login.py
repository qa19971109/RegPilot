from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReauthorizePasswordLoginDeps:
    auth_base: str
    har_browser_fetch_headers_fn: Callable[..., dict[str, str]]
    response_json_fn: Callable[[Any], dict[str, Any]]
    first_callbackish_url_fn: Callable[..., str]
    merge_url_query_fn: Callable[..., str]


def login_username_payload(identifier: str) -> dict[str, str]:
    value = str(identifier or "").strip()
    normalized_phone = re.sub(r"\s+", "", value)
    if re.fullmatch(r"\+?[0-9]{6,20}", normalized_phone):
        return {"kind": "phone_number", "value": normalized_phone}
    return {"kind": "email", "value": value}


def _password_login_state_and_referer(registrar: Any) -> tuple[str, str]:
    state = str(registrar.last_authorize.get("state") or "").strip()
    referer_path = f"/log-in/password?state={state}" if state else "/log-in/password"
    return state, referer_path


def _direct_password_login_attempts(
    auth_base: str,
    email: str,
    password: str,
    username_payload: dict[str, str],
) -> list[tuple[str, dict[str, Any]]]:
    password_verify_url = f"{auth_base}/api/accounts/password/verify"
    return [
        (password_verify_url, {"password": password}),
        (password_verify_url, {"username": email, "password": password}),
        (password_verify_url, {"username": username_payload, "password": password}),
        (password_verify_url, {"password": password}),
        (password_verify_url, {"username": email, "password": password}),
        (password_verify_url, {"username": username_payload, "password": password}),
        (f"{auth_base}/api/auth/password", {"password": password}),
    ]


def _summarize_password_response(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    resp: Any,
    deps: ReauthorizePasswordLoginDeps,
) -> dict[str, Any]:
    body = deps.response_json_fn(resp)
    status = int(resp.status_code or 0)
    location = str(resp.headers.get("Location") or "")
    final_url = str(getattr(resp, "url", url) or url)
    ok = 200 <= status < 300 or bool(
        deps.first_callbackish_url_fn(location, body.get("continue_url") if isinstance(body, dict) else "", final_url)
    )
    return {
        "url": url,
        "body_mode": "direct_json",
        "payload_keys": sorted(payload.keys()),
        "ok": ok,
        "status": status,
        "json": body,
        "text": resp.text[:2000],
        "location": location,
        "final_url": final_url,
        "referer": headers.get("referer") or "",
        "sentinel_token_present": bool(headers.get("OpenAI-Sentinel-Token")),
        "sentinel_error": headers.get("x-openai-sentinel-error") or "",
    }


def _password_exception_attempt(url: str, payload: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "url": url,
        "body_mode": "direct_json",
        "payload_keys": sorted(payload.keys()),
        "ok": False,
        "status": 0,
        "json": {},
        "text": "",
        "location": "",
        "final_url": url,
        "error": str(exc),
    }


def _run_direct_password_attempts(
    registrar: Any,
    direct_attempts: list[tuple[str, dict[str, Any]]],
    referer_path: str,
    deps: ReauthorizePasswordLoginDeps,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    attempts: list[dict[str, Any]] = []
    auth_failure: dict[str, Any] | None = None
    for url, payload in direct_attempts:
        headers = deps.har_browser_fetch_headers_fn(referer_path)
        try:
            resp = registrar.session.post(url, json=payload, headers=headers, verify=False, timeout=30, allow_redirects=False)
            attempt = _summarize_password_response(url, payload, headers, resp, deps)
        except Exception as exc:
            attempt = _password_exception_attempt(url, payload, exc)
        attempts.append(attempt)
        if attempt.get("ok"):
            return {**attempt, "attempts": attempts, "authorize": registrar.last_authorize}, attempts, auth_failure
        status = int(attempt.get("status") or 0)
        if status == 401 and auth_failure is None:
            auth_failure = attempt
        if status in (403, 422):
            return {**attempt, "attempts": attempts, "authorize": registrar.last_authorize}, attempts, auth_failure
    return None, attempts, auth_failure


def _password_remix_candidates(deps: ReauthorizePasswordLoginDeps, state: str) -> list[tuple[str, str]]:
    password_data_url = (
        deps.merge_url_query_fn(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password", state=state)
        if state
        else deps.merge_url_query_fn(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password")
    )
    plain_password_data_url = deps.merge_url_query_fn(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password")
    return [
        (password_data_url, "json"),
        (password_data_url, "form"),
        (plain_password_data_url, "json"),
        (plain_password_data_url, "form"),
        (f"{deps.auth_base}/api/accounts", "json"),
    ]


def _run_remix_password_attempts(
    registrar: Any,
    *,
    username_payload: dict[str, str],
    password: str,
    referer_path: str,
    state: str,
    attempts: list[dict[str, Any]],
    deps: ReauthorizePasswordLoginDeps,
) -> dict[str, Any] | None:
    remix_payloads = [
        {"origin_page_type": "login_password", "data": {"intent": "validate", "username": username_payload, "password": password}},
    ]
    candidates = _password_remix_candidates(deps, state)
    for payload in remix_payloads:
        info = registrar._post_accounts_payload(payload, referer_path, candidates=candidates)
        attempts.extend(info.get("attempts") or [info])
        if info.get("ok"):
            return {**info, "attempts": attempts, "authorize": registrar.last_authorize}
        if int(info.get("status") or 0) in (403, 422):
            return {**info, "attempts": attempts, "authorize": registrar.last_authorize}
    return None


def _final_password_login_result(
    attempts: list[dict[str, Any]],
    auth_failure: dict[str, Any] | None,
    authorize: dict[str, Any],
) -> dict[str, Any]:
    last = attempts[-1] if attempts else {"ok": False, "status": 0, "json": {}, "text": "", "error": "no_login_attempts"}
    if auth_failure is not None:
        return {**auth_failure, "attempts": attempts, "authorize": authorize}
    return {**last, "attempts": attempts, "authorize": authorize}


def attempt_password_login(
    registrar: Any,
    email: str,
    password: str,
    deps: ReauthorizePasswordLoginDeps,
) -> dict[str, Any]:
    state, referer_path = _password_login_state_and_referer(registrar)
    username_payload = login_username_payload(email)
    direct_result, attempts, auth_failure = _run_direct_password_attempts(
        registrar,
        _direct_password_login_attempts(deps.auth_base, email, password, username_payload),
        referer_path,
        deps,
    )
    if direct_result:
        return direct_result
    remix_result = _run_remix_password_attempts(
        registrar,
        username_payload=username_payload,
        password=password,
        referer_path=referer_path,
        state=state,
        attempts=attempts,
        deps=deps,
    )
    if remix_result:
        return remix_result
    return _final_password_login_result(attempts, auth_failure, registrar.last_authorize)
