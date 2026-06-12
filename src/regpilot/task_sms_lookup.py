from __future__ import annotations

from typing import Any, Callable

from .oauth_token_flow import (
    HeroSMSConfig,
    fetch_country_name_zh_map,
    fetch_hero_sms_countries,
    fetch_hero_sms_price_summary,
    fetch_hero_sms_quote_list,
)


__all__ = [
    "DEFAULT_HERO_COUNTRY_LABEL_BY_ID",
    "hero_country_label",
    "hero_country_lookup",
    "hero_price_lookup",
]


SmsConfigFromPayload = Callable[[dict[str, Any]], HeroSMSConfig]
FetchCountries = Callable[[HeroSMSConfig], list[dict[str, Any]]]
FetchPriceSummary = Callable[..., dict[str, Any]]
FetchQuoteList = Callable[[HeroSMSConfig], dict[str, Any]]
FetchCountryNameMap = Callable[[], dict[str, str]]


DEFAULT_HERO_COUNTRY_LABEL_BY_ID = {
    "6": "\u5370\u5ea6\u5c3c\u897f\u4e9a",
    "52": "\u6cf0\u56fd",
    "187": "\u7f8e\u56fd\uff08\u5b9e\u4f53\uff09",
    "16": "\u82f1\u683c\u5170",
    "43": "\u5fb7\u56fd",
    "73": "\u6cd5\u56fd",
    "10": "\u8d8a\u5357",
}


def hero_country_label(
    country_id: str,
    eng_name: str = "",
    *,
    fetch_country_name_map_fn: FetchCountryNameMap = fetch_country_name_zh_map,
) -> str:
    normalized_id = str(country_id or "").strip()
    if normalized_id in DEFAULT_HERO_COUNTRY_LABEL_BY_ID:
        return DEFAULT_HERO_COUNTRY_LABEL_BY_ID[normalized_id]
    normalized_eng = str(eng_name or "").strip()
    if normalized_eng:
        zh_map = fetch_country_name_map_fn()
        return str(zh_map.get(normalized_eng) or normalized_eng).strip()
    return f"\u56fd\u5bb6 #{country_id}"


def hero_price_lookup(
    payload: dict[str, Any],
    *,
    sms_config_from_payload_fn: SmsConfigFromPayload,
    fetch_countries_fn: FetchCountries = fetch_hero_sms_countries,
    fetch_price_summary_fn: FetchPriceSummary = fetch_hero_sms_price_summary,
    fetch_quote_list_fn: FetchQuoteList = fetch_hero_sms_quote_list,
) -> dict[str, Any]:
    config = sms_config_from_payload_fn(payload)
    if not config.api_key:
        raise ValueError("sms_api_key_required")
    country = str(config.country or "16").strip() or "16"
    eng_name = ""
    try:
        countries = fetch_countries_fn(config)
        eng_name = next((str(item.get("eng") or "") for item in countries if str(item.get("id") or "") == country), "")
    except Exception:
        eng_name = ""
    label = hero_country_label(country, eng_name)
    summary = fetch_price_summary_fn(config, country_label=label)
    if config.provider in {"smsbower", "5sim"}:
        return {"ok": True, "provider": config.provider, **summary}
    quotes = fetch_quote_list_fn(config)
    quote_list = quotes.get("quote_list") or []
    if quote_list:
        top_line = " | ".join(f"${float(item['price']):.4f} x{int(item['quantity'])}" for item in quote_list[:12])
        operator_count = int(quotes.get("operator_count") or 0)
        summary["summary"] = (
            f"Operators: {operator_count or 1}; "
            f"Price tiers: {len(quote_list)}; {top_line}"
        )
    return {"ok": True, "provider": config.provider, **summary, **quotes}


def hero_country_lookup(
    payload: dict[str, Any],
    *,
    sms_config_from_payload_fn: SmsConfigFromPayload,
    fetch_countries_fn: FetchCountries = fetch_hero_sms_countries,
) -> dict[str, Any]:
    config = sms_config_from_payload_fn(payload)
    if not config.api_key:
        raise ValueError("sms_api_key_required")
    selected = str(config.country or "16").strip() or "16"
    items = fetch_countries_fn(config)
    return {
        "ok": True,
        "provider": config.provider,
        "selected_country": selected,
        "items": [
            {
                "id": item["id"],
                "label": str(item.get("label") or "") or hero_country_label(item["id"], str(item.get("eng") or "")),
                "eng": str(item.get("eng") or ""),
                "visible": bool(item.get("visible")),
            }
            for item in items
            if item.get("visible") is not False
        ],
    }
