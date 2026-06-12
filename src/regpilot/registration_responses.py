from __future__ import annotations

from typing import Any


def response_json(resp: Any) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def accounts_error_code(info: dict[str, Any]) -> str:
    body = info.get("json") if isinstance(info.get("json"), dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    for value in (error.get("code"), body.get("code"), error.get("type"), body.get("type")):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def response_error_summary(prefix: str, info: dict[str, Any]) -> str:
    status = info.get("status")
    body = info.get("json") or {}
    code = str(body.get("code") or body.get("error_code") or "").strip()
    message = str(body.get("message") or body.get("error") or "").strip()
    pieces = [f"{prefix}_{status}"]
    if code:
        pieces.append(code)
    if message:
        pieces.append(message[:160])
    return ": ".join(pieces)
