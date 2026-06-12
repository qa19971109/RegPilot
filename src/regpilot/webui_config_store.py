from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import parse_bool


__all__ = [
    "WEBUI_BOOL_KEYS",
    "WEBUI_CONFIG_CACHE",
    "WEBUI_CONFIG_LOCK",
    "WEBUI_LEGACY_KEY_MIGRATIONS",
    "WEBUI_POSITIVE_INT_KEYS",
    "apply_last_result_prefill",
    "backup_corrupt_webui_config",
    "bool_from_payload",
    "clone_webui_config_defaults",
    "get_cached_webui_config",
    "invalidate_webui_config_cache",
    "load_last_result",
    "load_webui_config",
    "load_webui_config_uncached",
    "merge_webui_config",
    "migrate_legacy_webui_config",
    "positive_int_from_payload",
    "read_webui_config_json",
    "reset_webui_config",
    "sanitize_webui_config_value",
    "save_webui_config",
    "webui_config_last_valid_path",
    "webui_config_signature",
    "write_last_valid_webui_config",
]


EnsureDirs = Callable[[], Any]
WriteJsonAtomic = Callable[[Path, dict[str, Any]], Any]


WEBUI_LEGACY_KEY_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("hero_sms_wait_timeout", "sms_wait_timeout"),
    ("hero_sms_wait_interval", "sms_wait_interval"),
    ("hero_sms_resend_after_seconds", "sms_resend_after_seconds"),
    ("hero_sms_timeout_after_resend_seconds", "sms_timeout_after_resend_seconds"),
    ("hero_sms_release_after_seconds", "sms_release_after_seconds"),
    ("hero_sms_auto_retry", "sms_auto_retry"),
    ("hero_sms_retry_count", "sms_retry_count"),
)

WEBUI_BOOL_KEYS = {
    "env_random_enabled",
    "cf_temp_use_random_subdomain",
    "hotmail_alias_enabled",
    "codex2api_auto_import",
    "sms_auto_retry",
    "hero_sms_auto_retry",
    "auto_pause_on_expired",
}

WEBUI_POSITIVE_INT_KEYS = {
    "total",
    "threads",
    "request_timeout",
    "wait_timeout",
    "wait_interval",
    "sms_wait_timeout",
    "sms_wait_interval",
    "sms_resend_after_seconds",
    "sms_timeout_after_resend_seconds",
    "sms_release_after_seconds",
    "sms_retry_count",
    "concurrency",
    "priority",
    "rate_multiplier",
    "job_log_max_mb",
    "hotmail_alias_max_per_account",
}

WEBUI_CONFIG_CACHE: dict[str, Any] = {
    "signature": None,
    "data": None,
}
WEBUI_CONFIG_LOCK = threading.Lock()


def clone_webui_config_defaults(defaults: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return json.loads(json.dumps(defaults))


def load_last_result(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "last_result.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def apply_last_result_prefill(config: dict[str, dict[str, Any]], data_dir: Path) -> dict[str, dict[str, Any]]:
    merged = json.loads(json.dumps(config))
    last_result = load_last_result(data_dir)
    email = str(last_result.get("email") or "").strip()
    password = str(last_result.get("password") or "").strip()
    if email:
        hero_inject = merged.get("hero_inject") or {}
        if not str(hero_inject.get("email") or "").strip():
            hero_inject["email"] = email
    if password:
        hero_inject = merged.get("hero_inject") or {}
        if not str(hero_inject.get("password") or "").strip():
            hero_inject["password"] = password
    return merged


def migrate_legacy_webui_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        return config
    for section in config.values():
        if not isinstance(section, dict):
            continue
        for old_key, new_key in WEBUI_LEGACY_KEY_MIGRATIONS:
            if old_key in section and new_key not in section:
                section[new_key] = section[old_key]
        provider = str(section.get("sms_provider") or "").strip().lower().replace("-", "_")
        if provider in {"5sim", "five_sim", "fivesim", "five"} and not str(section.get("fivesim_api_key") or "").strip():
            section["fivesim_api_key"] = str(section.get("sms_api_key") or section.get("hero_sms_api_key") or "").strip()
    return config


def merge_webui_config(raw: Any, defaults: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged = clone_webui_config_defaults(defaults)
    if not isinstance(raw, dict):
        return merged
    raw = migrate_legacy_webui_config(raw)
    for section, section_defaults in merged.items():
        candidate = raw.get(section)
        if not isinstance(candidate, dict):
            continue
        for key in section_defaults:
            if key in candidate:
                merged[section][key] = candidate[key]
    return merged


def read_webui_config_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def backup_corrupt_webui_config(config_path: Path) -> None:
    try:
        if not config_path.exists():
            return
        backup_path = config_path.with_name(f"webui_config.corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json")
        backup_path.write_bytes(config_path.read_bytes())
    except Exception:
        pass


def webui_config_last_valid_path(data_dir: Path, config_path: Path, configured_last_valid_path: Path) -> Path:
    default_path = data_dir / "webui_config.last_valid.json"
    if configured_last_valid_path != default_path:
        return configured_last_valid_path
    return config_path.with_name("webui_config.last_valid.json")


def write_last_valid_webui_config(
    config: dict[str, dict[str, Any]],
    *,
    data_dir: Path,
    config_path: Path,
    configured_last_valid_path: Path,
    ensure_dirs_fn: EnsureDirs,
    write_json_atomic_fn: WriteJsonAtomic,
) -> None:
    try:
        ensure_dirs_fn()
        write_json_atomic_fn(webui_config_last_valid_path(data_dir, config_path, configured_last_valid_path), config)
    except Exception:
        pass


def webui_config_signature(config_path: Path, data_dir: Path) -> tuple[Any, Any]:
    try:
        mtime_webui = config_path.stat().st_mtime if config_path.exists() else None
    except Exception:
        mtime_webui = None
    try:
        last_result_path = data_dir / "last_result.json"
        mtime_last = last_result_path.stat().st_mtime if last_result_path.exists() else None
    except Exception:
        mtime_last = None
    return mtime_webui, mtime_last


def load_webui_config_uncached(
    *,
    defaults: dict[str, dict[str, Any]],
    data_dir: Path,
    config_path: Path,
    configured_last_valid_path: Path,
    ensure_dirs_fn: EnsureDirs,
    write_json_atomic_fn: WriteJsonAtomic,
) -> dict[str, dict[str, Any]]:
    if not config_path.exists():
        return apply_last_result_prefill(clone_webui_config_defaults(defaults), data_dir)
    try:
        merged = merge_webui_config(read_webui_config_json(config_path), defaults)
    except Exception:
        backup_corrupt_webui_config(config_path)
        try:
            merged = merge_webui_config(read_webui_config_json(webui_config_last_valid_path(data_dir, config_path, configured_last_valid_path)), defaults)
        except Exception:
            merged = clone_webui_config_defaults(defaults)
        return apply_last_result_prefill(merged, data_dir)
    write_last_valid_webui_config(
        merged,
        data_dir=data_dir,
        config_path=config_path,
        configured_last_valid_path=configured_last_valid_path,
        ensure_dirs_fn=ensure_dirs_fn,
        write_json_atomic_fn=write_json_atomic_fn,
    )
    return apply_last_result_prefill(merged, data_dir)


def get_cached_webui_config(
    *,
    defaults: dict[str, dict[str, Any]],
    data_dir: Path,
    config_path: Path,
    configured_last_valid_path: Path,
    ensure_dirs_fn: EnsureDirs,
    write_json_atomic_fn: WriteJsonAtomic,
) -> dict[str, dict[str, Any]]:
    signature = webui_config_signature(config_path, data_dir)
    with WEBUI_CONFIG_LOCK:
        if WEBUI_CONFIG_CACHE["data"] is not None and WEBUI_CONFIG_CACHE["signature"] == signature:
            return json.loads(json.dumps(WEBUI_CONFIG_CACHE["data"]))
        data = load_webui_config_uncached(
            defaults=defaults,
            data_dir=data_dir,
            config_path=config_path,
            configured_last_valid_path=configured_last_valid_path,
            ensure_dirs_fn=ensure_dirs_fn,
            write_json_atomic_fn=write_json_atomic_fn,
        )
        WEBUI_CONFIG_CACHE["data"] = data
        WEBUI_CONFIG_CACHE["signature"] = signature
        return json.loads(json.dumps(data))


def invalidate_webui_config_cache() -> None:
    with WEBUI_CONFIG_LOCK:
        WEBUI_CONFIG_CACHE["data"] = None
        WEBUI_CONFIG_CACHE["signature"] = None


def load_webui_config(
    *,
    defaults: dict[str, dict[str, Any]],
    data_dir: Path,
    config_path: Path,
    configured_last_valid_path: Path,
    ensure_dirs_fn: EnsureDirs,
    write_json_atomic_fn: WriteJsonAtomic,
) -> dict[str, dict[str, Any]]:
    ensure_dirs_fn()
    return get_cached_webui_config(
        defaults=defaults,
        data_dir=data_dir,
        config_path=config_path,
        configured_last_valid_path=configured_last_valid_path,
        ensure_dirs_fn=ensure_dirs_fn,
        write_json_atomic_fn=write_json_atomic_fn,
    )


def bool_from_payload(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    return parse_bool(payload.get(key, default), default=default, key=key)


def positive_int_from_payload(payload: dict[str, Any], key: str, default: int = 1) -> int:
    raw = payload.get(key, default)
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        raise ValueError(f"invalid_{key}")
    if isinstance(raw, float) and not raw.is_integer():
        raise ValueError(f"invalid_{key}")
    if isinstance(raw, str):
        text = raw.strip()
        if not re.fullmatch(r"\d+", text):
            raise ValueError(f"invalid_{key}")
        raw = text
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"invalid_{key}")
    if value < 1:
        raise ValueError(f"invalid_{key}")
    return value


def sanitize_webui_config_value(key: str, value: Any, default: Any) -> Any:
    if default == "" and value in (None, ""):
        return ""
    if key in WEBUI_BOOL_KEYS:
        return bool_from_payload({key: value}, key, bool(default))
    if key in WEBUI_POSITIVE_INT_KEYS:
        return positive_int_from_payload({key: value}, key, int(default or 1))
    return value


def save_webui_config(
    payload: dict[str, Any],
    *,
    defaults: dict[str, dict[str, Any]],
    data_dir: Path,
    config_path: Path,
    configured_last_valid_path: Path,
    ensure_dirs_fn: EnsureDirs,
    write_json_atomic_fn: WriteJsonAtomic,
) -> dict[str, dict[str, Any]]:
    merged = clone_webui_config_defaults(defaults)
    if isinstance(payload, dict):
        for section, section_defaults in merged.items():
            candidate = payload.get(section)
            if not isinstance(candidate, dict):
                continue
            for key in section_defaults:
                if key in candidate:
                    merged[section][key] = sanitize_webui_config_value(key, candidate[key], section_defaults[key])
    ensure_dirs_fn()
    write_json_atomic_fn(config_path, merged)
    write_last_valid_webui_config(
        merged,
        data_dir=data_dir,
        config_path=config_path,
        configured_last_valid_path=configured_last_valid_path,
        ensure_dirs_fn=ensure_dirs_fn,
        write_json_atomic_fn=write_json_atomic_fn,
    )
    invalidate_webui_config_cache()
    return merged


def reset_webui_config(
    *,
    defaults: dict[str, dict[str, Any]],
    data_dir: Path,
    config_path: Path,
    configured_last_valid_path: Path,
    ensure_dirs_fn: EnsureDirs,
    write_json_atomic_fn: WriteJsonAtomic,
) -> dict[str, dict[str, Any]]:
    merged = apply_last_result_prefill(clone_webui_config_defaults(defaults), data_dir)
    ensure_dirs_fn()
    write_json_atomic_fn(config_path, merged)
    write_last_valid_webui_config(
        merged,
        data_dir=data_dir,
        config_path=config_path,
        configured_last_valid_path=configured_last_valid_path,
        ensure_dirs_fn=ensure_dirs_fn,
        write_json_atomic_fn=write_json_atomic_fn,
    )
    invalidate_webui_config_cache()
    return merged
