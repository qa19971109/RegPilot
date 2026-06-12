from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReauthorizePhoneVerificationDeps:
    auth_base: str
    har_browser_fetch_headers_fn: Callable[..., dict[str, str]]
    response_json_fn: Callable[[Any], dict[str, Any]]
    extract_callback_from_step_fn: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ReauthorizePhoneVerificationFlowDeps:
    auth_base: str
    step_requires_phone_verification_fn: Callable[[dict[str, Any]], bool]
    phone_verification_page_brief_fn: Callable[[dict[str, Any]], str]
    first_step_continue_url_fn: Callable[[dict[str, Any]], str]
    log_stage_fn: Callable[[str], None]
    response_brief_fn: Callable[[dict[str, Any]], str]
    acquire_or_reuse_phone_activation_fn: Callable[[Any], dict[str, Any]]
    send_add_phone_number_fn: Callable[..., dict[str, Any]]
    retire_phone_activation_fn: Callable[[Any, str, str], None]
    set_hero_sms_status_fn: Callable[[Any, str, int], Any]
    poll_sms_code_fn: Callable[..., str]
    validate_add_phone_otp_fn: Callable[..., dict[str, Any]]
    resolve_callback_step_fn: Callable[..., str]
    resolve_consent_callback_direct_fn: Callable[[Any, str, str], tuple[str, dict[str, Any]]]
    record_phone_activation_success_fn: Callable[..., dict[str, Any]]
    set_phone_activation_after_success_fn: Callable[[Any, str, dict[str, Any]], None]


@dataclass(frozen=True)
class PhoneVerificationActivation:
    activation: dict[str, Any]
    activation_id: str
    phone_number: str
    reused: bool


@dataclass(frozen=True)
class PhoneVerificationAttemptResult:
    callback: str
    info: dict[str, Any]
    activation: PhoneVerificationActivation


def continue_with_optional_phone_verification(
    registrar: Any,
    source_info: dict[str, Any],
    state: str,
    *,
    sms_config: Any,
    retry_count: int = 1,
    account_id: str = "",
    email: str = "",
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not _ensure_phone_verification_required(source_info, sms_config, deps):
        return "", source_info, {"required": False}

    attempts = max(1, int(retry_count or 1))
    last_info: dict[str, Any] = source_info
    debug: dict[str, Any] = {"required": True, "provider": sms_config.provider, "attempts": []}
    referer = deps.first_step_continue_url_fn(source_info) or f"{deps.auth_base}/add-phone"
    deps.log_stage_fn(f"开始手机二次验证，接码服务={sms_config.provider or '-'}，{deps.phone_verification_page_brief_fn(source_info)}")
    for attempt in range(1, attempts + 1):
        activation_id = ""
        phone_number = ""
        try:
            result = _run_phone_verification_attempt(
                registrar,
                sms_config,
                state,
                attempt=attempt,
                attempts=attempts,
                referer=referer,
                debug=debug,
                deps=deps,
            )
            activation_id = result.activation.activation_id
            phone_number = result.activation.phone_number
            last_info = result.info
            if not result.callback:
                continue
            _complete_phone_verification_success(
                sms_config,
                result.activation,
                account_id=account_id,
                email=email,
                debug=debug,
                deps=deps,
            )
            return result.callback, result.info, debug
        except Exception as exc:
            _record_phone_verification_exception(
                sms_config,
                attempt,
                attempts,
                phone_number,
                activation_id,
                exc,
                debug,
                deps,
            )
            if attempt >= attempts:
                raise
    return "", last_info, debug


def _ensure_phone_verification_required(
    source_info: dict[str, Any],
    sms_config: Any,
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> bool:
    if not deps.step_requires_phone_verification_fn(source_info):
        return False
    if not str(sms_config.api_key or "").strip():
        deps.log_stage_fn(f"需要手机二次验证，但未配置接码服务 API Key：{deps.phone_verification_page_brief_fn(source_info)}")
        raise RuntimeError("sms_api_key_required_for_phone_verification")
    return True


def _acquire_phone_verification_activation(
    sms_config: Any,
    attempt: int,
    attempts: int,
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> PhoneVerificationActivation:
    activation = deps.acquire_or_reuse_phone_activation_fn(sms_config)
    activation_id = str(activation.get("activation_id") or "").strip()
    phone_number = str(activation.get("phone_number") or "").strip()
    phone_price = str(activation.get("price") or "").strip()
    reused = str(activation.get("reused") or "") == "1"
    reuse_text = "复用" if reused else "新取"
    price_text = f"，价格 {phone_price}" if phone_price else ""
    deps.log_stage_fn(f"手机二次验证号码已获取：{reuse_text}号码 {phone_number}{price_text}，第 {attempt}/{attempts} 次")
    return PhoneVerificationActivation(activation=activation, activation_id=activation_id, phone_number=phone_number, reused=reused)


def _run_phone_verification_attempt(
    registrar: Any,
    sms_config: Any,
    state: str,
    *,
    attempt: int,
    attempts: int,
    referer: str,
    debug: dict[str, Any],
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> PhoneVerificationAttemptResult:
    activation = _acquire_phone_verification_activation(sms_config, attempt, attempts, deps)
    send_info = _send_phone_verification_number(
        registrar,
        activation,
        referer=referer,
        attempt=attempt,
        debug=debug,
        deps=deps,
    )
    if not send_info.get("ok"):
        _retire_failed_phone_verification_activation(
            sms_config,
            activation.activation_id,
            f"add_phone_send_{send_info.get('status') or 0}",
            deps,
        )
        return PhoneVerificationAttemptResult(callback="", info=send_info, activation=activation)

    validate_info = _poll_and_validate_phone_verification_code(
        registrar,
        sms_config,
        activation,
        send_info,
        referer=referer,
        deps=deps,
    )
    if not validate_info.get("ok"):
        _retire_failed_phone_verification_activation(
            sms_config,
            activation.activation_id,
            f"validate_phone_otp_{validate_info.get('status') or 0}",
            deps,
        )
        return PhoneVerificationAttemptResult(callback="", info=validate_info, activation=activation)

    callback = _resolve_phone_verification_callback(registrar, validate_info, state, debug, deps)
    if not callback:
        deps.log_stage_fn("手机二次验证已通过但未拿到 OAuth 回调，释放当前号码并重试")
        _retire_failed_phone_verification_activation(sms_config, activation.activation_id, "callback_missing_after_phone_otp", deps)
    return PhoneVerificationAttemptResult(callback=callback, info=validate_info, activation=activation)

def _send_phone_verification_number(
    registrar: Any,
    activation: PhoneVerificationActivation,
    *,
    referer: str,
    attempt: int,
    debug: dict[str, Any],
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> dict[str, Any]:
    send_info = deps.send_add_phone_number_fn(registrar, activation.phone_number, referer=referer)
    debug["attempts"].append(
        {
            "attempt": attempt,
            "phone": activation.phone_number,
            "activation_id": activation.activation_id,
            "reused": activation.reused,
            "reuse_count": activation.activation.get("reuse_count"),
            "max_uses": activation.activation.get("max_uses"),
            "send_status": send_info.get("status"),
            "send_ok": send_info.get("ok"),
        }
    )
    deps.log_stage_fn(f"手机二次验证短信发送结果：{deps.response_brief_fn(send_info)}")
    return send_info


def _poll_and_validate_phone_verification_code(
    registrar: Any,
    sms_config: Any,
    activation: PhoneVerificationActivation,
    send_info: dict[str, Any],
    *,
    referer: str,
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> dict[str, Any]:
    def _resend() -> None:
        resend_info = deps.send_add_phone_number_fn(
            registrar,
            activation.phone_number,
            referer=deps.first_step_continue_url_fn(send_info) or referer,
        )
        deps.log_stage_fn(f"手机二次验证短信重发结果：{deps.response_brief_fn(resend_info)}")
        if not resend_info.get("ok"):
            raise RuntimeError(f"add_phone_resend_{resend_info.get('status') or 0}")

    deps.log_stage_fn("等待手机二次验证短信验证码")
    sms_code = deps.poll_sms_code_fn(sms_config, activation.activation_id, on_resend=_resend, timeout_after_resend=60)
    deps.log_stage_fn(f"已收到手机二次验证短信验证码：{sms_code}")
    validate_info = deps.validate_add_phone_otp_fn(
        registrar,
        sms_code,
        referer=deps.first_step_continue_url_fn(send_info) or f"{deps.auth_base}/phone-verification",
    )
    deps.log_stage_fn(f"手机二次验证验证码校验结果：{deps.response_brief_fn(validate_info)}")
    return validate_info


def _resolve_phone_verification_callback(
    registrar: Any,
    validate_info: dict[str, Any],
    state: str,
    debug: dict[str, Any],
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> str:
    callback = deps.resolve_callback_step_fn(registrar, validate_info, state, allow_state_resume=False)
    if callback:
        return callback
    consent_url = _phone_verification_consent_url(validate_info)
    if consent_url:
        callback, consent_summary = deps.resolve_consent_callback_direct_fn(registrar, consent_url, state)
        debug["consent_after_phone"] = consent_summary
    return callback


def _phone_verification_consent_url(validate_info: dict[str, Any]) -> str:
    body = validate_info.get("json") or {}
    page = body.get("page") or {}
    return str(body.get("continue_url") or page.get("continue_url") or "").strip()


def _complete_phone_verification_success(
    sms_config: Any,
    activation: PhoneVerificationActivation,
    *,
    account_id: str,
    email: str,
    debug: dict[str, Any],
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> None:
    usage = deps.record_phone_activation_success_fn(
        sms_config,
        activation.activation_id,
        activation.phone_number,
        account_id=account_id,
        email=email,
    )
    deps.set_phone_activation_after_success_fn(sms_config, activation.activation_id, usage)
    debug["callback_ready"] = True
    debug["phone_number"] = activation.phone_number
    debug["activation_id"] = activation.activation_id
    debug["phone_reuse"] = usage
    deps.log_stage_fn("手机二次验证已通过，OAuth 回调已拿到")


def _retire_failed_phone_verification_activation(
    sms_config: Any,
    activation_id: str,
    reason: str,
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> None:
    deps.retire_phone_activation_fn(sms_config, activation_id, reason)
    deps.set_hero_sms_status_fn(sms_config, activation_id, 8)


def _record_phone_verification_exception(
    sms_config: Any,
    attempt: int,
    attempts: int,
    phone_number: str,
    activation_id: str,
    exc: Exception,
    debug: dict[str, Any],
    deps: ReauthorizePhoneVerificationFlowDeps,
) -> None:
    deps.log_stage_fn(f"手机二次验证第 {attempt}/{attempts} 次失败：{exc}")
    debug["attempts"].append({"attempt": attempt, "phone": phone_number, "activation_id": activation_id, "error": str(exc)})
    if activation_id:
        _retire_failed_phone_verification_activation(sms_config, activation_id, str(exc), deps)


def send_add_phone_number(
    registrar: Any,
    phone_number: str,
    *,
    referer: str = "",
    deps: ReauthorizePhoneVerificationDeps,
) -> dict[str, Any]:
    request_url = f"{deps.auth_base}/api/accounts/add-phone/send"
    headers = deps.har_browser_fetch_headers_fn("/add-phone")
    headers["referer"] = str(referer or f"{deps.auth_base}/add-phone")
    headers["oai-device-id"] = registrar.device_id
    try:
        resp = registrar.session.post(
            request_url,
            json={"phone_number": str(phone_number or "").strip()},
            headers=headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        body = deps.response_json_fn(resp)
        status = int(resp.status_code or 0)
        final_url = str(getattr(resp, "url", request_url) or request_url)
        return {
            "ok": status == 200,
            "status": status,
            "json": body,
            "text": str(getattr(resp, "text", "") or "")[:2000],
            "location": str(resp.headers.get("Location") or ""),
            "final_url": final_url,
            "referer": headers.get("referer") or "",
            "authorize": registrar.last_authorize,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": request_url,
            "referer": headers.get("referer") or "",
            "error": str(exc),
            "authorize": registrar.last_authorize,
        }


def validate_add_phone_otp(
    registrar: Any,
    code: str,
    *,
    referer: str = "",
    deps: ReauthorizePhoneVerificationDeps,
) -> dict[str, Any]:
    request_url = f"{deps.auth_base}/api/accounts/phone-otp/validate"
    headers = deps.har_browser_fetch_headers_fn("/phone-verification")
    headers["referer"] = str(referer or f"{deps.auth_base}/phone-verification")
    headers["oai-device-id"] = registrar.device_id
    try:
        resp = registrar.session.post(
            request_url,
            json={"code": str(code or "").strip()},
            headers=headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        body = deps.response_json_fn(resp)
        status = int(resp.status_code or 0)
        final_url = str(getattr(resp, "url", request_url) or request_url)
        return {
            "ok": 200 <= status < 300
            or bool(deps.extract_callback_from_step_fn({"json": body, "location": str(resp.headers.get("Location") or ""), "final_url": final_url})),
            "status": status,
            "json": body,
            "text": str(getattr(resp, "text", "") or "")[:2000],
            "location": str(resp.headers.get("Location") or ""),
            "final_url": final_url,
            "referer": headers.get("referer") or "",
            "authorize": registrar.last_authorize,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "json": {},
            "text": "",
            "location": "",
            "final_url": request_url,
            "referer": headers.get("referer") or "",
            "error": str(exc),
            "authorize": registrar.last_authorize,
        }
