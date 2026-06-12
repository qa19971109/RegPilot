from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import MailConfig, RegisterConfig


SUPPORTED_MAIL_PROVIDER_NAMES = {"icloud", "cloudflare-temp-email", "hotmail-api"}


def normalize_mail_provider_name(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"icloud", "icloud-hme"}:
        return "icloud"
    if raw in {"cloudflare-temp-email", "cloudflare-temp", "cloudflare"}:
        return "cloudflare-temp-email"
    if raw in {"hotmail-api", "outlook-api", "microsoft-mail", "hotmail", "outlook"}:
        return "hotmail-api"
    return raw


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def webui_mail_section_names_for_account(account: dict[str, Any]) -> tuple[str, ...]:
    source = str(account.get("source") or "").strip().lower()
    if source in {"phone_signup", "phone_direct", "hero_phone_bind"}:
        return ("hero_phone_bind", "phone_direct", "register")
    return ("register", "hero_phone_bind", "phone_direct")


def load_webui_config(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "webui_config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def load_webui_default_mail_provider_name(account: dict[str, Any], data_dir: Path) -> str:
    data = load_webui_config(data_dir)
    for section_name in webui_mail_section_names_for_account(account):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        mail_type = normalize_mail_provider_name(section.get("mail_type") or "")
        if mail_type in SUPPORTED_MAIL_PROVIDER_NAMES:
            return mail_type
    return ""


def mail_target_email_for_account(account: dict[str, Any], mailbox: dict[str, Any]) -> str:
    email = first_text(mailbox.get("email"), mailbox.get("bind_email"), account.get("email"))
    return email if "@" in email else ""


def mail_provider_name_for_account(account: dict[str, Any], mailbox: dict[str, Any], data_dir: Path) -> str:
    for value in (mailbox.get("provider"), mailbox.get("mail_provider"), mailbox.get("email_provider")):
        provider_name = normalize_mail_provider_name(value)
        if provider_name in SUPPORTED_MAIL_PROVIDER_NAMES:
            return provider_name
    provider_name = load_webui_default_mail_provider_name(account, data_dir)
    if provider_name:
        return provider_name
    email = mail_target_email_for_account(account, mailbox).lower()
    if email.endswith(("@icloud.com", "@me.com", "@mac.com")):
        return "icloud"
    return ""


def load_webui_mail_defaults(provider_name: str, data_dir: Path) -> dict[str, Any]:
    provider_name = normalize_mail_provider_name(provider_name)
    data = load_webui_config(data_dir)
    for section_name in ("register", "phone_direct", "hero_phone_bind"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        mail_type = normalize_mail_provider_name(section.get("mail_type") or "")
        if mail_type and mail_type != provider_name:
            continue
        if provider_name == "cloudflare-temp-email":
            return {
                "type": "cloudflare-temp-email",
                "base_url": first_text(section.get("cf_temp_base_url")),
                "admin_auth": first_text(section.get("cf_temp_admin_auth")),
                "custom_auth": first_text(section.get("cf_temp_custom_auth")),
                "domain": first_text(section.get("cf_temp_domain")),
            }
        if provider_name == "icloud":
            return {
                "type": "icloud",
                "email": first_text(section.get("icloud_email")),
                "imap_user": first_text(section.get("icloud_imap_user")),
                "imap_password": first_text(section.get("icloud_imap_password")),
                "cookies_json": first_text(section.get("icloud_cookies_json")),
                "cookies_path": first_text(section.get("icloud_cookies_path")),
                "host": first_text(section.get("icloud_host"), "icloud.com"),
                "hme_label": first_text(section.get("icloud_hme_label"), "RegPilot"),
            }
        if provider_name == "hotmail-api":
            return {
                "type": "hotmail-api",
                "base_url": first_text(section.get("hotmail_api_base_url"), "http://127.0.0.1:17373"),
                "alias_enabled": bool(section.get("hotmail_alias_enabled", True)),
                "alias_max_per_account": section.get("hotmail_alias_max_per_account") or 5,
                "mailboxes": first_text(section.get("hotmail_mailboxes"), "INBOX,Junk"),
                "sender_filters": first_text(section.get("hotmail_sender_filters"), "openai,noreply,no-reply"),
                "subject_filters": first_text(section.get("hotmail_subject_filters"), "code,verification,验证码"),
                "required_keywords": first_text(section.get("hotmail_required_keywords")),
            }
    return {"type": provider_name} if provider_name else {}


def load_webui_cloudflare_fallback_provider(data_dir: Path) -> dict[str, Any]:
    data = load_webui_config(data_dir)
    for section_name in ("register", "phone_direct", "hero_phone_bind"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        provider = {
            "type": "cloudflare-temp-email",
            "base_url": first_text(section.get("cf_temp_base_url")),
            "admin_auth": first_text(section.get("cf_temp_admin_auth")),
            "custom_auth": first_text(section.get("cf_temp_custom_auth")),
            "domain": first_text(section.get("cf_temp_domain")),
        }
        if provider["base_url"] and provider["admin_auth"] and provider["domain"]:
            return provider
    return {}


def mailbox_mail_provider_config(account: dict[str, Any], mailbox: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    provider_name = mail_provider_name_for_account(account, mailbox, data_dir)
    if not provider_name:
        return {}
    provider = dict(load_webui_mail_defaults(provider_name, data_dir))
    provider["type"] = provider_name
    for key in (
        "email",
        "address",
        "alias",
        "login",
        "domain",
        "account_id",
        "temp_token",
        "base_url",
        "api_key",
        "admin_auth",
        "custom_auth",
        "imap_user",
        "imap_password",
        "cookies_json",
        "cookies_path",
        "host",
        "hme_label",
        "alias_source",
        "base_email",
        "client_id",
        "refresh_token",
        "microsoft_account_id",
        "mailboxes",
        "sender_filters",
        "subject_filters",
        "required_keywords",
        "alias_enabled",
        "alias_max_per_account",
    ):
        value = mailbox.get(key)
        if value not in (None, ""):
            provider[key] = value
    if isinstance(mailbox.get("cookies"), dict):
        provider["cookies"] = mailbox["cookies"]
    email = first_text(mail_target_email_for_account(account, mailbox), provider.get("email"))
    if email:
        provider["email"] = email
    return provider


def mailbox_for_mail_wait(account: dict[str, Any], mailbox: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    provider_name = mail_provider_name_for_account(account, mailbox, data_dir)
    email = mail_target_email_for_account(account, mailbox)
    if not provider_name:
        return mailbox
    current_provider = normalize_mail_provider_name(mailbox.get("provider"))
    if current_provider == provider_name and str(mailbox.get("email") or "").strip():
        return mailbox
    wait_mailbox = dict(mailbox)
    wait_mailbox["provider"] = provider_name
    if email:
        wait_mailbox["email"] = email
    return wait_mailbox


def sync_mail_wait_state(source: dict[str, Any], target: dict[str, Any]) -> None:
    if source is target:
        return
    for key in ("_last_code_meta", "_exclude_codes"):
        if key in source:
            target[key] = source[key]


def mail_wait_config_for_account(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    data_dir: Path,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
) -> RegisterConfig:
    provider = mailbox_mail_provider_config(account, mailbox, data_dir)
    return RegisterConfig(
        proxy=str(proxy or "").strip(),
        mail=MailConfig(
            wait_timeout=int(wait_timeout or 30),
            wait_interval=int(wait_interval or 2),
            request_timeout=int(request_timeout or 30),
            proxy=str(proxy or "").strip(),
            providers=[provider] if provider else [],
        ),
    )


def bind_mail_config_for_account(
    account: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    data_dir: Path,
    proxy: str,
    wait_timeout: int,
    wait_interval: int,
    request_timeout: int,
) -> dict[str, Any]:
    provider = mailbox_mail_provider_config(account, mailbox, data_dir)
    providers = [provider] if provider else []
    provider_type = str(provider.get("type") or "").strip().lower() if provider else ""
    if provider_type in {"icloud", "icloud-hme", "icloud_hme"}:
        fallback_provider = load_webui_cloudflare_fallback_provider(data_dir)
        if fallback_provider:
            providers.append(fallback_provider)
    return {
        "proxy": str(proxy or "").strip(),
        "wait_timeout": int(wait_timeout or 30),
        "wait_interval": int(wait_interval or 2),
        "request_timeout": int(request_timeout or 30),
        "providers": providers,
    }


def login_identifier_for_account(account: dict[str, Any], mailbox: dict[str, Any], fallback_email: str) -> str:
    for value in (mailbox.get("bind_email"), mailbox.get("email"), account.get("email"), fallback_email):
        candidate = str(value or "").strip()
        if "@" in candidate:
            return candidate
    phone = str(mailbox.get("phone_number") or account.get("phone_number") or "").strip()
    source = str(account.get("source") or "").strip().lower()
    if phone and (bool(mailbox.get("phone_number_verified")) or source in {"phone_signup", "phone_direct", "hero_phone_bind"}):
        return phone
    return str(fallback_email or "").strip()


def bind_email_hint_for_account(account: dict[str, Any], mailbox: dict[str, Any], fallback_email: str) -> str:
    for value in (mailbox.get("bind_email"), mailbox.get("email"), account.get("email"), fallback_email):
        candidate = str(value or "").strip()
        if "@" in candidate:
            return candidate
    return ""
