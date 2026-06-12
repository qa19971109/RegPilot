from __future__ import annotations

from typing import Any

import requests

from . import sms_activation_helpers


HERO_SMS_BASE_URL = "https://hero-sms.com/stubs/handler_api.php"
FIVESIM_BASE_URL = "https://5sim.net/v1"


def hero_sms_request(config: Any, params: dict[str, Any]) -> Any:
    base_url = str(getattr(config, "base_url", "") or HERO_SMS_BASE_URL).strip()
    query = {"api_key": str(getattr(config, "api_key", "") or "").strip(), **params}
    response = requests.get(base_url, params=query, timeout=30)
    text = response.text.strip()
    try:
        payload = response.json()
    except Exception:
        payload = text
    if response.status_code >= 400:
        label = sms_activation_helpers.sms_provider_label(config)
        raise RuntimeError(f"{label} HTTP {response.status_code}: {text[:300]}")
    return payload


def fivesim_request(config: Any, path: str, params: dict[str, Any] | None = None) -> Any:
    base_url = str(getattr(config, "base_url", "") or FIVESIM_BASE_URL).strip().rstrip("/")
    clean_path = "/" + str(path or "").strip().lstrip("/")
    headers = {
        "Authorization": f"Bearer {str(getattr(config, 'api_key', '') or '').strip()}",
        "Accept": "application/json",
    }
    response = requests.get(f"{base_url}{clean_path}", params=params or {}, headers=headers, timeout=30)
    text = response.text.strip()
    try:
        payload = response.json()
    except Exception:
        payload = text
    if response.status_code >= 400:
        raise RuntimeError(f"5sim HTTP {response.status_code}: {text[:300]}")
    return payload
