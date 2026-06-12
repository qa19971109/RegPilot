from __future__ import annotations

from typing import Any


def load_registration_state(
    registrar: Any,
    url: str,
    *,
    load_continue_page_fn: Any,
    state_from_info_fn: Any,
) -> dict[str, Any]:
    target = str(url or "").strip()
    if not target:
        return {"kind": "unknown", "url": "", "page_type": "", "raw": {}}
    try:
        probe = load_continue_page_fn(registrar, target)
        if probe.get("callback_url"):
            return {
                "kind": "callback",
                "url": str(probe.get("callback_url") or ""),
                "page_type": str(probe.get("page_type") or ""),
                "raw": probe,
            }
        info = {
            "json": probe.get("json") or {},
            "text": probe.get("text") or "",
            "final_url": probe.get("continue_url") or target,
            "location": probe.get("location") or "",
        }
        state = state_from_info_fn(info)
        state["raw"] = probe
        return state
    except Exception as exc:
        return {"kind": "continue", "url": target, "page_type": "", "raw": {"error": str(exc)}}


def resolve_registration_post_create_url(
    registrar: Any,
    *,
    start_info: dict[str, Any],
    create_info: dict[str, Any],
    fallback_url: str = "",
    continue_url_fn: Any,
    callback_params_from_url_fn: Any,
    expected_state_fn: Any,
    resolve_oauth_callback_fn: Any,
    log_fn: Any,
) -> str:
    candidate = continue_url_fn(create_info) or str(fallback_url or "").strip()
    if callback_params_from_url_fn(candidate):
        return candidate
    state = expected_state_fn(registrar, start_info, create_info)
    try:
        resolved = resolve_oauth_callback_fn(registrar, candidate, state)
        if resolved:
            return resolved
    except Exception as exc:
        log_fn(f"注册后 OAuth 回调预解析异常：{exc}")
    return candidate


def wait_email_otp_with_resend(
    config: Any,
    registrar: Any,
    mailbox: dict,
    *,
    resend_on_miss: bool = True,
    wait_for_code_fn: Any,
    now_ms_fn: Any,
    response_error_summary_fn: Any,
    log_fn: Any,
) -> str:
    code = wait_for_code_fn(config, mailbox)
    if code or not resend_on_miss:
        return str(code or "").strip()
    mailbox["_code_after_ts"] = int(now_ms_fn())
    otp_info = registrar.send_otp()
    log_fn(f"邮箱验证码重发结果：status={otp_info.get('status')} ok={otp_info.get('ok')} final_url={otp_info.get('final_url')}")
    if not otp_info.get("ok"):
        error = response_error_summary_fn("send_otp", otp_info)
        log_fn(f"邮箱验证码重发失败：{error}")
        return ""
    return str(wait_for_code_fn(config, mailbox) or "").strip()


def follow_chatgpt_signup_callback(
    registrar: Any,
    callback_url: str,
    *,
    chatgpt_signup_redirect_uri_value: str,
    auth_base_value: str,
    request_with_retry_fn: Any,
    navigate_headers_fn: Any,
) -> dict[str, Any]:
    target = str(callback_url or "").strip()
    if not target.startswith(chatgpt_signup_redirect_uri_value):
        return {"followed": False, "final_url": target, "status": 0}
    session = getattr(registrar, "session", None)
    if session is None:
        return {"followed": False, "final_url": target, "status": 0, "error": "session_missing"}
    headers = navigate_headers_fn()
    headers["referer"] = f"{auth_base_value}/about-you"
    headers["sec-fetch-site"] = "cross-site"
    try:
        response, error = request_with_retry_fn(
            session,
            "get",
            target,
            headers=headers,
            allow_redirects=True,
            verify=False,
        )
    except Exception as exc:
        return {"followed": True, "final_url": target, "status": 0, "error": str(exc)}
    if response is None:
        return {"followed": True, "final_url": target, "status": 0, "error": error}
    return {
        "followed": True,
        "final_url": str(getattr(response, "url", "") or target),
        "status": int(getattr(response, "status_code", 0) or 0),
    }
