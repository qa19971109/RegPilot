from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReauthorizeResultStoreDeps:
    auto_outcome_cls: Callable[..., Any]
    finish_outcome_cls: Callable[..., Any]
    import_result_to_codex2api_fn: Callable[..., dict[str, Any]]
    log_stage_fn: Callable[[str], None]
    now_text_fn: Callable[[], str]
    save_registration_result_to_account_fn: Callable[..., dict[str, Any]]
    upsert_account_fn: Callable[[dict[str, Any]], dict[str, Any]]


def mark_reauthorize_failed(
    account: dict[str, Any],
    message: str,
    *,
    mailbox: dict[str, Any] | None = None,
    deps: ReauthorizeResultStoreDeps,
) -> dict[str, Any]:
    account["status"] = "auth_failed"
    account["last_error"] = str(message or "reauthorize_failed")
    if mailbox is not None:
        account["mailbox"] = mailbox
    return deps.upsert_account_fn(account)


def mark_reauthorize_authorized(
    account: dict[str, Any],
    *,
    callback_url: str = "",
    mailbox: dict[str, Any] | None = None,
    deps: ReauthorizeResultStoreDeps,
) -> dict[str, Any]:
    now = deps.now_text_fn()
    account["status"] = "authorized"
    account["last_error"] = ""
    account["last_auth_at"] = now
    account["last_sub2api_submit_at"] = now
    if callback_url:
        account["callback_url"] = str(callback_url)
    if mailbox is not None:
        account["mailbox"] = mailbox
    account["source"] = str(account.get("source") or "manual")
    return deps.upsert_account_fn(account)


def registration_reauth_blocker(account: dict[str, Any], mailbox: dict[str, Any]) -> str:
    source_error = str(mailbox.get("source_error") or "").strip()
    has_auth_artifact = any(
        str(account.get(key) or "").strip()
        for key in ("callback_url", "access_token", "refresh_token", "id_token")
    )
    if source_error == "registration_disallowed" and not has_auth_artifact:
        return "account_not_created_registration_disallowed"
    return ""


def mark_unusable_reauthorize_source(
    account: dict[str, Any],
    message: str,
    *,
    mailbox: dict[str, Any],
    deps: ReauthorizeResultStoreDeps,
) -> dict[str, Any]:
    account["status"] = "auth_failed"
    account["last_error"] = str(message or "reauthorize_blocked")
    account["usable_for_reauth"] = False
    account["mailbox"] = mailbox
    return deps.upsert_account_fn(account)


def finalize_cpa_submit_with_optional_local_tokens(
    registrar: Any,
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    email: str,
    password: str,
    cpa_callback_url: str,
    cpa_result: dict[str, Any],
    debug: dict[str, Any],
    deps: ReauthorizeResultStoreDeps,
) -> Any:
    _ = (registrar, email, password)
    if not bool(cpa_result.get("ok")):
        updated = mark_reauthorize_failed(
            account,
            str(cpa_result.get("message") or "cpa_callback_submit_failed"),
            mailbox=mailbox,
            deps=deps,
        )
        message = str(cpa_result.get("message") or "cpa_callback_submit_failed")
        return deps.auto_outcome_cls(
            ok=False,
            message=message,
            account=updated,
            callback_url=str(cpa_callback_url),
            codex2api_import_submit_ok=False,
            codex2api_import_submit_message=str(cpa_result.get("message") or ""),
            debug=debug,
        )

    deps.log_stage_fn("CPA 回调已提交，主任务成功")
    deps.log_stage_fn("仅提交 CPA 授权，跳过本地 token / sub2api 后续链路")
    mailbox["_cpa_submit_ok"] = True
    mailbox["_cpa_submit_message"] = str(cpa_result.get("message") or "CPA callback submitted")
    debug["local_token_after_cpa"] = {"skipped": True, "reason": "cpa_only"}
    updated = mark_reauthorize_authorized(
        account,
        callback_url=str(cpa_callback_url),
        mailbox=mailbox,
        deps=deps,
    )
    return deps.auto_outcome_cls(
        ok=True,
        message=str(cpa_result.get("message") or "CPA callback submitted"),
        account=updated,
        callback_url=str(cpa_callback_url),
        codex2api_import_submit_ok=True,
        codex2api_import_submit_message=str(cpa_result.get("message") or ""),
        debug=debug,
    )


def save_result_and_import_codex2api(
    account: dict[str, Any],
    result: Any,
    *,
    source: str,
    codex2api_url: str = "",
    codex2api_admin_key: str = "",
    codex2api_proxy_url: str = "",
    deps: ReauthorizeResultStoreDeps,
) -> Any:
    if not result.ok:
        updated = mark_reauthorize_failed(account, str(result.error or "reauthorize_exchange_failed"), deps=deps)
        return deps.finish_outcome_cls(
            ok=False,
            message=account["last_error"],
            account=updated,
            callback_url=str(result.callback_url or ""),
        )

    email = str(account.get("email") or result.email or "").strip()
    result.email = result.email or email
    result.password = str(account.get("password") or "")
    result.mailbox = account.get("mailbox") if isinstance(account.get("mailbox"), dict) else {}

    codex2api_result: dict[str, Any] = {}
    if str(codex2api_url or "").strip() and str(codex2api_admin_key or "").strip():
        try:
            codex2api_result = deps.import_result_to_codex2api_fn(
                result,
                codex2api_url=codex2api_url,
                admin_key=codex2api_admin_key,
                account_name=email,
                proxy_url=str(codex2api_proxy_url or "").strip(),
            )
        except Exception as exc:
            codex2api_result = {"ok": False, "message": str(exc or "codex2api_import_failed")}

    updated = deps.save_registration_result_to_account_fn(result, source=source, account_id=str(account.get("id") or ""))
    codex2api_ok = bool(codex2api_result.get("ok")) if codex2api_result else False
    overall_ok = codex2api_ok if codex2api_result else True
    if overall_ok:
        updated = mark_reauthorize_authorized(updated, callback_url=str(result.callback_url or ""), deps=deps)
    else:
        updated = mark_reauthorize_failed(updated, str(codex2api_result.get("message") or "codex2api_import_failed"), deps=deps)

    return deps.finish_outcome_cls(
        ok=overall_ok,
        message="reauthorize_finished" if overall_ok else str(codex2api_result.get("message") or "codex2api_import_failed"),
        account=updated,
        callback_url=str(result.callback_url or ""),
        codex2api_import_submit_ok=codex2api_ok,
        codex2api_import_submit_message=str(codex2api_result.get("message") or "") if codex2api_result else "",
    )
