from __future__ import annotations

import re
from typing import Any

import requests
from fastapi import APIRouter, HTTPException

from .accounts_store import count_accounts, delete_account, delete_accounts, get_account, list_accounts, upsert_account
from .account_status import _account_phone_status, _safe_account_with_status
from .api_config_values import _prefer_proxy
from .api_models import AccountDeleteRequest, AccountUpsertRequest
from .register_core import _decode_jwt_payload, auth_base, platform_oauth_client_id


router = APIRouter()


@router.get("/api/accounts")
def api_list_accounts(limit: int = 200, offset: int = 0, q: str = "") -> dict[str, Any]:
    limit = min(500, max(1, int(limit)))
    offset = max(0, int(offset))
    search = str(q or "").strip()
    return {
        "ok": True,
        "items": [_safe_account_with_status(item) for item in list_accounts(limit=limit, offset=offset, search=search)],
        "limit": limit,
        "offset": offset,
        "total": count_accounts(search=search),
        "q": search,
    }


@router.get("/api/accounts/{account_id}")
def api_get_account(account_id: str) -> dict[str, Any]:
    item = get_account(account_id)
    if not item:
        raise HTTPException(status_code=404, detail="account_not_found")
    return {"ok": True, "item": _safe_account_with_status(item)}


def _iso_from_jwt_exp(value: Any) -> str:
    try:
        import datetime as _dt

        exp = int(value or 0)
        if exp <= 0:
            return ""
        return _dt.datetime.fromtimestamp(exp, _dt.timezone(_dt.timedelta(hours=8))).replace(microsecond=0).isoformat()
    except Exception:
        return ""


def _refresh_account_tokens(item: dict[str, Any]) -> dict[str, Any]:
    refresh_token = str(item.get("refresh_token") or "").strip()
    if not refresh_token:
        raise HTTPException(status_code=400, detail="account_has_no_refresh_token")
    proxy = _prefer_proxy("")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        response = requests.post(
            f"{auth_base}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": platform_oauth_client_id,
            },
            timeout=60,
            verify=False,
            proxies=proxies,
        )
    except requests.RequestException as exc:
        failed = dict(item)
        failed["status"] = "auth_failed"
        failed["last_error"] = "refresh_token_failed:request_failed"
        try:
            upsert_account(failed)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=failed["last_error"]) from exc
    try:
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if response.status_code != 200:
        message = ""
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        if isinstance(error, dict):
            message = str(error.get("code") or error.get("message") or "").strip()
        failed = dict(item)
        failed["status"] = "auth_failed"
        failed["last_error"] = f"refresh_token_failed:{message or response.status_code}"
        try:
            upsert_account(failed)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=failed["last_error"])
    access_token = str(data.get("access_token") or "").strip()
    new_refresh_token = str(data.get("refresh_token") or refresh_token).strip()
    id_token = str(data.get("id_token") or item.get("id_token") or "").strip()
    if not access_token or not new_refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token_missing_tokens")
    updated = dict(item)
    updated["access_token"] = access_token
    updated["refresh_token"] = new_refresh_token
    updated["id_token"] = id_token
    updated["status"] = "authorized"
    updated["last_error"] = ""
    saved = upsert_account(updated)
    return saved or updated


@router.get("/api/accounts/{account_id}/export-json")
def api_export_account_json(account_id: str) -> dict[str, Any]:
    item = get_account(account_id)
    if not item:
        raise HTTPException(status_code=404, detail="account_not_found")
    if not str(item.get("access_token") or "").strip() or not str(item.get("refresh_token") or "").strip():
        raise HTTPException(status_code=400, detail="account_has_no_local_token")
    item = _refresh_account_tokens(item)
    phone_status = _account_phone_status(item)
    if phone_status.get("phone_status") not in {"verified", "bound_recorded", "authorized_assumed"}:
        raise HTTPException(status_code=400, detail="phone_not_verified_reauthorize_after_binding_required")
    access_token = str(item.get("access_token") or "")
    id_token = str(item.get("id_token") or "")
    access_payload = _decode_jwt_payload(access_token) or {}
    id_payload = _decode_jwt_payload(id_token) or {}
    profile = access_payload.get("https://api.openai.com/profile") if isinstance(access_payload.get("https://api.openai.com/profile"), dict) else {}
    auth_claim = id_payload.get("https://api.openai.com/auth") if isinstance(id_payload.get("https://api.openai.com/auth"), dict) else {}
    orgs = auth_claim.get("organizations") if isinstance(auth_claim.get("organizations"), list) else []
    default_org = next((org for org in orgs if isinstance(org, dict) and org.get("is_default")), orgs[0] if orgs else {})
    email = str(item.get("email") or profile.get("email") or id_payload.get("email") or "")
    expired = _iso_from_jwt_exp(access_payload.get("exp"))
    payload = {
        "access_token": access_token,
        "account_id": str((item.get("mailbox") or {}).get("account_id") or default_org.get("id") or "") if isinstance(item.get("mailbox"), dict) or isinstance(default_org, dict) else "",
        "disabled": False,
        "email": email,
        "expired": expired,
        "id_token": id_token,
        "last_refresh": str(item.get("last_auth_at") or item.get("updated_at") or ""),
        "plan_type": "free",
        "refresh_token": str(item.get("refresh_token") or ""),
        "type": "codex",
    }
    safe_email = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(item.get("email") or account_id)).strip("_") or account_id
    return {"ok": True, "filename": f"cpa-{safe_email}.json", "payload": payload}


@router.post("/api/accounts")
def api_upsert_account(payload: AccountUpsertRequest) -> dict[str, Any]:
    item = upsert_account(payload.model_dump())
    return {"ok": True, "item": _safe_account_with_status(item)}


@router.post("/api/accounts/delete")
def api_delete_accounts(payload: AccountDeleteRequest) -> dict[str, Any]:
    result = delete_accounts(payload.ids)
    return {"ok": True, **result}


@router.delete("/api/accounts/{account_id}")
def api_delete_account(account_id: str) -> dict[str, Any]:
    ok = delete_account(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="account_not_found")
    return {"ok": True, "id": account_id}
