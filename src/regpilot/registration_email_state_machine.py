from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class RegistrationEmailStateMachineDeps:
    accounts_error_code_fn: Callable[[dict[str, Any]], str]
    about_you_shape_log_summary_fn: Callable[[str], str]
    auth_base: str
    brief_flow_url_fn: Callable[[str], str]
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    exchange_registered_account_tokens_fn: Callable[..., Any]
    failed_registration_result_fn: Callable[..., Any]
    finalize_registration_result_fn: Callable[..., Any]
    load_registration_state_fn: Callable[[Any, str], dict[str, Any]]
    log_fn: Callable[[str], None]
    registration_continue_url_fn: Callable[[dict[str, Any]], str]
    registration_page_context_fn: Callable[[dict[str, Any]], str]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, str]]
    resolve_registration_post_create_url_fn: Callable[..., str]
    response_error_summary_fn: Callable[[str, dict[str, Any]], str]
    save_about_you_failure_artifacts_fn: Callable[..., dict[str, str]]
    save_about_you_presubmit_artifacts_fn: Callable[..., dict[str, str]]
    time_module: Any
    wait_email_otp_with_resend_fn: Callable[..., str]


@dataclass
class RegistrationStageOutcome:
    result: Any | None = None
    state: dict[str, Any] | None = None
    register_info: dict[str, Any] | None = None
    validate_info: dict[str, Any] | None = None
    create_start_done: bool | None = None
    register_submitted: bool | None = None
    otp_verified: bool | None = None


@dataclass
class RegistrationMachineRuntime:
    state: dict[str, Any]
    register_info: dict[str, Any] = field(default_factory=dict)
    validate_info: dict[str, Any] = field(default_factory=dict)
    create_start_done: bool = False
    register_submitted: bool = False
    otp_verified: bool = False
    seen: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass(frozen=True)
class AboutYouPageSnapshot:
    page_context: str
    pre_submit_snapshot: dict[str, Any]
    pre_submit_html: str


@dataclass(frozen=True)
class AboutYouCreateFailureHandling:
    create_info: dict[str, Any]
    callback_url: str
    outcome: RegistrationStageOutcome | None = None


def _handle_password_stage(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    state: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> RegistrationStageOutcome:
    auth_base = deps.auth_base
    register_info = registrar.register_user(email=email, password=password)
    deps.log_fn(f"注册提交结果：status={register_info.get('status')} ok={register_info.get('ok')}")
    if not register_info.get("ok"):
        error = deps.response_error_summary_fn("register_user", register_info)
        deps.log_fn(f"注册提交失败：{error}")
        return RegistrationStageOutcome(
            result=deps.failed_registration_result_fn(
                email=email,
                password=password,
                mailbox=mailbox,
                callback_url=deps.registration_continue_url_fn(register_info) or str(state.get("url") or ""),
                error=error,
            )
        )
    mailbox["_code_after_ts"] = int(deps.time_module.time() * 1000)
    otp_info = registrar.send_otp()
    deps.log_fn(f"邮箱验证码发送结果：status={otp_info.get('status')} ok={otp_info.get('ok')} final_url={otp_info.get('final_url')}")
    return RegistrationStageOutcome(
        state={
            "kind": "email_otp",
            "url": deps.registration_continue_url_fn(otp_info) or f"{auth_base}/email-verification",
            "page_type": "email_otp_verification",
        },
        register_info=register_info,
        register_submitted=True,
    )


def _handle_email_otp_stage(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    state: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> RegistrationStageOutcome:
    deps.log_fn("已进入邮箱验证页，等待邮箱验证码")
    code = deps.wait_email_otp_with_resend_fn(config, registrar, mailbox, resend_on_miss=True)
    if not code:
        return RegistrationStageOutcome(
            result=deps.failed_registration_result_fn(
                email=email,
                password=password,
                mailbox=mailbox,
                callback_url=str(state.get("url") or ""),
                error="wait_for_code_timeout",
            )
        )
    deps.log_fn(f"已收到邮箱验证码：{code}")
    code_meta = mailbox.get("_last_code_meta") or {}
    if code_meta:
        deps.log_fn(
            "verification code metadata: "
            f"provider={code_meta.get('provider') or ''} "
            f"message_id={code_meta.get('message_id') or ''} "
            f"received_at_ms={code_meta.get('received_at_ms') or 0}"
        )
    validate_info = registrar.validate_signup_otp(code)
    otp_verified = bool(validate_info.get("ok"))
    deps.log_fn(
        "邮箱验证码校验结果："
        f"status={validate_info.get('status')} ok={validate_info.get('ok')} "
        f"continue_url={((validate_info.get('json') or {}).get('continue_url') or '')}"
    )
    if not validate_info.get("ok"):
        validate_text = str(validate_info.get("text") or "").strip()
        validate_json = validate_info.get("json") or {}
        deps.log_fn(f"validate_signup_otp failure body: {validate_text[:500] or validate_json}")
        return RegistrationStageOutcome(
            result=deps.failed_registration_result_fn(
                email=email,
                password=password,
                mailbox=mailbox,
                callback_url=deps.registration_continue_url_fn(validate_info),
                error=f"validate_signup_otp_{validate_info.get('status')}",
            ),
            validate_info=validate_info,
            otp_verified=otp_verified,
        )
    next_state = deps.registration_state_from_info_fn(validate_info)
    if next_state.get("kind") == "continue" and next_state.get("url"):
        next_state = deps.load_registration_state_fn(registrar, str(next_state.get("url") or ""))
    return RegistrationStageOutcome(state=next_state, validate_info=validate_info, otp_verified=otp_verified)


def _prepare_about_you_page_snapshot(
    *,
    registrar: Any,
    state: dict[str, Any],
    validate_info: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> AboutYouPageSnapshot:
    auth_base = deps.auth_base
    log = deps.log_fn
    page_context = deps.registration_page_context_fn((state.get("raw") or {}) if isinstance(state.get("raw"), dict) else validate_info)
    pre_submit_snapshot: dict[str, Any] = {}
    try:
        pre_submit_state = deps.load_registration_state_fn(registrar, str(state.get("url") or f"{auth_base}/about-you"))
        pre_submit_snapshot = pre_submit_state.get("raw") if isinstance(pre_submit_state, dict) else {}
    except Exception as exc:
        pre_submit_snapshot = {"error": str(exc)}
    pre_submit_html = str(pre_submit_snapshot.get("text") or "").strip()
    if pre_submit_html:
        page_context = f"{page_context}\n{pre_submit_html}"
    log(f"about-you 页面识别：{deps.about_you_shape_log_summary_fn(page_context)}")
    try:
        pre_artifacts = deps.save_about_you_presubmit_artifacts_fn(
            state=state,
            page_snapshot=pre_submit_snapshot,
            page_context=page_context,
        )
        log(f"about-you 提交前页面：json={pre_artifacts.get('json_path') or ''} html={pre_artifacts.get('html_path') or ''}")
    except Exception as exc:
        log(f"about-you 提交前页面保存失败：{exc}")
    return AboutYouPageSnapshot(
        page_context=page_context,
        pre_submit_snapshot=pre_submit_snapshot,
        pre_submit_html=pre_submit_html,
    )


def _log_about_you_create_failure_response(
    create_info: dict[str, Any],
    error_code: str,
    deps: RegistrationEmailStateMachineDeps,
) -> None:
    log = deps.log_fn
    if error_code == "registration_disallowed":
        log("\u59d3\u540d\u5e74\u9f84/\u751f\u65e5\u63a5\u53e3\u5931\u8d25\u54cd\u5e94\uff1aregistration_disallowed\uff08\u4e0a\u6e38\u62d2\u7edd\u521b\u5efa\u8d26\u53f7\uff09")
        return
    failure_body = str(create_info.get("text") or create_info.get("json") or create_info.get("error") or "").strip()
    if failure_body:
        log(f"\u59d3\u540d\u5e74\u9f84/\u751f\u65e5\u63a5\u53e3\u5931\u8d25\u54cd\u5e94\uff1a{failure_body[:500]}")


def _save_about_you_failure_debug_artifacts(
    *,
    state: dict[str, Any],
    create_info: dict[str, Any],
    page_snapshot: AboutYouPageSnapshot,
    deps: RegistrationEmailStateMachineDeps,
) -> None:
    log = deps.log_fn
    try:
        artifacts = deps.save_about_you_failure_artifacts_fn(
            state=state,
            create_info=create_info,
            page_snapshot=page_snapshot.pre_submit_snapshot,
            page_context=page_snapshot.page_context,
        )
        log(f"about-you \u5931\u8d25\u8c03\u8bd5\u6587\u4ef6\uff1ajson={artifacts.get('json_path') or ''} html={artifacts.get('html_path') or ''}")
    except Exception as exc:
        log(f"about-you \u5931\u8d25\u8c03\u8bd5\u6587\u4ef6\u4fdd\u5b58\u5931\u8d25\uff1a{exc}")


def _registration_disallowed_about_you_failure(
    *,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    create_info: dict[str, Any],
    callback_url: str,
    deps: RegistrationEmailStateMachineDeps,
) -> AboutYouCreateFailureHandling:
    deps.log_fn("\u4e0a\u6e38\u62d2\u7edd\u521b\u5efa\u8d26\u53f7\uff1aregistration_disallowed\uff0c\u901a\u5e38\u662f\u5f53\u524d\u4ee3\u7406/\u73af\u5883/\u90ae\u7bb1\u98ce\u9669\u5bfc\u81f4\uff0c\u5df2\u505c\u6b62\u65e0\u6548\u8868\u5355\u515c\u5e95")
    return AboutYouCreateFailureHandling(
        create_info=create_info,
        callback_url=callback_url,
        outcome=RegistrationStageOutcome(
            result=deps.failed_registration_result_fn(
                email=email,
                password=password,
                mailbox=mailbox,
                callback_url=callback_url,
                error="registration_disallowed",
            )
        ),
    )


def _submit_about_you_form_after_create_failure(
    registrar: Any,
    *,
    full_name: str,
    birthdate: str,
    state: dict[str, Any],
    create_info: dict[str, Any],
    callback_url: str,
    page_snapshot: AboutYouPageSnapshot,
    deps: RegistrationEmailStateMachineDeps,
) -> tuple[dict[str, Any], str]:
    try:
        from .oauth_token_flow import _submit_about_you_form

        submitted_url, _ = _submit_about_you_form(
            registrar,
            page_url=str(state.get("url") or callback_url or f"{deps.auth_base}/about-you"),
            page_html=page_snapshot.pre_submit_html or str(create_info.get("text") or ""),
            full_name=full_name,
            birthdate=birthdate,
        )
        callback_url = str(submitted_url or callback_url).strip()
        deps.log_fn(f"\u59d3\u540d\u5e74\u9f84/\u751f\u65e5\u9875\u9762\u8868\u5355\u63d0\u4ea4\u7ed3\u679c\uff1afinal_url={callback_url}")
        if callback_url and "/about-you" not in callback_url:
            create_info = {**create_info, "ok": True, "location": callback_url}
    except Exception as exc:
        deps.log_fn(f"\u59d3\u540d\u5e74\u9f84/\u751f\u65e5\u9875\u9762\u8868\u5355\u63d0\u4ea4\u5931\u8d25\uff1a{exc}")
    return create_info, callback_url


def _handle_about_you_create_failure(
    *,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    state: dict[str, Any],
    create_info: dict[str, Any],
    callback_url: str,
    page_snapshot: AboutYouPageSnapshot,
    deps: RegistrationEmailStateMachineDeps,
) -> AboutYouCreateFailureHandling:
    error_code = deps.accounts_error_code_fn(create_info)
    _log_about_you_create_failure_response(create_info, error_code, deps)
    _save_about_you_failure_debug_artifacts(
        state=state,
        create_info=create_info,
        page_snapshot=page_snapshot,
        deps=deps,
    )
    if error_code == "registration_disallowed":
        return _registration_disallowed_about_you_failure(
            mailbox=mailbox,
            email=email,
            password=password,
            create_info=create_info,
            callback_url=callback_url,
            deps=deps,
        )
    create_info, callback_url = _submit_about_you_form_after_create_failure(
        registrar,
        full_name=full_name,
        birthdate=birthdate,
        state=state,
        create_info=create_info,
        callback_url=callback_url,
        page_snapshot=page_snapshot,
        deps=deps,
    )
    return AboutYouCreateFailureHandling(create_info=create_info, callback_url=callback_url)


def _create_about_you_account(
    registrar: Any,
    *,
    full_name: str,
    birthdate: str,
    state: dict[str, Any],
    page_context: str,
) -> dict[str, Any]:
    try:
        return registrar.create_account(full_name, birthdate, referer=str(state.get("url") or ""), page_context=page_context)
    except TypeError as exc:
        if "page_context" not in str(exc) and "unexpected keyword" not in str(exc):
            raise
        return registrar.create_account(full_name, birthdate, referer=str(state.get("url") or ""))


def _log_about_you_payload_attempts(create_info: dict[str, Any], deps: RegistrationEmailStateMachineDeps) -> None:
    payload_attempts = create_info.get("payload_attempts") or []
    if not payload_attempts:
        return
    attempt_summary = ", ".join(
        (
            f"{'+'.join(str(key) for key in (attempt.get('keys') or []) if key != 'name') or 'name'}:"
            f"{attempt.get('status')}"
            f"{('/' + str(attempt.get('error_code'))) if attempt.get('error_code') else ''}"
        )
        for attempt in payload_attempts[:6]
    )
    deps.log_fn(f"姓名年龄/生日提交尝试：{attempt_summary}")


def _post_about_you_next_state(
    registrar: Any,
    *,
    state: dict[str, Any],
    start_info: dict[str, Any],
    create_info: dict[str, Any],
    callback_url: str,
    deps: RegistrationEmailStateMachineDeps,
) -> dict[str, Any]:
    resolved_post_create_url = deps.resolve_registration_post_create_url_fn(
        registrar,
        start_info=start_info,
        create_info=create_info,
        fallback_url=callback_url,
    )
    if resolved_post_create_url and resolved_post_create_url != callback_url:
        deps.log_fn(f"about-you 后续回调/继续地址已解析：{deps.brief_flow_url_fn(resolved_post_create_url)}")
    callback_url = resolved_post_create_url or callback_url
    if deps.callback_params_from_url_fn(callback_url):
        next_state = {"kind": "callback", "url": callback_url, "page_type": ""}
    else:
        next_state = deps.registration_state_from_info_fn({**create_info, "final_url": callback_url})
    if next_state.get("kind") == "continue" and next_state.get("url"):
        next_state = deps.load_registration_state_fn(registrar, str(next_state.get("url") or ""))
    if next_state.get("kind") != "callback":
        next_state = {"kind": "callback", "url": str(next_state.get("url") or callback_url), "page_type": str(next_state.get("page_type") or "")}
    return next_state


def _handle_about_you_create_result(
    *,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    state: dict[str, Any],
    create_info: dict[str, Any],
    callback_url: str,
    page_snapshot: AboutYouPageSnapshot,
    deps: RegistrationEmailStateMachineDeps,
) -> tuple[dict[str, Any], str, RegistrationStageOutcome | None]:
    if create_info.get("ok"):
        return create_info, callback_url, None
    failure_handling = _handle_about_you_create_failure(
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        full_name=full_name,
        birthdate=birthdate,
        state=state,
        create_info=create_info,
        callback_url=callback_url,
        page_snapshot=page_snapshot,
        deps=deps,
    )
    if failure_handling.outcome is not None:
        return failure_handling.create_info, failure_handling.callback_url, failure_handling.outcome
    create_info = failure_handling.create_info
    callback_url = failure_handling.callback_url
    if create_info.get("ok"):
        return create_info, callback_url, None
    return create_info, callback_url, RegistrationStageOutcome(
        result=deps.failed_registration_result_fn(
            email=email,
            password=password,
            mailbox=mailbox,
            callback_url=callback_url,
            error=f"create_account_{create_info.get('status')}",
        )
    )


def _handle_about_you_stage(
    *,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    state: dict[str, Any],
    start_info: dict[str, Any],
    validate_info: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> RegistrationStageOutcome:
    log = deps.log_fn
    page_snapshot = _prepare_about_you_page_snapshot(
        registrar=registrar,
        state=state,
        validate_info=validate_info,
        deps=deps,
    )
    create_info = _create_about_you_account(
        registrar,
        full_name=full_name,
        birthdate=birthdate,
        state=state,
        page_context=page_snapshot.page_context,
    )
    _log_about_you_payload_attempts(create_info, deps)
    log(f"姓名年龄/生日提交结果：status={create_info.get('status')} ok={create_info.get('ok')} location={create_info.get('location')} final_url={create_info.get('final_url')}")
    callback_url = deps.registration_continue_url_fn(create_info) or str(state.get("url") or "")
    create_info, callback_url, failed_outcome = _handle_about_you_create_result(
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        full_name=full_name,
        birthdate=birthdate,
        state=state,
        create_info=create_info,
        callback_url=callback_url,
        page_snapshot=page_snapshot,
        deps=deps,
    )
    if failed_outcome is not None:
        return failed_outcome
    next_state = _post_about_you_next_state(
        registrar,
        state=state,
        start_info=start_info,
        create_info=create_info,
        callback_url=callback_url,
        deps=deps,
    )
    return RegistrationStageOutcome(state=next_state)


def _exchange_callback_state(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    start_info: dict[str, Any],
    state: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> Any:
    token_result = deps.exchange_registered_account_tokens_fn(
        config=config,
        registrar=registrar,
        email=email,
        password=password,
        mailbox=mailbox,
        code_verifier=str(start_info.get("code_verifier") or ""),
        callback_url=str(state.get("url") or ""),
    )
    return deps.finalize_registration_result_fn(config, registrar, token_result, email, mailbox)


def _handle_continue_or_unknown_stage(
    *,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    state: dict[str, Any],
    create_start_done: bool,
    register_submitted: bool,
    otp_verified: bool,
    deps: RegistrationEmailStateMachineDeps,
) -> RegistrationStageOutcome:
    current_url = str(state.get("url") or "")
    if current_url:
        next_state = deps.load_registration_state_fn(registrar, current_url)
        if next_state.get("kind") != "continue" or next_state.get("url") != current_url:
            return RegistrationStageOutcome(state=next_state)
    if not create_start_done:
        create_start = registrar.create_account_start(email)
        deps.log_fn(
            "邮箱注册入口初始化结果："
            f"status={create_start.get('status')} ok={create_start.get('ok')} final_url={create_start.get('final_url')}"
        )
        if not create_start.get("ok") and int(create_start.get("status") or 0) not in (400, 409, 422):
            error = deps.response_error_summary_fn("create_account_start", create_start)
            deps.log_fn(f"邮箱注册入口初始化失败：{error}")
            return RegistrationStageOutcome(
                result=deps.failed_registration_result_fn(
                    email=email,
                    password=password,
                    mailbox=mailbox,
                    callback_url=deps.registration_continue_url_fn(create_start) or current_url,
                    error=error,
                ),
                create_start_done=True,
            )
        next_state = deps.registration_state_from_info_fn(create_start)
        if next_state.get("kind") == "continue" and next_state.get("url"):
            next_state = deps.load_registration_state_fn(registrar, str(next_state.get("url") or ""))
        return RegistrationStageOutcome(state=next_state, create_start_done=True)
    if not register_submitted and not otp_verified:
        return RegistrationStageOutcome(
            state={
                "kind": "password",
                "url": f"{deps.auth_base}/create-account/password",
                "page_type": "create_account_password",
            }
        )
    return RegistrationStageOutcome()


def _failed_from_state(
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    state: dict[str, Any],
    error: str,
    deps: RegistrationEmailStateMachineDeps,
    callback_url: str | None = None,
) -> Any:
    return deps.failed_registration_result_fn(
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=str(callback_url if callback_url is not None else state.get("url") or ""),
        error=error,
    )


def _record_registration_state_visit(
    runtime: RegistrationMachineRuntime,
    step: int,
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> Any | None:
    state = runtime.state
    signature = (str(state.get("kind") or ""), str(state.get("url") or ""))
    runtime.seen[signature] = runtime.seen.get(signature, 0) + 1
    deps.log_fn(f"注册状态机：step={step} kind={state.get('kind') or '-'} page={state.get('page_type') or '-'} url={state.get('url') or '-'}")
    if runtime.seen[signature] <= 3:
        return None
    return _failed_from_state(
        email=email,
        password=password,
        mailbox=mailbox,
        state=state,
        error=f"registration_state_stuck:{state.get('kind') or 'unknown'}",
        deps=deps,
    )


def _apply_password_stage_outcome(
    runtime: RegistrationMachineRuntime,
    outcome: RegistrationStageOutcome,
) -> None:
    runtime.register_info = outcome.register_info or {}
    runtime.register_submitted = bool(outcome.register_submitted)
    runtime.state = outcome.state or runtime.state


def _apply_email_otp_stage_outcome(
    runtime: RegistrationMachineRuntime,
    outcome: RegistrationStageOutcome,
) -> None:
    runtime.validate_info = outcome.validate_info or {}
    runtime.otp_verified = bool(outcome.otp_verified)
    runtime.state = outcome.state or runtime.state


def _run_password_machine_step(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    runtime: RegistrationMachineRuntime,
    deps: RegistrationEmailStateMachineDeps,
) -> Any | None:
    if runtime.register_submitted:
        return _failed_from_state(email=email, password=password, mailbox=mailbox, state=runtime.state, error="password_stage_repeated", deps=deps)
    outcome = _handle_password_stage(
        config=config,
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        state=runtime.state,
        deps=deps,
    )
    if outcome.result is not None:
        return outcome.result
    _apply_password_stage_outcome(runtime, outcome)
    return None


def _run_email_otp_machine_step(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    runtime: RegistrationMachineRuntime,
    deps: RegistrationEmailStateMachineDeps,
) -> Any | None:
    outcome = _handle_email_otp_stage(
        config=config,
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        state=runtime.state,
        deps=deps,
    )
    if outcome.result is not None:
        return outcome.result
    _apply_email_otp_stage_outcome(runtime, outcome)
    return None


def _run_about_you_machine_step(
    *,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    start_info: dict[str, Any],
    runtime: RegistrationMachineRuntime,
    deps: RegistrationEmailStateMachineDeps,
) -> Any | None:
    outcome = _handle_about_you_stage(
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        full_name=full_name,
        birthdate=birthdate,
        state=runtime.state,
        start_info=start_info,
        validate_info=runtime.validate_info,
        deps=deps,
    )
    if outcome.result is not None:
        return outcome.result
    runtime.state = outcome.state or runtime.state
    return None


def _run_continue_machine_step(
    *,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    runtime: RegistrationMachineRuntime,
    deps: RegistrationEmailStateMachineDeps,
) -> Any | None:
    outcome = _handle_continue_or_unknown_stage(
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        state=runtime.state,
        create_start_done=runtime.create_start_done,
        register_submitted=runtime.register_submitted,
        otp_verified=runtime.otp_verified,
        deps=deps,
    )
    if outcome.result is not None:
        return outcome.result
    if outcome.create_start_done is not None:
        runtime.create_start_done = bool(outcome.create_start_done)
    if outcome.state is not None:
        runtime.state = outcome.state
        return None
    return _failed_from_state(
        email=email,
        password=password,
        mailbox=mailbox,
        state=runtime.state,
        error=f"unsupported_registration_state:{str(runtime.state.get('kind') or '') or 'unknown'}",
        deps=deps,
    )


def _run_registration_machine_step(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    start_info: dict[str, Any],
    runtime: RegistrationMachineRuntime,
    deps: RegistrationEmailStateMachineDeps,
) -> Any | None:
    kind = str(runtime.state.get("kind") or "")
    if kind == "error":
        url = str(runtime.state.get("url") or "")
        error = "authorize_hydra_invalid_request" if "authorize_hydra_invalid_request" in url else "authorize_failed"
        return _failed_from_state(email=email, password=password, mailbox=mailbox, state=runtime.state, error=error, deps=deps, callback_url=url)
    if kind == "password":
        return _run_password_machine_step(config=config, registrar=registrar, mailbox=mailbox, email=email, password=password, runtime=runtime, deps=deps)
    if kind == "email_otp":
        return _run_email_otp_machine_step(config=config, registrar=registrar, mailbox=mailbox, email=email, password=password, runtime=runtime, deps=deps)
    if kind == "about_you":
        return _run_about_you_machine_step(
            registrar=registrar,
            mailbox=mailbox,
            email=email,
            password=password,
            full_name=full_name,
            birthdate=birthdate,
            start_info=start_info,
            runtime=runtime,
            deps=deps,
        )
    if kind == "callback":
        return _exchange_callback_state(
            config=config,
            registrar=registrar,
            mailbox=mailbox,
            email=email,
            password=password,
            start_info=start_info,
            state=runtime.state,
            deps=deps,
        )
    if kind in {"continue", "unknown"}:
        return _run_continue_machine_step(registrar=registrar, mailbox=mailbox, email=email, password=password, runtime=runtime, deps=deps)
    return _failed_from_state(
        email=email,
        password=password,
        mailbox=mailbox,
        state=runtime.state,
        error=f"unsupported_registration_state:{kind or 'unknown'}",
        deps=deps,
    )


def run_email_registration_state_machine(
    *,
    config: Any,
    registrar: Any,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    start_info: dict[str, Any],
    deps: RegistrationEmailStateMachineDeps,
) -> Any:
    runtime = RegistrationMachineRuntime(state=deps.registration_state_from_info_fn(start_info))

    for step in range(1, 14):
        stuck_result = _record_registration_state_visit(runtime, step, email=email, password=password, mailbox=mailbox, deps=deps)
        if stuck_result is not None:
            return stuck_result
        result = _run_registration_machine_step(
            config=config,
            registrar=registrar,
            mailbox=mailbox,
            email=email,
            password=password,
            full_name=full_name,
            birthdate=birthdate,
            start_info=start_info,
            runtime=runtime,
            deps=deps,
        )
        if result is not None:
            return result

    return deps.failed_registration_result_fn(
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=deps.registration_continue_url_fn(runtime.validate_info or runtime.register_info or start_info),
        error="registration_state_machine_exhausted",
    )
