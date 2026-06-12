from __future__ import annotations

import time
import re
import json
import requests
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .accounts_store import get_account, save_registration_result_to_account, upsert_account
from .config import DATA_DIR, RegisterConfig
from .register_core import PlatformRegistrar, RegistrationResult, auth_base, wait_for_code, extract_oauth_callback_params_from_consent_session, _make_trace_headers
from .jwt_utils import decode_jwt_payload as _decode_jwt_payload
from .registration_about_you import about_you_shape_log_summary as _about_you_shape_log_summary
from .registration_callback import extract_oauth_callback_params_from_url
from .registration_environment import get_common_headers
from .registration_responses import accounts_error_code as _accounts_error_code, response_json as _response_json
from .registration_sentinel import build_sentinel_token
from .registration_state import registration_state_from_info as _registration_state_from_info
from .registration_identity import random_birthdate as _random_birthdate, random_name as _random_name
from . import reauthorize_cpa_oauth
from . import reauthorize_consent_callback
from . import reauthorize_email_otp_flow
from . import reauthorize_mail_config
from . import reauthorize_about_you_flow
from . import reauthorize_auto_flow
from . import reauthorize_local_token_flow
from . import reauthorize_local_tokens
from . import reauthorize_password_login
from . import reauthorize_phone_pool
from . import reauthorize_phone_verification
from . import reauthorize_result_store
from .oauth_token_flow import (
    HeroSMSConfig,
    Sub2APIOAuthFlowConfig,
    acquire_hero_sms_phone,
    build_openai_oauth_authorize_url,
    exchange_callback_code,
    import_result_to_codex2api,
    poll_hero_sms_code,
    set_hero_sms_status,
    _request_codex2api_json,
    _normalize_sub2api_origin,
    _resolve_oauth_callback,
    _continue_with_optional_add_email,
    _load_continue_page,
    _prepare_bind_mailbox,
    _submit_about_you_form,
    _extract_form_inputs,
    _post_form_and_follow,
)
from .sms_provider_config import build_sms_config_from_values


PHONE_REUSE_LIMIT = 3
PHONE_REUSE_POOL_NAME = "phone_reuse_pool.json"


@dataclass
class ReauthorizeStartOutcome:
    ok: bool
    message: str = ""
    account: dict[str, Any] | None = None
    authorize_url: str = ""
    state: str = ""
    nonce: str = ""
    redirect_uri: str = ""
    client_id: str = ""
    code_verifier: str = ""
    bind_email: str = ""


@dataclass
class ReauthorizeFinishOutcome:
    ok: bool
    message: str = ""
    account: dict[str, Any] | None = None
    callback_url: str = ""
    codex2api_import_submit_ok: bool = False
    codex2api_import_submit_message: str = ""


def _har_browser_fetch_headers(referer_path: str, *, accept: str = "application/json", content_type: str = "application/json") -> dict[str, str]:
    referer = referer_path if referer_path.startswith("http") else f"{auth_base}{referer_path}"
    headers = get_common_headers()
    headers["accept"] = accept
    headers["accept-language"] = "zh-CN,zh;q=0.9"
    headers["referer"] = referer
    headers["sec-ch-ua"] = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'
    headers.update(_make_trace_headers())
    if content_type:
        headers["content-type"] = content_type
    else:
        headers.pop("content-type", None)
    return headers


def _reauthorize_email_otp_deps() -> reauthorize_email_otp_flow.ReauthorizeEmailOtpDeps:
    return reauthorize_email_otp_flow.ReauthorizeEmailOtpDeps(
        auth_base=auth_base,
        har_browser_fetch_headers_fn=_har_browser_fetch_headers,
        response_json_fn=_response_json,
        merge_url_query_fn=_merge_url_query,
        safe_response_summary_fn=_safe_response_summary,
        extract_email_otp_form_inputs_fn=_extract_email_otp_form_inputs,
        post_form_and_follow_fn=_post_form_and_follow,
        build_sentinel_token_fn=build_sentinel_token,
        extract_callback_from_step_fn=_extract_callback_from_step,
    )


def _reauthorize_phone_verification_deps() -> reauthorize_phone_verification.ReauthorizePhoneVerificationDeps:
    return reauthorize_phone_verification.ReauthorizePhoneVerificationDeps(
        auth_base=auth_base,
        har_browser_fetch_headers_fn=_har_browser_fetch_headers,
        response_json_fn=_response_json,
        extract_callback_from_step_fn=_extract_callback_from_step,
    )


def _reauthorize_phone_verification_flow_deps() -> reauthorize_phone_verification.ReauthorizePhoneVerificationFlowDeps:
    return reauthorize_phone_verification.ReauthorizePhoneVerificationFlowDeps(
        auth_base=auth_base,
        step_requires_phone_verification_fn=_step_requires_phone_verification,
        phone_verification_page_brief_fn=_phone_verification_page_brief,
        first_step_continue_url_fn=_first_step_continue_url,
        log_stage_fn=_log_stage,
        response_brief_fn=_response_brief,
        acquire_or_reuse_phone_activation_fn=_acquire_or_reuse_phone_activation,
        send_add_phone_number_fn=_send_add_phone_number,
        retire_phone_activation_fn=_retire_phone_activation,
        set_hero_sms_status_fn=set_hero_sms_status,
        poll_sms_code_fn=poll_hero_sms_code,
        validate_add_phone_otp_fn=_validate_add_phone_otp,
        resolve_callback_step_fn=_resolve_callback_step,
        resolve_consent_callback_direct_fn=_resolve_consent_callback_direct,
        record_phone_activation_success_fn=_record_phone_activation_success,
        set_phone_activation_after_success_fn=_set_phone_activation_after_success,
    )


def _reauthorize_consent_callback_deps() -> reauthorize_consent_callback.ReauthorizeConsentCallbackDeps:
    return reauthorize_consent_callback.ReauthorizeConsentCallbackDeps(
        auth_base=auth_base,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        consent_session_callback_fn=extract_oauth_callback_params_from_consent_session,
        merge_url_query_fn=_merge_url_query,
        response_json_fn=_response_json,
        safe_response_summary_fn=_safe_response_summary,
        extract_form_inputs_fn=_extract_form_inputs,
    )


def _reauthorize_password_login_deps() -> reauthorize_password_login.ReauthorizePasswordLoginDeps:
    return reauthorize_password_login.ReauthorizePasswordLoginDeps(
        auth_base=auth_base,
        har_browser_fetch_headers_fn=_har_browser_fetch_headers,
        response_json_fn=_response_json,
        first_callbackish_url_fn=_first_callbackish_url,
        merge_url_query_fn=_merge_url_query,
    )


def _reauthorize_about_you_deps() -> reauthorize_about_you_flow.ReauthorizeAboutYouDeps:
    return reauthorize_about_you_flow.ReauthorizeAboutYouDeps(
        auth_base=auth_base,
        load_continue_page_fn=_load_continue_page,
        safe_response_summary_fn=_safe_response_summary,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        registration_state_from_info_fn=_registration_state_from_info,
        random_name_fn=_random_name,
        random_birthdate_fn=_random_birthdate,
        log_stage_fn=_log_stage,
        about_you_shape_log_summary_fn=_about_you_shape_log_summary,
        accounts_error_code_fn=_accounts_error_code,
        submit_about_you_form_fn=_submit_about_you_form,
        short_url_fn=_short_url,
        zh_bool_fn=_zh_bool,
        resolve_callback_step_fn=_resolve_callback_step,
        resolve_oauth_callback_fn=_resolve_oauth_callback,
    )


def _reauthorize_local_token_deps() -> reauthorize_local_tokens.ReauthorizeLocalTokenDeps:
    return reauthorize_local_tokens.ReauthorizeLocalTokenDeps(
        auth_base=auth_base,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        registration_result_cls=RegistrationResult,
        platform_registrar_cls=PlatformRegistrar,
        response_json_fn=_response_json,
        decode_jwt_payload_fn=_decode_jwt_payload,
    )


def _reauthorize_local_token_flow_deps() -> reauthorize_local_token_flow.ReauthorizeLocalTokenFlowDeps:
    return reauthorize_local_token_flow.ReauthorizeLocalTokenFlowDeps(
        attempt_password_login_fn=_attempt_password_login,
        build_openai_oauth_authorize_url_fn=build_openai_oauth_authorize_url,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        common_headers_fn=get_common_headers,
        compact_consent_debug_summary_fn=_compact_consent_debug_summary,
        direct_exchange_local_callback_fn=_direct_exchange_local_callback,
        exchange_callback_code_fn=exchange_callback_code,
        first_step_continue_url_fn=_first_step_continue_url,
        handle_email_otp_step_fn=_handle_email_otp_step,
        log_stage_fn=_log_stage,
        oauth_flow_config_cls=Sub2APIOAuthFlowConfig,
        ready_text_fn=_ready_text,
        registration_result_cls=RegistrationResult,
        registration_state_from_info_fn=_registration_state_from_info,
        resolve_callback_step_fn=_resolve_callback_step,
        resolve_consent_callback_direct_fn=_resolve_consent_callback_direct,
        resolve_oauth_callback_fn=_resolve_oauth_callback,
        response_brief_fn=_response_brief,
        response_json_fn=_response_json,
        short_url_fn=_short_url,
        zh_bool_fn=_zh_bool,
    )


@dataclass
class ReauthorizeAutoOutcome:
    ok: bool
    message: str = ""
    account: dict[str, Any] | None = None
    callback_url: str = ""
    code: str = ""
    codex2api_import_submit_ok: bool = False
    codex2api_import_submit_message: str = ""
    debug: dict[str, Any] | None = None


def _reauthorize_result_store_deps() -> reauthorize_result_store.ReauthorizeResultStoreDeps:
    return reauthorize_result_store.ReauthorizeResultStoreDeps(
        auto_outcome_cls=ReauthorizeAutoOutcome,
        finish_outcome_cls=ReauthorizeFinishOutcome,
        import_result_to_codex2api_fn=import_result_to_codex2api,
        log_stage_fn=_log_stage,
        now_text_fn=_now_text,
        save_registration_result_to_account_fn=save_registration_result_to_account,
        upsert_account_fn=upsert_account,
    )


def _reauthorize_auto_flow_deps() -> reauthorize_auto_flow.ReauthorizeAutoFlowDeps:
    return reauthorize_auto_flow.ReauthorizeAutoFlowDeps(
        auth_base=auth_base,
        auto_outcome_cls=ReauthorizeAutoOutcome,
        attempt_password_login_fn=_attempt_password_login,
        bind_email_hint_for_account_fn=_bind_email_hint_for_account,
        bind_mail_config_for_account_fn=_bind_mail_config_for_account,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        continue_with_optional_about_you_fn=_continue_with_optional_about_you,
        continue_with_optional_add_email_fn=_continue_with_optional_add_email,
        fail_manual_phone_verification_required_fn=_fail_manual_phone_verification_required,
        finalize_cpa_submit_with_optional_local_tokens_fn=_finalize_cpa_submit_with_optional_local_tokens,
        first_step_continue_url_fn=_first_step_continue_url,
        get_account_fn=get_account,
        handle_email_otp_step_fn=_handle_email_otp_step,
        is_consent_like_url_fn=_is_consent_like_url,
        log_phone_required_after_email_otp_fn=_log_phone_required_after_email_otp,
        log_stage_fn=_log_stage,
        login_identifier_for_account_fn=_login_identifier_for_account,
        login_username_payload_fn=_login_username_payload,
        mail_wait_config_for_account_fn=_mail_wait_config_for_account,
        mailbox_for_mail_wait_fn=_mailbox_for_mail_wait,
        mark_reauthorize_failed_fn=_mark_reauthorize_failed,
        phone_verification_page_brief_fn=_phone_verification_page_brief,
        platform_registrar_cls=PlatformRegistrar,
        prepare_bind_mailbox_fn=_prepare_bind_mailbox,
        proxy_text_fn=_proxy_text,
        ready_text_fn=_ready_text,
        registration_state_from_info_fn=_registration_state_from_info,
        resolve_callback_step_fn=_resolve_callback_step,
        resolve_consent_callback_direct_fn=_resolve_consent_callback_direct,
        response_brief_fn=_response_brief,
        response_json_fn=_response_json,
        safe_response_summary_fn=_safe_response_summary,
        send_login_otp_fn=_send_login_otp,
        short_url_fn=_short_url,
        start_cpa_oauth_fn=_start_cpa_oauth,
        step_requires_phone_verification_fn=_step_requires_phone_verification,
        submit_callback_to_cpa_fn=_submit_callback_to_cpa,
        sync_mail_wait_state_fn=_sync_mail_wait_state,
        time_module=time,
        upsert_account_fn=upsert_account,
        validate_login_otp_fn=_validate_login_otp,
        wait_for_code_fn=wait_for_code,
        zh_bool_fn=_zh_bool,
    )

def _zh_bool(value: Any) -> str:
    return "是" if bool(value) else "否"


def _ready_text(value: Any) -> str:
    return "已拿到" if bool(value) else "未拿到"


def _proxy_text(value: Any) -> str:
    text = str(value or "").strip()
    return text or "直连"


def _log_stage(message: str) -> None:
    print(f"阶段：{message}")


def _short_url(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit] or "-"


def _response_brief(info: dict[str, Any], *, include_final: bool = True) -> str:
    parts = [
        f"状态码={info.get('status') if info.get('status') not in (None, '') else '-'}",
        f"成功={_zh_bool(info.get('ok'))}",
    ]
    page = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = page.get("page") if isinstance(page.get("page"), dict) else {}
    page_type = str(page.get("type") or info.get("page_type") or "").strip()
    if page_type:
        parts.append(f"页面类型={page_type}")
    if include_final:
        parts.append(f"最终地址={_short_url(info.get('final_url'))}")
    return "，".join(parts)


def _phone_verification_page_brief(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    page_type = str(page.get("type") or body.get("page_type") or info.get("page_type") or "").strip() or "-"
    continue_url = _first_step_continue_url(info) or "-"
    final_url = str(info.get("final_url") or "").strip() or "-"
    return f"页面类型={page_type}，继续地址={_short_url(continue_url)}，最终地址={_short_url(final_url)}"


def _log_phone_required_after_email_otp(info: dict[str, Any], source: str) -> None:
    _log_stage(f"{source}：邮箱验证码已通过，OpenAI 要求继续手机二次验证")
    _log_stage(f"手机二次验证页面：{_phone_verification_page_brief(info)}")


def _log_phone_verification_not_configured(info: dict[str, Any]) -> None:
    _log_stage(f"需要手机二次验证，但未配置接码服务或未允许手机验证：{_phone_verification_page_brief(info)}")


def _fail_manual_phone_verification_required(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    debug: dict[str, Any],
    info: dict[str, Any],
    *,
    code: str = "",
) -> ReauthorizeAutoOutcome:
    message = "manual_phone_verification_required"
    debug["manual_phone_verification_required"] = {
        "page": _phone_verification_page_brief(info),
        "reason": "OpenAI 要求账号原手机号二次验证，重新授权不再使用接码服务自动处理",
    }
    _log_stage("无法继续自动授权：OpenAI 要求手机二次验证，需要人工使用账号原手机号完成验证")
    _log_stage(f"手机二次验证页面：{_phone_verification_page_brief(info)}")
    updated = _mark_reauthorize_failed(account, message, mailbox=mailbox)
    return ReauthorizeAutoOutcome(ok=False, message=message, account=updated, code=code, debug=debug)


def _reauthorize_cpa_oauth_deps() -> reauthorize_cpa_oauth.ReauthorizeCpaOAuthDeps:
    return reauthorize_cpa_oauth.ReauthorizeCpaOAuthDeps(
        callback_params_from_url=extract_oauth_callback_params_from_url,
        normalize_origin=_normalize_sub2api_origin,
        request_codex2api_json=_request_codex2api_json,
        requests_get=requests.get,
        requests_post=requests.post,
    )


def _submit_callback_to_cpa(
    callback_or_code: str,
    *,
    cpa_url: str,
    cpa_management_key: str,
    expected_state: str = "",
) -> dict[str, Any]:
    return reauthorize_cpa_oauth.submit_callback_to_cpa(
        callback_or_code,
        cpa_url=cpa_url,
        cpa_management_key=cpa_management_key,
        expected_state=expected_state,
        deps=_reauthorize_cpa_oauth_deps(),
    )


def _exchange_callback_with_codex2api(
    callback_or_code: str,
    *,
    codex2api_url: str,
    codex2api_admin_key: str,
    session_id: str,
    expected_state: str = "",
) -> dict[str, Any]:
    return reauthorize_cpa_oauth.exchange_callback_with_codex2api(
        callback_or_code,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        session_id=session_id,
        expected_state=expected_state,
        deps=_reauthorize_cpa_oauth_deps(),
    )


def _start_cpa_oauth(
    *,
    cpa_url: str,
    cpa_management_key: str,
    email: str = "",
    proxy_url: str = "",
) -> dict[str, str]:
    return reauthorize_cpa_oauth.start_cpa_oauth(
        cpa_url=cpa_url,
        cpa_management_key=cpa_management_key,
        email=email,
        proxy_url=proxy_url,
        deps=_reauthorize_cpa_oauth_deps(),
    )


def _start_codex2api_oauth(
    *,
    codex2api_url: str,
    codex2api_admin_key: str,
    email: str,
    proxy_url: str = "",
) -> dict[str, str]:
    return reauthorize_cpa_oauth.start_codex2api_oauth(
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        email=email,
        proxy_url=proxy_url,
        deps=_reauthorize_cpa_oauth_deps(),
    )


def _merge_url_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value is not None:
            query[key] = [str(value)]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query, doseq=True), parsed.fragment))


def _first_callbackish_url(*values: Any) -> str:
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        parsed = urlparse(raw)
        if parse_qs(parsed.query).get("code"):
            return raw
        if raw.startswith(("http://", "https://")) and any(token in raw for token in ("/authorize/continue", "/consent", "/oauth/", "/auth/callback")):
            return raw
    return ""


def _is_consent_like_url(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return False
    return any(token in raw for token in ("/authorize/continue", "/authorize/resume", "/consent", "/oauth/"))


def _extract_callback_from_step(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    authorize = info.get("authorize") if isinstance(info.get("authorize"), dict) else {}
    attempts = info.get("attempts") if isinstance(info.get("attempts"), list) else []
    candidate_values: list[Any] = [
        body.get("continue_url"),
        body.get("redirect_url"),
        body.get("url"),
        body.get("callback_url"),
        body.get("return_to"),
        page.get("continue_url"),
        page.get("redirect_url"),
        page.get("url"),
        page.get("callback_url"),
        page.get("return_to"),
        info.get("location"),
        info.get("final_url"),
        authorize.get("continue_url"),
        authorize.get("redirect_url"),
        authorize.get("final_url"),
    ]
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_body = attempt.get("json") if isinstance(attempt.get("json"), dict) else {}
        candidate_values.extend([
            attempt_body.get("continue_url"),
            attempt_body.get("redirect_url"),
            attempt_body.get("url"),
            attempt_body.get("callback_url"),
            attempt.get("location"),
            attempt.get("final_url"),
        ])
    return _first_callbackish_url(*candidate_values)


def _resolve_callback_step(registrar: PlatformRegistrar, info: dict[str, Any], state: str, *, allow_state_resume: bool = True) -> str:
    candidate = _extract_callback_from_step(info)
    if candidate and extract_oauth_callback_params_from_url(candidate):
        return candidate
    if not allow_state_resume:
        return ""
    try:
        resolved = _resolve_oauth_callback(registrar, candidate or "", state if allow_state_resume else "")
    except Exception:
        resolved = ""
    resolved = str(resolved or "").strip()
    if resolved and extract_oauth_callback_params_from_url(resolved):
        return resolved
    return ""


def _continue_with_optional_about_you(
    registrar: PlatformRegistrar,
    continue_url: str,
    state: str,
    bind_email: str = "",
) -> tuple[str, dict[str, Any]]:
    return reauthorize_about_you_flow.continue_with_optional_about_you(
        registrar,
        continue_url,
        state,
        bind_email=bind_email,
        deps=_reauthorize_about_you_deps(),
    )


def _normalize_mail_provider_name(value: Any) -> str:
    return reauthorize_mail_config.normalize_mail_provider_name(value)


_SUPPORTED_MAIL_PROVIDER_NAMES = reauthorize_mail_config.SUPPORTED_MAIL_PROVIDER_NAMES


def _first_text(*values: Any) -> str:
    return reauthorize_mail_config.first_text(*values)


def _webui_mail_section_names_for_account(account: dict[str, Any]) -> tuple[str, ...]:
    return reauthorize_mail_config.webui_mail_section_names_for_account(account)


def _load_webui_default_mail_provider_name(account: dict[str, Any]) -> str:
    return reauthorize_mail_config.load_webui_default_mail_provider_name(account, DATA_DIR)


def _mail_target_email_for_account(account: dict[str, Any], mailbox: dict[str, Any]) -> str:
    return reauthorize_mail_config.mail_target_email_for_account(account, mailbox)


def _mail_provider_name_for_account(account: dict[str, Any], mailbox: dict[str, Any]) -> str:
    return reauthorize_mail_config.mail_provider_name_for_account(account, mailbox, DATA_DIR)


def _load_webui_mail_defaults(provider_name: str) -> dict[str, Any]:
    return reauthorize_mail_config.load_webui_mail_defaults(provider_name, DATA_DIR)


def _load_webui_cloudflare_fallback_provider() -> dict[str, Any]:
    return reauthorize_mail_config.load_webui_cloudflare_fallback_provider(DATA_DIR)


def _mailbox_mail_provider_config(account: dict[str, Any], mailbox: dict[str, Any]) -> dict[str, Any]:
    return reauthorize_mail_config.mailbox_mail_provider_config(account, mailbox, DATA_DIR)


def _mailbox_for_mail_wait(account: dict[str, Any], mailbox: dict[str, Any]) -> dict[str, Any]:
    return reauthorize_mail_config.mailbox_for_mail_wait(account, mailbox, DATA_DIR)


def _sync_mail_wait_state(source: dict[str, Any], target: dict[str, Any]) -> None:
    reauthorize_mail_config.sync_mail_wait_state(source, target)


def _mail_wait_config_for_account(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
) -> RegisterConfig:
    return reauthorize_mail_config.mail_wait_config_for_account(
        account,
        mailbox,
        data_dir=DATA_DIR,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )


def _bind_mail_config_for_account(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
) -> dict[str, Any]:
    return reauthorize_mail_config.bind_mail_config_for_account(
        account,
        mailbox,
        data_dir=DATA_DIR,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
    )


def _login_identifier_for_account(account: dict[str, Any], mailbox: dict[str, Any], fallback_email: str) -> str:
    return reauthorize_mail_config.login_identifier_for_account(account, mailbox, fallback_email)


def _bind_email_hint_for_account(account: dict[str, Any], mailbox: dict[str, Any], fallback_email: str) -> str:
    return reauthorize_mail_config.bind_email_hint_for_account(account, mailbox, fallback_email)





def _safe_response_summary(info: dict[str, Any]) -> dict[str, Any]:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    candidates = []
    for value in [
        body.get("continue_url"), body.get("redirect_url"), body.get("url"), body.get("callback_url"), body.get("return_to"),
        info.get("location"), info.get("final_url"), page.get("continue_url"), page.get("redirect_url"), page.get("url"),
    ]:
        v = str(value or "").strip()
        if v:
            candidates.append(v[:160])
    text = str(info.get("text") or "")
    summary = {
        "status": info.get("status"),
        "ok": info.get("ok"),
        "json_keys": sorted(str(k) for k in body.keys())[:30],
        "page_type": str(page.get("type") or ""),
        "location_prefix": str(info.get("location") or "")[:160],
        "final_url_prefix": str(info.get("final_url") or "")[:160],
        "candidate_prefixes": candidates[:12],
        "text_markers": {
            "has_code": "code=" in text,
            "has_continue": "continue" in text.lower(),
            "has_callback": "callback" in text.lower(),
            "has_consent": "consent" in text.lower(),
        },
    }
    detail = ""
    if isinstance(body, dict):
        for key in ("error", "message", "detail", "reason"):
            value = body.get(key)
            if value:
                detail = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                break
    if detail:
        summary["error_prefix"] = detail[:240]
    return summary

def _enter_login_email_otp_step(registrar: PlatformRegistrar, state: str) -> dict[str, Any]:
    return reauthorize_email_otp_flow.enter_login_email_otp_step(
        registrar,
        state,
        _reauthorize_email_otp_deps(),
    )


def _send_login_otp(registrar: PlatformRegistrar, state: str) -> dict[str, Any]:
    return reauthorize_email_otp_flow.send_login_otp(
        registrar,
        state,
        _reauthorize_email_otp_deps(),
    )


def _trigger_passwordless_login_otp(registrar: PlatformRegistrar) -> dict[str, Any]:
    return reauthorize_email_otp_flow.trigger_passwordless_login_otp(
        registrar,
        _reauthorize_email_otp_deps(),
    )


def _html_attr(attrs: str, name: str) -> str:
    return reauthorize_email_otp_flow.html_attr(attrs, name)


def _strip_tags(value: str) -> str:
    return reauthorize_email_otp_flow.strip_tags(value)


def _extract_email_otp_form_inputs(html_text: str) -> tuple[str, dict[str, str], str, str]:
    return reauthorize_email_otp_flow.extract_email_otp_form_inputs(
        html_text,
        fallback_extract_form_inputs=_extract_form_inputs,
    )


def _submit_login_email_otp_page_form(registrar: PlatformRegistrar, page_info: dict[str, Any]) -> dict[str, Any]:
    return reauthorize_email_otp_flow.submit_login_email_otp_page_form(
        registrar,
        page_info,
        _reauthorize_email_otp_deps(),
    )


def _validate_login_otp(registrar: PlatformRegistrar, state: str, code: str) -> dict[str, Any]:
    return reauthorize_email_otp_flow.validate_login_otp(
        registrar,
        state,
        code,
        _reauthorize_email_otp_deps(),
    )


def _reauthorize_email_otp_step_deps() -> reauthorize_email_otp_flow.ReauthorizeEmailOtpStepDeps:
    return reauthorize_email_otp_flow.ReauthorizeEmailOtpStepDeps(
        mailbox_for_mail_wait_fn=_mailbox_for_mail_wait,
        wait_for_code_fn=wait_for_code,
        sync_mail_wait_state_fn=_sync_mail_wait_state,
        mail_wait_config_for_account_fn=_mail_wait_config_for_account,
        log_stage_fn=_log_stage,
        time_time_fn=time.time,
        enter_login_email_otp_step_fn=_enter_login_email_otp_step,
        response_brief_fn=_response_brief,
        trigger_passwordless_login_otp_fn=_trigger_passwordless_login_otp,
        safe_response_summary_fn=_safe_response_summary,
        send_login_otp_fn=_send_login_otp,
        validate_login_otp_fn=_validate_login_otp,
        step_requires_phone_verification_fn=_step_requires_phone_verification,
        registration_state_from_info_fn=_registration_state_from_info,
        resolve_callback_step_fn=_resolve_callback_step,
        is_consent_like_url_fn=_is_consent_like_url,
        short_url_fn=_short_url,
        resolve_consent_callback_direct_fn=_resolve_consent_callback_direct,
        ready_text_fn=_ready_text,
    )


def _handle_email_otp_step(
    registrar: PlatformRegistrar,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    state: str,
    *,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return reauthorize_email_otp_flow.handle_email_otp_step(
        registrar,
        account,
        mailbox,
        state,
        proxy=proxy,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        deps=_reauthorize_email_otp_step_deps(),
    )


def _first_step_continue_url(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    for value in (
        body.get("continue_url"),
        body.get("redirect_url"),
        body.get("url"),
        page.get("continue_url"),
        page.get("redirect_url"),
        page.get("url"),
        info.get("location"),
        info.get("final_url"),
    ):
        raw = str(value or "").strip()
        if not raw:
            continue
        if raw.startswith("/"):
            return f"{auth_base}{raw}"
        return raw
    return ""


def _step_requires_phone_verification(info: dict[str, Any]) -> bool:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    page = body.get("page") if isinstance(body.get("page"), dict) else {}
    page_type = str(page.get("type") or body.get("page_type") or info.get("page_type") or "").strip().lower()
    if page_type in {"add_phone", "phone_otp_send", "phone_otp_select_channel", "phone_otp_verification", "phone_verification"}:
        return True
    for value in (body.get("continue_url"), body.get("redirect_url"), body.get("url"), page.get("continue_url"), page.get("redirect_url"), page.get("url"), info.get("location"), info.get("final_url")):
        raw = str(value or "").lower()
        if "/add-phone" in raw or "/phone-verification" in raw or "/phone-otp/" in raw:
            return True
    return False


def _build_reauthorize_sms_config(
    *,
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
    sms_wait_timeout: int = 60,
    sms_wait_interval: int = 5,
    sms_resend_after_seconds: int = 30,
    sms_timeout_after_resend_seconds: int = 60,
    sms_release_after_seconds: int = 120,
    sms_retry_count: int = 3,
    sms_auto_retry: bool = False,
) -> HeroSMSConfig:
    return build_sms_config_from_values(
        {
            "sms_provider": sms_provider,
            "sms_api_key": sms_api_key,
            "hero_sms_api_key": hero_sms_api_key,
            "smsbower_api_key": smsbower_api_key,
            "fivesim_api_key": fivesim_api_key,
            "hero_sms_base_url": hero_sms_base_url,
            "smsbower_base_url": smsbower_base_url,
            "hero_sms_country": hero_sms_country,
            "hero_sms_service": hero_sms_service,
            "hero_sms_min_price": hero_sms_min_price,
            "hero_sms_max_price": hero_sms_max_price,
            "sms_wait_timeout": sms_wait_timeout,
            "sms_wait_interval": sms_wait_interval,
            "sms_resend_after_seconds": sms_resend_after_seconds,
            "sms_timeout_after_resend_seconds": sms_timeout_after_resend_seconds,
            "sms_release_after_seconds": sms_release_after_seconds,
            "sms_retry_count": sms_retry_count,
            "sms_auto_retry": sms_auto_retry,
        }
    )


def _phone_pool_now() -> str:
    return reauthorize_phone_pool.phone_pool_now()


def _phone_reuse_pool_path():
    return reauthorize_phone_pool.phone_reuse_pool_path(DATA_DIR, PHONE_REUSE_POOL_NAME)


def _load_phone_reuse_pool() -> dict[str, Any]:
    return reauthorize_phone_pool.load_phone_reuse_pool(DATA_DIR, PHONE_REUSE_POOL_NAME)


def _save_phone_reuse_pool(data: dict[str, Any]) -> None:
    from .json_store import write_json_atomic

    reauthorize_phone_pool.save_phone_reuse_pool(data, DATA_DIR, PHONE_REUSE_POOL_NAME, write_json_atomic)


def _phone_pool_key(config: HeroSMSConfig, activation_id: str) -> str:
    return reauthorize_phone_pool.phone_pool_key(config, activation_id)


def _phone_pool_matches_config(item: dict[str, Any], config: HeroSMSConfig) -> bool:
    return reauthorize_phone_pool.phone_pool_matches_config(item, config, PHONE_REUSE_LIMIT)


def _find_reusable_phone_activation(config: HeroSMSConfig) -> dict[str, str]:
    return reauthorize_phone_pool.find_reusable_phone_activation(
        config,
        data_dir=DATA_DIR,
        pool_name=PHONE_REUSE_POOL_NAME,
        reuse_limit=PHONE_REUSE_LIMIT,
    )


def _acquire_or_reuse_phone_activation(config: HeroSMSConfig) -> dict[str, str]:
    return reauthorize_phone_pool.acquire_or_reuse_phone_activation(
        config,
        data_dir=DATA_DIR,
        pool_name=PHONE_REUSE_POOL_NAME,
        reuse_limit=PHONE_REUSE_LIMIT,
        acquire_phone_fn=acquire_hero_sms_phone,
    )


def _retire_phone_activation(config: HeroSMSConfig, activation_id: str, reason: str = "") -> None:
    from .json_store import write_json_atomic

    reauthorize_phone_pool.retire_phone_activation(
        config,
        activation_id,
        reason=reason,
        data_dir=DATA_DIR,
        pool_name=PHONE_REUSE_POOL_NAME,
        write_json_atomic_fn=write_json_atomic,
    )


def _record_phone_activation_success(
    config: HeroSMSConfig,
    activation_id: str,
    phone_number: str,
    *,
    account_id: str = "",
    email: str = "",
) -> dict[str, Any]:
    from .json_store import write_json_atomic

    return reauthorize_phone_pool.record_phone_activation_success(
        config,
        activation_id,
        phone_number,
        account_id=account_id,
        email=email,
        data_dir=DATA_DIR,
        pool_name=PHONE_REUSE_POOL_NAME,
        reuse_limit=PHONE_REUSE_LIMIT,
        write_json_atomic_fn=write_json_atomic,
    )


def _set_phone_activation_after_success(config: HeroSMSConfig, activation_id: str, usage: dict[str, Any]) -> None:
    reauthorize_phone_pool.set_phone_activation_after_success(config, activation_id, usage, set_hero_sms_status)


def _send_add_phone_number(registrar: PlatformRegistrar, phone_number: str, referer: str = "") -> dict[str, Any]:
    return reauthorize_phone_verification.send_add_phone_number(
        registrar,
        phone_number,
        referer=referer,
        deps=_reauthorize_phone_verification_deps(),
    )


def _validate_add_phone_otp(registrar: PlatformRegistrar, code: str, referer: str = "") -> dict[str, Any]:
    return reauthorize_phone_verification.validate_add_phone_otp(
        registrar,
        code,
        referer=referer,
        deps=_reauthorize_phone_verification_deps(),
    )


def _continue_with_optional_phone_verification(
    registrar: PlatformRegistrar,
    source_info: dict[str, Any],
    state: str,
    *,
    sms_config: HeroSMSConfig,
    retry_count: int = 1,
    account_id: str = "",
    email: str = "",
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return reauthorize_phone_verification.continue_with_optional_phone_verification(
        registrar,
        source_info,
        state,
        sms_config=sms_config,
        retry_count=retry_count,
        account_id=account_id,
        email=email,
        deps=_reauthorize_phone_verification_flow_deps(),
    )


def _callback_url_from_response_info_direct(info: dict[str, Any]) -> str:
    return reauthorize_consent_callback.callback_url_from_response_info(
        info,
        _reauthorize_consent_callback_deps(),
    )

def _resolve_consent_callback_direct(registrar: PlatformRegistrar, consent_url: str, state: str) -> tuple[str, dict[str, Any]]:
    return reauthorize_consent_callback.resolve_consent_callback_direct(
        registrar,
        consent_url,
        state,
        _reauthorize_consent_callback_deps(),
    )

def _login_username_payload(identifier: str) -> dict[str, str]:
    return reauthorize_password_login.login_username_payload(identifier)


def _attempt_password_login(registrar: PlatformRegistrar, email: str, password: str) -> dict[str, Any]:
    return reauthorize_password_login.attempt_password_login(
        registrar,
        email,
        password,
        _reauthorize_password_login_deps(),
    )


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _mark_reauthorize_failed(account: dict[str, Any], message: str, *, mailbox: dict[str, Any] | None = None) -> dict[str, Any]:
    return reauthorize_result_store.mark_reauthorize_failed(
        account,
        message,
        mailbox=mailbox,
        deps=_reauthorize_result_store_deps(),
    )


def _mark_reauthorize_authorized(account: dict[str, Any], *, callback_url: str = "", mailbox: dict[str, Any] | None = None) -> dict[str, Any]:
    return reauthorize_result_store.mark_reauthorize_authorized(
        account,
        callback_url=callback_url,
        mailbox=mailbox,
        deps=_reauthorize_result_store_deps(),
    )


def _registration_reauth_blocker(account: dict[str, Any], mailbox: dict[str, Any]) -> str:
    return reauthorize_result_store.registration_reauth_blocker(account, mailbox)


def _mark_unusable_reauthorize_source(account: dict[str, Any], message: str, *, mailbox: dict[str, Any]) -> dict[str, Any]:
    return reauthorize_result_store.mark_unusable_reauthorize_source(
        account,
        message,
        mailbox=mailbox,
        deps=_reauthorize_result_store_deps(),
    )


def _direct_exchange_local_callback(
    config: Sub2APIOAuthFlowConfig,
    prepared: Any,
    callback_url: str,
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
) -> RegistrationResult:
    return reauthorize_local_tokens.direct_exchange_local_callback(
        config,
        prepared,
        callback_url,
        email=email,
        password=password,
        mailbox=mailbox,
        deps=_reauthorize_local_token_deps(),
    )


def _compact_consent_debug_summary(consent_summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = consent_summary if isinstance(consent_summary, dict) else {}
    compact_attempts: list[dict[str, Any]] = []
    for attempt in (summary.get("attempts") or [])[:3]:
        if not isinstance(attempt, dict):
            continue
        compact: dict[str, Any] = {
            "method": attempt.get("method"),
            "matched": attempt.get("matched"),
            "status": attempt.get("status"),
            "page_type": attempt.get("page_type"),
            "target_prefix": str(attempt.get("target_prefix") or "")[:120],
            "final_url_prefix": str(attempt.get("final_url_prefix") or "")[:120],
            "location_prefix": str(attempt.get("location_prefix") or "")[:120],
            "json_keys": attempt.get("json_keys"),
            "text_markers": attempt.get("text_markers"),
        }
        steps: list[dict[str, Any]] = []
        for step in (attempt.get("steps") or [])[:5]:
            if not isinstance(step, dict):
                continue
            steps.append(
                {
                    "method": step.get("method"),
                    "source": step.get("source"),
                    "status": step.get("status"),
                    "content_type_prefix": step.get("content_type_prefix"),
                    "final_url_prefix": str(step.get("final_url_prefix") or "")[:120],
                    "location_prefix": str(step.get("location_prefix") or "")[:120],
                    "json_keys": step.get("json_keys"),
                    "text_markers": step.get("text_markers"),
                }
            )
        if steps:
            compact["steps"] = steps
        compact_attempts.append({k: v for k, v in compact.items() if v not in (None, "", [])})
    return {"url_prefix": str(summary.get("url_prefix") or "")[:120], "attempts": compact_attempts}


def _exchange_local_tokens_after_cpa(
    registrar: PlatformRegistrar,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    wait_timeout: int = 60,
    wait_interval: int = 2,
    request_timeout: int = 30,
) -> RegistrationResult:
    return reauthorize_local_token_flow.exchange_local_tokens_after_cpa(
        registrar,
        account,
        mailbox,
        email=email,
        password=password,
        wait_timeout=wait_timeout,
        wait_interval=wait_interval,
        request_timeout=request_timeout,
        deps=_reauthorize_local_token_flow_deps(),
    )


def _finalize_cpa_submit_with_optional_local_tokens(
    registrar: PlatformRegistrar,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    cpa_callback_url: str,
    cpa_result: dict[str, Any],
    debug: dict[str, Any],
) -> ReauthorizeAutoOutcome:
    return reauthorize_result_store.finalize_cpa_submit_with_optional_local_tokens(
        registrar,
        account,
        mailbox,
        email=email,
        password=password,
        cpa_callback_url=cpa_callback_url,
        cpa_result=cpa_result,
        debug=debug,
        deps=_reauthorize_result_store_deps(),
    )


def _save_result_and_import_codex2api(
    account: dict[str, Any],
    result: RegistrationResult,
    *,
    source: str,
    codex2api_url: str = "",
    codex2api_admin_key: str = "",
    codex2api_proxy_url: str = "",
) -> ReauthorizeFinishOutcome:
    return reauthorize_result_store.save_result_and_import_codex2api(
        account,
        result,
        source=source,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        codex2api_proxy_url=codex2api_proxy_url,
        deps=_reauthorize_result_store_deps(),
    )


def start_account_reauthorize(account_id: str, *, proxy: str = "") -> ReauthorizeStartOutcome:
    account = get_account(account_id)
    if not account:
        return ReauthorizeStartOutcome(ok=False, message="account_not_found")
    email = str(account.get("email") or "").strip()
    password = str(account.get("password") or "")
    mailbox = account.get("mailbox") if isinstance(account.get("mailbox"), dict) else {}
    if not email or not password:
        return ReauthorizeStartOutcome(ok=False, message="account_email_or_password_missing", account=account)
    cfg = Sub2APIOAuthFlowConfig(login_hint=email, proxy=str(proxy or "").strip())
    prepared = build_openai_oauth_authorize_url(cfg)
    account["status"] = "authorizing"
    account["last_error"] = ""
    updated = upsert_account(account)
    return ReauthorizeStartOutcome(ok=True, message="reauthorize_authorize_ready", account=updated, authorize_url=str(prepared.authorize_url or ""), state=str(prepared.state or ""), nonce=str(prepared.nonce or ""), redirect_uri=str(prepared.redirect_uri or ""), client_id=str(prepared.client_id or ""), code_verifier=str(prepared.code_verifier or ""), bind_email=str(mailbox.get("bind_email") or email))


def finish_account_reauthorize(
    account_id: str,
    *,
    callback_or_code: str,
    code_verifier: str,
    state: str,
    redirect_uri: str,
    client_id: str,
    codex2api_url: str = "",
    codex2api_admin_key: str = "",
    codex2api_proxy_url: str = "",
    proxy: str = "",
) -> ReauthorizeFinishOutcome:
    account = get_account(account_id)
    if not account:
        return ReauthorizeFinishOutcome(ok=False, message="account_not_found")
    email = str(account.get("email") or "").strip()
    cfg = Sub2APIOAuthFlowConfig(login_hint=email, redirect_uri=str(redirect_uri or "").strip() or "http://localhost:1455/auth/callback", proxy=str(proxy or "").strip())
    prepared = build_openai_oauth_authorize_url(cfg)
    prepared.state = str(state or prepared.state)
    prepared.code_verifier = str(code_verifier or prepared.code_verifier)
    prepared.client_id = str(client_id or prepared.client_id)
    prepared.redirect_uri = str(redirect_uri or prepared.redirect_uri)
    try:
        result = exchange_callback_code(cfg, prepared, callback_or_code)
    except Exception as exc:
        result = RegistrationResult(ok=False, email=email, error=str(exc or "reauthorize_exchange_failed"))
    return _save_result_and_import_codex2api(account, result, source="reauthorize", codex2api_url=codex2api_url, codex2api_admin_key=codex2api_admin_key, codex2api_proxy_url=codex2api_proxy_url)


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
) -> ReauthorizeAutoOutcome:
    options = dict(locals())
    options.pop("account_id", None)
    options["deps"] = _reauthorize_auto_flow_deps()
    return reauthorize_auto_flow.auto_reauthorize_account_with_email_otp(account_id, **options)
