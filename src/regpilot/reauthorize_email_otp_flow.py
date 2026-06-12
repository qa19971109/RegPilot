from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable


FormInputs = tuple[str, dict[str, str], str, str]


@dataclass(frozen=True)
class ReauthorizeEmailOtpDeps:
    auth_base: str
    har_browser_fetch_headers_fn: Callable[..., dict[str, str]]
    response_json_fn: Callable[[Any], dict[str, Any]]
    merge_url_query_fn: Callable[..., str]
    safe_response_summary_fn: Callable[[dict[str, Any]], dict[str, Any]]
    extract_email_otp_form_inputs_fn: Callable[[str], FormInputs]
    post_form_and_follow_fn: Callable[..., tuple[str, str]]
    build_sentinel_token_fn: Callable[[Any, str, str], str]
    extract_callback_from_step_fn: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ReauthorizeEmailOtpStepDeps:
    mailbox_for_mail_wait_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    wait_for_code_fn: Callable[..., str | None]
    sync_mail_wait_state_fn: Callable[[dict[str, Any], dict[str, Any]], None]
    mail_wait_config_for_account_fn: Callable[..., Any]
    log_stage_fn: Callable[[str], None]
    time_time_fn: Callable[[], float]
    enter_login_email_otp_step_fn: Callable[[Any, str], dict[str, Any]]
    response_brief_fn: Callable[[dict[str, Any]], str]
    trigger_passwordless_login_otp_fn: Callable[[Any], dict[str, Any]]
    safe_response_summary_fn: Callable[[dict[str, Any]], dict[str, Any]]
    send_login_otp_fn: Callable[[Any, str], dict[str, Any]]
    validate_login_otp_fn: Callable[[Any, str, str], dict[str, Any]]
    step_requires_phone_verification_fn: Callable[[dict[str, Any]], bool]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, str]]
    resolve_callback_step_fn: Callable[..., str]
    is_consent_like_url_fn: Callable[[Any], bool]
    short_url_fn: Callable[[Any], str]
    resolve_consent_callback_direct_fn: Callable[[Any, str, str], tuple[str, dict[str, Any]]]
    ready_text_fn: Callable[[Any], str]


@dataclass(frozen=True)
class EmailOtpMailWaitConfigs:
    quick: Any
    full: Any


@dataclass(frozen=True)
class EmailOtpFormCandidate:
    action: str
    body: str


@dataclass(frozen=True)
class EmailOtpSubmitChoice:
    score: int
    name: str
    value: str
    formaction: str


@dataclass(frozen=True)
class EmailOtpCollectedInputs:
    fields: dict[str, str]
    email_name: str
    code_name: str
    submit_choice: EmailOtpSubmitChoice | None = None


def html_attr(attrs: str, name: str) -> str:
    match = re.search(rf'{re.escape(name)}\s*=\s*["\']([^"\']*)["\']', str(attrs or ""), re.I | re.S)
    return str(match.group(1) or "").strip() if match else ""


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or ""))


def _email_otp_form_score(match: re.Match[str]) -> tuple[int, int]:
    attrs = match.group(1) or ""
    body = match.group(2) or ""
    haystack = f"{attrs}\n{strip_tags(body)}".lower()
    score = 0
    for token, weight in [
        ("email-verification", 40),
        ("email verification", 40),
        ("email-otp", 36),
        ("otp", 26),
        ("verification code", 24),
        ("send code", 22),
        ("resend", 18),
        ("email", 12),
        ("log in", 8),
        ("login", 8),
        ("continue", 6),
    ]:
        if token in haystack:
            score += weight
    for token, weight in [("password", -24), ("consent", -18), ("authorize", -18), ("codex", -18), ("signup", -10)]:
        if token in haystack:
            score += weight
    if re.search(r"name\s*=\s*[\"'](?:email|code|otp|state)[\"']", body, re.I):
        score += 8
    if re.search(r"<button\b|type\s*=\s*[\"']submit[\"']", body, re.I):
        score += 4
    return score, len(body)


def _select_email_otp_form(text: str) -> EmailOtpFormCandidate | None:
    form_matches = list(re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.I | re.S))
    if not form_matches:
        return None
    form_match = max(form_matches, key=_email_otp_form_score)
    return EmailOtpFormCandidate(
        action=html_attr(form_match.group(1) or "", "action"),
        body=form_match.group(2) or "",
    )


def _email_otp_submit_score(label: str, input_type: str) -> int:
    haystack = f"{label} {input_type}".lower()
    score = 0
    for token, weight in [("send", 30), ("resend", 28), ("email", 18), ("code", 18), ("verification", 16), ("continue", 8), ("submit", 4)]:
        if token in haystack:
            score += weight
    for token, weight in [("password", -20), ("authorize", -20), ("consent", -20), ("codex", -20)]:
        if token in haystack:
            score += weight
    return score


def _collect_email_otp_input_fields(form_body: str) -> EmailOtpCollectedInputs:
    fields: dict[str, str] = {}
    email_name = ""
    code_name = ""
    submit_choice: EmailOtpSubmitChoice | None = None
    for input_match in re.finditer(r"<input\b([^>]*)>", form_body, re.I | re.S):
        attrs = input_match.group(1) or ""
        name = html_attr(attrs, "name")
        input_type = html_attr(attrs, "type").lower()
        value = html_attr(attrs, "value")
        if name and input_type in ("hidden", "checkbox", "radio"):
            if input_type not in ("checkbox", "radio") or re.search(r"\bchecked\b", attrs, re.I):
                fields[name] = value
        if name and input_type in ("submit", "button", "image"):
            candidate = EmailOtpSubmitChoice(
                score=_email_otp_submit_score(f"{name} {value}", input_type),
                name=name,
                value=value,
                formaction=html_attr(attrs, "formaction"),
            )
            if submit_choice is None or candidate.score > submit_choice.score:
                submit_choice = candidate
        if name and not email_name and (input_type == "email" or "email" in name.lower()):
            email_name = name
        if name and not code_name and ("code" in name.lower() or "otp" in name.lower()):
            code_name = name
    return EmailOtpCollectedInputs(fields=fields, email_name=email_name, code_name=code_name, submit_choice=submit_choice)


def _collect_email_otp_button_choice(
    form_body: str,
    submit_choice: EmailOtpSubmitChoice | None,
) -> EmailOtpSubmitChoice | None:
    for button_match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", form_body, re.I | re.S):
        attrs = button_match.group(1) or ""
        button_type = html_attr(attrs, "type").lower() or "submit"
        if button_type not in ("", "submit"):
            continue
        name = html_attr(attrs, "name")
        value = html_attr(attrs, "value") or strip_tags(button_match.group(2) or "").strip()
        candidate = EmailOtpSubmitChoice(
            score=_email_otp_submit_score(f"{name} {value}", button_type),
            name=name,
            value=value,
            formaction=html_attr(attrs, "formaction"),
        )
        if submit_choice is None or candidate.score > submit_choice.score:
            submit_choice = candidate
    return submit_choice


def _apply_email_otp_submit_choice(
    action: str,
    fields: dict[str, str],
    submit_choice: EmailOtpSubmitChoice | None,
) -> str:
    if not submit_choice:
        return action
    if submit_choice.name and submit_choice.name not in fields:
        fields[submit_choice.name] = submit_choice.value
    return submit_choice.formaction or action


def extract_email_otp_form_inputs(
    html_text: str,
    *,
    fallback_extract_form_inputs: Callable[[str], FormInputs],
) -> FormInputs:
    text = str(html_text or "")
    selected_form = _select_email_otp_form(text)
    if selected_form is None:
        return fallback_extract_form_inputs(text)
    collected = _collect_email_otp_input_fields(selected_form.body)
    submit_choice = _collect_email_otp_button_choice(selected_form.body, collected.submit_choice)
    action = _apply_email_otp_submit_choice(selected_form.action, collected.fields, submit_choice)
    return action, collected.fields, collected.email_name, collected.code_name


def enter_login_email_otp_step(registrar: Any, state: str, deps: ReauthorizeEmailOtpDeps) -> dict[str, Any]:
    state = str(state or "").strip()
    targets = [
        f"{deps.auth_base}/log-in/email-verification?state={state}" if state else f"{deps.auth_base}/log-in/email-verification",
        f"{deps.auth_base}/email-verification?state={state}" if state else f"{deps.auth_base}/email-verification",
        deps.merge_url_query_fn(f"{deps.auth_base}/log-in/email-verification", _data="routes/log-in/email-verification", state=state)
        if state
        else deps.merge_url_query_fn(f"{deps.auth_base}/log-in/email-verification", _data="routes/log-in/email-verification"),
    ]
    attempts: list[dict[str, Any]] = []
    for target in targets:
        headers = deps.har_browser_fetch_headers_fn(
            f"/log-in/email-verification?state={state}" if state else "/log-in/email-verification",
            accept="application/json, text/html, */*",
            content_type="",
        )
        try:
            resp = registrar.session.get(target, headers=headers, verify=False, timeout=30, allow_redirects=True)
            html = str(getattr(resp, "text", "") or "")
            info = {
                "ok": 200 <= int(resp.status_code or 0) < 400,
                "status": int(resp.status_code or 0),
                "json": deps.response_json_fn(resp),
                "text": html[:2000],
                "html": html,
                "location": str(resp.headers.get("Location") or ""),
                "final_url": str(getattr(resp, "url", target) or target),
                "target": target,
            }
        except Exception as exc:
            info = {"ok": False, "status": 0, "json": {}, "text": "", "location": "", "final_url": target, "target": target, "error": str(exc)}
        attempts.append(info)
        summary = deps.safe_response_summary_fn(info)
        page_type = str(summary.get("page_type") or "")
        final_url = str(info.get("final_url") or "")
        if info.get("ok") and (page_type == "email_otp_verification" or "email-verification" in final_url):
            return {**info, "attempts": attempts}
    last = attempts[-1] if attempts else {"ok": False, "status": 0, "json": {}, "text": "", "final_url": ""}
    return {**last, "attempts": attempts}


def _login_email_otp_route_candidates(state: str, deps: ReauthorizeEmailOtpDeps) -> list[tuple[str, str]]:
    route_target = (
        deps.merge_url_query_fn(f"{deps.auth_base}/log-in/email-verification", _data="routes/log-in/email-verification", state=state)
        if state
        else deps.merge_url_query_fn(f"{deps.auth_base}/log-in/email-verification", _data="routes/log-in/email-verification")
    )
    return [
        (route_target, "json"),
        (route_target, "form"),
        (f"{deps.auth_base}/log-in/email-verification?state={state}" if state else f"{deps.auth_base}/log-in/email-verification", "form"),
    ]


def _login_email_otp_send_payloads() -> tuple[dict[str, Any], ...]:
    return (
        {"origin_page_type": "email_otp_send", "data": {"intent": "send"}},
        {"origin_page_type": "email_otp_send", "data": {"intent": "resend"}},
        {"origin_page_type": "email_otp_send"},
        {"origin_page_type": "email_otp_verification", "data": {"intent": "resend"}},
    )


def _email_otp_send_info_looks_ok(info: dict[str, Any]) -> bool:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    text = str(info.get("text") or body or "")
    final_url = str(info.get("final_url") or "")
    location = str(info.get("location") or "")
    return bool(
        info.get("ok")
        or int(info.get("status") or 0) in (200, 204, 302)
        or "email-verification" in f"{text} {final_url} {location}"
    )


def _send_login_otp_via_route_action(
    registrar: Any,
    *,
    referer_path: str,
    route_candidates: list[tuple[str, str]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for payload in _login_email_otp_send_payloads():
        info = registrar._post_accounts_payload(payload, referer_path, candidates=route_candidates)
        payload_attempts = info.get("attempts") if isinstance(info.get("attempts"), list) else [info]
        attempts.extend(payload_attempts)
        if _email_otp_send_info_looks_ok(info):
            return {**info, "ok": True, "attempt": "remix:email_otp_send", "attempts": attempts}
    return None


def _send_login_otp_api_attempt(
    registrar: Any,
    *,
    method: str,
    path: str,
    referer_path: str,
    deps: ReauthorizeEmailOtpDeps,
) -> dict[str, Any]:
    url = f"{deps.auth_base}/api/accounts/email-otp/{path}"
    headers = deps.har_browser_fetch_headers_fn(referer_path, accept="application/json", content_type="")
    try:
        if method == "post":
            headers = deps.har_browser_fetch_headers_fn(referer_path, accept="application/json", content_type="application/json")
            resp = registrar.session.post(url, json={}, headers=headers, verify=False, timeout=30, allow_redirects=True)
        else:
            resp = registrar.session.get(url, headers=headers, verify=False, timeout=30, allow_redirects=True)
        status = int(resp.status_code or 0)
        return {
            "ok": status in (200, 302),
            "status": status,
            "json": deps.response_json_fn(resp),
            "text": resp.text[:2000],
            "location": str(resp.headers.get("Location") or ""),
            "final_url": str(getattr(resp, "url", url) or url),
            "referer": headers.get("referer") or "",
            "attempt": f"{method}:{path}",
            "authorize": registrar.last_authorize,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": url,
            "referer": headers.get("referer") or "",
            "attempt": f"{method}:{path}",
            "error": str(exc),
            "authorize": registrar.last_authorize,
        }


def send_login_otp(registrar: Any, state: str, deps: ReauthorizeEmailOtpDeps) -> dict[str, Any]:
    state = str(state or "").strip()
    referer_path = f"/log-in/email-verification?state={state}" if state else "/log-in/email-verification"
    attempts: list[dict[str, Any]] = []
    route_result = _send_login_otp_via_route_action(
        registrar,
        referer_path=referer_path,
        route_candidates=_login_email_otp_route_candidates(state, deps),
        attempts=attempts,
    )
    if route_result is not None:
        return route_result
    for method, path in (("get", "send"), ("post", "send"), ("post", "resend")):
        info = _send_login_otp_api_attempt(
            registrar,
            method=method,
            path=path,
            referer_path=referer_path,
            deps=deps,
        )
        attempts.append(info)
        if info.get("ok"):
            return {**info, "attempts": attempts}
    last = attempts[-1] if attempts else {"ok": False, "status": 0, "json": {}, "text": "", "final_url": f"{deps.auth_base}/api/accounts/email-otp/send"}
    return {**last, "attempts": attempts}


def _passwordless_otp_direct_attempt(
    registrar: Any,
    *,
    referer_path: str,
    deps: ReauthorizeEmailOtpDeps,
) -> dict[str, Any]:
    url = f"{deps.auth_base}/api/accounts/passwordless/send-otp"
    headers = deps.har_browser_fetch_headers_fn(referer_path, accept="application/json", content_type="application/json")
    try:
        resp = registrar.session.post(
            url,
            data="",
            headers=headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        body = deps.response_json_fn(resp)
        status = int(resp.status_code or 0)
        direct_info = {
            "ok": 200 <= status < 300,
            "status": status,
            "json": body,
            "text": str(getattr(resp, "text", "") or "")[:2000],
            "location": str(resp.headers.get("Location") or ""),
            "final_url": str(getattr(resp, "url", url) or url),
            "referer": headers.get("referer") or "",
            "attempt": "passwordless_send_otp",
        }
        return direct_info
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": url,
            "referer": headers.get("referer") or "",
            "attempt": "passwordless_send_otp",
            "error": str(exc),
        }


def _passwordless_otp_route_candidates(state: str, deps: ReauthorizeEmailOtpDeps) -> list[tuple[str, str]]:
    route_target = (
        deps.merge_url_query_fn(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password", state=state)
        if state
        else deps.merge_url_query_fn(f"{deps.auth_base}/log-in/password", _data="routes/log-in/password")
    )
    return [
        (route_target, "json"),
        (route_target, "form"),
        (f"{deps.auth_base}/api/accounts", "json"),
    ]


def _passwordless_otp_fallback_looks_ok(info: dict[str, Any], attempts: list[dict[str, Any]]) -> bool:
    ok = bool(info.get("ok")) or int(info.get("status") or 0) in (200, 204, 302)
    if ok:
        return True
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        body = attempt.get("json") if isinstance(attempt.get("json"), dict) else {}
        text = str(attempt.get("text") or body or "")
        if int(attempt.get("status") or 0) in (200, 204, 302) or "email_otp" in text or "email-verification" in text:
            return True
    return False


def _passwordless_otp_route_action_attempt(
    registrar: Any,
    *,
    state: str,
    referer_path: str,
    attempts: list[dict[str, Any]],
    deps: ReauthorizeEmailOtpDeps,
) -> dict[str, Any]:
    payload = {"origin_page_type": "login_password", "data": {"intent": "passwordless_login_send_otp"}}
    info = registrar._post_accounts_payload(
        payload,
        referer_path,
        candidates=_passwordless_otp_route_candidates(state, deps),
    )
    attempts.extend(info.get("attempts") if isinstance(info.get("attempts"), list) else [info])
    ok = _passwordless_otp_fallback_looks_ok(info, attempts)
    return {**info, "ok": ok, "attempt": "passwordless_login_send_otp", "attempts": attempts}


def trigger_passwordless_login_otp(registrar: Any, deps: ReauthorizeEmailOtpDeps) -> dict[str, Any]:
    state = str(registrar.last_authorize.get("state") or "").strip()
    referer_path = f"/log-in/password?state={state}" if state else "/log-in/password"
    attempts: list[dict[str, Any]] = []
    direct_info = _passwordless_otp_direct_attempt(registrar, referer_path=referer_path, deps=deps)
    attempts.append(direct_info)
    if direct_info.get("ok"):
        return {**direct_info, "attempts": attempts}
    return _passwordless_otp_route_action_attempt(
        registrar,
        state=state,
        referer_path=referer_path,
        attempts=attempts,
        deps=deps,
    )


def submit_login_email_otp_page_form(registrar: Any, page_info: dict[str, Any], deps: ReauthorizeEmailOtpDeps) -> dict[str, Any]:
    page_url = str(page_info.get("final_url") or "").strip()
    page_html = str(page_info.get("html") or page_info.get("text") or "")
    action, fields, email_name, code_name = deps.extract_email_otp_form_inputs_fn(page_html)
    if not action and not fields:
        return {"ok": False, "status": 0, "json": {}, "text": "", "location": "", "final_url": page_url, "attempt": "page_form", "error": "email_otp_form_not_found"}
    payload = dict(fields)
    if email_name and email_name not in payload:
        email = str(registrar.last_authorize.get("email") or "").strip()
        if email:
            payload[email_name] = email
    if code_name and code_name in payload and not payload.get(code_name):
        payload.pop(code_name, None)
    try:
        final_url, body = deps.post_form_and_follow_fn(
            registrar,
            page_url=page_url or f"{deps.auth_base}/log-in/email-verification",
            action=action,
            payload=payload,
        )
        return {"ok": True, "status": 200, "json": {}, "text": body[:2000], "location": "", "final_url": final_url, "attempt": "page_form", "payload_keys": sorted(payload.keys())}
    except Exception as exc:
        return {"ok": False, "status": 0, "json": {}, "text": "", "location": "", "final_url": page_url, "attempt": "page_form", "error": str(exc), "payload_keys": sorted(payload.keys())}


def _login_otp_referer_path(state: str) -> str:
    return f"/log-in/email-verification?state={state}" if state else "/log-in/email-verification"


def _login_otp_validate_headers(registrar: Any, referer_path: str, deps: ReauthorizeEmailOtpDeps) -> dict[str, str]:
    headers = deps.har_browser_fetch_headers_fn(referer_path)
    device_id = str(getattr(registrar, "device_id", "") or "").strip()
    if device_id:
        headers["oai-device-id"] = device_id
        try:
            headers["openai-sentinel-token"] = deps.build_sentinel_token_fn(registrar.session, device_id, "authorize_continue")
        except Exception:
            pass
    return headers


def _direct_validate_login_otp(
    registrar: Any,
    *,
    code: str,
    headers: dict[str, str],
    deps: ReauthorizeEmailOtpDeps,
) -> dict[str, Any]:
    request_url = f"{deps.auth_base}/api/accounts/email-otp/validate"
    try:
        resp = registrar.session.post(
            request_url,
            json={"code": str(code).strip()},
            headers=headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        body = deps.response_json_fn(resp)
        status = int(resp.status_code or 0)
        final_url = str(getattr(resp, "url", request_url) or request_url)
        return {
            "ok": 200 <= status < 300 or bool(deps.extract_callback_from_step_fn({"json": body, "location": str(resp.headers.get("Location") or ""), "final_url": final_url})),
            "status": status,
            "json": body,
            "text": resp.text[:2000],
            "location": str(resp.headers.get("Location") or ""),
            "final_url": final_url,
            "referer": headers.get("referer") or "",
            "authorize": registrar.last_authorize,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": request_url,
            "referer": headers.get("referer") or "",
            "error": str(exc),
            "authorize": registrar.last_authorize,
        }


def _login_otp_route_candidates(state: str, deps: ReauthorizeEmailOtpDeps) -> list[tuple[str, str]]:
    route_target = (
        deps.merge_url_query_fn(f"{deps.auth_base}/log-in/email-verification", _data="routes/log-in/email-verification", state=state)
        if state
        else deps.merge_url_query_fn(f"{deps.auth_base}/log-in/email-verification", _data="routes/log-in/email-verification")
    )
    return [
        (route_target, "json"),
        (route_target, "form"),
        (f"{deps.auth_base}/log-in/email-verification?state={state}" if state else f"{deps.auth_base}/log-in/email-verification", "form"),
    ]


def _login_otp_validate_payloads(state: str, code: str) -> list[dict[str, Any]]:
    clean_code = str(code or "").strip()
    payloads = [
        {"origin_page_type": "email_otp_verification", "data": {"intent": "validate", "code": clean_code}},
        {"origin_page_type": "email_otp_verification", "data": {"code": clean_code}},
        {"origin_page_type": "email_otp_verification", "code": clean_code},
    ]
    if state:
        payloads.insert(1, {"origin_page_type": "email_otp_verification", "data": {"intent": "validate", "code": clean_code, "state": state}})
    return payloads


def _remix_validate_login_otp(
    registrar: Any,
    *,
    state: str,
    code: str,
    referer_path: str,
    attempts: list[dict[str, Any]],
    deps: ReauthorizeEmailOtpDeps,
) -> dict[str, Any]:
    route_candidates = _login_otp_route_candidates(state, deps)
    for payload in _login_otp_validate_payloads(state, code):
        remix_info = registrar._post_accounts_payload(payload, referer_path, candidates=route_candidates)
        payload_attempts = remix_info.get("attempts") if isinstance(remix_info.get("attempts"), list) else [remix_info]
        attempts.extend(payload_attempts)
        callbackish = deps.extract_callback_from_step_fn(
            {
                "json": remix_info.get("json") if isinstance(remix_info.get("json"), dict) else {},
                "location": str(remix_info.get("location") or ""),
                "final_url": str(remix_info.get("final_url") or ""),
                "text": str(remix_info.get("text") or ""),
            }
        )
        if remix_info.get("ok") or callbackish:
            return {**remix_info, "ok": True, "attempt": "remix:email_otp_validate", "attempts": attempts, "authorize": registrar.last_authorize}
    return {**attempts[-1], "attempts": attempts, "authorize": registrar.last_authorize}


def validate_login_otp(registrar: Any, state: str, code: str, deps: ReauthorizeEmailOtpDeps) -> dict[str, Any]:
    state = str(state or "").strip()
    referer_path = _login_otp_referer_path(state)
    headers = _login_otp_validate_headers(registrar, referer_path, deps)
    info = _direct_validate_login_otp(registrar, code=code, headers=headers, deps=deps)
    if info.get("ok"):
        return info
    attempts = [info]
    return _remix_validate_login_otp(registrar, state=state, code=code, referer_path=referer_path, attempts=attempts, deps=deps)


def _build_email_otp_mail_wait_configs(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    deps: ReauthorizeEmailOtpStepDeps,
) -> EmailOtpMailWaitConfigs:
    quick_wait_timeout = min(3, max(1, int(wait_timeout or 1)))
    quick_wait_interval = min(1, max(1, int(wait_interval or 1)))
    return EmailOtpMailWaitConfigs(
        quick=deps.mail_wait_config_for_account_fn(
            account,
            mailbox,
            proxy=proxy,
            wait_timeout=quick_wait_timeout,
            wait_interval=quick_wait_interval,
            request_timeout=request_timeout,
        ),
        full=deps.mail_wait_config_for_account_fn(
            account,
            mailbox,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        ),
    )


def _wait_email_otp_code_once(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    mail_config: Any,
    deps: ReauthorizeEmailOtpStepDeps,
) -> str:
    wait_mailbox = deps.mailbox_for_mail_wait_fn(account, mailbox)
    code_value = str(deps.wait_for_code_fn(mail_config, wait_mailbox) or "").strip()
    deps.sync_mail_wait_state_fn(wait_mailbox, mailbox)
    return code_value


def _exclude_email_otp_code(mailbox: dict[str, Any], code_value: str) -> None:
    excluded = list(mailbox.get("_exclude_codes") or [])
    if code_value and code_value not in excluded:
        excluded.append(code_value)
        mailbox["_exclude_codes"] = excluded


def _wait_fresh_email_otp_code(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    mail_config: Any,
    deps: ReauthorizeEmailOtpStepDeps,
) -> str:
    code_value = _wait_email_otp_code_once(account, mailbox, mail_config, deps)
    if not code_value:
        return ""
    meta = mailbox.get("_last_code_meta") if isinstance(mailbox.get("_last_code_meta"), dict) else {}
    code_received_ms = int(meta.get("received_at_ms") or 0)
    threshold_ms = int(mailbox.get("_code_after_ts") or 0)
    stale_before_ms = max(0, threshold_ms - 2000)
    if threshold_ms > 0 and code_received_ms <= 0:
        deps.log_stage_fn(f"邮箱验证码缺少收信时间，已丢弃并重试：code_after_ts={threshold_ms}")
        _exclude_email_otp_code(mailbox, code_value)
        return _wait_email_otp_code_once(account, mailbox, mail_config, deps)
    if threshold_ms > 0 and code_received_ms > 0 and code_received_ms < stale_before_ms:
        deps.log_stage_fn(f"命中旧邮箱验证码，已丢弃并重试：code_received_ms={code_received_ms} < code_after_ts={threshold_ms}")
        _exclude_email_otp_code(mailbox, code_value)
        return _wait_email_otp_code_once(account, mailbox, mail_config, deps)
    return code_value


def _open_email_otp_page_for_passwordless(
    registrar: Any,
    state: str,
    debug: dict[str, Any],
    deps: ReauthorizeEmailOtpStepDeps,
) -> None:
    deps.log_stage_fn("进入邮箱验证码登录页")
    enter_info = deps.enter_login_email_otp_step_fn(registrar, state)
    debug["enter_email_otp"] = enter_info
    deps.log_stage_fn(f"邮箱验证码登录页打开结果：{deps.response_brief_fn(enter_info)}")
    debug["email_otp_page_form"] = {"skipped": True, "reason": "prefer_passwordless_send_otp"}
    deps.log_stage_fn("跳过邮箱验证码页面表单触发，直接使用无密码接口")


def _trigger_passwordless_email_otp(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    mail_config: Any,
    debug: dict[str, Any],
    deps: ReauthorizeEmailOtpStepDeps,
) -> str:
    deps.log_stage_fn("触发无密码邮箱验证码发送")
    trigger_info = deps.trigger_passwordless_login_otp_fn(registrar)
    debug["passwordless_login_otp"] = trigger_info
    debug["passwordless_login_otp_summary"] = deps.safe_response_summary_fn(trigger_info)
    deps.log_stage_fn(f"无密码邮箱验证码发送结果：{deps.response_brief_fn(trigger_info)}，方式={trigger_info.get('attempt') or '-'}")
    if not trigger_info.get("ok"):
        return ""
    deps.log_stage_fn("无密码验证码已触发，等待邮箱验证码")
    return _wait_fresh_email_otp_code(account, mailbox, mail_config, deps)


def _send_login_email_otp(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    mail_config: Any,
    debug: dict[str, Any],
    deps: ReauthorizeEmailOtpStepDeps,
) -> str:
    deps.log_stage_fn("发送登录邮箱验证码")
    send_info = deps.send_login_otp_fn(registrar, state)
    debug["send_otp"] = send_info
    debug["send_otp_summary"] = deps.safe_response_summary_fn(send_info)
    deps.log_stage_fn(f"登录邮箱验证码发送结果：{deps.response_brief_fn(send_info)}，方式={send_info.get('attempt') or '-'}")
    if not send_info.get("ok"):
        detail = ""
        for attempt in send_info.get("attempts") or [send_info]:
            if not isinstance(attempt, dict):
                continue
            text = re.sub(r"\s+", " ", str(attempt.get("text") or attempt.get("json") or "")).strip()
            detail += f" {attempt.get('attempt') or '?'}:{attempt.get('status') or 0}:{text[:180]}"
        raise RuntimeError(f"send_otp_{send_info.get('status') or 0}:{detail.strip()}")
    deps.log_stage_fn("登录验证码已触发，等待邮箱验证码")
    return _wait_fresh_email_otp_code(account, mailbox, mail_config, deps)


def _validate_email_otp_code(
    registrar: Any,
    state: str,
    code: str,
    debug: dict[str, Any],
    deps: ReauthorizeEmailOtpStepDeps,
) -> dict[str, Any]:
    deps.log_stage_fn("提交并校验邮箱验证码")
    validate_info = deps.validate_login_otp_fn(registrar, state, code)
    debug["validate_otp"] = validate_info
    debug["validate_otp_summary"] = deps.safe_response_summary_fn(validate_info)
    deps.log_stage_fn(f"邮箱验证码校验结果：{deps.response_brief_fn(validate_info)}")
    if not validate_info.get("ok"):
        raise RuntimeError(f"validate_otp_{validate_info.get('status') or 0}")
    return validate_info


def _email_otp_result_needs_followup(validate_info: dict[str, Any], deps: ReauthorizeEmailOtpStepDeps) -> bool:
    if deps.step_requires_phone_verification_fn(validate_info):
        return True
    validate_state = deps.registration_state_from_info_fn(validate_info)
    validate_kind = str(validate_state.get("kind") or "").strip()
    return validate_kind in {"about_you", "add_email", "email_otp"}


def _resolve_email_otp_callback(
    registrar: Any,
    state: str,
    validate_info: dict[str, Any],
    debug: dict[str, Any],
    deps: ReauthorizeEmailOtpStepDeps,
) -> str:
    callback = deps.resolve_callback_step_fn(registrar, validate_info, state, allow_state_resume=False)
    if callback:
        return callback
    body = validate_info.get("json") if isinstance(validate_info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    consent_url = str(body.get("continue_url") or page.get("continue_url") or "").strip()
    if deps.is_consent_like_url_fn(consent_url):
        deps.log_stage_fn(f"邮箱验证码通过，进入授权确认页：{deps.short_url_fn(consent_url)}")
        callback, consent_summary = deps.resolve_consent_callback_direct_fn(registrar, consent_url, state)
        debug["consent_direct_summary"] = consent_summary
        deps.log_stage_fn(f"授权确认页处理结果：回调{deps.ready_text_fn(callback)}，尝试次数={len((consent_summary or {}).get('attempts') or [])}")
    if callback:
        return callback
    deps.log_stage_fn("邮箱验证码通过后未直接拿到回调，尝试通过 state 恢复授权")
    callback = deps.resolve_callback_step_fn(registrar, validate_info, state, allow_state_resume=True)
    deps.log_stage_fn(f"state 恢复授权结果：回调{deps.ready_text_fn(callback)}")
    return callback


def handle_email_otp_step(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    deps: ReauthorizeEmailOtpStepDeps,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    deps.log_stage_fn("快速检查邮箱中是否已有可用验证码")
    mail_configs = _build_email_otp_mail_wait_configs(
        account,
        mailbox,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        deps=deps,
    )
    if int(mailbox.get("_code_after_ts") or 0) <= 0:
        mailbox["_code_after_ts"] = int(deps.time_time_fn() * 1000)
    code = _wait_fresh_email_otp_code(account, mailbox, mail_configs.quick, deps)
    debug: dict[str, Any] = {"email_code_received_before_resend": bool(code)}
    deps.log_stage_fn(f"已有邮箱验证码检查结果：{'已找到' if code else '未找到'}")
    if not code:
        _open_email_otp_page_for_passwordless(registrar, state, debug, deps)
    if not code:
        code = _trigger_passwordless_email_otp(registrar, account, mailbox, mail_configs.full, debug, deps)
    if not code:
        code = _send_login_email_otp(registrar, account, mailbox, state, mail_configs.full, debug, deps)
    debug["email_code_received"] = bool(code)
    deps.log_stage_fn(f"邮箱验证码接收结果：{'已收到' if code else '等待超时'}")
    if code:
        deps.log_stage_fn(f"邮箱验证码内容：{code}")
    if not code:
        raise RuntimeError("wait_for_code_timeout")
    validate_info = _validate_email_otp_code(registrar, state, code, debug, deps)
    if _email_otp_result_needs_followup(validate_info, deps):
        return "", validate_info, debug
    callback = _resolve_email_otp_callback(registrar, state, validate_info, debug, deps)
    return callback, validate_info, debug
