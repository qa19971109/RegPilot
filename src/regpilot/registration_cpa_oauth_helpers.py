from __future__ import annotations

from typing import Any


def should_use_cpa_oauth_auto_import(config: Any, *, parse_bool_fn: Any) -> bool:
    return (
        parse_bool_fn(getattr(config, "codex2api_auto_import", False), key="codex2api_auto_import")
        and bool(str(getattr(config, "codex2api_url", "") or "").strip())
        and bool(str(getattr(config, "codex2api_admin_key", "") or "").strip())
    )


def build_cpa_sms_config_and_retry_count(
    config: Any,
    *,
    build_reauthorize_sms_config_fn: Any,
    parse_bool_fn: Any,
) -> tuple[Any, int]:
    sms_config = build_reauthorize_sms_config_fn(
        sms_provider=str(getattr(config, "sms_provider", "") or "hero_sms"),
        sms_api_key=str(getattr(config, "sms_api_key", "") or ""),
        hero_sms_api_key=str(getattr(config, "hero_sms_api_key", "") or ""),
        smsbower_api_key=str(getattr(config, "smsbower_api_key", "") or ""),
        hero_sms_base_url=str(getattr(config, "hero_sms_base_url", "") or ""),
        smsbower_base_url=str(getattr(config, "smsbower_base_url", "") or ""),
        hero_sms_country=str(getattr(config, "hero_sms_country", "") or "16"),
        hero_sms_service=str(getattr(config, "hero_sms_service", "") or "dr"),
        hero_sms_max_price=getattr(config, "hero_sms_max_price", 0.0),
        sms_wait_timeout=int(getattr(config, "sms_wait_timeout", getattr(config, "hero_sms_wait_timeout", 60)) or 60),
        sms_wait_interval=int(getattr(config, "sms_wait_interval", getattr(config, "hero_sms_wait_interval", 5)) or 5),
        sms_auto_retry=parse_bool_fn(getattr(config, "sms_auto_retry", getattr(config, "hero_sms_auto_retry", False)), key="sms_auto_retry"),
    )
    retry_count = max(1, int(getattr(config, "sms_retry_count", getattr(config, "hero_sms_retry_count", 3)) or 3)) if sms_config.auto_retry else 1
    return sms_config, retry_count


def mail_config_for_add_email(config: Any, *, asdict_fn: Any) -> dict[str, Any]:
    try:
        value = asdict_fn(config.mail)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}
