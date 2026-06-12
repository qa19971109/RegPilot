from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import reauthorize_auto_helpers


@dataclass(frozen=True)
class ReauthorizeAutoFlowDeps:
    auth_base: str
    auto_outcome_cls: Callable[..., Any]
    attempt_password_login_fn: Callable[..., dict[str, Any]]
    bind_email_hint_for_account_fn: Callable[[dict[str, Any], dict[str, Any], str], str]
    bind_mail_config_for_account_fn: Callable[..., dict[str, Any]]
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    continue_with_optional_about_you_fn: Callable[..., tuple[str, dict[str, Any]]]
    continue_with_optional_add_email_fn: Callable[..., tuple[str, str]]
    fail_manual_phone_verification_required_fn: Callable[..., Any]
    finalize_cpa_submit_with_optional_local_tokens_fn: Callable[..., Any]
    first_step_continue_url_fn: Callable[[dict[str, Any]], str]
    get_account_fn: Callable[[str], dict[str, Any] | None]
    handle_email_otp_step_fn: Callable[..., tuple[str, dict[str, Any], dict[str, Any]]]
    is_consent_like_url_fn: Callable[[Any], bool]
    log_phone_required_after_email_otp_fn: Callable[[dict[str, Any], str], None]
    log_stage_fn: Callable[[str], None]
    login_identifier_for_account_fn: Callable[[dict[str, Any], dict[str, Any], str], str]
    login_username_payload_fn: Callable[[str], dict[str, Any]]
    mail_wait_config_for_account_fn: Callable[..., Any]
    mailbox_for_mail_wait_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    mark_reauthorize_failed_fn: Callable[..., dict[str, Any]]
    phone_verification_page_brief_fn: Callable[[dict[str, Any]], str]
    platform_registrar_cls: Callable[[str], Any]
    prepare_bind_mailbox_fn: Callable[[dict[str, Any], str], tuple[str, dict[str, Any] | None]]
    proxy_text_fn: Callable[[Any], str]
    ready_text_fn: Callable[[Any], str]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, str]]
    resolve_callback_step_fn: Callable[..., str]
    resolve_consent_callback_direct_fn: Callable[[Any, str, str], tuple[str, dict[str, Any]]]
    response_brief_fn: Callable[[dict[str, Any]], str]
    response_json_fn: Callable[[Any], dict[str, Any]]
    safe_response_summary_fn: Callable[[dict[str, Any]], dict[str, Any]]
    send_login_otp_fn: Callable[[Any, str], dict[str, Any]]
    short_url_fn: Callable[..., str]
    start_cpa_oauth_fn: Callable[..., dict[str, str]]
    step_requires_phone_verification_fn: Callable[[dict[str, Any]], bool]
    submit_callback_to_cpa_fn: Callable[..., dict[str, Any]]
    sync_mail_wait_state_fn: Callable[[dict[str, Any], dict[str, Any]], None]
    time_module: Any
    upsert_account_fn: Callable[[dict[str, Any]], dict[str, Any]]
    validate_login_otp_fn: Callable[[Any, str, str], dict[str, Any]]
    wait_for_code_fn: Callable[..., str]
    zh_bool_fn: Callable[[Any], str]


@dataclass(frozen=True)
class ReauthorizeSessionStart:
    result: Any | None = None
    oauth_info: dict[str, Any] | None = None
    login_identifier: str = ""
    state: str = ""
    start_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReauthorizeAccountLoad:
    result: Any | None = None
    account: dict[str, Any] | None = None
    email: str = ""
    password: str = ""
    mailbox: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReauthorizePasswordLoginStage:
    result: Any | None = None
    callback_or_code: str = ""
    code: str = ""


@dataclass(frozen=True)
class ReauthorizePasswordStepContext:
    deps: ReauthorizeAutoFlowDeps
    registrar: Any
    account: dict[str, Any]
    mailbox: dict[str, Any]
    password_info: dict[str, Any]
    state: str
    email: str
    proxy: str
    wait_timeout: int
    wait_interval: int
    request_timeout: int
    submit_ready_callback: Callable[[Any], Any]
    debug: dict[str, Any]


@dataclass(frozen=True)
class ReauthorizeAboutYouStage:
    result: Any | None = None
    callback_or_code: str = ""
    debug_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReauthorizeAuthorizationRuntime:
    account: dict[str, Any]
    mailbox: dict[str, Any]
    email: str
    password: str
    codex2api_url: str
    codex2api_admin_key: str
    codex2api_proxy_url: str
    registrar_proxy: str
    proxy: str
    wait_timeout: int
    wait_interval: int
    request_timeout: int
    debug: dict[str, Any]


def _submit_cpa_callback_and_finalize(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    callback_or_code: str,
    codex2api_url: str,
    codex2api_admin_key: str,
    expected_state: str,
    debug: dict[str, Any],
) -> Any:
    cpa_result = deps.submit_callback_to_cpa_fn(
        callback_or_code,
        cpa_url=codex2api_url,
        cpa_management_key=codex2api_admin_key,
        expected_state=expected_state,
    )
    return deps.finalize_cpa_submit_with_optional_local_tokens_fn(
        registrar,
        account,
        mailbox,
        email=email,
        password=password,
        cpa_callback_url=str(callback_or_code),
        cpa_result=cpa_result,
        debug=debug,
    )


def _submit_final_callback(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    callback_or_code: str,
    codex2api_url: str,
    codex2api_admin_key: str,
    oauth_info: dict[str, Any],
    debug: dict[str, Any],
    code: str = "",
) -> Any:
    cb_params = deps.callback_params_from_url_fn(str(callback_or_code)) or {}
    cb_code = str(cb_params.get("code") or "").strip()
    cb_state = str(cb_params.get("state") or "").strip()
    expected_state = str(oauth_info.get("state") or "").strip()
    debug["callback_summary"] = {
        "has_code": bool(cb_code),
        "code_len": len(cb_code),
        "state_matches": bool(cb_state and expected_state and cb_state == expected_state),
        "state_len": len(cb_state),
        "expected_state_len": len(expected_state),
        "url_prefix": str(callback_or_code)[:80],
    }
    deps.log_stage_fn(
        "OAuth 鍥炶皟鏍￠獙锛氬寘鍚巿鏉冪爜="
        f"{deps.zh_bool_fn(cb_code)}锛屾巿鏉冪爜闀垮害={len(cb_code)}锛宻tate 鍖归厤="
        f"{deps.zh_bool_fn(cb_state and expected_state and cb_state == expected_state)}"
    )
    deps.log_stage_fn("鎻愪氦 OAuth 鍥炶皟鍒?CPA")
    cpa_result = deps.submit_callback_to_cpa_fn(
        callback_or_code,
        cpa_url=codex2api_url,
        cpa_management_key=codex2api_admin_key,
        expected_state=str(oauth_info.get("state") or ""),
    )
    deps.log_stage_fn(f"CPA 鍥炶皟鎻愪氦缁撴灉锛氭垚鍔?{deps.zh_bool_fn(cpa_result.get('ok'))}")
    outcome = deps.finalize_cpa_submit_with_optional_local_tokens_fn(
        registrar,
        account,
        mailbox,
        email=email,
        password=password,
        cpa_callback_url=str(callback_or_code),
        cpa_result=cpa_result,
        debug=debug,
    )
    outcome.code = code
    return outcome


def _fail_reauthorize(
    deps: ReauthorizeAutoFlowDeps,
    account: dict[str, Any],
    message: str,
    *,
    mailbox: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
    code: str = "",
) -> Any:
    if mailbox is None:
        updated = deps.mark_reauthorize_failed_fn(account, message)
    else:
        updated = deps.mark_reauthorize_failed_fn(account, message, mailbox=mailbox)
    kwargs: dict[str, Any] = {
        "ok": False,
        "message": str(updated.get("last_error") or message),
        "account": updated,
        "debug": debug,
    }
    if code:
        kwargs["code"] = code
    return deps.auto_outcome_cls(**kwargs)


def _load_reauthorize_account(
    deps: ReauthorizeAutoFlowDeps,
    account_id: str,
    *,
    codex2api_url: str,
    codex2api_admin_key: str,
) -> ReauthorizeAccountLoad:
    account = deps.get_account_fn(account_id)
    if not account:
        return ReauthorizeAccountLoad(result=deps.auto_outcome_cls(ok=False, message="account_not_found"))
    email = str(account.get("email") or "").strip()
    password = str(account.get("password") or "")
    mailbox = account.get("mailbox") if isinstance(account.get("mailbox"), dict) else {}
    if not email or not password:
        return ReauthorizeAccountLoad(result=deps.auto_outcome_cls(ok=False, message="account_email_or_password_missing", account=account))
    if not mailbox or not str(mailbox.get("provider") or "").strip():
        return ReauthorizeAccountLoad(result=deps.auto_outcome_cls(ok=False, message="mailbox_missing", account=account))
    if not str(codex2api_url or "").strip() or not str(codex2api_admin_key or "").strip():
        return ReauthorizeAccountLoad(result=deps.auto_outcome_cls(ok=False, message="cpa_config_missing", account=account))
    account["status"] = "authorizing"
    account["last_error"] = ""
    account = deps.upsert_account_fn(account)
    return ReauthorizeAccountLoad(account=account, email=email, password=password, mailbox=mailbox)


def _start_reauthorize_oauth_session(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    codex2api_url: str,
    codex2api_admin_key: str,
    codex2api_proxy_url: str,
    registrar_proxy: str,
    debug: dict[str, Any],
) -> ReauthorizeSessionStart:
    deps.log_stage_fn("生成 CPA OAuth 授权地址")
    oauth_info = deps.start_cpa_oauth_fn(
        cpa_url=codex2api_url,
        cpa_management_key=codex2api_admin_key,
        email=email,
        proxy_url=codex2api_proxy_url,
    )
    deps.log_stage_fn(f"CPA 账号代理：{deps.proxy_text_fn(codex2api_proxy_url)}")
    debug["cpa_oauth"] = oauth_info
    deps.log_stage_fn("CPA OAuth 授权地址已生成")
    deps.log_stage_fn("打开 OpenAI 授权页")
    mailbox["_code_after_ts"] = int(deps.time_module.time() * 1000) - 5000
    login_identifier = deps.login_identifier_for_account_fn(account, mailbox, email)
    if login_identifier != email:
        deps.log_stage_fn(f"当前账号使用手机号登录：{login_identifier}")
    info = registrar.start_authorize(email=login_identifier, authorize_url=oauth_info["authorize_url"], screen_hint="login")
    debug["start"] = info
    deps.log_stage_fn(f"OpenAI 授权页打开结果：{deps.response_brief_fn(info)}")
    deps.log_stage_fn("建立登录会话")
    establish_info = registrar.establish_signup_session()
    debug["establish"] = establish_info
    deps.log_stage_fn(f"登录会话建立结果：成功={deps.zh_bool_fn(establish_info.get('ok'))}，流程类型={establish_info.get('flow_kind') or '-'}")
    if not establish_info.get("ok"):
        return ReauthorizeSessionStart(result=_fail_reauthorize(deps, account, "session_establishment_failed", debug=debug))

    if str(mailbox.get("bind_email") or "").strip() or "@" in email:
        mailbox["bind_email"] = str(mailbox.get("bind_email") or email)
    state = str(oauth_info.get("state") or registrar.last_authorize.get("state") or "").strip()
    start_state = deps.registration_state_from_info_fn({"final_url": str(info.get("final_url") or ""), "json": {}, "text": ""})
    deps.log_stage_fn(f"当前授权页：类型={start_state.get('kind') or '-'}，地址={deps.short_url_fn(start_state.get('url') or info.get('final_url'))}")
    return ReauthorizeSessionStart(
        oauth_info=oauth_info,
        login_identifier=login_identifier,
        state=state,
        start_state=start_state,
    )


def _handle_password_login_failure(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    *,
    login_identifier: str,
    password_error: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> Any:
    if deps.login_username_payload_fn(login_identifier).get("kind") == "phone_number":
        message = f"{password_error}:phone_password_login_failed"
        return _fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug)
    deps.log_stage_fn(f"密码登录失败，切换为邮箱验证码登录：原因={password_error}")
    try:
        callback_or_code, validate_info, email_otp_debug = deps.handle_email_otp_step_fn(
            registrar,
            account,
            mailbox,
            state,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
    except Exception as exc:
        message = f"{password_error}; email_otp_fallback_failed:{exc}"
        return _fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug)
    debug["password_fallback_email_otp"] = email_otp_debug
    phone_required_after_email_otp = deps.step_requires_phone_verification_fn(validate_info)
    if phone_required_after_email_otp:
        deps.log_phone_required_after_email_otp_fn(validate_info, "密码登录失败后的邮箱验证码登录")
        return deps.fail_manual_phone_verification_required_fn(account, mailbox, debug, validate_info)
    deps.log_stage_fn(f"授权确认回调结果：回调{deps.ready_text_fn(callback_or_code)}")
    if not callback_or_code:
        return _fail_reauthorize(deps, account, "missing_callback_after_auth", mailbox=mailbox, debug=debug)
    return submit_ready_callback(callback_or_code)


def _prepare_about_you_bind_email_after_password(
    deps: ReauthorizeAutoFlowDeps,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    email: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> str:
    prepared_bind_email = reauthorize_auto_helpers.prepare_about_you_bind_email(
        deps,
        account,
        mailbox,
        email,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )
    about_you_bind_email = prepared_bind_email.email
    if prepared_bind_email.error:
        debug["about_you_bind_email_prepare_error"] = prepared_bind_email.error
    if prepared_bind_email.prepared and about_you_bind_email:
        deps.log_stage_fn(f"about-you 前已准备绑定邮箱：{about_you_bind_email}")
    return about_you_bind_email


def _continue_about_you_after_password_step(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    password_step_state: dict[str, Any],
    state: str,
    *,
    bind_email: str,
    debug: dict[str, Any],
) -> ReauthorizeAboutYouStage:
    try:
        callback_or_code, about_you_debug = deps.continue_with_optional_about_you_fn(
            registrar,
            str(password_step_state.get("url") or deps.first_step_continue_url_fn(password_info) or ""),
            state,
            bind_email=bind_email,
        )
        debug["about_you_after_password"] = about_you_debug
    except Exception as exc:
        message = f"about_you_after_password_failed:{exc}"
        return ReauthorizeAboutYouStage(result=_fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug))
    deps.log_stage_fn(f"about-you 后回调解析结果：回调{deps.ready_text_fn(callback_or_code)}")
    return ReauthorizeAboutYouStage(callback_or_code=str(callback_or_code or ""), debug_info=about_you_debug)


def _retry_about_you_after_missing_email(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    email: str,
    state: str,
    *,
    callback_or_code: str,
    about_you_debug: dict[str, Any],
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> ReauthorizeAboutYouStage:
    if callback_or_code or about_you_debug.get("create_account_error_code") != "missing_email":
        return ReauthorizeAboutYouStage(callback_or_code=callback_or_code, debug_info=about_you_debug)
    deps.log_stage_fn("about-you 缺少邮箱，先绑定邮箱再继续")
    try:
        add_email_url, resolved_bind_email = reauthorize_auto_helpers.continue_add_email_for_account(
            deps,
            registrar,
            account,
            mailbox,
            email,
            continue_url=str(about_you_debug.get("missing_email_continue_url") or f"{deps.auth_base}/add-email"),
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
    except Exception as exc:
        message = f"bind_email_before_about_you_retry_failed:{exc}"
        return ReauthorizeAboutYouStage(result=_fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug))
    debug["add_email_before_about_you_retry"] = {
        "url": str(add_email_url or ""),
        "bind_email": str(resolved_bind_email or ""),
    }
    if deps.callback_params_from_url_fn(add_email_url):
        return ReauthorizeAboutYouStage(callback_or_code=str(add_email_url), debug_info=about_you_debug)
    callback_or_code, about_you_retry_debug = deps.continue_with_optional_about_you_fn(
        registrar,
        str(add_email_url or f"{deps.auth_base}/about-you"),
        state,
    )
    debug["about_you_after_email_bind"] = about_you_retry_debug
    deps.log_stage_fn(f"绑定邮箱后 about-you 回调解析结果：回调{deps.ready_text_fn(callback_or_code)}")
    return ReauthorizeAboutYouStage(callback_or_code=str(callback_or_code or ""), debug_info=about_you_debug)


def _handle_email_otp_after_about_you(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    *,
    callback_or_code: str,
    about_you_debug: dict[str, Any],
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> ReauthorizeAboutYouStage:
    if callback_or_code:
        return ReauthorizeAboutYouStage(callback_or_code=callback_or_code, debug_info=about_you_debug)
    about_you_next_url = str(about_you_debug.get("next_url") or "").strip()
    about_you_next_state = deps.registration_state_from_info_fn({"final_url": about_you_next_url, "json": {}, "text": ""})
    if str(about_you_next_state.get("kind") or "") != "email_otp":
        return ReauthorizeAboutYouStage(debug_info=about_you_debug)
    deps.log_stage_fn("about-you 后进入邮箱验证码，开始校验")
    try:
        callback_or_code, validate_info, email_otp_debug = deps.handle_email_otp_step_fn(
            registrar,
            account,
            mailbox,
            state,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
    except Exception as exc:
        message = f"email_otp_after_about_you_failed:{exc}"
        return ReauthorizeAboutYouStage(result=_fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug))
    debug["email_otp_after_about_you"] = email_otp_debug
    phone_required_after_email_otp = deps.step_requires_phone_verification_fn(validate_info)
    if phone_required_after_email_otp:
        deps.log_phone_required_after_email_otp_fn(validate_info, "about-you 后的邮箱验证码")
        return ReauthorizeAboutYouStage(result=deps.fail_manual_phone_verification_required_fn(account, mailbox, debug, validate_info))
    return ReauthorizeAboutYouStage(callback_or_code=str(callback_or_code or ""), debug_info=about_you_debug)


def _run_about_you_after_password_with_retry(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    password_step_state: dict[str, Any],
    state: str,
    *,
    email: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> ReauthorizeAboutYouStage:
    about_you_bind_email = _prepare_about_you_bind_email_after_password(
        deps,
        account,
        mailbox,
        email,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )
    about_you_stage = _continue_about_you_after_password_step(
        deps,
        registrar,
        account,
        mailbox,
        password_info,
        password_step_state,
        state,
        bind_email=about_you_bind_email,
        debug=debug,
    )
    if about_you_stage.result is not None:
        return about_you_stage
    return _retry_about_you_after_missing_email(
        deps,
        registrar,
        account,
        mailbox,
        email,
        state,
        callback_or_code=about_you_stage.callback_or_code,
        about_you_debug=about_you_stage.debug_info or {},
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )


def _finish_about_you_after_password(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    about_you_stage: ReauthorizeAboutYouStage,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> ReauthorizeAboutYouStage:
    return _handle_email_otp_after_about_you(
        deps,
        registrar,
        account,
        mailbox,
        state,
        callback_or_code=about_you_stage.callback_or_code,
        about_you_debug=about_you_stage.debug_info or {},
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )


def _handle_about_you_after_password(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    password_step_state: dict[str, Any],
    state: str,
    *,
    email: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> Any | None:
    deps.log_stage_fn("密码登录后进入 about-you，继续提交姓名和生日")
    about_you_stage = _run_about_you_after_password_with_retry(
        deps,
        registrar,
        account,
        mailbox,
        password_info,
        password_step_state,
        state,
        email=email,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )
    if about_you_stage.result is not None:
        return about_you_stage.result
    about_you_stage = _finish_about_you_after_password(
        deps,
        registrar,
        account,
        mailbox,
        state,
        about_you_stage,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )
    if about_you_stage.result is not None:
        return about_you_stage.result
    if about_you_stage.callback_or_code:
        return submit_ready_callback(about_you_stage.callback_or_code)
    return None


def _handle_email_otp_after_add_email(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> tuple[str, Any | None]:
    deps.log_stage_fn("绑定邮箱后进入邮箱验证码，开始校验")
    try:
        callback_or_code, validate_info, email_otp_debug = deps.handle_email_otp_step_fn(
            registrar,
            account,
            mailbox,
            state,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
    except Exception as exc:
        message = f"email_otp_after_bind_email_failed:{exc}"
        return "", _fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug)
    debug["email_otp_after_add_email"] = email_otp_debug
    if deps.step_requires_phone_verification_fn(validate_info):
        deps.log_phone_required_after_email_otp_fn(validate_info, "绑定邮箱后的邮箱验证码登录")
        return "", deps.fail_manual_phone_verification_required_fn(account, mailbox, debug, validate_info)
    return str(callback_or_code or ""), None


def _resolve_callback_after_add_email(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    add_email_url: str,
    state: str,
) -> str:
    return deps.resolve_callback_step_fn(
        registrar,
        {
            "ok": True,
            "status": 200,
            "json": {"continue_url": str(add_email_url or "")},
            "final_url": str(add_email_url or ""),
            "text": "",
        },
        state,
        allow_state_resume=True,
    )


def _callback_after_add_email(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    add_email_url: str,
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> tuple[str, Any | None]:
    if deps.callback_params_from_url_fn(add_email_url):
        return str(add_email_url), None
    add_email_state = deps.registration_state_from_info_fn({"final_url": str(add_email_url or ""), "json": {}, "text": ""})
    callback_or_code = ""
    if str(add_email_state.get("kind") or "") == "email_otp":
        callback_or_code, result = _handle_email_otp_after_add_email(
            deps,
            registrar,
            account,
            mailbox,
            state,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            debug=debug,
        )
        if result is not None:
            return "", result
    if not callback_or_code:
        callback_or_code = _resolve_callback_after_add_email(deps, registrar, add_email_url, state)
    return str(callback_or_code or ""), None


def _handle_add_email_after_password(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    password_step_state: dict[str, Any],
    state: str,
    *,
    email: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> Any | None:
    deps.log_stage_fn("密码登录后要求绑定邮箱")
    try:
        add_email_url, resolved_bind_email = reauthorize_auto_helpers.continue_add_email_for_account(
            deps,
            registrar,
            account,
            mailbox,
            email,
            continue_url=str(password_step_state.get("url") or deps.first_step_continue_url_fn(password_info) or ""),
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
    except Exception as exc:
        message = f"bind_email_after_password_failed:{exc}"
        return _fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug)

    debug["add_email_after_password"] = {
        "url": str(add_email_url or ""),
        "bind_email": str(resolved_bind_email or ""),
    }
    deps.log_stage_fn(f"绑定邮箱流程结果：地址={deps.short_url_fn(add_email_url)}")
    callback_or_code, result = _callback_after_add_email(
        deps,
        registrar,
        account,
        mailbox,
        str(add_email_url or ""),
        state,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )
    if result is not None:
        return result
    deps.log_stage_fn(f"绑定邮箱后回调解析结果：回调{deps.ready_text_fn(callback_or_code)}")
    if callback_or_code:
        return submit_ready_callback(callback_or_code)
    return None


def _handle_email_otp_after_password(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> Any | None:
    deps.log_stage_fn("密码登录后进入邮箱验证码，开始校验")
    try:
        callback_or_code, validate_info, email_otp_debug = deps.handle_email_otp_step_fn(
            registrar,
            account,
            mailbox,
            state,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
        )
    except Exception as exc:
        message = f"email_otp_after_password_failed:{exc}"
        return _fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug)

    debug["email_otp_after_password"] = email_otp_debug
    phone_required_after_email_otp = deps.step_requires_phone_verification_fn(validate_info)
    if phone_required_after_email_otp:
        deps.log_phone_required_after_email_otp_fn(validate_info, "密码页后的邮箱验证码登录")
        return deps.fail_manual_phone_verification_required_fn(account, mailbox, debug, validate_info)

    email_otp_state = deps.registration_state_from_info_fn(validate_info)
    if str(email_otp_state.get("kind") or "") == "about_you":
        deps.log_stage_fn("邮箱验证码后进入 about-you，继续提交姓名/生日")
        try:
            callback_or_code, about_you_debug = deps.continue_with_optional_about_you_fn(
                registrar,
                str(email_otp_state.get("url") or deps.first_step_continue_url_fn(password_info) or ""),
                state,
            )
            debug["about_you_after_email_otp"] = about_you_debug
        except Exception as exc:
            message = f"about_you_after_email_otp_failed:{exc}"
            return _fail_reauthorize(deps, account, message, mailbox=mailbox, debug=debug)
    elif str(email_otp_state.get("kind") or "") == "add_email":
        deps.log_stage_fn("邮箱验证码后要求绑定邮箱，停止重复发送邮箱验证码")
        return _fail_reauthorize(deps, account, "missing_callback_after_auth", mailbox=mailbox, debug=debug)

    deps.log_stage_fn(f"密码页邮箱验证码后回调解析结果：回调{deps.ready_text_fn(callback_or_code)}")
    if callback_or_code:
        return submit_ready_callback(callback_or_code)
    return None


def _handle_initial_authorize_state(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    start_state: dict[str, Any],
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> Any | None:
    kind = str(start_state.get("kind") or "")
    if kind == "email_otp":
        deps.log_stage_fn("授权页直接进入邮箱验证码，先校验验证码")
        try:
            callback_or_code, validate_info, email_otp_debug = deps.handle_email_otp_step_fn(
                registrar,
                account,
                mailbox,
                state,
                proxy=proxy,
                wait_timeout=wait_timeout,
                wait_interval=wait_interval,
                request_timeout=request_timeout,
            )
        except Exception as exc:
            return _fail_reauthorize(deps, account, str(exc), mailbox=mailbox, debug=debug)
        debug["pre_password_email_otp"] = email_otp_debug
        deps.log_stage_fn(f"授权确认回调结果：回调{deps.ready_text_fn(callback_or_code)}")
        if callback_or_code:
            return submit_ready_callback(callback_or_code)
        deps.log_stage_fn("邮箱验证码已通过但未拿到回调，继续走密码登录")
        return None
    if kind == "password":
        deps.log_stage_fn("授权页进入密码页，准备提交密码")
        return None
    if kind == "callback":
        return submit_ready_callback(str(start_state.get("url") or ""))
    if kind not in {"continue", "unknown", ""}:
        return _fail_reauthorize(deps, account, f"unsupported_reauthorize_page:{start_state.get('kind')}", mailbox=mailbox, debug=debug)
    return None


def _password_step_result_stage(outcome: Any | None) -> ReauthorizePasswordLoginStage | None:
    if outcome is None:
        return None
    return ReauthorizePasswordLoginStage(result=outcome)


def _about_you_password_step_stage(
    ctx: ReauthorizePasswordStepContext,
    password_step_state: dict[str, Any],
) -> ReauthorizePasswordLoginStage | None:
    outcome = _handle_about_you_after_password(
        ctx.deps,
        ctx.registrar,
        ctx.account,
        ctx.mailbox,
        ctx.password_info,
        password_step_state,
        ctx.state,
        email=ctx.email,
        proxy=ctx.proxy,
        wait_timeout=ctx.wait_timeout,
        wait_interval=ctx.wait_interval,
        request_timeout=ctx.request_timeout,
        submit_ready_callback=ctx.submit_ready_callback,
        debug=ctx.debug,
    )
    return _password_step_result_stage(outcome)


def _add_email_password_step_stage(
    ctx: ReauthorizePasswordStepContext,
    password_step_state: dict[str, Any],
) -> ReauthorizePasswordLoginStage | None:
    outcome = _handle_add_email_after_password(
        ctx.deps,
        ctx.registrar,
        ctx.account,
        ctx.mailbox,
        ctx.password_info,
        password_step_state,
        ctx.state,
        email=ctx.email,
        proxy=ctx.proxy,
        wait_timeout=ctx.wait_timeout,
        wait_interval=ctx.wait_interval,
        request_timeout=ctx.request_timeout,
        submit_ready_callback=ctx.submit_ready_callback,
        debug=ctx.debug,
    )
    return _password_step_result_stage(outcome)


def _email_otp_password_step_stage(ctx: ReauthorizePasswordStepContext) -> ReauthorizePasswordLoginStage | None:
    outcome = _handle_email_otp_after_password(
        ctx.deps,
        ctx.registrar,
        ctx.account,
        ctx.mailbox,
        ctx.password_info,
        ctx.state,
        proxy=ctx.proxy,
        wait_timeout=ctx.wait_timeout,
        wait_interval=ctx.wait_interval,
        request_timeout=ctx.request_timeout,
        submit_ready_callback=ctx.submit_ready_callback,
        debug=ctx.debug,
    )
    return _password_step_result_stage(outcome)


def _handle_password_step_state_after_login(
    ctx: ReauthorizePasswordStepContext,
    password_step_state: dict[str, Any],
) -> ReauthorizePasswordLoginStage | None:
    kind = str(password_step_state.get("kind") or "")
    if kind == "about_you":
        return _about_you_password_step_stage(ctx, password_step_state)
    if kind == "add_email":
        return _add_email_password_step_stage(ctx, password_step_state)
    if kind == "email_otp":
        return _email_otp_password_step_stage(ctx)
    return None


def _handle_password_step_state_when_callback_missing(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    state: str,
    *,
    email: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> ReauthorizePasswordLoginStage | None:
    password_step_state = reauthorize_auto_helpers.password_step_state(deps, password_info)
    ctx = ReauthorizePasswordStepContext(
        deps=deps,
        registrar=registrar,
        account=account,
        mailbox=mailbox,
        password_info=password_info,
        state=state,
        email=email,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        submit_ready_callback=submit_ready_callback,
        debug=debug,
    )
    return _handle_password_step_state_after_login(
        ctx,
        password_step_state,
    )


def _run_email_otp_callback_fallback_after_password(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> ReauthorizePasswordLoginStage:
    email_otp_result = reauthorize_auto_helpers.run_email_otp_callback_fallback(
        deps,
        registrar,
        account,
        mailbox,
        password_info,
        debug,
        state=state,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )
    code = email_otp_result.code
    if email_otp_result.failure_message:
        return ReauthorizePasswordLoginStage(
            result=_fail_reauthorize(deps, account, email_otp_result.failure_message, mailbox=mailbox, debug=debug, code=code)
        )
    if email_otp_result.manual_phone_verification_info is not None:
        return ReauthorizePasswordLoginStage(
            result=deps.fail_manual_phone_verification_required_fn(
                account,
                mailbox,
                debug,
                email_otp_result.manual_phone_verification_info,
                code=code,
            )
        )
    return ReauthorizePasswordLoginStage(callback_or_code=str(email_otp_result.callback_or_code or ""), code=code)


def _missing_callback_after_auth_stage(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    *,
    debug: dict[str, Any],
    code: str,
) -> ReauthorizePasswordLoginStage:
    reauthorize_auto_helpers.probe_authorize_resume(
        deps,
        registrar,
        debug,
        auth_base=deps.auth_base,
        state=state,
    )
    return ReauthorizePasswordLoginStage(
        result=_fail_reauthorize(deps, account, "missing_callback_after_auth", mailbox=mailbox, debug=debug, code=code)
    )


def _resolve_callback_via_email_otp_fallback(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
    password_info: dict[str, Any],
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> ReauthorizePasswordLoginStage:
    state = str(oauth_info.get("state") or registrar.last_authorize.get("state") or "").strip()
    email_otp_stage = _run_email_otp_callback_fallback_after_password(
        deps,
        registrar,
        account,
        mailbox,
        password_info,
        state=state,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug=debug,
    )
    if email_otp_stage.result is not None:
        return email_otp_stage
    callback_or_code = str(email_otp_stage.callback_or_code or "")
    if callback_or_code:
        return ReauthorizePasswordLoginStage(callback_or_code=callback_or_code, code=email_otp_stage.code)
    return _missing_callback_after_auth_stage(
        deps,
        registrar,
        account,
        mailbox,
        state,
        debug=debug,
        code=email_otp_stage.code,
    )


def _resolve_password_login_success_callback(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
    password_info: dict[str, Any],
    state: str,
    *,
    email: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> ReauthorizePasswordLoginStage:
    callback_or_code = ""
    deps.log_stage_fn("尝试解析密码登录后的 OAuth 回调")
    callback_or_code = callback_or_code or deps.resolve_callback_step_fn(registrar, password_info, state, allow_state_resume=False)
    deps.log_stage_fn(f"密码登录后回调解析结果：回调{deps.ready_text_fn(callback_or_code)}")
    if not callback_or_code:
        password_step_outcome = _handle_password_step_state_when_callback_missing(
            deps,
            registrar,
            account,
            mailbox,
            password_info,
            state,
            email=email,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            submit_ready_callback=submit_ready_callback,
            debug=debug,
        )
        if password_step_outcome is not None:
            return password_step_outcome

    if not callback_or_code:
        return _resolve_callback_via_email_otp_fallback(
            deps,
            registrar,
            account,
            mailbox,
            oauth_info,
            password_info,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            debug=debug,
        )

    return ReauthorizePasswordLoginStage(callback_or_code=str(callback_or_code), code="")


def _handle_password_login_stage(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
    state: str,
    *,
    email: str,
    password: str,
    login_identifier: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    submit_ready_callback: Callable[[Any], Any],
    debug: dict[str, Any],
) -> ReauthorizePasswordLoginStage:
    deps.log_stage_fn("提交密码登录")
    password_info = deps.attempt_password_login_fn(registrar, login_identifier, password)
    debug["login_password"] = password_info
    deps.log_stage_fn(f"密码登录结果：{deps.response_brief_fn(password_info)}")
    if not password_info.get("ok"):
        password_error = f"login_password_{password_info.get('status') or 0}"
        return ReauthorizePasswordLoginStage(
            result=_handle_password_login_failure(
                deps,
                registrar,
                account,
                mailbox,
                state,
                login_identifier=login_identifier,
                password_error=password_error,
                proxy=proxy,
                wait_timeout=wait_timeout,
                wait_interval=wait_interval,
                request_timeout=request_timeout,
                submit_ready_callback=submit_ready_callback,
                debug=debug,
            )
        )

    if deps.step_requires_phone_verification_fn(password_info):
        deps.log_stage_fn(f"密码登录后 OpenAI 要求手机二次验证：{deps.phone_verification_page_brief_fn(password_info)}")
        return ReauthorizePasswordLoginStage(
            result=deps.fail_manual_phone_verification_required_fn(account, mailbox, debug, password_info)
        )

    return _resolve_password_login_success_callback(
        deps,
        registrar,
        account,
        mailbox,
        oauth_info,
        password_info,
        state,
        email=email,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        submit_ready_callback=submit_ready_callback,
        debug=debug,
    )


def _build_reauthorize_ready_callback(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    codex2api_url: str,
    codex2api_admin_key: str,
    oauth_info: dict[str, Any],
    debug: dict[str, Any],
) -> Callable[[Any], Any]:
    def _submit_ready_callback(callback_or_code_value: Any) -> Any:
        return _submit_cpa_callback_and_finalize(
            deps,
            registrar,
            account,
            mailbox,
            email=email,
            password=password,
            callback_or_code=str(callback_or_code_value),
            codex2api_url=codex2api_url,
            codex2api_admin_key=codex2api_admin_key,
            expected_state=oauth_info.get("state") or "",
            debug=debug,
        )

    return _submit_ready_callback


def _build_reauthorize_ready_callback_for_runtime(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    runtime: ReauthorizeAuthorizationRuntime,
    oauth_info: dict[str, Any],
) -> Callable[[Any], Any]:
    return _build_reauthorize_ready_callback(
        deps,
        registrar,
        runtime.account,
        runtime.mailbox,
        email=runtime.email,
        password=runtime.password,
        codex2api_url=runtime.codex2api_url,
        codex2api_admin_key=runtime.codex2api_admin_key,
        oauth_info=oauth_info,
        debug=runtime.debug,
    )


def _handle_initial_authorize_state_for_runtime(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    runtime: ReauthorizeAuthorizationRuntime,
    session_start: ReauthorizeSessionStart,
    submit_ready_callback: Callable[[Any], Any],
) -> Any | None:
    return _handle_initial_authorize_state(
        deps,
        registrar,
        runtime.account,
        runtime.mailbox,
        session_start.start_state or {},
        session_start.state,
        proxy=runtime.proxy,
        wait_timeout=runtime.wait_timeout,
        wait_interval=runtime.wait_interval,
        request_timeout=runtime.request_timeout,
        submit_ready_callback=submit_ready_callback,
        debug=runtime.debug,
    )


def _handle_password_login_stage_for_runtime(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    runtime: ReauthorizeAuthorizationRuntime,
    session_start: ReauthorizeSessionStart,
    submit_ready_callback: Callable[[Any], Any],
) -> ReauthorizePasswordLoginStage:
    return _handle_password_login_stage(
        deps,
        registrar,
        runtime.account,
        runtime.mailbox,
        session_start.oauth_info or {},
        session_start.state,
        email=runtime.email,
        password=runtime.password,
        login_identifier=session_start.login_identifier,
        proxy=runtime.proxy,
        wait_timeout=runtime.wait_timeout,
        wait_interval=runtime.wait_interval,
        request_timeout=runtime.request_timeout,
        submit_ready_callback=submit_ready_callback,
        debug=runtime.debug,
    )


def _submit_final_callback_for_runtime(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    runtime: ReauthorizeAuthorizationRuntime,
    session_start: ReauthorizeSessionStart,
    password_stage: ReauthorizePasswordLoginStage,
) -> Any:
    return _submit_final_callback(
        deps,
        registrar,
        runtime.account,
        runtime.mailbox,
        email=runtime.email,
        password=runtime.password,
        callback_or_code=str(password_stage.callback_or_code),
        codex2api_url=runtime.codex2api_url,
        codex2api_admin_key=runtime.codex2api_admin_key,
        oauth_info=session_start.oauth_info or {},
        debug=runtime.debug,
        code=password_stage.code,
    )


def _run_reauthorize_authorization_runtime(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    runtime: ReauthorizeAuthorizationRuntime,
) -> Any:
    session_start = _start_reauthorize_oauth_session(
        deps,
        registrar,
        runtime.account,
        runtime.mailbox,
        email=runtime.email,
        codex2api_url=runtime.codex2api_url,
        codex2api_admin_key=runtime.codex2api_admin_key,
        codex2api_proxy_url=runtime.codex2api_proxy_url,
        registrar_proxy=runtime.registrar_proxy,
        debug=runtime.debug,
    )
    if session_start.result is not None:
        return session_start.result
    submit_ready_callback = _build_reauthorize_ready_callback_for_runtime(
        deps,
        registrar,
        runtime,
        session_start.oauth_info or {},
    )
    initial_state_outcome = _handle_initial_authorize_state_for_runtime(
        deps,
        registrar,
        runtime,
        session_start,
        submit_ready_callback,
    )
    if initial_state_outcome is not None:
        return initial_state_outcome
    password_stage = _handle_password_login_stage_for_runtime(
        deps,
        registrar,
        runtime,
        session_start,
        submit_ready_callback,
    )
    if password_stage.result is not None:
        return password_stage.result
    return _submit_final_callback_for_runtime(deps, registrar, runtime, session_start, password_stage)


def _run_reauthorize_authorization_flow(
    deps: ReauthorizeAutoFlowDeps,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    codex2api_url: str,
    codex2api_admin_key: str,
    codex2api_proxy_url: str,
    registrar_proxy: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> Any:
    return _run_reauthorize_authorization_runtime(
        deps,
        registrar,
        ReauthorizeAuthorizationRuntime(
            account=account,
            mailbox=mailbox,
            email=email,
            password=password,
            codex2api_url=codex2api_url,
            codex2api_admin_key=codex2api_admin_key,
            codex2api_proxy_url=codex2api_proxy_url,
            registrar_proxy=registrar_proxy,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            debug=debug,
        ),
    )


def _run_loaded_auto_reauthorize_account(
    deps: ReauthorizeAutoFlowDeps,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    codex2api_url: str,
    codex2api_admin_key: str,
    codex2api_proxy_url: str,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
    debug: dict[str, Any],
) -> Any:
    registrar_proxy = str(proxy or "").strip()
    deps.log_stage_fn(f"OpenAI 代理：{deps.proxy_text_fn(registrar_proxy)}")
    registrar = deps.platform_registrar_cls(registrar_proxy)
    try:
        return _run_reauthorize_authorization_flow(
            deps,
            registrar,
            account,
            mailbox,
            email=email,
            password=password,
            codex2api_url=codex2api_url,
            codex2api_admin_key=codex2api_admin_key,
            codex2api_proxy_url=codex2api_proxy_url,
            registrar_proxy=registrar_proxy,
            proxy=proxy,
            wait_timeout=wait_timeout,
            wait_interval=wait_interval,
            request_timeout=request_timeout,
            debug=debug,
        )
    except Exception as exc:
        return _fail_reauthorize(deps, account, str(exc or "reauthorize_failed"), debug=debug)
    finally:
        try:
            registrar.close()
        except Exception:
            pass


def auto_reauthorize_account_with_email_otp(
    account_id: str,
    *,
    codex2api_url: str = "",
    codex2api_admin_key: str = "",
    codex2api_proxy_url: str = "",
    proxy: str = "",
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
    sms_provider: str = "",
    sms_api_key: str = "",
    hero_sms_api_key: str = "",
    smsbower_api_key: str = "",
    fivesim_api_key: str = "",
    hero_sms_base_url: str = "",
    smsbower_base_url: str = "",
    hero_sms_country: str = "",
    hero_sms_service: str = "",
    hero_sms_min_price: float | str = 0.0,
    hero_sms_max_price: float | str = 0.0,
    hero_sms_wait_timeout: int = 180,
    hero_sms_wait_interval: int = 5,
    hero_sms_auto_retry: bool = False,
    hero_sms_retry_count: int = 3,
    sms_wait_timeout: int | None = None,
    sms_wait_interval: int | None = None,
    sms_resend_after_seconds: int | None = None,
    sms_timeout_after_resend_seconds: int | None = None,
    sms_release_after_seconds: int | None = None,
    sms_auto_retry: bool | None = None,
    sms_retry_count: int | None = None,
    allow_phone_verification: bool = False,
    deps: ReauthorizeAutoFlowDeps,
) -> Any:
    loaded = _load_reauthorize_account(
        deps,
        account_id,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
    )
    if loaded.result is not None:
        return loaded.result
    return _run_loaded_auto_reauthorize_account(
        deps,
        loaded.account or {},
        loaded.mailbox or {},
        email=loaded.email,
        password=loaded.password,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        codex2api_proxy_url=codex2api_proxy_url,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        debug={},
    )
