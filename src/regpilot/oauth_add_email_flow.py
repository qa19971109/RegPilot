from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse


@dataclass(frozen=True)
class AddEmailAddressStage:
    resolved_bind_email: str
    mailbox: dict[str, Any] | None
    action: str
    add_email_page_url: str
    next_url: str
    next_html: str
    callback_url: str = ""


@dataclass(frozen=True)
class AddEmailCodePageStage:
    next_url: str
    next_html: str
    callback_url: str = ""


@dataclass(frozen=True)
class AddEmailCodeForm:
    action: str
    body: str


@dataclass(frozen=True)
class AddEmailSubmitChoice:
    score: int
    name: str
    value: str
    formaction: str


@dataclass(frozen=True)
class AddEmailContinuationRuntime:
    request_with_retry_fn: Any
    navigate_headers_fn: Any
    response_json_fn: Any
    is_add_email_page_url_fn: Any
    prepare_bind_mailbox_fn: Any
    callback_params_from_url_fn: Any
    extract_form_inputs_fn: Any
    post_form_and_follow_fn: Any
    submit_add_email_api_fn: Any
    send_attempt_summary_fn: Any
    continue_url_from_step_fn: Any
    refresh_code_page_fn: Any
    wait_for_code_fn: Any
    bind_email_wait_config_fn: Any
    has_code_form_fn: Any
    submit_code_form_fn: Any
    url_indicates_completion_fn: Any
    validate_code_api_fn: Any
    now_ms_fn: Any


def prepare_bind_mailbox(
    mail_config: dict[str, Any] | None,
    explicit_email: str,
    *,
    create_mailbox_fn: Any,
) -> tuple[str, dict[str, Any] | None]:
    bind_email = str(explicit_email or "").strip()
    if bind_email:
        cfg = mail_config if isinstance(mail_config, dict) else {}
        providers = cfg.get("providers") if isinstance(cfg, dict) else []
        provider = providers[0] if isinstance(providers, list) and providers and isinstance(providers[0], dict) else {}
        provider_type = str(provider.get("type") or "").strip()
        if not provider_type:
            return bind_email, None
        mailbox = {"provider": provider_type, "email": bind_email, "bind_email": bind_email}
        for key in (
            "base_url",
            "api_key",
            "domain",
            "admin_auth",
            "custom_auth",
            "imap_user",
            "imap_password",
            "cookies_json",
            "cookies_path",
            "host",
            "hme_label",
        ):
            value = provider.get(key)
            if value not in (None, ""):
                mailbox[key] = value
        return bind_email, mailbox
    cfg = mail_config if isinstance(mail_config, dict) else {}
    if not cfg:
        raise RuntimeError("bind_email_required")
    mailbox = create_mailbox_fn(cfg, None)
    email = str(mailbox.get("email") or "").strip()
    if not email:
        raise RuntimeError("bind_email_create_failed")
    return email, mailbox


def add_email_headers(
    registrar: Any,
    referer: str,
    *,
    auth_base_value: str,
    common_headers_fn: Any,
    trace_headers_fn: Any,
    sentinel_token_fn: Any,
    content_type: str = "application/json",
    include_sentinel: bool = True,
) -> dict[str, str]:
    headers = common_headers_fn()
    headers["accept"] = "application/json, text/plain, */*"
    headers["accept-language"] = "zh-CN,zh;q=0.9"
    headers["referer"] = str(referer or f"{auth_base_value}/add-email")
    headers["oai-device-id"] = registrar.device_id
    headers.update(trace_headers_fn())
    if include_sentinel:
        try:
            headers["openai-sentinel-token"] = sentinel_token_fn(registrar.session, registrar.device_id, "authorize_continue")
        except Exception:
            pass
    if content_type:
        headers["content-type"] = content_type
    else:
        headers.pop("content-type", None)
    return headers


def _add_email_send_attempts(email_address: str) -> tuple[tuple[str, dict[str, Any], bool], ...]:
    return (
        ("/api/accounts/add-email/send", {"origin_page_type": "add_email", "data": {"email": email_address}}, False),
        ("/api/accounts/add-email/send", {"origin_page_type": "add_email", "data": {"email": email_address}}, True),
        ("/api/accounts/add-email/send", {"origin_page_type": "add_email", "email": email_address}, False),
        ("/api/accounts/add-email/send", {"origin_page_type": "add_email", "email": email_address}, True),
        ("/api/accounts/add-email/send", {"email": email_address}, False),
        ("/api/accounts/add-email/send", {"email": email_address}, True),
        ("/api/accounts/add-email", {"email": email_address}, False),
        ("/api/accounts/email-otp/send", {"email": email_address}, False),
        ("/api/accounts/email-otp/send", {}, False),
    )


def _submit_add_email_send_attempt(
    registrar: Any,
    *,
    url: str,
    path: str,
    payload: dict[str, Any],
    include_sentinel: bool,
    referer: str,
    add_email_headers_fn: Any,
    request_with_retry_fn: Any,
    response_json_fn: Any,
) -> dict[str, Any]:
    try:
        response, error = request_with_retry_fn(
            registrar.session,
            "post",
            url,
            json=payload,
            headers=add_email_headers_fn(registrar, referer, include_sentinel=include_sentinel),
            allow_redirects=False,
            verify=False,
        )
        status = int(getattr(response, "status_code", 0) or 0) if response is not None else 0
        return {
            "ok": response is not None and 200 <= status < 300,
            "status": status,
            "json": response_json_fn(response) if response is not None else {},
            "text": str(getattr(response, "text", "") or "")[:1000] if response is not None else "",
            "location": str(getattr(response, "headers", {}).get("Location") or "") if response is not None else "",
            "final_url": str(getattr(response, "url", url) or url) if response is not None else url,
            "attempt": path,
            "payload_keys": sorted(payload.keys()),
            "sentinel": include_sentinel,
            "error": error or "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": url,
            "attempt": path,
            "payload_keys": sorted(payload.keys()),
            "sentinel": include_sentinel,
            "error": str(exc),
        }


def submit_add_email_api(
    registrar: Any,
    email_address: str,
    referer: str,
    *,
    auth_base_value: str,
    add_email_headers_fn: Any,
    request_with_retry_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for path, payload, include_sentinel in _add_email_send_attempts(email_address):
        url = f"{auth_base_value}{path}"
        info = _submit_add_email_send_attempt(
            registrar,
            url=url,
            path=path,
            payload=payload,
            include_sentinel=include_sentinel,
            referer=referer,
            add_email_headers_fn=add_email_headers_fn,
            request_with_retry_fn=request_with_retry_fn,
            response_json_fn=response_json_fn,
        )
        attempts.append(info)
        if info.get("ok") or callback_params_from_url_fn(str(info.get("location") or info.get("final_url") or "")):
            return {**info, "attempts": attempts}
    return {**attempts[-1], "attempts": attempts} if attempts else {"ok": False, "status": 0, "attempts": []}


def add_email_send_attempt_summary(item: dict[str, Any]) -> str:
    attempt = str((item or {}).get("attempt") or "-")
    status = str((item or {}).get("status") or 0)
    body = (item or {}).get("json") if isinstance((item or {}).get("json"), dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = str(error.get("code") or body.get("code") or body.get("error_code") or "").strip()
    message = str(error.get("message") or body.get("message") or (item or {}).get("error") or "").strip()
    detail = code or message
    suffix = f"/{detail[:80]}" if detail else ""
    sentinel = "sentinel" if (item or {}).get("sentinel") else "browser"
    return f"{attempt}:{status}:{sentinel}{suffix}"


def _unique_json_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_payloads: set[str] = set()
    unique_payloads: list[dict[str, Any]] = []
    for payload in payloads:
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if key in seen_payloads:
            continue
        seen_payloads.add(key)
        unique_payloads.append(payload)
    return unique_payloads


def _add_email_code_validation_payloads(clean_code: str, clean_email: str, state: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = [
        {"code": clean_code},
        {"origin_page_type": "add_email", "code": clean_code},
        {"origin_page_type": "add_email", "data": {"code": clean_code}},
        {"origin_page_type": "email_otp_verification", "code": clean_code},
        {"origin_page_type": "email_otp_verification", "data": {"code": clean_code}},
    ]
    if clean_email:
        payloads.extend([
            {"email": clean_email, "code": clean_code},
            {"origin_page_type": "add_email", "email": clean_email, "code": clean_code},
            {"origin_page_type": "add_email", "data": {"email": clean_email, "code": clean_code}},
            {"origin_page_type": "email_otp_verification", "email": clean_email, "code": clean_code},
            {"origin_page_type": "email_otp_verification", "data": {"email": clean_email, "code": clean_code}},
        ])
    if state:
        payloads.extend([
            {"code": clean_code, "state": state},
            {"origin_page_type": "add_email", "code": clean_code, "state": state},
            {"origin_page_type": "add_email", "data": {"code": clean_code, "state": state}},
            {"origin_page_type": "email_otp_verification", "code": clean_code, "state": state},
            {"origin_page_type": "email_otp_verification", "data": {"code": clean_code, "state": state}},
        ])
        if clean_email:
            payloads.extend([
                {"email": clean_email, "code": clean_code, "state": state},
                {"origin_page_type": "add_email", "email": clean_email, "code": clean_code, "state": state},
                {"origin_page_type": "add_email", "data": {"email": clean_email, "code": clean_code, "state": state}},
                {"origin_page_type": "email_otp_verification", "email": clean_email, "code": clean_code, "state": state},
                {"origin_page_type": "email_otp_verification", "data": {"email": clean_email, "code": clean_code, "state": state}},
            ])
    return _unique_json_payloads(payloads)


def _should_skip_add_email_validation_payload(path: str, payload: dict[str, Any], clean_email: str) -> bool:
    return bool(
        path.startswith("/api/accounts/add-email")
        and clean_email
        and "email" not in payload
        and "data" not in payload
    )


def _post_add_email_code_validation_attempt(
    registrar: Any,
    *,
    auth_base_value: str,
    path: str,
    payload: dict[str, Any],
    referer: str,
    add_email_headers_fn: Any,
    request_with_retry_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
) -> dict[str, Any]:
    url = f"{auth_base_value}{path}"
    headers = add_email_headers_fn(registrar, referer)
    try:
        response, error = request_with_retry_fn(
            registrar.session,
            "post",
            url,
            json=payload,
            headers=headers,
            allow_redirects=False,
            verify=False,
        )
        status = int(getattr(response, "status_code", 0) or 0) if response is not None else 0
        body = response_json_fn(response) if response is not None else {}
        location = str(getattr(response, "headers", {}).get("Location") or "") if response is not None else ""
        final_url = str(getattr(response, "url", url) or url) if response is not None else url
        return {
            "ok": response is not None and (200 <= status < 300 or bool(callback_params_from_url_fn(location or final_url))),
            "status": status,
            "json": body,
            "text": str(getattr(response, "text", "") or "")[:1000] if response is not None else "",
            "location": location,
            "final_url": final_url,
            "error": error or "",
            "attempt": path,
            "payload_keys": sorted(payload.keys()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": url,
            "error": str(exc),
            "attempt": path,
            "payload_keys": sorted(payload.keys()),
        }


def validate_add_email_code_api(
    registrar: Any,
    code: str,
    referer: str,
    email_address: str = "",
    *,
    auth_base_value: str,
    add_email_headers_fn: Any,
    request_with_retry_fn: Any,
    response_json_fn: Any,
    callback_params_from_url_fn: Any,
    continue_url_from_step_fn: Any,
    url_indicates_completion_fn: Any,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    clean_code = str(code or "").strip()
    clean_email = str(email_address or "").strip()
    state = str((getattr(registrar, "last_authorize", {}) or {}).get("state") or "").strip()
    unique_payloads = _add_email_code_validation_payloads(clean_code, clean_email, state)
    for path in ("/api/accounts/add-email/validate", "/api/accounts/add-email/verify", "/api/accounts/email-otp/validate"):
        for payload in unique_payloads:
            if _should_skip_add_email_validation_payload(path, payload, clean_email):
                continue
            info = _post_add_email_code_validation_attempt(
                registrar,
                auth_base_value=auth_base_value,
                path=path,
                payload=payload,
                referer=referer,
                add_email_headers_fn=add_email_headers_fn,
                request_with_retry_fn=request_with_retry_fn,
                response_json_fn=response_json_fn,
                callback_params_from_url_fn=callback_params_from_url_fn,
            )
            attempts.append(info)
            if info.get("ok") and url_indicates_completion_fn(continue_url_from_step_fn(info, str(info.get("final_url") or ""))):
                return {**info, "attempts": attempts}
    return {**attempts[-1], "attempts": attempts} if attempts else {"ok": False, "status": 0, "json": {}, "text": "", "location": "", "final_url": f"{auth_base_value}/api/accounts/email-otp/validate", "error": ""}


def refresh_add_email_code_page(
    registrar: Any,
    current_url: str,
    *,
    auth_base_value: str,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
    trace_headers_fn: Any,
    sentinel_token_fn: Any,
    callback_params_from_url_fn: Any,
    has_code_form_fn: Any,
) -> tuple[str, str]:
    state = str((getattr(registrar, "last_authorize", {}) or {}).get("state") or "").strip()
    candidates = [
        str(current_url or "").strip() or f"{auth_base_value}/add-email",
        f"{auth_base_value}/add-email?state={quote(state, safe='')}" if state else "",
        f"{auth_base_value}/add-email",
    ]
    seen: set[str] = set()
    for target in candidates:
        if not target or target in seen:
            continue
        seen.add(target)
        headers = navigate_headers_fn()
        headers["accept"] = "application/json, text/html, */*"
        headers["referer"] = target
        headers["oai-device-id"] = registrar.device_id
        headers.update(trace_headers_fn())
        try:
            headers["openai-sentinel-token"] = sentinel_token_fn(registrar.session, registrar.device_id, "authorize_continue")
        except Exception:
            pass
        response, _ = request_with_retry_fn(
            registrar.session,
            "get",
            target,
            headers=headers,
            allow_redirects=True,
            verify=False,
        )
        if response is None:
            continue
        final_url = str(getattr(response, "url", target) or target)
        try:
            html = str(response.text or "")
        except Exception:
            html = ""
        if callback_params_from_url_fn(final_url) or has_code_form_fn(html):
            return final_url, html
    return "", ""


def submit_add_email_code_form(
    registrar: Any,
    *,
    page_url: str,
    page_html: str,
    fallback_action: str,
    code: str,
    extract_code_form_inputs_fn: Any,
    post_form_and_follow_fn: Any,
    result_url_fn: Any,
) -> tuple[str, str]:
    verify_action, verify_hidden, _, verify_code_name = extract_code_form_inputs_fn(page_html)
    if not verify_action:
        verify_action = fallback_action
    if not verify_code_name:
        verify_code_name = "code"
    verify_payload = dict(verify_hidden)
    verify_payload[verify_code_name] = code
    final_url, body = post_form_and_follow_fn(
        registrar,
        page_url=page_url,
        action=verify_action,
        payload=verify_payload,
    )
    resolved_url = result_url_fn(final_url, body)
    return resolved_url or final_url, body


def continue_url_from_step(info: dict[str, Any], fallback: str = "", *, auth_base_value: str) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    for value in (
        body.get("continue_url"),
        body.get("redirect_url"),
        body.get("url"),
        page.get("continue_url"),
        page.get("redirect_url"),
        page.get("url"),
        info.get("location"),
        info.get("final_url"),
        fallback,
    ):
        raw = str(value or "").strip()
        if not raw:
            continue
        if raw.startswith("/"):
            return f"{auth_base_value}{raw}"
        return raw
    return ""


def add_email_result_url(
    final_url: str,
    body_text: str = "",
    *,
    iter_flow_candidates_fn: Any,
    flow_url_priority_fn: Any,
    callback_params_from_url_fn: Any,
    choose_preferred_flow_url_fn: Any,
) -> str:
    candidates = iter_flow_candidates_fn([body_text, final_url])
    for candidate in sorted(candidates, key=flow_url_priority_fn):
        if callback_params_from_url_fn(candidate):
            return candidate
    non_callback_candidates = [candidate for candidate in candidates if not callback_params_from_url_fn(candidate)]
    return choose_preferred_flow_url_fn(non_callback_candidates, fallback=final_url)


def is_add_email_page_url(url: str) -> bool:
    try:
        path = urlparse(str(url or "")).path.rstrip("/").lower()
    except Exception:
        path = ""
    return path.endswith("/add-email")


def add_email_url_indicates_completion(url: str, *, callback_params_from_url_fn: Any) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    if callback_params_from_url_fn(raw):
        return True
    lowered = raw.lower()
    pending_markers = (
        "/add-email",
        "/email-verification",
        "/email-otp",
        "/api/accounts/add-email",
        "/api/accounts/email-otp",
    )
    return not any(marker in lowered for marker in pending_markers)


def has_add_email_code_form(html_text: str, *, extract_code_form_inputs_fn: Any) -> bool:
    text = str(html_text or "")
    if not text:
        return False
    _action, fields, _email_name, code_name = extract_code_form_inputs_fn(text)
    if code_name:
        return True
    return any("code" in str(key).lower() or "otp" in str(key).lower() for key in fields)


def bind_email_wait_config(config: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(config or {})
    try:
        base_timeout = int(out.get("wait_timeout") or 0)
    except (TypeError, ValueError):
        base_timeout = 0
    try:
        bind_timeout = int(out.get("bind_email_wait_timeout") or out.get("add_email_wait_timeout") or 180)
    except (TypeError, ValueError):
        bind_timeout = 180
    if bind_timeout > base_timeout:
        out["wait_timeout"] = bind_timeout
    return out


def _score_add_email_code_form(match: re.Match[str], *, strip_html_tags_fn: Any) -> tuple[int, int]:
    attrs = match.group(1) or ""
    body = match.group(2) or ""
    haystack = f"{attrs}\n{strip_html_tags_fn(body)}".lower()
    score = 0
    for token, weight in [
        ("email-verification", 44),
        ("email verification", 44),
        ("add-email/verify", 42),
        ("email-otp", 38),
        ("otp", 34),
        ("verification code", 30),
        ("code", 24),
        ("verify", 22),
        ("confirm", 12),
        ("continue", 8),
    ]:
        if token in haystack:
            score += weight
    if re.search(r"<input\b[^>]*name\s*=\s*[\"'][^\"']*(?:code|otp)[^\"']*[\"']", body, re.I):
        score += 80
    if re.search(r"<input\b[^>]*(?:type\s*=\s*[\"']email[\"']|name\s*=\s*[\"'][^\"']*email[^\"']*[\"'])", body, re.I):
        score -= 45
    return score, len(body)


def _select_add_email_code_form(
    text: str,
    *,
    extract_attr_fn: Any,
    strip_html_tags_fn: Any,
) -> AddEmailCodeForm | None:
    form_matches = list(re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.I | re.S))
    if not form_matches:
        return None
    form_match = max(form_matches, key=lambda match: _score_add_email_code_form(match, strip_html_tags_fn=strip_html_tags_fn))
    return AddEmailCodeForm(
        action=extract_attr_fn(form_match.group(1) or "", "action"),
        body=form_match.group(2) or "",
    )


def _add_email_code_submit_score(label: str, input_type: str) -> int:
    haystack = f"{label} {input_type}".lower()
    score = 0
    for token, weight in [
        ("verify", 35),
        ("verification", 30),
        ("code", 26),
        ("otp", 26),
        ("confirm", 18),
        ("continue", 10),
        ("submit", 6),
    ]:
        if token in haystack:
            score += weight
    for token, weight in [("send", -18), ("resend", -14), ("email", -8)]:
        if token in haystack:
            score += weight
    return score


def _prefer_add_email_submit_choice(
    current: AddEmailSubmitChoice | None,
    candidate: AddEmailSubmitChoice,
) -> AddEmailSubmitChoice:
    if current is None or candidate.score > current.score:
        return candidate
    return current


def _collect_add_email_code_input_fields(
    form_body: str,
    *,
    extract_attr_fn: Any,
) -> tuple[dict[str, str], str, str, AddEmailSubmitChoice | None]:
    hidden: dict[str, str] = {}
    email_name = ""
    code_name = ""
    submit_choice: AddEmailSubmitChoice | None = None
    for input_match in re.finditer(r"<input\b([^>]*)>", form_body, re.I | re.S):
        attrs = input_match.group(1) or ""
        name = extract_attr_fn(attrs, "name")
        if not name:
            continue
        input_type = extract_attr_fn(attrs, "type").lower()
        value = extract_attr_fn(attrs, "value")
        if input_type in ("hidden", "checkbox", "radio"):
            if input_type not in ("checkbox", "radio") or re.search(r"\bchecked\b", attrs, re.I):
                hidden[name] = value
        if input_type in ("submit", "button", "image"):
            submit_choice = _prefer_add_email_submit_choice(
                submit_choice,
                AddEmailSubmitChoice(
                    score=_add_email_code_submit_score(f"{name} {value}", input_type),
                    name=name,
                    value=value,
                    formaction=extract_attr_fn(attrs, "formaction"),
                ),
            )
        if not email_name and (input_type == "email" or "email" in name.lower()):
            email_name = name
        if not code_name and ("code" in name.lower() or "otp" in name.lower()):
            code_name = name
    return hidden, email_name, code_name, submit_choice


def _collect_add_email_code_button_choice(
    form_body: str,
    submit_choice: AddEmailSubmitChoice | None,
    *,
    extract_attr_fn: Any,
    strip_html_tags_fn: Any,
) -> AddEmailSubmitChoice | None:
    for button_match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", form_body, re.I | re.S):
        attrs = button_match.group(1) or ""
        button_type = extract_attr_fn(attrs, "type").lower() or "submit"
        if button_type not in ("", "submit"):
            continue
        name = extract_attr_fn(attrs, "name")
        value = extract_attr_fn(attrs, "value") or strip_html_tags_fn(button_match.group(2) or "").strip()
        submit_choice = _prefer_add_email_submit_choice(
            submit_choice,
            AddEmailSubmitChoice(
                score=_add_email_code_submit_score(f"{name} {value}", button_type),
                name=name,
                value=value,
                formaction=extract_attr_fn(attrs, "formaction"),
            ),
        )
    return submit_choice


def _apply_add_email_submit_choice(
    action: str,
    hidden: dict[str, str],
    submit_choice: AddEmailSubmitChoice | None,
) -> str:
    if not submit_choice:
        return action
    if submit_choice.name and submit_choice.name not in hidden:
        hidden[submit_choice.name] = submit_choice.value
    return submit_choice.formaction or action


def extract_add_email_code_form_inputs(
    html_text: str,
    *,
    extract_form_inputs_fn: Any,
    extract_attr_fn: Any,
    strip_html_tags_fn: Any,
) -> tuple[str, dict[str, str], str, str]:
    text = str(html_text or "")
    selected_form = _select_add_email_code_form(text, extract_attr_fn=extract_attr_fn, strip_html_tags_fn=strip_html_tags_fn)
    if selected_form is None:
        return extract_form_inputs_fn(text)
    hidden, email_name, code_name, submit_choice = _collect_add_email_code_input_fields(
        selected_form.body,
        extract_attr_fn=extract_attr_fn,
    )
    submit_choice = _collect_add_email_code_button_choice(
        selected_form.body,
        submit_choice,
        extract_attr_fn=extract_attr_fn,
        strip_html_tags_fn=strip_html_tags_fn,
    )
    action = _apply_add_email_submit_choice(selected_form.action, hidden, submit_choice)
    return action, hidden, email_name, code_name


def _open_add_email_page(
    registrar: Any,
    continue_url: str,
    *,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
) -> tuple[str, str]:
    response, error = request_with_retry_fn(
        registrar.session,
        "get",
        continue_url,
        headers=navigate_headers_fn(),
        allow_redirects=True,
        verify=False,
    )
    if response is None:
        raise RuntimeError(error or "open_add_email_page_failed")
    return str(getattr(response, "url", "") or continue_url), str(getattr(response, "text", "") or "")


def _add_email_address_callback_stage(
    *,
    resolved_bind_email: str,
    mailbox: dict[str, Any] | None,
    add_email_page_url: str,
    add_email_page_html: str,
) -> AddEmailAddressStage:
    return AddEmailAddressStage(
        resolved_bind_email=resolved_bind_email,
        mailbox=mailbox,
        action="",
        add_email_page_url=add_email_page_url,
        next_url=add_email_page_url,
        next_html=add_email_page_html,
        callback_url=add_email_page_url,
    )


def _add_email_address_payload(
    add_email_page_html: str,
    resolved_bind_email: str,
    *,
    extract_form_inputs_fn: Any,
) -> tuple[str, dict[str, Any], str]:
    action, hidden, email_name, _ = extract_form_inputs_fn(add_email_page_html)
    if not action:
        action = "/add-email"
    if not email_name:
        email_name = "email"
    payload = dict(hidden)
    payload[email_name] = resolved_bind_email
    return action, payload, email_name


def _add_email_address_submitted_stage(
    *,
    resolved_bind_email: str,
    mailbox: dict[str, Any] | None,
    action: str,
    add_email_page_url: str,
    next_url: str,
    next_html: str,
    callback_params_from_url_fn: Any,
) -> AddEmailAddressStage:
    callback_url = next_url if callback_params_from_url_fn(next_url) else ""
    return AddEmailAddressStage(
        resolved_bind_email=resolved_bind_email,
        mailbox=mailbox,
        action=action,
        add_email_page_url=add_email_page_url,
        next_url=next_url,
        next_html=next_html,
        callback_url=callback_url,
    )


def _submit_add_email_address_stage(
    registrar: Any,
    *,
    continue_url: str,
    bind_email: str,
    bind_mail_config: dict[str, Any] | None,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
    prepare_bind_mailbox_fn: Any,
    callback_params_from_url_fn: Any,
    extract_form_inputs_fn: Any,
    post_form_and_follow_fn: Any,
    now_ms_fn: Any,
) -> AddEmailAddressStage:
    print("阶段：当前授权要求绑定邮箱")
    resolved_bind_email, mailbox = prepare_bind_mailbox_fn(bind_mail_config, bind_email)
    print(f"阶段：绑定邮箱地址：{resolved_bind_email}")
    add_email_page_url, add_email_page_html = _open_add_email_page(
        registrar,
        continue_url,
        request_with_retry_fn=request_with_retry_fn,
        navigate_headers_fn=navigate_headers_fn,
    )
    if callback_params_from_url_fn(add_email_page_url):
        return _add_email_address_callback_stage(
            resolved_bind_email=resolved_bind_email,
            mailbox=mailbox,
            add_email_page_url=add_email_page_url,
            add_email_page_html=add_email_page_html,
        )
    action, email_form_payload, email_name = _add_email_address_payload(
        add_email_page_html,
        resolved_bind_email,
        extract_form_inputs_fn=extract_form_inputs_fn,
    )
    if mailbox is not None:
        mailbox["_code_after_ts"] = int(now_ms_fn())
    print(f"阶段：提交绑定邮箱表单：提交地址={action or '-'}，字段={email_name}")
    next_url, next_html = post_form_and_follow_fn(
        registrar,
        page_url=add_email_page_url,
        action=action,
        payload=email_form_payload,
    )
    print(f"阶段：绑定邮箱表单提交结果：地址={next_url[:160] or '-'}")
    return _add_email_address_submitted_stage(
        resolved_bind_email=resolved_bind_email,
        mailbox=mailbox,
        action=action,
        add_email_page_url=add_email_page_url,
        next_url=next_url,
        next_html=next_html,
        callback_params_from_url_fn=callback_params_from_url_fn,
    )


def _send_add_email_code_and_refresh_page(
    registrar: Any,
    *,
    resolved_bind_email: str,
    next_url: str,
    next_html: str,
    add_email_page_url: str,
    submit_add_email_api_fn: Any,
    send_attempt_summary_fn: Any,
    continue_url_from_step_fn: Any,
    callback_params_from_url_fn: Any,
    refresh_code_page_fn: Any,
) -> AddEmailCodePageStage:
    api_info = submit_add_email_api_fn(registrar, resolved_bind_email, next_url or add_email_page_url)
    print(f"阶段：绑定邮箱验证码发送结果：状态码={api_info.get('status') or '-'}，成功={'是' if api_info.get('ok') else '否'}，方式={api_info.get('attempt') or '-'}")
    if not api_info.get("ok"):
        attempts = api_info.get("attempts") if isinstance(api_info.get("attempts"), list) else [api_info]
        summary = ",".join(
            send_attempt_summary_fn(item or {})
            for item in attempts
            if isinstance(item, dict)
        )
        raise RuntimeError(f"bind_email_send_failed_{api_info.get('status') or 0}:{summary}")
    api_next_url = continue_url_from_step_fn(api_info, "")
    if callback_params_from_url_fn(api_next_url):
        return AddEmailCodePageStage(next_url=next_url, next_html=next_html, callback_url=api_next_url)
    try:
        api_next_path = urlparse(api_next_url).path.lower()
    except Exception:
        api_next_path = ""
    if api_next_url and "/api/accounts/" not in api_next_path and api_next_url != next_url:
        next_url = api_next_url
    refreshed_url, refreshed_html = refresh_code_page_fn(registrar, next_url or add_email_page_url)
    if refreshed_url:
        next_url = refreshed_url
        next_html = refreshed_html
        print(f"阶段：绑定邮箱验证码页面已刷新：地址={next_url[:160] or '-'}")
    return AddEmailCodePageStage(next_url=next_url, next_html=next_html)


def _try_add_email_code_form_submit(
    registrar: Any,
    *,
    code: str,
    next_url: str,
    next_html: str,
    add_email_page_url: str,
    action: str,
    resolved_bind_email: str,
    has_code_form_fn: Any,
    submit_code_form_fn: Any,
    url_indicates_completion_fn: Any,
) -> tuple[str, str, tuple[str, str] | None]:
    if not has_code_form_fn(next_html):
        return next_url, next_html, None
    try:
        verified_url, verified_html = submit_code_form_fn(
            registrar,
            page_url=next_url or add_email_page_url,
            page_html=next_html,
            fallback_action=action,
            code=code,
        )
        if url_indicates_completion_fn(verified_url):
            print("\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801\u5df2\u901a\u8fc7")
            return next_url, next_html, (verified_url, resolved_bind_email)
        next_url = verified_url or next_url
        next_html = verified_html or next_html
        print(f"\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801\u8868\u5355\u5df2\u63d0\u4ea4\uff0c\u4f46\u5f53\u524d\u4ecd\u672a\u79bb\u5f00\u7ed1\u5b9a/\u9a8c\u8bc1\u7801\u9875\uff1a\u5730\u5740={str(next_url or '-')[:160]}")
    except Exception as exc:
        print(f"\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u8868\u5355\u9a8c\u8bc1\u7801\u515c\u5e95\u6821\u9a8c\u5931\u8d25\uff1a{str(exc)[:160]}")
    return next_url, next_html, None


def _validate_add_email_code_api_stage(
    registrar: Any,
    *,
    code: str,
    next_url: str,
    next_html: str,
    add_email_page_url: str,
    resolved_bind_email: str,
    has_code_form_fn: Any,
    url_indicates_completion_fn: Any,
    validate_code_api_fn: Any,
    continue_url_from_step_fn: Any,
) -> tuple[dict[str, Any], tuple[str, str] | None]:
    validate_info = validate_code_api_fn(registrar, code, next_url or add_email_page_url, resolved_bind_email)
    print(
        "\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801 API \u6821\u9a8c\u7ed3\u679c\uff1a"
        f"\u72b6\u6001\u7801={validate_info.get('status') or '-'}\uff0c\u6210\u529f={'\u662f' if validate_info.get('ok') else '\u5426'}\uff0c\u65b9\u5f0f={validate_info.get('attempt') or '-'}"
    )
    if validate_info.get("ok"):
        verified_url = continue_url_from_step_fn(validate_info, str(validate_info.get("final_url") or next_url))
        if url_indicates_completion_fn(verified_url):
            print("\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801\u5df2\u901a\u8fc7")
            return validate_info, (verified_url, resolved_bind_email)
        if verified_url:
            print(f"\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801 API \u5df2\u63a5\u53d7\uff0c\u4f46\u5f53\u524d\u4ecd\u672a\u79bb\u5f00\u7ed1\u5b9a/\u9a8c\u8bc1\u7801\u9875\uff1a\u5730\u5740={verified_url[:160]}")
            return validate_info, (verified_url, "")
    if not has_code_form_fn(next_html):
        attempts = validate_info.get("attempts") if isinstance(validate_info.get("attempts"), list) else [validate_info]
        summary = ",".join(
            f"{str((item or {}).get('attempt') or '-')}:{str((item or {}).get('status') or 0)}"
            for item in attempts
            if isinstance(item, dict)
        )
        print(f"\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801\u6821\u9a8c\u5931\u8d25\uff1a\u5c1d\u8bd5\u6458\u8981={summary or '-'}")
        raise RuntimeError(f"bind_email_validate_failed_{validate_info.get('status') or 0}:{summary}")
    return validate_info, None


def _submit_final_add_email_code_form(
    registrar: Any,
    *,
    code: str,
    next_url: str,
    next_html: str,
    action: str,
    resolved_bind_email: str,
    submit_code_form_fn: Any,
    url_indicates_completion_fn: Any,
) -> tuple[str, str]:
    verified_url, _ = submit_code_form_fn(
        registrar,
        page_url=next_url,
        page_html=next_html,
        fallback_action=action,
        code=code,
    )
    if url_indicates_completion_fn(verified_url):
        print("\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801\u5df2\u901a\u8fc7")
        return verified_url, resolved_bind_email
    print(f"\u9636\u6bb5\uff1a\u7ed1\u5b9a\u90ae\u7bb1\u9a8c\u8bc1\u7801\u8868\u5355\u6700\u7ec8\u63d0\u4ea4\u540e\u4ecd\u672a\u79bb\u5f00\u7ed1\u5b9a/\u9a8c\u8bc1\u7801\u9875\uff1a\u5730\u5740={str(verified_url or '-')[:160]}")
    return verified_url, ""


def _validate_add_email_code_stage(
    registrar: Any,
    *,
    code: str,
    next_url: str,
    next_html: str,
    add_email_page_url: str,
    action: str,
    resolved_bind_email: str,
    has_code_form_fn: Any,
    submit_code_form_fn: Any,
    url_indicates_completion_fn: Any,
    validate_code_api_fn: Any,
    continue_url_from_step_fn: Any,
) -> tuple[str, str]:
    next_url, next_html, form_result = _try_add_email_code_form_submit(
        registrar,
        code=code,
        next_url=next_url,
        next_html=next_html,
        add_email_page_url=add_email_page_url,
        action=action,
        resolved_bind_email=resolved_bind_email,
        has_code_form_fn=has_code_form_fn,
        submit_code_form_fn=submit_code_form_fn,
        url_indicates_completion_fn=url_indicates_completion_fn,
    )
    if form_result is not None:
        return form_result
    _, api_result = _validate_add_email_code_api_stage(
        registrar,
        code=code,
        next_url=next_url,
        next_html=next_html,
        add_email_page_url=add_email_page_url,
        resolved_bind_email=resolved_bind_email,
        has_code_form_fn=has_code_form_fn,
        url_indicates_completion_fn=url_indicates_completion_fn,
        validate_code_api_fn=validate_code_api_fn,
        continue_url_from_step_fn=continue_url_from_step_fn,
    )
    if api_result is not None:
        return api_result
    return _submit_final_add_email_code_form(
        registrar,
        code=code,
        next_url=next_url,
        next_html=next_html,
        action=action,
        resolved_bind_email=resolved_bind_email,
        submit_code_form_fn=submit_code_form_fn,
        url_indicates_completion_fn=url_indicates_completion_fn,
    )


def _probe_add_email_continue_entry(
    registrar: Any,
    continue_url: str,
    *,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
    response_json_fn: Any,
) -> tuple[str, str]:
    page_type = ""
    if not continue_url:
        return continue_url, page_type
    try:
        response, _ = request_with_retry_fn(
            registrar.session,
            "get",
            continue_url,
            headers=navigate_headers_fn(),
            allow_redirects=True,
            verify=False,
        )
        body = response_json_fn(response) if response is not None else {}
        page_type = str((body.get("page") or {}).get("type") or "").strip()
        if response is not None:
            continue_url = str(getattr(response, "url", "") or continue_url)
    except Exception:
        page_type = ""
    return continue_url, page_type


def _resolve_bind_email_code(
    bind_email_code: str,
    mailbox: dict[str, Any] | None,
    bind_mail_config: dict[str, Any] | None,
    *,
    wait_for_code_fn: Any,
    bind_email_wait_config_fn: Any,
) -> str:
    code = str(bind_email_code or "").strip()
    if not code:
        if mailbox is None:
            raise RuntimeError("bind_email_code_required")
        print("阶段：等待绑定邮箱验证码")
        code = str(wait_for_code_fn(bind_email_wait_config_fn(bind_mail_config), mailbox) or "").strip()
    if not code:
        raise RuntimeError("bind_email_code_timeout")
    print(f"阶段：已收到绑定邮箱验证码：{code}")
    return code


def _complete_add_email_code_flow(
    registrar: Any,
    *,
    bind_email_code: str,
    bind_mail_config: dict[str, Any] | None,
    email_stage: AddEmailAddressStage,
    runtime: AddEmailContinuationRuntime,
) -> tuple[str, str]:
    resolved_bind_email = email_stage.resolved_bind_email
    code_page_stage = _send_add_email_code_and_refresh_page(
        registrar,
        resolved_bind_email=resolved_bind_email,
        next_url=email_stage.next_url,
        next_html=email_stage.next_html,
        add_email_page_url=email_stage.add_email_page_url,
        submit_add_email_api_fn=runtime.submit_add_email_api_fn,
        send_attempt_summary_fn=runtime.send_attempt_summary_fn,
        continue_url_from_step_fn=runtime.continue_url_from_step_fn,
        callback_params_from_url_fn=runtime.callback_params_from_url_fn,
        refresh_code_page_fn=runtime.refresh_code_page_fn,
    )
    if code_page_stage.callback_url:
        return code_page_stage.callback_url, resolved_bind_email
    code = _resolve_bind_email_code(
        bind_email_code,
        email_stage.mailbox,
        bind_mail_config,
        wait_for_code_fn=runtime.wait_for_code_fn,
        bind_email_wait_config_fn=runtime.bind_email_wait_config_fn,
    )
    return _validate_add_email_code_stage(
        registrar,
        code=code,
        next_url=code_page_stage.next_url,
        next_html=code_page_stage.next_html,
        add_email_page_url=email_stage.add_email_page_url,
        action=email_stage.action,
        resolved_bind_email=resolved_bind_email,
        has_code_form_fn=runtime.has_code_form_fn,
        submit_code_form_fn=runtime.submit_code_form_fn,
        url_indicates_completion_fn=runtime.url_indicates_completion_fn,
        validate_code_api_fn=runtime.validate_code_api_fn,
        continue_url_from_step_fn=runtime.continue_url_from_step_fn,
    )


def _continue_with_add_email_runtime(
    registrar: Any,
    *,
    continue_url: str,
    bind_email: str,
    bind_email_code: str,
    bind_mail_config: dict[str, Any] | None,
    runtime: AddEmailContinuationRuntime,
) -> tuple[str, str]:
    continue_url, page_type = _probe_add_email_continue_entry(
        registrar,
        continue_url,
        request_with_retry_fn=runtime.request_with_retry_fn,
        navigate_headers_fn=runtime.navigate_headers_fn,
        response_json_fn=runtime.response_json_fn,
    )
    if page_type != "add_email" and not runtime.is_add_email_page_url_fn(continue_url):
        return continue_url, ""
    email_stage = _submit_add_email_address_stage(
        registrar,
        continue_url=continue_url,
        bind_email=bind_email,
        bind_mail_config=bind_mail_config,
        request_with_retry_fn=runtime.request_with_retry_fn,
        navigate_headers_fn=runtime.navigate_headers_fn,
        prepare_bind_mailbox_fn=runtime.prepare_bind_mailbox_fn,
        callback_params_from_url_fn=runtime.callback_params_from_url_fn,
        extract_form_inputs_fn=runtime.extract_form_inputs_fn,
        post_form_and_follow_fn=runtime.post_form_and_follow_fn,
        now_ms_fn=runtime.now_ms_fn,
    )
    if email_stage.callback_url:
        return email_stage.callback_url, email_stage.resolved_bind_email
    return _complete_add_email_code_flow(
        registrar,
        bind_email_code=bind_email_code,
        bind_mail_config=bind_mail_config,
        email_stage=email_stage,
        runtime=runtime,
    )

def continue_with_optional_add_email(
    registrar: Any,
    *,
    continue_url: str,
    bind_email: str = "",
    bind_email_code: str = "",
    bind_mail_config: dict[str, Any] | None = None,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
    response_json_fn: Any,
    is_add_email_page_url_fn: Any,
    prepare_bind_mailbox_fn: Any,
    callback_params_from_url_fn: Any,
    extract_form_inputs_fn: Any,
    post_form_and_follow_fn: Any,
    submit_add_email_api_fn: Any,
    send_attempt_summary_fn: Any,
    continue_url_from_step_fn: Any,
    refresh_code_page_fn: Any,
    wait_for_code_fn: Any,
    bind_email_wait_config_fn: Any,
    has_code_form_fn: Any,
    submit_code_form_fn: Any,
    url_indicates_completion_fn: Any,
    validate_code_api_fn: Any,
    now_ms_fn: Any,
) -> tuple[str, str]:
    runtime = AddEmailContinuationRuntime(
        request_with_retry_fn=request_with_retry_fn,
        navigate_headers_fn=navigate_headers_fn,
        response_json_fn=response_json_fn,
        is_add_email_page_url_fn=is_add_email_page_url_fn,
        prepare_bind_mailbox_fn=prepare_bind_mailbox_fn,
        callback_params_from_url_fn=callback_params_from_url_fn,
        extract_form_inputs_fn=extract_form_inputs_fn,
        post_form_and_follow_fn=post_form_and_follow_fn,
        submit_add_email_api_fn=submit_add_email_api_fn,
        send_attempt_summary_fn=send_attempt_summary_fn,
        continue_url_from_step_fn=continue_url_from_step_fn,
        refresh_code_page_fn=refresh_code_page_fn,
        wait_for_code_fn=wait_for_code_fn,
        bind_email_wait_config_fn=bind_email_wait_config_fn,
        has_code_form_fn=has_code_form_fn,
        submit_code_form_fn=submit_code_form_fn,
        url_indicates_completion_fn=url_indicates_completion_fn,
        validate_code_api_fn=validate_code_api_fn,
        now_ms_fn=now_ms_fn,
    )
    return _continue_with_add_email_runtime(
        registrar,
        continue_url=continue_url,
        bind_email=bind_email,
        bind_email_code=bind_email_code,
        bind_mail_config=bind_mail_config,
        runtime=runtime,
    )
