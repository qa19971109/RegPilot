from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .registration_cpa_add_email import RegistrationCpaAddEmailHandler


@dataclass(frozen=True)
class RegistrationTokenExchangeDeps:
    asdict_fn: Callable[[Any], dict[str, Any]]
    brief_flow_url_fn: Callable[[str], str]
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    chatgpt_signup_redirect_uri: str
    cpa_oauth_helpers_module: Any
    follow_chatgpt_signup_callback_fn: Callable[[Any, str], dict[str, Any]]
    log_fn: Callable[[str], None]
    parse_bool_fn: Callable[..., bool]
    registration_result_cls: Callable[..., Any]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, str]]


@dataclass(frozen=True)
class RegistrationCpaPasswordStageResult:
    resolved_callback: str = ""
    phone_info: dict[str, Any] | None = None
    phone_debug: dict[str, Any] | None = None
    result: Any = None


@dataclass(frozen=True)
class RegistrationCpaOauthSession:
    cpa_oauth: dict[str, Any]
    oauth_info: dict[str, Any]
    expected_state: str
    add_email_handler: RegistrationCpaAddEmailHandler


@dataclass(frozen=True)
class RegistrationCpaOauthRuntime:
    attempt_password_login_fn: Callable[..., dict[str, Any]]
    build_reauthorize_sms_config_fn: Callable[..., Any]
    continue_with_optional_add_email_fn: Callable[..., tuple[str, str]]
    continue_with_optional_phone_verification_fn: Callable[..., tuple[str, dict[str, Any], dict[str, Any]]]
    resolve_callback_step_fn: Callable[..., str]
    resolve_oauth_callback_fn: Callable[[Any, str, str], str]
    start_cpa_oauth_fn: Callable[..., dict[str, Any]]
    step_requires_phone_verification_fn: Callable[[dict[str, Any]], bool]
    submit_callback_to_cpa_fn: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class RegistrationCpaOauthExchangeContext:
    runtime: RegistrationCpaOauthRuntime
    sms_config_and_retry_count_fn: Callable[[], tuple[Any, int]]
    cpa_session: RegistrationCpaOauthSession
    resolved_callback: str = ""
    phone_info: dict[str, Any] | None = None
    phone_debug: dict[str, Any] | None = None
    early_result: Any = None


def _continue_cpa_add_email_if_needed(
    add_email_handler: RegistrationCpaAddEmailHandler,
    continue_url: str,
    *,
    source: str,
) -> str:
    add_email_handler.continue_if_needed(continue_url, source=source)
    return str(add_email_handler.resolved_callback or "")


def _attach_account_metadata(token_result: Any, *, password: str, mailbox: dict[str, Any]) -> Any:
    token_result.password = password
    token_result.mailbox = mailbox
    return token_result


def _registration_failure_result(
    deps: RegistrationTokenExchangeDeps,
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    callback_url: str,
    error: str,
) -> Any:
    return deps.registration_result_cls(
        ok=False,
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=callback_url,
        error=error,
    )


def _cpa_callback_registration_result(
    deps: RegistrationTokenExchangeDeps,
    *,
    config: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    callback_url: str,
    expected_state: str,
    submit_callback_to_cpa_fn: Callable[..., dict[str, Any]],
) -> Any:
    cpa_result = submit_callback_to_cpa_fn(
        callback_url,
        cpa_url=config.codex2api_url,
        cpa_management_key=config.codex2api_admin_key,
        expected_state=expected_state,
    )
    mailbox["_callback_url"] = callback_url
    mailbox["_cpa_submit_ok"] = bool(cpa_result.get("ok"))
    mailbox["_cpa_submit_message"] = str(cpa_result.get("message") or "")
    deps.log_fn(f"CPA 回调提交结果：ok={mailbox['_cpa_submit_ok']} message={mailbox['_cpa_submit_message']}")
    return deps.registration_result_cls(
        ok=bool(cpa_result.get("ok")),
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=callback_url,
        error="" if bool(cpa_result.get("ok")) else str(cpa_result.get("message") or "cpa_callback_submit_failed"),
    )


def _exchange_tokens_via_oauth_fallback(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    resolve_oauth_callback_fn: Callable[[Any, str, str], str],
) -> Any:
    RegistrationResult = deps.registration_result_cls
    try:
        oauth_info = registrar.start_authorize(email=email, screen_hint="login")
    except TypeError as exc:
        if "screen_hint" not in str(exc) and "unexpected keyword" not in str(exc):
            raise
        oauth_info = registrar.start_authorize(email=email)
    deps.log_fn(
        "注册后 OAuth 授权入口已打开："
        f"status={oauth_info.get('status')} final_url={deps.brief_flow_url_fn(str(oauth_info.get('final_url') or ''))}"
    )
    resolved_callback = ""
    try:
        resolved_callback = resolve_oauth_callback_fn(
            registrar,
            str(oauth_info.get("final_url") or ""),
            str(oauth_info.get("state") or ""),
        )
    except Exception as exc:
        deps.log_fn(f"注册后 OAuth 回调解析异常：{exc}")
    if not resolved_callback:
        return RegistrationResult(
            ok=False,
            email=email,
            password=password,
            mailbox=mailbox,
            callback_url=str(oauth_info.get("final_url") or ""),
            error="oauth_callback_not_reached",
        )
    token_result = registrar.exchange_platform_tokens(str(oauth_info.get("code_verifier") or ""), resolved_callback)
    return _attach_account_metadata(token_result, password=password, mailbox=mailbox)


def _cpa_password_stage_failure(
    deps: RegistrationTokenExchangeDeps,
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    callback_url: str,
    error: str,
) -> RegistrationCpaPasswordStageResult:
    return RegistrationCpaPasswordStageResult(
        result=_registration_failure_result(
            deps,
            email=email,
            password=password,
            mailbox=mailbox,
            callback_url=callback_url,
            error=error,
        )
    )


def _establish_cpa_login_session(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
) -> RegistrationCpaPasswordStageResult | None:
    if not hasattr(registrar, "establish_signup_session"):
        return None
    establish_info = registrar.establish_signup_session()
    deps.log_fn(
        f"注册后 CPA OAuth 登录会话建立：ok={establish_info.get('ok')} "
        f"kind={establish_info.get('flow_kind') or '-'}"
    )
    if establish_info.get("ok"):
        return None
    return _cpa_password_stage_failure(
        deps,
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=str(oauth_info.get("final_url") or ""),
        error="cpa_login_session_establishment_failed",
    )


def _attempt_cpa_password_login_stage(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
    attempt_password_login_fn: Callable[[Any, str, str], dict[str, Any]],
) -> tuple[dict[str, Any], RegistrationCpaPasswordStageResult | None]:
    deps.log_fn("注册后 CPA OAuth 密码页已打开，提交刚创建账号的密码")
    password_info = attempt_password_login_fn(registrar, email, password)
    deps.log_fn(f"注册后 CPA OAuth 密码登录结果：status={password_info.get('status')} ok={password_info.get('ok')}")
    if password_info.get("ok"):
        return password_info, None
    return password_info, _cpa_password_stage_failure(
        deps,
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=str(oauth_info.get("final_url") or ""),
        error=f"cpa_login_password_{password_info.get('status') or 0}",
    )


def _resolve_cpa_password_phone_verification(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password_info: dict[str, Any],
    expected_state: str,
    sms_config_and_retry_count_fn: Callable[[], tuple[Any, int]],
    continue_with_optional_phone_verification_fn: Callable[..., tuple[str, dict[str, Any], dict[str, Any]]],
    step_requires_phone_verification_fn: Callable[[dict[str, Any]], bool],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not step_requires_phone_verification_fn(password_info):
        return "", {}, {}
    try:
        sms_config, retry_count = sms_config_and_retry_count_fn()
        resolved_callback, phone_info, phone_debug = continue_with_optional_phone_verification_fn(
            registrar,
            password_info,
            expected_state,
            sms_config=sms_config,
            retry_count=retry_count,
            email=email,
        )
        deps.log_fn(
            "注册后 CPA OAuth 密码后手机验证处理："
            f"provider={sms_config.provider} callback={'ready' if resolved_callback else 'missing'}"
        )
        return resolved_callback, phone_info, phone_debug
    except Exception as exc:
        deps.log_fn(f"注册后 CPA OAuth 密码后手机验证失败：{exc}")
        raise


def _resolve_cpa_password_callback_from_login(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    password_info: dict[str, Any],
    expected_state: str,
    add_email_handler: RegistrationCpaAddEmailHandler,
    resolve_callback_step_fn: Callable[..., str],
) -> str:
    resolved_callback = ""
    password_state = deps.registration_state_from_info_fn(password_info)
    if str(password_state.get("kind") or "") == "add_email":
        resolved_callback = _continue_cpa_add_email_if_needed(
            add_email_handler,
            str(password_state.get("url") or ""),
            source="password",
        )
    if not resolved_callback:
        resolved_callback = resolve_callback_step_fn(registrar, password_info, expected_state, allow_state_resume=False)
        deps.log_fn(f"注册后 CPA OAuth 密码登录后回调：{'ready' if resolved_callback else 'missing'}")
    if not resolved_callback:
        resolved_callback = resolve_callback_step_fn(registrar, password_info, expected_state, allow_state_resume=True)
        deps.log_fn(f"注册后 CPA OAuth 密码登录后 state 恢复回调：{'ready' if resolved_callback else 'missing'}")
    return resolved_callback


def _resolve_cpa_password_callback_after_reopen(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    cpa_oauth: dict[str, Any],
    expected_state: str,
    add_email_handler: RegistrationCpaAddEmailHandler,
    resolve_callback_step_fn: Callable[..., str],
) -> str:
    try:
        reopened_info = registrar.start_authorize(
            email=email,
            authorize_url=str(cpa_oauth.get("authorize_url") or ""),
            screen_hint="login",
        )
    except Exception as exc:
        reopened_info = {"ok": False, "status": 0, "final_url": "", "error": str(exc)}
    deps.log_fn(
        "注册后 CPA OAuth 密码登录后重开授权入口："
        f"status={reopened_info.get('status')} final_url={deps.brief_flow_url_fn(str(reopened_info.get('final_url') or ''))}"
    )
    reopened_state = deps.registration_state_from_info_fn(
        {
            "final_url": str(reopened_info.get("final_url") or ""),
            "json": reopened_info.get("json") if isinstance(reopened_info.get("json"), dict) else {},
            "text": str(reopened_info.get("text") or ""),
        }
    )
    resolved_callback = ""
    if str(reopened_state.get("kind") or "") == "add_email":
        resolved_callback = _continue_cpa_add_email_if_needed(
            add_email_handler,
            str(reopened_state.get("url") or reopened_info.get("final_url") or ""),
            source="password_reopen",
        )
    if not resolved_callback:
        resolved_callback = resolve_callback_step_fn(registrar, reopened_info, expected_state, allow_state_resume=True)
    deps.log_fn(f"注册后 CPA OAuth 密码登录后重开授权回调：{'ready' if resolved_callback else 'missing'}")
    return resolved_callback


def _resolve_cpa_password_callback_sequence(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password_info: dict[str, Any],
    cpa_oauth: dict[str, Any],
    expected_state: str,
    add_email_handler: RegistrationCpaAddEmailHandler,
    sms_config_and_retry_count_fn: Callable[[], tuple[Any, int]],
    runtime: RegistrationCpaOauthRuntime,
) -> RegistrationCpaPasswordStageResult:
    resolved_callback, phone_info, phone_debug = _resolve_cpa_password_phone_verification(
        deps,
        registrar=registrar,
        email=email,
        password_info=password_info,
        expected_state=expected_state,
        sms_config_and_retry_count_fn=sms_config_and_retry_count_fn,
        continue_with_optional_phone_verification_fn=runtime.continue_with_optional_phone_verification_fn,
        step_requires_phone_verification_fn=runtime.step_requires_phone_verification_fn,
    )
    if not resolved_callback:
        resolved_callback = _resolve_cpa_password_callback_from_login(
            deps,
            registrar=registrar,
            password_info=password_info,
            expected_state=expected_state,
            add_email_handler=add_email_handler,
            resolve_callback_step_fn=runtime.resolve_callback_step_fn,
        )
    if not resolved_callback:
        resolved_callback = _resolve_cpa_password_callback_after_reopen(
            deps,
            registrar=registrar,
            email=email,
            cpa_oauth=cpa_oauth,
            expected_state=expected_state,
            add_email_handler=add_email_handler,
            resolve_callback_step_fn=runtime.resolve_callback_step_fn,
        )
    return RegistrationCpaPasswordStageResult(
        resolved_callback=resolved_callback,
        phone_info=phone_info,
        phone_debug=phone_debug,
    )


def _handle_cpa_oauth_password_page(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
    cpa_oauth: dict[str, Any],
    expected_state: str,
    add_email_handler: RegistrationCpaAddEmailHandler,
    sms_config_and_retry_count_fn: Callable[[], tuple[Any, int]],
    runtime: RegistrationCpaOauthRuntime,
) -> RegistrationCpaPasswordStageResult:
    session_failure = _establish_cpa_login_session(
        deps,
        registrar=registrar,
        email=email,
        password=password,
        mailbox=mailbox,
        oauth_info=oauth_info,
    )
    if session_failure is not None:
        return session_failure
    password_info, login_failure = _attempt_cpa_password_login_stage(
        deps,
        registrar=registrar,
        email=email,
        password=password,
        mailbox=mailbox,
        oauth_info=oauth_info,
        attempt_password_login_fn=runtime.attempt_password_login_fn,
    )
    if login_failure is not None:
        return login_failure
    return _resolve_cpa_password_callback_sequence(
        deps,
        registrar=registrar,
        email=email,
        password_info=password_info,
        cpa_oauth=cpa_oauth,
        expected_state=expected_state,
        sms_config_and_retry_count_fn=sms_config_and_retry_count_fn,
        add_email_handler=add_email_handler,
        runtime=runtime,
    )


def _start_registration_cpa_oauth_session(
    deps: RegistrationTokenExchangeDeps,
    *,
    config: Any,
    registrar: Any,
    email: str,
    mailbox: dict[str, Any],
    start_cpa_oauth_fn: Callable[..., dict[str, Any]],
    continue_with_optional_add_email_fn: Callable[..., tuple[str, str]],
    resolve_oauth_callback_fn: Callable[[Any, str, str], str],
    resolve_callback_step_fn: Callable[..., str],
) -> RegistrationCpaOauthSession:
    cpa_oauth = start_cpa_oauth_fn(
        cpa_url=config.codex2api_url,
        cpa_management_key=config.codex2api_admin_key,
        email=email,
        proxy_url=str(config.codex2api_proxy_url or "").strip(),
    )
    deps.log_fn("注册后 CPA OAuth 授权参数已获取")
    oauth_info = registrar.start_authorize(
        email=email,
        authorize_url=str(cpa_oauth.get("authorize_url") or ""),
        screen_hint="login",
    )
    deps.log_fn(
        "注册后 CPA OAuth 授权入口已打开："
        f"status={oauth_info.get('status')} final_url={deps.brief_flow_url_fn(str(oauth_info.get('final_url') or ''))}"
    )
    expected_state = str(cpa_oauth.get("state") or oauth_info.get("state") or "").strip()

    def _mail_config_for_add_email() -> dict[str, Any]:
        return deps.cpa_oauth_helpers_module.mail_config_for_add_email(config, asdict_fn=deps.asdict_fn)

    add_email_handler = RegistrationCpaAddEmailHandler(
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        cpa_oauth=cpa_oauth,
        expected_state=expected_state,
        log_fn=deps.log_fn,
        brief_flow_url_fn=deps.brief_flow_url_fn,
        mail_config_for_add_email_fn=_mail_config_for_add_email,
        continue_with_optional_add_email_fn=continue_with_optional_add_email_fn,
        callback_params_from_url_fn=deps.callback_params_from_url_fn,
        resolve_oauth_callback_fn=resolve_oauth_callback_fn,
        resolve_callback_step_fn=resolve_callback_step_fn,
        registration_state_from_info_fn=deps.registration_state_from_info_fn,
    )
    return RegistrationCpaOauthSession(
        cpa_oauth=cpa_oauth,
        oauth_info=oauth_info,
        expected_state=expected_state,
        add_email_handler=add_email_handler,
    )


def _resolve_registration_cpa_start_callback(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    cpa_session: RegistrationCpaOauthSession,
    sms_config_and_retry_count_fn: Callable[[], tuple[Any, int]],
    runtime: RegistrationCpaOauthRuntime,
) -> RegistrationCpaPasswordStageResult:
    log = deps.log_fn
    _brief_flow_url = deps.brief_flow_url_fn
    _registration_state_from_info = deps.registration_state_from_info_fn
    oauth_info = cpa_session.oauth_info
    start_state = _registration_state_from_info({"final_url": str(oauth_info.get("final_url") or ""), "json": {}, "text": ""})
    log(f"注册后 CPA OAuth 当前页面：kind={start_state.get('kind') or '-'} url={_brief_flow_url(str(start_state.get('url') or oauth_info.get('final_url') or ''))}")
    resolved_callback = ""
    phone_info: dict[str, Any] = {}
    phone_debug: dict[str, Any] = {}
    if str(start_state.get("kind") or "") == "add_email":
        resolved_callback = _continue_cpa_add_email_if_needed(
            cpa_session.add_email_handler,
            str(start_state.get("url") or oauth_info.get("final_url") or ""),
            source="start",
        )
    if not resolved_callback and str(start_state.get("kind") or "") == "password":
        password_stage = _handle_cpa_oauth_password_page(
            deps,
            registrar=registrar,
            email=email,
            password=password,
            mailbox=mailbox,
            oauth_info=oauth_info,
            cpa_oauth=cpa_session.cpa_oauth,
            expected_state=cpa_session.expected_state,
            add_email_handler=cpa_session.add_email_handler,
            sms_config_and_retry_count_fn=sms_config_and_retry_count_fn,
            runtime=runtime,
        )
        if password_stage.result is not None:
            return password_stage
        resolved_callback = password_stage.resolved_callback
        phone_info = password_stage.phone_info or {}
        phone_debug = password_stage.phone_debug or {}
    return RegistrationCpaPasswordStageResult(
        resolved_callback=resolved_callback,
        phone_info=phone_info,
        phone_debug=phone_debug,
    )


def _resolve_registration_cpa_remaining_callback(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    cpa_session: RegistrationCpaOauthSession,
    resolved_callback: str,
    phone_info: dict[str, Any],
    phone_debug: dict[str, Any],
    sms_config_and_retry_count_fn: Callable[[], tuple[Any, int]],
    continue_with_optional_phone_verification_fn: Callable[..., tuple[str, dict[str, Any], dict[str, Any]]],
    resolve_oauth_callback_fn: Callable[[Any, str, str], str],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    log = deps.log_fn
    _registration_state_from_info = deps.registration_state_from_info_fn
    oauth_info = cpa_session.oauth_info
    expected_state = cpa_session.expected_state
    if not resolved_callback:
        try:
            sms_config, retry_count = sms_config_and_retry_count_fn()
            resolved_callback, phone_info, phone_debug = continue_with_optional_phone_verification_fn(
                registrar,
                oauth_info,
                expected_state,
                sms_config=sms_config,
                retry_count=retry_count,
                email=email,
            )
            if phone_debug.get("required"):
                log(
                    "注册后 CPA OAuth 手机验证处理："
                    f"provider={sms_config.provider} callback={'ready' if resolved_callback else 'missing'}"
                )
        except Exception as exc:
            log(f"注册后 CPA OAuth 手机验证失败：{exc}")
            raise
    if not resolved_callback:
        callback_source_info = phone_info or oauth_info
        callback_state = _registration_state_from_info(callback_source_info)
        if str(callback_state.get("kind") or "") == "add_email":
            resolved_callback = _continue_cpa_add_email_if_needed(
                cpa_session.add_email_handler,
                str(callback_state.get("url") or ""),
                source="phone",
            )
    if not resolved_callback:
        resolved_callback = resolve_oauth_callback_fn(
            registrar,
            str((phone_info or oauth_info).get("final_url") or oauth_info.get("final_url") or ""),
            expected_state,
        )
    return resolved_callback, phone_info, phone_debug


def _load_registration_cpa_oauth_runtime() -> RegistrationCpaOauthRuntime:
    from .oauth_token_flow import _continue_with_optional_add_email, _resolve_oauth_callback
    from .reauthorize import (
        _attempt_password_login,
        _build_reauthorize_sms_config,
        _continue_with_optional_phone_verification,
        _resolve_callback_step,
        _start_cpa_oauth,
        _step_requires_phone_verification,
        _submit_callback_to_cpa,
    )

    return RegistrationCpaOauthRuntime(
        attempt_password_login_fn=_attempt_password_login,
        build_reauthorize_sms_config_fn=_build_reauthorize_sms_config,
        continue_with_optional_add_email_fn=_continue_with_optional_add_email,
        continue_with_optional_phone_verification_fn=_continue_with_optional_phone_verification,
        resolve_callback_step_fn=_resolve_callback_step,
        resolve_oauth_callback_fn=_resolve_oauth_callback,
        start_cpa_oauth_fn=_start_cpa_oauth,
        step_requires_phone_verification_fn=_step_requires_phone_verification,
        submit_callback_to_cpa_fn=_submit_callback_to_cpa,
    )


def _registration_cpa_sms_config_loader(
    *,
    config: Any,
    parse_bool: Callable[..., bool],
    cpa_oauth_helpers_module: Any,
    runtime: RegistrationCpaOauthRuntime,
) -> Callable[[], tuple[Any, int]]:
    def _sms_config_and_retry_count() -> tuple[Any, int]:
        return cpa_oauth_helpers_module.build_cpa_sms_config_and_retry_count(
            config,
            build_reauthorize_sms_config_fn=runtime.build_reauthorize_sms_config_fn,
            parse_bool_fn=parse_bool,
        )

    return _sms_config_and_retry_count


def _registration_cpa_oauth_result_from_callback(
    deps: RegistrationTokenExchangeDeps,
    *,
    config: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    oauth_info: dict[str, Any],
    expected_state: str,
    resolved_callback: str,
    runtime: RegistrationCpaOauthRuntime,
) -> Any:
    if not resolved_callback:
        return _registration_failure_result(
            deps,
            email=email,
            password=password,
            mailbox=mailbox,
            callback_url=str(oauth_info.get("final_url") or ""),
            error="cpa_callback_not_reached",
        )
    return _cpa_callback_registration_result(
        deps,
        config=config,
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=resolved_callback,
        expected_state=expected_state,
        submit_callback_to_cpa_fn=runtime.submit_callback_to_cpa_fn,
    )


def _prepare_registration_cpa_oauth_exchange(
    deps: RegistrationTokenExchangeDeps,
    *,
    config: Any,
    registrar: Any,
    email: str,
    mailbox: dict[str, Any],
    parse_bool: Callable[..., bool],
) -> RegistrationCpaOauthExchangeContext:
    runtime = _load_registration_cpa_oauth_runtime()
    sms_config_and_retry_count_fn = _registration_cpa_sms_config_loader(
        config=config,
        parse_bool=parse_bool,
        cpa_oauth_helpers_module=deps.cpa_oauth_helpers_module,
        runtime=runtime,
    )
    cpa_session = _start_registration_cpa_oauth_session(
        deps,
        config=config,
        registrar=registrar,
        email=email,
        mailbox=mailbox,
        start_cpa_oauth_fn=runtime.start_cpa_oauth_fn,
        continue_with_optional_add_email_fn=runtime.continue_with_optional_add_email_fn,
        resolve_oauth_callback_fn=runtime.resolve_oauth_callback_fn,
        resolve_callback_step_fn=runtime.resolve_callback_step_fn,
    )
    return RegistrationCpaOauthExchangeContext(
        runtime=runtime,
        sms_config_and_retry_count_fn=sms_config_and_retry_count_fn,
        cpa_session=cpa_session,
    )


def _resolve_registration_cpa_oauth_exchange_callback(
    deps: RegistrationTokenExchangeDeps,
    *,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    context: RegistrationCpaOauthExchangeContext,
) -> RegistrationCpaOauthExchangeContext:
    runtime = context.runtime
    start_callback_stage = _resolve_registration_cpa_start_callback(
        deps,
        registrar=registrar,
        email=email,
        password=password,
        mailbox=mailbox,
        cpa_session=context.cpa_session,
        sms_config_and_retry_count_fn=context.sms_config_and_retry_count_fn,
        runtime=runtime,
    )
    if start_callback_stage.result is not None:
        return RegistrationCpaOauthExchangeContext(
            runtime=runtime,
            sms_config_and_retry_count_fn=context.sms_config_and_retry_count_fn,
            cpa_session=context.cpa_session,
            resolved_callback="",
            phone_info={},
            phone_debug={},
            early_result=start_callback_stage.result,
        )
    resolved_callback, phone_info, phone_debug = _resolve_registration_cpa_remaining_callback(
        deps,
        registrar=registrar,
        email=email,
        cpa_session=context.cpa_session,
        resolved_callback=start_callback_stage.resolved_callback,
        phone_info=start_callback_stage.phone_info or {},
        phone_debug=start_callback_stage.phone_debug or {},
        sms_config_and_retry_count_fn=context.sms_config_and_retry_count_fn,
        continue_with_optional_phone_verification_fn=runtime.continue_with_optional_phone_verification_fn,
        resolve_oauth_callback_fn=runtime.resolve_oauth_callback_fn,
    )
    return RegistrationCpaOauthExchangeContext(
        runtime=runtime,
        sms_config_and_retry_count_fn=context.sms_config_and_retry_count_fn,
        cpa_session=context.cpa_session,
        resolved_callback=resolved_callback,
        phone_info=phone_info,
        phone_debug=phone_debug,
    )


def _cpa_oauth_exchange_early_result(context: RegistrationCpaOauthExchangeContext) -> Any | None:
    return context.early_result


def _exchange_tokens_via_cpa_oauth(
    deps: RegistrationTokenExchangeDeps,
    *,
    config: Any,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    candidate_callback: str,
    parse_bool: Callable[..., bool],
) -> Any:
    log = deps.log_fn
    try:
        context = _prepare_registration_cpa_oauth_exchange(
            deps,
            config=config,
            registrar=registrar,
            email=email,
            mailbox=mailbox,
            parse_bool=parse_bool,
        )
        context = _resolve_registration_cpa_oauth_exchange_callback(
            deps,
            registrar=registrar,
            email=email,
            password=password,
            mailbox=mailbox,
            context=context,
        )
        early_result = _cpa_oauth_exchange_early_result(context)
        if early_result is not None:
            return early_result
        return _registration_cpa_oauth_result_from_callback(
            deps,
            config=config,
            email=email,
            password=password,
            mailbox=mailbox,
            oauth_info=context.cpa_session.oauth_info,
            expected_state=context.cpa_session.expected_state,
            resolved_callback=context.resolved_callback,
            runtime=context.runtime,
        )
    except Exception as exc:
        log(f"注册后 CPA OAuth 回调提交失败：{exc}")
        return _registration_failure_result(
            deps,
            email=email,
            password=password,
            mailbox=mailbox,
            callback_url=candidate_callback,
            error=str(exc or "cpa_callback_submit_failed"),
        )


def exchange_registered_account_tokens(
    *,
    config: Any,
    registrar: Any,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    code_verifier: str,
    callback_url: str,
    deps: RegistrationTokenExchangeDeps,
) -> Any:
    _brief_flow_url = deps.brief_flow_url_fn
    _follow_chatgpt_signup_callback = deps.follow_chatgpt_signup_callback_fn
    chatgpt_signup_redirect_uri = deps.chatgpt_signup_redirect_uri
    log = deps.log_fn
    parse_bool = deps.parse_bool_fn
    registration_cpa_oauth_helpers = deps.cpa_oauth_helpers_module
    candidate_callback = str(callback_url or "").strip()
    candidate_verifier = str(code_verifier or "").strip()
    if candidate_callback.startswith(chatgpt_signup_redirect_uri):
        follow_info = _follow_chatgpt_signup_callback(registrar, candidate_callback)
        if follow_info.get("followed"):
            log(
                "注册后 ChatGPT 回调已跟随："
                f"status={follow_info.get('status')} final_url={_brief_flow_url(str(follow_info.get('final_url') or ''))}"
            )

    if candidate_callback and not candidate_callback.startswith(chatgpt_signup_redirect_uri):
        token_result = registrar.exchange_platform_tokens(candidate_verifier, candidate_callback)
        if token_result.ok:
            return _attach_account_metadata(token_result, password=password, mailbox=mailbox)
        log(f"注册回调换 token 未成功，准备用当前登录会话重新打开 OAuth：{token_result.error}")
        if not hasattr(registrar, "start_authorize"):
            return token_result

    if registration_cpa_oauth_helpers.should_use_cpa_oauth_auto_import(config, parse_bool_fn=parse_bool):
        return _exchange_tokens_via_cpa_oauth(
            deps,
            config=config,
            registrar=registrar,
            email=email,
            password=password,
            mailbox=mailbox,
            candidate_callback=candidate_callback,
            parse_bool=parse_bool,
        )

    from .oauth_token_flow import _resolve_oauth_callback

    return _exchange_tokens_via_oauth_fallback(
        deps,
        registrar=registrar,
        email=email,
        password=password,
        mailbox=mailbox,
        resolve_oauth_callback_fn=_resolve_oauth_callback,
    )
