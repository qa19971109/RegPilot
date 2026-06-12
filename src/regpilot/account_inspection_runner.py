from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from fastapi import HTTPException


@dataclass(frozen=True)
class AccountInspectionRunDeps:
    inspection_account_ids: Callable[[Any], list[str]]
    accounts_for_inspection: Callable[[Any], list[dict[str, Any]]]
    inspection_accounts_from_cpa_auth_files: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    cpa_auth_files: Callable[..., list[dict[str, Any]]]
    cpa_auth_test: Callable[[dict[str, Any], Any, list[dict[str, Any]]], dict[str, Any]]
    codex_account_test: Callable[[dict[str, Any], Any], dict[str, Any]]
    inspection_item_from_result: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    summarize_inspection_items: Callable[[list[dict[str, Any]]], Any]
    inspection_needs_reauthorize: Callable[[dict[str, Any]], bool]
    message_requires_delete_mark: Callable[[Any], bool]
    mark_account_delete_pending: Callable[[dict[str, Any] | None, str], dict[str, Any]]
    get_account: Callable[[str], dict[str, Any] | None]
    auto_reauthorize_account_with_email_otp: Callable[..., Any]
    prefer_proxy: Callable[[str], str]
    prefer_codex2api_url: Callable[[str], str]
    prefer_codex2api_admin_key: Callable[[str], str]
    prefer_codex2api_proxy_url: Callable[[str], str]
    zh_job_message: Callable[[Any], str]


@dataclass(frozen=True)
class AccountInspectionTargetLoad:
    accounts: list[dict[str, Any]]
    auth_files: list[dict[str, Any]]
    use_cpa_test: bool
    target_source: str
    error_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class AccountInspectionRuntime:
    payload: Any
    sms_values: dict[str, Any]
    auth_files: list[dict[str, Any]]
    use_cpa_test: bool
    reauthorize_lock: Lock


def _inspection_source_label(value: str) -> str:
    return {
        "cpa_auth_files": "CPA 认证文件",
        "selected_accounts": "勾选账号",
        "account_pool": "账号池",
    }.get(str(value or ""), str(value or "-"))


def _load_account_inspection_targets(payload: Any, deps: AccountInspectionRunDeps) -> AccountInspectionTargetLoad:
    selected_ids = deps.inspection_account_ids(payload)
    auth_files: list[dict[str, Any]] = []
    use_cpa_test = bool(payload.use_cpa_test)
    if use_cpa_test:
        cpa_url = deps.prefer_codex2api_url(payload.codex2api_url)
        cpa_key = deps.prefer_codex2api_admin_key(payload.codex2api_admin_key)
        print(f"巡检：读取 CPA 认证文件：{cpa_url or '-'}")
        try:
            auth_files = deps.cpa_auth_files(cpa_url, cpa_key, timeout=max(1, int(payload.request_timeout or 30)))
            print(f"巡检：读取到 {len(auth_files)} 个 CPA 认证文件")
        except HTTPException as exc:
            message = str(exc.detail or "cpa_auth_files_load_failed")
            print(f"巡检：读取 CPA 认证文件失败：{message}")
            return AccountInspectionTargetLoad(
                accounts=[],
                auth_files=[],
                use_cpa_test=True,
                target_source="selected_accounts" if selected_ids else "cpa_auth_files",
                error_response={"ok": False, "message": message, "checked_count": 0, "items": []},
            )
        accounts = deps.accounts_for_inspection(payload) if selected_ids else deps.inspection_accounts_from_cpa_auth_files(auth_files)
        return AccountInspectionTargetLoad(
            accounts=accounts,
            auth_files=auth_files,
            use_cpa_test=True,
            target_source="selected_accounts" if selected_ids else "cpa_auth_files",
        )
    return AccountInspectionTargetLoad(
        accounts=deps.accounts_for_inspection(payload),
        auth_files=[],
        use_cpa_test=False,
        target_source="selected_accounts" if selected_ids else "account_pool",
    )


def _account_inspection_test_result(
    account: dict[str, Any],
    account_id: str,
    email: str,
    runtime: AccountInspectionRuntime,
    deps: AccountInspectionRunDeps,
) -> dict[str, Any]:
    try:
        if runtime.use_cpa_test:
            return deps.cpa_auth_test(account, runtime.payload, runtime.auth_files)
        return deps.codex_account_test(account, runtime.payload)
    except HTTPException as exc:
        return {
            "ok": False,
            "account_id": account_id,
            "email": email,
            "status_code": 0,
            "latency_ms": 0,
            "error": str(exc.detail or exc),
        }


def _reauthorize_inspection_account(
    account_id: str,
    email: str,
    item: dict[str, Any],
    runtime: AccountInspectionRuntime,
    deps: AccountInspectionRunDeps,
) -> dict[str, Any]:
    payload = runtime.payload
    item["action"] = "reauthorize_started"
    print(f"巡检：401，开始重新授权：{email}")
    with runtime.reauthorize_lock:
        print(f"巡检：等待重授权队列：{email}")
        outcome = deps.auto_reauthorize_account_with_email_otp(
            account_id,
            codex2api_url=deps.prefer_codex2api_url(payload.codex2api_url),
            codex2api_admin_key=deps.prefer_codex2api_admin_key(payload.codex2api_admin_key),
            codex2api_proxy_url=deps.prefer_codex2api_proxy_url(payload.codex2api_proxy_url),
            proxy=deps.prefer_proxy(payload.proxy),
            wait_timeout=payload.wait_timeout,
            wait_interval=payload.wait_interval,
            request_timeout=payload.request_timeout,
            allow_phone_verification=bool(payload.allow_phone_verification),
            **runtime.sms_values,
        )
    item["reauthorize_ok"] = bool(outcome.ok)
    item["reauthorize_message"] = str(outcome.message or "")
    item["message"] = str(outcome.message or item["message"])
    if outcome.ok:
        item["action"] = "reauthorized"
        print(f"巡检：重授权完成：{email}")
    elif deps.message_requires_delete_mark(outcome.message):
        marked = deps.mark_account_delete_pending(
            outcome.account or deps.get_account(account_id),
            str(outcome.message or "manual_phone_verification_required"),
        )
        item["action"] = "delete_pending"
        item["status"] = str(marked.get("status") or "")
        print(f"巡检：需要手机二验，已标记待删除：{email}")
    else:
        item["action"] = "reauthorize_failed"
        print(f"巡检：重授权失败：{email}：{deps.zh_job_message(outcome.message)}")
    return item


def _skip_inspection_reauthorize(item: dict[str, Any], email: str, *, use_cpa_test: bool) -> dict[str, Any]:
    item["action"] = "cpa_auth_invalid" if use_cpa_test else "failed_no_reauthorize"
    item["message"] = "401 unauthorized; automatic reauthorization is disabled"
    print(f"巡检：401，未勾选自动重授权：{email}")
    return item


def _inspect_account(account: dict[str, Any], runtime: AccountInspectionRuntime, deps: AccountInspectionRunDeps) -> dict[str, Any]:
    account_id = str(account.get("id") or "")
    email = str(account.get("email") or "").strip() or account_id
    print(f"巡检：检测账号：{email}")
    result = _account_inspection_test_result(account, account_id, email, runtime, deps)
    item = deps.inspection_item_from_result(account, result)
    if result.get("ok"):
        print(f"巡检：通过：{email}（HTTP {item['status_code']}）")
        return item
    if not deps.inspection_needs_reauthorize(result):
        if item["action"] == "checked":
            item["action"] = "failed_no_reauthorize"
        print(f"巡检：失败非 401：{email}（HTTP {item['status_code']}）")
        return item
    if not account_id:
        item["action"] = "cpa_unauthorized_no_local_account"
        item["message"] = "CPA auth file returned 401, but no matching RegPilot account was found for automatic reauthorization"
        print(f"巡检：401，但未匹配本地账号：{email}")
        return item
    if not bool(getattr(runtime.payload, "auto_reauthorize", False)):
        return _skip_inspection_reauthorize(item, email, use_cpa_test=runtime.use_cpa_test)
    return _reauthorize_inspection_account(account_id, email, item, runtime, deps)


def _run_inspection_workers(
    accounts_to_check: list[dict[str, Any]],
    workers: int,
    runtime: AccountInspectionRuntime,
    deps: AccountInspectionRunDeps,
) -> list[dict[str, Any]]:
    if workers == 1 or len(accounts_to_check) <= 1:
        return [_inspect_account(account, runtime, deps) for account in accounts_to_check]
    items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_inspect_account, account, runtime, deps) for account in accounts_to_check]
        for future in as_completed(futures):
            items.append(future.result())
    return items


def _account_inspection_response(
    items: list[dict[str, Any]],
    *,
    target_source: str,
    workers: int,
    use_cpa_test: bool,
    deps: AccountInspectionRunDeps,
) -> dict[str, Any]:
    summary = deps.summarize_inspection_items(items)
    print(
        f"巡检完成：通过 {summary.ok_count}，失败 {summary.failed_count}，"
        f"401 {summary.unauthorized_count}，重授权 {summary.reauthorized_count}，待删除 {summary.delete_marked_count}"
    )
    return {
        "ok": True,
        "message": "account_inspection_finished",
        "checked_count": summary.checked_count,
        "target_source": target_source,
        "threads": workers,
        "use_cpa_test": use_cpa_test,
        "ok_count": summary.ok_count,
        "failed_count": summary.failed_count,
        "unauthorized_count": summary.unauthorized_count,
        "reauthorized_count": summary.reauthorized_count,
        "delete_marked_count": summary.delete_marked_count,
        "items": items,
    }


def run_account_inspection(payload: Any, sms_values: dict[str, Any], deps: AccountInspectionRunDeps) -> dict[str, Any]:
    target_load = _load_account_inspection_targets(payload, deps)
    if target_load.error_response is not None:
        return target_load.error_response
    print(f"巡检开始：来源 {_inspection_source_label(target_load.target_source)}，目标 {len(target_load.accounts)} 个")
    if not target_load.accounts:
        return {"ok": True, "message": "no_accounts", "checked_count": 0, "items": [], "target_source": target_load.target_source}
    workers = max(1, min(50, int(payload.threads or 1)))
    runtime = AccountInspectionRuntime(
        payload=payload,
        sms_values=sms_values,
        auth_files=target_load.auth_files,
        use_cpa_test=target_load.use_cpa_test,
        reauthorize_lock=Lock(),
    )
    items = _run_inspection_workers(target_load.accounts, workers, runtime, deps)
    return _account_inspection_response(
        items,
        target_source=target_load.target_source,
        workers=workers,
        use_cpa_test=target_load.use_cpa_test,
        deps=deps,
    )
