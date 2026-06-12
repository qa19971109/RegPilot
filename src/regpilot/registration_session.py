from __future__ import annotations

import base64
import json
from typing import Any


CLIENT_AUTH_SESSION_COOKIE = "oai-client-auth-session"
AUTH_COOKIE_DOMAINS = (".auth.openai.com", "auth.openai.com")


def safe_cookie_get(session: Any, name: str, *preferred_domains: str) -> str:
    jar = getattr(session, "cookies", None)
    if jar is None:
        return ""
    for domain in preferred_domains:
        try:
            value = jar.get(name, domain=domain)
        except Exception:
            value = None
        if value:
            return str(value)
    try:
        value = jar.get(name)
    except Exception:
        value = None
    if value:
        return str(value)
    try:
        for cookie in jar:
            if getattr(cookie, "name", "") != name:
                continue
            domain = str(getattr(cookie, "domain", "") or "")
            if preferred_domains and domain not in preferred_domains:
                continue
            return str(getattr(cookie, "value", "") or "")
        for cookie in jar:
            if getattr(cookie, "name", "") == name:
                return str(getattr(cookie, "value", "") or "")
    except Exception:
        return ""
    return ""


def decode_client_auth_session_value(raw: Any) -> dict[str, Any]:
    value = str(raw or "").strip()
    if not value:
        return {}
    try:
        first_part = value.split(".")[0]
        padding = 4 - len(first_part) % 4
        if padding != 4:
            first_part += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(first_part))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def cookie_snapshot(session: Any) -> dict[str, str]:
    wanted = [
        "oai-did",
        CLIENT_AUTH_SESSION_COOKIE,
        "oai-auth-token",
        "__cf_bm",
        "cf_clearance",
        "_cfuvid",
        "did",
        "did_compat",
        "auth_session",
    ]
    values: dict[str, str] = {}
    for name in wanted:
        val = safe_cookie_get(session, name, *AUTH_COOKIE_DOMAINS)
        if val:
            values[name] = val
    return values


def summarize_cookie_snapshot(snapshot: dict[str, str]) -> dict[str, Any]:
    summary: dict[str, Any] = {"present": sorted(snapshot.keys())}
    raw = str(snapshot.get(CLIENT_AUTH_SESSION_COOKIE) or "").strip()
    if raw:
        try:
            payload = decode_client_auth_session_value(raw)
            if payload:
                summary["client_auth_session"] = {
                    "keys": sorted(payload.keys()),
                    "has_workspaces": bool(payload.get("workspaces")),
                    "has_session_id": bool(payload.get("session_id")),
                }
            else:
                summary["client_auth_session"] = {"keys": [], "has_workspaces": False, "has_session_id": False}
        except Exception as exc:
            summary["client_auth_session_decode_error"] = str(exc)
    return summary


def extract_workspace_id_from_client_auth_session(raw: str) -> str:
    payload = decode_client_auth_session_value(raw)
    workspaces = payload.get("workspaces") if isinstance(payload, dict) else None
    if isinstance(workspaces, list) and workspaces:
        return str((workspaces[0] or {}).get("id") or "").strip()
    return ""


def get_session_workspace_id(session: Any) -> str:
    raw = safe_cookie_get(session, CLIENT_AUTH_SESSION_COOKIE, *AUTH_COOKIE_DOMAINS)
    return extract_workspace_id_from_client_auth_session(raw)


def workspace_id_from_client_auth_session_cookie(session: Any) -> str:
    raw = safe_cookie_get(session, CLIENT_AUTH_SESSION_COOKIE, *AUTH_COOKIE_DOMAINS)
    payload = decode_client_auth_session_value(raw)
    return find_workspace_id_from_auth_session_node(payload) if payload else ""


def find_workspace_id_from_auth_session_node(node: Any) -> str:
    if isinstance(node, dict):
        decoded = decode_client_auth_session_value(node.get("client_auth_session"))
        if decoded:
            found = find_workspace_id_from_auth_session_node(decoded)
            if found:
                return found
        for key in ("workspace_id", "workspaceId"):
            value = str(node.get(key) or "").strip()
            if value:
                return value
        for key in ("workspaces", "workspace"):
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item_id = str(item.get("id") or "").strip()
                        if item_id:
                            return item_id
            if isinstance(value, dict):
                item_id = str(value.get("id") or "").strip()
                if item_id:
                    return item_id
            found = find_workspace_id_from_auth_session_node(value)
            if found:
                return found
        node_type = str(node.get("type") or node.get("kind") or "").lower()
        node_id = str(node.get("id") or "").strip()
        if node_id and "workspace" in node_type:
            return node_id
        for value in node.values():
            found = find_workspace_id_from_auth_session_node(value)
            if found:
                return found
    if isinstance(node, (list, tuple)):
        for item in node:
            found = find_workspace_id_from_auth_session_node(item)
            if found:
                return found
    return ""


def find_org_project_from_auth_session_node(node: Any) -> tuple[str, str]:
    if isinstance(node, dict):
        decoded = decode_client_auth_session_value(node.get("client_auth_session"))
        if decoded:
            found = find_org_project_from_auth_session_node(decoded)
            if found[0]:
                return found
        for key in ("orgs", "organizations"):
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    org_id = str(item.get("id") or item.get("organization_id") or item.get("org_id") or "").strip()
                    projects = item.get("projects") if isinstance(item.get("projects"), list) else []
                    project_id = ""
                    for project in projects:
                        if isinstance(project, dict):
                            project_id = str(project.get("id") or project.get("project_id") or "").strip()
                            if project_id:
                                break
                    if org_id and project_id:
                        return org_id, project_id
                    if org_id:
                        return org_id, ""
        node_type = str(node.get("type") or node.get("kind") or "").lower()
        node_id = str(node.get("id") or node.get("organization_id") or node.get("org_id") or "").strip()
        if node_id and ("org" in node_type or "organization" in node_type):
            projects = node.get("projects") if isinstance(node.get("projects"), list) else []
            project_id = ""
            for project in projects:
                if isinstance(project, dict):
                    project_id = str(project.get("id") or project.get("project_id") or "").strip()
                    if project_id:
                        break
            return node_id, project_id
        for value in node.values():
            found = find_org_project_from_auth_session_node(value)
            if found[0]:
                return found
    if isinstance(node, (list, tuple)):
        for item in node:
            found = find_org_project_from_auth_session_node(item)
            if found[0]:
                return found
    return "", ""
