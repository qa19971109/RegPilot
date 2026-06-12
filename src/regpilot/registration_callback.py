from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_PLATFORM_BASE = "https://platform.openai.com"


def extract_oauth_callback_params_from_url(url: str) -> dict[str, str] | None:
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except Exception:
        return None
    code = str((params.get("code") or [""])[0]).strip()
    if not code:
        return None
    return {
        "code": code,
        "state": str((params.get("state") or [""])[0]).strip(),
        "scope": str((params.get("scope") or [""])[0]).strip(),
    }


def extract_oauth_callback_params_from_text(text: str, *, platform_base: str = DEFAULT_PLATFORM_BASE) -> dict[str, str] | None:
    raw = str(text or "")
    if not raw:
        return None
    variants = [raw]
    if "\\/" in raw:
        variants.append(raw.replace("\\/", "/"))
    for variant in variants:
        for match in re.finditer(r"https?://[^\"'\s<>\\]+", variant, re.I):
            callback_params = extract_oauth_callback_params_from_url(str(match.group(0) or "").strip())
            if callback_params:
                return callback_params
        for match in re.finditer(r"/(?:auth/callback)\?[^\"'\s<>\\]+", variant, re.I):
            callback_params = extract_oauth_callback_params_from_url(f"{platform_base}{str(match.group(0) or '').strip()}")
            if callback_params:
                return callback_params
    return None


def extract_oauth_callback_params_from_response(response: Any | None, *, platform_base: str = DEFAULT_PLATFORM_BASE) -> dict[str, str] | None:
    if response is None:
        return None
    callback_params = extract_oauth_callback_params_from_url(str(getattr(response, "url", "") or ""))
    if callback_params:
        return callback_params
    callback_params = extract_oauth_callback_params_from_url(str(getattr(response, "headers", {}).get("Location") or "").strip())
    if callback_params:
        return callback_params
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, (dict, list)):
        callback_params = extract_oauth_callback_params_from_text(json.dumps(body, ensure_ascii=False), platform_base=platform_base)
        if callback_params:
            return callback_params
    try:
        text = str(getattr(response, "text", "") or "")
    except Exception:
        text = ""
    return extract_oauth_callback_params_from_text(text, platform_base=platform_base)
