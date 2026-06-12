from __future__ import annotations

from typing import Any, Callable

from .sms_provider_config import HeroSMSConfig, build_sms_config_from_values, sms_api_key_from_values, sms_provider_from_values


__all__ = [
    "SMS_FALLBACK_KEYS",
    "sms_api_key_from_payload",
    "sms_config_from_payload",
    "sms_payload_with_webui_fallback",
    "sms_provider_from_payload",
]


LoadWebuiConfig = Callable[[], dict[str, Any]]


SMS_FALLBACK_KEYS: tuple[str, ...] = (
    "env_random_enabled",
    "env_proxy_pool",
    "env_ua_pool",
    "env_accept_language_pool",
    "env_timezone_pool",
    "env_viewport_pool",
    "sms_provider",
    "sms_api_key",
    "hero_sms_api_key",
    "hero_sms_base_url",
    "smsbower_api_key",
    "fivesim_api_key",
    "smsbower_base_url",
    "hero_sms_service",
    "hero_sms_country",
    "hero_sms_max_price",
    "sms_wait_timeout",
    "sms_wait_interval",
    "sms_resend_after_seconds",
    "sms_timeout_after_resend_seconds",
    "sms_release_after_seconds",
    "sms_auto_retry",
    "sms_retry_count",
    "hero_sms_wait_timeout",
    "hero_sms_wait_interval",
    "hero_sms_auto_retry",
    "hero_sms_retry_count",
)


def _has_explicit_provider_key(values: dict[str, Any]) -> bool:
    return bool(
        str(values.get("hero_sms_api_key") or "").strip()
        or str(values.get("smsbower_api_key") or "").strip()
        or str(values.get("fivesim_api_key") or "").strip()
    )


def sms_payload_with_webui_fallback(payload: dict[str, Any], load_webui_config: LoadWebuiConfig) -> dict[str, Any]:
    merged = dict(payload or {})
    has_explicit_provider_key = _has_explicit_provider_key(merged)
    webui_config = load_webui_config()
    register_cfg = (webui_config.get("register") or {}) if isinstance(webui_config, dict) else {}
    for key in SMS_FALLBACK_KEYS:
        current = merged.get(key)
        if key == "sms_provider" and current in (None, "") and has_explicit_provider_key:
            continue
        if current in (None, ""):
            merged[key] = register_cfg.get(key)
    return merged


def sms_provider_from_payload(payload: dict[str, Any]) -> str:
    return sms_provider_from_values(payload)


def sms_api_key_from_payload(payload: dict[str, Any], provider: str) -> str:
    return sms_api_key_from_values(payload, provider)


def sms_config_from_payload(payload: dict[str, Any], load_webui_config: LoadWebuiConfig) -> HeroSMSConfig:
    payload = sms_payload_with_webui_fallback(payload, load_webui_config)
    return build_sms_config_from_values(payload)
