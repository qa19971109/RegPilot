from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any
from urllib.parse import quote

from . import sms_activation_helpers


@dataclass(frozen=True)
class SmsAcquirePriceLimits:
    provider: str
    min_limit: float | None
    price_limit: float | None


@dataclass
class SmsPollState:
    wait_timeout: int
    interval: int
    heartbeat_interval: int
    resend_after: int
    effective_timeout_after_resend: int | None
    start_ts: float
    deadline: float
    next_progress_ts: float
    resend_ts: float = 0.0
    resent: bool = False


def _sms_acquire_price_limits(config: Any, max_price_override: float | None) -> SmsAcquirePriceLimits:
    provider = sms_activation_helpers.normalize_sms_provider(getattr(config, "provider", "hero_sms"))
    price_limit = sms_activation_helpers.normalize_hero_sms_price(max_price_override)
    if price_limit is None:
        price_limit = sms_activation_helpers.normalize_hero_sms_price(config.max_price)
    min_limit = sms_activation_helpers.normalize_hero_sms_price(getattr(config, "min_price", 0.0))
    if price_limit is not None and min_limit is not None and float(min_limit) > float(price_limit) + 1e-9:
        raise RuntimeError(f"{provider}_get_number_failed: minPrice_gt_maxPrice (minPrice={float(min_limit):.4f}, maxPrice={float(price_limit):.4f})")
    return SmsAcquirePriceLimits(provider=provider, min_limit=min_limit, price_limit=price_limit)


def _acquire_5sim_phone(config: Any, price_limit: float | None, fivesim_request_fn: Any) -> dict[str, str]:
    country = str(config.country or "england").strip() or "england"
    product = str(config.service or "openai").strip() or "openai"
    params: dict[str, Any] = {}
    if price_limit is not None and price_limit > 0:
        params["maxPrice"] = float(price_limit)
    payload = fivesim_request_fn(
        config,
        f"/user/buy/activation/{quote(country, safe='')}/any/{quote(product, safe='')}",
        params,
    )
    activation_id = str((payload or {}).get("id") or "").strip() if isinstance(payload, dict) else ""
    phone_number = str((payload or {}).get("phone") or (payload or {}).get("number") or "").strip() if isinstance(payload, dict) else ""
    if not activation_id or not phone_number:
        text = sms_activation_helpers.hero_sms_text(payload)
        lowered = text.lower()
        if "no free phones" in lowered or "no numbers" in lowered or "not enough" in lowered:
            raise RuntimeError("5sim_get_number_failed: NO_NUMBERS")
        raise RuntimeError(f"5sim_get_number_failed: {text[:300]}")
    return sms_activation_helpers.activation_result(
        activation_id,
        phone_number,
        price=sms_activation_helpers.activation_price_from_payload(payload),
    )


def _hero_sms_number_params(config: Any, price_limit: float | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "action": "getNumberV2" if sms_activation_helpers.is_smsbower_config(config) else "getNumber",
        "service": str(getattr(config, "service", "") or "dr"),
        "country": str(config.country or "52"),
    }
    if price_limit is not None and price_limit > 0:
        params["maxPrice"] = float(price_limit)
    return params


def _hero_sms_access_number_match(payload: Any) -> re.Match[str] | None:
    text = sms_activation_helpers.hero_sms_text(payload)
    match = re.search(r"ACCESS_NUMBER:([^:]+):(.+)", text, re.I)
    if match or not isinstance(payload, dict):
        return match
    activation_id = str(payload.get("activationId") or payload.get("id") or "").strip()
    phone_number = str(payload.get("phoneNumber") or payload.get("number") or "").strip()
    if not activation_id or not phone_number:
        return None
    return re.match(r"(.+)", f"ACCESS_NUMBER:{activation_id}:{phone_number}")


def _quote_min_available_price(config: Any, quote_list_fn: Any) -> float | None:
    try:
        quote_data = quote_list_fn(config) if not sms_activation_helpers.is_smsbower_config(config) else {}
        prices = [
            sms_activation_helpers.normalize_hero_sms_price(row.get("price"))
            for row in (quote_data.get("quote_list") or [])
            if isinstance(row, dict)
        ]
        prices = [value for value in prices if value is not None]
    except Exception:
        prices = []
    return min(prices) if prices else None


def _raise_no_numbers_error(config: Any, limits: SmsAcquirePriceLimits, quote_list_fn: Any) -> None:
    min_available = _quote_min_available_price(config, quote_list_fn)
    if limits.price_limit is not None and min_available is not None and min_available > limits.price_limit:
        raise RuntimeError(
            f"{limits.provider}_get_number_failed: NO_NUMBERS (minPrice={float(limits.min_limit or 0):.4f}, maxPrice={limits.price_limit:.4f}, minAvailable={min_available:.4f})"
        )
    if limits.price_limit is not None:
        raise RuntimeError(f"{limits.provider}_get_number_failed: NO_NUMBERS (minPrice={float(limits.min_limit or 0):.4f}, maxPrice={limits.price_limit:.4f})")
    raise RuntimeError(f"{limits.provider}_get_number_failed: NO_NUMBERS")


def _maybe_retry_wrong_max_price(
    config: Any,
    text: str,
    limits: SmsAcquirePriceLimits,
    *,
    allow_wrong_price_retry: bool,
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
    quote_list_fn: Any,
    exact_request_price_fn: Any,
) -> dict[str, str] | None:
    wrong_price_match = re.search(r"WRONG_MAX_PRICE[:=]\s*([0-9]+(?:\.[0-9]+)?)", str(text or ""), re.I)
    if sms_activation_helpers.is_smsbower_config(config) or not allow_wrong_price_retry or not wrong_price_match:
        return None
    suggested_price = sms_activation_helpers.normalize_hero_sms_price(wrong_price_match.group(1))
    current_price = sms_activation_helpers.normalize_hero_sms_price(limits.price_limit)
    user_limit = sms_activation_helpers.normalize_hero_sms_price(config.max_price)
    effective_limit = current_price if current_price is not None else user_limit
    if (
        suggested_price is not None
        and (effective_limit is None or float(suggested_price) <= float(effective_limit) + 1e-9)
        and (current_price is None or abs(float(suggested_price) - float(current_price)) > 1e-9)
    ):
        return acquire_hero_sms_phone(
            config,
            max_price_override=float(suggested_price),
            allow_wrong_price_retry=False,
            hero_sms_request_fn=hero_sms_request_fn,
            fivesim_request_fn=fivesim_request_fn,
            quote_list_fn=quote_list_fn,
            exact_request_price_fn=exact_request_price_fn,
        )
    if suggested_price is not None and effective_limit is not None and float(suggested_price) > float(effective_limit) + 1e-9:
        raise RuntimeError(
            f"hero_sms_get_number_failed: WRONG_MAX_PRICE (maxPrice={float(effective_limit):.4f}, required={float(suggested_price):.4f})"
        )
    return None


def _raise_hero_sms_acquire_failure(
    config: Any,
    text: str,
    limits: SmsAcquirePriceLimits,
    *,
    allow_wrong_price_retry: bool,
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
    quote_list_fn: Any,
    exact_request_price_fn: Any,
) -> dict[str, str]:
    retry_result = _maybe_retry_wrong_max_price(
        config,
        text,
        limits,
        allow_wrong_price_retry=allow_wrong_price_retry,
        hero_sms_request_fn=hero_sms_request_fn,
        fivesim_request_fn=fivesim_request_fn,
        quote_list_fn=quote_list_fn,
        exact_request_price_fn=exact_request_price_fn,
    )
    if retry_result is not None:
        return retry_result
    lowered = str(text or "").lower()
    if "no_numbers" in lowered or "no numbers" in lowered:
        _raise_no_numbers_error(config, limits, quote_list_fn)
    raise RuntimeError(f"{limits.provider}_get_number_failed: {text[:300]}")


def _hero_sms_activation_price(config: Any, payload: Any, price_limit: float | None, exact_request_price_fn: Any) -> str:
    price = sms_activation_helpers.activation_price_from_payload(payload)
    if not price and (not sms_activation_helpers.is_smsbower_config(config)) and price_limit is not None and price_limit > 0:
        return exact_request_price_fn(config, price_limit) or f"≤{float(price_limit):.4f}"
    return price


def acquire_hero_sms_phone(
    config: Any,
    *,
    max_price_override: float | None = None,
    allow_wrong_price_retry: bool = True,
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
    quote_list_fn: Any,
    exact_request_price_fn: Any,
) -> dict[str, str]:
    if not str(getattr(config, "api_key", "") or "").strip():
        raise RuntimeError("missing_sms_api_key")
    limits = _sms_acquire_price_limits(config, max_price_override)
    if sms_activation_helpers.is_5sim_config(config):
        return _acquire_5sim_phone(config, limits.price_limit, fivesim_request_fn)

    params = _hero_sms_number_params(config, limits.price_limit)
    payload = hero_sms_request_fn(config, params)
    text = sms_activation_helpers.hero_sms_text(payload)
    match = _hero_sms_access_number_match(payload)
    if not match:
        return _raise_hero_sms_acquire_failure(
            config,
            text,
            limits,
            allow_wrong_price_retry=allow_wrong_price_retry,
            hero_sms_request_fn=hero_sms_request_fn,
            fivesim_request_fn=fivesim_request_fn,
            quote_list_fn=quote_list_fn,
            exact_request_price_fn=exact_request_price_fn,
        )
    activation_id = str(match.group(1) or "").strip()
    phone_number = str(match.group(2) or "").strip()
    price = _hero_sms_activation_price(config, payload, limits.price_limit, exact_request_price_fn)
    return sms_activation_helpers.activation_result(activation_id, phone_number, price=price)


def _sms_poll_now(datetime_module: Any) -> float:
    return datetime_module.now(timezone.utc).timestamp()


def _sms_poll_state(
    config: Any,
    *,
    timeout_after_resend: int | None,
    on_resend: Any,
    progress_interval: int,
    datetime_module: Any,
    resend_after_default: int,
    timeout_after_resend_default: int,
    release_after_default: int,
) -> SmsPollState:
    resend_after = max(1, int(getattr(config, "resend_after_seconds", resend_after_default) or resend_after_default))
    release_after = max(1, int(getattr(config, "release_after_seconds", release_after_default) or release_after_default))
    configured_timeout_after_resend = max(
        1,
        int(getattr(config, "timeout_after_resend_seconds", timeout_after_resend_default) or timeout_after_resend_default),
    )
    wait_timeout = max(15, int(config.wait_timeout or release_after))
    effective_timeout_after_resend = (
        int(timeout_after_resend)
        if timeout_after_resend is not None
        else (configured_timeout_after_resend if callable(on_resend) else None)
    )
    start_ts = _sms_poll_now(datetime_module)
    return SmsPollState(
        wait_timeout=wait_timeout,
        interval=max(1, int(config.wait_interval or 5)),
        heartbeat_interval=max(1, int(progress_interval or 15)),
        resend_after=resend_after,
        effective_timeout_after_resend=effective_timeout_after_resend,
        start_ts=start_ts,
        deadline=start_ts + wait_timeout,
        next_progress_ts=start_ts + max(1, int(progress_interval or 15)),
    )


def _poll_sms_activation_payload(config: Any, activation_id: str, *, hero_sms_request_fn: Any, fivesim_request_fn: Any) -> tuple[Any, str]:
    if sms_activation_helpers.is_5sim_config(config):
        payload = fivesim_request_fn(config, f"/user/check/{quote(str(activation_id), safe='')}")
        return payload, sms_activation_helpers.extract_5sim_sms_code(payload)
    payload = hero_sms_request_fn(config, {"action": "getStatus", "id": activation_id})
    return payload, sms_activation_helpers.extract_sms_code(payload)


def _raise_if_terminal_sms_status(payload: Any) -> None:
    text = sms_activation_helpers.hero_sms_text(payload)
    if re.search(r"STATUS_CANCEL|STATUS_BANNED|NO_ACTIVATION|BAD_STATUS|ERROR|CANCELED|CANCELLED|BANNED|TIMEOUT", text, re.I):
        raise RuntimeError(f"sms_terminal_status: {text[:300]}")


def _maybe_resend_sms_poll(state: SmsPollState, *, on_resend: Any, datetime_module: Any) -> None:
    elapsed = max(0, int(_sms_poll_now(datetime_module) - state.start_ts))
    if state.resent or elapsed < state.resend_after:
        return
    if callable(on_resend):
        on_resend()
    state.resent = True
    state.resend_ts = _sms_poll_now(datetime_module)
    if state.effective_timeout_after_resend is not None:
        state.deadline = min(state.deadline, state.resend_ts + max(1, int(state.effective_timeout_after_resend)))


def _maybe_report_sms_poll_progress(state: SmsPollState, *, on_progress: Any, datetime_module: Any) -> None:
    now_ts = _sms_poll_now(datetime_module)
    if not callable(on_progress) or now_ts < state.next_progress_ts:
        return
    on_progress(
        {
            "elapsed": max(0, int(now_ts - state.start_ts)),
            "wait_timeout": state.wait_timeout,
            "remaining": max(0, int(state.deadline - now_ts)),
            "resent": state.resent,
            "resend_after_seconds": state.resend_after,
            "after_resend_elapsed": max(0, int(now_ts - state.resend_ts)) if state.resent and state.resend_ts else 0,
            "timeout_after_resend": int(state.effective_timeout_after_resend) if state.effective_timeout_after_resend is not None else None,
        }
    )
    state.next_progress_ts = now_ts + state.heartbeat_interval


def poll_hero_sms_code(
    config: Any,
    activation_id: str,
    *,
    on_resend: Any = None,
    timeout_after_resend: int | None = None,
    on_progress: Any = None,
    progress_interval: int = 15,
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
    datetime_module: Any,
    sleep_fn: Any,
    resend_after_default: int,
    timeout_after_resend_default: int,
    release_after_default: int,
) -> str:
    state = _sms_poll_state(
        config,
        timeout_after_resend=timeout_after_resend,
        on_resend=on_resend,
        progress_interval=progress_interval,
        datetime_module=datetime_module,
        resend_after_default=resend_after_default,
        timeout_after_resend_default=timeout_after_resend_default,
        release_after_default=release_after_default,
    )
    while _sms_poll_now(datetime_module) < state.deadline:
        payload, code = _poll_sms_activation_payload(
            config,
            activation_id,
            hero_sms_request_fn=hero_sms_request_fn,
            fivesim_request_fn=fivesim_request_fn,
        )
        if code:
            return code
        _raise_if_terminal_sms_status(payload)
        _maybe_resend_sms_poll(state, on_resend=on_resend, datetime_module=datetime_module)
        _maybe_report_sms_poll_progress(state, on_progress=on_progress, datetime_module=datetime_module)
        sleep_fn(state.interval)
    raise RuntimeError("sms_code_timeout")


def set_hero_sms_status(
    config: Any,
    activation_id: str,
    status: int,
    *,
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
) -> None:
    if not activation_id:
        return
    try:
        if sms_activation_helpers.is_5sim_config(config):
            status_int = int(status)
            if status_int == 6:
                fivesim_request_fn(config, f"/user/finish/{quote(str(activation_id), safe='')}")
            elif status_int == 8:
                fivesim_request_fn(config, f"/user/cancel/{quote(str(activation_id), safe='')}")
            return
        hero_sms_request_fn(config, {"action": "setStatus", "id": activation_id, "status": int(status)})
    except Exception:
        return


def phone_activation_acquire(
    config: Any,
    *,
    max_price_override: float | None = None,
    acquire_phone_fn: Any,
) -> dict[str, str]:
    activation = acquire_phone_fn(config, max_price_override=max_price_override)
    return {
        "activation_id": str(activation.get("activation_id") or "").strip(),
        "phone_number": str(activation.get("phone_number") or "").strip(),
    }


def phone_activation_reuse(phone_number: str, activation_id: str) -> dict[str, str]:
    return {
        "activation_id": str(activation_id or "").strip(),
        "phone_number": str(phone_number or "").strip(),
    }


def phone_activation_poll_code(config: Any, activation_id: str, *, poll_code_fn: Any) -> str:
    return poll_code_fn(config, activation_id)


def phone_activation_complete(config: Any, activation_id: str, *, set_status_fn: Any) -> None:
    set_status_fn(config, activation_id, 6)


def phone_activation_cancel(config: Any, activation_id: str, *, set_status_fn: Any) -> None:
    set_status_fn(config, activation_id, 8)


def phone_activation_reactivate(config: Any, activation_id: str, *, set_status_fn: Any) -> None:
    set_status_fn(config, activation_id, 3)
