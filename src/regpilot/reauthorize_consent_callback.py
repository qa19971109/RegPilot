from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class ReauthorizeConsentCallbackDeps:
    auth_base: str
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    consent_session_callback_fn: Callable[..., dict[str, str] | None]
    merge_url_query_fn: Callable[..., str]
    response_json_fn: Callable[[Any], dict[str, Any]]
    safe_response_summary_fn: Callable[[dict[str, Any]], dict[str, Any]]
    extract_form_inputs_fn: Callable[[str], tuple[str, dict[str, str], str, str]]


def callback_url_from_response_info(info: dict[str, Any], deps: ReauthorizeConsentCallbackDeps) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    for value in [
        info.get("location"),
        info.get("final_url"),
        body.get("callback_url"),
        body.get("redirect_url"),
        body.get("continue_url"),
        body.get("url"),
        page.get("callback_url"),
        page.get("redirect_url"),
        page.get("continue_url"),
        page.get("url"),
    ]:
        raw = str(value or "").strip()
        if raw.startswith("/"):
            raw = f"{deps.auth_base}{raw}"
        if raw and deps.callback_params_from_url_fn(raw):
            return raw
    text = str(info.get("text") or "")
    for raw in re.findall(r"https?://[^\"'\s<>]+|/(?:auth/callback)\?[^\"'\s<>]+", text, re.I):
        raw = str(raw or "").strip()
        if raw.startswith("/"):
            raw = f"{deps.auth_base}{raw}"
        if raw and deps.callback_params_from_url_fn(raw):
            return raw
    return ""


def _absolute_form_action(action: str, *, auth_base: str, final_url: str, target: str) -> str:
    form_action = str(action or target or "").strip()
    if form_action.startswith("/"):
        return f"{auth_base}{form_action}"
    if form_action.startswith(("http://", "https://")):
        return form_action
    base = final_url or target
    parsed_base = urlparse(base)
    if form_action.startswith("?"):
        return urlunparse((parsed_base.scheme, parsed_base.netloc, parsed_base.path, parsed_base.params, form_action[1:], parsed_base.fragment))
    return f"{parsed_base.scheme}://{parsed_base.netloc}/{form_action.lstrip('/')}"


def _response_info_from_http_response(response: Any, *, fallback_url: str, deps: ReauthorizeConsentCallbackDeps) -> dict[str, Any]:
    return {
        "ok": 200 <= int(response.status_code or 0) < 400,
        "status": int(response.status_code or 0),
        "json": deps.response_json_fn(response),
        "text": str(getattr(response, "text", "") or "")[:2000],
        "location": str(response.headers.get("Location") or ""),
        "final_url": str(getattr(response, "url", fallback_url) or fallback_url),
    }


def _record_attempt_and_callback(
    summary: dict[str, Any],
    info: dict[str, Any],
    *,
    method: str,
    target_prefix: str,
    deps: ReauthorizeConsentCallbackDeps,
    payload_keys: list[str] | None = None,
) -> str:
    attempt = deps.safe_response_summary_fn(info)
    attempt["method"] = method
    attempt["target_prefix"] = target_prefix
    if payload_keys is not None:
        attempt["payload_keys"] = payload_keys
    summary["attempts"].append(attempt)
    return callback_url_from_response_info(info, deps)


def _workspace_select_payloads(workspace_id: str, fields: dict[str, str], state: str) -> list[dict[str, str]]:
    return [
        {"workspace_id": workspace_id},
        {"workspaceId": workspace_id},
        {"id": workspace_id},
        {"workspace_id": workspace_id, "state": str((fields or {}).get("state") or state or "").strip()},
    ]


def _post_consent_json(
    registrar: Any,
    url: str,
    payload: dict[str, str],
    headers: dict[str, str],
    deps: ReauthorizeConsentCallbackDeps,
) -> dict[str, Any]:
    response = registrar.session.post(url, json=payload, headers=headers, verify=False, timeout=12, allow_redirects=False)
    return _response_info_from_http_response(response, fallback_url=url, deps=deps)


def _first_org_and_project(ws_data: dict[str, Any]) -> tuple[str, str]:
    orgs = ((ws_data.get("data") or {}).get("orgs") or []) if isinstance(ws_data, dict) else []
    if not orgs:
        return "", ""
    org = orgs[0] or {}
    org_id = str(org.get("id") or "").strip()
    project_id = str(((org.get("projects") or [{}])[0]).get("id") or "").strip()
    return org_id, project_id


def _try_organization_select_for_callback(
    registrar: Any,
    summary: dict[str, Any],
    ws_data: dict[str, Any],
    ws_headers: dict[str, str],
    *,
    final_url: str,
    target: str,
    deps: ReauthorizeConsentCallbackDeps,
) -> str:
    org_id, project_id = _first_org_and_project(ws_data)
    if not org_id:
        return ""
    org_headers = dict(ws_headers)
    org_headers["referer"] = str(ws_data.get("continue_url") or final_url or target)
    org_body = {"org_id": org_id}
    if project_id:
        org_body["project_id"] = project_id
    organization_select_url = f"{deps.auth_base}/api/accounts/organization/select"
    info_org = _post_consent_json(registrar, organization_select_url, org_body, org_headers, deps)
    return _record_attempt_and_callback(
        summary,
        info_org,
        method="organization_select",
        target_prefix=organization_select_url,
        deps=deps,
        payload_keys=sorted(org_body.keys()),
    )


def _record_workspace_select_error(
    summary: dict[str, Any],
    workspace_select_url: str,
    ws_payload: dict[str, str],
    exc: Exception,
) -> None:
    summary["attempts"].append(
        {
            "method": "workspace_select",
            "target_prefix": workspace_select_url,
            "payload_keys": sorted(ws_payload.keys()),
            "error": str(exc),
        }
    )


def _try_workspace_select_for_callback(
    registrar: Any,
    headers: dict[str, str],
    fields: dict[str, str],
    summary: dict[str, Any],
    *,
    state: str,
    final_url: str,
    target: str,
    deps: ReauthorizeConsentCallbackDeps,
) -> str:
    workspace_id = str((fields or {}).get("workspace_id") or (fields or {}).get("workspaceId") or "").strip()
    if not workspace_id:
        return ""
    ws_headers = dict(headers)
    ws_headers["referer"] = final_url or target
    ws_headers["content-type"] = "application/json"
    workspace_select_url = f"{deps.auth_base}/api/accounts/workspace/select"
    for ws_payload in _workspace_select_payloads(workspace_id, fields, state):
        try:
            info_ws = _post_consent_json(registrar, workspace_select_url, ws_payload, ws_headers, deps)
            cb = _record_attempt_and_callback(
                summary,
                info_ws,
                method="workspace_select",
                target_prefix=workspace_select_url,
                deps=deps,
                payload_keys=sorted(ws_payload.keys()),
            )
            if cb:
                return cb
            ws_data = info_ws.get("json") if isinstance(info_ws.get("json"), dict) else {}
            if isinstance(ws_data, dict):
                cb = _try_organization_select_for_callback(
                    registrar,
                    summary,
                    ws_data,
                    ws_headers,
                    final_url=final_url,
                    target=target,
                    deps=deps,
                )
                if cb:
                    return cb
        except Exception as exc:
            _record_workspace_select_error(summary, workspace_select_url, ws_payload, exc)
    return ""


def _normalized_consent_url(consent_url: str, deps: ReauthorizeConsentCallbackDeps) -> str:
    url = str(consent_url or "").strip()
    if url.startswith("/"):
        return f"{deps.auth_base}{url}"
    return url


def _consent_headers(registrar: Any, state: str) -> dict[str, str]:
    referer_path = f"/sign-in-with-chatgpt/codex/consent?state={state}" if state else "/sign-in-with-chatgpt/codex/consent"
    headers = dict(registrar._build_accounts_headers(referer_path, "authorize_continue"))
    headers["accept"] = "application/json, text/html, */*"
    return headers


def _try_browser_like_consent_callback(
    registrar: Any,
    url: str,
    state: str,
    summary: dict[str, Any],
    deps: ReauthorizeConsentCallbackDeps,
) -> str:
    try:
        browser_like_steps: list[dict[str, Any]] = []
        params = deps.consent_session_callback_fn(
            registrar.session,
            url,
            str(getattr(registrar, "device_id", "") or ""),
            state=state,
            debug_steps=browser_like_steps,
        )
        summary["attempts"].append({"method": "browser_like_consent_session", "target_prefix": url[:160], "matched": bool(params), "steps": browser_like_steps[:20]})
        if params:
            code = str(params.get("code") or "").strip()
            cb_state = str(params.get("state") or state or "").strip()
            if code and cb_state:
                return f"http://localhost:1455/auth/callback?code={code}&state={cb_state}"
            if code:
                return f"http://localhost:1455/auth/callback?code={code}"
    except Exception as exc:
        summary["attempts"].append({"method": "browser_like_consent_session", "target_prefix": url[:160], "error": str(exc)})
    return ""


def _consent_candidates(url: str, state: str, deps: ReauthorizeConsentCallbackDeps) -> list[str]:
    candidates = [url]
    if state and "state=" not in url:
        candidates.append(deps.merge_url_query_fn(url, state=state))
    return list(dict.fromkeys(candidates))


def _consent_request_methods(state: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("get", {}),
        ("post", {"json": {}}),
        ("post", {"json": {"state": state}} if state else {"json": {}}),
        ("post", {"json": {"action": "approve", "state": state}} if state else {"json": {"action": "approve"}}),
    ]


def _try_consent_form_callback(
    registrar: Any,
    response: Any,
    headers: dict[str, str],
    summary: dict[str, Any],
    *,
    state: str,
    final_url: str,
    target: str,
    deps: ReauthorizeConsentCallbackDeps,
) -> str:
    try:
        action, fields, email_name, code_name = deps.extract_form_inputs_fn(str(getattr(response, "text", "") or ""))
    except Exception:
        action, fields, email_name, code_name = "", {}, "", ""
    if not action and not fields:
        return ""
    cb = _try_workspace_select_for_callback(
        registrar,
        headers,
        fields or {},
        summary,
        state=state,
        final_url=final_url,
        target=target,
        deps=deps,
    )
    if cb:
        return cb
    form_action = _absolute_form_action(action or target, auth_base=deps.auth_base, final_url=final_url, target=target)
    payload = dict(fields or {})
    if state and not payload.get("state"):
        payload["state"] = state
    if email_name and email_name not in payload:
        payload[email_name] = ""
    if code_name and code_name not in payload:
        payload[code_name] = ""
    form_headers = dict(headers)
    form_headers["content-type"] = "application/x-www-form-urlencoded"
    form_headers["referer"] = final_url or target
    try:
        r_form = registrar.session.post(form_action, data=payload, headers=form_headers, verify=False, timeout=12, allow_redirects=False)
        info_form = _response_info_from_http_response(r_form, fallback_url=form_action, deps=deps)
        cb = _record_attempt_and_callback(
            summary,
            info_form,
            method="form_post",
            target_prefix=form_action[:160],
            deps=deps,
            payload_keys=sorted(payload.keys())[:20],
        )
        if cb:
            return cb
        form_follow = str(info_form.get("location") or info_form.get("final_url") or "").strip()
        if form_follow.startswith("/"):
            form_follow = f"{deps.auth_base}{form_follow}"
        if form_follow and form_follow != form_action:
            r_follow = registrar.session.get(form_follow, headers=headers, verify=False, timeout=12, allow_redirects=True)
            info_follow = _response_info_from_http_response(r_follow, fallback_url=form_follow, deps=deps)
            return _record_attempt_and_callback(summary, info_follow, method="form_follow", target_prefix=form_follow[:160], deps=deps)
    except Exception as exc:
        summary["attempts"].append({"method": "form_post", "target_prefix": str(form_action)[:160], "error": str(exc)})
    return ""


def _try_consent_follow_callbacks(
    registrar: Any,
    headers: dict[str, str],
    summary: dict[str, Any],
    *,
    body: dict[str, Any],
    location: str,
    final_url: str,
    target: str,
    deps: ReauthorizeConsentCallbackDeps,
) -> str:
    follow_candidates = [
        body.get("continue_url") if isinstance(body, dict) else "",
        body.get("redirect_url") if isinstance(body, dict) else "",
        location,
        final_url,
    ]
    for follow in follow_candidates:
        follow = str(follow or "").strip()
        if not follow or follow == target:
            continue
        if follow.startswith("/"):
            follow = f"{deps.auth_base}{follow}"
        try:
            r2 = registrar.session.get(follow, headers=headers, verify=False, timeout=8, allow_redirects=True)
            info2 = _response_info_from_http_response(r2, fallback_url=follow, deps=deps)
            cb = _record_attempt_and_callback(summary, info2, method="follow", target_prefix=follow[:160], deps=deps)
            if cb:
                return cb
        except Exception as exc:
            summary["attempts"].append({"method": "follow", "target_prefix": follow[:160], "error": str(exc)})
    return ""


def _try_consent_request_callback(
    registrar: Any,
    target: str,
    method: str,
    kwargs: dict[str, Any],
    headers: dict[str, str],
    summary: dict[str, Any],
    *,
    state: str,
    deps: ReauthorizeConsentCallbackDeps,
) -> str:
    resp = registrar.session.request(method.upper(), target, headers=headers, verify=False, timeout=8, allow_redirects=False, **kwargs)
    info = _response_info_from_http_response(resp, fallback_url=target, deps=deps)
    body = info["json"]
    loc = str(info.get("location") or "")
    final = str(info.get("final_url") or target)
    cb = _record_attempt_and_callback(summary, info, method=method, target_prefix=target[:160], deps=deps)
    if cb:
        return cb
    cb = _try_consent_form_callback(
        registrar,
        resp,
        headers,
        summary,
        state=state,
        final_url=final,
        target=target,
        deps=deps,
    )
    if cb:
        return cb
    return _try_consent_follow_callbacks(
        registrar,
        headers,
        summary,
        body=body,
        location=loc,
        final_url=final,
        target=target,
        deps=deps,
    )


def resolve_consent_callback_direct(
    registrar: Any,
    consent_url: str,
    state: str,
    deps: ReauthorizeConsentCallbackDeps,
) -> tuple[str, dict[str, Any]]:
    url = _normalized_consent_url(consent_url, deps)
    if not url:
        return "", {"error": "empty_consent_url"}
    headers = _consent_headers(registrar, state)
    summary: dict[str, Any] = {"url_prefix": url[:160], "attempts": []}
    callback = _try_browser_like_consent_callback(registrar, url, state, summary, deps)
    if callback:
        return callback, summary
    for target in _consent_candidates(url, state, deps):
        for method, kwargs in _consent_request_methods(state):
            try:
                cb = _try_consent_request_callback(registrar, target, method, kwargs, headers, summary, state=state, deps=deps)
                if cb:
                    return cb, summary
            except Exception as exc:
                summary["attempts"].append({"method": method, "target_prefix": target[:160], "error": str(exc)})
    return "", summary
