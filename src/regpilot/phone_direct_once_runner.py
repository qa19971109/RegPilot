from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PhoneDirectOnceDeps:
    about_you_shape_log_summary_fn: Any
    accounts_error_code_fn: Any
    acquire_hero_sms_phone_fn: Any
    attach_phone_direct_exception_context_fn: Any
    bool_from_payload_fn: Any
    build_phone_account_exchange_config_fn: Any
    build_phone_direct_signup_flow_fn: Any
    continue_phone_signup_after_sms_fn: Any
    cpa_oauth_lock: Any
    environment_profile_context_fn: Any
    exchange_registered_account_tokens_fn: Any
    finish_phone_token_result_fn: Any
    import_result_to_codex2api_fn: Any
    load_continue_page_fn: Any
    mail_config_dict_from_payload_fn: Any
    phone_signup_continuation_deps_cls: Any
    phone_signup_entry_error_fn: Any
    phone_signup_probe_is_login_password_fn: Any
    platform_registrar_cls: Any
    poll_hero_sms_code_fn: Any
    prepare_environment_profile_from_payload_fn: Any
    probe_phone_signup_password_page_fn: Any
    random_birthdate_fn: Any
    random_name_fn: Any
    random_password_fn: Any
    record_phone_activation_attempt_fn: Any
    resolve_oauth_callback_fn: Any
    safe_register_failure_summary_fn: Any
    save_partial_hero_phone_bind_result_fn: Any
    save_result_fn: Any
    set_hero_sms_status_fn: Any
    sms_config_from_payload_fn: Any
    sms_retry_count_from_payload_fn: Any
    sms_retry_exhausted_message_fn: Any
    sms_wait_progress_message_fn: Any
    submit_about_you_form_fn: Any
    summarize_environment_profile_fn: Any


@dataclass(frozen=True)
class PhoneOtpStageResult:
    validate_info: dict[str, Any]
    phone_verified: bool
    activation_released: bool


@dataclass(frozen=True)
class PhoneSignupEntryStageResult:
    info: dict[str, Any]
    password_probe: dict[str, Any]
    login_password_page: bool


@dataclass(frozen=True)
class PhoneDirectAttemptSetup:
    phone_number: str
    phone_price: str
    activation_id: str
    password: str
    birthdate: Any
    full_name: str
    phone_flow: dict[str, Any]


@dataclass
class PhoneDirectAttemptStatus:
    phone_verified: bool = False
    activation_released: bool = False


@dataclass(frozen=True)
class PhoneDirectAttemptTokenContext:
    payload: dict[str, Any]
    effective_proxy: str
    hero_sms: Any
    codex2api_url: str
    codex2api_admin_key: str
    wants_codex2api: bool
    registrar: Any
    attempt_setup: PhoneDirectAttemptSetup
    attempted_phones: list[str]
    attempted_phone_prices: list[str]
    deps: PhoneDirectOnceDeps


@dataclass(frozen=True)
class PhoneDirectRuntime:
    payload: dict[str, Any]
    hero_sms: Any
    codex2api_url: str
    codex2api_admin_key: str
    wants_codex2api: bool
    env_profile: Any
    effective_proxy: str
    manage_environment: bool
    worker_index: int
    worker_total: int
    deps: PhoneDirectOnceDeps


@dataclass(frozen=True)
class PhoneDirectAttemptLoopState:
    attempted_phones: list[str]
    attempted_phone_prices: list[str]
    attempt_limit: int


def _exchange_phone_account_tokens_with_lock(
    *,
    payload: dict[str, Any],
    effective_proxy: str,
    hero_sms: Any,
    codex2api_url: str,
    codex2api_admin_key: str,
    wants_codex2api: bool,
    registrar: Any,
    phone_number: str,
    activation_id: str,
    password: str,
    callback_url: str = "",
    note: str = "",
    mail_config_dict_from_payload_fn: Any = None,
    build_phone_account_exchange_config_fn: Any = None,
    cpa_oauth_lock: Any = None,
    exchange_registered_account_tokens_fn: Any = None,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    mailbox = {
        "phone_number": phone_number,
        "phone_number_verified": True,
        "activation_id": activation_id,
        "bind_email": "",
    }
    if callback_url:
        mailbox["_signup_callback_url"] = callback_url
    mail_config = mail_config_dict_from_payload_fn(payload)
    exchange_config = build_phone_account_exchange_config_fn(
        payload=payload,
        effective_proxy=effective_proxy,
        hero_sms=hero_sms,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        wants_codex2api=wants_codex2api,
        mail_config=mail_config,
    )
    if note:
        print(note)
    print("阶段：等待 CPA OAuth 状态锁，避免并发授权 state 被覆盖")
    with cpa_oauth_lock:
        print("阶段：已进入 CPA OAuth 状态锁，开始获取并提交 CPA 回调")
        token_result = exchange_registered_account_tokens_fn(
            config=exchange_config,
            registrar=registrar,
            email=phone_number,
            password=password,
            mailbox=mailbox,
            code_verifier="",
            callback_url=callback_url,
        )
    return token_result, mail_config, mailbox


def _exchange_and_finish_phone_attempt_tokens(
    token_context: PhoneDirectAttemptTokenContext,
    callback_url: str = "",
    *,
    note: str = "",
) -> dict[str, Any]:
    setup = token_context.attempt_setup
    token_result, mail_config, mailbox = _exchange_phone_account_tokens_with_lock(
        payload=token_context.payload,
        effective_proxy=token_context.effective_proxy,
        hero_sms=token_context.hero_sms,
        codex2api_url=token_context.codex2api_url,
        codex2api_admin_key=token_context.codex2api_admin_key,
        wants_codex2api=token_context.wants_codex2api,
        registrar=token_context.registrar,
        phone_number=setup.phone_number,
        activation_id=setup.activation_id,
        password=setup.password,
        callback_url=callback_url,
        note=note,
        mail_config_dict_from_payload_fn=token_context.deps.mail_config_dict_from_payload_fn,
        build_phone_account_exchange_config_fn=token_context.deps.build_phone_account_exchange_config_fn,
        cpa_oauth_lock=token_context.deps.cpa_oauth_lock,
        exchange_registered_account_tokens_fn=token_context.deps.exchange_registered_account_tokens_fn,
    )
    return token_context.deps.finish_phone_token_result_fn(
        token_result=token_result,
        mailbox=mailbox,
        password=setup.password,
        phone_number=setup.phone_number,
        phone_price=setup.phone_price,
        attempted_phones=list(token_context.attempted_phones),
        attempted_phone_prices=list(token_context.attempted_phone_prices),
        phone_flow=setup.phone_flow,
        mail_config=mail_config,
        callback_url=callback_url,
        wants_codex2api=token_context.wants_codex2api,
        codex2api_url=token_context.codex2api_url,
        codex2api_admin_key=token_context.codex2api_admin_key,
        codex2api_proxy_url=str(token_context.payload.get("codex2api_proxy_url") or "").strip(),
        save_result_fn=token_context.deps.save_result_fn,
        import_result_fn=token_context.deps.import_result_to_codex2api_fn,
        save_partial_fn=token_context.deps.save_partial_hero_phone_bind_result_fn,
    )


def _prepare_phone_direct_attempt(
    *,
    payload: dict[str, Any],
    hero_sms: Any,
    attempt_index: int,
    attempt_limit: int,
    worker_index: int,
    worker_total: int,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    acquire_hero_sms_phone_fn: Any,
    record_phone_activation_attempt_fn: Any,
    random_password_fn: Any,
    random_name_fn: Any,
    random_birthdate_fn: Any,
    build_phone_direct_signup_flow_fn: Any,
    save_partial_hero_phone_bind_result_fn: Any,
) -> PhoneDirectAttemptSetup:
    activation = acquire_hero_sms_phone_fn(hero_sms)
    phone_number, phone_price, activation_id = record_phone_activation_attempt_fn(
        attempted_phones,
        attempted_phone_prices,
        activation,
    )
    password = str(payload.get("default_password") or "").strip() or random_password_fn()
    first_name, last_name = random_name_fn()
    birthdate = random_birthdate_fn()
    full_name = f"{first_name} {last_name}"
    phone_flow = build_phone_direct_signup_flow_fn(
        phone_number=phone_number,
        activation_id=activation_id,
        phone_price=phone_price,
        provider=hero_sms.provider,
    )
    attempt_text = f"（第 {attempt_index}/{attempt_limit} 次）" if hero_sms.auto_retry else ""
    price_text = f"，价格 {phone_price}" if phone_price else ""
    save_partial_hero_phone_bind_result_fn(phone_flow=phone_flow, password=password, note=f"已获取注册手机号{price_text}，准备开始主链{attempt_text}")
    worker_text = f"并发单元 {worker_index}/{worker_total} " if worker_index and worker_total else ""
    print(f"阶段：{worker_text}已获取注册手机号 {phone_number}{price_text}{attempt_text}（新取，仅本次直注使用）")
    print(f"阶段：本次账号密码：{password}")
    return PhoneDirectAttemptSetup(
        phone_number=phone_number,
        phone_price=phone_price,
        activation_id=activation_id,
        password=password,
        birthdate=birthdate,
        full_name=full_name,
        phone_flow=phone_flow,
    )


def _run_phone_signup_entry_stage(
    *,
    registrar: Any,
    phone_number: str,
    probe_phone_signup_password_page_fn: Any,
    phone_signup_entry_error_fn: Any,
    phone_signup_probe_is_login_password_fn: Any,
) -> PhoneSignupEntryStageResult:
    print("阶段：准备打开手机号注册页（网络请求最多约90秒，失败会自动换号）")
    info = registrar.start_phone_signup(phone_number)
    print(f"阶段：已打开手机号注册页（状态 {info.get('status')}）")
    password_probe = probe_phone_signup_password_page_fn(registrar, phone_number)
    print(
        "阶段：注册密码页检测 "
        f"状态码={password_probe.get('status') or '-'} 匹配={'是' if password_probe.get('matched') else '否'} 标题={password_probe.get('title') or '-'} 最终地址={password_probe.get('final_url')}"
    )
    create_start: dict[str, Any] = {}
    if not password_probe.get("matched"):
        create_start = registrar.create_account_start(phone_number)
        print(f"阶段：手机号注册入口初始化结果 状态码={create_start.get('status') or '-'} 成功={'是' if create_start.get('ok') else '否'}")
        password_probe = probe_phone_signup_password_page_fn(registrar, phone_number)
        print(
            "阶段：注册密码页二次检测 "
            f"状态码={password_probe.get('status') or '-'} 匹配={'是' if password_probe.get('matched') else '否'} 标题={password_probe.get('title') or '-'} 最终地址={password_probe.get('final_url')}"
        )
    if not password_probe.get("matched"):
        entry_error = phone_signup_entry_error_fn(
            password_probe.get("final_url"),
            password_probe.get("text"),
            create_start.get("final_url"),
            create_start.get("text"),
        )
        suffix = f": {entry_error}" if entry_error else ""
        raise RuntimeError(f"phone_signup_password_page_not_reached{suffix}")
    print("阶段：已进入手机号注册密码页")
    return PhoneSignupEntryStageResult(
        info=info,
        password_probe=password_probe,
        login_password_page=phone_signup_probe_is_login_password_fn(password_probe),
    )


def _run_phone_signup_otp_stage(
    *,
    registrar: Any,
    hero_sms: Any,
    activation_id: str,
    poll_hero_sms_code_fn: Any,
    set_hero_sms_status_fn: Any,
    sms_wait_progress_message_fn: Any,
) -> PhoneOtpStageResult:
    otp_info = registrar.send_phone_otp()
    print(f"阶段：已请求短信验证码（状态 {otp_info.get('status')}，{'成功' if otp_info.get('ok') else '失败'}）")
    if not otp_info.get("ok"):
        raise RuntimeError(f"send_phone_otp_{otp_info.get('status')}")
    print("阶段：等待短信验证码（30秒后自动重发，重发后最多等待60秒）")

    def _trigger_gpt_phone_resend() -> None:
        resend_info = registrar.resend_phone_otp()
        print(f"阶段：已重发短信验证码（HTTP {resend_info.get('status')}，业务{'成功' if resend_info.get('ok') else '失败'}）")
        if not resend_info.get("ok"):
            raise RuntimeError(f"resend_phone_otp_{resend_info.get('status')}")

    def _log_sms_wait_progress(info: dict[str, Any]) -> None:
        print(sms_wait_progress_message_fn(info))

    poll_kwargs: dict[str, Any] = {
        "on_resend": _trigger_gpt_phone_resend,
        "on_progress": _log_sms_wait_progress,
        "timeout_after_resend": 60,
    }
    sms_code = poll_hero_sms_code_fn(hero_sms, activation_id, **poll_kwargs)
    print(f"阶段：已收到短信验证码：{sms_code}")
    validate_info = registrar.validate_phone_signup_otp(sms_code)
    print(f"阶段：短信验证码校验结果（状态 {validate_info.get('status')}，{'成功' if validate_info.get('ok') else '失败'}）")
    if not validate_info.get("ok"):
        raise RuntimeError(f"validate_phone_signup_otp_{validate_info.get('status')}")
    set_hero_sms_status_fn(hero_sms, activation_id, 6)
    print("阶段：手机直注短信验证码已校验成功，已释放手机号")
    print("阶段：手机直注号码已完成使用，不加入复用池")
    return PhoneOtpStageResult(
        validate_info=validate_info,
        phone_verified=True,
        activation_released=True,
    )


def _handle_phone_direct_attempt_failure(
    exc: Exception,
    *,
    hero_sms: Any,
    activation_id: str,
    phone_verified: bool,
    activation_released: bool,
    set_hero_sms_status_fn: Any,
    attempt_index: int,
    attempt_limit: int,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    phone_number: str,
    phone_price: str,
    attach_phone_direct_exception_context_fn: Any,
    sms_retry_exhausted_message_fn: Any,
) -> str:
    last_error = str(exc)
    try:
        if activation_id and not phone_verified and not activation_released:
            set_hero_sms_status_fn(hero_sms, activation_id, 8)
    except Exception:
        pass
    if not hero_sms.auto_retry:
        print(f"阶段：当前号码流程失败，未开启自动重试，错误={last_error}")
        attach_phone_direct_exception_context_fn(
            exc,
            attempted_phones=attempted_phones,
            attempted_phone_prices=attempted_phone_prices,
            phone_number=phone_number,
            phone_price=phone_price,
        )
        raise exc
    if attempt_index >= attempt_limit:
        print(f"阶段：当前号码流程失败，已达到最大重试次数（{attempt_index}/{attempt_limit}），错误={last_error}")
        retry_error = RuntimeError(sms_retry_exhausted_message_fn(hero_sms.provider, attempt_limit, last_error))
        attach_phone_direct_exception_context_fn(
            retry_error,
            attempted_phones=attempted_phones,
            attempted_phone_prices=attempted_phone_prices,
            phone_number=phone_number,
            phone_price=phone_price,
        )
        raise retry_error
    print(f"阶段：当前号码流程失败，自动重试下一个号码（{attempt_index}/{attempt_limit}），错误={last_error}")
    return last_error


def _submit_phone_direct_registration(
    registrar: Any,
    phone_number: str,
    password: str,
    deps: PhoneDirectOnceDeps,
) -> dict[str, Any]:
    register_info = registrar.register_user(phone_number, password)
    if register_info.get("ok"):
        return register_info
    failure_summary = deps.safe_register_failure_summary_fn(register_info)
    print(f"阶段：注册提交失败 {failure_summary}")
    raise RuntimeError(f"register_user_{register_info.get('status')}: {failure_summary}")


def _continue_phone_direct_after_sms(
    registrar: Any,
    *,
    initial_info: dict[str, Any],
    validate_info: dict[str, Any],
    setup: PhoneDirectAttemptSetup,
    deps: PhoneDirectOnceDeps,
) -> dict[str, Any]:
    return deps.continue_phone_signup_after_sms_fn(
        registrar,
        initial_info=initial_info,
        validate_info=validate_info,
        full_name=setup.full_name,
        birthdate=setup.birthdate,
        phone_number=setup.phone_number,
        deps=deps.phone_signup_continuation_deps_cls(
            load_continue_page_fn=deps.load_continue_page_fn,
            about_you_shape_log_summary_fn=deps.about_you_shape_log_summary_fn,
            accounts_error_code_fn=deps.accounts_error_code_fn,
            submit_about_you_form_fn=deps.submit_about_you_form_fn,
            resolve_oauth_callback_fn=deps.resolve_oauth_callback_fn,
            log_fn=print,
        ),
    )


def _save_phone_direct_callback_ready(
    setup: PhoneDirectAttemptSetup,
    callback_url: str,
    deps: PhoneDirectOnceDeps,
) -> None:
    setup.phone_flow["callback"] = {"url": callback_url, "source": "resolved"}
    setup.phone_flow["stage"] = "callback_fetched"
    setup.phone_flow["status"] = "callback_ready"
    deps.save_partial_hero_phone_bind_result_fn(
        phone_flow=setup.phone_flow,
        password=setup.password,
        note="已拿到 OAuth 回调，准备可选绑邮箱与 token 交换",
    )
    print(f"阶段：已拿到 OAuth 回调 {callback_url}")


def _finish_phone_direct_continuation(
    token_context: PhoneDirectAttemptTokenContext,
    continuation: dict[str, Any],
) -> dict[str, Any]:
    if continuation.get("registration_disallowed"):
        return _exchange_and_finish_phone_attempt_tokens(
            token_context,
            "",
            note="阶段：手机号登录补跑开始，使用短信已验证手机号继续 CPA OAuth",
        )
    callback_url = str(continuation.get("callback_url") or "")
    if not callback_url:
        raise RuntimeError("callback_not_reached")
    _save_phone_direct_callback_ready(token_context.attempt_setup, callback_url, token_context.deps)
    print("阶段：手机号注册完成，开始重新打开 OAuth 获取平台 token/CPA 回调")
    return _exchange_and_finish_phone_attempt_tokens(token_context, callback_url)


def _phone_direct_token_context(
    *,
    payload: dict[str, Any],
    effective_proxy: str,
    hero_sms: Any,
    codex2api_url: str,
    codex2api_admin_key: str,
    wants_codex2api: bool,
    registrar: Any,
    attempt_setup: PhoneDirectAttemptSetup,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    deps: PhoneDirectOnceDeps,
) -> PhoneDirectAttemptTokenContext:
    return PhoneDirectAttemptTokenContext(
        payload=payload,
        effective_proxy=effective_proxy,
        hero_sms=hero_sms,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        wants_codex2api=wants_codex2api,
        registrar=registrar,
        attempt_setup=attempt_setup,
        attempted_phones=attempted_phones,
        attempted_phone_prices=attempted_phone_prices,
        deps=deps,
    )


def _finish_phone_direct_login_password_entry(token_context: PhoneDirectAttemptTokenContext) -> dict[str, Any]:
    print("阶段：手机号入口已进入登录密码页，改用手机号+密码继续 CPA OAuth")
    return _exchange_and_finish_phone_attempt_tokens(
        token_context,
        "",
        note="阶段：手机号登录补跑开始，使用当前手机号和本次密码继续授权",
    )


def _run_phone_direct_attempt_flow(
    *,
    payload: dict[str, Any],
    effective_proxy: str,
    hero_sms: Any,
    codex2api_url: str,
    codex2api_admin_key: str,
    wants_codex2api: bool,
    registrar: Any,
    attempt_setup: PhoneDirectAttemptSetup,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    status: PhoneDirectAttemptStatus,
    deps: PhoneDirectOnceDeps,
) -> dict[str, Any]:
    token_context = _phone_direct_token_context(
        payload=payload,
        effective_proxy=effective_proxy,
        hero_sms=hero_sms,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        wants_codex2api=wants_codex2api,
        registrar=registrar,
        attempt_setup=attempt_setup,
        attempted_phones=attempted_phones,
        attempted_phone_prices=attempted_phone_prices,
        deps=deps,
    )

    entry_stage = _run_phone_signup_entry_stage(
        registrar=registrar,
        phone_number=attempt_setup.phone_number,
        probe_phone_signup_password_page_fn=deps.probe_phone_signup_password_page_fn,
        phone_signup_entry_error_fn=deps.phone_signup_entry_error_fn,
        phone_signup_probe_is_login_password_fn=deps.phone_signup_probe_is_login_password_fn,
    )
    info = entry_stage.info

    if entry_stage.login_password_page:
        return _finish_phone_direct_login_password_entry(token_context)

    _submit_phone_direct_registration(registrar, attempt_setup.phone_number, attempt_setup.password, deps)
    otp_stage = _run_phone_signup_otp_stage(
        registrar=registrar,
        hero_sms=hero_sms,
        activation_id=attempt_setup.activation_id,
        poll_hero_sms_code_fn=deps.poll_hero_sms_code_fn,
        set_hero_sms_status_fn=deps.set_hero_sms_status_fn,
        sms_wait_progress_message_fn=deps.sms_wait_progress_message_fn,
    )
    validate_info = otp_stage.validate_info
    status.phone_verified = otp_stage.phone_verified
    status.activation_released = otp_stage.activation_released

    continuation = _continue_phone_direct_after_sms(
        registrar,
        initial_info=info,
        validate_info=validate_info,
        setup=attempt_setup,
        deps=deps,
    )
    return _finish_phone_direct_continuation(token_context, continuation)


def _phone_direct_runtime(
    payload: dict[str, Any],
    *,
    env_profile: Any,
    effective_proxy: str,
    manage_environment: bool,
    log_environment: bool,
    worker_index: int,
    worker_total: int,
    deps: PhoneDirectOnceDeps,
) -> PhoneDirectRuntime:
    hero_sms = deps.sms_config_from_payload_fn(payload)
    if not hero_sms.api_key:
        raise ValueError("sms_api_key_required")
    codex2api_url = str(payload.get("codex2api_url") or "").strip()
    codex2api_admin_key = str(payload.get("codex2api_admin_key") or "").strip()
    wants_codex2api = bool(deps.bool_from_payload_fn(payload, "codex2api_auto_import") and codex2api_url and codex2api_admin_key)
    env_profile = env_profile or deps.prepare_environment_profile_from_payload_fn(payload, fallback_proxy=str(payload.get("proxy") or ""))
    effective_proxy = str(effective_proxy or env_profile.proxy or payload.get("proxy") or "").strip()
    if log_environment:
        print(f"阶段：环境模块 {deps.summarize_environment_profile_fn(env_profile)}")
    return PhoneDirectRuntime(
        payload=payload,
        hero_sms=hero_sms,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        wants_codex2api=wants_codex2api,
        env_profile=env_profile,
        effective_proxy=effective_proxy,
        manage_environment=manage_environment,
        worker_index=worker_index,
        worker_total=worker_total,
        deps=deps,
    )


def _prepare_phone_direct_loop_state(runtime: PhoneDirectRuntime) -> PhoneDirectAttemptLoopState:
    return PhoneDirectAttemptLoopState(
        attempted_phones=[],
        attempted_phone_prices=[],
        attempt_limit=runtime.deps.sms_retry_count_from_payload_fn(runtime.payload, runtime.hero_sms.auto_retry),
    )


def _run_one_phone_direct_attempt(
    runtime: PhoneDirectRuntime,
    loop_state: PhoneDirectAttemptLoopState,
    attempt_index: int,
) -> tuple[dict[str, Any] | None, str]:
    deps = runtime.deps
    attempt_setup = _prepare_phone_direct_attempt(
        payload=runtime.payload,
        hero_sms=runtime.hero_sms,
        attempt_index=attempt_index,
        attempt_limit=loop_state.attempt_limit,
        worker_index=runtime.worker_index,
        worker_total=runtime.worker_total,
        attempted_phones=loop_state.attempted_phones,
        attempted_phone_prices=loop_state.attempted_phone_prices,
        acquire_hero_sms_phone_fn=deps.acquire_hero_sms_phone_fn,
        record_phone_activation_attempt_fn=deps.record_phone_activation_attempt_fn,
        random_password_fn=deps.random_password_fn,
        random_name_fn=deps.random_name_fn,
        random_birthdate_fn=deps.random_birthdate_fn,
        build_phone_direct_signup_flow_fn=deps.build_phone_direct_signup_flow_fn,
        save_partial_hero_phone_bind_result_fn=deps.save_partial_hero_phone_bind_result_fn,
    )
    attempt_status = PhoneDirectAttemptStatus()
    registrar = deps.platform_registrar_cls(runtime.effective_proxy)
    try:
        return _run_phone_direct_attempt_flow(
            payload=runtime.payload,
            effective_proxy=runtime.effective_proxy,
            hero_sms=runtime.hero_sms,
            codex2api_url=runtime.codex2api_url,
            codex2api_admin_key=runtime.codex2api_admin_key,
            wants_codex2api=runtime.wants_codex2api,
            registrar=registrar,
            attempt_setup=attempt_setup,
            attempted_phones=loop_state.attempted_phones,
            attempted_phone_prices=loop_state.attempted_phone_prices,
            status=attempt_status,
            deps=deps,
        ), ""
    except Exception as exc:
        return None, _handle_phone_direct_attempt_failure(
            exc,
            hero_sms=runtime.hero_sms,
            activation_id=attempt_setup.activation_id,
            phone_verified=attempt_status.phone_verified,
            activation_released=attempt_status.activation_released,
            set_hero_sms_status_fn=deps.set_hero_sms_status_fn,
            attempt_index=attempt_index,
            attempt_limit=loop_state.attempt_limit,
            attempted_phones=loop_state.attempted_phones,
            attempted_phone_prices=loop_state.attempted_phone_prices,
            phone_number=attempt_setup.phone_number,
            phone_price=attempt_setup.phone_price,
            attach_phone_direct_exception_context_fn=deps.attach_phone_direct_exception_context_fn,
            sms_retry_exhausted_message_fn=deps.sms_retry_exhausted_message_fn,
        )
    finally:
        registrar.close()


def _run_phone_direct_attempt_loop(runtime: PhoneDirectRuntime) -> dict[str, Any]:
    loop_state = _prepare_phone_direct_loop_state(runtime)
    last_error = ""
    for attempt_index in range(1, loop_state.attempt_limit + 1):
        result, last_error = _run_one_phone_direct_attempt(runtime, loop_state, attempt_index)
        if result is not None:
            return result
    raise RuntimeError(last_error or "phone_direct_failed")


def run_phone_direct_once(
    payload: dict[str, Any],
    *,
    env_profile: Any = None,
    effective_proxy: str = "",
    manage_environment: bool = True,
    log_environment: bool = True,
    worker_index: int = 0,
    worker_total: int = 0,
    deps: PhoneDirectOnceDeps,
) -> dict[str, Any]:
    runtime = _phone_direct_runtime(
        payload,
        env_profile=env_profile,
        effective_proxy=effective_proxy,
        manage_environment=manage_environment,
        log_environment=log_environment,
        worker_index=worker_index,
        worker_total=worker_total,
        deps=deps,
    )
    env_context = runtime.deps.environment_profile_context_fn(runtime.env_profile) if runtime.manage_environment else nullcontext()
    with env_context:
        return _run_phone_direct_attempt_loop(runtime)
