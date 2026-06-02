from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .accounts_store import get_account
from .api_config_values import (
    _prefer_codex2api_admin_key,
    _prefer_codex2api_proxy_url,
    _prefer_codex2api_url,
    _prefer_proxy,
    _prefer_reauthorize_sms_values,
)
from .api_models import ReauthorizeAutoRequest, ReauthorizeFinishRequest, ReauthorizeRequest
from .api_presenters import _zh_job_message
from .api_tasks import _run_job
from .reauthorize import auto_reauthorize_account_with_email_otp, finish_account_reauthorize, start_account_reauthorize


router = APIRouter()


@router.post("/api/accounts/reauthorize")
def api_reauthorize(payload: ReauthorizeRequest) -> dict[str, Any]:
    outcome = start_account_reauthorize(payload.account_id, proxy=_prefer_proxy(payload.proxy))
    if not outcome.ok and outcome.message == "account_not_found":
        raise HTTPException(status_code=404, detail="account_not_found")
    return {
        "ok": outcome.ok,
        "message": outcome.message,
        "item": outcome.account,
        "authorize_url": outcome.authorize_url,
        "state": outcome.state,
        "nonce": outcome.nonce,
        "redirect_uri": outcome.redirect_uri,
        "client_id": outcome.client_id,
        "code_verifier": outcome.code_verifier,
        "bind_email": outcome.bind_email,
    }


@router.post("/api/accounts/reauthorize/finish")
def api_reauthorize_finish(payload: ReauthorizeFinishRequest) -> dict[str, Any]:
    outcome = finish_account_reauthorize(
        payload.account_id,
        callback_or_code=payload.callback_or_code,
        code_verifier=payload.code_verifier,
        state=payload.state,
        redirect_uri=payload.redirect_uri,
        client_id=payload.client_id,
        codex2api_url=_prefer_codex2api_url(payload.codex2api_url),
        codex2api_admin_key=_prefer_codex2api_admin_key(payload.codex2api_admin_key),
        codex2api_proxy_url=_prefer_codex2api_proxy_url(payload.codex2api_proxy_url),
        proxy=_prefer_proxy(payload.proxy),
    )
    if not outcome.ok and outcome.message == "account_not_found":
        raise HTTPException(status_code=404, detail="account_not_found")
    return {
        "ok": outcome.ok,
        "message": outcome.message,
        "item": outcome.account,
        "callback_url": outcome.callback_url,
        "cpa_import_submit_ok": outcome.codex2api_import_submit_ok,
        "cpa_import_submit_message": outcome.codex2api_import_submit_message,
        "codex2api_import_submit_ok": outcome.codex2api_import_submit_ok,
        "codex2api_import_submit_message": outcome.codex2api_import_submit_message,
    }


def _reauthorize_account_log_line(account_id: str) -> str:
    account = get_account(account_id) or {}
    account_email = str(account.get("email") or "").strip()
    if account_email:
        return f"阶段：账号：{account_email}（ID：{account_id}）"
    return f"阶段：账号ID：{account_id}"


@router.post("/api/accounts/reauthorize/auto/job")
def api_reauthorize_auto_job(payload: ReauthorizeAutoRequest) -> dict[str, Any]:
    sms_values = _prefer_reauthorize_sms_values(payload)

    def run() -> dict[str, Any]:
        print("阶段：开始重新授权")
        print(_reauthorize_account_log_line(payload.account_id))
        print(f"阶段：CPA 地址：{_prefer_codex2api_url(payload.codex2api_url)}")
        outcome = auto_reauthorize_account_with_email_otp(
            payload.account_id,
            codex2api_url=_prefer_codex2api_url(payload.codex2api_url),
            codex2api_admin_key=_prefer_codex2api_admin_key(payload.codex2api_admin_key),
            codex2api_proxy_url=_prefer_codex2api_proxy_url(payload.codex2api_proxy_url),
            proxy=_prefer_proxy(payload.proxy),
            wait_timeout=payload.wait_timeout,
            wait_interval=payload.wait_interval,
            request_timeout=payload.request_timeout,
            allow_phone_verification=bool(payload.allow_phone_verification),
            **sms_values,
        )
        print(f"阶段：重新授权任务结束：{_zh_job_message(outcome.message)}")
        if outcome.debug:
            try:
                slim = {k: v for k, v in outcome.debug.items() if k in {"validate_otp_summary", "resume_probe", "callback_summary", "codex2api_oauth", "cpa_oauth", "consent_direct_summary", "phone_verification_after_password_summary", "phone_verification_after_email_otp_summary", "phone_verification_after_pre_password_email_otp_summary"}}
                if slim:
                    print("阶段：调试摘要已生成，敏感字段已隐藏")
            except Exception as exc:
                print(f"阶段：调试摘要生成失败：{exc}")
        return {
            "ok": outcome.ok,
            "message": outcome.message,
            "item": outcome.account,
            "callback_url": outcome.callback_url,
            "code": outcome.code,
            "cpa_import_submit_ok": outcome.codex2api_import_submit_ok,
            "cpa_import_submit_message": outcome.codex2api_import_submit_message,
            "codex2api_import_submit_ok": outcome.codex2api_import_submit_ok,
            "codex2api_import_submit_message": outcome.codex2api_import_submit_message,
        }
    return _run_job("reauthorize", run)


@router.post("/api/accounts/reauthorize/auto")
def api_reauthorize_auto(payload: ReauthorizeAutoRequest) -> dict[str, Any]:
    sms_values = _prefer_reauthorize_sms_values(payload)
    outcome = auto_reauthorize_account_with_email_otp(
        payload.account_id,
        codex2api_url=_prefer_codex2api_url(payload.codex2api_url),
        codex2api_admin_key=_prefer_codex2api_admin_key(payload.codex2api_admin_key),
        codex2api_proxy_url=_prefer_codex2api_proxy_url(payload.codex2api_proxy_url),
        proxy=_prefer_proxy(payload.proxy),
        wait_timeout=payload.wait_timeout,
        wait_interval=payload.wait_interval,
        request_timeout=payload.request_timeout,
        allow_phone_verification=bool(payload.allow_phone_verification),
        **sms_values,
    )
    if not outcome.ok and outcome.message == "account_not_found":
        raise HTTPException(status_code=404, detail="account_not_found")
    return {
        "ok": outcome.ok,
        "message": outcome.message,
        "item": outcome.account,
        "callback_url": outcome.callback_url,
        "code": outcome.code,
        "cpa_import_submit_ok": outcome.codex2api_import_submit_ok,
        "cpa_import_submit_message": outcome.codex2api_import_submit_message,
        "codex2api_import_submit_ok": outcome.codex2api_import_submit_ok,
        "codex2api_import_submit_message": outcome.codex2api_import_submit_message,
        "debug": outcome.debug,
    }

