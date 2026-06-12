from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class ReauthorizeLocalTokenFlowDeps:
    attempt_password_login_fn: Callable[..., dict[str, Any]]
    build_openai_oauth_authorize_url_fn: Callable[[Any], Any]
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    common_headers_fn: Callable[[], dict[str, str]]
    compact_consent_debug_summary_fn: Callable[[Any], Any]
    direct_exchange_local_callback_fn: Callable[..., Any]
    exchange_callback_code_fn: Callable[..., Any]
    first_step_continue_url_fn: Callable[[dict[str, Any]], str]
    handle_email_otp_step_fn: Callable[..., tuple[str, dict[str, Any], dict[str, Any]]]
    log_stage_fn: Callable[[str], None]
    oauth_flow_config_cls: Callable[..., Any]
    ready_text_fn: Callable[[Any], str]
    registration_result_cls: Callable[..., Any]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, Any]]
    resolve_callback_step_fn: Callable[..., str]
    resolve_consent_callback_direct_fn: Callable[[Any, str, str], tuple[str, dict[str, Any]]]
    resolve_oauth_callback_fn: Callable[..., str]
    response_brief_fn: Callable[[dict[str, Any]], str]
    response_json_fn: Callable[[Any], dict[str, Any]]
    short_url_fn: Callable[[Any], str]
    zh_bool_fn: Callable[[Any], str]


@dataclass(frozen=True)
class LocalTokenAuthorizeSession:
    local_config: Any
    prepared: Any
    state: str
    final_url: str
    authorize_info: dict[str, Any]
    callback: str
    local_state: dict[str, Any]


@dataclass(frozen=True)
class LocalTokenCallbackState:
    callback: str
    authorize_info: dict[str, Any]


def _open_local_token_authorize(
    registrar: Any,
    *,
    email: str,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> LocalTokenAuthorizeSession:
    _log_stage = deps.log_stage_fn
    _registration_state_from_info = deps.registration_state_from_info_fn
    _response_brief = deps.response_brief_fn
    _response_json = deps.response_json_fn
    _short_url = deps.short_url_fn
    Sub2APIOAuthFlowConfig = deps.oauth_flow_config_cls
    build_openai_oauth_authorize_url = deps.build_openai_oauth_authorize_url_fn
    extract_oauth_callback_params_from_url = deps.callback_params_from_url_fn
    get_common_headers = deps.common_headers_fn

    _log_stage("CPA 回调提交后开始附加获取本地 token")
    local_config = Sub2APIOAuthFlowConfig(proxy=str(registrar.proxy or "").strip(), login_hint=str(email or "").strip())
    prepared = build_openai_oauth_authorize_url(local_config)
    session_device_id = str(registrar.device_id or prepared.device_id).strip()
    prepared.device_id = session_device_id
    parsed_authorize = urlparse(prepared.authorize_url)
    authorize_params = parse_qs(parsed_authorize.query, keep_blank_values=True)
    authorize_params["device_id"] = [session_device_id]
    authorize_params["login_hint"] = [str(email or "").strip()]
    authorize_params.pop("max_age", None)
    authorize_params.pop("prompt", None)
    prepared.authorize_url = urlunparse(parsed_authorize._replace(query=urlencode(authorize_params, doseq=True)))
    registrar.session.cookies.set("oai-did", session_device_id, domain=".auth.openai.com")
    registrar.session.cookies.set("oai-did", session_device_id, domain="auth.openai.com")
    response = registrar.session.get(prepared.authorize_url, headers=get_common_headers(), verify=False, timeout=30, allow_redirects=True)
    final_url = str(getattr(response, "url", prepared.authorize_url) or prepared.authorize_url)
    authorize_info = {"ok": 200 <= int(getattr(response, "status_code", 0) or 0) < 400, "status": int(getattr(response, "status_code", 0) or 0), "json": _response_json(response), "text": str(getattr(response, "text", "") or "")[:2000], "location": str(getattr(response, "headers", {}).get("Location") or ""), "final_url": final_url}
    registrar.last_authorize = {
        "email": email,
        "state": prepared.state,
        "nonce": prepared.nonce,
        "code_verifier": prepared.code_verifier,
        "code_challenge": prepared.code_challenge,
        "external_authorize": False,
        "screen_hint": "login",
        "flow_kind": "login",
        "final_url": final_url,
        "status": str(getattr(response, "status_code", "") or ""),
    }
    _log_stage(f"本地 token 授权入口已打开：状态码={getattr(response, 'status_code', '') or '-'}，最终地址={_short_url(final_url)}")
    _log_stage(f"本地 token 授权入口摘要：{_response_brief(authorize_info)}")
    callback = final_url if extract_oauth_callback_params_from_url(final_url) else ""
    local_state = _registration_state_from_info(authorize_info)
    return LocalTokenAuthorizeSession(
        local_config=local_config,
        prepared=prepared,
        state=prepared.state,
        final_url=final_url,
        authorize_info=authorize_info,
        callback=callback,
        local_state=local_state,
    )


def _local_token_callback_state(callback: str, authorize_info: dict[str, Any]) -> LocalTokenCallbackState:
    result = str(callback or '')
    return LocalTokenCallbackState(callback=result, authorize_info=authorize_info)


def _resolve_local_token_email_otp_callback(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    state: str,
    authorize_info: dict[str, Any],
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> LocalTokenCallbackState:
    _handle_email_otp_step = deps.handle_email_otp_step_fn
    _log_stage = deps.log_stage_fn
    _ready_text = deps.ready_text_fn

    try:
        callback, validate_info, _ = _handle_email_otp_step(
            registrar,
            account,
            mailbox,
            state,
            proxy=str(registrar.proxy or "").strip(),
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
        _log_stage(f"本地 token 邮箱验证码后回调解析结果：回调{_ready_text(callback)}")
        if not callback:
            return _local_token_callback_state(callback, validate_info)
        return _local_token_callback_state(callback, authorize_info)
    except Exception as exc:
        _log_stage(f"本地 token 邮箱验证码流程失败：{exc}")
    return _local_token_callback_state("", authorize_info)


def _resolve_local_token_password_consent_callback(
    registrar: Any,
    *,
    state: str,
    authorize_info: dict[str, Any],
    password_info: dict[str, Any],
    deps: ReauthorizeLocalTokenFlowDeps,
) -> LocalTokenCallbackState:
    _compact_consent_debug_summary = deps.compact_consent_debug_summary_fn
    _first_step_continue_url = deps.first_step_continue_url_fn
    _log_stage = deps.log_stage_fn
    _ready_text = deps.ready_text_fn
    _resolve_consent_callback_direct = deps.resolve_consent_callback_direct_fn

    consent_url = _first_step_continue_url(password_info)
    if not consent_url:
        return _local_token_callback_state("", authorize_info)
    callback, consent_summary = _resolve_consent_callback_direct(registrar, consent_url, state)
    attempts = len((consent_summary or {}).get("attempts") or [])
    _log_stage(f"本地 token 密码登录后授权确认处理结果：回调{_ready_text(callback)}，尝试次数={attempts}")
    if not callback:
        _log_stage(f"本地 token 授权确认调试摘要：{_compact_consent_debug_summary(consent_summary)}")
    return _local_token_callback_state(callback, authorize_info)


def _resolve_local_token_password_callback(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    state: str,
    authorize_info: dict[str, Any],
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> LocalTokenCallbackState:
    _attempt_password_login = deps.attempt_password_login_fn
    _log_stage = deps.log_stage_fn
    _resolve_callback_step = deps.resolve_callback_step_fn
    _response_brief = deps.response_brief_fn

    _log_stage("本地 token 授权入口要求登录，先提交密码")
    password_info = _attempt_password_login(registrar, email, password)
    _log_stage(f"本地 token 密码登录结果：{_response_brief(password_info)}")
    callback = _resolve_callback_step(registrar, password_info, state, allow_state_resume=False)
    if callback:
        return _local_token_callback_state(callback, authorize_info)
    if not password_info.get("ok"):
        _log_stage("本地 token 密码登录失败，切换邮箱验证码")
        return _resolve_local_token_email_otp_callback(
            registrar,
            account,
            mailbox,
            state=state,
            authorize_info=authorize_info,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            deps=deps,
        )
    return _resolve_local_token_password_consent_callback(
        registrar,
        state=state,
        authorize_info=authorize_info,
        password_info=password_info,
        deps=deps,
    )


def _resolve_local_token_oauth_fallback(
    registrar: Any,
    *,
    state: str,
    authorize_info: dict[str, Any],
    final_url: str,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> LocalTokenCallbackState:
    _first_step_continue_url = deps.first_step_continue_url_fn
    _log_stage = deps.log_stage_fn
    _ready_text = deps.ready_text_fn
    _resolve_oauth_callback = deps.resolve_oauth_callback_fn

    _log_stage("快速解析本地 token OAuth 回调")
    callback_target = _first_step_continue_url(authorize_info) or final_url
    callback = _resolve_oauth_callback(registrar, callback_target, state, max_steps=3, request_timeout=5, include_codex_consent=False)
    _log_stage(f"本地 token OAuth 回调解析结果：回调{_ready_text(callback)}")
    return _local_token_callback_state(callback, authorize_info)


def _resolve_local_token_consent_fallback(
    registrar: Any,
    *,
    state: str,
    authorize_info: dict[str, Any],
    final_url: str,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> LocalTokenCallbackState:
    _compact_consent_debug_summary = deps.compact_consent_debug_summary_fn
    _first_step_continue_url = deps.first_step_continue_url_fn
    _log_stage = deps.log_stage_fn
    _ready_text = deps.ready_text_fn
    _resolve_consent_callback_direct = deps.resolve_consent_callback_direct_fn

    _log_stage("处理 CPA 后的本地 token 授权确认页")
    consent_target = _first_step_continue_url(authorize_info) or final_url
    callback, consent_summary = _resolve_consent_callback_direct(registrar, consent_target, state)
    attempts = len((consent_summary or {}).get("attempts") or [])
    _log_stage(f"本地 token 授权确认处理结果：回调{_ready_text(callback)}，尝试次数={attempts}")
    if not callback:
        _log_stage(f"本地 token 授权确认调试摘要：{_compact_consent_debug_summary(consent_summary)}")
    return _local_token_callback_state(callback, authorize_info)


def _resolve_local_token_callback(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    authorize_session: LocalTokenAuthorizeSession,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> tuple[str, dict[str, Any]]:
    _log_stage = deps.log_stage_fn

    state = authorize_session.state
    final_url = authorize_session.final_url
    flow_state = _local_token_callback_state(authorize_session.callback, authorize_session.authorize_info)
    local_state = authorize_session.local_state

    if not flow_state.callback and str(local_state.get("kind") or "") == "password":
        flow_state = _resolve_local_token_password_callback(
            registrar,
            account,
            mailbox,
            email=email,
            password=password,
            state=state,
            authorize_info=flow_state.authorize_info,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            deps=deps,
        )
    elif not flow_state.callback and str(local_state.get("kind") or "") == "email_otp":
        _log_stage("本地 token 授权入口直接进入邮箱验证码")
        flow_state = _resolve_local_token_email_otp_callback(
            registrar,
            account,
            mailbox,
            state=state,
            authorize_info=flow_state.authorize_info,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            deps=deps,
        )
    if not flow_state.callback:
        flow_state = _resolve_local_token_oauth_fallback(
            registrar,
            state=state,
            authorize_info=flow_state.authorize_info,
            final_url=final_url,
            deps=deps,
        )
    if not flow_state.callback:
        flow_state = _resolve_local_token_consent_fallback(
            registrar,
            state=state,
            authorize_info=flow_state.authorize_info,
            final_url=final_url,
            deps=deps,
        )
    return flow_state.callback, flow_state.authorize_info


def exchange_local_tokens_after_cpa(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
    deps: ReauthorizeLocalTokenFlowDeps,
) -> Any:
    _direct_exchange_local_callback = deps.direct_exchange_local_callback_fn
    _log_stage = deps.log_stage_fn
    _zh_bool = deps.zh_bool_fn
    RegistrationResult = deps.registration_result_cls
    exchange_callback_code = deps.exchange_callback_code_fn

    authorize_session = _open_local_token_authorize(registrar, email=email, deps=deps)
    local_config = authorize_session.local_config
    prepared = authorize_session.prepared
    final_url = authorize_session.final_url
    callback, _ = _resolve_local_token_callback(
        registrar,
        account,
        mailbox,
        email=email,
        password=password,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        authorize_session=authorize_session,
        deps=deps,
    )
    if not callback:
        _log_stage("CPA 后未拿到本地 token 回调")
        return RegistrationResult(ok=False, email=email, password=password, mailbox=mailbox, callback_url=final_url, error="local_callback_not_reached_after_cpa")
    result = _direct_exchange_local_callback(local_config, prepared, callback, email=email, password=password, mailbox=mailbox)
    if not result.ok:
        _log_stage(f"本地 token 直接换取失败：{result.error or '-'}")
        try:
            result = exchange_callback_code(local_config, prepared, callback)
        except Exception as exc:
            _log_stage(f"本地 token 主流程换取失败：{exc}")
    if not result.ok:
        _log_stage(f"主流程失败后再次尝试直接换取本地 token：{result.error or '-'}")
        direct_result = _direct_exchange_local_callback(local_config, prepared, callback, email=email, password=password, mailbox=mailbox)
        if direct_result.ok or not result.error:
            result = direct_result
    _log_stage(f"本地 token 换取结果：成功={_zh_bool(result.ok)}，错误={result.error or '-'}")
    result.email = result.email or email
    result.password = password
    result.mailbox = mailbox
    return result
