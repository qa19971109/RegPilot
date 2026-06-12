from __future__ import annotations

import json
import time
from typing import Any

from . import api_config_values
from .api_presenters import _redact_sensitive
from .config import DATA_DIR
from .jwt_utils import decode_jwt_payload as _decode_jwt_payload


_PHONE_REUSE_POOL_CACHE: dict[str, Any] = {"mtime": None, "data": {}}


def _load_webui_config() -> dict[str, Any]:
    return api_config_values._load_webui_config()


def _load_phone_reuse_pool_cached() -> dict[str, Any]:
    pool_path = DATA_DIR / "phone_reuse_pool.json"
    try:
        mtime = pool_path.stat().st_mtime if pool_path.exists() else None
        if _PHONE_REUSE_POOL_CACHE.get("mtime") == mtime:
            return _PHONE_REUSE_POOL_CACHE.get("data") if isinstance(_PHONE_REUSE_POOL_CACHE.get("data"), dict) else {}
        data = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else {}
        if not isinstance(data, dict):
            data = {}
        _PHONE_REUSE_POOL_CACHE.update({"mtime": mtime, "data": data})
        return data
    except Exception:
        return {}


def _jwt_exp_value(payload: dict[str, Any]) -> int:
    try:
        return int((payload or {}).get("exp") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _account_token_status(item: dict[str, Any]) -> dict[str, Any]:
    access_token = str(item.get("access_token") or "").strip()
    refresh_token = str(item.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        mailbox = item.get("mailbox") if isinstance(item.get("mailbox"), dict) else {}
        if mailbox.get("_cpa_submit_ok") and str(item.get("callback_url") or mailbox.get("_callback_url") or "").strip():
            return {"token_status": "cpa_only", "token_status_label": "仅CPA", "token_refreshable": False}
        return {"token_status": "missing", "token_status_label": "无", "token_refreshable": False}
    access_payload = _decode_jwt_payload(access_token) or {}
    exp = _jwt_exp_value(access_payload)
    if str(item.get("last_error") or "").startswith("refresh_token_failed:"):
        return {"token_status": "reauth_required", "token_status_label": "需重新授权", "token_refreshable": False, "token_expires_at": exp}
    if exp and exp <= int(time.time()):
        return {"token_status": "access_expired", "token_status_label": "需刷新", "token_refreshable": True, "token_expires_at": exp}
    return {"token_status": "refreshable", "token_status_label": "可刷新", "token_refreshable": True, "token_expires_at": exp}


def _account_phone_status(item: dict[str, Any]) -> dict[str, Any]:
    access_payload = _decode_jwt_payload(str(item.get("access_token") or "")) or {}
    id_payload = _decode_jwt_payload(str(item.get("id_token") or "")) or {}
    profile = access_payload.get("https://api.openai.com/profile") if isinstance(access_payload.get("https://api.openai.com/profile"), dict) else {}
    phone_number = str(profile.get("phone_number") or "").strip()
    verified = bool(profile.get("phone_number_verified"))
    if phone_number and verified:
        return {"phone_status": "verified", "phone_status_label": "已绑定", "phone_number": phone_number}
    mailbox = item.get("mailbox") if isinstance(item.get("mailbox"), dict) else {}
    if str(mailbox.get("phone_number") or "").strip():
        return {"phone_status": "bound_recorded", "phone_status_label": "已绑定", "phone_number": str(mailbox.get("phone_number") or "").strip()}
    try:
        pool = _load_phone_reuse_pool_cached()
        account_id = str(item.get("id") or "").strip()
        email = str(item.get("email") or "").strip()
        for row in pool.get("items") or []:
            if not isinstance(row, dict):
                continue
            for account in row.get("accounts") or []:
                if not isinstance(account, dict):
                    continue
                if (account_id and str(account.get("account_id") or "") == account_id) or (email and str(account.get("email") or "") == email):
                    phone = str(row.get("phone_number") or "").strip()
                    if phone:
                        return {"phone_status": "bound_recorded", "phone_status_label": "已绑定", "phone_number": phone}
    except Exception:
        pass
    if str(item.get("status") or "") == "authorized" and (str(item.get("callback_url") or "").strip() or item.get("access_token") or item.get("id_token")):
        return {"phone_status": "authorized_assumed", "phone_status_label": "已绑定（授权通过）", "phone_number": ""}
    if item.get("access_token") or item.get("id_token"):
        return {"phone_status": "missing", "phone_status_label": "未绑定", "phone_number": ""}
    return {"phone_status": "unknown", "phone_status_label": "未知", "phone_number": ""}


def _configured_cloudflare_domains() -> set[str]:
    domains: set[str] = set()
    try:
        data = _load_webui_config()
    except Exception:
        data = {}
    for section_name in ("register", "phone_direct", "hero_phone_bind"):
        section = data.get(section_name) if isinstance(data, dict) else {}
        if not isinstance(section, dict):
            continue
        domain = str(section.get("cf_temp_domain") or "").strip().lower().lstrip("@")
        if domain:
            domains.add(domain)
    return domains


def _account_mail_provider_status(item: dict[str, Any]) -> dict[str, Any]:
    mailbox = item.get("mailbox") if isinstance(item.get("mailbox"), dict) else {}
    provider = str(mailbox.get("provider") or "").strip().lower().replace("_", "-")
    if provider in {"icloud-hme", "icloud"}:
        provider = "icloud"
    if provider in {"outlook-api", "microsoft-mail"}:
        provider = "hotmail-api"
    if not provider:
        mailbox_domain = str(mailbox.get("domain") or "").strip().lower().lstrip("@")
        email = str(item.get("email") or mailbox.get("email") or mailbox.get("bind_email") or "").strip().lower()
        email_domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        cloudflare_domains = _configured_cloudflare_domains()
        if mailbox_domain in cloudflare_domains or email_domain in cloudflare_domains:
            provider = "cloudflare-temp-email"
    labels = {
        "cloudflare-temp-email": "Cloudflare",
        "icloud": "iCloud",
        "hotmail-api": "Outlook/Hotmail",
    }
    return {
        "mail_provider": provider,
        "mail_provider_label": labels.get(provider, provider or "-"),
    }


def _account_with_token_status(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out.update(_account_token_status(item))
    out.update(_account_phone_status(item))
    out.update(_account_mail_provider_status(item))
    return out


def _safe_account_with_status(item: dict[str, Any]) -> dict[str, Any]:
    return _redact_sensitive(_account_with_token_status(item))
