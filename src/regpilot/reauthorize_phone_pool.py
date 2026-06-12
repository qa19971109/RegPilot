from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .sms_activation_helpers import normalize_sms_provider
from .sms_provider_config import HeroSMSConfig


AcquirePhone = Callable[[HeroSMSConfig], dict[str, Any]]
SetSmsStatus = Callable[[HeroSMSConfig, str, int], Any]
WriteJsonAtomic = Callable[[Path, dict[str, Any]], Any]


def phone_pool_now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def phone_reuse_pool_path(data_dir: Path, pool_name: str) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / pool_name


def load_phone_reuse_pool(data_dir: Path, pool_name: str) -> dict[str, Any]:
    path = phone_reuse_pool_path(data_dir, pool_name)
    if not path.exists():
        return {"items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}
    if not isinstance(data, dict):
        return {"items": []}
    items = data.get("items")
    if not isinstance(items, list):
        data["items"] = []
    return data


def save_phone_reuse_pool(data: dict[str, Any], data_dir: Path, pool_name: str, write_json_atomic_fn: WriteJsonAtomic) -> None:
    path = phone_reuse_pool_path(data_dir, pool_name)
    payload = data if isinstance(data, dict) else {"items": []}
    payload.setdefault("items", [])
    write_json_atomic_fn(path, payload)


def phone_pool_key(config: HeroSMSConfig, activation_id: str) -> str:
    return f"{normalize_sms_provider(config.provider)}:{str(activation_id or '').strip()}"


def phone_pool_matches_config(item: dict[str, Any], config: HeroSMSConfig, reuse_limit: int) -> bool:
    return (
        str(item.get("status") or "active") == "active"
        and str(item.get("provider") or "") == normalize_sms_provider(config.provider)
        and str(item.get("country") or "") == str(config.country or "")
        and str(item.get("service") or "") == str(config.service or "")
        and bool(str(item.get("activation_id") or "").strip())
        and bool(str(item.get("phone_number") or "").strip())
        and int(item.get("use_count") or 0) < int(item.get("max_uses") or reuse_limit)
    )


def find_reusable_phone_activation(config: HeroSMSConfig, *, data_dir: Path, pool_name: str, reuse_limit: int) -> dict[str, str]:
    pool = load_phone_reuse_pool(data_dir, pool_name)
    for item in pool.get("items") or []:
        if isinstance(item, dict) and phone_pool_matches_config(item, config, reuse_limit):
            return {
                "activation_id": str(item.get("activation_id") or "").strip(),
                "phone_number": str(item.get("phone_number") or "").strip(),
                "reuse_count": str(int(item.get("use_count") or 0)),
                "max_uses": str(int(item.get("max_uses") or reuse_limit)),
                "reused": "1",
            }
    return {}


def acquire_or_reuse_phone_activation(
    config: HeroSMSConfig,
    *,
    data_dir: Path,
    pool_name: str,
    reuse_limit: int,
    acquire_phone_fn: AcquirePhone,
) -> dict[str, str]:
    reused = find_reusable_phone_activation(config, data_dir=data_dir, pool_name=pool_name, reuse_limit=reuse_limit)
    if reused:
        return reused
    activation = acquire_phone_fn(config)
    return {
        "activation_id": str(activation.get("activation_id") or "").strip(),
        "phone_number": str(activation.get("phone_number") or "").strip(),
        "price": str(activation.get("price") or "").strip(),
        "reuse_count": "0",
        "max_uses": str(reuse_limit),
        "reused": "0",
    }


def retire_phone_activation(
    config: HeroSMSConfig,
    activation_id: str,
    *,
    reason: str = "",
    data_dir: Path,
    pool_name: str,
    write_json_atomic_fn: WriteJsonAtomic,
) -> None:
    activation_id = str(activation_id or "").strip()
    if not activation_id:
        return
    pool = load_phone_reuse_pool(data_dir, pool_name)
    key = phone_pool_key(config, activation_id)
    changed = False
    for item in pool.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "") == key or str(item.get("activation_id") or "") == activation_id:
            item["status"] = "retired"
            item["retired_reason"] = str(reason or "").strip()
            item["updated_at"] = phone_pool_now()
            changed = True
    if changed:
        save_phone_reuse_pool(pool, data_dir, pool_name, write_json_atomic_fn)


def record_phone_activation_success(
    config: HeroSMSConfig,
    activation_id: str,
    phone_number: str,
    *,
    account_id: str = "",
    email: str = "",
    data_dir: Path,
    pool_name: str,
    reuse_limit: int,
    write_json_atomic_fn: WriteJsonAtomic,
) -> dict[str, Any]:
    activation_id = str(activation_id or "").strip()
    phone_number = str(phone_number or "").strip()
    if not activation_id or not phone_number:
        return {"use_count": 0, "max_uses": reuse_limit, "completed": False}
    pool = load_phone_reuse_pool(data_dir, pool_name)
    items = pool.setdefault("items", [])
    key = phone_pool_key(config, activation_id)
    item = next((row for row in items if isinstance(row, dict) and str(row.get("key") or "") == key), None)
    if item is None:
        item = {
            "key": key,
            "provider": normalize_sms_provider(config.provider),
            "activation_id": activation_id,
            "phone_number": phone_number,
            "country": str(config.country or ""),
            "service": str(config.service or ""),
            "use_count": 0,
            "max_uses": reuse_limit,
            "status": "active",
            "accounts": [],
            "created_at": phone_pool_now(),
        }
        items.append(item)
    accounts = item.setdefault("accounts", [])
    if not isinstance(accounts, list):
        accounts = []
        item["accounts"] = accounts
    item["phone_number"] = phone_number
    item["provider"] = normalize_sms_provider(config.provider)
    item["country"] = str(config.country or "")
    item["service"] = str(config.service or "")
    item["max_uses"] = int(item.get("max_uses") or reuse_limit)
    item["use_count"] = min(int(item.get("max_uses") or reuse_limit), int(item.get("use_count") or 0) + 1)
    if account_id or email:
        accounts.append({"account_id": str(account_id or ""), "email": str(email or ""), "at": phone_pool_now()})
    completed = int(item["use_count"]) >= int(item["max_uses"])
    item["status"] = "completed" if completed else "active"
    item["updated_at"] = phone_pool_now()
    save_phone_reuse_pool(pool, data_dir, pool_name, write_json_atomic_fn)
    return {
        "use_count": int(item["use_count"]),
        "max_uses": int(item["max_uses"]),
        "completed": completed,
        "remaining": max(0, int(item["max_uses"]) - int(item["use_count"])),
    }


def set_phone_activation_after_success(config: HeroSMSConfig, activation_id: str, usage: dict[str, Any], set_status_fn: SetSmsStatus) -> None:
    if bool(usage.get("completed")):
        set_status_fn(config, activation_id, 6)
    else:
        # SMS-Activate compatible APIs use status=3 to request the next SMS on the same activation.
        set_status_fn(config, activation_id, 3)
