from __future__ import annotations

import html
import re


def workspace_id_from_consent_html(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    variants = [raw]
    unescaped = html.unescape(raw)
    if unescaped != raw:
        variants.append(unescaped)
    slash_unescaped = raw.replace('\\"', '"').replace("\\'", "'")
    if slash_unescaped not in variants:
        variants.append(slash_unescaped)
    for value in variants:
        patterns = [
            r"name\s*=\s*[\"']workspace_id[\"'][^>]*value\s*=\s*[\"']([^\"']+)",
            r"value\s*=\s*[\"']([^\"']+)[\"'][^>]*name\s*=\s*[\"']workspace_id[\"']",
            r"[\\]?[\"']current_workspace_id[\\]?[\"']\s*:\s*[\\]?[\"']([^\\\"']+)",
            r"[\\]?[\"']workspace_id[\\]?[\"']\s*:\s*[\\]?[\"']([^\\\"']+)",
            r"[\\]?[\"']workspaceId[\\]?[\"']\s*:\s*[\\]?[\"']([^\\\"']+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, value, re.I | re.S)
            if match:
                workspace_id = str(match.group(1) or "").strip()
                if workspace_id:
                    return workspace_id
    return ""


def org_project_from_consent_html(text: str) -> tuple[str, str]:
    raw = str(text or "")
    if not raw:
        return "", ""
    variants = [raw]
    unescaped = html.unescape(raw)
    if unescaped != raw:
        variants.append(unescaped)
    slash_unescaped = raw.replace('\\"', '"').replace("\\'", "'")
    if slash_unescaped not in variants:
        variants.append(slash_unescaped)
    for value in variants:
        normalized = value.replace('\\"', '"').replace("\\/", "/").replace("\\n", " ")
        orgs_index = normalized.find('"orgs"')
        if orgs_index < 0:
            orgs_index = normalized.find("organizations")
        if orgs_index < 0:
            continue
        window = normalized[orgs_index:orgs_index + 20000]
        org_match = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{20,})"', window)
        if not org_match:
            continue
        org_id = org_match.group(1).strip()
        project_id = ""
        projects_index = window.find('"projects"')
        if projects_index >= 0:
            project_window = window[projects_index:projects_index + 6000]
            project_match = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{20,})"', project_window)
            if project_match:
                project_id = project_match.group(1).strip()
        return org_id, project_id
    return "", ""


def extract_attr(attrs: str, name: str) -> str:
    text = str(attrs or "")
    match = re.search(rf'{re.escape(name)}\s*=\s*["\']([^"\']*)["\']', text, re.I | re.S)
    if not match:
        match = re.search(rf'{re.escape(name)}\s*=\s*([^\s"\'<>`]+)', text, re.I | re.S)
    return html.unescape(str(match.group(1) or "").strip()) if match else ""


def strip_html_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or ""))


def _score_consent_form(match: re.Match[str]) -> tuple[int, int]:
    attrs = match.group(1) or ""
    body = match.group(2) or ""
    haystack = f"{attrs}\n{strip_html_tags(body)}".lower()
    score = 0
    for token, weight in [
        ("codex", 30),
        ("consent", 24),
        ("authorize", 24),
        ("allow", 18),
        ("approve", 18),
        ("continue", 10),
        ("confirm", 10),
        ("state", 4),
    ]:
        if token in haystack:
            score += weight
    if re.search(r"<button\b|type\s*=\s*[\"']submit[\"']", body, re.I):
        score += 8
    return score, len(body)


def _score_consent_submit(label: str, input_type: str) -> int:
    haystack = f"{label} {input_type}".lower()
    score = 0
    for token, weight in [
        ("codex", 40),
        ("authorize", 35),
        ("allow", 30),
        ("approve", 30),
        ("consent", 24),
        ("confirm", 16),
        ("continue", 12),
        ("submit", 6),
    ]:
        if token in haystack:
            score += weight
    return score


def _better_submit_choice(
    current: tuple[int, str, str, str] | None,
    candidate: tuple[int, str, str, str],
) -> tuple[int, str, str, str]:
    return candidate if current is None or candidate[0] > current[0] else current


def _collect_consent_input_payload(form_body: str) -> tuple[dict[str, str], tuple[int, str, str, str] | None]:
    payload: dict[str, str] = {}
    submit_choice: tuple[int, str, str, str] | None = None

    for input_match in re.finditer(r"<input\b([^>]*)>", form_body, re.I | re.S):
        attrs = input_match.group(1) or ""
        name = extract_attr(attrs, "name")
        if not name:
            continue
        input_type = extract_attr(attrs, "type").lower()
        value = extract_attr(attrs, "value")
        if input_type in ("hidden", "checkbox", "radio"):
            if input_type not in ("checkbox", "radio") or re.search(r"\bchecked\b", attrs, re.I):
                payload[name] = value
        if input_type in ("submit", "button", "image"):
            candidate = (_score_consent_submit(f"{name} {value}", input_type), name, value, extract_attr(attrs, "formaction"))
            submit_choice = _better_submit_choice(submit_choice, candidate)
    return payload, submit_choice


def _collect_consent_button_choice(form_body: str) -> tuple[int, str, str, str] | None:
    submit_choice: tuple[int, str, str, str] | None = None

    for button_match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", form_body, re.I | re.S):
        attrs = button_match.group(1) or ""
        button_type = extract_attr(attrs, "type").lower() or "submit"
        if button_type not in ("", "submit"):
            continue
        name = extract_attr(attrs, "name")
        value = extract_attr(attrs, "value") or strip_html_tags(button_match.group(2) or "").strip()
        candidate = (_score_consent_submit(f"{name} {value}", button_type), name, value, extract_attr(attrs, "formaction"))
        submit_choice = _better_submit_choice(submit_choice, candidate)
    return submit_choice


def _apply_consent_submit_choice(
    action: str,
    payload: dict[str, str],
    submit_choice: tuple[int, str, str, str] | None,
) -> tuple[str, dict[str, str]]:
    if not submit_choice:
        return action, payload
    if submit_choice[1] and submit_choice[1] not in payload:
        payload[submit_choice[1]] = submit_choice[2]
    if submit_choice[3]:
        action = submit_choice[3]
    return action, payload


def extract_consent_form_inputs(html_text: str) -> tuple[str, dict[str, str]]:
    text = str(html_text or "")
    form_matches = list(re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.I | re.S))
    if not form_matches:
        return "", {}

    form_match = max(form_matches, key=_score_consent_form)
    form_attrs = form_match.group(1) or ""
    form_body = form_match.group(2) or ""
    action = extract_attr(form_attrs, "action")
    payload, input_submit_choice = _collect_consent_input_payload(form_body)
    button_submit_choice = _collect_consent_button_choice(form_body)
    submit_choice = _better_submit_choice(input_submit_choice, button_submit_choice) if button_submit_choice else input_submit_choice
    return _apply_consent_submit_choice(action, payload, submit_choice)
