from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def about_you_data_urls(raw_url: str) -> list[str]:
    parsed = urlparse(raw_url)
    path = parsed.path or "/about-you"
    if not path.endswith(".data"):
        path = f"{path.rstrip('/')}.data"
    base_query = parse_qs(parsed.query, keep_blank_values=True)
    urls: list[str] = []
    for route_id in ("routes/about-you", "routes/_auth/about-you", "routes/_auth.about-you"):
        query = {key: values[:] for key, values in base_query.items()}
        query["_routes"] = [route_id]
        urls.append(urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, urlencode(query, doseq=True), parsed.fragment)))
    return urls


def _about_you_refresh_urls(target: str, merge_url_query_fn: Callable[..., str]) -> list[str]:
    urls = [target]
    if "/about-you" in target:
        urls.extend(
            [
                merge_url_query_fn(target, _data="routes/about-you"),
                merge_url_query_fn(target, _data="routes/_auth/about-you"),
            ]
        )
    return list(dict.fromkeys([url for url in urls if url]))


def _refresh_about_you_html_candidates(
    registrar: Any,
    target: str,
    page_html: str,
    *,
    merge_url_query_fn: Callable[..., str],
    request_with_retry_fn: Callable[..., tuple[Any, str]],
    navigate_headers_fn: Callable[[], dict[str, str]],
) -> list[str]:
    html_candidates: list[str] = [str(page_html or "")]
    for refresh_url in _about_you_refresh_urls(target, merge_url_query_fn):
        try:
            response, _ = request_with_retry_fn(
                registrar.session,
                "get",
                refresh_url,
                headers=navigate_headers_fn(),
                allow_redirects=True,
                verify=False,
            )
            if response is None:
                continue
            fresh_html = str(getattr(response, "text", "") or "")
            if fresh_html and fresh_html not in html_candidates:
                html_candidates.append(fresh_html)
        except Exception:
            continue
    return html_candidates


def _select_about_you_form_html(html_candidates: list[str]) -> str:
    for candidate_html in html_candidates:
        if re.search(r"<form\b", candidate_html, re.I) or re.search(r"name\s*=\s*[\"'](?:name|birthdate|birthday|age)[\"']", candidate_html, re.I):
            return candidate_html
    return html_candidates[-1] if html_candidates else ""


def _about_you_candidate_urls(target: str, merge_url_query_fn: Callable[..., str]) -> list[str]:
    candidate_urls = [target]
    if "/about-you" in target:
        candidate_urls.extend(
            [
                merge_url_query_fn(target, _data="routes/about-you"),
                merge_url_query_fn(target, _data="routes/_auth/about-you"),
                *about_you_data_urls(target),
            ]
        )
    return list(dict.fromkeys(candidate_urls))


def _about_you_attempt_label(candidate_url: str, payload: dict[str, str]) -> str:
    payload_keys = "+".join(key for key in payload.keys() if key != "name") or "name"
    return f"{urlparse(candidate_url).path or '/'}:{payload_keys}"


def _about_you_post_result(
    registrar: Any,
    *,
    final_url: str,
    body: str,
    candidate_url: str,
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None],
    load_continue_page_fn: Callable[..., dict[str, Any]],
) -> tuple[str, str, str]:
    if final_url and "/about-you" not in final_url:
        return final_url, body, ""
    if callback_params_from_url_fn(final_url):
        return final_url, body, ""
    follow_probe = load_continue_page_fn(registrar, final_url or candidate_url)
    follow_url = str(follow_probe.get("continue_url") or final_url or candidate_url).strip()
    follow_callback = str(follow_probe.get("callback_url") or "").strip()
    follow_page_type = str(follow_probe.get("page_type") or "").strip()
    follow_text = str(follow_probe.get("text") or body or "")
    if follow_callback:
        return follow_callback, follow_text, ""
    if follow_url and "/about-you" not in follow_url:
        return follow_url, follow_text, ""
    if follow_page_type in ("add_email", "consent", "oauth_consent"):
        return follow_url or final_url or candidate_url, follow_text, ""
    return "", "", f"about_you_still_on_page::{follow_url or final_url or candidate_url}"


def _about_you_submission_candidates(
    registrar: Any,
    *,
    target: str,
    page_html: str,
    full_name: str,
    birthdate: str,
    merge_url_query_fn: Callable[..., str],
    request_with_retry_fn: Callable[..., tuple[Any, str]],
    navigate_headers_fn: Callable[[], dict[str, str]],
    extract_form_inputs_fn: Callable[[str], tuple[str, dict[str, str], str, str]],
    about_you_form_payloads_fn: Callable[..., list[dict[str, str]]],
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    html_candidates = _refresh_about_you_html_candidates(
        registrar,
        target,
        page_html,
        merge_url_query_fn=merge_url_query_fn,
        request_with_retry_fn=request_with_retry_fn,
        navigate_headers_fn=navigate_headers_fn,
    )
    selected_html = _select_about_you_form_html(html_candidates)
    action, hidden, _, _ = extract_form_inputs_fn(selected_html)
    candidate_payloads = about_you_form_payloads_fn(
        hidden=hidden,
        full_name=full_name,
        birthdate=birthdate,
        page_context=selected_html,
    )
    return (
        _about_you_candidate_urls(target, merge_url_query_fn),
        ["", action or "/about-you"],
        candidate_payloads,
    )


def _about_you_attempt_error(last_error: str, attempts: list[str]) -> str:
    if attempts:
        return f"{last_error}; attempts={', '.join(attempts[-8:])}"
    return last_error or "about_you_form_submit_failed"


def _submit_about_you_candidates(
    registrar: Any,
    *,
    candidate_urls: list[str],
    candidate_actions: list[str],
    candidate_payloads: list[dict[str, str]],
    post_form_and_follow_fn: Callable[..., tuple[str, str]],
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None],
    load_continue_page_fn: Callable[..., dict[str, Any]],
) -> tuple[str, str, str]:
    last_error = "about_you_form_submit_failed"
    attempts: list[str] = []
    for candidate_url in candidate_urls:
        for candidate_action in candidate_actions:
            for payload in candidate_payloads:
                try:
                    final_url, body = post_form_and_follow_fn(
                        registrar,
                        page_url=candidate_url,
                        action=candidate_action,
                        payload=payload,
                    )
                    attempts.append(_about_you_attempt_label(candidate_url, payload))
                    resolved_url, resolved_body, next_error = _about_you_post_result(
                        registrar,
                        final_url=final_url,
                        body=body,
                        candidate_url=candidate_url,
                        callback_params_from_url_fn=callback_params_from_url_fn,
                        load_continue_page_fn=load_continue_page_fn,
                    )
                    if resolved_url:
                        return resolved_url, resolved_body, ""
                    last_error = next_error or last_error
                except Exception as exc:
                    last_error = str(exc or "about_you_form_submit_failed")
                    continue
    return "", "", _about_you_attempt_error(last_error, attempts)


def submit_about_you_form(
    registrar: Any,
    *,
    page_url: str,
    page_html: str,
    full_name: str,
    birthdate: str,
    merge_url_query_fn: Callable[..., str],
    request_with_retry_fn: Callable[..., tuple[Any, str]],
    navigate_headers_fn: Callable[[], dict[str, str]],
    extract_form_inputs_fn: Callable[[str], tuple[str, dict[str, str], str, str]],
    about_you_form_payloads_fn: Callable[..., list[dict[str, str]]],
    post_form_and_follow_fn: Callable[..., tuple[str, str]],
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None],
    load_continue_page_fn: Callable[..., dict[str, Any]],
) -> tuple[str, str]:
    target = str(page_url or "").strip()
    if not target:
        raise RuntimeError("about_you_page_url_missing")

    candidate_urls, candidate_actions, candidate_payloads = _about_you_submission_candidates(
        registrar,
        target=target,
        page_html=page_html,
        full_name=full_name,
        birthdate=birthdate,
        merge_url_query_fn=merge_url_query_fn,
        request_with_retry_fn=request_with_retry_fn,
        navigate_headers_fn=navigate_headers_fn,
        extract_form_inputs_fn=extract_form_inputs_fn,
        about_you_form_payloads_fn=about_you_form_payloads_fn,
    )
    resolved_url, resolved_body, error = _submit_about_you_candidates(
        registrar,
        candidate_urls=candidate_urls,
        candidate_actions=candidate_actions,
        candidate_payloads=candidate_payloads,
        post_form_and_follow_fn=post_form_and_follow_fn,
        callback_params_from_url_fn=callback_params_from_url_fn,
        load_continue_page_fn=load_continue_page_fn,
    )
    if resolved_url:
        return resolved_url, resolved_body
    raise RuntimeError(error)
