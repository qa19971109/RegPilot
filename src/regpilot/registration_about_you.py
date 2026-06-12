from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any


def _extract_attr(attrs: str, name: str) -> str:
    match = re.search(rf'\b{name}\s*=\s*(["\'])(.*?)\1', attrs, re.I | re.S)
    if match:
        return match.group(2)
    match = re.search(rf"\b{name}\s*=\s*([^\s>]+)", attrs, re.I | re.S)
    return match.group(1) if match else ""


def age_from_birthdate(birthdate: str, today: date | None = None) -> int:
    current = today or date.today()
    try:
        year_text, month_text, day_text = str(birthdate or "").split("-", 2)
        born = date(int(year_text), int(month_text), int(day_text))
        age = current.year - born.year - ((current.month, current.day) < (born.month, born.day))
        return max(18, min(80, age))
    except Exception:
        return 21


def about_you_form_age_from_birthdate(birthdate: str) -> int:
    try:
        birth_year = int(str(birthdate or "").split("-", 1)[0])
        return max(18, datetime.now(timezone.utc).year - birth_year)
    except Exception:
        return 21


def about_you_page_shape(page_context: str = "") -> dict[str, Any]:
    raw = str(page_context or "")
    lowered = raw.lower()
    shape: dict[str, Any] = {
        "visible_age": False,
        "hidden_age": False,
        "visible_birthday": False,
        "hidden_birthday": False,
        "visible_birthdate": False,
        "hidden_birthdate": False,
        "age_cue": bool(re.search(r"\bage\b|how old are you|年龄|几岁", lowered)),
        "birthday_cue": bool(re.search(r"birth(?:day|date)|date of birth|生日|出生", lowered)),
        "preferred": "unknown",
        "birthday_fields": ["birthdate", "birthday"],
    }
    for input_match in re.finditer(r"<input\b([^>]*)>", raw, re.I | re.S):
        attrs = input_match.group(1) or ""
        name = _extract_attr(attrs, "name").strip().lower()
        if name not in {"age", "birthday", "birthdate"}:
            continue
        input_type = _extract_attr(attrs, "type").strip().lower()
        hidden_attr = bool(re.search(r"(?:^|\s)hidden(?:\s|=|$)", attrs, re.I))
        style = _extract_attr(attrs, "style").lower()
        is_hidden = input_type == "hidden" or hidden_attr or "display:none" in style or "display: none" in style
        key = f"{'hidden' if is_hidden else 'visible'}_{name}"
        shape[key] = True

    if shape["visible_birthdate"]:
        shape["birthday_fields"] = ["birthdate", "birthday"]
    elif shape["visible_birthday"]:
        shape["birthday_fields"] = ["birthday", "birthdate"]
    elif "name=\"birthday\"" in lowered or "name='birthday'" in lowered:
        shape["birthday_fields"] = ["birthday", "birthdate"]

    has_visible_birthday = bool(shape["visible_birthdate"] or shape["visible_birthday"])
    if shape["visible_age"] and not has_visible_birthday:
        shape["preferred"] = "age"
    elif has_visible_birthday:
        shape["preferred"] = "birthday"
    elif shape["age_cue"] and not shape["birthday_cue"]:
        shape["preferred"] = "age"
    elif shape["birthday_cue"]:
        shape["preferred"] = "birthday"
    return shape


def about_you_consent_fields(page_context: str = "") -> dict[str, str]:
    raw = str(page_context or "")
    fields: dict[str, str] = {}
    has_explicit_marker = bool(re.search(r"isExplicitConsentRequired", raw, re.I))

    def is_required(attrs: str) -> bool:
        return bool(re.search(r"(?:^|\s)required(?:\s|=|$)", attrs, re.I))

    def is_consent_like(field_name: str, attrs: str) -> bool:
        haystack = f"{field_name} {attrs}".lower()
        return bool(
            field_name == "isexplicitconsentrequired"
            or re.search(r"consent|agree|terms|privacy|policy|checkbox|accept", haystack)
        )

    for input_match in re.finditer(r"<input\b([^>]*)>", raw, re.I | re.S):
        attrs = input_match.group(1) or ""
        name = _extract_attr(attrs, "name").strip()
        if not name:
            continue
        normalized_name = name.lower()
        if normalized_name in {"name", "age", "birthday", "birthdate"}:
            continue
        input_type = (_extract_attr(attrs, "type") or "text").strip().lower()
        value = _extract_attr(attrs, "value")
        if input_type == "hidden" and is_consent_like(normalized_name, attrs):
            fields[name] = value if value != "" else "true"
        elif input_type in {"checkbox", "radio"}:
            checked = bool(re.search(r"(?:^|\s)checked(?:\s|=|$)", attrs, re.I))
            if checked or is_required(attrs) or is_consent_like(normalized_name, attrs) or has_explicit_marker:
                fields[name] = value if value != "" else "on"

    if "isExplicitConsentRequired" not in fields:
        marker = re.search(r'\\*["\']isExplicitConsentRequired\\*["\']\s*:\s*(true|false)', raw, re.I)
        if marker:
            fields["isExplicitConsentRequired"] = marker.group(1).lower()
    return fields


def about_you_shape_log_summary(page_context: str = "") -> str:
    shape = about_you_page_shape(page_context)
    visible = []
    hidden = []
    for key, label in (("age", "年龄"), ("birthdate", "出生日期"), ("birthday", "生日")):
        if shape.get(f"visible_{key}"):
            visible.append(label)
        if shape.get(f"hidden_{key}"):
            hidden.append(label)
    cues = []
    if shape.get("age_cue"):
        cues.append("年龄")
    if shape.get("birthday_cue"):
        cues.append("生日")
    consent_fields = about_you_consent_fields(page_context)
    preferred_map = {"age": "年龄", "birthdate": "出生日期", "birthday": "生日", "unknown": "未识别"}
    return (
        f"优先填写={preferred_map.get(str(shape.get('preferred') or 'unknown'), str(shape.get('preferred') or '未识别'))} "
        f"可见字段={'+'.join(visible) or '-'} "
        f"隐藏字段={'+'.join(hidden) or '-'} "
        f"提示词={'+'.join(cues) or '-'} "
        f"同意字段={'+'.join(consent_fields.keys()) or '-'}"
    )


def about_you_create_account_payloads(name: str, birthdate: str, page_context: str = "", email: str = "") -> list[dict[str, Any]]:
    age = str(age_from_birthdate(birthdate))
    shape = about_you_page_shape(page_context)
    preferred = str(shape.get("preferred") or "unknown")
    birthday_fields = list(shape.get("birthday_fields") or ["birthdate", "birthday"])
    clean_email = str(email or "").strip()
    ordered: list[dict[str, Any]] = []

    def add(payload: dict[str, Any]) -> None:
        normalized = {key: value for key, value in payload.items() if value not in (None, "")}
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    def add_candidate(payload: dict[str, str]) -> None:
        if clean_email:
            add({**payload, "email": clean_email})
        add(payload)

    if preferred == "age":
        add_candidate({"name": name, "age": age})
        for field in birthday_fields:
            add_candidate({"name": name, field: birthdate})
    elif preferred == "birthday":
        for field in birthday_fields:
            add_candidate({"name": name, field: birthdate})
        add_candidate({"name": name, "age": age})
    else:
        add_candidate({"name": name, "birthdate": birthdate})
        add_candidate({"name": name, "birthday": birthdate})
    add_candidate({"name": name, "age": age})
    return ordered


def about_you_form_payloads(
    *,
    hidden: dict[str, str] | None,
    full_name: str,
    birthdate: str,
    page_context: str = "",
) -> list[dict[str, str]]:
    shape = about_you_page_shape(page_context)
    preferred = str(shape.get("preferred") or "unknown")
    birthday_fields = list(shape.get("birthday_fields") or ["birthdate", "birthday"])
    age_value = about_you_form_age_from_birthdate(birthdate)

    base_payload = dict(hidden or {})
    base_payload.update(about_you_consent_fields(page_context))
    for field_name in ("age", "birthday", "birthdate"):
        base_payload.pop(field_name, None)
    candidate_payloads: list[dict[str, str]] = []

    def add_payload(extra: dict[str, str]) -> None:
        payload = dict(base_payload)
        payload.update({key: str(value) for key, value in extra.items() if value not in (None, "")})
        if payload not in candidate_payloads:
            candidate_payloads.append(payload)

    detected_name = bool(re.search(r'name\s*=\s*["\']name["\']', page_context, re.I) or re.search(r'autocomplete\s*=\s*["\']name["\']', page_context, re.I))
    detected_birthday = bool(re.search(r'name\s*=\s*["\'](?:birthday|birthdate)["\']', page_context, re.I))
    detected_age = bool(re.search(r'name\s*=\s*["\']age["\']', page_context, re.I))

    if preferred == "age":
        add_payload({"name": full_name, "age": str(age_value)})
        for field_name in birthday_fields:
            add_payload({"name": full_name, field_name: birthdate})
    elif preferred == "birthday":
        for field_name in birthday_fields:
            add_payload({"name": full_name, field_name: birthdate})
        add_payload({"name": full_name, "age": str(age_value)})
    else:
        if detected_name or detected_birthday:
            for field_name in birthday_fields:
                add_payload({"name": full_name, field_name: birthdate})
        if detected_name or detected_age:
            add_payload({"name": full_name, "age": str(age_value)})

    for field_name in birthday_fields:
        add_payload({"name": full_name, field_name: birthdate})
    add_payload({"name": full_name, "age": str(age_value)})
    add_payload({"name": full_name, "birthday": birthdate, "age": str(age_value)})
    add_payload({"name": full_name, "birthday": birthdate, "allCheckboxes": "on"})
    add_payload({"name": full_name, "age": str(age_value), "allCheckboxes": "on"})
    add_payload({"name": full_name, "birthday": birthdate, "age": str(age_value), "allCheckboxes": "on"})
    return candidate_payloads
