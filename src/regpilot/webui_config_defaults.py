from __future__ import annotations

from typing import Any

from .sms_provider_config import SMSBOWER_BASE_URL

__all__ = ["WEBUI_CONFIG_DEFAULTS"]


ENVIRONMENT_DEFAULTS: dict[str, Any] = {
    "proxy": "",
    "env_random_enabled": False,
    "env_proxy_pool": "",
    "env_ua_pool": "",
    "env_accept_language_pool": "",
    "env_timezone_pool": "",
    "env_viewport_pool": "",
}

CPA_DEFAULTS: dict[str, Any] = {
    "codex2api_url": "",
    "codex2api_admin_key": "",
    "codex2api_proxy_url": "",
    "codex2api_auto_import": False,
}

MAIL_DEFAULTS: dict[str, Any] = {
    "mail_type": "cloudflare-temp-email",
    "icloud_email": "",
    "icloud_imap_user": "",
    "icloud_imap_password": "",
    "icloud_cookies_json": "",
    "icloud_cookies_path": "",
    "icloud_host": "icloud.com",
    "icloud_hme_label": "RegPilot",
    "cf_temp_base_url": "",
    "cf_temp_admin_auth": "",
    "cf_temp_custom_auth": "",
    "cf_temp_domain": "",
    "cf_temp_use_random_subdomain": False,
    "hotmail_api_base_url": "http://127.0.0.1:17373",
    "hotmail_alias_enabled": True,
    "hotmail_alias_max_per_account": 5,
    "hotmail_mailboxes": "INBOX,Junk",
    "hotmail_sender_filters": "openai,noreply,no-reply",
    "hotmail_subject_filters": "code,verification,验证码",
    "hotmail_required_keywords": "",
}

SMS_DEFAULTS: dict[str, Any] = {
    "sms_provider": "hero_sms",
    "sms_api_key": "",
    "hero_sms_api_key": "",
    "hero_sms_base_url": "https://hero-sms.com/stubs/handler_api.php",
    "smsbower_api_key": "",
    "smsbower_base_url": SMSBOWER_BASE_URL,
    "fivesim_api_key": "",
    "hero_sms_country": "16",
    "hero_sms_country_label": "英格兰 (United Kingdom)",
    "hero_sms_service": "dr",
    "hero_sms_min_price": "",
    "hero_sms_max_price": "0.023",
    "sms_wait_timeout": 60,
    "sms_wait_interval": 5,
    "sms_resend_after_seconds": 30,
    "sms_timeout_after_resend_seconds": 60,
    "sms_release_after_seconds": 120,
    "sms_auto_retry": False,
    "sms_retry_count": 3,
}

REGISTER_TASK_DEFAULTS: dict[str, Any] = {
    "total": 1,
    "threads": 1,
    "default_password": "",
    "request_timeout": 30,
    "wait_timeout": 60,
    "wait_interval": 2,
}

WEBUI_CONFIG_DEFAULTS: dict[str, dict[str, Any]] = {
    "logs": {
        "job_log_max_mb": 100,
    },
    "register": {
        **ENVIRONMENT_DEFAULTS,
        **REGISTER_TASK_DEFAULTS,
        **MAIL_DEFAULTS,
        **CPA_DEFAULTS,
        **SMS_DEFAULTS,
    },
    "hero_phone_bind": {
        **ENVIRONMENT_DEFAULTS,
        **CPA_DEFAULTS,
        **SMS_DEFAULTS,
        **MAIL_DEFAULTS,
    },
}
