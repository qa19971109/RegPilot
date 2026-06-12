from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse, urlunparse


def contains_whatsapp_marker(payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False).lower() if isinstance(payload, (dict, list)) else str(payload or "").lower()
    return "whatsapp" in text


def callback_url_from_consent_params(params: dict[str, Any] | None, state: str = "") -> str:
    if not params:
        return ""
    code = str(params.get("code") or "").strip()
    cb_state = str(params.get("state") or state or "").strip()
    if code and cb_state:
        return f"http://localhost:1455/auth/callback?code={code}&state={cb_state}"
    if code:
        return f"http://localhost:1455/auth/callback?code={code}"
    return ""


def fetch_phone_verification_page_text(
    registrar: Any,
    candidate_url: str = "",
    *,
    auth_base_value: str,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
) -> str:
    urls = []
    if candidate_url:
        urls.append(candidate_url)
    urls.extend(
        [
            f"{auth_base_value}/phone-verification",
            f"{auth_base_value}/add-phone",
        ]
    )
    for url in urls:
        target = f"{auth_base_value}{url}" if str(url).startswith("/") else str(url)
        response, _ = request_with_retry_fn(
            registrar.session,
            "get",
            target,
            headers=navigate_headers_fn(),
            allow_redirects=True,
            verify=False,
        )
        if response is None:
            continue
        try:
            text = str(response.text or "")
        except Exception:
            text = ""
        if text:
            return text
    return ""


def is_static_flow_asset(url: str) -> bool:
    lowered = str(url or "").strip().lower()
    if not lowered:
        return False
    if "oaistatic.com/assets/" in lowered:
        return True
    return bool(re.search(r"\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|map|woff2?|ttf|eot)(?:[?#].*)?$", lowered))


def sanitize_flow_candidate(candidate: str, *, auth_base_value: str) -> str:
    normalized = str(candidate or "").strip().strip('"\'')
    if not normalized:
        return ""
    if normalized.startswith("\\/"):
        normalized = normalized.replace("\\/", "/")
    normalized = normalized.replace("&amp;", "&")
    normalized = normalized.rstrip('.,;)]}"\'')
    if normalized.startswith("/"):
        normalized = f"{auth_base_value}{normalized}"
    if not normalized.startswith(("http://", "https://")):
        return ""
    if is_static_flow_asset(normalized):
        return ""
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunparse(parsed)


def iter_flow_url_candidates(value: Any, *, auth_base_value: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _push(candidate: str) -> None:
        normalized = sanitize_flow_candidate(candidate, auth_base_value=auth_base_value)
        if not normalized:
            return
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for item in node.values():
                _walk(item)
            return
        if isinstance(node, (list, tuple, set)):
            for item in node:
                _walk(item)
            return
        text = str(node or "")
        if not text:
            return
        variants = [text]
        if "\\/" in text:
            variants.append(text.replace("\\/", "/"))
        for variant in variants:
            for match in re.finditer(r"https?://[^\"'\s<>\\]+", variant, re.I):
                _push(str(match.group(0) or ""))
            for match in re.finditer(r"(?<![A-Za-z0-9.:])/(?:authorize/resume|sign-in-with-chatgpt/codex/consent|u/[A-Za-z0-9_./-]+|create-account/[A-Za-z0-9_./-]+|add-email(?:[/?][^\"'\s<>\\]*)?|auth/callback\?[^\"'\s<>\\]+)[^\"'\s<>\\]*", variant, re.I):
                _push(str(match.group(0) or ""))

    _walk(value)
    return candidates


def flow_url_priority(
    url: str,
    *,
    auth_base_value: str,
    callback_params_from_url_fn: Any,
) -> tuple[int, int]:
    normalized = str(url or "").strip()
    lowered = normalized.lower()
    if not normalized or is_static_flow_asset(normalized):
        return (99, 0)
    if callback_params_from_url_fn(normalized):
        return (0, -len(normalized))
    if any(token in lowered for token in (
        "/authorize/resume",
        "/sign-in-with-chatgpt/codex/consent",
        "/add-email",
        "/add-phone",
        "/phone-verification",
        "/create-account/",
        "/u/signup",
        "/about-you",
    )):
        return (1, -len(normalized))
    if lowered.startswith(auth_base_value.lower()):
        return (5, -len(normalized))
    return (10, -len(normalized))


def choose_preferred_flow_url(
    candidates: list[str],
    fallback: str = "",
    *,
    auth_base_value: str,
    callback_params_from_url_fn: Any,
) -> str:
    ranked: list[str] = [candidate for candidate in candidates if not is_static_flow_asset(candidate)]
    normalized_fallback = str(fallback or "").strip()
    if normalized_fallback and not is_static_flow_asset(normalized_fallback):
        ranked.append(normalized_fallback)
    if not ranked:
        return normalized_fallback
    ranked = sorted(
        dict.fromkeys(ranked),
        key=lambda url: flow_url_priority(
            url,
            auth_base_value=auth_base_value,
            callback_params_from_url_fn=callback_params_from_url_fn,
        ),
    )
    return ranked[0]


def extract_callback_url_from_text(
    text: str,
    *,
    auth_base_value: str,
    callback_params_from_url_fn: Any,
) -> str:
    for candidate in sorted(
        iter_flow_url_candidates(text, auth_base_value=auth_base_value),
        key=lambda url: flow_url_priority(
            url,
            auth_base_value=auth_base_value,
            callback_params_from_url_fn=callback_params_from_url_fn,
        ),
    ):
        normalized = sanitize_flow_candidate(candidate, auth_base_value=auth_base_value)
        if normalized and callback_params_from_url_fn(normalized):
            return normalized
    return ""


def _password_probe_error_result(target: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "matched": False,
        "url": target,
        "final_url": target,
        "status": 0,
        "error": error,
        "title": "",
        "text": "",
    }


def _response_text(response: Any) -> str:
    try:
        return str(getattr(response, "text", "") or "")
    except Exception:
        return ""


def _html_title(text: str) -> str:
    title_match = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    return re.sub(r"\s+", " ", str(title_match.group(1) or "")).strip() if title_match else ""


def _is_password_route(final_url: str) -> bool:
    return any(
        token in final_url
        for token in (
            "/create-account/password",
            "/log-in/password",
            "/u/signup/password",
        )
    )


def _is_password_content(text: str) -> bool:
    return (
        "create-account/password" in text
        or "log-in/password" in text
        or 'type="password"' in text
        or "Enter your password" in text
        or "Create your password" in text
        or "Continue with password" in text
        or "title=\"Enter your password - OpenAI\"" in text
    )


def _password_probe_matches(
    *,
    status: int,
    final_url: str,
    text: str,
    phone_value: str,
) -> bool:
    normalized_phone_digits = re.sub(r"\D+", "", phone_value)
    html_phone_digits = re.sub(r"\D+", "", text)
    phone_matches = (
        not normalized_phone_digits
        or normalized_phone_digits in html_phone_digits
        or f'name="username" value="{phone_value}"' in text
    )
    return bool(
        status == 200
        and _is_password_route(final_url)
        and _is_password_content(text)
        and phone_matches
    )


def probe_phone_signup_password_page(
    registrar: Any,
    phone_number: str,
    *,
    auth_base_value: str,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
) -> dict[str, Any]:
    target = str((registrar.last_authorize or {}).get("final_url") or "").strip() or f"{auth_base_value}/create-account/password"
    response, error = request_with_retry_fn(
        registrar.session,
        "get",
        target,
        headers=navigate_headers_fn(),
        allow_redirects=True,
        verify=False,
    )
    if response is None:
        return _password_probe_error_result(target, error)
    final_url = str(getattr(response, "url", "") or target)
    status = int(getattr(response, "status_code", 0) or 0)
    text = _response_text(response)
    phone_value = str(phone_number or "").strip()
    matched = _password_probe_matches(
        status=status,
        final_url=final_url,
        text=text,
        phone_value=phone_value,
    )
    return {
        "ok": status == 200,
        "matched": matched,
        "url": target,
        "final_url": final_url,
        "status": status,
        "error": error,
        "title": _html_title(text),
        "text": text,
    }


def _empty_continue_page_result(continue_url: str = "", *, error: str | None = None) -> dict[str, Any]:
    result = {
        "ok": False,
        "continue_url": continue_url,
        "page_type": "",
        "callback_url": "",
        "location": "",
        "text": "",
        "json": {},
    }
    if error is not None:
        result["error"] = error
    return result


def _request_continue_page(
    registrar: Any,
    target: str,
    *,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
) -> tuple[Any, str]:
    return request_with_retry_fn(
        registrar.session,
        "get",
        target,
        headers=navigate_headers_fn(),
        allow_redirects=True,
        verify=False,
    )


def _continue_page_response_parts(
    response: Any,
    target: str,
    *,
    response_json_fn: Any,
) -> tuple[str, str, dict[str, Any], str, str]:
    final_url = str(getattr(response, "url", "") or target)
    location = str(getattr(response, "headers", {}).get("Location") or "").strip()
    if is_static_flow_asset(final_url):
        final_url = target
    body = response_json_fn(response) if response is not None else {}
    page_type = str((body.get("page") or {}).get("type") or "").strip() if isinstance(body, dict) else ""
    try:
        text = str(getattr(response, "text", "") or "")
    except Exception:
        text = ""
    return final_url, location, body if isinstance(body, dict) else {}, page_type, text


def _continue_page_callback_url(
    flow_candidates: list[str],
    *,
    auth_base_value: str,
    callback_params_from_url_fn: Any,
) -> str:
    for candidate in sorted(
        flow_candidates,
        key=lambda url: flow_url_priority(
            url,
            auth_base_value=auth_base_value,
            callback_params_from_url_fn=callback_params_from_url_fn,
        ),
    ):
        if callback_params_from_url_fn(candidate):
            return candidate
    return ""


def _continue_page_preferred_url(
    flow_candidates: list[str],
    *,
    final_url: str,
    target: str,
    auth_base_value: str,
    callback_params_from_url_fn: Any,
) -> str:
    non_callback_candidates = [candidate for candidate in flow_candidates if not callback_params_from_url_fn(candidate)]
    return choose_preferred_flow_url(
        non_callback_candidates,
        fallback=final_url or target,
        auth_base_value=auth_base_value,
        callback_params_from_url_fn=callback_params_from_url_fn,
    )


def load_continue_page(
    registrar: Any,
    continue_url: str,
    *,
    auth_base_value: str,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
) -> dict[str, Any]:
    target = str(continue_url or "").strip()
    if not target:
        return _empty_continue_page_result()
    response, error = _request_continue_page(
        registrar,
        target,
        request_with_retry_fn=request_with_retry_fn,
        navigate_headers_fn=navigate_headers_fn,
    )
    if response is None:
        return _empty_continue_page_result(target, error=error)
    final_url, location, body, page_type, text = _continue_page_response_parts(
        response,
        target,
        response_json_fn=response_json_fn,
    )
    flow_candidates = iter_flow_url_candidates([body, text, location, final_url, target], auth_base_value=auth_base_value)
    callback_url = _continue_page_callback_url(
        flow_candidates,
        auth_base_value=auth_base_value,
        callback_params_from_url_fn=callback_params_from_url_fn,
    )
    resolved_continue_url = _continue_page_preferred_url(
        flow_candidates,
        final_url=final_url,
        target=target,
        auth_base_value=auth_base_value,
        callback_params_from_url_fn=callback_params_from_url_fn,
    )
    return {
        "ok": True,
        "continue_url": resolved_continue_url or final_url,
        "page_type": page_type,
        "callback_url": callback_url,
        "location": location,
        "text": text,
        "json": body,
    }


def _embedded_callback_form_hidden(response: Any, text: str, *, extract_form_inputs_fn: Any) -> tuple[str, dict[str, Any], str, str]:
    hidden = ("", {}, "", "")
    try:
        if response is not None and 200 <= int(getattr(response, "status_code", 0) or 0) < 400:
            hidden = extract_form_inputs_fn(text)
    except Exception:
        hidden = ("", {}, "", "")
    try:
        action, fields, email_name, code_name = hidden
    except Exception:
        return "", {}, "", ""
    return str(action or ""), dict(fields or {}), str(email_name or ""), str(code_name or "")


def _embedded_callback_form_payload(
    fields: dict[str, Any],
    *,
    email_name: str,
    code_name: str,
    state: str,
) -> dict[str, Any]:
    payload = dict(fields)
    if email_name and "email" not in payload:
        payload[email_name] = ""
    if code_name and code_name not in payload:
        payload[code_name] = ""
    if "code" in payload:
        payload["code"] = str(payload.get("code") or "").strip()
    if "state" in payload and not payload.get("state"):
        payload["state"] = state
    return payload


def _submit_callback_form_request(
    active_session: Any,
    form_action: str,
    payload: dict[str, Any],
    *,
    navigate_headers_fn: Any,
    request_timeout: int,
) -> Any:
    try:
        return active_session.request(
            "POST",
            form_action,
            data=payload,
            headers=navigate_headers_fn(),
            allow_redirects=True,
            verify=False,
            timeout=max(3, int(request_timeout or 8)),
        )
    except Exception:
        return None


def _callback_from_submitted_form_response(
    form_resp: Any,
    *,
    auth_base_value: str,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
) -> str:
    form_final = str(getattr(form_resp, "url", "") or "")
    form_loc = str(getattr(form_resp, "headers", {}).get("Location") or "")
    form_body = response_json_fn(form_resp) if form_resp is not None else {}
    try:
        form_text = str(getattr(form_resp, "text", "") or "")
    except Exception:
        form_text = ""
    for candidate in iter_flow_url_candidates([form_final, form_loc, form_body, form_text], auth_base_value=auth_base_value):
        normalized_candidate = sanitize_flow_candidate(candidate, auth_base_value=auth_base_value)
        if normalized_candidate and callback_params_from_url_fn(normalized_candidate):
            return normalized_candidate
    return ""


def _callback_from_submitted_form_consent(
    active_session: Any,
    form_resp: Any,
    form_action: str,
    *,
    state: str,
    device_id: str,
    include_codex_consent: bool,
    consent_session_callback_fn: Any,
) -> str:
    if not include_codex_consent:
        return ""
    try:
        form_callback_params = consent_session_callback_fn(
            active_session,
            str(getattr(form_resp, "url", "") or getattr(form_resp, "headers", {}).get("Location") or form_action),
            device_id,
        )
    except Exception:
        form_callback_params = None
    return callback_url_from_consent_params(form_callback_params, state)


def _submit_embedded_callback_form(
    active_session: Any,
    response: Any,
    text: str,
    *,
    state: str,
    device_id: str,
    auth_base_value: str,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    consent_session_callback_fn: Any,
    extract_form_inputs_fn: Any,
    include_codex_consent: bool,
    request_timeout: int,
) -> str:
    action, fields, email_name, code_name = _embedded_callback_form_hidden(response, text, extract_form_inputs_fn=extract_form_inputs_fn)
    if not action:
        return ""
    form_action = action if action.startswith("http") else f"{auth_base_value}{action}" if action.startswith("/") else action
    payload = _embedded_callback_form_payload(fields, email_name=email_name, code_name=code_name, state=state)
    form_resp = _submit_callback_form_request(
        active_session,
        form_action,
        payload,
        navigate_headers_fn=navigate_headers_fn,
        request_timeout=request_timeout,
    )
    callback_url = _callback_from_submitted_form_response(
        form_resp,
        auth_base_value=auth_base_value,
        response_json_fn=response_json_fn,
        callback_params_from_url_fn=callback_params_from_url_fn,
    )
    if callback_url:
        return callback_url
    return _callback_from_submitted_form_consent(
        active_session,
        form_resp,
        form_action,
        state=state,
        device_id=device_id,
        include_codex_consent=include_codex_consent,
        consent_session_callback_fn=consent_session_callback_fn,
    )


def _enqueue_oauth_callback_url(urls: list[str], seen: set[str], url: str, *, auth_base_value: str) -> None:
    normalized = str(url or "").strip()
    if not normalized:
        return
    if normalized.startswith("/"):
        normalized = f"{auth_base_value}{normalized}"
    if normalized in seen:
        return
    seen.add(normalized)
    urls.append(normalized)


def _oauth_flow_response_parts(
    active_session: Any,
    url: str,
    *,
    request_timeout: int,
    navigate_headers_fn: Any,
    response_json_fn: Any,
) -> tuple[Any, str, str, Any, str]:
    try:
        response = active_session.request(
            "GET",
            url,
            headers=navigate_headers_fn(),
            allow_redirects=True,
            verify=False,
            timeout=max(3, int(request_timeout or 8)),
        )
    except Exception:
        response = None
    final_url = str(getattr(response, "url", "") or "")
    location = str(getattr(response, "headers", {}).get("Location") or "")
    body = response_json_fn(response) if response is not None else {}
    try:
        text = str(getattr(response, "text", "") or "")
    except Exception:
        text = ""
    return response, final_url, location, body, text


def _callback_from_flow_candidates(
    pending: list[str],
    local_seen: set[str],
    values: list[Any],
    *,
    auth_base_value: str,
    callback_params_from_url_fn: Any,
) -> str:
    for candidate in iter_flow_url_candidates(values, auth_base_value=auth_base_value):
        normalized_candidate = sanitize_flow_candidate(candidate, auth_base_value=auth_base_value)
        if normalized_candidate and callback_params_from_url_fn(normalized_candidate):
            return normalized_candidate
        if normalized_candidate and normalized_candidate not in local_seen:
            local_seen.add(normalized_candidate)
            pending.append(normalized_candidate)
    return ""


def _callback_from_consent_session(
    active_session: Any,
    consent_candidate: str,
    device_id: str,
    state: str,
    *,
    include_codex_consent: bool,
    consent_session_callback_fn: Any,
) -> str:
    if not include_codex_consent:
        return ""
    try:
        callback_params = consent_session_callback_fn(
            active_session,
            consent_candidate,
            device_id,
        )
    except Exception:
        callback_params = None
    return callback_url_from_consent_params(callback_params, state)


def _follow_oauth_callback_candidate(
    active_session: Any,
    device_id: str,
    url: str,
    pending: list[str],
    local_seen: set[str],
    state: str,
    *,
    request_timeout: int,
    include_codex_consent: bool,
    auth_base_value: str,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    consent_session_callback_fn: Any,
    extract_form_inputs_fn: Any,
) -> str:
    response, final_url, location, body, text = _oauth_flow_response_parts(
        active_session,
        url,
        request_timeout=request_timeout,
        navigate_headers_fn=navigate_headers_fn,
        response_json_fn=response_json_fn,
    )
    callback_url = _callback_from_flow_candidates(
        pending,
        local_seen,
        [final_url, location, body, text],
        auth_base_value=auth_base_value,
        callback_params_from_url_fn=callback_params_from_url_fn,
    )
    if callback_url:
        return callback_url
    callback_url = _callback_from_consent_session(
        active_session,
        final_url or location or url,
        device_id,
        state,
        include_codex_consent=include_codex_consent,
        consent_session_callback_fn=consent_session_callback_fn,
    )
    if callback_url:
        return callback_url
    return _submit_embedded_callback_form(
        active_session,
        response,
        text,
        state=state,
        device_id=device_id,
        auth_base_value=auth_base_value,
        navigate_headers_fn=navigate_headers_fn,
        response_json_fn=response_json_fn,
        callback_params_from_url_fn=callback_params_from_url_fn,
        consent_session_callback_fn=consent_session_callback_fn,
        extract_form_inputs_fn=extract_form_inputs_fn,
        include_codex_consent=include_codex_consent,
        request_timeout=request_timeout,
    )


def _follow_oauth_callback_candidates(
    active_session: Any,
    device_id: str,
    urls: list[str],
    seen: set[str],
    state: str,
    *,
    max_steps: int,
    request_timeout: int,
    include_codex_consent: bool,
    auth_base_value: str,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    consent_session_callback_fn: Any,
    extract_form_inputs_fn: Any,
) -> str:
    pending = list(urls)
    local_seen = set(seen)
    steps = 0
    while pending and steps < max(1, int(max_steps or 12)):
        steps += 1
        url = pending.pop(0)
        callback_url = _follow_oauth_callback_candidate(
            active_session,
            device_id,
            url,
            pending,
            local_seen,
            state=state,
            request_timeout=request_timeout,
            include_codex_consent=include_codex_consent,
            auth_base_value=auth_base_value,
            navigate_headers_fn=navigate_headers_fn,
            response_json_fn=response_json_fn,
            callback_params_from_url_fn=callback_params_from_url_fn,
            consent_session_callback_fn=consent_session_callback_fn,
            extract_form_inputs_fn=extract_form_inputs_fn,
        )
        if callback_url:
            return callback_url
    return ""


def _oauth_callback_entry_urls(
    candidate_url: str,
    state: str,
    *,
    include_codex_consent: bool,
    auth_base_value: str,
) -> tuple[list[str], set[str]]:
    urls: list[str] = []
    seen: set[str] = set()
    if candidate_url:
        _enqueue_oauth_callback_url(urls, seen, candidate_url, auth_base_value=auth_base_value)
    if state:
        _enqueue_oauth_callback_url(urls, seen, f"{auth_base_value}/authorize/resume?state={state}", auth_base_value=auth_base_value)
        if include_codex_consent:
            _enqueue_oauth_callback_url(urls, seen, f"{auth_base_value}/sign-in-with-chatgpt/codex/consent?state={state}", auth_base_value=auth_base_value)
    if include_codex_consent:
        _enqueue_oauth_callback_url(urls, seen, f"{auth_base_value}/sign-in-with-chatgpt/codex/consent", auth_base_value=auth_base_value)
    return urls, seen


def _follow_oauth_callback_for_registrar_session(
    active_session: Any,
    device_id: str,
    urls: list[str],
    seen: set[str],
    state: str,
    *,
    max_steps: int,
    request_timeout: int,
    include_codex_consent: bool,
    auth_base_value: str,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    consent_session_callback_fn: Any,
    extract_form_inputs_fn: Any,
) -> str:
    return _follow_oauth_callback_candidates(
        active_session,
        device_id,
        urls,
        seen,
        state,
        max_steps=max_steps,
        request_timeout=request_timeout,
        include_codex_consent=include_codex_consent,
        auth_base_value=auth_base_value,
        navigate_headers_fn=navigate_headers_fn,
        response_json_fn=response_json_fn,
        callback_params_from_url_fn=callback_params_from_url_fn,
        consent_session_callback_fn=consent_session_callback_fn,
        extract_form_inputs_fn=extract_form_inputs_fn,
    )


def _oauth_callback_plain_tls_fallback(
    registrar: Any,
    urls: list[str],
    seen: set[str],
    state: str,
    *,
    max_steps: int,
    request_timeout: int,
    include_codex_consent: bool,
    auth_base_value: str,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    consent_session_callback_fn: Any,
    extract_form_inputs_fn: Any,
    plain_retry_session_builder_fn: Any,
) -> str:
    plain_session = plain_retry_session_builder_fn(str(getattr(registrar, "proxy", "") or ""))
    try:
        plain_session.cookies.update(registrar.session.cookies)
        return _follow_oauth_callback_for_registrar_session(
            plain_session,
            registrar.device_id,
            urls,
            seen,
            state,
            max_steps=max_steps,
            request_timeout=request_timeout,
            include_codex_consent=include_codex_consent,
            auth_base_value=auth_base_value,
            navigate_headers_fn=navigate_headers_fn,
            response_json_fn=response_json_fn,
            callback_params_from_url_fn=callback_params_from_url_fn,
            consent_session_callback_fn=consent_session_callback_fn,
            extract_form_inputs_fn=extract_form_inputs_fn,
        )
    finally:
        plain_session.close()


def resolve_oauth_callback(
    registrar: Any,
    candidate_url: str,
    state: str,
    *,
    max_steps: int = 12,
    request_timeout: int = 8,
    include_codex_consent: bool = True,
    auth_base_value: str,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    consent_session_callback_fn: Any,
    extract_form_inputs_fn: Any,
    plain_retry_session_builder_fn: Any,
) -> str:
    if candidate_url and callback_params_from_url_fn(candidate_url):
        return candidate_url
    urls, seen = _oauth_callback_entry_urls(
        candidate_url,
        state,
        include_codex_consent=include_codex_consent,
        auth_base_value=auth_base_value,
    )
    try:
        return _follow_oauth_callback_for_registrar_session(
            registrar.session,
            registrar.device_id,
            urls,
            seen,
            state,
            max_steps=max_steps,
            request_timeout=request_timeout,
            include_codex_consent=include_codex_consent,
            auth_base_value=auth_base_value,
            navigate_headers_fn=navigate_headers_fn,
            response_json_fn=response_json_fn,
            callback_params_from_url_fn=callback_params_from_url_fn,
            consent_session_callback_fn=consent_session_callback_fn,
            extract_form_inputs_fn=extract_form_inputs_fn,
        )
    except Exception as exc:
        message = str(exc or "").strip().lower()
        if "tls connect error" not in message and "openssl_internal" not in message:
            raise
        return _oauth_callback_plain_tls_fallback(
            registrar,
            urls,
            seen,
            state,
            max_steps=max_steps,
            request_timeout=request_timeout,
            include_codex_consent=include_codex_consent,
            auth_base_value=auth_base_value,
            navigate_headers_fn=navigate_headers_fn,
            response_json_fn=response_json_fn,
            callback_params_from_url_fn=callback_params_from_url_fn,
            consent_session_callback_fn=consent_session_callback_fn,
            extract_form_inputs_fn=extract_form_inputs_fn,
            plain_retry_session_builder_fn=plain_retry_session_builder_fn,
        )
