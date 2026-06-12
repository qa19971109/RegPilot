from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlencode, urlparse, urlunparse

import requests


@dataclass(frozen=True)
class ConsentFlowDeps:
    auth_base: str
    get_common_headers: Callable[[], dict[str, str]]
    get_navigate_headers: Callable[[], dict[str, str]]
    get_user_agent: Callable[[], str]
    make_trace_headers: Callable[[], dict[str, str]]
    build_sentinel_token: Callable[[requests.Session, str, str], str]
    response_json: Callable[[Any], dict[str, Any]]
    callback_params_from_response: Callable[[Any], dict[str, str] | None]
    workspace_id_from_client_auth_session_cookie: Callable[[Any], str]
    workspace_id_from_consent_html: Callable[[str], str]
    org_project_from_consent_html: Callable[[str], tuple[str, str]]
    extract_consent_form_inputs: Callable[[str], tuple[str, dict[str, str]]]
    find_workspace_id_from_auth_session_node: Callable[[Any], str]
    find_org_project_from_auth_session_node: Callable[[Any], tuple[str, str]]


@dataclass(frozen=True)
class ConsentDataSubmitResult:
    callback_params: dict[str, str] | None = None
    invalid_state: bool = False


def merge_url_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value is None:
            continue
        query[key] = [str(value)]
    merged = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, merged, parsed.fragment))


def append_consent_debug(debug_steps: list[dict[str, Any]] | None, **step: Any) -> None:
    if debug_steps is not None:
        debug_steps.append(step)


def submit_organization_select_for_consent(
    session: requests.Session,
    org_id: str,
    project_id: str,
    headers: dict[str, str],
    referer: str,
    debug_steps: list[dict[str, Any]] | None,
    source: str,
    *,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    org_id = str(org_id or "").strip()
    project_id = str(project_id or "").strip()
    if not org_id:
        return None
    org_headers = dict(headers)
    org_headers["accept"] = "application/json"
    org_headers["content-type"] = "application/json"
    org_headers["referer"] = referer or f"{deps.auth_base}/sign-in-with-chatgpt/codex/organization"
    bodies = [{"org_id": org_id}]
    if project_id:
        bodies.insert(0, {"org_id": org_id, "project_id": project_id})
    for body in bodies:
        org_resp = session.post(
            f"{deps.auth_base}/api/accounts/organization/select",
            json=body,
            headers=org_headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        org_summary = consent_response_summary(org_resp, method="post", target=f"{deps.auth_base}/api/accounts/organization/select", source=source, deps=deps)
        org_summary["payload_keys"] = sorted(body.keys())
        append_consent_debug(debug_steps, **org_summary)
        callback_params = deps.callback_params_from_response(org_resp)
        if callback_params:
            return callback_params
    return None


def build_authorize_continue_headers(session: requests.Session, device_id: str, referer: str, *, deps: ConsentFlowDeps) -> dict[str, str]:
    headers = deps.get_common_headers()
    headers["referer"] = referer
    headers["oai-device-id"] = device_id
    headers.update(deps.make_trace_headers())
    try:
        headers["openai-sentinel-token"] = deps.build_sentinel_token(session, device_id, "authorize_continue")
    except Exception as exc:
        headers["x-openai-sentinel-error"] = str(exc)
    return headers


def consent_data_payloads(workspace_id: str, state: str) -> list[dict[str, str]]:
    payloads: list[dict[str, str]] = []

    def _add(payload: dict[str, str]) -> None:
        normalized = {key: value for key, value in payload.items() if value}
        if normalized and normalized not in payloads:
            payloads.append(normalized)

    _add({"workspace_id": workspace_id, "state": state})
    _add({"workspaceId": workspace_id, "state": state})
    _add({"workspace_id": workspace_id, "action": "authorize", "state": state})
    _add({"workspace_id": workspace_id, "action": "approve", "state": state})
    _add({"workspace_id": workspace_id, "intent": "authorize", "state": state})
    _add({"workspace_id": workspace_id, "_action": "authorize", "state": state})
    return payloads


def _workspace_select_payload(workspace_id: str) -> dict[str, str]:
    workspace_id = str(workspace_id or "").strip()
    return {"workspace_id": workspace_id} if workspace_id else {}


def _workspace_select_header_profiles(
    headers: dict[str, str],
    consent_url: str,
    *,
    deps: ConsentFlowDeps,
) -> list[tuple[str, dict[str, str]]]:
    browser_fetch_headers = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/json",
        "origin": deps.auth_base,
        "priority": "u=1, i",
        "referer": consent_url,
        "user-agent": deps.get_user_agent(),
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    browser_fetch_headers.update(deps.make_trace_headers())
    enriched_headers = dict(headers)
    enriched_headers["accept"] = "application/json"
    enriched_headers["content-type"] = "application/json"
    enriched_headers["referer"] = consent_url
    return [("browser_fetch", browser_fetch_headers), ("enriched", enriched_headers)]


def _post_workspace_select(
    session: requests.Session,
    payload: dict[str, str],
    ws_headers: dict[str, str],
    *,
    source: str,
    header_profile: str,
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> Any:
    target = f"{deps.auth_base}/api/accounts/workspace/select"
    ws_resp = session.post(
        target,
        json=payload,
        headers=ws_headers,
        verify=False,
        timeout=30,
        allow_redirects=False,
    )
    ws_summary = consent_response_summary(ws_resp, method="post", target=target, source=source, deps=deps)
    ws_summary["payload_keys"] = sorted(payload.keys())
    ws_summary["header_profile"] = header_profile
    append_consent_debug(debug_steps, **ws_summary)
    return ws_resp


def _workspace_select_orgs(ws_body: Any) -> list[Any]:
    if not isinstance(ws_body, dict):
        return []
    data = ws_body.get("data") or {}
    return (data.get("orgs") or []) if isinstance(data, dict) else []


def _submit_workspace_select_org_fallback(
    session: requests.Session,
    ws_body: Any,
    ws_headers: dict[str, str],
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    source: str,
    *,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    orgs = _workspace_select_orgs(ws_body)
    if not orgs:
        return None
    org = orgs[0] or {}
    org_id = str(org.get("id") or "").strip()
    project_id = str((org.get("projects") or [{}])[0].get("id") or "").strip()
    if not org_id:
        return None
    return submit_organization_select_for_consent(
        session,
        org_id,
        project_id,
        ws_headers,
        str(ws_body.get("continue_url") or consent_url) if isinstance(ws_body, dict) else consent_url,
        debug_steps,
        f"{source}_organization_select",
        deps=deps,
    )


def _workspace_select_follow_url(ws_body: Any) -> str:
    if not isinstance(ws_body, dict):
        return ""
    return str(ws_body.get("continue_url") or ws_body.get("redirect_url") or ws_body.get("url") or "").strip()


def _follow_workspace_select_continue(
    session: requests.Session,
    follow_url: str,
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    source: str,
    *,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    if not follow_url:
        return None
    try:
        current_follow = follow_url
        seen_follow_urls: set[str] = set()
        for _ in range(6):
            if current_follow in seen_follow_urls:
                break
            seen_follow_urls.add(current_follow)
            follow_resp = session.get(
                current_follow,
                headers=har_like_browser_fetch_headers(consent_url, accept="application/json, text/html, */*", content_type="", deps=deps),
                verify=False,
                timeout=30,
                allow_redirects=False,
            )
            append_consent_debug(debug_steps, **consent_response_summary(follow_resp, method="get", target=current_follow, source=f"{source}_continue", deps=deps))
            callback_params = deps.callback_params_from_response(follow_resp)
            if callback_params:
                return callback_params
            follow_body = deps.response_json(follow_resp)
            if isinstance(follow_body, dict):
                json_follow = str(follow_body.get("continue_url") or follow_body.get("redirect_url") or follow_body.get("url") or "").strip()
                if json_follow and json_follow not in seen_follow_urls:
                    current_follow = urljoin(current_follow, json_follow)
                    continue
            location = str(getattr(follow_resp, "headers", {}).get("Location") or "").strip()
            if int(getattr(follow_resp, "status_code", 0) or 0) not in (301, 302, 303, 307, 308) or not location:
                break
            current_follow = urljoin(current_follow, location)
    except Exception as exc:
        append_consent_debug(debug_steps, method="get", source=f"{source}_continue", target_prefix=follow_url[:180], error=str(exc))
    return None


def submit_workspace_select_from_consent_form(
    session: requests.Session,
    workspace_id: str,
    headers: dict[str, str],
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    source: str,
    *,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    payload = _workspace_select_payload(workspace_id)
    if not payload:
        return None
    for header_profile, ws_headers in _workspace_select_header_profiles(headers, consent_url, deps=deps):
        ws_resp = _post_workspace_select(
            session,
            payload,
            ws_headers,
            source=source,
            header_profile=header_profile,
            debug_steps=debug_steps,
            deps=deps,
        )
        callback_params = deps.callback_params_from_response(ws_resp)
        if callback_params:
            return callback_params
        ws_body = deps.response_json(ws_resp)
        callback_params = _submit_workspace_select_org_fallback(session, ws_body, ws_headers, consent_url, debug_steps, source, deps=deps)
        if callback_params:
            return callback_params
        callback_params = _follow_workspace_select_continue(session, _workspace_select_follow_url(ws_body), consent_url, debug_steps, source, deps=deps)
        if callback_params:
            return callback_params
        if 200 <= int(getattr(ws_resp, "status_code", 0) or 0) < 300:
            return None
    return None


def har_like_browser_fetch_headers(referer: str, *, accept: str = "application/json", content_type: str = "application/json", deps: ConsentFlowDeps) -> dict[str, str]:
    headers = deps.get_common_headers()
    headers["accept"] = accept
    headers["accept-language"] = "zh-CN,zh;q=0.9"
    headers["referer"] = referer if referer.startswith("http") else f"{deps.auth_base}{referer}"
    headers["sec-ch-ua"] = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'
    headers.update(deps.make_trace_headers())
    if content_type:
        headers["content-type"] = content_type
    else:
        headers.pop("content-type", None)
    return headers


def consent_response_summary(response: Any, *, method: str, target: str, source: str = "", deps: ConsentFlowDeps) -> dict[str, Any]:
    body = deps.response_json(response)
    text = str(getattr(response, "text", "") or "")
    content_type = str(getattr(response, "headers", {}).get("Content-Type") or getattr(response, "headers", {}).get("content-type") or "")
    summary = {
        "method": method,
        "source": source,
        "target_prefix": str(target or "")[:180],
        "status": int(getattr(response, "status_code", 0) or 0),
        "content_type_prefix": content_type[:120],
        "location_prefix": str(getattr(response, "headers", {}).get("Location") or "")[:180],
        "final_url_prefix": str(getattr(response, "url", target) or target)[:180],
        "json_keys": sorted(body.keys())[:20] if isinstance(body, dict) else [],
        "text_markers": {
            "has_code": "code=" in text or '"code"' in text,
            "has_continue": "continue" in text.lower(),
            "has_callback": "/auth/callback" in text or "localhost:1455" in text,
            "has_consent": "consent" in text.lower(),
            "has_workspace_id": "workspace_id" in text or "workspaceId" in text,
        },
    }
    workspace_id = deps.find_workspace_id_from_auth_session_node(body) if isinstance(body, dict) else ""
    if not workspace_id:
        workspace_id = deps.workspace_id_from_consent_html(text)
    summary["workspace_id_present"] = bool(workspace_id)
    if workspace_id:
        summary["workspace_id_prefix"] = workspace_id[:16]
    org_id, project_id = deps.find_org_project_from_auth_session_node(body) if isinstance(body, dict) else ("", "")
    if not org_id:
        org_id, project_id = deps.org_project_from_consent_html(text)
    summary["org_id_present"] = bool(org_id)
    summary["project_id_present"] = bool(project_id)
    if org_id:
        summary["org_id_prefix"] = org_id[:16]
    if project_id:
        summary["project_id_prefix"] = project_id[:16]
    detail = ""
    if isinstance(body, dict):
        for key in ("error", "message", "detail", "reason"):
            value = body.get(key)
            if value:
                detail = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                break
    if detail:
        summary["error_prefix"] = detail[:240]
    snippets: dict[str, str] = {}
    for token in ("workspace_id", "workspaceId", "continue", "authorize", "_data", "_routes", "routeId", "SIGN_IN_WITH_CHATGPT_CODEX_CONSENT"):
        index = text.find(token)
        if index < 0:
            continue
        start = max(0, index - 90)
        end = min(len(text), index + 180)
        snippet = re.sub(r"\s+", " ", text[start:end])
        snippets[token] = snippet[:280]
    if snippets:
        summary["snippets"] = snippets
    route_candidates = sorted(dict.fromkeys(re.findall(r"(?:routes/[A-Za-z0-9_./$+-]+|SIGN_IN_WITH_CHATGPT_CODEX_CONSENT)", text)))[:20]
    if route_candidates:
        summary["route_candidates"] = route_candidates
    if source in ("consent_page", "consent_data", "consent_data_submit") or int(getattr(response, "status_code", 0) or 0) >= 400:
        prefix = re.sub(r"\s+", " ", text[:1200]).strip()
        if prefix:
            summary["text_prefix"] = prefix[:1200]
    return summary


def probe_client_auth_session_dump_for_consent(
    session: requests.Session,
    headers: dict[str, str],
    debug_steps: list[dict[str, Any]] | None,
    *,
    source: str,
    org_select_source: str,
    deps: ConsentFlowDeps,
) -> tuple[dict[str, str] | None, str]:
    target = f"{deps.auth_base}/api/accounts/client_auth_session_dump"
    dump_resp = session.get(
        target,
        headers=headers,
        verify=False,
        timeout=30,
        allow_redirects=False,
    )
    append_consent_debug(debug_steps, **consent_response_summary(dump_resp, method="get", target=target, source=source, deps=deps))
    callback_params = deps.callback_params_from_response(dump_resp)
    if callback_params:
        return callback_params, ""
    dump_body = deps.response_json(dump_resp)
    org_id, project_id = deps.find_org_project_from_auth_session_node(dump_body)
    if org_id:
        callback_params = submit_organization_select_for_consent(
            session,
            org_id,
            project_id,
            headers,
            f"{deps.auth_base}/sign-in-with-chatgpt/codex/organization",
            debug_steps,
            org_select_source,
            deps=deps,
        )
        if callback_params:
            return callback_params, ""
    return None, deps.find_workspace_id_from_auth_session_node(dump_body)


def submit_consent_form_and_follow(
    session: requests.Session,
    *,
    form_url: str,
    form_payload: dict[str, str],
    headers: dict[str, str],
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    form_headers = dict(headers)
    form_headers["content-type"] = "application/x-www-form-urlencoded"
    form_headers["referer"] = consent_url
    try:
        form_resp = session.post(
            form_url,
            data=form_payload,
            headers=form_headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        form_summary = consent_response_summary(form_resp, method="post", target=form_url, source="consent_form", deps=deps)
        form_summary["payload_keys"] = sorted(form_payload.keys())[:20]
        append_consent_debug(debug_steps, **form_summary)
        callback_params = deps.callback_params_from_response(form_resp)
        if callback_params:
            return callback_params
        follow = str(getattr(form_resp, "headers", {}).get("Location") or getattr(form_resp, "url", "") or "").strip()
        if follow:
            follow_url = urljoin(form_url, follow)
            follow_resp = session.get(
                follow_url,
                headers={**headers, "referer": form_url},
                verify=False,
                timeout=30,
                allow_redirects=True,
            )
            append_consent_debug(debug_steps, **consent_response_summary(follow_resp, method="get", target=follow_url, source="consent_form_follow", deps=deps))
            return deps.callback_params_from_response(follow_resp)
    except Exception as exc:
        append_consent_debug(debug_steps, method="post", source="consent_form", target_prefix=form_url[:180], error=str(exc))
    return None


def submit_consent_data_payloads_for_callback(
    session: requests.Session,
    *,
    workspace_id: str,
    state: str,
    headers: dict[str, str],
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> ConsentDataSubmitResult:
    submit_urls = [
        merge_url_query(
            f"{deps.auth_base}/sign-in-with-chatgpt/codex/consent.data",
            _routes="SIGN_IN_WITH_CHATGPT_CODEX_CONSENT",
        ),
        f"{deps.auth_base}/sign-in-with-chatgpt/codex/consent",
    ]
    for payload in consent_data_payloads(workspace_id, state):
        for submit_url in submit_urls:
            form_headers = dict(headers)
            form_headers["accept"] = "application/json, text/x-script, */*"
            form_headers["content-type"] = "application/x-www-form-urlencoded;charset=UTF-8"
            form_headers["referer"] = consent_url
            try:
                submit_resp = session.post(
                    submit_url,
                    data=payload,
                    headers=form_headers,
                    verify=False,
                    timeout=30,
                    allow_redirects=False,
                )
                submit_summary = consent_response_summary(submit_resp, method="post", target=submit_url, source="consent_data_submit", deps=deps)
                submit_summary["payload_keys"] = sorted(payload.keys())[:20]
                append_consent_debug(debug_steps, **submit_summary)
                callback_params = deps.callback_params_from_response(submit_resp)
                if callback_params:
                    return ConsentDataSubmitResult(callback_params=callback_params)
                if int(getattr(submit_resp, "status_code", 0) or 0) in (301, 302, 303, 307, 308):
                    follow = str(getattr(submit_resp, "headers", {}).get("Location") or "").strip()
                    if follow:
                        follow_url = urljoin(submit_url, follow)
                        follow_resp = session.get(
                            follow_url,
                            headers={**headers, "referer": submit_url},
                            verify=False,
                            timeout=30,
                            allow_redirects=True,
                        )
                        append_consent_debug(debug_steps, **consent_response_summary(follow_resp, method="get", target=follow_url, source="consent_data_submit_follow", deps=deps))
                        callback_params = deps.callback_params_from_response(follow_resp)
                        if callback_params:
                            return ConsentDataSubmitResult(callback_params=callback_params)
                submit_body = deps.response_json(submit_resp)
                submit_text = str(getattr(submit_resp, "text", "") or "")
                if "invalid_state" in json.dumps(submit_body, ensure_ascii=False) or "invalid_state" in submit_text:
                    return ConsentDataSubmitResult(invalid_state=True)
            except Exception as exc:
                append_consent_debug(debug_steps, method="post", source="consent_data_submit", target_prefix=submit_url[:180], payload_keys=sorted(payload.keys())[:20], error=str(exc))
    return ConsentDataSubmitResult()


def submit_final_workspace_and_organization_for_consent(
    session: requests.Session,
    *,
    workspace_id: str,
    headers: dict[str, str],
    consent_url: str,
    device_id: str,
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    ws_payload = {"workspace_id": workspace_id}
    ws_resp = session.post(f"{deps.auth_base}/api/accounts/workspace/select", json=ws_payload, headers=headers, verify=False, timeout=30, allow_redirects=False)
    append_consent_debug(debug_steps, **consent_response_summary(ws_resp, method="post", target=f"{deps.auth_base}/api/accounts/workspace/select", source="workspace_select", deps=deps))
    callback_params = deps.callback_params_from_response(ws_resp)
    if callback_params:
        return callback_params
    ws_data = deps.response_json(ws_resp)
    orgs = ((ws_data.get("data") or {}).get("orgs") or []) if isinstance(ws_data, dict) else []
    if not orgs:
        return None
    org_id = str((orgs[0] or {}).get("id") or "").strip()
    project_id = str(((orgs[0] or {}).get("projects") or [{}])[0].get("id") or "").strip()
    if not org_id:
        return None
    org_headers = deps.get_common_headers()
    org_headers["referer"] = str(ws_data.get("continue_url") or consent_url)
    org_headers["oai-device-id"] = device_id
    org_headers.update(deps.make_trace_headers())
    body = {"org_id": org_id}
    if project_id:
        body["project_id"] = project_id
    org_resp = session.post(f"{deps.auth_base}/api/accounts/organization/select", json=body, headers=org_headers, verify=False, timeout=30, allow_redirects=False)
    append_consent_debug(debug_steps, **consent_response_summary(org_resp, method="post", target=f"{deps.auth_base}/api/accounts/organization/select", source="organization_select", deps=deps))
    return deps.callback_params_from_response(org_resp)


def probe_har_like_consent_state(
    session: requests.Session,
    *,
    har_like_headers: dict[str, str],
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> tuple[dict[str, str] | None, str]:
    workspace_id = ""
    try:
        callback_params, workspace_id = probe_client_auth_session_dump_for_consent(
            session,
            {**har_like_headers, "accept": "application/json"},
            debug_steps,
            source="client_auth_session_dump_har_like",
            org_select_source="organization_select_from_session_dump_har_like",
            deps=deps,
        )
        if callback_params:
            return callback_params, workspace_id
    except Exception:
        workspace_id = ""

    try:
        data_url = merge_url_query(
            f"{deps.auth_base}/sign-in-with-chatgpt/codex/consent.data",
            _routes="SIGN_IN_WITH_CHATGPT_CODEX_CONSENT",
        )
        data_resp = session.get(
            data_url,
            headers=har_like_browser_fetch_headers(f"{deps.auth_base}/email-verification", accept="*/*", content_type="", deps=deps),
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        append_consent_debug(debug_steps, **consent_response_summary(data_resp, method="get", target=data_url, source="consent_data_har_like", deps=deps))
        callback_params = deps.callback_params_from_response(data_resp)
        if callback_params:
            return callback_params, workspace_id
        data_body = deps.response_json(data_resp)
        if not workspace_id:
            workspace_id = deps.find_workspace_id_from_auth_session_node(data_body)
        if not workspace_id:
            workspace_id = deps.workspace_id_from_consent_html(str(getattr(data_resp, "text", "") or ""))
        org_id, project_id = deps.find_org_project_from_auth_session_node(data_body)
        if not org_id:
            org_id, project_id = deps.org_project_from_consent_html(str(getattr(data_resp, "text", "") or ""))
        if org_id:
            callback_params = submit_organization_select_for_consent(
                session,
                org_id,
                project_id,
                har_like_headers,
                f"{deps.auth_base}/sign-in-with-chatgpt/codex/organization",
                debug_steps,
                "organization_select_from_consent_data_har_like",
                deps=deps,
            )
            if callback_params:
                return callback_params, workspace_id
    except Exception:
        pass
    return None, workspace_id


def submit_har_like_workspace_select_for_callback(
    session: requests.Session,
    *,
    workspace_id: str,
    har_like_headers: dict[str, str],
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    if not workspace_id:
        return None
    try:
        return submit_workspace_select_from_consent_form(
            session,
            workspace_id,
            har_like_headers,
            consent_url,
            debug_steps,
            "workspace_select_har_like",
            deps=deps,
        )
    except Exception as exc:
        append_consent_debug(debug_steps, method="post", source="workspace_select_har_like", target_prefix=f"{deps.auth_base}/api/accounts/workspace/select", payload_keys=["workspace_id"], error=str(exc))
    return None


def fetch_consent_page_for_callback(
    session: requests.Session,
    *,
    consent_url: str,
    har_like_headers: dict[str, str],
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> tuple[dict[str, str] | None, str]:
    current_url = consent_url
    consent_page_text = ""
    for _ in range(10):
        response = session.get(current_url, headers=deps.get_navigate_headers(), verify=False, timeout=30, allow_redirects=False)
        append_consent_debug(debug_steps, **consent_response_summary(response, method="get", target=current_url, source="consent_page", deps=deps))
        if not consent_page_text:
            consent_page_text = str(getattr(response, "text", "") or "")
        callback_params = deps.callback_params_from_response(response)
        if callback_params:
            return callback_params, consent_page_text
        page_org_id, page_project_id = deps.org_project_from_consent_html(str(getattr(response, "text", "") or ""))
        if page_org_id:
            callback_params = submit_organization_select_for_consent(
                session,
                page_org_id,
                page_project_id,
                har_like_headers,
                f"{deps.auth_base}/sign-in-with-chatgpt/codex/organization",
                debug_steps,
                "organization_select_from_consent_page_html",
                deps=deps,
            )
            if callback_params:
                return callback_params, consent_page_text
        location = str(response.headers.get("Location") or "").strip()
        if response.status_code not in (301, 302, 303, 307, 308) or not location:
            break
        current_url = f"{deps.auth_base}{location}" if location.startswith("/") else location
    return None, consent_page_text


def submit_consent_page_actions_for_callback(
    session: requests.Session,
    *,
    consent_page_text: str,
    consent_url: str,
    headers: dict[str, str],
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    action, form_payload = deps.extract_consent_form_inputs(consent_page_text)
    form_workspace_id = str((form_payload or {}).get("workspace_id") or (form_payload or {}).get("workspaceId") or "").strip()
    form_action_url = urljoin(consent_url, action or consent_url)
    page_workspace_id = deps.workspace_id_from_consent_html(consent_page_text)
    if page_workspace_id and page_workspace_id != form_workspace_id:
        try:
            callback_params = submit_workspace_select_from_consent_form(
                session,
                page_workspace_id,
                headers,
                consent_url,
                debug_steps,
                "workspace_select_from_consent_page",
                deps=deps,
            )
            if callback_params:
                return callback_params
        except Exception as exc:
            append_consent_debug(debug_steps, method="post", source="workspace_select_from_consent_page", target_prefix=f"{deps.auth_base}/api/accounts/workspace/select", payload_keys=["workspace_id"], error=str(exc))

    if form_workspace_id and (not action or "/sign-in-with-chatgpt/codex/consent" in form_action_url):
        try:
            callback_params = submit_workspace_select_from_consent_form(
                session,
                form_workspace_id,
                headers,
                consent_url,
                debug_steps,
                "workspace_select_from_consent_form",
                deps=deps,
            )
            if callback_params:
                return callback_params
        except Exception as exc:
            append_consent_debug(debug_steps, method="post", source="workspace_select_from_consent_form", target_prefix=f"{deps.auth_base}/api/accounts/workspace/select", payload_keys=["workspace_id"], error=str(exc))

    if not action:
        return None
    return submit_consent_form_and_follow(
        session,
        form_url=form_action_url,
        form_payload=form_payload,
        headers=headers,
        consent_url=consent_url,
        debug_steps=debug_steps,
        deps=deps,
    )


def resolve_workspace_after_consent_page(
    session: requests.Session,
    *,
    consent_page_text: str,
    headers: dict[str, str],
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> tuple[dict[str, str] | None, str]:
    workspace_id = deps.workspace_id_from_consent_html(consent_page_text)
    try:
        callback_params, dump_workspace_id = probe_client_auth_session_dump_for_consent(
            session,
            {**headers, "accept": "application/json", "content-type": "application/json"},
            debug_steps,
            source="client_auth_session_dump",
            org_select_source="organization_select_from_session_dump",
            deps=deps,
        )
        if callback_params:
            return callback_params, workspace_id
        if not workspace_id:
            workspace_id = dump_workspace_id
    except Exception:
        if not workspace_id:
            workspace_id = ""

    try:
        data_url = merge_url_query(
            f"{deps.auth_base}/sign-in-with-chatgpt/codex/consent.data",
            _routes="SIGN_IN_WITH_CHATGPT_CODEX_CONSENT",
        )
        data_resp = session.get(
            data_url,
            headers={**headers, "accept": "*/*"},
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        append_consent_debug(debug_steps, **consent_response_summary(data_resp, method="get", target=data_url, source="consent_data", deps=deps))
        callback_params = deps.callback_params_from_response(data_resp)
        if callback_params:
            return callback_params, workspace_id
        if not workspace_id:
            workspace_id = deps.find_workspace_id_from_auth_session_node(deps.response_json(data_resp))
        if not workspace_id:
            workspace_id = deps.workspace_id_from_consent_html(str(getattr(data_resp, "text", "") or ""))
    except Exception:
        pass

    if not workspace_id:
        workspace_id = deps.workspace_id_from_client_auth_session_cookie(session)
    return None, workspace_id


def _probe_har_like_and_fetch_consent_page_for_callback(
    session: requests.Session,
    *,
    consent_url: str,
    har_like_headers: dict[str, str],
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> tuple[dict[str, str] | None, str, str]:
    callback_params, workspace_id = probe_har_like_consent_state(
        session,
        har_like_headers=har_like_headers,
        debug_steps=debug_steps,
        deps=deps,
    )
    if callback_params:
        return callback_params, workspace_id, ""

    callback_params = submit_har_like_workspace_select_for_callback(
        session,
        workspace_id=workspace_id,
        har_like_headers=har_like_headers,
        consent_url=consent_url,
        debug_steps=debug_steps,
        deps=deps,
    )
    if callback_params:
        return callback_params, workspace_id, ""

    callback_params, consent_page_text = fetch_consent_page_for_callback(
        session,
        consent_url=consent_url,
        har_like_headers=har_like_headers,
        debug_steps=debug_steps,
        deps=deps,
    )
    return callback_params, workspace_id, consent_page_text


def _submit_consent_page_callback_flow(
    session: requests.Session,
    *,
    consent_page_text: str,
    consent_url: str,
    headers: dict[str, str],
    device_id: str,
    state: str,
    debug_steps: list[dict[str, Any]] | None,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    callback_params = submit_consent_page_actions_for_callback(
        session,
        consent_page_text=consent_page_text,
        consent_url=consent_url,
        headers=headers,
        debug_steps=debug_steps,
        deps=deps,
    )
    if callback_params:
        return callback_params

    callback_params, workspace_id = resolve_workspace_after_consent_page(
        session,
        consent_page_text=consent_page_text,
        headers=headers,
        debug_steps=debug_steps,
        deps=deps,
    )
    if callback_params:
        return callback_params
    if not workspace_id:
        return None

    consent_data_submit = submit_consent_data_payloads_for_callback(
        session,
        workspace_id=workspace_id,
        state=state,
        headers=headers,
        consent_url=consent_url,
        debug_steps=debug_steps,
        deps=deps,
    )
    if consent_data_submit.callback_params:
        return consent_data_submit.callback_params
    if consent_data_submit.invalid_state:
        return None

    return submit_final_workspace_and_organization_for_consent(
        session,
        workspace_id=workspace_id,
        headers=headers,
        consent_url=consent_url,
        device_id=device_id,
        debug_steps=debug_steps,
        deps=deps,
    )


def extract_oauth_callback_params_from_consent_session(
    session: requests.Session,
    consent_url: str,
    device_id: str,
    *,
    state: str = "",
    debug_steps: list[dict[str, Any]] | None = None,
    deps: ConsentFlowDeps,
) -> dict[str, str] | None:
    if consent_url.startswith("/"):
        consent_url = f"{deps.auth_base}{consent_url}"
    har_like_headers = har_like_browser_fetch_headers(f"{deps.auth_base}/email-verification", accept="application/json", content_type="", deps=deps)

    callback_params, _, consent_page_text = _probe_har_like_and_fetch_consent_page_for_callback(
        session,
        consent_url=consent_url,
        har_like_headers=har_like_headers,
        debug_steps=debug_steps,
        deps=deps,
    )
    if callback_params:
        return callback_params

    headers = build_authorize_continue_headers(session, device_id, consent_url, deps=deps)

    return _submit_consent_page_callback_flow(
        session,
        consent_page_text=consent_page_text,
        consent_url=consent_url,
        headers=headers,
        device_id=device_id,
        state=state,
        debug_steps=debug_steps,
        deps=deps,
    )
