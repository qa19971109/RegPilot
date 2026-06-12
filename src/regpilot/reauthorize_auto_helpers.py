from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreparedBindEmail:
    email: str = ""
    prepared: bool = False
    error: str = ""


@dataclass(frozen=True)
class EmailOtpFallbackResult:
    callback_or_code: str = ""
    code: str = ""
    failure_message: str = ""
    manual_phone_verification_info: dict[str, Any] | None = None


def bind_mail_config_for_context(
    deps: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    proxy: str = "",
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
) -> dict[str, Any]:
    return deps.bind_mail_config_for_account_fn(
        account,
        mailbox,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )


def store_resolved_bind_email(mailbox: dict[str, Any], resolved_bind_email: str) -> None:
    bind_email = str(resolved_bind_email or "").strip()
    if not bind_email:
        return
    mailbox["bind_email"] = bind_email
    mailbox["email"] = bind_email


def continue_add_email_for_account(
    deps: Any,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    email: str,
    *,
    continue_url: str,
    proxy: str = "",
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
) -> tuple[str, str]:
    bind_mail_config = bind_mail_config_for_context(
        deps,
        account,
        mailbox,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )
    bind_email = deps.bind_email_hint_for_account_fn(account, mailbox, email)
    if not bind_email:
        prepared_email, prepared_mailbox = deps.prepare_bind_mailbox_fn(bind_mail_config, "")
        if prepared_mailbox:
            mailbox.update(prepared_mailbox)
        store_resolved_bind_email(mailbox, str(prepared_email or ""))
        bind_email = str(prepared_email or "").strip()
    add_email_url, resolved_bind_email = deps.continue_with_optional_add_email_fn(
        registrar,
        continue_url=str(continue_url or ""),
        bind_email=bind_email,
        bind_mail_config=bind_mail_config,
    )
    store_resolved_bind_email(mailbox, resolved_bind_email)
    return str(add_email_url or ""), str(resolved_bind_email or "").strip()


def prepare_about_you_bind_email(
    deps: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    email: str,
    *,
    proxy: str = "",
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
) -> PreparedBindEmail:
    bind_email = deps.bind_email_hint_for_account_fn(account, mailbox, email)
    if bind_email:
        return PreparedBindEmail(email=str(bind_email), prepared=False)
    try:
        prepared_email, prepared_mailbox = deps.prepare_bind_mailbox_fn(
            bind_mail_config_for_context(
                deps,
                account,
                mailbox,
                proxy=proxy,
                wait_timeout=wait_timeout,
                wait_interval=wait_interval,
                request_timeout=request_timeout,
            ),
            "",
        )
    except Exception as exc:
        return PreparedBindEmail(error=str(exc))
    if prepared_mailbox:
        mailbox.update(prepared_mailbox)
    store_resolved_bind_email(mailbox, str(prepared_email or ""))
    return PreparedBindEmail(email=str(prepared_email or "").strip(), prepared=bool(prepared_email))


def password_step_state(deps: Any, password_info: dict[str, Any]) -> dict[str, str]:
    return deps.registration_state_from_info_fn(
        {
            "final_url": deps.first_step_continue_url_fn(password_info),
            "json": password_info.get("json") if isinstance(password_info.get("json"), dict) else {},
            "text": str(password_info.get("text") or ""),
        }
    )


def _send_email_otp_if_needed(deps: Any, registrar: Any, password_info: dict[str, Any], state: str) -> dict[str, Any]:
    password_page = (password_info.get("json") or {}).get("page") if isinstance(password_info.get("json"), dict) else {}
    if isinstance(password_page, dict) and str(password_page.get("type") or "") == "email_otp_verification":
        return {"ok": True, "status": 0, "skipped": True, "reason": "password_verify_entered_email_otp_verification"}
    deps.log_stage_fn("发送登录邮箱验证码")
    return deps.send_login_otp_fn(registrar, state)


def _wait_for_email_otp_code(
    deps: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
) -> str:
    wait_mailbox = deps.mailbox_for_mail_wait_fn(account, mailbox)
    code = str(
        deps.wait_for_code_fn(
            deps.mail_wait_config_for_account_fn(
                account,
                mailbox,
                proxy=proxy,
                wait_timeout=wait_timeout,
                wait_interval=wait_interval,
                request_timeout=request_timeout,
            ),
            wait_mailbox,
        )
        or ""
    ).strip()
    deps.sync_mail_wait_state_fn(wait_mailbox, mailbox)
    return code


def _validate_email_otp_code(
    deps: Any,
    registrar: Any,
    debug: dict[str, Any],
    *,
    state: str,
    code: str,
) -> dict[str, Any]:
    validate_info = deps.validate_login_otp_fn(registrar, state, code)
    debug["validate_otp"] = validate_info
    debug["validate_otp_summary"] = deps.safe_response_summary_fn(validate_info)
    return validate_info


def _resolve_email_otp_callback_after_validation(
    deps: Any,
    registrar: Any,
    validate_info: dict[str, Any],
    debug: dict[str, Any],
    *,
    state: str,
) -> str:
    callback_or_code = deps.resolve_callback_step_fn(registrar, validate_info, state, allow_state_resume=False)
    tried_direct_consent = False
    if not callback_or_code:
        body = validate_info.get("json") or {}
        page = body.get("page") or {} if isinstance(body, dict) else {}
        consent_url = str(body.get("continue_url") or page.get("continue_url") or "").strip() if isinstance(body, dict) else ""
        if deps.is_consent_like_url_fn(consent_url):
            deps.log_stage_fn(f"邮箱验证码通过，进入授权确认页：{deps.short_url_fn(consent_url, 100)}")
            tried_direct_consent = True
            callback_or_code, consent_summary = deps.resolve_consent_callback_direct_fn(registrar, consent_url, state)
            debug["consent_direct_summary"] = consent_summary
            deps.log_stage_fn(f"授权确认页处理结果：回调{deps.ready_text_fn(callback_or_code)}，尝试次数={len((consent_summary or {}).get('attempts') or [])}")
    if not callback_or_code and not tried_direct_consent:
        callback_or_code = deps.resolve_callback_step_fn(registrar, validate_info, state, allow_state_resume=True)
    return str(callback_or_code or "")


def run_email_otp_callback_fallback(
    deps: Any,
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    password_info: dict[str, Any],
    debug: dict[str, Any],
    *,
    state: str,
    proxy: str = "",
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
) -> EmailOtpFallbackResult:
    log_stage = deps.log_stage_fn
    otp_info = _send_email_otp_if_needed(deps, registrar, password_info, state)
    debug["send_otp"] = otp_info
    log_stage(f"登录邮箱验证码发送结果：{deps.response_brief_fn(otp_info)}")
    if not otp_info.get("ok"):
        return EmailOtpFallbackResult(failure_message=f"send_otp_{otp_info.get('status') or 0}")

    log_stage("等待邮箱验证码")
    code = _wait_for_email_otp_code(
        deps,
        account,
        mailbox,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )
    log_stage(f"邮箱验证码接收结果：{'已收到' if code else '等待超时'}")
    if not code:
        return EmailOtpFallbackResult(failure_message="wait_for_code_timeout")

    log_stage("提交并校验邮箱验证码")
    validate_info = _validate_email_otp_code(deps, registrar, debug, state=state, code=code)
    log_stage(f"邮箱验证码校验结果：{deps.response_brief_fn(validate_info)}")
    if not validate_info.get("ok"):
        return EmailOtpFallbackResult(code=code, failure_message=f"validate_otp_{validate_info.get('status') or 0}")

    if deps.step_requires_phone_verification_fn(validate_info):
        deps.log_phone_required_after_email_otp_fn(validate_info, "登录邮箱验证码")
        return EmailOtpFallbackResult(code=code, manual_phone_verification_info=validate_info)

    log_stage("尝试解析邮箱验证码后的 OAuth 回调")
    callback_or_code = _resolve_email_otp_callback_after_validation(deps, registrar, validate_info, debug, state=state)
    log_stage(f"邮箱验证码后回调解析结果：回调{deps.ready_text_fn(callback_or_code)}")
    return EmailOtpFallbackResult(callback_or_code=str(callback_or_code or ""), code=code)


def probe_authorize_resume(
    deps: Any,
    registrar: Any,
    debug: dict[str, Any],
    *,
    auth_base: str,
    state: str,
) -> None:
    try:
        resume_url = f"{auth_base}/authorize/resume?state={state}" if state else f"{auth_base}/sign-in-with-chatgpt/codex/consent"
        resp = registrar.session.get(
            resume_url,
            headers={"accept": "application/json, text/html, */*", "referer": f"{auth_base}/log-in/email-verification?state={state}"},
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        resume_summary = deps.safe_response_summary_fn(
            {
                "ok": 200 <= int(resp.status_code or 0) < 400,
                "status": int(resp.status_code or 0),
                "json": deps.response_json_fn(resp),
                "text": resp.text[:2000],
                "location": str(resp.headers.get("Location") or ""),
                "final_url": str(getattr(resp, "url", resume_url) or resume_url),
            }
        )
        debug["resume_probe"] = resume_summary
        deps.log_stage_fn(f"授权恢复探测摘要：{resume_summary}")
    except Exception as exc:
        debug["resume_probe"] = {"error": str(exc)}
        deps.log_stage_fn(f"授权恢复探测失败：{exc}")
