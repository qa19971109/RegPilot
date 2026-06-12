from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReauthorizeLocalTokenDeps:
    auth_base: str
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    registration_result_cls: Callable[..., Any]
    platform_registrar_cls: Callable[[str], Any]
    response_json_fn: Callable[[Any], dict[str, Any]]
    decode_jwt_payload_fn: Callable[[str], dict[str, Any]]


def direct_exchange_local_callback(
    config: Any,
    prepared: Any,
    callback_url: str,
    *,
    email: str,
    password: str,
    mailbox: dict[str, Any],
    deps: ReauthorizeLocalTokenDeps,
) -> Any:
    params = deps.callback_params_from_url_fn(callback_url) or {}
    code = str(params.get("code") or "").strip()
    if not code:
        return deps.registration_result_cls(ok=False, email=email, password=password, mailbox=mailbox, callback_url=callback_url, error="local_callback_code_missing")
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": prepared.redirect_uri,
        "client_id": prepared.client_id,
        "code_verifier": prepared.code_verifier,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    response = None
    data: dict[str, Any] = {}
    registrar = deps.platform_registrar_cls(str(getattr(config, "proxy", "") or "").strip())
    try:
        response = registrar.session.post(
            f"{deps.auth_base}/oauth/token",
            headers=headers,
            data=payload,
            verify=False,
            timeout=60,
        )
        data = deps.response_json_fn(response)
    except Exception as exc:
        registrar.close()
        return deps.registration_result_cls(ok=False, email=email, password=password, mailbox=mailbox, callback_url=callback_url, error=f"direct_oauth_token_failed:{exc or 'request_failed'}")
    finally:
        try:
            registrar.close()
        except Exception:
            pass
    if response is None:
        return deps.registration_result_cls(ok=False, email=email, password=password, mailbox=mailbox, callback_url=callback_url, error="direct_oauth_token_failed:request_failed")
    if response.status_code != 200:
        detail = str(data.get("error") or data.get("error_description") or data.get("message") or "").strip() if isinstance(data, dict) else ""
        return deps.registration_result_cls(ok=False, email=email, password=password, mailbox=mailbox, callback_url=callback_url, error=f"direct_oauth_token_http_{response.status_code}:{detail}")
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    id_token = str(data.get("id_token") or "").strip()
    if not access_token or not refresh_token or not id_token:
        return deps.registration_result_cls(ok=False, email=email, password=password, mailbox=mailbox, callback_url=callback_url, error="direct_oauth_token_missing_tokens")
    payload = deps.decode_jwt_payload_fn(id_token) or deps.decode_jwt_payload_fn(access_token) or {}
    return deps.registration_result_cls(
        ok=True,
        email=str(payload.get("email") or email).strip(),
        password=password,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        mailbox=mailbox,
        callback_url=callback_url,
    )
