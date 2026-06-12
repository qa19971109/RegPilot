from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from fastapi import HTTPException


__all__ = [
    "CpaAuthActionContext",
    "cpa_auth_action_context",
    "normalize_cpa_auth_action",
    "resolve_cpa_action_target",
    "run_cpa_auth_delete_action",
    "run_cpa_auth_status_action",
    "sync_local_account_after_cpa_status",
]


GetAccount = Callable[[str], dict[str, Any] | None]
UpsertAccount = Callable[[dict[str, Any]], dict[str, Any]]
DeleteAccount = Callable[[str], bool]
AccountAuthFileMatcher = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any] | None]
RequestCpa = Callable[..., dict[str, Any]]
SyncLocalStatus = Callable[[str, str], None]


@dataclass(frozen=True)
class CpaAuthActionContext:
    action: str
    cpa_url: str
    cpa_key: str
    auth_index: str
    name: str

    @property
    def target_name(self) -> str:
        return self.name or self.auth_index


def normalize_cpa_auth_action(action: Any) -> str:
    normalized = str(action or "").strip().lower()
    if normalized not in {"enable", "disable", "delete"}:
        raise HTTPException(status_code=400, detail="invalid_cpa_auth_action")
    return normalized


def resolve_cpa_action_target(
    *,
    account_id: str,
    auth_index: str,
    name: str,
    auth_files: list[dict[str, Any]],
    get_account: GetAccount,
    account_cpa_auth_file: AccountAuthFileMatcher,
) -> dict[str, str]:
    auth_index = str(auth_index or "").strip()
    name = str(name or "").strip()
    if auth_index or name:
        return {"auth_index": auth_index, "name": name}
    account = get_account(str(account_id or "").strip())
    if not account:
        raise HTTPException(status_code=404, detail="account_not_found")
    auth_file = account_cpa_auth_file(account, auth_files)
    if not auth_file:
        raise HTTPException(status_code=400, detail="cpa_auth_file_not_found")
    return {
        "auth_index": str(auth_file.get("auth_index") or ""),
        "name": str(auth_file.get("name") or ""),
    }


def cpa_auth_action_context(
    *,
    action: Any,
    account_id: str,
    auth_index: str,
    name: str,
    cpa_url: str,
    cpa_key: str,
    auth_files: list[dict[str, Any]],
    get_account: GetAccount,
    account_cpa_auth_file: AccountAuthFileMatcher,
) -> CpaAuthActionContext:
    normalized_action = normalize_cpa_auth_action(action)
    target = resolve_cpa_action_target(
        account_id=account_id,
        auth_index=auth_index,
        name=name,
        auth_files=auth_files,
        get_account=get_account,
        account_cpa_auth_file=account_cpa_auth_file,
    )
    return CpaAuthActionContext(
        action=normalized_action,
        cpa_url=cpa_url,
        cpa_key=cpa_key,
        auth_index=str(target.get("auth_index") or ""),
        name=str(target.get("name") or ""),
    )


def sync_local_account_after_cpa_status(
    *,
    account_id: str,
    action: str,
    get_account: GetAccount,
    upsert_account: UpsertAccount,
) -> None:
    if not account_id:
        return
    account = get_account(account_id)
    if not account:
        return
    updated = dict(account)
    updated["status"] = "cpa_disabled" if action == "disable" else "authorized"
    updated["last_error"] = "cpa_auth_disabled" if action == "disable" else ""
    upsert_account(updated)


def run_cpa_auth_status_action(
    *,
    context: CpaAuthActionContext,
    account_id: str,
    cpa_request: RequestCpa,
    sync_local_status: SyncLocalStatus,
) -> dict[str, Any]:
    body = {"name": context.target_name, "disabled": context.action == "disable"}
    data = cpa_request("PATCH", context.cpa_url, context.cpa_key, "/v0/management/auth-files/status", json_body=body)
    sync_local_status(account_id, context.action)
    return {"ok": True, "action": context.action, "auth_index": context.auth_index, "name": context.target_name, "result": data}


def run_cpa_auth_delete_action(
    *,
    context: CpaAuthActionContext,
    account_id: str,
    cpa_request: RequestCpa,
    delete_account: DeleteAccount,
) -> dict[str, Any]:
    delete_name = context.target_name
    if not delete_name:
        raise HTTPException(status_code=400, detail="cpa_auth_file_not_found")
    data = cpa_request("DELETE", context.cpa_url, context.cpa_key, f"/v0/management/auth-files?name={quote(delete_name)}")
    local_account_deleted = False
    if account_id:
        local_account_deleted = delete_account(account_id)
    return {
        "ok": True,
        "action": context.action,
        "auth_index": context.auth_index,
        "name": context.target_name,
        "result": data,
        "local_account_deleted": local_account_deleted,
    }
