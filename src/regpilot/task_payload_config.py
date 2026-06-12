from __future__ import annotations

import argparse
import re
from typing import Any, Callable

from .cli import load_config
from .sms_provider_config import SMSBOWER_BASE_URL
from .webui_config_store import bool_from_payload


__all__ = [
    "bool_from_renamed_payload",
    "cloudflare_mail_provider_from_payload",
    "cloudflare_mail_provider_is_ready",
    "hotmail_mail_provider_from_payload",
    "mail_config_dict_from_payload",
    "mail_providers_from_payload",
    "namespace",
    "positive_int_from_renamed_payload",
    "register_config_from_payload",
    "renamed_payload_value",
]


LoadConfig = Callable[[argparse.Namespace], Any]


def namespace(**values: Any) -> argparse.Namespace:
    return argparse.Namespace(**values)


def bool_from_renamed_payload(payload: dict[str, Any], key: str, legacy_key: str, default: bool = False) -> bool:
    if payload.get(key) not in (None, ""):
        return bool_from_payload(payload, key, default)
    return bool_from_payload(payload, legacy_key, default)


def renamed_payload_value(payload: dict[str, Any], key: str, legacy_key: str, default: Any = None) -> Any:
    value = payload.get(key)
    if value not in (None, ""):
        return value
    value = payload.get(legacy_key)
    if value not in (None, ""):
        return value
    return default


def positive_int_from_renamed_payload(payload: dict[str, Any], key: str, legacy_key: str, default: int, minimum: int = 1) -> int:
    return max(minimum, int(renamed_payload_value(payload, key, legacy_key, default) or default))


def _normalize_http_url(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"^(https?):/(?!/)", r"\1://", text, flags=re.I)


def cloudflare_mail_provider_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "cloudflare-temp-email",
        "base_url": _normalize_http_url(payload.get("cf_temp_base_url")),
        "admin_auth": str(payload.get("cf_temp_admin_auth") or "").strip(),
        "custom_auth": str(payload.get("cf_temp_custom_auth") or "").strip(),
        "domain": str(payload.get("cf_temp_domain") or "").strip(),
        "use_random_subdomain": bool_from_payload(payload, "cf_temp_use_random_subdomain"),
    }


def cloudflare_mail_provider_is_ready(provider: dict[str, Any]) -> bool:
    return bool(
        str(provider.get("base_url") or "").strip()
        and str(provider.get("admin_auth") or "").strip()
        and str(provider.get("domain") or "").strip()
    )


def hotmail_mail_provider_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "hotmail-api",
        "base_url": _normalize_http_url(payload.get("hotmail_api_base_url")),
        "alias_enabled": bool_from_payload(payload, "hotmail_alias_enabled", True),
        "alias_max_per_account": int(payload.get("hotmail_alias_max_per_account") or 5),
        "mailboxes": str(payload.get("hotmail_mailboxes") or "INBOX,Junk").strip(),
        "sender_filters": str(payload.get("hotmail_sender_filters") or "openai,noreply,no-reply").strip(),
        "subject_filters": str(payload.get("hotmail_subject_filters") or "code,verification,验证码").strip(),
        "required_keywords": str(payload.get("hotmail_required_keywords") or "").strip(),
        "top": 10,
    }


def _icloud_mail_provider_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "icloud",
        "email": str(payload.get("icloud_email") or "").strip(),
        "imap_user": str(payload.get("icloud_imap_user") or "").strip(),
        "imap_password": str(payload.get("icloud_imap_password") or "").strip(),
        "cookies_json": str(payload.get("icloud_cookies_json") or "").strip(),
        "cookies_path": str(payload.get("icloud_cookies_path") or "").strip(),
        "host": str(payload.get("icloud_host") or "icloud.com").strip() or "icloud.com",
        "hme_label": str(payload.get("icloud_hme_label") or "RegPilot").strip() or "RegPilot",
    }


def mail_providers_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mail_type = str(payload.get("mail_type") or "cloudflare-temp-email").strip().lower()
    if mail_type in {"icloud", "icloud-hme", "icloud_hme"}:
        providers = [_icloud_mail_provider_from_payload(payload)]
        fallback_provider = cloudflare_mail_provider_from_payload(payload)
        if cloudflare_mail_provider_is_ready(fallback_provider):
            providers.append(fallback_provider)
        return providers
    if mail_type == "cloudflare-temp-email":
        return [cloudflare_mail_provider_from_payload(payload)]
    if mail_type in {"hotmail-api", "outlook-api", "microsoft-mail"}:
        return [hotmail_mail_provider_from_payload(payload)]
    return [{"type": mail_type}]


def _register_namespace_from_payload(payload: dict[str, Any], providers: list[dict[str, Any]]) -> argparse.Namespace:
    return namespace(
        config="",
        proxy=str(payload.get("proxy") or "").strip(),
        env_random_enabled=bool_from_payload(payload, "env_random_enabled"),
        env_proxy_pool=str(payload.get("env_proxy_pool") or "").strip(),
        env_ua_pool=str(payload.get("env_ua_pool") or "").strip(),
        env_accept_language_pool=str(payload.get("env_accept_language_pool") or "").strip(),
        env_timezone_pool=str(payload.get("env_timezone_pool") or "").strip(),
        env_viewport_pool=str(payload.get("env_viewport_pool") or "").strip(),
        total=int(payload.get("total") or 1),
        threads=int(payload.get("threads") or 1),
        default_password=str(payload.get("default_password") or ""),
        codex2api_url=str(payload.get("codex2api_url") or "").strip(),
        codex2api_admin_key=str(payload.get("codex2api_admin_key") or "").strip(),
        codex2api_proxy_url=str(payload.get("codex2api_proxy_url") or "").strip(),
        codex2api_auto_import=bool_from_payload(payload, "codex2api_auto_import"),
        hero_sms_api_key=str(payload.get("hero_sms_api_key") or "").strip(),
        hero_sms_base_url=str(payload.get("hero_sms_base_url") or "").strip(),
        sms_provider=str(payload.get("sms_provider") or "hero_sms").strip(),
        sms_api_key=str(payload.get("sms_api_key") or "").strip(),
        smsbower_api_key=str(payload.get("smsbower_api_key") or "").strip(),
        fivesim_api_key=str(payload.get("fivesim_api_key") or "").strip(),
        smsbower_base_url=str(payload.get("smsbower_base_url") or SMSBOWER_BASE_URL).strip(),
        hero_sms_country=str(payload.get("hero_sms_country") or "").strip(),
        hero_sms_service=str(payload.get("hero_sms_service") or "").strip(),
        hero_sms_min_price=float(payload.get("hero_sms_min_price") or 0),
        hero_sms_max_price=float(payload.get("hero_sms_max_price") or 0),
        hero_sms_wait_timeout=positive_int_from_renamed_payload(payload, "sms_wait_timeout", "hero_sms_wait_timeout", 180),
        hero_sms_wait_interval=positive_int_from_renamed_payload(payload, "sms_wait_interval", "hero_sms_wait_interval", 5),
        hero_sms_auto_retry=bool_from_renamed_payload(payload, "sms_auto_retry", "hero_sms_auto_retry"),
        hero_sms_retry_count=positive_int_from_renamed_payload(payload, "sms_retry_count", "hero_sms_retry_count", 3),
    )


def register_config_from_payload(payload: dict[str, Any], load_config_fn: LoadConfig = load_config):
    providers = mail_providers_from_payload(payload)
    args = _register_namespace_from_payload(payload, providers)
    cfg = load_config_fn(args)
    cfg.mail.request_timeout = int(payload.get("request_timeout") or 30)
    cfg.mail.wait_timeout = int(payload.get("wait_timeout") or 60)
    cfg.mail.wait_interval = int(payload.get("wait_interval") or 2)
    cfg.mail.providers = providers
    cfg.sms_provider = str(payload.get("sms_provider") or "hero_sms").strip() or "hero_sms"
    cfg.sms_api_key = str(payload.get("sms_api_key") or "").strip()
    cfg.smsbower_api_key = str(payload.get("smsbower_api_key") or "").strip()
    cfg.fivesim_api_key = str(payload.get("fivesim_api_key") or "").strip()
    cfg.smsbower_base_url = str(payload.get("smsbower_base_url") or SMSBOWER_BASE_URL).strip() or SMSBOWER_BASE_URL
    cfg.hero_sms_auto_retry = bool_from_renamed_payload(payload, "sms_auto_retry", "hero_sms_auto_retry")
    cfg.hero_sms_retry_count = positive_int_from_renamed_payload(payload, "sms_retry_count", "hero_sms_retry_count", 3)
    cfg.default_password = str(payload.get("default_password") or "")
    cfg.hero_sms_min_price = float(payload.get("hero_sms_min_price") or 0)
    return cfg


def mail_config_dict_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_timeout": int(payload.get("request_timeout") or 30),
        "wait_timeout": int(payload.get("wait_timeout") or 60),
        "bind_email_wait_timeout": int(payload.get("bind_email_wait_timeout") or payload.get("add_email_wait_timeout") or 180),
        "wait_interval": int(payload.get("wait_interval") or 2),
        "providers": mail_providers_from_payload(payload),
    }
