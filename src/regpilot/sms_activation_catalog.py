from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import quote

import requests

from . import sms_activation_helpers


MANUAL_COUNTRY_NAME_ZH_MAP: dict[str, str] = {
    "Azerbaijan": "阿塞拜疆",
    "Bolivia": "玻利维亚",
    "Bosnia": "波黑",
    "Cambodia": "柬埔寨",
    "Cameroon": "喀麦隆",
    "Canada": "加拿大",
    "Cape Verde": "佛得角",
    "Chad": "乍得",
    "Chile": "智利",
    "China": "中国",
    "Colombia": "哥伦比亚",
    "Comoros": "科摩罗",
    "Congo": "刚果（布）",
    "Costa Rica": "哥斯达黎加",
    "Croatia": "克罗地亚",
    "Cyprus": "塞浦路斯",
    "Czech": "捷克",
    "Czechia": "捷克",
    "Denmark": "丹麦",
    "Djibouti": "吉布提",
    "Dominican Republic": "多米尼加共和国",
    "DR Congo": "刚果（金）",
    "East Timor": "东帝汶",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Hong Kong": "中国香港",
    "Indonesia": "印度尼西亚",
    "Ivory Coast": "科特迪瓦",
    "Kazakhstan": "哈萨克斯坦",
    "Kyrgyzstan": "吉尔吉斯斯坦",
    "Laos": "老挝",
    "Macao": "中国澳门",
    "Moldova": "摩尔多瓦",
    "New Caledonia": "新喀里多尼亚",
    "Palestine": "巴勒斯坦",
    "Papua": "巴布亚新几内亚",
    "Philippines": "菲律宾",
    "Puerto Rico": "波多黎各",
    "Reunion": "留尼汪",
    "Russia": "俄罗斯",
    "Saint Lucia": "圣卢西亚",
    "Salvador": "萨尔瓦多",
    "Sao Tome and Principe": "圣多美和普林西比",
    "Singapore": "新加坡",
    "Solomon Islands": "所罗门群岛",
    "South Africa": "南非",
    "Sri Lanka": "斯里兰卡",
    "Swaziland": "斯威士兰",
    "Syria": "叙利亚",
    "Taiwan": "中国台湾",
    "Tanzania": "坦桑尼亚",
    "Thailand": "泰国",
    "Timor-Leste": "东帝汶",
    "Trinidad and Tobago": "特立尼达和多巴哥",
    "Ukraine": "乌克兰",
    "United Kingdom": "英国",
    "USA": "美国（实体）",
    "USA (virtual)": "美国（虚拟）",
    "USA (physical)": "美国（实体）",
    "Venezuela": "委内瑞拉",
    "Vietnam": "越南",
    "Western Sahara": "西撒哈拉",
}


def resolve_hero_sms_stock_state(payload: Any) -> tuple[bool, int]:
    if not isinstance(payload, dict):
        return False, 0
    if payload.get("physicalCount") is not None:
        try:
            physical_count = int(float(payload.get("physicalCount")))
        except Exception:
            physical_count = 0
        return True, max(physical_count, 0)
    stock_candidates = []
    for key in ("count", "stock", "available", "quantity", "qty", "left", "free"):
        try:
            numeric = int(float(payload.get(key)))
        except Exception:
            continue
        stock_candidates.append(numeric)
    if not stock_candidates:
        return False, 0
    return True, max(stock_candidates)


def resolve_hero_sms_display_quantity(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("count", "stock", "available", "quantity", "qty", "left", "free", "physicalCount"):
        try:
            value = payload.get(key)
            if value is None or value == "":
                continue
            return max(int(float(value)), 0)
        except Exception:
            continue
    return None


def collect_hero_sms_price_candidates(
    payload: Any,
    *,
    include_zero_stock: bool = False,
    candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = candidates if candidates is not None else []
    if isinstance(payload, list):
        for entry in payload:
            collect_hero_sms_price_candidates(entry, include_zero_stock=include_zero_stock, candidates=rows)
        return rows
    if not isinstance(payload, dict):
        return rows

    cost = sms_activation_helpers.normalize_hero_sms_price(payload.get("cost"))
    if cost is not None:
        has_stock, stock_count = resolve_hero_sms_stock_state(payload)
        display_quantity = resolve_hero_sms_display_quantity(payload)
        if include_zero_stock or (not has_stock or stock_count > 0):
            rows.append(
                {
                    "price": cost,
                    "stock": stock_count if has_stock else None,
                    "display_quantity": display_quantity,
                }
            )

    for key, value in payload.items():
        keyed_price = sms_activation_helpers.normalize_hero_sms_price(key)
        if keyed_price is None:
            continue
        if isinstance(value, dict):
            has_stock, stock_count = resolve_hero_sms_stock_state(value)
            display_quantity = resolve_hero_sms_display_quantity(value)
            if has_stock and (include_zero_stock or stock_count > 0):
                rows.append({"price": keyed_price, "stock": stock_count, "display_quantity": display_quantity})
            continue
        try:
            numeric_count = int(float(value))
        except Exception:
            continue
        if include_zero_stock or numeric_count > 0:
            rows.append({"price": keyed_price, "stock": numeric_count, "display_quantity": numeric_count})

    for value in payload.values():
        collect_hero_sms_price_candidates(value, include_zero_stock=include_zero_stock, candidates=rows)
    return rows


def build_sorted_unique_price_candidates(values: list[Any]) -> list[float]:
    normalized = []
    for value in values:
        price = sms_activation_helpers.normalize_hero_sms_price(value)
        if price is None:
            continue
        normalized.append(round(float(price), 4))
    return sorted(set(normalized))


def build_hero_sms_price_tiers(raw_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_price: dict[float, int | None] = {}
    by_display_quantity: dict[float, int | None] = {}
    for row in raw_candidates:
        price = float(row["price"])
        stock = row.get("stock")
        display_quantity = row.get("display_quantity")
        if price not in by_price:
            by_price[price] = stock
            by_display_quantity[price] = display_quantity
            continue
        if stock is None:
            continue
        current = by_price[price]
        by_price[price] = stock if current is None else max(int(current), int(stock))
        current_display = by_display_quantity.get(price)
        if display_quantity is not None:
            by_display_quantity[price] = display_quantity if current_display is None else max(int(current_display), int(display_quantity))
    return [
        {"price": price, "stock": by_price[price], "quantity": by_display_quantity.get(price)}
        for price in sorted(by_price)
    ]


def fetch_hero_sms_price_payloads(
    config: Any,
    *,
    hero_sms_request_fn: Any,
) -> tuple[list[Any], list[dict[str, str]]]:
    payloads: list[Any] = []
    errors: list[dict[str, str]] = []
    actions = [
        ("getPricesExtended", {"freePrice": "true"}),
        ("getPrices", {}),
    ]
    for action, extra_query in actions:
        try:
            payload = hero_sms_request_fn(
                config,
                {
                    "action": action,
                    "service": str(config.service or "dr"),
                    "country": str(config.country or "52"),
                    **extra_query,
                },
            )
            payloads.append(payload)
        except Exception as exc:
            errors.append({"action": action, "message": str(exc)})
    return payloads, errors


def _first_catalog_quantity(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        try:
            raw_quantity = payload.get(key)
            if raw_quantity is None or raw_quantity == "":
                continue
            return max(int(float(raw_quantity)), 0)
        except Exception:
            continue
    return None


def _fetch_5sim_price_summary(
    config: Any,
    *,
    country_label: str = "",
    fivesim_request_fn: Any,
) -> dict[str, Any]:
    country = str(config.country or "england").strip() or "england"
    service = str(config.service or "openai").strip() or "openai"
    payload = fivesim_request_fn(config, f"/guest/products/{quote(country, safe='')}/any")
    product_payload = payload.get(service) if isinstance(payload, dict) else {}
    if not isinstance(product_payload, dict):
        product_payload = {}
    price = sms_activation_helpers.normalize_hero_sms_price(
        product_payload.get("Price")
        or product_payload.get("price")
        or product_payload.get("cost")
    )
    quantity = _first_catalog_quantity(product_payload, ("Qty", "qty", "count", "quantity", "stock"))
    tiers = [{"price": price, "stock": quantity, "quantity": quantity}] if price is not None else []
    return {
        "country": country,
        "country_label": str(country_label or country).strip(),
        "service": service,
        "min_price": sms_activation_helpers.normalize_hero_sms_price(getattr(config, "min_price", 0.0)),
        "max_price": sms_activation_helpers.normalize_hero_sms_price(config.max_price),
        "lowest_price": price,
        "tier_count": len(tiers),
        "tiers": tiers,
        "effective_prices": [price] if price is not None else [],
        "effective_price_count": 1 if price is not None else 0,
        "min_catalog_price": price,
        "synthetic_user_limit_probe": False,
        "errors": [],
        "summary": (
            f"5sim {service}：{price:.4f}"
            + (f"(x{quantity})" if quantity is not None else "")
            if price is not None
            else "未获取到可解析的价格档位"
        ),
        "raw": payload,
    }


def _collect_price_summary_candidates(payloads: list[Any]) -> tuple[list[dict[str, Any]], list[float], list[float]]:
    raw_candidates = []
    for item in payloads:
        raw_candidates.extend(collect_hero_sms_price_candidates(item, include_zero_stock=True))
    tiers = build_hero_sms_price_tiers(raw_candidates)
    in_stock_candidates = build_sorted_unique_price_candidates(
        [row["price"] for item in payloads for row in collect_hero_sms_price_candidates(item, include_zero_stock=False)]
    )
    all_catalog_candidates = build_sorted_unique_price_candidates([row["price"] for row in raw_candidates])
    return tiers, in_stock_candidates, all_catalog_candidates


def _effective_price_candidates(
    in_stock_candidates: list[float],
    all_catalog_candidates: list[float],
    *,
    user_min: float | None,
    user_limit: float | None,
) -> tuple[list[float | None], bool]:
    merged_candidates = (
        build_sorted_unique_price_candidates(in_stock_candidates + all_catalog_candidates)
        if in_stock_candidates
        else []
    )
    if user_min is not None:
        merged_candidates = [price for price in merged_candidates if price >= user_min]
    synthetic_user_limit_probe = False
    if user_limit is not None:
        bounded = [price for price in merged_candidates if price <= user_limit]
        if bounded:
            effective_prices = bounded
        else:
            effective_prices = [user_limit]
            synthetic_user_limit_probe = True
    elif merged_candidates:
        effective_prices = merged_candidates
    else:
        effective_prices = [None]
    return effective_prices, synthetic_user_limit_probe


def _format_hero_sms_tier_text(tiers: list[dict[str, Any]]) -> str:
    return ", ".join(
        f"{item['price']:.4f}(x{item['quantity'] if item.get('quantity') is not None else item['stock'] if item['stock'] is not None else '?'})"
        for item in tiers
    )


def _format_hero_sms_price_summary(
    *,
    effective_text: str,
    tiers: list[dict[str, Any]],
    min_catalog_price: float | None,
    synthetic_user_limit_probe: bool,
) -> str:
    tier_text = _format_hero_sms_tier_text(tiers)
    return (
        (
            f"有效价格计划：{effective_text or '无'}"
            + (f"；目录最低价：{min_catalog_price:.4f}" if min_catalog_price is not None else "")
            + (f"；目录档位：{tier_text}" if tier_text else "")
            + ("；当前为按最高价探测的虚拟档位" if synthetic_user_limit_probe else "")
        )
        if tiers or effective_text
        else "未获取到可解析的价格档位"
    )


def _fetch_regular_hero_sms_price_summary(
    config: Any,
    *,
    country_label: str = "",
    hero_sms_request_fn: Any,
) -> dict[str, Any]:
    payloads, errors = fetch_hero_sms_price_payloads(config, hero_sms_request_fn=hero_sms_request_fn)
    payload = payloads[-1] if payloads else {}
    tiers, in_stock_candidates, all_catalog_candidates = _collect_price_summary_candidates(payloads)
    merged_candidates = build_sorted_unique_price_candidates(in_stock_candidates + all_catalog_candidates) if in_stock_candidates else []
    min_catalog_price = all_catalog_candidates[0] if all_catalog_candidates else (merged_candidates[0] if merged_candidates else None)
    user_min = sms_activation_helpers.normalize_hero_sms_price(getattr(config, "min_price", 0.0))
    user_limit = sms_activation_helpers.normalize_hero_sms_price(config.max_price)
    effective_prices, synthetic_user_limit_probe = _effective_price_candidates(
        in_stock_candidates,
        all_catalog_candidates,
        user_min=user_min,
        user_limit=user_limit,
    )
    lowest_price = effective_prices[0] if effective_prices and effective_prices[0] is not None else None
    effective_text = ", ".join(f"{price:.4f}" for price in effective_prices if price is not None)
    return {
        "country": str(config.country or "").strip(),
        "country_label": str(country_label or config.country or "").strip(),
        "service": str(config.service or "dr"),
        "min_price": user_min,
        "max_price": user_limit,
        "lowest_price": lowest_price,
        "tier_count": len(tiers),
        "tiers": tiers,
        "effective_prices": effective_prices,
        "effective_price_count": len([price for price in effective_prices if price is not None]),
        "min_catalog_price": min_catalog_price,
        "synthetic_user_limit_probe": synthetic_user_limit_probe,
        "errors": errors,
        "summary": _format_hero_sms_price_summary(
            effective_text=effective_text,
            tiers=tiers,
            min_catalog_price=min_catalog_price,
            synthetic_user_limit_probe=synthetic_user_limit_probe,
        ),
        "raw": payload,
    }


def fetch_hero_sms_price_summary(
    config: Any,
    *,
    country_label: str = "",
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
) -> dict[str, Any]:
    if sms_activation_helpers.is_5sim_config(config):
        return _fetch_5sim_price_summary(
            config,
            country_label=country_label,
            fivesim_request_fn=fivesim_request_fn,
        )
    return _fetch_regular_hero_sms_price_summary(
        config,
        country_label=country_label,
        hero_sms_request_fn=hero_sms_request_fn,
    )


def fetch_hero_sms_country_catalog(config: Any, *, hero_sms_request_fn: Any) -> list[dict[str, Any]]:
    payload = hero_sms_request_fn(
        config,
        {
            "action": "getPrices",
            "service": str(config.service or "dr"),
        },
    )
    if not isinstance(payload, dict):
        return []
    items: list[dict[str, Any]] = []
    for raw_country_id, country_payload in payload.items():
        country_id = str(raw_country_id or "").strip()
        if not country_id.isdigit():
            continue
        service_payload = country_payload
        if isinstance(country_payload, dict) and str(config.service or "dr") in country_payload:
            service_payload = country_payload.get(str(config.service or "dr"))
        raw_candidates = collect_hero_sms_price_candidates(service_payload, include_zero_stock=True)
        tiers = build_hero_sms_price_tiers(raw_candidates)
        items.append(
            {
                "id": country_id,
                "tiers": tiers,
            }
        )
    items.sort(key=lambda item: int(item["id"]))
    return items


def _fivesim_country_item(country_slug: Any, row: dict[str, Any], zh_lookup: dict[str, str]) -> dict[str, Any] | None:
    slug = str(country_slug or "").strip()
    if not slug:
        return None
    eng = str(row.get("text_en") or row.get("eng") or row.get("name") or slug).strip()
    rus = str(row.get("text_ru") or row.get("rus") or "").strip()
    chn = str(zh_lookup.get(eng) or "").strip()
    label = f"{chn}（{eng}）" if chn and eng else (chn or eng or rus or slug)
    return {
        "id": slug,
        "eng": eng,
        "chn": chn,
        "name_en": eng,
        "label": label,
        "visible": True,
    }


def _fetch_fivesim_countries(config: Any, *, fivesim_request_fn: Any, country_name_map_fn: Any) -> list[dict[str, Any]]:
    payload = fivesim_request_fn(config, "/guest/countries")
    if not isinstance(payload, dict):
        return []
    zh_lookup = country_name_map_fn()
    items = [item for country_slug, row in payload.items() if isinstance(row, dict) for item in [_fivesim_country_item(country_slug, row, zh_lookup)] if item]
    items.sort(key=lambda item: str(item.get("label") or item.get("id") or "").lower())
    return items


def _hero_sms_country_rows(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        return list(payload.values())
    if isinstance(payload, list):
        return payload
    return []


def _hero_sms_country_visible(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


def _hero_sms_country_item(row: dict[str, Any]) -> dict[str, Any] | None:
    country_id = str(row.get("id") or "").strip()
    eng = str(row.get("eng") or "").strip()
    chn = str(row.get("chn") or "").strip()
    if not country_id.isdigit() or (not eng and not chn):
        return None
    return {
        "id": country_id,
        "eng": eng,
        "chn": chn,
        "label": chn or eng,
        "visible": _hero_sms_country_visible(row.get("visible")),
    }


def _fetch_regular_hero_sms_countries(config: Any, *, hero_sms_request_fn: Any) -> list[dict[str, Any]]:
    payload = hero_sms_request_fn(config, {"action": "getCountries"})
    items = [item for row in _hero_sms_country_rows(payload) if isinstance(row, dict) for item in [_hero_sms_country_item(row)] if item]
    items.sort(key=lambda item: int(item["id"]))
    return items


def fetch_hero_sms_countries(
    config: Any,
    *,
    hero_sms_request_fn: Any,
    fivesim_request_fn: Any,
    country_name_map_fn: Any,
) -> list[dict[str, Any]]:
    if sms_activation_helpers.is_5sim_config(config):
        return _fetch_fivesim_countries(
            config,
            fivesim_request_fn=fivesim_request_fn,
            country_name_map_fn=country_name_map_fn,
        )
    return _fetch_regular_hero_sms_countries(config, hero_sms_request_fn=hero_sms_request_fn)


def build_hero_sms_quote_rows(operators: list[Any]) -> dict[str, Any]:
    quote_list_by_operator: list[dict[str, Any]] = []
    quote_quantity_by_price: dict[float, int] = {}
    rent_list: list[dict[str, Any]] = []
    for operator in operators:
        if not isinstance(operator, dict):
            continue
        operator_name = str(operator.get("name") or "").strip()
        operator_label = str(operator.get("localName") or operator_name).strip()
        free_price_offers = operator.get("freePriceOffers")
        if isinstance(free_price_offers, dict):
            for price, count in free_price_offers.items():
                normalized_price = sms_activation_helpers.normalize_hero_sms_price(price)
                if normalized_price is None:
                    continue
                try:
                    quantity = max(int(float(count)), 0)
                except Exception:
                    quantity = 0
                quote_list_by_operator.append(
                    {
                        "operator_name": operator_name,
                        "operator_label": operator_label,
                        "price": normalized_price,
                        "quantity": quantity,
                    }
                )
                quote_quantity_by_price[normalized_price] = quote_quantity_by_price.get(normalized_price, 0) + quantity
        rent_offers = operator.get("rentOffers")
        current_rent = (rent_offers or {}).get("0") if isinstance(rent_offers, dict) else {}
        if isinstance(current_rent, dict):
            for hours, info in current_rent.items():
                if not isinstance(info, dict):
                    continue
                normalized_price = sms_activation_helpers.normalize_hero_sms_price(info.get("price"))
                if normalized_price is None:
                    continue
                try:
                    quantity = max(int(float(info.get("count") or 0)), 0)
                except Exception:
                    quantity = 0
                try:
                    normalized_hours = int(hours)
                except Exception:
                    continue
                rent_list.append(
                    {
                        "operator_name": operator_name,
                        "operator_label": operator_label,
                        "hours": normalized_hours,
                        "price": normalized_price,
                        "quantity": quantity,
                    }
                )
    quote_list = [{"price": price, "quantity": quote_quantity_by_price.get(price, 0)} for price in sorted(quote_quantity_by_price, reverse=True)]
    quote_list_by_operator.sort(key=lambda item: (item["price"], item["operator_label"]), reverse=True)
    rent_list.sort(key=lambda item: item["hours"])
    return {
        "quote_list": quote_list,
        "quote_list_by_operator": quote_list_by_operator,
        "rent_list": rent_list,
        "operator_count": len([item for item in operators if isinstance(item, dict)]),
    }


def fetch_hero_sms_quote_list(config: Any) -> dict[str, Any]:
    url = f"https://hero-sms.com/api/v1/left-menu/service/{str(config.service or 'dr')}/country/{str(config.country or '16')}/offers"
    response = requests.get(url, timeout=30)
    payload = response.json() if response.ok else {}
    if response.status_code >= 400:
        raise RuntimeError(f"hero_sms_offers_http_{response.status_code}")
    data = ((payload.get("data") or {}).get(str(config.service or "dr")) or {}) if isinstance(payload, dict) else {}
    operators = list(data.get("operators") or []) if isinstance(data, dict) else []
    target = None
    for item in operators:
        if str((item or {}).get("name") or "").strip().lower() == "any":
            target = item
            break
    if target is None and operators:
        target = operators[0]
    if not operators:
        return {
            "operator_name": "",
            "operator_label": "",
            "quote_list": [],
            "quote_list_by_operator": [],
            "rent_list": [],
            "raw": payload,
        }

    quote_rows = build_hero_sms_quote_rows(operators)
    return {
        "operator_name": str((target or {}).get("name") or "").strip(),
        "operator_label": str((target or {}).get("localName") or (target or {}).get("name") or "").strip(),
        **quote_rows,
        "raw": payload,
    }


def _country_name_zh_entries_from_restcountries_payload(payload: Any) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if not isinstance(payload, list):
        return lookup
    for row in payload:
        if not isinstance(row, dict):
            continue
        zh = ((row.get("translations") or {}).get("zho") or {})
        zh_name = str(zh.get("common") or zh.get("official") or "").strip()
        if not zh_name:
            continue
        names = set()
        name_info = row.get("name") or {}
        for key in ("common", "official"):
            value = str(name_info.get(key) or "").strip()
            if value:
                names.add(value)
        for alt in row.get("altSpellings") or []:
            value = str(alt or "").strip()
            if value:
                names.add(value)
        for name in names:
            lookup.setdefault(name, zh_name)
    return lookup


@lru_cache(maxsize=1)
def fetch_country_name_zh_map() -> dict[str, str]:
    try:
        response = requests.get(
            "https://restcountries.com/v3.1/all?fields=name,translations,altSpellings",
            timeout=30,
        )
        payload = response.json() if response.ok else []
    except Exception:
        payload = []
    lookup = dict(MANUAL_COUNTRY_NAME_ZH_MAP)
    for key, value in _country_name_zh_entries_from_restcountries_payload(payload).items():
        lookup.setdefault(key, value)
    return lookup


def resolve_hero_sms_catalog_price_candidates(
    config: Any,
    *,
    quote_list_fn: Any,
    price_summary_fn: Any,
) -> list[float]:
    candidates: list[float] = []
    try:
        quote_data = quote_list_fn(config)
        rows: Any = []
        if isinstance(quote_data, dict):
            rows = quote_data.get("quote_list_by_operator") or quote_data.get("quote_list")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                price = sms_activation_helpers.normalize_hero_sms_price(row.get("price"))
                quantity = max(0, int(float(row.get("quantity") or 0))) if row.get("quantity") is not None else 0
                if price is None or quantity <= 0:
                    continue
                candidates.append(price)
    except Exception:
        candidates = []

    if not candidates:
        try:
            summary = price_summary_fn(config)
            for row in summary.get("tiers") or []:
                if not isinstance(row, dict):
                    continue
                price = sms_activation_helpers.normalize_hero_sms_price(row.get("price"))
                quantity = row.get("quantity")
                stock = row.get("stock")
                has_supply = False
                for value in (quantity, stock):
                    try:
                        if int(float(value or 0)) > 0:
                            has_supply = True
                            break
                    except Exception:
                        continue
                if price is None or not has_supply:
                    continue
                candidates.append(price)
        except Exception:
            candidates = []

    min_limit = sms_activation_helpers.normalize_hero_sms_price(getattr(config, "min_price", 0.0))
    return sorted(set(round(float(price), 4) for price in candidates if min_limit is None or float(price) >= float(min_limit)))


def resolve_hero_sms_price_candidates_for_retry(
    config: Any,
    *,
    catalog_price_candidates_fn: Any,
) -> list[float]:
    unique_sorted = catalog_price_candidates_fn(config)
    user_limit = sms_activation_helpers.normalize_hero_sms_price(config.max_price)
    if user_limit is not None:
        bounded = [price for price in unique_sorted if price <= user_limit]
        if bounded:
            return bounded
        return [round(float(user_limit), 4)]
    return unique_sorted


def resolve_hero_sms_exact_request_price(
    config: Any,
    price_limit: float | None,
    *,
    catalog_price_candidates_fn: Any,
) -> str:
    normalized_limit = sms_activation_helpers.normalize_hero_sms_price(price_limit)
    if normalized_limit is None or normalized_limit <= 0:
        return ""
    try:
        candidates = catalog_price_candidates_fn(config)
    except Exception:
        candidates = []
    for candidate in candidates:
        if abs(float(candidate) - float(normalized_limit)) <= 1e-9:
            return f"{float(candidate):.4f}"
    return ""
