from __future__ import annotations

import secrets
import time
from functools import lru_cache
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests

from . import mail_provider
from . import oauth_form_inputs
from .config import DATA_DIR
from .register_core import (
    RegistrationResult,
    PlatformRegistrar,
    _random_birthdate,
    _random_name,
    _random_password,
    _generate_pkce,
    _make_trace_headers,
    auth_base,
    platform_oauth_audience,
    platform_oauth_client_id,
    request_with_local_retry,
    save_result,
    exchange_platform_tokens,
    extract_oauth_callback_params_from_consent_session,
    _is_socks_proxy,
)
from .jwt_utils import decode_jwt_payload as _decode_jwt_payload
from .registration_about_you import about_you_form_payloads
from .registration_callback import extract_oauth_callback_params_from_url
from .registration_environment import get_common_headers, get_navigate_headers
from .registration_responses import response_json as _response_json
from .registration_sentinel import build_sentinel_token
from . import oauth_account_export
from . import oauth_about_you_flow
from . import oauth_add_email_flow
from . import oauth_continue_navigation
from . import oauth_cpa_callback
from . import oauth_phone_flow_runtime
from . import sms_activation_catalog
from . import sms_activation_client
from . import sms_activation_flow
from . import sms_activation_helpers
from .sms_provider_config import (
    FIVESIM_BASE_URL,
    HERO_SMS_MAX_RETRY_COUNT,
    HERO_SMS_RELEASE_AFTER_SECONDS,
    HERO_SMS_RESEND_AFTER_SECONDS,
    HERO_SMS_TIMEOUT_AFTER_RESEND_SECONDS,
    HeroSMSConfig,
    SMSBOWER_BASE_URL,
)


DEFAULT_SUB2API_EXPORT_NAME = oauth_account_export.DEFAULT_SUB2API_EXPORT_NAME
DEFAULT_ACCOUNT_ARCHIVE_NAME = oauth_account_export.DEFAULT_ACCOUNT_ARCHIVE_NAME
PARTIAL_HERO_PHONE_BIND_RESULT_NAME = oauth_phone_flow_runtime.PARTIAL_HERO_PHONE_BIND_RESULT_NAME


@dataclass
class Sub2APIOAuthFlowConfig:
    proxy: str = ""
    redirect_uri: str = "http://localhost:1455/auth/callback"
    login_hint: str = ""
    account_name: str = ""
    concurrency: int = 10
    priority: int = 1
    rate_multiplier: int = 1
    auto_pause_on_expired: bool = True
    plan_type: str = "free"
    privacy_mode: str = "training_off"
    export_name: str = DEFAULT_SUB2API_EXPORT_NAME
    archive_name: str = DEFAULT_ACCOUNT_ARCHIVE_NAME
    organization_id: str = ""
    hero_sms: HeroSMSConfig | None = None


@dataclass
class Sub2APIOAuthPrepared:
    authorize_url: str
    state: str
    nonce: str
    device_id: str
    code_verifier: str
    code_challenge: str
    client_id: str
    redirect_uri: str
    login_hint: str = ""


@dataclass
class Sub2APIOAuthFlowResult:
    ok: bool
    authorize_url: str = ""
    callback_url: str = ""
    code: str = ""
    export_path: str = ""
    archive_path: str = ""
    saved_result: str = ""
    email: str = ""
    error: str = ""
    payload: dict[str, Any] | None = None
    archive: dict[str, Any] | None = None
    tokens: RegistrationResult | None = None


PhoneFlowFailure = oauth_phone_flow_runtime.PhoneFlowFailure


def _merge_url_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value is None:
            continue
        query[key] = [str(value)]
    merged = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, merged, parsed.fragment))


def build_openai_oauth_authorize_url(config: Sub2APIOAuthFlowConfig) -> Sub2APIOAuthPrepared:
    """Build the manual authorize URL + PKCE bundle for phase-1 OAuth handoff."""
    registrar = PlatformRegistrar(config.proxy)
    try:
        registrar.session.cookies.set("oai-did", registrar.device_id, domain=".auth.openai.com")
        registrar.session.cookies.set("oai-did", registrar.device_id, domain="auth.openai.com")
        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        params = {
            "issuer": auth_base,
            "client_id": platform_oauth_client_id,
            "audience": platform_oauth_audience,
            "redirect_uri": config.redirect_uri,
            "device_id": registrar.device_id,
            "screen_hint": "login_or_signup",
            "max_age": "0",
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "response_mode": "query",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9",
        }
        if config.login_hint:
            params["login_hint"] = config.login_hint
        authorize_url = f"{auth_base}/api/accounts/authorize?" + "&".join(
            f"{key}={__import__('requests').utils.quote(str(value), safe='')}" for key, value in params.items()
        )
        return Sub2APIOAuthPrepared(
            authorize_url=authorize_url,
            state=state,
            nonce=nonce,
            device_id=registrar.device_id,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            client_id=platform_oauth_client_id,
            redirect_uri=config.redirect_uri,
            login_hint=config.login_hint,
        )
    finally:
        registrar.close()


def normalize_callback_url(callback_or_code: str, redirect_uri: str, state: str = "") -> tuple[str, str]:
    """Accept either a full callback URL or a raw code and normalize both forms."""
    raw = str(callback_or_code or "").strip()
    if not raw:
        raise ValueError("empty_callback_or_code")
    callback_params = extract_oauth_callback_params_from_url(raw)
    if callback_params:
        callback_url = raw
        code = str(callback_params.get("code") or "").strip()
        return callback_url, code
    code = raw
    callback_url = _merge_url_query(redirect_uri, code=code, state=state)
    return callback_url, code


def exchange_callback_code(config: Sub2APIOAuthFlowConfig, prepared: Sub2APIOAuthPrepared, callback_or_code: str) -> RegistrationResult:
    """Exchange the returned callback/code into tokens, with direct token fallback."""
    callback_url, code = normalize_callback_url(callback_or_code, prepared.redirect_uri, prepared.state)
    primary_registrar = PlatformRegistrar(config.proxy)
    fallback_registrar: PlatformRegistrar | None = None
    try:
        result = exchange_platform_tokens(primary_registrar.session, prepared.device_id, prepared.code_verifier, callback_url, config.proxy)
        if not result.callback_url:
            result.callback_url = callback_url
        if not result.ok and code:
            fallback_registrar = PlatformRegistrar(config.proxy)
            resp = fallback_registrar.session.post(
                f"{auth_base}/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": prepared.redirect_uri,
                    "client_id": prepared.client_id,
                    "code_verifier": prepared.code_verifier,
                },
                verify=False,
                timeout=60,
            )
            try:
                data = resp.json()
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            if resp.status_code == 200:
                result = RegistrationResult(
                    ok=True,
                    email=str((_decode_jwt_payload(str(data.get("id_token") or "")) or {}).get("email") or (_decode_jwt_payload(str(data.get("access_token") or "")) or {}).get("https://api.openai.com/profile", {}).get("email") or "").strip(),
                    access_token=str(data.get("access_token") or "").strip(),
                    refresh_token=str(data.get("refresh_token") or "").strip(),
                    id_token=str(data.get("id_token") or "").strip(),
                    callback_url=callback_url,
                )
            else:
                result = RegistrationResult(ok=False, callback_url=callback_url, error=f"oauth_token_http_{resp.status_code}")
        return result
    finally:
        primary_registrar.close()
        if fallback_registrar is not None:
            fallback_registrar.close()


def _utc_now_iso() -> str:
    return oauth_account_export.utc_now_iso()


def _extract_account_identity(result: RegistrationResult, organization_id: str = "", plan_type: str = "free") -> dict[str, Any]:
    return oauth_account_export.extract_account_identity(result, organization_id=organization_id, plan_type=plan_type)


def _normalize_sub2api_origin(raw_url: str) -> str:
    return oauth_account_export.normalize_sub2api_origin(raw_url)


def _request_codex2api_json(
    codex2api_url: str,
    *,
    path: str,
    admin_key: str,
    method: str = "POST",
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    return oauth_account_export.request_codex2api_json(
        codex2api_url,
        path=path,
        admin_key=admin_key,
        method=method,
        body=body,
        timeout=timeout,
    )


def _extract_codex2api_account_id(data: Any, *, email: str = "", name: str = "") -> int:
    return oauth_account_export.extract_codex2api_account_id(data, email=email, name=name)


def _find_codex2api_account_id(codex2api_url: str, *, admin_key: str, email: str = "", name: str = "") -> int:
    return oauth_account_export.find_codex2api_account_id(codex2api_url, admin_key=admin_key, email=email, name=name)


def _refresh_codex2api_account(codex2api_url: str, *, admin_key: str, account_id: int) -> dict[str, Any]:
    return oauth_account_export.refresh_codex2api_account(codex2api_url, admin_key=admin_key, account_id=account_id)


def import_result_to_codex2api(
    result: RegistrationResult,
    *,
    codex2api_url: str,
    admin_key: str,
    account_name: str = "",
    proxy_url: str = "",
) -> dict[str, Any]:
    return oauth_account_export.import_result_to_codex2api(
        result,
        codex2api_url=codex2api_url,
        admin_key=admin_key,
        account_name=account_name,
        proxy_url=proxy_url,
    )


def build_sub2api_import_payload(
    result: RegistrationResult,
    *,
    concurrency: int = 10,
    priority: int = 1,
    rate_multiplier: int = 1,
    auto_pause_on_expired: bool = True,
    organization_id: str = "",
    plan_type: str = "free",
    privacy_mode: str = "training_off",
    account_name: str = "",
) -> dict[str, Any]:
    return oauth_account_export.build_sub2api_import_payload(
        result,
        concurrency=concurrency,
        priority=priority,
        rate_multiplier=rate_multiplier,
        auto_pause_on_expired=auto_pause_on_expired,
        organization_id=organization_id,
        plan_type=plan_type,
        privacy_mode=privacy_mode,
        account_name=account_name,
    )


def build_account_archive(
    prepared: Sub2APIOAuthPrepared,
    result: RegistrationResult,
    *,
    callback_url: str,
    phone_number: str = "",
    phone_country: str = "CO",
    hero_sms_order_id: str = "",
    hero_sms_price: float | None = None,
    email_password: str = "",
    mail_provider_name: str = "",
    organization_id: str = "",
    plan_type: str = "free",
) -> dict[str, Any]:
    return oauth_account_export.build_account_archive(
        prepared,
        result,
        callback_url=callback_url,
        phone_number=phone_number,
        phone_country=phone_country,
        hero_sms_order_id=hero_sms_order_id,
        hero_sms_price=hero_sms_price,
        email_password=email_password,
        mail_provider_name=mail_provider_name,
        organization_id=organization_id,
        plan_type=plan_type,
    )


def save_sub2api_export(payload: dict[str, Any], filename: str = DEFAULT_SUB2API_EXPORT_NAME) -> Path:
    return oauth_account_export.save_sub2api_export(payload, filename)


def save_account_archive(archive: dict[str, Any], filename: str = DEFAULT_ACCOUNT_ARCHIVE_NAME) -> Path:
    return oauth_account_export.save_account_archive(archive, filename)


def _hero_sms_request(config: HeroSMSConfig, params: dict[str, Any]) -> Any:
    return sms_activation_client.hero_sms_request(config, params)


def _5sim_request(config: HeroSMSConfig, path: str, params: dict[str, Any] | None = None) -> Any:
    return sms_activation_client.fivesim_request(config, path, params)


def _normalize_sms_provider(value: Any) -> str:
    return sms_activation_helpers.normalize_sms_provider(value)


def _is_5sim_config(config: HeroSMSConfig) -> bool:
    return sms_activation_helpers.is_5sim_config(config)


def _is_smsbower_config(config: HeroSMSConfig) -> bool:
    return sms_activation_helpers.is_smsbower_config(config)


def _sms_provider_label(config: HeroSMSConfig) -> str:
    return sms_activation_helpers.sms_provider_label(config)


def _hero_sms_text(payload: Any) -> str:
    return sms_activation_helpers.hero_sms_text(payload)


def _extract_sms_code(value: Any) -> str:
    return sms_activation_helpers.extract_sms_code(value)


def _extract_5sim_sms_code(value: Any) -> str:
    return sms_activation_helpers.extract_5sim_sms_code(value)


def _normalize_hero_sms_price(value: Any) -> float | None:
    return sms_activation_helpers.normalize_hero_sms_price(value)


def _resolve_hero_sms_stock_state(payload: Any) -> tuple[bool, int]:
    return sms_activation_catalog.resolve_hero_sms_stock_state(payload)


def _resolve_hero_sms_display_quantity(payload: Any) -> int | None:
    return sms_activation_catalog.resolve_hero_sms_display_quantity(payload)


def _collect_hero_sms_price_candidates(payload: Any, *, include_zero_stock: bool = False, candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return sms_activation_catalog.collect_hero_sms_price_candidates(
        payload,
        include_zero_stock=include_zero_stock,
        candidates=candidates,
    )


def _build_sorted_unique_price_candidates(values: list[Any]) -> list[float]:
    return sms_activation_catalog.build_sorted_unique_price_candidates(values)


def _fetch_hero_sms_price_payloads(config: HeroSMSConfig) -> tuple[list[Any], list[dict[str, str]]]:
    return sms_activation_catalog.fetch_hero_sms_price_payloads(
        config,
        hero_sms_request_fn=_hero_sms_request,
    )


def fetch_hero_sms_price_summary(config: HeroSMSConfig, *, country_label: str = "") -> dict[str, Any]:
    return sms_activation_catalog.fetch_hero_sms_price_summary(
        config,
        country_label=country_label,
        hero_sms_request_fn=_hero_sms_request,
        fivesim_request_fn=_5sim_request,
    )


def fetch_hero_sms_country_catalog(config: HeroSMSConfig) -> list[dict[str, Any]]:
    return sms_activation_catalog.fetch_hero_sms_country_catalog(
        config,
        hero_sms_request_fn=_hero_sms_request,
    )


def fetch_hero_sms_countries(config: HeroSMSConfig) -> list[dict[str, Any]]:
    return sms_activation_catalog.fetch_hero_sms_countries(
        config,
        hero_sms_request_fn=_hero_sms_request,
        fivesim_request_fn=_5sim_request,
        country_name_map_fn=fetch_country_name_zh_map,
    )


def fetch_hero_sms_quote_list(config: HeroSMSConfig) -> dict[str, Any]:
    return sms_activation_catalog.fetch_hero_sms_quote_list(config)


@lru_cache(maxsize=1)
def fetch_country_name_zh_map() -> dict[str, str]:
    return sms_activation_catalog.fetch_country_name_zh_map()


def _resolve_hero_sms_catalog_price_candidates(config: HeroSMSConfig) -> list[float]:
    return sms_activation_catalog.resolve_hero_sms_catalog_price_candidates(
        config,
        quote_list_fn=fetch_hero_sms_quote_list,
        price_summary_fn=fetch_hero_sms_price_summary,
    )


def _resolve_hero_sms_price_candidates_for_retry(config: HeroSMSConfig) -> list[float]:
    return sms_activation_catalog.resolve_hero_sms_price_candidates_for_retry(
        config,
        catalog_price_candidates_fn=_resolve_hero_sms_catalog_price_candidates,
    )


def _resolve_hero_sms_exact_request_price(config: HeroSMSConfig, price_limit: float | None) -> str:
    return sms_activation_catalog.resolve_hero_sms_exact_request_price(
        config,
        price_limit,
        catalog_price_candidates_fn=_resolve_hero_sms_catalog_price_candidates,
    )


def _normalize_acquired_phone_number(phone_number: str) -> str:
    return sms_activation_helpers.normalize_acquired_phone_number(phone_number)


def _activation_price_from_payload(payload: Any) -> str:
    return sms_activation_helpers.activation_price_from_payload(payload)


def _activation_result(activation_id: str, phone_number: str, *, price: str = "") -> dict[str, str]:
    return sms_activation_helpers.activation_result(activation_id, phone_number, price=price)


def acquire_hero_sms_phone(
    config: HeroSMSConfig,
    *,
    max_price_override: float | None = None,
    allow_wrong_price_retry: bool = True,
) -> dict[str, str]:
    return sms_activation_flow.acquire_hero_sms_phone(
        config,
        max_price_override=max_price_override,
        allow_wrong_price_retry=allow_wrong_price_retry,
        hero_sms_request_fn=_hero_sms_request,
        fivesim_request_fn=_5sim_request,
        quote_list_fn=fetch_hero_sms_quote_list,
        exact_request_price_fn=_resolve_hero_sms_exact_request_price,
    )


def poll_hero_sms_code(
    config: HeroSMSConfig,
    activation_id: str,
    *,
    on_resend: Any = None,
    timeout_after_resend: int | None = None,
    on_progress: Any = None,
    progress_interval: int = 15,
) -> str:
    return sms_activation_flow.poll_hero_sms_code(
        config,
        activation_id,
        on_resend=on_resend,
        timeout_after_resend=timeout_after_resend,
        on_progress=on_progress,
        progress_interval=progress_interval,
        hero_sms_request_fn=_hero_sms_request,
        fivesim_request_fn=_5sim_request,
        datetime_module=datetime,
        sleep_fn=time.sleep,
        resend_after_default=HERO_SMS_RESEND_AFTER_SECONDS,
        timeout_after_resend_default=HERO_SMS_TIMEOUT_AFTER_RESEND_SECONDS,
        release_after_default=HERO_SMS_RELEASE_AFTER_SECONDS,
    )


def set_hero_sms_status(config: HeroSMSConfig, activation_id: str, status: int) -> None:
    return sms_activation_flow.set_hero_sms_status(
        config,
        activation_id,
        status,
        hero_sms_request_fn=_hero_sms_request,
        fivesim_request_fn=_5sim_request,
    )


def _phone_activation_acquire(
    config: HeroSMSConfig,
    *,
    max_price_override: float | None = None,
) -> dict[str, str]:
    return sms_activation_flow.phone_activation_acquire(
        config,
        max_price_override=max_price_override,
        acquire_phone_fn=acquire_hero_sms_phone,
    )


def _phone_activation_reuse(phone_number: str, activation_id: str) -> dict[str, str]:
    return sms_activation_flow.phone_activation_reuse(phone_number, activation_id)


def _phone_activation_poll_code(config: HeroSMSConfig, activation_id: str) -> str:
    return sms_activation_flow.phone_activation_poll_code(
        config,
        activation_id,
        poll_code_fn=poll_hero_sms_code,
    )


def _phone_activation_complete(config: HeroSMSConfig, activation_id: str) -> None:
    sms_activation_flow.phone_activation_complete(
        config,
        activation_id,
        set_status_fn=set_hero_sms_status,
    )


def _phone_activation_cancel(config: HeroSMSConfig, activation_id: str) -> None:
    sms_activation_flow.phone_activation_cancel(
        config,
        activation_id,
        set_status_fn=set_hero_sms_status,
    )


def _phone_activation_reactivate(config: HeroSMSConfig, activation_id: str) -> None:
    sms_activation_flow.phone_activation_reactivate(
        config,
        activation_id,
        set_status_fn=set_hero_sms_status,
    )


def _auth_json_headers(registrar: PlatformRegistrar, referer: str, sentinel_flow: str) -> dict[str, str]:
    headers = get_common_headers()
    headers["accept"] = "application/json"
    headers["content-type"] = "application/json"
    headers["referer"] = referer
    headers["oai-device-id"] = registrar.device_id
    headers.update(_make_trace_headers())
    try:
        headers["OpenAI-Sentinel-Token"] = registrar._ensure_sentinel_token(sentinel_flow)
    except Exception as exc:
        headers["x-openai-sentinel-error"] = str(exc)
    return headers


def _contains_whatsapp_marker(payload: Any) -> bool:
    return oauth_continue_navigation.contains_whatsapp_marker(payload)


def _fetch_phone_verification_page_text(registrar: PlatformRegistrar, candidate_url: str = "") -> str:
    return oauth_continue_navigation.fetch_phone_verification_page_text(
        registrar,
        candidate_url,
        auth_base_value=auth_base,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
    )


def _is_static_flow_asset(url: str) -> bool:
    return oauth_continue_navigation.is_static_flow_asset(url)


def _sanitize_flow_candidate(candidate: str) -> str:
    return oauth_continue_navigation.sanitize_flow_candidate(candidate, auth_base_value=auth_base)


def _iter_flow_url_candidates(value: Any) -> list[str]:
    return oauth_continue_navigation.iter_flow_url_candidates(value, auth_base_value=auth_base)


def _flow_url_priority(url: str) -> tuple[int, int]:
    return oauth_continue_navigation.flow_url_priority(
        url,
        auth_base_value=auth_base,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
    )


def _choose_preferred_flow_url(candidates: list[str], fallback: str = "") -> str:
    return oauth_continue_navigation.choose_preferred_flow_url(
        candidates,
        fallback=fallback,
        auth_base_value=auth_base,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
    )


def _extract_callback_url_from_text(text: str) -> str:
    return oauth_continue_navigation.extract_callback_url_from_text(
        text,
        auth_base_value=auth_base,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
    )


def _probe_phone_signup_password_page(registrar: PlatformRegistrar, phone_number: str) -> dict[str, Any]:
    return oauth_continue_navigation.probe_phone_signup_password_page(
        registrar,
        phone_number,
        auth_base_value=auth_base,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
    )


def _load_continue_page(registrar: PlatformRegistrar, continue_url: str) -> dict[str, Any]:
    return oauth_continue_navigation.load_continue_page(
        registrar,
        continue_url,
        auth_base_value=auth_base,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
        response_json_fn=_response_json,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
    )


def _build_plain_retry_session(proxy: str = "") -> requests.Session:
    session = requests.Session()
    session.verify = False
    adapter = requests.adapters.HTTPAdapter(max_retries=2, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    normalized_proxy = str(proxy or "").strip()
    if normalized_proxy and not _is_socks_proxy(normalized_proxy):
        session.proxies.update({"http": normalized_proxy, "https": normalized_proxy})
    return session


def _resolve_oauth_callback(registrar: PlatformRegistrar, candidate_url: str, state: str, *, max_steps: int = 12, request_timeout: int = 8, include_codex_consent: bool = True) -> str:
    return oauth_continue_navigation.resolve_oauth_callback(
        registrar,
        candidate_url,
        state,
        max_steps=max_steps,
        request_timeout=request_timeout,
        include_codex_consent=include_codex_consent,
        auth_base_value=auth_base,
        navigate_headers_fn=get_navigate_headers,
        response_json_fn=_response_json,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        consent_session_callback_fn=extract_oauth_callback_params_from_consent_session,
        extract_form_inputs_fn=_extract_form_inputs,
        plain_retry_session_builder_fn=_build_plain_retry_session,
    )


def _extract_attr(attrs: str, name: str) -> str:
    return oauth_form_inputs.extract_attr(attrs, name)


def _strip_html_tags(value: str) -> str:
    return oauth_form_inputs.strip_html_tags(value)


def _extract_form_inputs(html_text: str) -> tuple[str, dict[str, str], str, str]:
    return oauth_form_inputs.extract_form_inputs(html_text)


def _extract_add_email_code_form_inputs(html_text: str) -> tuple[str, dict[str, str], str, str]:
    return oauth_add_email_flow.extract_add_email_code_form_inputs(
        html_text,
        extract_form_inputs_fn=_extract_form_inputs,
        extract_attr_fn=_extract_attr,
        strip_html_tags_fn=_strip_html_tags,
    )


def _submit_about_you_form(
    registrar: PlatformRegistrar,
    *,
    page_url: str,
    page_html: str,
    full_name: str,
    birthdate: str,
) -> tuple[str, str]:
    return oauth_about_you_flow.submit_about_you_form(
        registrar,
        page_url=page_url,
        page_html=page_html,
        full_name=full_name,
        birthdate=birthdate,
        merge_url_query_fn=_merge_url_query,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
        extract_form_inputs_fn=_extract_form_inputs,
        about_you_form_payloads_fn=about_you_form_payloads,
        post_form_and_follow_fn=_post_form_and_follow,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        load_continue_page_fn=_load_continue_page,
    )


def _post_form_and_follow(
    registrar: PlatformRegistrar,
    *,
    page_url: str,
    action: str,
    payload: dict[str, str],
) -> tuple[str, str]:
    raw_action = str(action or "").strip()
    if not raw_action:
        target = page_url
    elif raw_action.startswith("http://") or raw_action.startswith("https://"):
        target = raw_action
    elif raw_action.startswith("?"):
        parsed = urlparse(page_url)
        target = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, raw_action[1:], parsed.fragment))
    else:
        target = urljoin(page_url, raw_action)
    headers = get_navigate_headers()
    headers["content-type"] = "application/x-www-form-urlencoded"
    headers["origin"] = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    headers["referer"] = page_url
    if ".data" in target or "_data=" in target or "_routes=" in target:
        headers["accept"] = "*/*"
        headers["sec-fetch-dest"] = "empty"
        headers["sec-fetch-mode"] = "cors"
        headers["sec-fetch-site"] = "same-origin"
        headers["x-remix-request"] = "yes"
    response, error = request_with_local_retry(
        registrar.session,
        "post",
        target,
        data=payload,
        headers=headers,
        allow_redirects=True,
        verify=False,
    )
    if response is None:
        raise RuntimeError(error or "form_submit_failed")
    final_url = str(getattr(response, "url", "") or target)
    try:
        body = str(response.text or "")
    except Exception:
        body = ""
    return final_url, body


_PHONE_FLOW_SIGNUP_VERIFIED_STAGES = oauth_phone_flow_runtime.PHONE_FLOW_SIGNUP_VERIFIED_STAGES
_PHONE_FLOW_OAUTH_VERIFIED_STAGES = oauth_phone_flow_runtime.PHONE_FLOW_OAUTH_VERIFIED_STAGES


def _classify_phone_flow_error(raw_error: str) -> PhoneFlowFailure:
    return oauth_phone_flow_runtime.classify_phone_flow_error(raw_error)


def _build_phone_flow_runtime(
    *,
    phone_number: str = "",
    activation_id: str = "",
    provider: str = "",
    stage: str = "partial",
    purpose: str = "signup",
    status: str = "partial",
    bind_email: str = "",
    callback_url: str = "",
    callback_source: str = "",
    import_submit_ok: bool | None = None,
    import_submit_message: str = "",
    last_error: str = "",
    error_code: str = "",
    error_retryable: bool = False,
    recovery_action: str = "stop",
) -> dict[str, Any]:
    return oauth_phone_flow_runtime.build_phone_flow_runtime(
        phone_number=phone_number,
        activation_id=activation_id,
        provider=provider,
        stage=stage,
        purpose=purpose,
        status=status,
        bind_email=bind_email,
        callback_url=callback_url,
        callback_source=callback_source,
        import_submit_ok=import_submit_ok,
        import_submit_message=import_submit_message,
        last_error=last_error,
        error_code=error_code,
        error_retryable=error_retryable,
        recovery_action=recovery_action,
    )


def _set_phone_flow_stage(
    phone_flow: dict[str, Any],
    stage: str,
    *,
    status: str | None = None,
    bind_email: str | None = None,
    callback_url: str | None = None,
    callback_source: str | None = None,
    import_submit_ok: bool | None = None,
    import_submit_message: str | None = None,
    cpa_submit_ok: bool | None = None,
    cpa_submit_message: str | None = None,
    last_error: str | None = None,
    error_code: str | None = None,
    error_retryable: bool | None = None,
    recovery_action: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    return oauth_phone_flow_runtime.set_phone_flow_stage(
        phone_flow,
        stage,
        status=status,
        bind_email=bind_email,
        callback_url=callback_url,
        callback_source=callback_source,
        import_submit_ok=import_submit_ok,
        import_submit_message=import_submit_message,
        cpa_submit_ok=cpa_submit_ok,
        cpa_submit_message=cpa_submit_message,
        last_error=last_error,
        error_code=error_code,
        error_retryable=error_retryable,
        recovery_action=recovery_action,
        purpose=purpose,
    )


def _snapshot_phone_flow_attempt(phone_flow: dict[str, Any], *, note: str = "") -> dict[str, Any]:
    return oauth_phone_flow_runtime.snapshot_phone_flow_attempt(phone_flow, note=note)


def _save_partial_hero_phone_bind_result(
    *,
    phone_flow: dict[str, Any],
    password: str,
    note: str = "",
) -> str:
    return oauth_phone_flow_runtime.save_partial_hero_phone_bind_result(
        phone_flow=phone_flow,
        password=password,
        note=note,
        data_dir=DATA_DIR,
    )


def submit_callback_to_cpa_management(
    cpa_url: str,
    cpa_management_key: str,
    callback_or_code: str,
    expected_state: str = "",
) -> dict[str, Any]:
    return oauth_cpa_callback.submit_callback_to_cpa_management(
        cpa_url,
        cpa_management_key,
        callback_or_code,
        expected_state,
    )


def _submit_callback_to_cpa_with_retry(
    cpa_url: str,
    cpa_management_key: str,
    callback_or_code: str,
    *,
    expected_state: str = "",
    max_attempts: int = 3,
    retry_delay: float = 2.0,
) -> dict[str, Any]:
    return oauth_cpa_callback.submit_callback_to_cpa_with_retry(
        cpa_url,
        cpa_management_key,
        callback_or_code,
        expected_state=expected_state,
        max_attempts=max_attempts,
        retry_delay=retry_delay,
        submit_fn=submit_callback_to_cpa_management,
        sleep_fn=time.sleep,
    )


def _prepare_bind_mailbox(mail_config: dict[str, Any] | None, explicit_email: str) -> tuple[str, dict[str, Any] | None]:
    return oauth_add_email_flow.prepare_bind_mailbox(
        mail_config,
        explicit_email,
        create_mailbox_fn=mail_provider.create_mailbox,
    )


def _add_email_headers(
    registrar: PlatformRegistrar,
    referer: str,
    *,
    content_type: str = "application/json",
    include_sentinel: bool = True,
) -> dict[str, str]:
    return oauth_add_email_flow.add_email_headers(
        registrar,
        referer,
        auth_base_value=auth_base,
        common_headers_fn=get_common_headers,
        trace_headers_fn=_make_trace_headers,
        sentinel_token_fn=build_sentinel_token,
        content_type=content_type,
        include_sentinel=include_sentinel,
    )


def _submit_add_email_api(registrar: PlatformRegistrar, email_address: str, referer: str) -> dict[str, Any]:
    return oauth_add_email_flow.submit_add_email_api(
        registrar,
        email_address,
        referer,
        auth_base_value=auth_base,
        add_email_headers_fn=_add_email_headers,
        request_with_retry_fn=request_with_local_retry,
        response_json_fn=_response_json,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
    )


def _add_email_send_attempt_summary(item: dict[str, Any]) -> str:
    return oauth_add_email_flow.add_email_send_attempt_summary(item)


def _validate_add_email_code_api(registrar: PlatformRegistrar, code: str, referer: str, email_address: str = "") -> dict[str, Any]:
    return oauth_add_email_flow.validate_add_email_code_api(
        registrar,
        code,
        referer,
        email_address,
        auth_base_value=auth_base,
        add_email_headers_fn=_add_email_headers,
        request_with_retry_fn=request_with_local_retry,
        response_json_fn=_response_json,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        continue_url_from_step_fn=_continue_url_from_step,
        url_indicates_completion_fn=_add_email_url_indicates_completion,
    )


def _refresh_add_email_code_page(registrar: PlatformRegistrar, current_url: str) -> tuple[str, str]:
    return oauth_add_email_flow.refresh_add_email_code_page(
        registrar,
        current_url,
        auth_base_value=auth_base,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
        trace_headers_fn=_make_trace_headers,
        sentinel_token_fn=build_sentinel_token,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        has_code_form_fn=_has_add_email_code_form,
    )


def _submit_add_email_code_form(
    registrar: PlatformRegistrar,
    *,
    page_url: str,
    page_html: str,
    fallback_action: str,
    code: str,
) -> tuple[str, str]:
    return oauth_add_email_flow.submit_add_email_code_form(
        registrar,
        page_url=page_url,
        page_html=page_html,
        fallback_action=fallback_action,
        code=code,
        extract_code_form_inputs_fn=_extract_add_email_code_form_inputs,
        post_form_and_follow_fn=_post_form_and_follow,
        result_url_fn=_add_email_result_url,
    )


def _continue_url_from_step(info: dict[str, Any], fallback: str = "") -> str:
    return oauth_add_email_flow.continue_url_from_step(info, fallback, auth_base_value=auth_base)


def _add_email_result_url(final_url: str, body_text: str = "") -> str:
    return oauth_add_email_flow.add_email_result_url(
        final_url,
        body_text,
        iter_flow_candidates_fn=_iter_flow_url_candidates,
        flow_url_priority_fn=_flow_url_priority,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        choose_preferred_flow_url_fn=_choose_preferred_flow_url,
    )


def _is_add_email_page_url(url: str) -> bool:
    return oauth_add_email_flow.is_add_email_page_url(url)


def _add_email_url_indicates_completion(url: str) -> bool:
    return oauth_add_email_flow.add_email_url_indicates_completion(
        url,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
    )


def _has_add_email_code_form(html_text: str) -> bool:
    return oauth_add_email_flow.has_add_email_code_form(
        html_text,
        extract_code_form_inputs_fn=_extract_add_email_code_form_inputs,
    )


def _bind_email_wait_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return oauth_add_email_flow.bind_email_wait_config(config)


def _continue_with_optional_add_email(
    registrar: PlatformRegistrar,
    *,
    continue_url: str,
    bind_email: str = "",
    bind_email_code: str = "",
    bind_mail_config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    return oauth_add_email_flow.continue_with_optional_add_email(
        registrar,
        continue_url=continue_url,
        bind_email=bind_email,
        bind_email_code=bind_email_code,
        bind_mail_config=bind_mail_config,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
        response_json_fn=_response_json,
        is_add_email_page_url_fn=_is_add_email_page_url,
        prepare_bind_mailbox_fn=_prepare_bind_mailbox,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        extract_form_inputs_fn=_extract_form_inputs,
        post_form_and_follow_fn=_post_form_and_follow,
        submit_add_email_api_fn=_submit_add_email_api,
        send_attempt_summary_fn=_add_email_send_attempt_summary,
        continue_url_from_step_fn=_continue_url_from_step,
        refresh_code_page_fn=_refresh_add_email_code_page,
        wait_for_code_fn=mail_provider.wait_for_code,
        bind_email_wait_config_fn=_bind_email_wait_config,
        has_code_form_fn=_has_add_email_code_form,
        submit_code_form_fn=_submit_add_email_code_form,
        url_indicates_completion_fn=_add_email_url_indicates_completion,
        validate_code_api_fn=_validate_add_email_code_api,
        now_ms_fn=lambda: int(time.time() * 1000),
    )


def legacy_phone_login_bind_email_and_submit(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("legacy_flow_removed_use_sub2api_only")


def legacy_login_and_submit(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("legacy_flow_removed_use_sub2api_only")


def run_oauth_token_flow(
    config: Sub2APIOAuthFlowConfig,
    *,
    callback_or_code: str,
    phone_number: str = "",
    hero_sms_order_id: str = "",
    hero_sms_price: float | None = None,
    email_password: str = "",
    mail_provider_name: str = "",
) -> Sub2APIOAuthFlowResult:
    prepared = build_openai_oauth_authorize_url(config)
    callback_url, code = normalize_callback_url(callback_or_code, prepared.redirect_uri, prepared.state)
    result = exchange_callback_code(config, prepared, callback_or_code)
    if not result.ok:
        return Sub2APIOAuthFlowResult(
            ok=False,
            authorize_url=prepared.authorize_url,
            callback_url=callback_url,
            code=code,
            error=result.error,
            tokens=result,
        )
    payload = build_sub2api_import_payload(
        result,
        concurrency=config.concurrency,
        priority=config.priority,
        rate_multiplier=config.rate_multiplier,
        auto_pause_on_expired=config.auto_pause_on_expired,
        organization_id=config.organization_id,
        plan_type=config.plan_type,
        privacy_mode=config.privacy_mode,
        account_name=config.account_name,
    )
    archive = build_account_archive(
        prepared,
        result,
        callback_url=callback_url,
        phone_number=phone_number,
        phone_country=(config.hero_sms.country if config.hero_sms else "CO"),
        hero_sms_order_id=hero_sms_order_id,
        hero_sms_price=hero_sms_price,
        email_password=email_password,
        mail_provider_name=mail_provider_name,
        organization_id=config.organization_id,
        plan_type=config.plan_type,
    )
    export_path = save_sub2api_export(payload, config.export_name)
    archive_path = save_account_archive(archive, config.archive_name)
    saved_result = save_result(result)
    return Sub2APIOAuthFlowResult(
        ok=True,
        authorize_url=prepared.authorize_url,
        callback_url=callback_url,
        code=code,
        export_path=str(export_path),
        archive_path=str(archive_path),
        saved_result=str(saved_result),
        email=result.email,
        payload=payload,
        archive=archive,
        tokens=result,
    )


def generate_authorize_bundle(config: Sub2APIOAuthFlowConfig) -> dict[str, Any]:
    """Return a JSON-serializable authorize bundle for manual/browser-assisted OAuth."""
    prepared = build_openai_oauth_authorize_url(config)
    return asdict(prepared)
