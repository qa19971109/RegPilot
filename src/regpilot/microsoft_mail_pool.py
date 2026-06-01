from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ensure_dirs


POOL_PATH = DATA_DIR / "microsoft_mail_pool.json"
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_email(value: Any) -> str:
    return _clean_text(value).lower()


def _normalize_account(record: dict[str, Any]) -> dict[str, Any] | None:
    email = _normalize_email(record.get("email"))
    client_id = _clean_text(record.get("client_id") or record.get("clientId"))
    refresh_token = _clean_text(record.get("refresh_token") or record.get("refreshToken"))
    if "@" not in email or not client_id or not refresh_token:
        return None
    alias_index = max(0, int(record.get("alias_index") or record.get("aliasIndex") or 0))
    alias_max = max(1, min(50, int(record.get("alias_max") or record.get("aliasMax") or 5)))
    return {
        "id": _clean_text(record.get("id")) or uuid.uuid4().hex,
        "email": email,
        "password": _clean_text(record.get("password")),
        "client_id": client_id,
        "refresh_token": refresh_token,
        "status": _clean_text(record.get("status")) or "authorized",
        "used": bool(record.get("used")),
        "alias_index": alias_index,
        "alias_max": alias_max,
        "last_used_at": _clean_text(record.get("last_used_at") or record.get("lastUsedAt")),
        "last_error": _clean_text(record.get("last_error") or record.get("lastError")),
        "notes": _clean_text(record.get("notes")),
        "created_at": _clean_text(record.get("created_at") or record.get("createdAt")) or _now(),
        "updated_at": _clean_text(record.get("updated_at") or record.get("updatedAt")) or _now(),
    }


def _read_unlocked() -> dict[str, Any]:
    ensure_dirs()
    if not POOL_PATH.exists():
        return {"items": []}
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}
    items = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if isinstance(item, dict):
            normalized = _normalize_account(item)
            if normalized:
                items.append(normalized)
    return {"items": items}


def _write_unlocked(data: dict[str, Any]) -> None:
    ensure_dirs()
    POOL_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_accounts() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_read_unlocked().get("items") or [])


def count_available(*, alias_enabled: bool = False) -> int:
    return len([item for item in list_accounts() if _is_available(item, alias_enabled=alias_enabled)])


def _is_available(account: dict[str, Any], *, alias_enabled: bool) -> bool:
    if str(account.get("status") or "").strip().lower() not in {"authorized", "active", "ok"}:
        return False
    if alias_enabled:
        return int(account.get("alias_index") or 0) < int(account.get("alias_max") or 5)
    return not bool(account.get("used"))


def _alias_address(email: str, index: int) -> str:
    local, domain = email.rsplit("@", 1)
    local = re.sub(r"\+.*$", "", local)
    return f"{local}+rp{max(1, int(index))}@{domain}"


def claim_account(*, alias_enabled: bool = False, alias_max: int = 5) -> dict[str, Any]:
    with _LOCK:
        data = _read_unlocked()
        items = data.get("items") or []
        candidates = [item for item in items if _is_available(item, alias_enabled=alias_enabled)]
        if not candidates:
            raise RuntimeError("microsoft_mail_pool_empty")
        candidates.sort(key=lambda item: (str(item.get("last_used_at") or ""), str(item.get("email") or "")))
        selected = candidates[0]
        selected["last_used_at"] = _now()
        selected["updated_at"] = _now()
        selected["alias_max"] = max(1, min(50, int(alias_max or selected.get("alias_max") or 5)))
        registration_email = str(selected.get("email") or "")
        alias_index = int(selected.get("alias_index") or 0)
        if alias_enabled:
            alias_index += 1
            selected["alias_index"] = alias_index
            selected["used"] = alias_index >= int(selected.get("alias_max") or 5)
            registration_email = _alias_address(registration_email, alias_index)
        else:
            selected["used"] = True
        _write_unlocked({"items": items})
        out = dict(selected)
        out["registration_email"] = registration_email
        out["base_email"] = str(selected.get("email") or "")
        out["alias_used"] = bool(alias_enabled)
        return out


def upsert_account(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_account(record)
    if not normalized:
        raise ValueError("invalid_microsoft_mail_account")
    with _LOCK:
        data = _read_unlocked()
        items = data.get("items") or []
        existing = next((item for item in items if item.get("id") == normalized["id"] or item.get("email") == normalized["email"]), None)
        if existing:
            created_at = str(existing.get("created_at") or normalized["created_at"])
            existing.update({**normalized, "created_at": created_at, "updated_at": _now()})
            saved = existing
        else:
            items.append(normalized)
            saved = normalized
        _write_unlocked({"items": items})
        return dict(saved)


def import_accounts(text: str) -> dict[str, Any]:
    imported = 0
    skipped = 0
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = re.split(r"\s*(?:----|\t|,)\s*", raw)
        if len(parts) < 4:
            skipped += 1
            continue
        try:
            upsert_account({"email": parts[0], "password": parts[1], "client_id": parts[2], "refresh_token": parts[3]})
            imported += 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


def delete_account(account_id: str) -> bool:
    key = _clean_text(account_id)
    with _LOCK:
        data = _read_unlocked()
        items = data.get("items") or []
        kept = [item for item in items if item.get("id") != key]
        if len(kept) == len(items):
            return False
        _write_unlocked({"items": kept})
        return True


def clear_used() -> int:
    with _LOCK:
        data = _read_unlocked()
        count = 0
        for item in data.get("items") or []:
            if item.get("used") or int(item.get("alias_index") or 0) > 0:
                item["used"] = False
                item["alias_index"] = 0
                item["last_error"] = ""
                item["updated_at"] = _now()
                count += 1
        _write_unlocked(data)
        return count
