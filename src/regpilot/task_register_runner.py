from __future__ import annotations

from typing import Any, Callable


__all__ = [
    "registration_attempt_limit",
    "run_register_task",
    "summarize_registration_result",
]


RegisterConfigFromPayload = Callable[[dict[str, Any]], Any]
RunPlaceholder = Callable[[Any], Any]
SaveResult = Callable[[Any], Any]
Sleep = Callable[[float], Any]


def registration_attempt_limit(requested_total: int, payload: dict[str, Any]) -> int:
    try:
        disallowed_retry_count = max(1, int(payload.get("registration_disallowed_retry_count") or 5))
    except (TypeError, ValueError):
        disallowed_retry_count = 5
    return max(requested_total, requested_total * disallowed_retry_count)


def summarize_registration_result(item: Any) -> dict[str, Any]:
    mailbox = item.mailbox if isinstance(item.mailbox, dict) else {}
    return {
        "ok": bool(item.ok),
        "email": str(item.email or ""),
        "error": str(item.error or ""),
        "callback_url": str(mailbox.get("_callback_url", item.callback_url or "") or ""),
        "has_access_token": bool(item.access_token),
        "import_submit_ok": bool(mailbox.get("_cpa_submit_ok")),
        "import_submit_message": str(mailbox.get("_cpa_submit_message") or ""),
    }


def run_register_task(
    payload: dict[str, Any],
    *,
    register_config_from_payload: RegisterConfigFromPayload,
    run_placeholder: RunPlaceholder,
    save_result: SaveResult,
    sleep: Sleep,
) -> dict[str, Any]:
    cfg = register_config_from_payload(payload)
    requested_total = max(1, int(getattr(cfg, "total", 1) or 1))
    attempts = registration_attempt_limit(requested_total, payload)
    result = None
    successes: list[Any] = []
    failures: list[Any] = []

    for attempt in range(1, attempts + 1):
        print(f"阶段：注册尝试 {attempt}/{attempts}（目标成功 {len(successes) + 1}/{requested_total}）")
        result = run_placeholder(cfg)
        if result.ok:
            successes.append(result)
            if len(successes) >= requested_total:
                break
            continue
        failures.append(result)
        if str(result.error or "") == "registration_disallowed" and attempt < attempts:
            print("阶段：本次被上游拒绝创建账号，已更换邮箱/资料后重试")
            sleep(2)
            continue
        break
    assert result is not None
    path = save_result(result)
    mailbox = result.mailbox if isinstance(result.mailbox, dict) else {}
    ok = len(successes) >= requested_total
    error = "" if ok else str(result.error or "registration_target_not_reached")
    return {
        "ok": ok,
        "target_total": requested_total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "attempt_count": len(successes) + len(failures),
        "items": [summarize_registration_result(item) for item in successes],
        "failures": [summarize_registration_result(item) for item in failures[-5:]],
        "email": str(result.email or ""),
        "error": error,
        "callback_url": str(mailbox.get("_callback_url", result.callback_url or "") or ""),
        "has_access_token": bool(result.access_token),
        "import_submit_ok": bool(mailbox.get("_cpa_submit_ok")),
        "import_submit_message": str(mailbox.get("_cpa_submit_message") or ""),
        "saved_result": str(path),
    }
