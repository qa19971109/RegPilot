from __future__ import annotations

import email
import imaplib
import json
import random
import re
import string
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests


CODE_PATTERNS = [
    re.compile(r"\b(\d{6})\b"),
    re.compile(r"code[^\d]{0,10}(\d{6})", re.I),
    re.compile(r"verification[^\d]{0,10}(\d{6})", re.I),
]


def _request(method: str, url: str, *, timeout: int = 30, **kwargs):
    return requests.request(method.upper(), url, timeout=timeout, **kwargs)


def _normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if re.match(r"^https?:/[^/]", value):
        scheme, rest = value.split(":/", 1)
        value = f"{scheme}://{rest}"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"invalid_mail_base_url: {value}")
    return value.rstrip("/")


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ""


def _extract_code(text: str) -> str | None:
    content = str(text or "")
    content = content.replace("=\r\n", "").replace("=\n", "")
    content = re.sub(r"=3D", "=", content, flags=re.I)
    prioritized_patterns = [
        re.compile(r"temporary\s+verification\s+code(?:\s+to\s+continue)?[^0-9]{0,80}(\d{6})", re.I),
        re.compile(r"enter\s+this\s+temporary\s+verification\s+code[^0-9]{0,80}(\d{6})", re.I),
        re.compile(r"openai\s+verification\s+code[^0-9]{0,80}(\d{6})", re.I),
        re.compile(r"your\s+temporary\s+openai\s+verification\s+code[^0-9]{0,120}(\d{6})", re.I),
    ]
    for pattern in prioritized_patterns:
        match = pattern.search(content)
        if match:
            return match.group(1)
    for pattern in CODE_PATTERNS:
        match = pattern.search(content)
        if match:
            return match.group(1)
    return None


def _normalize_cloudflare_temp_email_domain(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    value = value.replace("@", "", 1) if value.startswith("@") else value
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0]
    return value if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", value, re.I) else ""


def _normalize_icloud_host(raw: str) -> str:
    value = str(raw or "icloud.com").strip().lower()
    try:
        host = urlparse(value if "://" in value else f"https://{value}").hostname or value
    except Exception:
        host = value
    return "icloud.com.cn" if host.endswith(".icloud.com.cn") or host == "icloud.com.cn" else "icloud.com"


def _parse_cookie_header(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    cookies: dict[str, str] = {}
    for part in text.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, raw_value = item.split("=", 1)
        name = name.strip()
        raw_value = raw_value.strip()
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
            raw_value = raw_value[1:-1]
        if name and raw_value:
            cookies[name] = raw_value
    return cookies


def _load_icloud_cookies(provider: dict[str, Any]) -> dict[str, str]:
    raw = provider.get("cookies")
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items() if value not in (None, "")}
    for key in ("cookies_json", "cookiesJson", "icloud_cookies_json"):
        text = str(provider.get(key) or "").strip()
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                parsed = _parse_cookie_header(text)
                if parsed:
                    return parsed
                raise RuntimeError("iCloud Cookies 必须是 JSON 对象、浏览器 cookie 数组，或 name=value; name2=value2 格式")
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if v not in (None, "")}
            if isinstance(data, list):
                out: dict[str, str] = {}
                for item in data:
                    if isinstance(item, dict) and item.get("name"):
                        out[str(item.get("name"))] = str(item.get("value") or "")
                return {k: v for k, v in out.items() if v}
            raise RuntimeError("iCloud cookies JSON 必须是对象或浏览器 cookie 数组")
    path_text = str(
        provider.get("cookies_path")
        or provider.get("cookiesPath")
        or provider.get("icloud_cookies_path")
        or ""
    ).strip()
    if path_text:
        text = Path(path_text).read_text(encoding="utf-8").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            parsed = _parse_cookie_header(text)
            if parsed:
                return parsed
            raise RuntimeError("iCloud Cookies 文件必须是 JSON 对象、浏览器 cookie 数组，或 name=value; name2=value2 格式")
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v not in (None, "")}
        if isinstance(data, list):
            out = {}
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    out[str(item.get("name"))] = str(item.get("value") or "")
            return {k: v for k, v in out.items() if v}
    return {}


class _ICloudHMEClient:
    def __init__(self, cookies: dict[str, str], *, host: str = "icloud.com", timeout: int = 30) -> None:
        if not cookies:
            raise RuntimeError("iCloud 未配置 cookies")
        self.host = _normalize_icloud_host(host)
        self.timeout = max(5, int(timeout or 30))
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self._service_url = ""
        self.last_code_meta: dict[str, Any] = {}

    @property
    def origin(self) -> str:
        return f"https://www.{self.host}"

    @property
    def setup_url(self) -> str:
        if self.host == "icloud.com.cn":
            return "https://setup.icloud.com.cn/setup/ws/1"
        return "https://setup.icloud.com/setup/ws/1"

    def _url(self, raw: str) -> str:
        parsed = urlparse(raw)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.setdefault("clientBuildNumber", ["2206Hotfix11"])
        params.setdefault("clientMasteringNumber", ["2206Hotfix11"])
        return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

    def _request(self, method: str, url: str, *, body: Any = None) -> Any:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": self.origin,
            "Referer": f"{self.origin}/",
            "Content-Type": "application/json",
        }
        data = json.dumps(body, ensure_ascii=False) if body is not None else None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.request(method.upper(), self._url(url), headers=headers, data=data, timeout=self.timeout)
                if response.status_code >= 400:
                    raise RuntimeError(f"iCloud 请求失败: HTTP {response.status_code} {response.text[:200]}")
                if not response.text:
                    return {}
                try:
                    return response.json()
                except Exception:
                    return {}
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise
        raise last_error or RuntimeError("iCloud 请求失败")

    def _resolve_service(self) -> str:
        if self._service_url:
            return self._service_url
        errors: list[str] = []
        for host in dict.fromkeys([self.host, "icloud.com.cn" if self.host == "icloud.com" else "icloud.com"]):
            self.host = host
            try:
                data = self._request("POST", f"{self.setup_url}/validate")
                premium = ((data.get("webservices") or {}).get("premiummailsettings") or {}) if isinstance(data, dict) else {}
                url = str(premium.get("url") or "").strip().rstrip("/")
                if url:
                    self._service_url = url
                    return url
                errors.append(f"{host}: premiummailsettings_missing")
            except Exception as exc:
                errors.append(f"{host}: {exc}")
        raise RuntimeError("iCloud 会话校验失败: " + "; ".join(errors))

    def create_alias(self, *, label: str = "") -> str:
        service_url = self._resolve_service()
        generated = self._request("POST", f"{service_url}/v1/hme/generate")
        if not isinstance(generated, dict) or not generated.get("success"):
            raise RuntimeError(f"iCloud HME 生成别名失败: {generated}")
        hme = generated.get("result", {}).get("hme") if isinstance(generated.get("result"), dict) else ""
        if isinstance(hme, dict):
            hme = hme.get("hme") or hme.get("email") or ""
        email_addr = str(hme or "").strip().lower()
        if "@" not in email_addr:
            raise RuntimeError(f"iCloud HME 返回空别名: {generated}")
        reserve_label = str(label or f"RegPilot {datetime.now().strftime('%Y-%m-%d')}").strip()
        reserved = self._request("POST", f"{service_url}/v1/hme/reserve", body={"hme": email_addr, "label": reserve_label, "note": "Generated by RegPilot"})
        if isinstance(reserved, dict) and reserved.get("success") is False:
            raise RuntimeError(f"iCloud HME 保留别名失败: {reserved.get('error') or reserved}")
        result = reserved.get("result", {}).get("hme") if isinstance(reserved, dict) and isinstance(reserved.get("result"), dict) else None
        if isinstance(result, dict):
            return str(result.get("hme") or result.get("email") or email_addr).strip().lower()
        return email_addr

    def poll_mail_for_code(self, target_email: str, *, timeout: int, interval: int, exclude_codes: set[str] | None = None, after_ts_ms: int = 0) -> str | None:
        service_url = self._resolve_service()
        mail_url = f"{service_url}/maildomainws"
        excluded = set(exclude_codes or set())
        started = time.time()
        seen: set[str] = set()
        self.last_code_meta = {}
        while time.time() - started <= timeout:
            try:
                payload = self._request("GET", f"{mail_url}/messages?folder=INBOX&limit=30")
                messages = payload.get("messages", []) if isinstance(payload, dict) else []
                for item in messages if isinstance(messages, list) else []:
                    if not isinstance(item, dict):
                        continue
                    msg_id = str(item.get("guid") or item.get("id") or "").strip()
                    if msg_id and msg_id in seen:
                        continue
                    if msg_id:
                        seen.add(msg_id)
                    received_at_ms = _to_timestamp_ms(
                        item.get("receivedTimestamp")
                        or item.get("receivedDateTime")
                        or item.get("date")
                        or item.get("received_at")
                        or item.get("created_at")
                        or item.get("createdAt")
                    )
                    if after_ts_ms and received_at_ms and received_at_ms < max(0, after_ts_ms - 2000):
                        continue
                    haystack = " ".join([
                        str(item.get("from") or item.get("sender") or ""),
                        str(item.get("subject") or ""),
                        str(item.get("to") or item.get("recipients") or ""),
                    ])
                    body = ""
                    if msg_id:
                        detail = self._request("GET", f"{mail_url}/messages/{msg_id}")
                        if isinstance(detail, dict):
                            body = "\n".join(str(detail.get(key) or "") for key in ("subject", "body", "textBody", "htmlBody"))
                    text = "\n".join([haystack, body])
                    if not _matches_target_mail(target_email, text):
                        continue
                    code = _extract_code(text)
                    if code and code not in excluded:
                        self.last_code_meta = {
                            "code": code,
                            "message_id": msg_id,
                            "received_at_ms": received_at_ms,
                            "provider": "icloud",
                            "preview": text[:200],
                        }
                        return code
            except Exception:
                pass
            time.sleep(max(1, int(interval or 2)))
        return None


def _strip_html_tags(value: str) -> str:
    return (
        str(value or "")
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _html_to_text(value: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", " ", str(value or ""), flags=re.I)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", _strip_html_tags(text)).strip()


def _matches_target_mail(target_email: str, text: str) -> bool:
    target = str(target_email or "").strip().lower()
    haystack = str(text or "").lower()
    if target and "@" in target:
        return target in haystack
    return any(token in haystack for token in ("openai", "chatgpt", "verification", "no-reply", "noreply"))


def _decode_email_part(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    charset = part.get_content_charset() or "utf-8"
    if payload is None:
        raw = part.get_payload()
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            return " ".join(_decode_email_part(item) for item in raw if isinstance(item, email.message.Message))
        return ""
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


def _extract_text_from_raw_email(raw: str) -> str:
    source = str(raw or "").strip()
    if not source:
        return ""
    try:
        message = email.message_from_string(source)
    except Exception:
        return re.sub(r"\s+", " ", source).strip()
    texts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            content_type = str(part.get_content_type() or "").lower()
            if content_type not in ("text/plain", "text/html"):
                continue
            decoded = _decode_email_part(part)
            texts.append(_html_to_text(decoded) if content_type == "text/html" else re.sub(r"\s+", " ", decoded).strip())
    else:
        content_type = str(message.get_content_type() or "").lower()
        decoded = _decode_email_part(message)
        texts.append(_html_to_text(decoded) if content_type == "text/html" else re.sub(r"\s+", " ", decoded).strip())
    return re.sub(r"\s+", " ", " ".join(filter(None, texts))).strip()


def _to_timestamp_ms(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return 0
        return int(numeric if numeric > 10_000_000_000 else numeric * 1000)
    source = str(value).strip()
    if not source:
        return 0
    if source.isdigit():
        numeric = int(source)
        return numeric if numeric > 10_000_000_000 else numeric * 1000
    try:
        dt = parsedate_to_datetime(source)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        pass
    try:
        normalized = source.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(source, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    return 0


def _record_code_match(mailbox: dict[str, Any], *, code: str, message_id: str = "", received_at_ms: int = 0, provider: str = "", preview: str = "") -> None:
    mailbox["_last_code_meta"] = {
        "code": code,
        "message_id": message_id,
        "received_at_ms": int(received_at_ms or 0),
        "provider": provider,
        "preview": preview[:200],
    }


def _random_local_part(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "oc" + "".join(random.choice(alphabet) for _ in range(length))


def _cloudflare_temp_email_headers(provider: dict[str, Any], *, json_body: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    admin_auth = _first_non_empty(provider.get("admin_auth"), provider.get("adminAuth"), provider.get("cloudflare_temp_email_admin_auth"))
    custom_auth = _first_non_empty(provider.get("custom_auth"), provider.get("customAuth"), provider.get("cloudflare_temp_email_custom_auth"))
    if admin_auth:
        headers["x-admin-auth"] = admin_auth
    if custom_auth:
        headers["x-custom-auth"] = custom_auth
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _cloudflare_temp_email_request(
    provider: dict[str, Any],
    method: str,
    path: str,
    *,
    timeout: int = 30,
    json_body: dict | None = None,
    params: dict | None = None,
):
    base_url = _normalize_base_url(str(provider.get("base_url") or provider.get("baseUrl") or ""))
    if not base_url:
        raise RuntimeError("Cloudflare Temp Email 未配置 base_url")
    url = f"{base_url}{path if path.startswith('/') else '/' + path}"
    response = _request(
        method,
        url,
        timeout=timeout,
        headers=_cloudflare_temp_email_headers(provider, json_body=json_body is not None),
        json=json_body,
        params=params,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        if isinstance(payload, dict):
            detail = _first_non_empty(payload.get("message"), payload.get("error"), payload.get("msg"), response.text)
        else:
            detail = str(response.text or "").strip()
        raise RuntimeError(f"Cloudflare Temp Email 请求失败: {detail or f'HTTP {response.status_code}'}")
    return payload


def _cloudflare_temp_email_get_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "messages", "mails", "results", "rows"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _cloudflare_temp_email_extract_text(row: dict[str, Any]) -> str:
    raw_message = _first_non_empty(row.get("raw"), row.get("source"), row.get("mime"), row.get("message"))
    parsed = _extract_text_from_raw_email(raw_message)
    if parsed:
        return parsed
    raw = _first_non_empty(row.get("text"), row.get("preview"), row.get("body"), raw_message)
    return re.sub(r"\s+", " ", raw).strip()


def _create_cloudflare_temp_email_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30) -> dict[str, Any]:
    admin_auth = _first_non_empty(provider.get("admin_auth"), provider.get("adminAuth"), provider.get("cloudflare_temp_email_admin_auth"))
    if not admin_auth:
        raise RuntimeError("Cloudflare Temp Email 未配置 admin_auth")
    domain = _normalize_cloudflare_temp_email_domain(str(provider.get("domain") or ""))
    if not domain:
        raise RuntimeError("Cloudflare Temp Email 未配置有效 domain")
    requested_name = str(username or _random_local_part()).strip().lower()
    payload = {
        "enablePrefix": True,
        "enableRandomSubdomain": bool(provider.get("use_random_subdomain") or provider.get("useRandomSubdomain")),
        "name": requested_name,
        "domain": domain,
    }
    data = _cloudflare_temp_email_request(provider, "post", "/admin/new_address", json_body=payload, timeout=request_timeout)
    email = _first_non_empty(
        data.get("address"),
        data.get("email"),
        (data.get("data") or {}).get("address") if isinstance(data.get("data"), dict) else "",
        (data.get("data") or {}).get("email") if isinstance(data.get("data"), dict) else "",
    ).lower()
    if not email:
        raise RuntimeError(f"Cloudflare Temp Email 未返回可用邮箱地址: {data}")
    return {
        "provider": "cloudflare-temp-email",
        "email": email,
        "base_url": _normalize_base_url(str(provider.get("base_url") or provider.get("baseUrl") or "")),
        "admin_auth": admin_auth,
        "custom_auth": _first_non_empty(provider.get("custom_auth"), provider.get("customAuth"), provider.get("cloudflare_temp_email_custom_auth")),
        "domain": domain,
    }


def _normalize_hotmail_api_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if re.match(r"^https?:/[^/]", value):
        scheme, rest = value.split(":/", 1)
        value = f"{scheme}://{rest}"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"invalid_hotmail_api_base_url: {value}")
    return value.rstrip("/")


def _split_csv(value: Any, default: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,\n]+", str(value or ""))
    out = [str(item or "").strip() for item in items if str(item or "").strip()]
    return out or list(default or [])


def _create_hotmail_api_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30) -> dict[str, Any]:
    from . import microsoft_mail_pool

    base_url = _normalize_hotmail_api_base_url(str(provider.get("base_url") or provider.get("baseUrl") or ""))
    if not base_url:
        raise RuntimeError("Hotmail/Outlook 邮箱服务未配置 API 地址")
    alias_enabled = bool(provider.get("alias_enabled") or provider.get("aliasEnabled"))
    alias_max = max(1, min(50, int(provider.get("alias_max_per_account") or provider.get("aliasMaxPerAccount") or 5)))
    account = microsoft_mail_pool.claim_account(alias_enabled=alias_enabled, alias_max=alias_max)
    return {
        "provider": "hotmail-api",
        "email": str(account.get("registration_email") or account.get("email") or "").strip().lower(),
        "base_email": str(account.get("base_email") or account.get("email") or "").strip().lower(),
        "password": str(account.get("password") or ""),
        "client_id": str(account.get("client_id") or ""),
        "refresh_token": str(account.get("refresh_token") or ""),
        "microsoft_account_id": str(account.get("id") or ""),
        "base_url": base_url,
        "mailboxes": _split_csv(provider.get("mailboxes"), ["INBOX", "Junk"]),
        "top": max(1, min(30, int(provider.get("top") or 10))),
        "sender_filters": _split_csv(provider.get("sender_filters") or provider.get("senderFilters"), ["openai", "noreply", "no-reply"]),
        "subject_filters": _split_csv(provider.get("subject_filters") or provider.get("subjectFilters"), ["code", "verification", "验证码"]),
        "required_keywords": _split_csv(provider.get("required_keywords") or provider.get("requiredKeywords"), []),
        "alias_source": "outlook-plus" if alias_enabled else "microsoft-account",
        "request_timeout": request_timeout,
    }


def _hotmail_api_request(mailbox: dict[str, Any], path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    base_url = _normalize_hotmail_api_base_url(str(mailbox.get("base_url") or ""))
    if not base_url:
        raise RuntimeError("Hotmail/Outlook 邮箱服务未配置 API 地址")
    response = _request(
        "POST",
        f"{base_url}{path if path.startswith('/') else '/' + path}",
        timeout=timeout,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=payload,
    )
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
        detail = ""
        if isinstance(data, dict):
            detail = _first_non_empty(data.get("error"), data.get("message"), data.get("detail"))
        raise RuntimeError(f"Hotmail/Outlook 邮箱 API 请求失败: {detail or response.text or response.status_code}")
    return data if isinstance(data, dict) else {}


def _create_icloud_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30) -> dict[str, Any]:
    email_addr = _first_non_empty(provider.get("email"), provider.get("address"), provider.get("alias")).lower()
    host = _normalize_icloud_host(str(provider.get("host") or "icloud.com"))
    imap_user = _first_non_empty(provider.get("imap_user"), provider.get("imapUser"), provider.get("icloud_imap_user"))
    imap_password = _first_non_empty(provider.get("imap_password"), provider.get("imapPassword"), provider.get("icloud_imap_password"))
    cookies_json = _first_non_empty(provider.get("cookies_json"), provider.get("cookiesJson"), provider.get("icloud_cookies_json"))
    cookies_path = _first_non_empty(provider.get("cookies_path"), provider.get("cookiesPath"), provider.get("icloud_cookies_path"))
    if not email_addr:
        cookies = _load_icloud_cookies(provider)
        label = _first_non_empty(provider.get("hme_label"), provider.get("label"), "RegPilot")
        email_addr = _ICloudHMEClient(cookies, host=host, timeout=request_timeout).create_alias(label=label)
    if "@" not in email_addr:
        raise RuntimeError("iCloud 未配置有效邮箱或 HME cookies")
    mailbox = {
        "provider": "icloud",
        "email": email_addr,
        "host": host,
        "alias_source": "configured" if _first_non_empty(provider.get("email"), provider.get("address"), provider.get("alias")) else "hide-my-email",
    }
    if imap_user:
        mailbox["imap_user"] = imap_user
    if imap_password:
        mailbox["imap_password"] = imap_password
    if cookies_json:
        mailbox["cookies_json"] = cookies_json
    if cookies_path:
        mailbox["cookies_path"] = cookies_path
    return mailbox


def create_mailbox(config: dict[str, Any], username: str | None = None) -> dict[str, Any]:
    request_timeout = int(config.get("request_timeout") or 30) if isinstance(config, dict) else 30
    providers = config.get("providers") if isinstance(config, dict) else None
    last_error: Exception | None = None
    if isinstance(providers, list) and providers:
        normalized_providers = [item if isinstance(item, dict) else {} for item in providers]
        for index, provider in enumerate(normalized_providers):
            provider_type = str(provider.get("type") or "").strip().lower()
            try:
                if provider_type == "cloudflare-temp-email":
                    return _create_cloudflare_temp_email_mailbox(provider, username=username, request_timeout=request_timeout)
                if provider_type in {"hotmail-api", "outlook-api", "microsoft-mail"}:
                    return _create_hotmail_api_mailbox(provider, username=username, request_timeout=request_timeout)
                if provider_type in {"icloud", "icloud-hme", "icloud_hme"}:
                    return _create_icloud_mailbox(provider, username=username, request_timeout=request_timeout)
            except Exception as exc:
                last_error = exc
                next_provider = next(
                    (
                        str(row.get("type") or "").strip().lower()
                        for row in normalized_providers[index + 1 :]
                        if isinstance(row, dict) and str(row.get("type") or "").strip()
                    ),
                    "",
                )
                if next_provider:
                    error_text = str(exc)
                    if provider_type in {"icloud", "icloud-hme", "icloud_hme"} and ("-41015" in error_text or "limit of addresses" in error_text):
                        print(f"阶段：iCloud HME 创建别名被限流，切换 {next_provider} 邮箱")
                    else:
                        print(f"阶段：邮箱服务 {provider_type or '-'} 创建失败，尝试切换 {next_provider}：{error_text[:180]}")
                continue
        if last_error is not None:
            raise last_error
    raise RuntimeError("mail_provider_not_configured")


def _wait_hotmail_api_code(mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30) -> str | None:
    base_email = str(mailbox.get("base_email") or mailbox.get("email") or "").strip().lower()
    client_id = str(mailbox.get("client_id") or "").strip()
    refresh_token = str(mailbox.get("refresh_token") or "").strip()
    if not base_email or not client_id or not refresh_token:
        return None
    started = time.time()
    last_token = refresh_token
    while time.time() - started <= timeout:
        payload = {
            "email": base_email,
            "clientId": client_id,
            "refreshToken": last_token,
            "mailboxes": _split_csv(mailbox.get("mailboxes"), ["INBOX", "Junk"]),
            "top": max(1, min(30, int(mailbox.get("top") or 10))),
            "senderFilters": _split_csv(mailbox.get("sender_filters") or mailbox.get("senderFilters"), ["openai", "noreply", "no-reply"]),
            "subjectFilters": _split_csv(mailbox.get("subject_filters") or mailbox.get("subjectFilters"), ["code", "verification", "验证码"]),
            "requiredKeywords": _split_csv(mailbox.get("required_keywords") or mailbox.get("requiredKeywords"), []),
            "excludeCodes": [str(item) for item in (mailbox.get("_exclude_codes") or []) if item],
            "filterAfterTimestamp": int(mailbox.get("_code_after_ts") or 0),
        }
        data = _hotmail_api_request(mailbox, "/code", payload, timeout=request_timeout)
        next_token = str(data.get("nextRefreshToken") or "").strip()
        if next_token:
            last_token = next_token
            mailbox["refresh_token"] = next_token
        code = str(data.get("code") or "").strip()
        if code:
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            received_at_ms = _to_timestamp_ms(message.get("receivedTimestamp") or message.get("receivedDateTime"))
            preview = "\n".join(
                [
                    str(message.get("subject") or ""),
                    str(((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""),
                    str(message.get("bodyPreview") or ""),
                    str(((message.get("body") or {}).get("content") if isinstance(message.get("body"), dict) else "") or ""),
                ]
            )
            _record_code_match(
                mailbox,
                code=code,
                message_id=str(message.get("id") or ""),
                received_at_ms=received_at_ms,
                provider="hotmail-api",
                preview=preview,
            )
            return code
        time.sleep(max(1, interval))
    return None


def _wait_cloudflare_temp_email_code(mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30) -> str | None:
    email = str(mailbox.get("email") or "").strip().lower()
    if not email:
        return None
    after_ts_ms = int(mailbox.get("_code_after_ts") or 0)
    provider = {
        "base_url": mailbox.get("base_url") or "",
        "admin_auth": mailbox.get("admin_auth") or "",
        "custom_auth": mailbox.get("custom_auth") or "",
    }
    started = time.time()
    seen: set[str] = set()
    while time.time() - started <= timeout:
        payload = _cloudflare_temp_email_request(
            provider,
            "get",
            "/admin/mails",
            params={"limit": 20, "offset": 0, "address": email},
            timeout=request_timeout,
        )
        rows = _cloudflare_temp_email_get_rows(payload)
        rows.sort(
            key=lambda item: _first_non_empty(
                item.get("receivedDateTime"),
                item.get("received_at"),
                item.get("created_at"),
                item.get("createdAt"),
                item.get("updated_at"),
                item.get("date"),
            ),
            reverse=True,
        )
        for row in rows:
            message_id = _first_non_empty(row.get("id"), row.get("mail_id"), row.get("mailId"))
            if message_id and message_id in seen:
                continue
            if message_id:
                seen.add(message_id)
            received_at_ms = _to_timestamp_ms(
                row.get("receivedDateTime")
                or row.get("received_at")
                or row.get("created_at")
                or row.get("createdAt")
                or row.get("updated_at")
                or row.get("date")
            )
            if after_ts_ms and received_at_ms and received_at_ms < max(0, after_ts_ms - 2000):
                continue
            row_email = _first_non_empty(row.get("address"), row.get("mail_address"), row.get("email"), row.get("recipient")).lower()
            if row_email and row_email != email:
                continue
            text = "\n".join(
                [
                    _first_non_empty(row.get("subject")),
                    _first_non_empty(row.get("from"), row.get("sender"), row.get("mail_from")),
                    _cloudflare_temp_email_extract_text(row),
                ]
            )
            code = _extract_code(text)
            if code:
                _record_code_match(
                    mailbox,
                    code=code,
                    message_id=message_id,
                    received_at_ms=received_at_ms,
                    provider="cloudflare-temp-email",
                    preview=text,
                )
                return code
        time.sleep(max(1, interval))
    return None


def _provider_config_for_mailbox(config: dict[str, Any], mailbox: dict[str, Any]) -> dict[str, Any]:
    provider_name = str(mailbox.get("provider") or "").strip().lower()
    aliases = {provider_name}
    if provider_name in {"icloud", "icloud-hme", "icloud_hme"}:
        aliases.update({"icloud", "icloud-hme", "icloud_hme"})
    if provider_name in {"hotmail-api", "outlook-api", "microsoft-mail"}:
        aliases.update({"hotmail-api", "outlook-api", "microsoft-mail"})
    providers = config.get("providers") if isinstance(config, dict) else []
    if isinstance(providers, list):
        for item in providers:
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() in aliases:
                return item
    return {}


def _wait_icloud_code(config: dict[str, Any], mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30) -> str | None:
    provider = _provider_config_for_mailbox(config, mailbox)
    email_addr = str(mailbox.get("email") or provider.get("email") or provider.get("address") or provider.get("alias") or "").strip().lower()
    if not email_addr:
        return None
    after_ts_ms = int(mailbox.get("_code_after_ts") or 0)
    excluded = set(str(item) for item in (mailbox.get("_exclude_codes") or []) if item)
    imap_user = _first_non_empty(provider.get("imap_user"), provider.get("imapUser"), provider.get("icloud_imap_user"))
    imap_password = _first_non_empty(provider.get("imap_password"), provider.get("imapPassword"), provider.get("icloud_imap_password"))
    cookies = _load_icloud_cookies(provider)
    web_client = _ICloudHMEClient(cookies, host=str(provider.get("host") or mailbox.get("host") or "icloud.com"), timeout=request_timeout) if cookies else None
    if web_client and imap_user and imap_password:
        started = time.time()
        slice_timeout = max(3, min(6, int(interval or 2) * 2))
        while time.time() - started <= timeout:
            remaining = max(1, int(timeout - (time.time() - started)))
            code = web_client.poll_mail_for_code(
                email_addr,
                timeout=min(slice_timeout, remaining),
                interval=max(1, min(2, int(interval or 2))),
                exclude_codes=excluded,
                after_ts_ms=after_ts_ms,
            )
            if code:
                meta = getattr(web_client, "last_code_meta", {}) or {}
                _record_code_match(
                    mailbox,
                    code=code,
                    message_id=str(meta.get("message_id") or ""),
                    received_at_ms=int(meta.get("received_at_ms") or 0),
                    provider="icloud",
                    preview=str(meta.get("preview") or "iCloud maildomainws"),
                )
                return code
            remaining = max(1, int(timeout - (time.time() - started)))
            code = _wait_icloud_imap_code(
                email_addr,
                imap_user=imap_user,
                imap_password=imap_password,
                timeout=min(slice_timeout, remaining),
                interval=max(1, min(2, int(interval or 2))),
                request_timeout=request_timeout,
                after_ts_ms=after_ts_ms,
                excluded=excluded,
                mailbox=mailbox,
            )
            if code:
                return code
        return None
    if imap_user and imap_password:
        code = _wait_icloud_imap_code(
            email_addr,
            imap_user=imap_user,
            imap_password=imap_password,
            timeout=timeout,
            interval=interval,
            request_timeout=request_timeout,
            after_ts_ms=after_ts_ms,
            excluded=excluded,
            mailbox=mailbox,
        )
        if code:
            return code
    if web_client:
        code = web_client.poll_mail_for_code(
            email_addr,
            timeout=timeout,
            interval=interval,
            exclude_codes=excluded,
            after_ts_ms=after_ts_ms,
        )
        if code:
            meta = getattr(web_client, "last_code_meta", {}) or {}
            _record_code_match(
                mailbox,
                code=code,
                message_id=str(meta.get("message_id") or ""),
                received_at_ms=int(meta.get("received_at_ms") or 0),
                provider="icloud",
                preview=str(meta.get("preview") or "iCloud maildomainws"),
            )
        return code
    return None


def _wait_icloud_imap_code(
    target_email: str,
    *,
    imap_user: str,
    imap_password: str,
    timeout: int,
    interval: int,
    request_timeout: int,
    after_ts_ms: int,
    excluded: set[str],
    mailbox: dict[str, Any],
) -> str | None:
    started = time.time()
    seen: set[str] = set()

    def internaldate_ms(metadata: Any) -> int:
        try:
            parsed = imaplib.Internaldate2tuple(metadata)
            if parsed:
                return int(time.mktime(parsed) * 1000)
        except Exception:
            return 0
        return 0

    def fetch_raw_message(conn: imaplib.IMAP4_SSL, msg_id: bytes | str) -> tuple[str, bytes, int]:
        last_status = ""
        for fetch_spec in ("(INTERNALDATE RFC822)", "(INTERNALDATE BODY.PEEK[])", "(INTERNALDATE BODY.PEEK[])"):
            status, msg_data = conn.fetch(msg_id, fetch_spec)
            last_status = str(status or "")
            if status != "OK":
                continue
            for item in msg_data or []:
                if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes) and item[1]:
                    return fetch_spec, item[1], internaldate_ms(item[0])
        return last_status, b"", 0

    while time.time() - started <= timeout:
        conn = None
        try:
            conn = imaplib.IMAP4_SSL("imap.mail.me.com", 993, timeout=max(5, int(request_timeout or 30)))
            conn.login(imap_user, imap_password)
            conn.select("INBOX")
            status, data = conn.search(None, "ALL")
            if status != "OK":
                time.sleep(max(1, int(interval or 2)))
                continue
            ids = (data[0].split() if data and data[0] else [])[-25:]
            for msg_id in reversed(ids):
                msg_key = msg_id.decode("ascii", errors="ignore") if isinstance(msg_id, bytes) else str(msg_id)
                if msg_key in seen:
                    continue
                seen.add(msg_key)
                fetch_spec, raw, internal_received_at_ms = fetch_raw_message(conn, msg_id)
                if not raw:
                    continue
                try:
                    message = email.message_from_bytes(raw)
                    received_at_ms = _to_timestamp_ms(message.get("Date")) or internal_received_at_ms
                    recipients = " ".join([
                        str(message.get("To") or ""),
                        str(message.get("Delivered-To") or ""),
                        str(message.get("X-Original-To") or ""),
                    ]).lower()
                    subject = str(message.get("Subject") or "")
                    sender = str(message.get("From") or "")
                    text = _extract_text_from_raw_email(raw.decode("utf-8", errors="replace"))
                except Exception:
                    received_at_ms = internal_received_at_ms
                    recipients = ""
                    subject = ""
                    sender = ""
                    text = raw.decode("utf-8", errors="replace")
                if after_ts_ms and received_at_ms and received_at_ms < max(0, after_ts_ms - 2000):
                    continue
                combined = "\n".join([recipients, subject, sender, text])
                if not _matches_target_mail(target_email, combined):
                    continue
                code = _extract_code(combined)
                if code and code not in excluded:
                    _record_code_match(
                        mailbox,
                        code=code,
                        message_id=msg_key,
                        received_at_ms=received_at_ms,
                        provider="icloud",
                        preview=f"{fetch_spec}\n{combined}",
                    )
                    return code
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
        time.sleep(max(1, int(interval or 2)))
    return None


def wait_for_code(config: dict[str, Any], mailbox: dict[str, Any]) -> str | None:
    request_timeout = int(config.get("request_timeout") or 30) if isinstance(config, dict) else 30
    timeout = int(config.get("wait_timeout") or 30) if isinstance(config, dict) else 30
    interval = int(config.get("wait_interval") or 2) if isinstance(config, dict) else 2
    provider = str(mailbox.get("provider") or "").strip().lower()
    if provider == "cloudflare-temp-email":
        return _wait_cloudflare_temp_email_code(mailbox, timeout=timeout, interval=interval, request_timeout=request_timeout)
    if provider in {"hotmail-api", "outlook-api", "microsoft-mail"}:
        return _wait_hotmail_api_code(mailbox, timeout=timeout, interval=interval, request_timeout=request_timeout)
    if provider in {"icloud", "icloud-hme", "icloud_hme"}:
        return _wait_icloud_code(config, mailbox, timeout=timeout, interval=interval, request_timeout=request_timeout)
    return None
