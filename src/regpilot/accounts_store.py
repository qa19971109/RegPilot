from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ensure_dirs
from .register_core import RegistrationResult

DB_PATH = DATA_DIR / "accounts.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def get_db_path() -> Path:
    ensure_dirs()
    return DB_PATH


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    ensure_dirs()
    with connect_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'register',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_auth_at TEXT NOT NULL DEFAULT '',
                last_sub2api_submit_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                callback_url TEXT NOT NULL DEFAULT '',
                access_token TEXT NOT NULL DEFAULT '',
                refresh_token TEXT NOT NULL DEFAULT '',
                id_token TEXT NOT NULL DEFAULT '',
                mailbox_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                usable_for_reauth INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status)")
        conn.execute(
            """
            UPDATE accounts
            SET status = 'authorized'
            WHERE source = 'reauthorize'
              AND status = 'active'
              AND last_error = ''
              AND callback_url <> ''
            """
        )
        conn.commit()


def _row_to_account(row: sqlite3.Row) -> dict[str, Any]:
    mailbox = {}
    tags: list[str] = []
    try:
        mailbox = json.loads(row["mailbox_json"] or "{}")
    except Exception:
        mailbox = {}
    try:
        parsed_tags = json.loads(row["tags_json"] or "[]")
        if isinstance(parsed_tags, list):
            tags = [str(item) for item in parsed_tags]
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "email": row["email"],
        "password": row["password"],
        "status": row["status"],
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_auth_at": row["last_auth_at"],
        "last_sub2api_submit_at": row["last_sub2api_submit_at"],
        "last_error": row["last_error"],
        "callback_url": row["callback_url"],
        "access_token": row["access_token"],
        "refresh_token": row["refresh_token"],
        "id_token": row["id_token"],
        "mailbox": mailbox,
        "notes": row["notes"],
        "tags": tags,
        "usable_for_reauth": bool(row["usable_for_reauth"]),
    }


def _account_search_clause(search: str) -> tuple[str, list[str]]:
    terms = [item.strip().lower() for item in str(search or "").split() if item.strip()]
    if not terms:
        return "", []
    fields = ("id", "email", "status", "source", "notes", "mailbox_json", "tags_json")
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        clauses.append("(" + " OR ".join(f"lower({field}) LIKE ?" for field in fields) + ")")
        params.extend([f"%{term}%"] * len(fields))
    return " WHERE " + " AND ".join(clauses), params


def list_accounts(limit: int = 200, offset: int = 0, search: str = "") -> list[dict[str, Any]]:
    init_db()
    where, params = _account_search_clause(search)
    with connect_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM accounts{where} ORDER BY datetime(updated_at) DESC, rowid DESC LIMIT ? OFFSET ?",
            (*params, max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
    return [_row_to_account(row) for row in rows]


def count_accounts(search: str = "") -> int:
    init_db()
    where, params = _account_search_clause(search)
    with connect_db() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM accounts{where}", params).fetchone()
    return int(row["total"] or 0) if row else 0


def get_account(account_id: str) -> dict[str, Any] | None:
    init_db()
    with connect_db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (str(account_id),)).fetchone()
    return _row_to_account(row) if row else None


_UPSERT_ACCOUNT_SQL = """
    INSERT INTO accounts (
        id, email, password, status, source, created_at, updated_at,
        last_auth_at, last_sub2api_submit_at, last_error, callback_url,
        access_token, refresh_token, id_token, mailbox_json, notes, tags_json, usable_for_reauth
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        email=excluded.email,
        password=excluded.password,
        status=excluded.status,
        source=excluded.source,
        updated_at=excluded.updated_at,
        last_auth_at=excluded.last_auth_at,
        last_sub2api_submit_at=excluded.last_sub2api_submit_at,
        last_error=excluded.last_error,
        callback_url=excluded.callback_url,
        access_token=excluded.access_token,
        refresh_token=excluded.refresh_token,
        id_token=excluded.id_token,
        mailbox_json=excluded.mailbox_json,
        notes=excluded.notes,
        tags_json=excluded.tags_json,
        usable_for_reauth=excluded.usable_for_reauth
    """


def _stored_account_text(record: dict[str, Any], key: str, default: str = "") -> str:
    return str(record.get(key) if key in record else default or "")


def _stored_account_secret(record: dict[str, Any], existing: dict[str, Any] | None, key: str) -> str:
    raw = str(record.get(key) or "")
    if existing and (key not in record or raw in {"", "***"}):
        return str(existing.get(key) or "")
    return raw


def _stored_existing_when_missing(
    record: dict[str, Any],
    existing: dict[str, Any] | None,
    key: str,
    default: str = "",
) -> str:
    if existing and key not in record:
        return str(existing.get(key) or "")
    return str(record.get(key) or default)


def _upsert_account_values(
    record: dict[str, Any],
    existing: dict[str, Any] | None,
    account_id: str,
    created_at: str,
    now: str,
) -> tuple[Any, ...]:
    return (
        account_id,
        str(record.get("email") or "").strip(),
        _stored_account_secret(record, existing, "password"),
        str(record.get("status") or "active"),
        str(record.get("source") or "register"),
        created_at,
        now,
        _stored_existing_when_missing(record, existing, "last_auth_at"),
        _stored_existing_when_missing(record, existing, "last_sub2api_submit_at"),
        _stored_existing_when_missing(record, existing, "last_error"),
        _stored_account_secret(record, existing, "callback_url"),
        _stored_account_secret(record, existing, "access_token"),
        _stored_account_secret(record, existing, "refresh_token"),
        _stored_account_secret(record, existing, "id_token"),
        json.dumps(record.get("mailbox") or {}, ensure_ascii=False),
        _stored_account_text(record, "notes"),
        json.dumps(record.get("tags") or [], ensure_ascii=False),
        1 if bool(record.get("usable_for_reauth", True)) else 0,
    )


def upsert_account(record: dict[str, Any]) -> dict[str, Any]:
    init_db()
    account_id = str(record.get("id") or uuid.uuid4().hex)
    now = _utc_now_iso()
    existing = get_account(account_id)
    created_at = str((existing or {}).get("created_at") or now)
    values = _upsert_account_values(record, existing, account_id, created_at, now)
    with connect_db() as conn:
        conn.execute(_UPSERT_ACCOUNT_SQL, values)
        conn.commit()
    return get_account(account_id) or {"id": account_id}


def delete_account(account_id: str) -> bool:
    init_db()
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM accounts WHERE id = ?", (str(account_id),))
        conn.commit()
        return int(cursor.rowcount or 0) > 0


def delete_accounts(account_ids: list[str]) -> dict[str, Any]:
    ids = [str(item).strip() for item in account_ids if str(item).strip()]
    if not ids:
        return {"requested": 0, "deleted": 0, "ids": []}
    init_db()
    with connect_db() as conn:
        cursor = conn.executemany("DELETE FROM accounts WHERE id = ?", [(item,) for item in ids])
        conn.commit()
        # sqlite executemany rowcount can be unreliable on some builds; count by requested - remaining.
        remaining = {
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM accounts WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
        }
    deleted_ids = [item for item in ids if item not in remaining]
    return {"requested": len(ids), "deleted": len(deleted_ids), "ids": deleted_ids}


def save_registration_result_to_account(result: RegistrationResult, *, source: str = "register", account_id: str = "") -> dict[str, Any]:
    mailbox = result.mailbox if isinstance(result.mailbox, dict) else {}
    now = _utc_now_iso()
    existing = get_account(str(account_id).strip()) if str(account_id or "").strip() else None
    stored_source = str(source or "register")
    if stored_source == "reauthorize" and existing:
        stored_source = str(existing.get("source") or "manual")
    cpa_submitted = bool(result.ok) and bool(mailbox.get("_cpa_submit_ok")) and bool(str(getattr(result, "callback_url", "") or "").strip())
    record = {
        "email": str(getattr(result, "email", "") or "").strip(),
        "password": str(getattr(result, "password", "") or ""),
        "status": "authorized" if cpa_submitted else ("active" if bool(result.ok) else "failed"),
        "source": stored_source,
        "last_auth_at": now if bool(result.ok) else "",
        "last_sub2api_submit_at": now if cpa_submitted else "",
        "last_error": str(getattr(result, "error", "") or ""),
        "callback_url": str(getattr(result, "callback_url", "") or ""),
        "access_token": str(getattr(result, "access_token", "") or ""),
        "refresh_token": str(getattr(result, "refresh_token", "") or ""),
        "id_token": str(getattr(result, "id_token", "") or ""),
        "mailbox": mailbox,
        "usable_for_reauth": True,
    }
    if str(account_id or "").strip():
        record["id"] = str(account_id).strip()
    return upsert_account(record)
