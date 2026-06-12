from __future__ import annotations

import json
import re
from typing import Any


def normalize_sms_provider(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"5sim", "five_sim", "fivesim", "five"}:
        return "5sim"
    if normalized in {"smsbower", "sms_bower", "smsbower_page"}:
        return "smsbower"
    return "hero_sms"


def is_5sim_config(config: Any) -> bool:
    provider = normalize_sms_provider(getattr(config, "provider", ""))
    base_url = str(getattr(config, "base_url", "") or "").lower()
    return provider == "5sim" or "5sim.net" in base_url


def is_smsbower_config(config: Any) -> bool:
    provider = normalize_sms_provider(getattr(config, "provider", ""))
    base_url = str(getattr(config, "base_url", "") or "").lower()
    return provider == "smsbower" or "smsbower" in base_url


def sms_provider_label(config: Any) -> str:
    if is_5sim_config(config):
        return "5sim"
    return "SMSBower" if is_smsbower_config(config) else "HeroSMS"


def hero_sms_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        if payload.get("status") and payload.get("message"):
            return f"{payload.get('status')}:{payload.get('message')}"
        if payload.get("activationId") or payload.get("phoneNumber"):
            return f"ACCESS_NUMBER:{payload.get('activationId') or payload.get('id')}:{payload.get('phoneNumber') or payload.get('number')}"
        if payload.get("sms"):
            return json.dumps(payload, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False) if payload is not None else ""


def extract_sms_code(value: Any) -> str:
    text = hero_sms_text(value)
    if isinstance(value, dict):
        candidates = [
            value.get("code"),
            value.get("sms_code"),
            (value.get("sms") or {}).get("code") if isinstance(value.get("sms"), dict) else "",
            (value.get("sms") or {}).get("text") if isinstance(value.get("sms"), dict) else "",
        ]
        if isinstance(value.get("sms"), list):
            for item in value.get("sms") or []:
                if not isinstance(item, dict):
                    continue
                candidates.extend([item.get("code"), item.get("text")])
        for candidate in candidates:
            match = re.search(r"\b(\d{4,8})\b", str(candidate or ""))
            if match:
                return match.group(1)
    match = re.search(r"(?:STATUS_OK|code|sms)[^0-9]{0,30}(\d{4,8})", text, re.I)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{4,8})\b", text)
    return match.group(1) if match else ""


def extract_5sim_sms_code(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    sms_rows = value.get("sms")
    if isinstance(sms_rows, dict):
        sms_rows = [sms_rows]
    if not isinstance(sms_rows, list):
        return ""
    for item in sms_rows:
        if not isinstance(item, dict):
            continue
        for candidate in (item.get("code"), item.get("text")):
            match = re.search(r"\b(\d{4,8})\b", str(candidate or ""))
            if match:
                return match.group(1)
    return ""


def normalize_hero_sms_price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return round(number, 4) if number > 0 else None
    match = re.search(r"(\d+(?:\.\d+)?)", str(value).strip())
    if not match:
        return None
    number = float(match.group(1))
    return round(number, 4) if number > 0 else None


def normalize_acquired_phone_number(phone_number: str) -> str:
    text = str(phone_number or "").strip()
    if text and not text.startswith("+"):
        text = f"+{re.sub(r'[^0-9]', '', text)}"
    return text


def activation_price_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("activationCost", "activation_cost", "price", "cost", "activationPrice", "activation_price"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def activation_result(activation_id: str, phone_number: str, *, price: str = "") -> dict[str, str]:
    result = {
        "activation_id": str(activation_id or "").strip(),
        "phone_number": normalize_acquired_phone_number(phone_number),
    }
    if price:
        result["price"] = str(price).strip()
    return result
