from __future__ import annotations

from dataclasses import dataclass
from typing import Any


__all__ = [
    "AccountInspectionSummary",
    "inspection_item_from_result",
    "summarize_inspection_items",
]


@dataclass(frozen=True)
class AccountInspectionSummary:
    checked_count: int = 0
    ok_count: int = 0
    failed_count: int = 0
    unauthorized_count: int = 0
    reauthorized_count: int = 0
    delete_marked_count: int = 0


def inspection_item_from_result(account: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    account_id = str(account.get("id") or "")
    email = str(account.get("email") or "").strip() or account_id
    return {
        "account_id": account_id,
        "email": email,
        "ok": bool(result.get("ok")),
        "auth_index": str(result.get("auth_index") or ""),
        "auth_name": str(result.get("auth_name") or ""),
        "auth_disabled": bool(result.get("auth_disabled")),
        "usage_state": str(result.get("usage_state") or ""),
        "recommended_action": str(result.get("recommended_action") or ""),
        "used_percent": result.get("used_percent"),
        "weekly_used_percent": result.get("weekly_used_percent"),
        "five_hour_used_percent": result.get("five_hour_used_percent"),
        "status_code": int(result.get("status_code") or 0),
        "latency_ms": int(result.get("latency_ms") or 0),
        "message": str(result.get("error") or result.get("text") or ""),
        "action": str(result.get("action") or "checked"),
    }


def summarize_inspection_items(items: list[dict[str, Any]]) -> AccountInspectionSummary:
    ok_count = 0
    failed_count = 0
    unauthorized_count = 0
    reauthorized_count = 0
    delete_marked_count = 0
    neutral_actions = {"cpa_auth_disabled", "cpa_keep"}
    for item in items:
        if item.get("ok") or item.get("action") == "reauthorized":
            ok_count += 1
        elif item.get("action") not in neutral_actions:
            failed_count += 1
        if item.get("action") in {"reauthorized", "reauthorize_failed", "delete_pending"} or int(item.get("status_code") or 0) == 401:
            unauthorized_count += 1
        if item.get("action") == "reauthorized":
            reauthorized_count += 1
        if item.get("action") == "delete_pending":
            delete_marked_count += 1
    return AccountInspectionSummary(
        checked_count=len(items),
        ok_count=ok_count,
        failed_count=failed_count,
        unauthorized_count=unauthorized_count,
        reauthorized_count=reauthorized_count,
        delete_marked_count=delete_marked_count,
    )
