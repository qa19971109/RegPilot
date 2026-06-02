from __future__ import annotations

import argparse
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .accounts_store import get_account, init_db
from .config import DATA_DIR
from . import microsoft_mail_pool
from .api_tasks import (
    JOBS,
    _hero_phone_bind,
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
from .api_reauthorization import (
    router as reauthorization_router,
    _reauthorize_account_log_line,
    api_reauthorize,
    api_reauthorize_auto,
    api_reauthorize_auto_job,
    api_reauthorize_finish,
)
from .api_task_routes import (
    router as task_router,
    _hero_country_lookup,
    _hero_price_lookup,
    _phone_direct,
    api_hero_countries,
    api_sms_countries,
    api_sms_price,
    api_task_hero_phone_bind,
    api_task_phone_direct,
    api_task_register,
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
app.include_router(task_router)


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




app.include_router(reauthorization_router)
app.include_router(accounts_router)


if __name__ == "__main__":
    main()
