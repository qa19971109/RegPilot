from __future__ import annotations

import re


FormInputs = tuple[str, dict[str, str], str, str]
SubmitChoice = tuple[int, str, str, str]

FORM_SCORE_TOKENS = [
    ("codex", 30),
    ("consent", 24),
    ("authorize", 24),
    ("authorise", 24),
    ("allow", 18),
    ("approve", 18),
    ("agree", 12),
    ("continue", 10),
    ("confirm", 10),
    ("submit", 5),
    ("state", 4),
]

SUBMIT_SCORE_TOKENS = [
    ("codex", 40),
    ("authorize", 35),
    ("authorise", 35),
    ("allow", 30),
    ("approve", 30),
    ("consent", 24),
    ("agree", 18),
    ("confirm", 16),
    ("continue", 12),
    ("submit", 6),
]


def extract_attr(attrs: str, name: str) -> str:
    text = str(attrs or "")
    match = re.search(rf'{re.escape(name)}\s*=\s*["\']([^"\']*)["\']', text, re.I | re.S)
    if not match:
        match = re.search(rf'{re.escape(name)}\s*=\s*([^\s"\'<>`]+)', text, re.I | re.S)
    return str(match.group(1) or "").strip() if match else ""


def strip_html_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or ""))


def _score_form(match: re.Match[str]) -> tuple[int, int]:
    attrs = match.group(1) or ""
    body = match.group(2) or ""
    haystack = f"{attrs}\n{strip_html_tags(body)}".lower()
    score = sum(weight for token, weight in FORM_SCORE_TOKENS if token in haystack)
    if re.search(r"<button\b|type\s*=\s*[\"']submit[\"']", body, re.I):
        score += 8
    if re.search(r"name\s*=\s*[\"'](?:email|code|otp|state)[\"']", body, re.I):
        score += 4
    if re.search(r"<input\b[^>]*(?:type\s*=\s*[\"']email[\"']|name\s*=\s*[\"'][^\"']*email[^\"']*[\"'])", body, re.I):
        score += 35
    if re.search(r"<input\b[^>]*name\s*=\s*[\"'][^\"']*(?:code|otp)[^\"']*[\"']", body, re.I):
        score += 28
    return score, len(body)


def _submit_score(label: str, input_type: str) -> int:
    haystack = f"{label} {input_type}".lower()
    return sum(weight for token, weight in SUBMIT_SCORE_TOKENS if token in haystack)


def _collect_input_attrs(text: str, form_body: str, form_id: str) -> list[str]:
    input_attrs: list[str] = []
    seen_inputs: set[str] = set()
    for input_match in re.finditer(r"<input\b([^>]*)>", form_body, re.I | re.S):
        markup = input_match.group(0)
        if markup not in seen_inputs:
            seen_inputs.add(markup)
            input_attrs.append(input_match.group(1) or "")
    if form_id:
        for input_match in re.finditer(r"<input\b([^>]*)>", text, re.I | re.S):
            attrs = input_match.group(1) or ""
            markup = input_match.group(0)
            if markup not in seen_inputs and extract_attr(attrs, "form") == form_id:
                seen_inputs.add(markup)
                input_attrs.append(attrs)
    return input_attrs


def _include_checkbox_like(name: str, attrs: str, *, has_explicit_marker: bool) -> bool:
    haystack = f"{name} {attrs}".lower()
    return bool(
        re.search(r"\bchecked\b", attrs, re.I)
        or re.search(r"(?:^|\s)required(?:\s|=|$)", attrs, re.I)
        or re.search(r"consent|agree|terms|privacy|policy|checkbox|accept", haystack)
        or has_explicit_marker
    )


def _extract_input_details(
    input_attrs: list[str],
    *,
    has_explicit_marker: bool,
) -> tuple[dict[str, str], str, str, SubmitChoice | None]:
    hidden: dict[str, str] = {}
    email_name = ""
    code_name = ""
    submit_choice: SubmitChoice | None = None
    for attrs in input_attrs:
        name = extract_attr(attrs, "name")
        if not name:
            continue
        input_type = extract_attr(attrs, "type").lower()
        value = extract_attr(attrs, "value")
        if input_type in ("hidden", "checkbox", "radio"):
            if input_type not in ("checkbox", "radio") or _include_checkbox_like(name, attrs, has_explicit_marker=has_explicit_marker):
                hidden[name] = value if value != "" or input_type == "hidden" else "on"
        if input_type in ("submit", "button", "image"):
            score = _submit_score(f"{name} {value}", input_type)
            candidate = (score, name, value, extract_attr(attrs, "formaction"))
            if submit_choice is None or candidate[0] > submit_choice[0]:
                submit_choice = candidate
        if not email_name and (input_type == "email" or "email" in name.lower()):
            email_name = name
        if not code_name and ("code" in name.lower() or "otp" in name.lower()):
            code_name = name
    return hidden, email_name, code_name, submit_choice


def _collect_button_matches(text: str, form_body: str, form_id: str) -> list[tuple[str, str]]:
    button_matches: list[tuple[str, str]] = []
    seen_buttons: set[str] = set()
    for button_match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", form_body, re.I | re.S):
        markup = button_match.group(0)
        if markup not in seen_buttons:
            seen_buttons.add(markup)
            button_matches.append((button_match.group(1) or "", button_match.group(2) or ""))
    if form_id:
        for button_match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", text, re.I | re.S):
            attrs = button_match.group(1) or ""
            markup = button_match.group(0)
            if markup not in seen_buttons and extract_attr(attrs, "form") == form_id:
                seen_buttons.add(markup)
                button_matches.append((attrs, button_match.group(2) or ""))
    return button_matches


def _choose_submit_button(
    button_matches: list[tuple[str, str]],
    submit_choice: SubmitChoice | None,
) -> SubmitChoice | None:
    for attrs, body in button_matches:
        button_type = extract_attr(attrs, "type").lower() or "submit"
        if button_type not in ("", "submit"):
            continue
        name = extract_attr(attrs, "name")
        value = extract_attr(attrs, "value") or strip_html_tags(body).strip()
        score = _submit_score(f"{name} {value}", button_type)
        candidate = (score, name, value, extract_attr(attrs, "formaction"))
        if submit_choice is None or candidate[0] > submit_choice[0]:
            submit_choice = candidate
    return submit_choice


def extract_form_inputs(html_text: str) -> FormInputs:
    text = str(html_text or "")
    form_matches = list(re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.I | re.S))
    if not form_matches:
        return "", {}, "", ""

    form_match = max(form_matches, key=_score_form)
    form_attrs = form_match.group(1) or ""
    form_body = form_match.group(2) or ""
    action = extract_attr(form_attrs, "action")
    form_id = extract_attr(form_attrs, "id")

    input_attrs = _collect_input_attrs(text, form_body, form_id)
    has_explicit_marker = bool(re.search(r"isExplicitConsentRequired", text, re.I))
    hidden, email_name, code_name, submit_choice = _extract_input_details(
        input_attrs,
        has_explicit_marker=has_explicit_marker,
    )
    button_matches = _collect_button_matches(text, form_body, form_id)
    submit_choice = _choose_submit_button(button_matches, submit_choice)

    if submit_choice:
        if submit_choice[1] and submit_choice[1] not in hidden:
            hidden[submit_choice[1]] = submit_choice[2]
        if submit_choice[3]:
            action = submit_choice[3]
    return action, hidden, email_name, code_name
