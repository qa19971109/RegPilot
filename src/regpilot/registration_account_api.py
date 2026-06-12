from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class RegistrationAccountApiDeps:
    auth_base: str
    platform_oauth_client_id: str
    platform_oauth_audience: str
    platform_oauth_redirect_uri: str
    chatgpt_signup_client_id: str
    chatgpt_signup_redirect_uri: str
    chatgpt_signup_scope: str
    merge_url_query: Callable[..., str]
    request_with_retry: Callable[..., Any]
    response_json: Callable[[Any], dict]
    cookie_snapshot: Callable[[Any], dict[str, str]]
    summarize_cookie_snapshot: Callable[[dict[str, str]], dict[str, Any]]
    navigate_headers: Callable[[], dict[str, str]]
    trace_headers: Callable[[], dict[str, str]]
    token_urlsafe: Callable[[int], str]
    uuid4: Callable[[], Any]
    generate_pkce: Callable[[], tuple[str, str]]
    quote: Callable[..., str]


def response_probe(resp: Any, fallback_url: str, response_json: Callable[[Any], dict]) -> dict[str, Any]:
    body = {}
    text = ""
    status = 0
    content_type = ""
    location = ""
    final_url = fallback_url
    if resp is not None:
        status = int(getattr(resp, "status_code", 0) or 0)
        body = response_json(resp)
        final_url = str(getattr(resp, "url", fallback_url) or fallback_url)
        content_type = str(getattr(resp, "headers", {}).get("Content-Type") or "")
        location = str(getattr(resp, "headers", {}).get("Location") or "")
        try:
            text = resp.text[:2000]
        except Exception:
            text = ""
    return {
        "status": status,
        "json": body,
        "text": text,
        "content_type": content_type,
        "location": location,
        "final_url": final_url,
    }


def _set_oai_did_cookies(registrar: Any) -> None:
    registrar.session.cookies.set("oai-did", registrar.device_id, domain=".auth.openai.com")
    registrar.session.cookies.set("oai-did", registrar.device_id, domain="auth.openai.com")


def _chatgpt_signup_authorize_url(registrar: Any, login_hint: str, state: str, *, deps: RegistrationAccountApiDeps) -> str:
    params = {
        "client_id": deps.chatgpt_signup_client_id,
        "scope": deps.chatgpt_signup_scope,
        "response_type": "code",
        "redirect_uri": deps.chatgpt_signup_redirect_uri,
        "audience": deps.platform_oauth_audience,
        "state": state,
        "device_id": registrar.device_id,
        "ext-oai-did": registrar.device_id,
        "screen_hint": "login_or_signup",
        "prompt": "login",
        "auth_session_logging_id": str(deps.uuid4()),
        "login_hint": login_hint,
    }
    return f"{deps.auth_base}/api/accounts/authorize?" + urlencode(params)


def _authorize_start_result(registrar: Any, response: Any, *, email: str, state: str, nonce: str = "", code_verifier: str = "", code_challenge: str = "", external_authorize: bool = False, screen_hint: str = "", flow_kind: str, deps: RegistrationAccountApiDeps) -> dict[str, str]:
    snapshot = deps.cookie_snapshot(registrar.session)
    registrar.last_authorize = {
        "email": email,
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
        "external_authorize": external_authorize,
        "flow_kind": flow_kind,
        "final_url": str(response.url),
        "status": str(response.status_code),
        "cookie_snapshot": snapshot,
        "cookie_summary": deps.summarize_cookie_snapshot(snapshot),
    }
    if screen_hint:
        registrar.last_authorize["screen_hint"] = screen_hint
    return {
        "code_verifier": code_verifier,
        "final_url": str(response.url),
        "status": str(response.status_code),
        "state": state,
        "cookie_summary": registrar.last_authorize.get("cookie_summary") or {},
    }


def start_phone_signup(registrar: Any, phone_number: str = "", *, deps: RegistrationAccountApiDeps) -> dict[str, str]:
    _set_oai_did_cookies(registrar)
    phone_hint = re.sub(r"\s+", "", str(phone_number or "").strip())
    if phone_hint:
        state = deps.token_urlsafe(32)
        url = _chatgpt_signup_authorize_url(registrar, phone_hint, state, deps=deps)
    else:
        state = ""
        url = f"{deps.auth_base}/u/signup/identifier"
    headers = deps.navigate_headers()
    if phone_hint:
        headers["referer"] = "https://chatgpt.com/"
        headers["sec-fetch-site"] = "cross-site"
    headers["oai-device-id"] = registrar.device_id
    headers.update(deps.trace_headers())
    try:
        headers["OpenAI-Sentinel-Token"] = registrar._ensure_sentinel_token("signup")
    except Exception as exc:
        headers["x-openai-sentinel-error"] = str(exc)
    response, error = deps.request_with_retry(
        registrar.session,
        "get",
        url,
        headers=headers,
        allow_redirects=True,
        verify=False,
    )
    if response is None:
        raise RuntimeError(error or "phone_signup_start_failed")
    result = _authorize_start_result(registrar, response, email="", state=state, flow_kind="phone_signup", deps=deps)
    result.pop("state", None)
    return result


def start_email_signup(registrar: Any, email: str, *, deps: RegistrationAccountApiDeps) -> dict[str, str]:
    _set_oai_did_cookies(registrar)
    email_hint = str(email or "").strip()
    if not email_hint:
        raise ValueError("email_required")
    state = deps.token_urlsafe(32)
    url = _chatgpt_signup_authorize_url(registrar, email_hint, state, deps=deps)
    headers = deps.navigate_headers()
    headers["referer"] = "https://chatgpt.com/"
    headers["sec-fetch-site"] = "cross-site"
    headers["oai-device-id"] = registrar.device_id
    headers.update(deps.trace_headers())
    try:
        headers["OpenAI-Sentinel-Token"] = registrar._ensure_sentinel_token("signup")
    except Exception as exc:
        headers["x-openai-sentinel-error"] = str(exc)
    response, error = deps.request_with_retry(
        registrar.session,
        "get",
        url,
        headers=headers,
        allow_redirects=True,
        verify=False,
    )
    if response is None:
        raise RuntimeError(error or "email_signup_start_failed")
    return _authorize_start_result(registrar, response, email=email_hint, state=state, flow_kind="email_signup", deps=deps)


def start_authorize(registrar: Any, email: str, authorize_url: str = "", screen_hint: str = "login_or_signup", *, deps: RegistrationAccountApiDeps) -> dict[str, str]:
    _set_oai_did_cookies(registrar)
    code_verifier = ""
    requested_screen_hint = str(screen_hint or "login_or_signup")
    if authorize_url:
        parsed = urlparse(authorize_url)
        params = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        params["login_hint"] = email
        params["screen_hint"] = requested_screen_hint
        params.setdefault("device_id", registrar.device_id)
        state = str(params.get("state") or "")
        nonce = str(params.get("nonce") or "")
        url = urlunparse(parsed._replace(query=urlencode(params)))
    else:
        code_verifier, code_challenge = deps.generate_pkce()
        state = deps.token_urlsafe(32)
        nonce = deps.token_urlsafe(32)
        params = {
            "issuer": deps.auth_base,
            "client_id": deps.platform_oauth_client_id,
            "audience": deps.platform_oauth_audience,
            "redirect_uri": deps.platform_oauth_redirect_uri,
            "device_id": registrar.device_id,
            "screen_hint": requested_screen_hint,
            "max_age": "0",
            "login_hint": email,
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "response_mode": "query",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9",
        }
        url = f"{deps.auth_base}/api/accounts/authorize?" + "&".join(f"{key}={deps.quote(str(value), safe='')}" for key, value in params.items())
    response, error = deps.request_with_retry(
        registrar.session,
        "get",
        url,
        headers=deps.navigate_headers(),
        allow_redirects=True,
        verify=False,
    )
    if response is None:
        raise RuntimeError(error or "authorize_request_failed")
    flow_kind = "login" if requested_screen_hint == "login" else "signup" if requested_screen_hint == "signup" else "login_or_signup"
    return _authorize_start_result(
        registrar,
        response,
        email=email,
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        code_challenge=str(params.get("code_challenge") or ""),
        external_authorize=bool(authorize_url),
        screen_hint=requested_screen_hint,
        flow_kind=flow_kind,
        deps=deps,
    )


def _accounts_payload_flow(registrar: Any, payload: dict[str, Any]) -> str:
    origin_page_type = str(payload.get("origin_page_type") or "")
    flow_kind = str(registrar.last_authorize.get("flow_kind") or "").strip()
    if origin_page_type.startswith("login") or (origin_page_type.startswith("email_otp") and flow_kind == "login"):
        return "auth"
    return "signup"


def _default_accounts_payload_candidates(
    referer_url: str,
    state: str,
    *,
    deps: RegistrationAccountApiDeps,
) -> list[tuple[str, str]]:
    candidates = [
        (f"{deps.auth_base}/api/accounts", "json"),
        (referer_url, "json"),
        (referer_url, "form"),
        (deps.merge_url_query(referer_url, _data="routes/u/signup/identifier"), "json"),
        (deps.merge_url_query(referer_url, _data="routes/u/signup/identifier"), "form"),
        (deps.merge_url_query(referer_url, _data="routes/u/signup/password"), "json"),
        (deps.merge_url_query(referer_url, _data="routes/u/signup/password"), "form"),
        (deps.merge_url_query(f"{deps.auth_base}/u/signup/identifier", _data="routes/u/signup/identifier"), "json"),
        (deps.merge_url_query(f"{deps.auth_base}/u/signup/identifier", _data="routes/u/signup/identifier"), "form"),
        (deps.merge_url_query(f"{deps.auth_base}/u/signup/password", _data="routes/u/signup/password"), "json"),
        (deps.merge_url_query(f"{deps.auth_base}/u/signup/password", _data="routes/u/signup/password"), "form"),
    ]
    if state:
        candidates.extend(
            [
                (deps.merge_url_query(f"{deps.auth_base}/u/signup/identifier", state=state, _data="routes/u/signup/identifier"), "json"),
                (deps.merge_url_query(f"{deps.auth_base}/u/signup/identifier", state=state, _data="routes/u/signup/identifier"), "form"),
                (deps.merge_url_query(f"{deps.auth_base}/u/signup/password", state=state, _data="routes/u/signup/password"), "json"),
                (deps.merge_url_query(f"{deps.auth_base}/u/signup/password", state=state, _data="routes/u/signup/password"), "form"),
            ]
        )
    return candidates


def _accounts_payload_post_kwargs(
    headers: dict[str, str],
    payload: dict[str, Any],
    body_mode: str,
    state: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    try_headers = dict(headers)
    kwargs: dict[str, Any] = {"headers": try_headers, "verify": False}
    if body_mode == "form":
        try_headers["content-type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        kwargs["data"] = {
            "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            "state": state,
            "origin_page_type": str(payload.get("origin_page_type") or ""),
        }
    else:
        kwargs["json"] = payload
    return try_headers, kwargs


def _accounts_payload_attempt(
    *,
    url: str,
    body_mode: str,
    resp: Any,
    error: str,
    probe: dict[str, Any],
    cookie_summary: dict[str, Any],
    headers: dict[str, str],
    try_headers: dict[str, str],
    state: str,
) -> dict[str, Any]:
    return {
        "url": url,
        "body_mode": body_mode,
        "ok": resp is not None and 200 <= probe["status"] < 300,
        "status": probe["status"],
        "json": probe["json"],
        "text": probe["text"],
        "error": error,
        "final_url": probe["final_url"],
        "cookie_summary": cookie_summary,
        "referer": headers.get("referer") or "",
        "state": state,
        "sentinel_token_present": bool(try_headers.get("OpenAI-Sentinel-Token")),
        "sentinel_error": try_headers.get("x-openai-sentinel-error") or "",
    }


def _empty_accounts_payload_attempt(
    *,
    cookie_summary: dict[str, Any],
    headers: dict[str, str],
    state: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": 0,
        "json": {},
        "text": "",
        "error": "no_attempts",
        "url": "",
        "final_url": "",
        "body_mode": "json",
        "cookie_summary": cookie_summary,
        "referer": headers.get("referer") or "",
        "state": state,
    }


def post_accounts_payload(
    registrar: Any,
    payload: dict[str, Any],
    referer_path: str,
    candidates: list[tuple[str, str]] | None = None,
    *,
    deps: RegistrationAccountApiDeps,
) -> dict[str, Any]:
    headers = registrar._build_accounts_headers(referer_path, _accounts_payload_flow(registrar, payload))
    state = str(registrar.last_authorize.get("state") or "").strip()
    referer_url = f"{deps.auth_base}{referer_path}"
    default_candidates = _default_accounts_payload_candidates(referer_url, state, deps=deps)
    attempts: list[dict[str, Any]] = []
    snapshot = deps.cookie_snapshot(registrar.session)
    cookie_summary = deps.summarize_cookie_snapshot(snapshot)
    for url, body_mode in (candidates or default_candidates):
        try_headers, kwargs = _accounts_payload_post_kwargs(headers, payload, body_mode, state)
        resp, error = deps.request_with_retry(registrar.session, "post", url, **kwargs)
        probe = response_probe(resp, url, deps.response_json)
        attempt = _accounts_payload_attempt(
            url=url,
            body_mode=body_mode,
            resp=resp,
            error=error,
            probe=probe,
            cookie_summary=cookie_summary,
            headers=headers,
            try_headers=try_headers,
            state=state,
        )
        attempts.append(attempt)
        if attempt["ok"] or probe["status"] in (401, 403, 405, 422):
            return {**attempt, "payload": payload, "attempts": attempts, "authorize": registrar.last_authorize}
    last = attempts[-1] if attempts else _empty_accounts_payload_attempt(cookie_summary=cookie_summary, headers=headers, state=state)
    return {**last, "payload": payload, "attempts": attempts, "authorize": registrar.last_authorize}


@dataclass(frozen=True)
class SignupSessionContext:
    state: str
    final_url: str
    is_login_flow: bool
    base_page: str

    @property
    def flow_kind(self) -> str:
        return "login" if self.is_login_flow else "signup"


def _signup_session_context(registrar: Any, deps: RegistrationAccountApiDeps) -> SignupSessionContext:
    state = str(registrar.last_authorize.get("state") or "").strip()
    final_url = str(registrar.last_authorize.get("final_url") or "").strip() or f"{deps.auth_base}/u/signup"
    requested_flow = str(registrar.last_authorize.get("flow_kind") or "").strip()
    is_login_flow = requested_flow == "login" or any(token in final_url for token in ("/log-in", "screen_hint=login", "login_or_signup"))
    base_page = "/log-in/password" if is_login_flow else "/u/signup/password"
    return SignupSessionContext(state=state, final_url=final_url, is_login_flow=is_login_flow, base_page=base_page)


def _signup_session_nav_headers(registrar: Any, context: SignupSessionContext, deps: RegistrationAccountApiDeps) -> dict[str, str]:
    headers = deps.navigate_headers()
    headers["oai-device-id"] = registrar.device_id
    headers.update(deps.trace_headers())
    try:
        headers["OpenAI-Sentinel-Token"] = registrar._ensure_sentinel_token("auth" if context.is_login_flow else "signup")
    except Exception as exc:
        headers["x-openai-sentinel-error"] = str(exc)
    return headers


def _signup_session_nav_candidates(context: SignupSessionContext, deps: RegistrationAccountApiDeps) -> list[str]:
    state = context.state
    if context.is_login_flow:
        return [
            context.final_url,
            f"{deps.auth_base}/log-in/password?state={state}" if state else f"{deps.auth_base}/log-in/password",
            f"{deps.auth_base}/api/auth/session",
            f"{deps.auth_base}/api/auth/session?state={state}" if state else f"{deps.auth_base}/api/auth/session",
            f"{deps.auth_base}/api/client_auth_session_dump",
            f"{deps.auth_base}/api/client_auth_session_dump?state={state}" if state else f"{deps.auth_base}/api/client_auth_session_dump",
        ]
    return [
        context.final_url,
        f"{deps.auth_base}/u/signup",
        f"{deps.auth_base}/u/signup?state={state}" if state else f"{deps.auth_base}/u/signup",
        f"{deps.auth_base}/u/signup/identifier?state={state}" if state else f"{deps.auth_base}/u/signup/identifier",
        f"{deps.auth_base}/u/signup/password?state={state}" if state else f"{deps.auth_base}/u/signup/password",
        f"{deps.auth_base}/log-in/password?state={state}" if state else f"{deps.auth_base}/log-in/password",
        f"{deps.auth_base}/api/auth/session",
        f"{deps.auth_base}/api/auth/session?state={state}" if state else f"{deps.auth_base}/api/auth/session",
        f"{deps.auth_base}/api/client_auth_session_dump",
        f"{deps.auth_base}/api/client_auth_session_dump?state={state}" if state else f"{deps.auth_base}/api/client_auth_session_dump",
    ]


def _signup_session_xhr_headers(registrar: Any, context: SignupSessionContext) -> dict[str, str]:
    xhr_headers = registrar._build_accounts_headers(
        f"{context.base_page}?state={context.state}" if context.state else context.base_page,
        "auth" if context.is_login_flow else "signup",
    )
    xhr_headers["accept"] = "application/json"
    return xhr_headers


def _signup_session_xhr_candidates(context: SignupSessionContext, deps: RegistrationAccountApiDeps) -> list[tuple[str, str]]:
    state = context.state
    if context.is_login_flow:
        return [
            (deps.merge_url_query(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password", state=state) if state else deps.merge_url_query(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password"), "get"),
            (f"{deps.auth_base}/api/auth/session", "get"),
            (f"{deps.auth_base}/api/client_auth_session_dump", "get"),
        ]
    return [
        (deps.merge_url_query(f"{deps.auth_base}/u/signup", _data="routes/u/signup", state=state) if state else deps.merge_url_query(f"{deps.auth_base}/u/signup", _data="routes/u/signup"), "get"),
        (deps.merge_url_query(f"{deps.auth_base}/u/signup/identifier", _data="routes/u/signup/identifier", state=state) if state else deps.merge_url_query(f"{deps.auth_base}/u/signup/identifier", _data="routes/u/signup/identifier"), "get"),
        (deps.merge_url_query(f"{deps.auth_base}/u/signup/password", _data="routes/u/signup/password", state=state) if state else deps.merge_url_query(f"{deps.auth_base}/u/signup/password", _data="routes/u/signup/password"), "get"),
        (deps.merge_url_query(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password", state=state) if state else deps.merge_url_query(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password"), "get"),
        (f"{deps.auth_base}/api/auth/session", "get"),
        (f"{deps.auth_base}/api/client_auth_session_dump", "get"),
    ]


def _append_signup_session_probe(
    probes: list[dict[str, Any]],
    registrar: Any,
    *,
    probe_type: str,
    method: str,
    url: str,
    headers: dict[str, str],
    deps: RegistrationAccountApiDeps,
) -> None:
    resp, error = deps.request_with_retry(
        registrar.session,
        method,
        url,
        headers=headers,
        verify=False,
        allow_redirects=False,
    )
    probe = response_probe(resp, url, deps.response_json)
    item = {
        "probe_type": probe_type,
        "url": url,
        "status": probe["status"],
        "content_type": probe["content_type"],
        "location": probe["location"],
        "json": probe["json"],
        "text": probe["text"],
        "error": error,
        "final_url": probe["final_url"],
        "is_html_shell": "<html" in probe["text"].lower() or "auth-cdn.oaistatic.com/assets/" in probe["text"],
    }
    if probe_type == "xhr":
        item["method"] = method
    probes.append(item)


def _signup_session_probes(
    registrar: Any,
    context: SignupSessionContext,
    headers: dict[str, str],
    deps: RegistrationAccountApiDeps,
) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for url in _signup_session_nav_candidates(context, deps):
        _append_signup_session_probe(probes, registrar, probe_type="navigate", method="get", url=url, headers=headers, deps=deps)
    xhr_headers = _signup_session_xhr_headers(registrar, context)
    for url, method in _signup_session_xhr_candidates(context, deps):
        _append_signup_session_probe(probes, registrar, probe_type="xhr", method=method, url=url, headers=xhr_headers, deps=deps)
    return probes


def establish_signup_session(registrar: Any, *, deps: RegistrationAccountApiDeps) -> dict[str, Any]:
    context = _signup_session_context(registrar, deps)
    headers = _signup_session_nav_headers(registrar, context, deps)
    probes = _signup_session_probes(registrar, context, headers, deps)
    snapshot = deps.cookie_snapshot(registrar.session)
    cookie_summary = deps.summarize_cookie_snapshot(snapshot)
    result = {
        "ok": bool(snapshot.get("oai-client-auth-session") or snapshot.get("auth_session") or snapshot.get("oai-auth-token")),
        "state": context.state,
        "flow_kind": context.flow_kind,
        "input_final_url": context.final_url,
        "cookie_summary": cookie_summary,
        "cookie_snapshot": snapshot,
        "sentinel_token_present": bool(headers.get("OpenAI-Sentinel-Token")),
        "sentinel_error": headers.get("x-openai-sentinel-error") or "",
        "probes": probes,
    }
    registrar.last_authorize["session_establishment"] = result
    return result
