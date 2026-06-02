from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .api_config_values import (
    _merge_task_values,
    _preflight_hero_phone_bind,
    _preflight_phone_direct,
    _preflight_register,
    _preflight_sms_lookup,
)
from .api_models import TaskRunRequest
from .api_tasks import (
    _hero_country_lookup,
    _hero_price_lookup,
    _phone_direct,
    _run_job,
    _run_register,
)


router = APIRouter()


@router.post("/api/tasks/register")
def api_task_register(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("register", payload.values or {})
    _preflight_register(merged)
    return _run_job("register", _run_register, merged)


@router.post("/api/tasks/hero/phone-bind")
def api_task_hero_phone_bind(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("hero_phone_bind", payload.values or {})
    _preflight_hero_phone_bind(merged)
    return _run_job("phone_direct", _phone_direct, merged)


@router.post("/api/tasks/phone-direct")
def api_task_phone_direct(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("phone_direct", payload.values or {})
    _preflight_phone_direct(merged)
    return _run_job("phone_direct", _phone_direct, merged)


@router.post("/api/hero/countries")
def api_hero_countries(payload: TaskRunRequest) -> dict[str, Any]:
    return api_sms_countries(payload)


@router.post("/api/sms/countries")
def api_sms_countries(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("hero_phone_bind", payload.values or {})
    _preflight_sms_lookup(merged)
    try:
        return _hero_country_lookup(merged)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sms/price")
def api_sms_price(payload: TaskRunRequest) -> dict[str, Any]:
    merged = _merge_task_values("hero_phone_bind", payload.values or {})
    _preflight_sms_lookup(merged)
    try:
        return _hero_price_lookup(merged)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
