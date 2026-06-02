from __future__ import annotations

import argparse
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .accounts_store import get_account, init_db
from .config import DATA_DIR
from . import microsoft_mail_pool
from .reauthorize import auto_reauthorize_account_with_email_otp, finish_account_reauthorize, start_account_reauthorize
from .api_tasks import (
    JOBS,
    _hero_country_lookup,
    _hero_phone_bind,
    _hero_price_lookup,
    _phone_direct,
    _run_job,
    _run_register,
)
from .webui_html import FASTAPI_INDEX_HTML
from .api_models import (
    AccountDeleteRequest,
    AccountUpsertRequest,
    ConfigSaveRequest,
    ReauthorizeAutoRequest,
    ReauthorizeFinishRequest,
    ReauthorizeRequest,
    TaskRunRequest,
)
from .api_microsoft_mail import (
    router as microsoft_mail_router,
    _safe_microsoft_mail_account,
    api_clear_used_microsoft_mail_accounts,
    api_delete_microsoft_mail_account,
    api_import_microsoft_mail_accounts,
    api_list_microsoft_mail_accounts,
    api_upsert_microsoft_mail_account,
)
from .api_accounts import (
    router as accounts_router,
    _iso_from_jwt_exp,
    _refresh_account_tokens,
    api_delete_account,
    api_delete_accounts,
    api_export_account_json,
    api_get_account,
    api_list_accounts,
    api_upsert_account,
)
from .api_presenters import _safe_job, _zh_job_message
from .account_status import _safe_account_with_status
from .api_config_values import (
    _load_webui_config,
    _merge_task_values,
    _preflight_hero_phone_bind,
    _preflight_phone_direct,
    _preflight_register,
    _preflight_sms_lookup,
    _prefer_codex2api_admin_key,
    _prefer_codex2api_proxy_url,
    _prefer_codex2api_url,
    _prefer_proxy,
    _prefer_reauthorize_sms_values,
    _save_webui_config,
)
from .account_inspection import (
    AccountInspectionCpaActionRequest,
    AccountInspectionDeps,
    AccountInspectionRequest,
    configure_account_inspection,
    _run_account_inspection,
    _run_cpa_auth_action,
)
app = FastAPI(title="RegPilot API", version="0.1.0")
app.include_router(microsoft_mail_router)


def main() -> None:
    parser = argparse.ArgumentParser(prog="regpilot-api", description="Run the RegPilot FastAPI server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run("regpilot.api:app", host=args.host, port=args.port)




@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return FASTAPI_INDEX_HTML


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    return {"ok": True, "path": str(DATA_DIR / "webui_config.json"), "config": _load_webui_config()}


@app.post("/api/config")
def api_save_config(payload: ConfigSaveRequest) -> dict[str, Any]:
    requested_section = str(payload.section or "register").strip() or "register"
    if requested_section not in {"register", "phone_direct", "hero_phone_bind", "logs"}:
        raise HTTPException(status_code=400, detail="invalid_config_section")
    section = "hero_phone_bind" if requested_section == "phone_direct" else requested_section
    data = _load_webui_config()
    current = data.get(section) if isinstance(data.get(section), dict) else {}
    merged = dict(current)
    for key, value in (payload.values or {}).items():
        merged[str(key)] = value
    data[section] = merged
    try:
        saved = _save_webui_config(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": str(DATA_DIR / "webui_config.json"), "section": requested_section, "storage_section": section, "config": saved}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "RegPilot API"}


@app.post("/api/accounts/inspection/job")
def api_account_inspection_job(payload: AccountInspectionRequest) -> dict[str, Any]:
    sms_values = _prefer_reauthorize_sms_values(payload)

    def run() -> dict[str, Any]:
        return _run_account_inspection(payload, sms_values)

    return _run_job("account_inspection", run)


@app.post("/api/accounts/inspection/cpa-action")
def api_account_inspection_cpa_action(payload: AccountInspectionCpaActionRequest) -> dict[str, Any]:
    return _run_cpa_auth_action(payload)


@app.post("/api/tasks/register")
def api_task_register(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("register", payload.values or {})
    _preflight_register(merged)
    return _run_job("register", _run_register, merged)


@app.post("/api/tasks/hero/phone-bind")
def api_task_hero_phone_bind(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("hero_phone_bind", payload.values or {})
    _preflight_hero_phone_bind(merged)
    return _run_job("phone_direct", _phone_direct, merged)


@app.post("/api/tasks/phone-direct")
def api_task_phone_direct(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("phone_direct", payload.values or {})
    _preflight_phone_direct(merged)
    return _run_job("phone_direct", _phone_direct, merged)


@app.post("/api/hero/countries")
def api_hero_countries(payload: TaskRunRequest) -> dict[str, Any]:
    return api_sms_countries(payload)


@app.post("/api/sms/countries")
def api_sms_countries(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("hero_phone_bind", payload.values or {})
    _preflight_sms_lookup(merged)
    try:
        return _hero_country_lookup(merged)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sms/price")
def api_sms_price(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("hero_phone_bind", payload.values or {})
    _preflight_sms_lookup(merged)
    try:
        return _hero_price_lookup(merged)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))



configure_account_inspection(
    AccountInspectionDeps(
        prefer_proxy=_prefer_proxy,
        prefer_codex2api_url=_prefer_codex2api_url,
        prefer_codex2api_admin_key=_prefer_codex2api_admin_key,
        prefer_codex2api_proxy_url=_prefer_codex2api_proxy_url,
        refresh_account_tokens=_refresh_account_tokens,
        zh_job_message=_zh_job_message,
    )
)


@app.get("/api/jobs")
def api_jobs() -> dict[str, Any]:
    return {"ok": True, "items": [_safe_job(job) for job in JOBS.list()]}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict[str, Any]:
    for job in JOBS.list():
        if job.get("id") == job_id:
            return {"ok": True, "item": _safe_job(job)}
    raise HTTPException(status_code=404, detail="job_not_found")


@app.post("/api/jobs/{job_id}/stop")
def api_job_stop(job_id: str) -> dict[str, Any]:
    try:
        result = JOBS.request_stop(job_id)
    except ValueError as exc:
        if str(exc) == "job_not_found":
            raise HTTPException(status_code=404, detail="job_not_found")
        raise
    return {"ok": True, **result}


@app.post("/api/accounts/reauthorize")
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


@app.post("/api/accounts/reauthorize/finish")
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


@app.post("/api/accounts/reauthorize/auto/job")
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


@app.post("/api/accounts/reauthorize/auto")
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


app.include_router(accounts_router)


if __name__ == "__main__":
    main()
