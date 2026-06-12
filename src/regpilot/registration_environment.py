from __future__ import annotations

import contextvars
import random
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .config import RegisterConfig, parse_bool


AUTH_BASE = "https://auth.openai.com"
DEFAULT_ENV_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.7049.115 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.6998.166 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.6943.127 Safari/537.36",
]
DEFAULT_ENV_ACCEPT_LANGUAGE_POOL = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,zh-CN;q=0.8",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8",
]
DEFAULT_ENV_TIMEZONE_POOL = [
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Asia/Singapore",
]
DEFAULT_ENV_VIEWPORT_POOL = [
    (1920, 1080),
    (1680, 1050),
    (1600, 900),
    (1536, 864),
    (1440, 900),
    (1366, 768),
]
_ENV_SPLIT_RE = re.compile(r"[\r\n,;]+")
_ENV_LOCK = threading.RLock()


@dataclass
class EnvironmentProfile:
    user_agent: str
    accept_language: str
    timezone: str
    viewport_width: int
    viewport_height: int
    proxy: str = ""
    randomized: bool = False


def _chrome_major_from_ua(ua: str) -> str:
    match = re.search(r"Chrome/(\d+)", str(ua or ""))
    return str(match.group(1)) if match else "136"


def _chrome_full_from_ua(ua: str) -> str:
    match = re.search(r"Chrome/([0-9.]+)", str(ua or ""))
    return str(match.group(1)) if match else "136.0.7103.92"


def _sec_ch_ua_for_major(major: str) -> str:
    safe_major = str(major or "136").strip() or "136"
    return f'"Not(A:Brand";v="99", "Google Chrome";v="{safe_major}", "Chromium";v="{safe_major}"'


def _sec_ch_ua_full_version_list(full_version: str) -> str:
    safe = str(full_version or "136.0.7103.92").strip() or "136.0.7103.92"
    return f'"Chromium";v="{safe}", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="{safe}"'


def _split_pool_text(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    items = [part.strip() for part in _ENV_SPLIT_RE.split(text)]
    return [item for item in items if item]


def _parse_viewport(item: str) -> tuple[int, int] | None:
    token = str(item or "").strip().lower().replace(" ", "")
    if not token:
        return None
    match = re.match(r"^(\d{3,4})[x\*](\d{3,4})$", token)
    if not match:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    if width < 320 or height < 320:
        return None
    return width, height


def _viewport_pool_from_text(raw: str) -> list[tuple[int, int]]:
    parsed: list[tuple[int, int]] = []
    for token in _split_pool_text(raw):
        viewport = _parse_viewport(token)
        if viewport:
            parsed.append(viewport)
    return parsed


def _build_common_headers() -> dict[str, str]:
    return {
        "accept": "application/json",
        "accept-language": get_accept_language(),
        "content-type": "application/json",
        "origin": AUTH_BASE,
        "priority": "u=1, i",
        "user-agent": get_user_agent(),
        "sec-ch-ua": get_sec_ch_ua(),
        "sec-ch-ua-arch": '"x86_64"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version-list": get_sec_ch_ua_full_version_list(),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"10.0.0"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }


def _build_navigate_headers() -> dict[str, str]:
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": get_accept_language(),
        "user-agent": get_user_agent(),
        "sec-ch-ua": get_sec_ch_ua(),
        "sec-ch-ua-arch": '"x86_64"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version-list": get_sec_ch_ua_full_version_list(),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"10.0.0"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }


def _default_environment_profile(proxy: str = "") -> EnvironmentProfile:
    ua = DEFAULT_ENV_UA_POOL[0]
    viewport = DEFAULT_ENV_VIEWPORT_POOL[0]
    return EnvironmentProfile(
        user_agent=ua,
        accept_language=DEFAULT_ENV_ACCEPT_LANGUAGE_POOL[0],
        timezone=DEFAULT_ENV_TIMEZONE_POOL[0],
        viewport_width=int(viewport[0]),
        viewport_height=int(viewport[1]),
        proxy=str(proxy or "").strip(),
        randomized=False,
    )


def build_environment_profile(
    *,
    enabled: bool,
    fallback_proxy: str = "",
    proxy_pool_text: str = "",
    ua_pool_text: str = "",
    accept_language_pool_text: str = "",
    timezone_pool_text: str = "",
    viewport_pool_text: str = "",
) -> EnvironmentProfile:
    fallback = _default_environment_profile(proxy=fallback_proxy)
    if not enabled:
        return fallback

    ua_pool = _split_pool_text(ua_pool_text) or list(DEFAULT_ENV_UA_POOL)
    lang_pool = _split_pool_text(accept_language_pool_text) or list(DEFAULT_ENV_ACCEPT_LANGUAGE_POOL)
    tz_pool = _split_pool_text(timezone_pool_text) or list(DEFAULT_ENV_TIMEZONE_POOL)
    viewport_pool = _viewport_pool_from_text(viewport_pool_text) or list(DEFAULT_ENV_VIEWPORT_POOL)
    proxy_pool = _split_pool_text(proxy_pool_text)
    selected_proxy = random.choice(proxy_pool) if proxy_pool else str(fallback_proxy or "").strip()
    viewport = random.choice(viewport_pool)
    return EnvironmentProfile(
        user_agent=random.choice(ua_pool),
        accept_language=random.choice(lang_pool),
        timezone=random.choice(tz_pool),
        viewport_width=int(viewport[0]),
        viewport_height=int(viewport[1]),
        proxy=selected_proxy,
        randomized=True,
    )


def prepare_environment_profile_from_config(config: RegisterConfig) -> EnvironmentProfile:
    return build_environment_profile(
        enabled=parse_bool(getattr(config, "env_random_enabled", False), key="env_random_enabled"),
        fallback_proxy=str(getattr(config, "proxy", "") or ""),
        proxy_pool_text=str(getattr(config, "env_proxy_pool", "") or ""),
        ua_pool_text=str(getattr(config, "env_ua_pool", "") or ""),
        accept_language_pool_text=str(getattr(config, "env_accept_language_pool", "") or ""),
        timezone_pool_text=str(getattr(config, "env_timezone_pool", "") or ""),
        viewport_pool_text=str(getattr(config, "env_viewport_pool", "") or ""),
    )


def prepare_environment_profile_from_payload(payload: dict[str, Any], fallback_proxy: str = "") -> EnvironmentProfile:
    data = payload if isinstance(payload, dict) else {}
    return build_environment_profile(
        enabled=parse_bool(data.get("env_random_enabled"), key="env_random_enabled"),
        fallback_proxy=str(data.get("proxy") or fallback_proxy or ""),
        proxy_pool_text=str(data.get("env_proxy_pool") or ""),
        ua_pool_text=str(data.get("env_ua_pool") or ""),
        accept_language_pool_text=str(data.get("env_accept_language_pool") or ""),
        timezone_pool_text=str(data.get("env_timezone_pool") or ""),
        viewport_pool_text=str(data.get("env_viewport_pool") or ""),
    )


def summarize_environment_profile(profile: EnvironmentProfile) -> str:
    ua_major = _chrome_major_from_ua(profile.user_agent)
    mode = "随机" if profile.randomized else "默认"
    proxy_text = profile.proxy if profile.proxy else "无"
    return (
        f"{mode} UA=Chrome/{ua_major} 语言={profile.accept_language} "
        f"时区={profile.timezone} 视口={profile.viewport_width}x{profile.viewport_height} 代理={proxy_text}"
    )


def _snapshot_environment_state() -> dict[str, Any]:
    return {
        "user_agent": _user_agent_var.get(),
        "sec_ch_ua": _sec_ch_ua_var.get(),
        "sec_ch_ua_full_version_list": _sec_ch_ua_full_version_list_var.get(),
        "current_accept_language": _accept_language_var.get(),
        "current_timezone": _timezone_var.get(),
        "current_viewport_width": _viewport_width_var.get(),
        "current_viewport_height": _viewport_height_var.get(),
        "common_headers": _build_common_headers(),
        "navigate_headers": _build_navigate_headers(),
    }


def _apply_environment_state(profile: EnvironmentProfile) -> None:
    ua = str(profile.user_agent).strip() or DEFAULT_ENV_UA_POOL[0]
    major = _chrome_major_from_ua(ua)
    full = _chrome_full_from_ua(ua)
    accept_language = str(profile.accept_language).strip() or DEFAULT_ENV_ACCEPT_LANGUAGE_POOL[0]
    timezone = str(profile.timezone).strip() or DEFAULT_ENV_TIMEZONE_POOL[0]
    viewport_width = int(profile.viewport_width or DEFAULT_ENV_VIEWPORT_POOL[0][0])
    viewport_height = int(profile.viewport_height or DEFAULT_ENV_VIEWPORT_POOL[0][1])
    _user_agent_var.set(ua)
    _sec_ch_ua_var.set(_sec_ch_ua_for_major(major))
    _sec_ch_ua_full_version_list_var.set(_sec_ch_ua_full_version_list(full))
    _accept_language_var.set(accept_language)
    _timezone_var.set(timezone)
    _viewport_width_var.set(viewport_width)
    _viewport_height_var.set(viewport_height)


def _restore_environment_state(snapshot: dict[str, Any]) -> None:
    ua = str(snapshot.get("user_agent") or DEFAULT_ENV_UA_POOL[0])
    _user_agent_var.set(ua)
    _sec_ch_ua_var.set(str(snapshot.get("sec_ch_ua") or _sec_ch_ua_for_major(_chrome_major_from_ua(ua))))
    _sec_ch_ua_full_version_list_var.set(
        str(snapshot.get("sec_ch_ua_full_version_list") or _sec_ch_ua_full_version_list(_chrome_full_from_ua(ua)))
    )
    _accept_language_var.set(str(snapshot.get("current_accept_language") or DEFAULT_ENV_ACCEPT_LANGUAGE_POOL[0]))
    _timezone_var.set(str(snapshot.get("current_timezone") or DEFAULT_ENV_TIMEZONE_POOL[0]))
    _viewport_width_var.set(int(snapshot.get("current_viewport_width") or DEFAULT_ENV_VIEWPORT_POOL[0][0]))
    _viewport_height_var.set(int(snapshot.get("current_viewport_height") or DEFAULT_ENV_VIEWPORT_POOL[0][1]))


@contextmanager
def environment_profile_context(profile: EnvironmentProfile):
    snapshot = _snapshot_environment_state()
    _apply_environment_state(profile)
    try:
        yield
    finally:
        _restore_environment_state(snapshot)


_DEFAULT_ENV = _default_environment_profile()
_user_agent_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "regpilot_user_agent", default=_DEFAULT_ENV.user_agent
)
_sec_ch_ua_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "regpilot_sec_ch_ua", default=_sec_ch_ua_for_major(_chrome_major_from_ua(_DEFAULT_ENV.user_agent))
)
_sec_ch_ua_full_version_list_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "regpilot_sec_ch_ua_full_version_list",
    default=_sec_ch_ua_full_version_list(_chrome_full_from_ua(_DEFAULT_ENV.user_agent)),
)
_accept_language_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "regpilot_accept_language", default=_DEFAULT_ENV.accept_language
)
_timezone_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "regpilot_timezone", default=_DEFAULT_ENV.timezone
)
_viewport_width_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "regpilot_viewport_width", default=_DEFAULT_ENV.viewport_width
)
_viewport_height_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "regpilot_viewport_height", default=_DEFAULT_ENV.viewport_height
)


def get_user_agent() -> str:
    return _user_agent_var.get()


def get_sec_ch_ua() -> str:
    return _sec_ch_ua_var.get()


def get_sec_ch_ua_full_version_list() -> str:
    return _sec_ch_ua_full_version_list_var.get()


def get_accept_language() -> str:
    return _accept_language_var.get()


def get_timezone() -> str:
    return _timezone_var.get()


def get_viewport_width() -> int:
    return _viewport_width_var.get()


def get_viewport_height() -> int:
    return _viewport_height_var.get()


def get_common_headers() -> dict[str, str]:
    return _build_common_headers()


def get_navigate_headers() -> dict[str, str]:
    return _build_navigate_headers()


def __getattr__(name: str) -> Any:
    if name == "user_agent":
        return _user_agent_var.get()
    if name == "sec_ch_ua":
        return _sec_ch_ua_var.get()
    if name == "sec_ch_ua_full_version_list":
        return _sec_ch_ua_full_version_list_var.get()
    if name == "current_accept_language":
        return _accept_language_var.get()
    if name == "current_timezone":
        return _timezone_var.get()
    if name == "current_viewport_width":
        return _viewport_width_var.get()
    if name == "current_viewport_height":
        return _viewport_height_var.get()
    if name == "common_headers":
        return _build_common_headers()
    if name == "navigate_headers":
        return _build_navigate_headers()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
