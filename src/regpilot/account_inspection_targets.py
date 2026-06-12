from __future__ import annotations

from typing import Any, Callable


__all__ = [
    "accounts_for_inspection",
    "inspection_account_ids",
    "local_accounts_for_inspection",
]


GetAccount = Callable[[str], dict[str, Any] | None]
CountAccounts = Callable[[], int]
ListAccounts = Callable[..., list[dict[str, Any]]]


def inspection_account_ids(account_ids: list[Any] | None, account_id: Any = "") -> list[str]:
    ids = [str(item).strip() for item in (account_ids or []) if str(item).strip()]
    single = str(account_id or "").strip()
    if single and single not in ids:
        ids.append(single)
    return ids


def _account_pool_limit(total: Any, *, cap: int = 10000) -> int:
    try:
        count = int(total or 0)
    except (TypeError, ValueError, OverflowError):
        count = 0
    return max(1, min(cap, count or cap))


def local_accounts_for_inspection(*, count_accounts: CountAccounts, list_accounts: ListAccounts, limit_cap: int = 10000) -> list[dict[str, Any]]:
    total = count_accounts()
    return list_accounts(limit=_account_pool_limit(total, cap=limit_cap), offset=0)


def accounts_for_inspection(
    *,
    account_ids: list[Any] | None,
    account_id: Any,
    get_account: GetAccount,
    count_accounts: CountAccounts,
    list_accounts: ListAccounts,
    limit_cap: int = 10000,
) -> list[dict[str, Any]]:
    ids = inspection_account_ids(account_ids, account_id)
    if ids:
        return [item for item in (get_account(current_id) for current_id in ids) if item]
    return local_accounts_for_inspection(count_accounts=count_accounts, list_accounts=list_accounts, limit_cap=limit_cap)
