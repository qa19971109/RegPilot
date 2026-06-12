from __future__ import annotations

import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

import requests
import urllib3

from .config import DATA_DIR, RegisterConfig, parse_bool
from .json_store import write_json_atomic
from .logging_utils import log
from .registration_about_you import (
    about_you_consent_fields,
    about_you_create_account_payloads,
    about_you_page_shape,
    about_you_shape_log_summary,
    age_from_birthdate,
)
from .registration_consent import (
    extract_attr as _consent_extract_attr,
    extract_consent_form_inputs,
    org_project_from_consent_html,
    strip_html_tags,
    workspace_id_from_consent_html,
)
from .registration_artifacts import save_about_you_failure_artifacts, save_about_you_presubmit_artifacts
from .registration_identity import random_birthdate, random_name, random_password
from .registration_responses import accounts_error_code, response_error_summary, response_json
from .registration_sentinel import SentinelTokenGenerator, build_sentinel_token
from . import registration_account_api
from . import registration_consent_flow
from . import registration_http
from .jwt_utils import decode_jwt_payload
from .registration_session import (
    cookie_snapshot,
    decode_client_auth_session_value,
    extract_workspace_id_from_client_auth_session,
    find_org_project_from_auth_session_node,
    find_workspace_id_from_auth_session_node,
    get_session_workspace_id,
    safe_cookie_get,
    summarize_cookie_snapshot,
    workspace_id_from_client_auth_session_cookie,
)
from .registration_callback import (
    extract_oauth_callback_params_from_response as _callback_params_from_response,
    extract_oauth_callback_params_from_text as _callback_params_from_text,
    extract_oauth_callback_params_from_url as _callback_params_from_url,
)
from . import registration_cpa_oauth_helpers
from . import registration_oauth_helpers
from . import registration_token_exchange
from . import registration_email_state_machine
from .registration_state import (
    brief_flow_url as _state_brief_flow_url,
    registration_continue_url,
    registration_expected_state,
    registration_page_context,
    registration_state_from_info,
)
from .registration_environment import (
    DEFAULT_ENV_ACCEPT_LANGUAGE_POOL,
    DEFAULT_ENV_TIMEZONE_POOL,
    DEFAULT_ENV_UA_POOL,
    DEFAULT_ENV_VIEWPORT_POOL,
    EnvironmentProfile,
    _ENV_LOCK,
    _apply_environment_state,
    _build_common_headers,
    _build_navigate_headers,
    _chrome_full_from_ua,
    _chrome_major_from_ua,
    _default_environment_profile,
    _parse_viewport,
    _restore_environment_state,
    _sec_ch_ua_for_major,
    _sec_ch_ua_full_version_list,
    _snapshot_environment_state,
    _split_pool_text,
    _viewport_pool_from_text,
    build_environment_profile,
    environment_profile_context,
    get_accept_language,
    get_common_headers,
    get_navigate_headers,
    get_sec_ch_ua,
    get_sec_ch_ua_full_version_list,
    get_timezone,
    get_user_agent,
    get_viewport_height,
    get_viewport_width,
    prepare_environment_profile_from_config,
    prepare_environment_profile_from_payload,
    summarize_environment_profile,
    __getattr__ as _environment_getattr,
)
from . import mail_provider

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

auth_base = "https://auth.openai.com"
platform_base = "https://platform.openai.com"
platform_oauth_client_id = "app_EMoamEEZ73f0CkXaXp7hrann"
platform_oauth_redirect_uri = f"{platform_base}/auth/callback"
platform_oauth_audience = "https://api.openai.com/v1"
chatgpt_signup_client_id = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
chatgpt_signup_redirect_uri = "https://chatgpt.com/api/auth/callback/openai"
chatgpt_signup_scope = "openid email profile offline_access model.request model.read organization.read organization.write"
default_timeout = 30
def __getattr__(name: str) -> Any:
    return _environment_getattr(name)


@dataclass
class RegistrationResult:
    ok: bool
    email: str = ""
    password: str = ""
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    mailbox: dict[str, Any] | None = None
    callback_url: str = ""
    error: str = ""


def _make_trace_headers() -> dict[str, str]:
    return registration_http.make_trace_headers()


def _generate_pkce() -> tuple[str, str]:
    return registration_http.generate_pkce()


def _random_password(length: int = 16) -> str:
    return random_password(length)


def _random_name() -> tuple[str, str]:
    return random_name()


def _random_birthdate() -> str:
    return random_birthdate()


def _age_from_birthdate(birthdate: str, today: date | None = None) -> int:
    return age_from_birthdate(birthdate, today)


def _about_you_page_shape(page_context: str = "") -> dict[str, Any]:
    return about_you_page_shape(page_context)


def _about_you_shape_log_summary(page_context: str = "") -> str:
    return about_you_shape_log_summary(page_context)


def _about_you_consent_fields(page_context: str = "") -> dict[str, str]:
    return about_you_consent_fields(page_context)


def _about_you_create_account_payloads(name: str, birthdate: str, page_context: str = "", email: str = "") -> list[dict[str, Any]]:
    return about_you_create_account_payloads(name, birthdate, page_context, email)


def _response_json(resp) -> dict:
    return response_json(resp)


def _accounts_error_code(info: dict[str, Any]) -> str:
    return accounts_error_code(info)


def _decode_jwt_payload(token: str) -> dict:
    return decode_jwt_payload(token)


def _merge_url_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value is None:
            continue
        query[key] = [str(value)]
    merged = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, merged, parsed.fragment))


def _safe_cookie_get(session: Any, name: str, *preferred_domains: str) -> str:
    return safe_cookie_get(session, name, *preferred_domains)


def _cookie_snapshot(session: Any) -> dict[str, str]:
    return cookie_snapshot(session)


def _summarize_cookie_snapshot(snapshot: dict[str, str]) -> dict[str, Any]:
    return summarize_cookie_snapshot(snapshot)


def _extract_workspace_id_from_client_auth_session(raw: str) -> str:
    return extract_workspace_id_from_client_auth_session(raw)


def _decode_client_auth_session_value(raw: Any) -> dict[str, Any]:
    return decode_client_auth_session_value(raw)


def _get_session_workspace_id(session: Any) -> str:
    return get_session_workspace_id(session)


def create_mailbox(config: RegisterConfig, username: str | None = None) -> dict:
    return mail_provider.create_mailbox(asdict(config.mail), username)


def wait_for_code(config: RegisterConfig, mailbox: dict) -> str | None:
    return mail_provider.wait_for_code(asdict(config.mail), mailbox)


def _is_socks_proxy(proxy: str) -> bool:
    return registration_http.is_socks_proxy(proxy)


def create_session(proxy: str = "") -> Any:
    return registration_http.create_session(proxy)


def request_with_local_retry(session: requests.Session, method: str, url: str, retry_attempts: int = 3, **kwargs):
    return registration_http.request_with_local_retry(
        session,
        method,
        url,
        default_timeout=default_timeout,
        retry_attempts=retry_attempts,
        **kwargs,
    )


def validate_otp(session: requests.Session, device_id: str, code: str):
    return registration_http.validate_otp(
        session,
        device_id,
        code,
        auth_base=auth_base,
        request_with_retry_fn=request_with_local_retry,
        trace_headers_fn=_make_trace_headers,
        sentinel_builder_fn=build_sentinel_token,
    )


def extract_oauth_callback_params_from_url(url: str) -> dict[str, str] | None:
    return _callback_params_from_url(url)


def _extract_oauth_callback_params_from_text(text: str) -> dict[str, str] | None:
    return _callback_params_from_text(text, platform_base=platform_base)


def _extract_oauth_callback_params_from_response(response: requests.Response | None) -> dict[str, str] | None:
    return _callback_params_from_response(response, platform_base=platform_base)


def _find_workspace_id_from_auth_session_node(node: Any) -> str:
    return find_workspace_id_from_auth_session_node(node)


def _find_org_project_from_auth_session_node(node: Any) -> tuple[str, str]:
    return find_org_project_from_auth_session_node(node)


def _consent_flow_deps() -> registration_consent_flow.ConsentFlowDeps:
    return registration_consent_flow.ConsentFlowDeps(
        auth_base=auth_base,
        get_common_headers=get_common_headers,
        get_navigate_headers=get_navigate_headers,
        get_user_agent=get_user_agent,
        make_trace_headers=_make_trace_headers,
        build_sentinel_token=build_sentinel_token,
        response_json=_response_json,
        callback_params_from_response=_extract_oauth_callback_params_from_response,
        workspace_id_from_client_auth_session_cookie=_workspace_id_from_client_auth_session_cookie,
        workspace_id_from_consent_html=_workspace_id_from_consent_html,
        org_project_from_consent_html=_org_project_from_consent_html,
        extract_consent_form_inputs=_extract_consent_form_inputs,
        find_workspace_id_from_auth_session_node=_find_workspace_id_from_auth_session_node,
        find_org_project_from_auth_session_node=_find_org_project_from_auth_session_node,
    )


def _registration_account_api_deps() -> registration_account_api.RegistrationAccountApiDeps:
    return registration_account_api.RegistrationAccountApiDeps(
        auth_base=auth_base,
        platform_oauth_client_id=platform_oauth_client_id,
        platform_oauth_audience=platform_oauth_audience,
        platform_oauth_redirect_uri=platform_oauth_redirect_uri,
        chatgpt_signup_client_id=chatgpt_signup_client_id,
        chatgpt_signup_redirect_uri=chatgpt_signup_redirect_uri,
        chatgpt_signup_scope=chatgpt_signup_scope,
        merge_url_query=_merge_url_query,
        request_with_retry=request_with_local_retry,
        response_json=_response_json,
        cookie_snapshot=_cookie_snapshot,
        summarize_cookie_snapshot=_summarize_cookie_snapshot,
        navigate_headers=get_navigate_headers,
        trace_headers=_make_trace_headers,
        token_urlsafe=secrets.token_urlsafe,
        uuid4=uuid.uuid4,
        generate_pkce=_generate_pkce,
        quote=requests.utils.quote,
    )


def _registration_response_probe(resp: Any, fallback_url: str) -> dict[str, Any]:
    return registration_account_api.response_probe(resp, fallback_url, _response_json)


def _submit_organization_select_for_consent(
    session: requests.Session,
    org_id: str,
    project_id: str,
    headers: dict[str, str],
    referer: str,
    debug_steps: list[dict[str, Any]] | None,
    source: str,
) -> dict[str, str] | None:
    return registration_consent_flow.submit_organization_select_for_consent(
        session,
        org_id,
        project_id,
        headers,
        referer,
        debug_steps,
        source,
        deps=_consent_flow_deps(),
    )


def _workspace_id_from_client_auth_session_cookie(session: Any) -> str:
    return workspace_id_from_client_auth_session_cookie(session)


def _workspace_id_from_consent_html(text: str) -> str:
    return workspace_id_from_consent_html(text)


def _org_project_from_consent_html(text: str) -> tuple[str, str]:
    return org_project_from_consent_html(text)


def _extract_attr(attrs: str, name: str) -> str:
    return _consent_extract_attr(attrs, name)


def _strip_html_tags(value: str) -> str:
    return strip_html_tags(value)


def _extract_consent_form_inputs(html_text: str) -> tuple[str, dict[str, str]]:
    return extract_consent_form_inputs(html_text)


def _append_consent_debug(debug_steps: list[dict[str, Any]] | None, **step: Any) -> None:
    registration_consent_flow.append_consent_debug(debug_steps, **step)


def _build_authorize_continue_headers(session: requests.Session, device_id: str, referer: str) -> dict[str, str]:
    return registration_consent_flow.build_authorize_continue_headers(
        session,
        device_id,
        referer,
        deps=_consent_flow_deps(),
    )


def _consent_data_payloads(workspace_id: str, state: str) -> list[dict[str, str]]:
    return registration_consent_flow.consent_data_payloads(workspace_id, state)


def _submit_workspace_select_from_consent_form(
    session: requests.Session,
    workspace_id: str,
    headers: dict[str, str],
    consent_url: str,
    debug_steps: list[dict[str, Any]] | None,
    source: str,
) -> dict[str, str] | None:
    return registration_consent_flow.submit_workspace_select_from_consent_form(
        session,
        workspace_id,
        headers,
        consent_url,
        debug_steps,
        source,
        deps=_consent_flow_deps(),
    )


def _har_like_browser_fetch_headers(referer: str, *, accept: str = "application/json", content_type: str = "application/json") -> dict[str, str]:
    return registration_consent_flow.har_like_browser_fetch_headers(
        referer,
        accept=accept,
        content_type=content_type,
        deps=_consent_flow_deps(),
    )


def _consent_response_summary(response: Any, *, method: str, target: str, source: str = "") -> dict[str, Any]:
    return registration_consent_flow.consent_response_summary(
        response,
        method=method,
        target=target,
        source=source,
        deps=_consent_flow_deps(),
    )


def extract_oauth_callback_params_from_consent_session(session: requests.Session, consent_url: str, device_id: str, state: str = "", debug_steps: list[dict[str, Any]] | None = None) -> dict[str, str] | None:
    return registration_consent_flow.extract_oauth_callback_params_from_consent_session(
        session,
        consent_url,
        device_id,
        state=state,
        debug_steps=debug_steps,
        deps=_consent_flow_deps(),
    )


def exchange_platform_tokens(session: requests.Session, device_id: str, code_verifier: str, consent_url: str, proxy: str = "") -> RegistrationResult:
    callback_params = extract_oauth_callback_params_from_consent_session(session, consent_url, device_id)
    if not callback_params:
        try:
            r = session.get(consent_url, headers=get_navigate_headers(), allow_redirects=True, verify=False, timeout=30)
            callback_params = _extract_oauth_callback_params_from_response(r)
            if not callback_params:
                for hist in getattr(r, "history", []) or []:
                    callback_params = _extract_oauth_callback_params_from_response(hist)
                    if callback_params:
                        break
        except Exception as exc:
            return RegistrationResult(ok=False, error=f"consent_redirect_failed:{exc}")
    if not callback_params:
        return RegistrationResult(ok=False, error="missing_oauth_callback")
    code = str(callback_params.get("code") or "").strip()
    resp = create_session(proxy).post(
        f"{auth_base}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": platform_oauth_redirect_uri,
            "client_id": platform_oauth_client_id,
            "code_verifier": code_verifier,
        },
        verify=False,
        timeout=60,
    )
    data = _response_json(resp)
    if resp.status_code != 200:
        return RegistrationResult(ok=False, callback_url=consent_url, error=f"oauth_token_http_{resp.status_code}")
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    id_token = str(data.get("id_token") or "").strip()
    if not access_token or not refresh_token or not id_token:
        return RegistrationResult(ok=False, callback_url=consent_url, error="missing_tokens")
    payload = _decode_jwt_payload(id_token) or _decode_jwt_payload(access_token)
    return RegistrationResult(
        ok=True,
        email=str(payload.get("email") or "").strip(),
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        callback_url=consent_url,
    )


def save_result(result: RegistrationResult) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "last_result.json"
    write_json_atomic(path, asdict(result))
    return path


def _save_about_you_failure_artifacts(
    *,
    state: dict[str, Any] | None,
    create_info: dict[str, Any] | None,
    page_snapshot: dict[str, Any] | None,
    page_context: str = "",
) -> dict[str, str]:
    return save_about_you_failure_artifacts(
        DATA_DIR,
        state=state,
        create_info=create_info,
        page_snapshot=page_snapshot,
        page_context=page_context,
    )


def _save_about_you_presubmit_artifacts(
    *,
    state: dict[str, Any] | None,
    page_snapshot: dict[str, Any] | None,
    page_context: str = "",
) -> dict[str, str]:
    return save_about_you_presubmit_artifacts(
        DATA_DIR,
        state=state,
        page_snapshot=page_snapshot,
        page_context=page_context,
    )


def _response_error_summary(prefix: str, info: dict[str, Any]) -> str:
    return response_error_summary(prefix, info)


def _failed_registration_result(
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    callback_url: str,
    error: str,
) -> RegistrationResult:
    result = RegistrationResult(
        ok=False,
        email=email,
        password=password,
        mailbox=mailbox,
        callback_url=callback_url,
        error=error,
    )
    save_result(result)
    return result


class PlatformRegistrar:
    def __init__(self, proxy: str = "") -> None:
        self.session = create_session(proxy)
        self.device_id = str(uuid.uuid4())
        self.proxy = proxy
        self.last_authorize: dict[str, Any] = {}
        self.sentinel_tokens: dict[str, str] = {}

    def close(self) -> None:
        self.session.close()

    def get_workspace_id(self) -> str:
        return _get_session_workspace_id(self.session)

    def start_phone_signup(self, phone_number: str = "") -> dict[str, str]:
        return registration_account_api.start_phone_signup(
            self,
            phone_number,
            deps=_registration_account_api_deps(),
        )

    def start_email_signup(self, email: str) -> dict[str, str]:
        return registration_account_api.start_email_signup(
            self,
            email,
            deps=_registration_account_api_deps(),
        )

    def start_authorize(self, email: str, authorize_url: str = "", screen_hint: str = "login_or_signup") -> dict[str, str]:
        return registration_account_api.start_authorize(
            self,
            email,
            authorize_url,
            screen_hint,
            deps=_registration_account_api_deps(),
        )

    def _ensure_sentinel_token(self, flow: str) -> str:
        cached = str(self.sentinel_tokens.get(flow) or "").strip()
        if cached:
            return cached
        token = build_sentinel_token(self.session, self.device_id, flow)
        self.sentinel_tokens[flow] = token
        return token

    def _build_accounts_headers(self, referer_path: str, flow: str) -> dict[str, str]:
        headers = get_common_headers()
        headers["referer"] = f"{auth_base}{referer_path}"
        headers["oai-device-id"] = self.device_id
        headers["accept"] = "application/json"
        headers["x-requested-with"] = "XMLHttpRequest"
        headers["sec-fetch-site"] = "same-origin"
        headers["sec-fetch-mode"] = "cors"
        headers["sec-fetch-dest"] = "empty"
        headers.update(_make_trace_headers())
        try:
            headers["OpenAI-Sentinel-Token"] = self._ensure_sentinel_token(flow)
        except Exception as exc:
            headers["x-openai-sentinel-error"] = str(exc)
        return headers

    def _post_accounts_payload(self, payload: dict[str, Any], referer_path: str, candidates: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        return registration_account_api.post_accounts_payload(
            self,
            payload,
            referer_path,
            candidates,
            deps=_registration_account_api_deps(),
        )

    def establish_signup_session(self) -> dict[str, Any]:
        return registration_account_api.establish_signup_session(
            self,
            deps=_registration_account_api_deps(),
        )

    def create_account_start(self, email: str) -> dict[str, Any]:
        identifier = str(email or "").strip()
        identifier_kind = "phone" if re.fullmatch(r"\+?[0-9]{6,20}", re.sub(r"\s+", "", identifier)) else "email"
        username_payload = {"value": identifier, "kind": identifier_kind}
        if identifier_kind == "phone":
            username_payload["phone_country_code"] = "AUTO"
        payload = {
            "origin_page_type": "create_account_start",
            "data": {
                "kind": "username",
                "username": username_payload,
            },
        }
        state = self.last_authorize.get("state") or ""
        return self._post_accounts_payload(payload, f"/u/signup/identifier?state={state}")

    def register_user(self, email: str, password: str) -> dict[str, Any]:
        headers = get_common_headers()
        headers["referer"] = f"{auth_base}/create-account/password"
        headers["oai-device-id"] = self.device_id
        headers.update(_make_trace_headers())
        try:
            headers["openai-sentinel-token"] = self._ensure_sentinel_token("username_password_create")
        except Exception as exc:
            headers["x-openai-sentinel-error"] = str(exc)
        resp, error = request_with_local_retry(
            self.session,
            "post",
            f"{auth_base}/api/accounts/user/register",
            json={"username": email, "password": password},
            headers=headers,
            verify=False,
        )
        probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/user/register")
        return {
            "ok": resp is not None and 200 <= probe["status"] < 300,
            "status": probe["status"],
            "json": probe["json"],
            "text": probe["text"],
            "error": error,
            "final_url": probe["final_url"],
            "payload": {"username": email, "password": password},
            "authorize": self.last_authorize,
            "sentinel_token_present": bool(headers.get("openai-sentinel-token") or headers.get("OpenAI-Sentinel-Token")),
            "sentinel_error": headers.get("x-openai-sentinel-error") or "",
        }

    def send_otp(self) -> dict[str, Any]:
        headers = get_navigate_headers()
        headers["referer"] = f"{auth_base}/create-account/password"
        resp, error = request_with_local_retry(
            self.session,
            "get",
            f"{auth_base}/api/accounts/email-otp/send",
            headers=headers,
            allow_redirects=True,
            verify=False,
        )
        probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/email-otp/send")
        return {
            "ok": resp is not None and probe["status"] in (200, 302),
            "status": probe["status"],
            "json": probe["json"],
            "text": probe["text"],
            "error": error,
            "final_url": probe["final_url"],
            "authorize": self.last_authorize,
        }

    def send_phone_otp(self) -> dict[str, Any]:
        headers = get_navigate_headers()
        headers["referer"] = f"{auth_base}/create-account/phone-verification"
        resp, error = request_with_local_retry(
            self.session,
            "get",
            f"{auth_base}/api/accounts/phone-otp/send",
            headers=headers,
            allow_redirects=True,
            verify=False,
        )
        probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/phone-otp/send")
        return {
            "ok": resp is not None and probe["status"] in (200, 302),
            "status": probe["status"],
            "json": probe["json"],
            "text": probe["text"],
            "error": error,
            "final_url": probe["final_url"],
            "authorize": self.last_authorize,
        }

    def resend_phone_otp(self) -> dict[str, Any]:
        return self.send_phone_otp()

    def validate_signup_otp(self, code: str) -> dict[str, Any]:
        resp, error = validate_otp(self.session, self.device_id, code)
        probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/email-otp/validate")
        return {
            "ok": resp is not None and 200 <= probe["status"] < 300,
            "status": probe["status"],
            "json": probe["json"],
            "text": probe["text"],
            "error": error,
            "final_url": probe["final_url"],
            "authorize": self.last_authorize,
        }

    def validate_phone_signup_otp(self, code: str) -> dict[str, Any]:
        headers = get_common_headers()
        headers["referer"] = f"{auth_base}/create-account/phone-verification"
        headers["oai-device-id"] = self.device_id
        headers.update(_make_trace_headers())
        try:
            headers["openai-sentinel-token"] = self._ensure_sentinel_token("authorize_continue")
        except Exception:
            pass
        resp, error = request_with_local_retry(
            self.session,
            "post",
            f"{auth_base}/api/accounts/phone-otp/validate",
            json={"code": str(code).strip()},
            headers=headers,
            verify=False,
        )
        probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/phone-otp/validate")
        return {
            "ok": resp is not None and 200 <= probe["status"] < 300,
            "status": probe["status"],
            "json": probe["json"],
            "text": probe["text"],
            "error": error,
            "final_url": probe["final_url"],
            "authorize": self.last_authorize,
        }

    def create_account(self, name: str, birthdate: str, referer: str = "", page_context: str = "", email: str = "") -> dict[str, Any]:
        headers = get_common_headers()
        headers["referer"] = str(referer or f"{auth_base}/about-you")
        headers["oai-device-id"] = self.device_id
        headers.update(_make_trace_headers())
        try:
            headers["openai-sentinel-token"] = self._ensure_sentinel_token("oauth_create_account")
        except Exception as exc:
            headers["x-openai-sentinel-error"] = str(exc)
        payloads = _about_you_create_account_payloads(name, birthdate, page_context, email=email)
        attempts: list[dict[str, Any]] = []
        last_resp = None
        last_error = ""
        for payload in payloads:
            resp, error = request_with_local_retry(
                self.session,
                "post",
                f"{auth_base}/api/accounts/create_account",
                json=payload,
                headers=headers,
                verify=False,
                allow_redirects=False,
            )
            attempt_probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/create_account")
            status = attempt_probe["status"]
            body = attempt_probe["json"]
            attempt_error_code = _accounts_error_code({"json": body})
            attempts.append({"keys": list(payload.keys()), "status": status, "ok": resp is not None and 200 <= status < 400, "error_code": attempt_error_code})
            last_resp = resp
            last_error = error
            if resp is not None and 200 <= status < 400:
                break
            if attempt_error_code == "registration_disallowed":
                break
        resp = last_resp
        error = last_error
        probe = _registration_response_probe(resp, f"{auth_base}/api/accounts/create_account")
        return {
            "ok": resp is not None and 200 <= probe["status"] < 400,
            "status": probe["status"],
            "json": probe["json"],
            "text": probe["text"],
            "error": error,
            "final_url": probe["final_url"],
            "location": probe["location"],
            "payload": payloads[min(len(attempts), len(payloads)) - 1] if attempts else {},
            "payload_attempts": attempts,
            "referer": headers.get("referer") or "",
            "authorize": self.last_authorize,
            "sentinel_token_present": bool(headers.get("openai-sentinel-token") or headers.get("OpenAI-Sentinel-Token")),
            "sentinel_error": headers.get("x-openai-sentinel-error") or "",
        }

    def exchange_platform_tokens(self, code_verifier: str, callback_url: str) -> RegistrationResult:
        return exchange_platform_tokens(self.session, self.device_id, code_verifier, callback_url, self.proxy)


def _registration_continue_url(info: dict[str, Any]) -> str:
    return registration_continue_url(info)


def _registration_page_context(info: dict[str, Any]) -> str:
    return registration_page_context(info)


def _registration_state_from_info(info: dict[str, Any]) -> dict[str, str]:
    return registration_state_from_info(info)


def _load_registration_state(registrar: PlatformRegistrar, url: str) -> dict[str, Any]:
    from .oauth_token_flow import _load_continue_page

    return registration_oauth_helpers.load_registration_state(
        registrar,
        url,
        load_continue_page_fn=_load_continue_page,
        state_from_info_fn=_registration_state_from_info,
    )


def _registration_expected_state(registrar: PlatformRegistrar, start_info: dict[str, Any], create_info: dict[str, Any]) -> str:
    return registration_expected_state(registrar, start_info, create_info)


def _resolve_registration_post_create_url(
    registrar: PlatformRegistrar,
    *,
    start_info: dict[str, Any],
    create_info: dict[str, Any],
    fallback_url: str = "",
) -> str:
    from .oauth_token_flow import _resolve_oauth_callback

    return registration_oauth_helpers.resolve_registration_post_create_url(
        registrar,
        start_info=start_info,
        create_info=create_info,
        fallback_url=fallback_url,
        continue_url_fn=_registration_continue_url,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        expected_state_fn=_registration_expected_state,
        resolve_oauth_callback_fn=_resolve_oauth_callback,
        log_fn=log,
    )


def _wait_email_otp_with_resend(config: RegisterConfig, registrar: PlatformRegistrar, mailbox: dict, *, resend_on_miss: bool = True) -> str:
    return registration_oauth_helpers.wait_email_otp_with_resend(
        config,
        registrar,
        mailbox,
        resend_on_miss=resend_on_miss,
        wait_for_code_fn=wait_for_code,
        now_ms_fn=lambda: int(time.time() * 1000),
        response_error_summary_fn=_response_error_summary,
        log_fn=log,
    )


def _brief_flow_url(url: str) -> str:
    return _state_brief_flow_url(url)


def _follow_chatgpt_signup_callback(registrar: PlatformRegistrar, callback_url: str) -> dict[str, Any]:
    return registration_oauth_helpers.follow_chatgpt_signup_callback(
        registrar,
        callback_url,
        chatgpt_signup_redirect_uri_value=chatgpt_signup_redirect_uri,
        auth_base_value=auth_base,
        request_with_retry_fn=request_with_local_retry,
        navigate_headers_fn=get_navigate_headers,
    )


def _registration_token_exchange_deps() -> registration_token_exchange.RegistrationTokenExchangeDeps:
    return registration_token_exchange.RegistrationTokenExchangeDeps(
        asdict_fn=asdict,
        brief_flow_url_fn=_brief_flow_url,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        chatgpt_signup_redirect_uri=chatgpt_signup_redirect_uri,
        cpa_oauth_helpers_module=registration_cpa_oauth_helpers,
        follow_chatgpt_signup_callback_fn=_follow_chatgpt_signup_callback,
        log_fn=log,
        parse_bool_fn=parse_bool,
        registration_result_cls=RegistrationResult,
        registration_state_from_info_fn=_registration_state_from_info,
    )

def _exchange_registered_account_tokens(
    *,
    config: RegisterConfig,
    registrar: PlatformRegistrar,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    code_verifier: str,
    callback_url: str,
) -> RegistrationResult:
    return registration_token_exchange.exchange_registered_account_tokens(
        config=config,
        registrar=registrar,
        email=email,
        password=password,
        mailbox=mailbox,
        code_verifier=code_verifier,
        callback_url=callback_url,
        deps=_registration_token_exchange_deps(),
    )


def _finalize_registration_result(config: RegisterConfig, registrar: PlatformRegistrar, result: RegistrationResult, email: str, mailbox: dict[str, Any]) -> RegistrationResult:
    workspace_id = registrar.get_workspace_id() if hasattr(registrar, "get_workspace_id") else ""
    if workspace_id:
        mailbox["account_id"] = workspace_id
        log(f"workspace/account id captured: {workspace_id}")
    result.mailbox = mailbox
    if result.ok and not mailbox.get("_cpa_submit_ok") and parse_bool(getattr(config, "codex2api_auto_import", False), key="codex2api_auto_import") and str(config.codex2api_url or "").strip() and str(config.codex2api_admin_key or "").strip():
        try:
            from .oauth_token_flow import import_result_to_codex2api

            codex2api_result = import_result_to_codex2api(
                result,
                codex2api_url=config.codex2api_url,
                admin_key=config.codex2api_admin_key,
                account_name=result.email or email,
                proxy_url=str(config.codex2api_proxy_url or ""),
            )
            mailbox["_codex2api_submit_ok"] = bool(codex2api_result.get("ok"))
            mailbox["_codex2api_submit_message"] = str(codex2api_result.get("message") or "")
            log(f"Codex2API auto import completed: {mailbox['_codex2api_submit_message']}")
        except Exception as exc:
            mailbox["_codex2api_submit_ok"] = False
            mailbox["_codex2api_submit_message"] = str(exc)
            log(f"Codex2API auto import failed: {exc}")
    save_result(result)
    try:
        from .accounts_store import save_registration_result_to_account

        save_registration_result_to_account(result, source="register")
    except Exception:
        pass
    return result


def _registration_email_state_machine_deps() -> registration_email_state_machine.RegistrationEmailStateMachineDeps:
    return registration_email_state_machine.RegistrationEmailStateMachineDeps(
        accounts_error_code_fn=_accounts_error_code,
        about_you_shape_log_summary_fn=_about_you_shape_log_summary,
        auth_base=auth_base,
        brief_flow_url_fn=_brief_flow_url,
        callback_params_from_url_fn=extract_oauth_callback_params_from_url,
        exchange_registered_account_tokens_fn=_exchange_registered_account_tokens,
        failed_registration_result_fn=_failed_registration_result,
        finalize_registration_result_fn=_finalize_registration_result,
        load_registration_state_fn=_load_registration_state,
        log_fn=log,
        registration_continue_url_fn=_registration_continue_url,
        registration_page_context_fn=_registration_page_context,
        registration_state_from_info_fn=_registration_state_from_info,
        resolve_registration_post_create_url_fn=_resolve_registration_post_create_url,
        response_error_summary_fn=_response_error_summary,
        save_about_you_failure_artifacts_fn=_save_about_you_failure_artifacts,
        save_about_you_presubmit_artifacts_fn=_save_about_you_presubmit_artifacts,
        time_module=time,
        wait_email_otp_with_resend_fn=_wait_email_otp_with_resend,
    )


def _run_email_registration_state_machine(
    *,
    config: RegisterConfig,
    registrar: PlatformRegistrar,
    mailbox: dict[str, Any],
    email: str,
    password: str,
    full_name: str,
    birthdate: str,
    start_info: dict[str, Any],
) -> RegistrationResult:
    return registration_email_state_machine.run_email_registration_state_machine(
        config=config,
        registrar=registrar,
        mailbox=mailbox,
        email=email,
        password=password,
        full_name=full_name,
        birthdate=birthdate,
        start_info=start_info,
        deps=_registration_email_state_machine_deps(),
    )


@dataclass(frozen=True)
class EmailRegistrationRuntime:
    config: RegisterConfig
    env_profile: EnvironmentProfile
    effective_proxy: str
    mailbox: dict[str, Any]
    email: str
    password: str
    full_name: str
    birthdate: str


def _prepare_email_registration_runtime(config: RegisterConfig) -> EmailRegistrationRuntime:
    env_profile = prepare_environment_profile_from_config(config)
    effective_proxy = str(env_profile.proxy or config.proxy or "").strip()
    mailbox = create_mailbox(config)
    email = str(mailbox.get("email") or "").strip()
    password = str(getattr(config, "default_password", "") or "").strip() or _random_password()
    first_name, last_name = _random_name()
    birthdate = _random_birthdate()
    full_name = f"{first_name} {last_name}"
    log(f"已创建邮箱：{email}")
    log(f"已生成注册资料：密码已生成，姓名={full_name}，生日={birthdate}")
    log(f"本次账号密码：{password}")
    return EmailRegistrationRuntime(
        config=config,
        env_profile=env_profile,
        effective_proxy=effective_proxy,
        mailbox=mailbox,
        email=email,
        password=password,
        full_name=full_name,
        birthdate=birthdate,
    )


def _failed_email_registration(runtime: EmailRegistrationRuntime, *, callback_url: str, error: str) -> RegistrationResult:
    return _failed_registration_result(
        email=runtime.email,
        password=runtime.password,
        mailbox=runtime.mailbox,
        callback_url=callback_url,
        error=error,
    )


def _start_email_registration_entry(
    registrar: PlatformRegistrar,
    runtime: EmailRegistrationRuntime,
) -> tuple[dict[str, Any], RegistrationResult | None]:
    info = registrar.start_email_signup(email=runtime.email)
    authorize_final_url = str(info.get("final_url") or "")
    log(f"邮箱注册入口已打开：status={info.get('status')} final_url={authorize_final_url}")
    if "/error" in authorize_final_url or "authorize_hydra_invalid_request" in authorize_final_url:
        error = "authorize_hydra_invalid_request" if "authorize_hydra_invalid_request" in authorize_final_url else "authorize_failed"
        return info, _failed_email_registration(runtime, callback_url=authorize_final_url, error=error)
    if "/password" in authorize_final_url or "email-verification" in authorize_final_url:
        return info, None
    create_start = registrar.create_account_start(runtime.email)
    log(f"邮箱注册入口初始化结果：status={create_start.get('status')} ok={create_start.get('ok')} final_url={create_start.get('final_url')}")
    if create_start.get("ok"):
        return info, None
    error = _response_error_summary("create_account_start", create_start)
    log(f"邮箱注册入口初始化失败：{error}")
    return info, _failed_email_registration(
        runtime,
        callback_url=str(create_start.get("final_url") or authorize_final_url),
        error=error,
    )


def _establish_email_registration_session(
    registrar: PlatformRegistrar,
    runtime: EmailRegistrationRuntime,
    start_info: dict[str, Any],
) -> RegistrationResult | None:
    establish_info = registrar.establish_signup_session()
    log(f"注册会话建立结果：ok={establish_info.get('ok')} cookies={((establish_info.get('cookie_summary') or {}).get('present') or [])}")
    if establish_info.get("ok"):
        return None
    return _failed_email_registration(
        runtime,
        callback_url=str(start_info.get("final_url") or ""),
        error="session_establishment_failed",
    )


def _run_email_registration_after_session(
    registrar: PlatformRegistrar,
    runtime: EmailRegistrationRuntime,
    start_info: dict[str, Any],
) -> RegistrationResult:
    return _run_email_registration_state_machine(
        config=runtime.config,
        registrar=registrar,
        mailbox=runtime.mailbox,
        email=runtime.email,
        password=runtime.password,
        full_name=runtime.full_name,
        birthdate=runtime.birthdate,
        start_info=start_info,
    )


def run_placeholder(config: RegisterConfig) -> RegistrationResult:
    runtime = _prepare_email_registration_runtime(config)
    registrar: PlatformRegistrar | None = None
    env_lock_acquired = False
    env_snapshot: dict[str, Any] = {}
    try:
        _ENV_LOCK.acquire()
        env_lock_acquired = True
        env_snapshot = _snapshot_environment_state()
        _apply_environment_state(runtime.env_profile)
        log(f"环境模块：{summarize_environment_profile(runtime.env_profile)}")
        registrar = PlatformRegistrar(proxy=runtime.effective_proxy)
        runtime.mailbox["_runtime_proxy"] = runtime.effective_proxy
        runtime.mailbox["_code_after_ts"] = int(time.time() * 1000)
        start_info, failed = _start_email_registration_entry(registrar, runtime)
        if failed is not None:
            return failed
        failed = _establish_email_registration_session(registrar, runtime, start_info)
        if failed is not None:
            return failed
        return _run_email_registration_after_session(registrar, runtime, start_info)
    finally:
        if env_lock_acquired:
            _restore_environment_state(env_snapshot)
            _ENV_LOCK.release()
        if registrar is not None:
            registrar.close()
