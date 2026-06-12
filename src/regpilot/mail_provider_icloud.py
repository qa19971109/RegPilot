from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests


@dataclass(frozen=True)
class ICloudHMEDeps:
    session_factory: Callable[[], Any]
    time_time: Callable[[], float]
    sleep: Callable[[float], None]
    to_timestamp_ms: Callable[[Any], int]
    matches_target_mail: Callable[[str, str], bool]
    extract_code: Callable[[str], str | None]


@dataclass(frozen=True)
class ICloudImapDeps:
    imap4_ssl: Callable[..., Any]
    internaldate_to_tuple: Callable[[Any], Any]
    time_time: Callable[[], float]
    time_mktime: Callable[[Any], float]
    sleep: Callable[[float], None]
    message_from_bytes: Callable[[bytes], Any]
    to_timestamp_ms: Callable[[Any], int]
    extract_text_from_raw_email: Callable[[str], str]
    matches_target_mail: Callable[[str, str], bool]
    extract_code: Callable[[str], str | None]
    record_code_match: Callable[..., None]


@dataclass(frozen=True)
class ICloudImapMessage:
    received_at_ms: int
    combined: str


def normalize_host(raw: str) -> str:
    value = str(raw or "icloud.com").strip().lower()
    try:
        host = urlparse(value if "://" in value else f"https://{value}").hostname or value
    except Exception:
        host = value
    return "icloud.com.cn" if host.endswith(".icloud.com.cn") or host == "icloud.com.cn" else "icloud.com"


def parse_cookie_header(value: str) -> dict[str, str]:
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


def load_cookies(provider: dict[str, Any]) -> dict[str, str]:
    raw = provider.get("cookies")
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items() if value not in (None, "")}
    for key in ("cookies_json", "cookiesJson", "icloud_cookies_json"):
        text = str(provider.get(key) or "").strip()
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                parsed = parse_cookie_header(text)
                if parsed:
                    return parsed
                raise RuntimeError("iCloud Cookies 蹇呴』鏄?JSON 瀵硅薄銆佹祻瑙堝櫒 cookie 鏁扮粍锛屾垨 name=value; name2=value2 鏍煎紡")
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if v not in (None, "")}
            if isinstance(data, list):
                out: dict[str, str] = {}
                for item in data:
                    if isinstance(item, dict) and item.get("name"):
                        out[str(item.get("name"))] = str(item.get("value") or "")
                return {k: v for k, v in out.items() if v}
            raise RuntimeError("iCloud cookies JSON 蹇呴』鏄璞℃垨娴忚鍣?cookie 鏁扮粍")
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
            parsed = parse_cookie_header(text)
            if parsed:
                return parsed
            raise RuntimeError("iCloud Cookies 鏂囦欢蹇呴』鏄?JSON 瀵硅薄銆佹祻瑙堝櫒 cookie 鏁扮粍锛屾垨 name=value; name2=value2 鏍煎紡")
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v not in (None, "")}
        if isinstance(data, list):
            out = {}
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    out[str(item.get("name"))] = str(item.get("value") or "")
            return {k: v for k, v in out.items() if v}
    return {}


def default_hme_deps(
    *,
    to_timestamp_ms: Callable[[Any], int],
    matches_target_mail: Callable[[str, str], bool],
    extract_code: Callable[[str], str | None],
) -> ICloudHMEDeps:
    return ICloudHMEDeps(
        session_factory=requests.Session,
        time_time=time.time,
        sleep=time.sleep,
        to_timestamp_ms=to_timestamp_ms,
        matches_target_mail=matches_target_mail,
        extract_code=extract_code,
    )


class ICloudHMEClient:
    def __init__(self, cookies: dict[str, str], *, host: str = "icloud.com", timeout: int = 30, deps: ICloudHMEDeps) -> None:
        if not cookies:
            raise RuntimeError("iCloud 鏈厤缃?cookies")
        self.host = normalize_host(host)
        self.timeout = max(5, int(timeout or 30))
        self.session = deps.session_factory()
        self.session.cookies.update(cookies)
        self._service_url = ""
        self.last_code_meta: dict[str, Any] = {}
        self._deps = deps

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
                    raise RuntimeError(f"iCloud 璇锋眰澶辫触: HTTP {response.status_code} {response.text[:200]}")
                if not response.text:
                    return {}
                try:
                    return response.json()
                except Exception:
                    return {}
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    self._deps.sleep(1 + attempt)
                    continue
                raise
        raise last_error or RuntimeError("iCloud 璇锋眰澶辫触")

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
        raise RuntimeError("iCloud 浼氳瘽鏍￠獙澶辫触: " + "; ".join(errors))

    def create_alias(self, *, label: str = "") -> str:
        service_url = self._resolve_service()
        generated = self._request("POST", f"{service_url}/v1/hme/generate")
        if not isinstance(generated, dict) or not generated.get("success"):
            raise RuntimeError(f"iCloud HME 鐢熸垚鍒悕澶辫触: {generated}")
        hme = generated.get("result", {}).get("hme") if isinstance(generated.get("result"), dict) else ""
        if isinstance(hme, dict):
            hme = hme.get("hme") or hme.get("email") or ""
        email_addr = str(hme or "").strip().lower()
        if "@" not in email_addr:
            raise RuntimeError(f"iCloud HME 杩斿洖绌哄埆鍚? {generated}")
        reserve_label = str(label or f"RegPilot {datetime.now().strftime('%Y-%m-%d')}").strip()
        reserved = self._request("POST", f"{service_url}/v1/hme/reserve", body={"hme": email_addr, "label": reserve_label, "note": "Generated by RegPilot"})
        if isinstance(reserved, dict) and reserved.get("success") is False:
            raise RuntimeError(f"iCloud HME 淇濈暀鍒悕澶辫触: {reserved.get('error') or reserved}")
        result = reserved.get("result", {}).get("hme") if isinstance(reserved, dict) and isinstance(reserved.get("result"), dict) else None
        if isinstance(result, dict):
            return str(result.get("hme") or result.get("email") or email_addr).strip().lower()
        return email_addr

    def poll_mail_for_code(self, target_email: str, *, timeout: int, interval: int, exclude_codes: set[str] | None = None, after_ts_ms: int = 0) -> str | None:
        service_url = self._resolve_service()
        mail_url = f"{service_url}/maildomainws"
        excluded = set(exclude_codes or set())
        started = self._deps.time_time()
        seen: set[str] = set()
        self.last_code_meta = {}
        while self._deps.time_time() - started <= timeout:
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
                    received_at_ms = self._deps.to_timestamp_ms(
                        item.get("receivedTimestamp")
                        or item.get("receivedDateTime")
                        or item.get("date")
                        or item.get("received_at")
                        or item.get("created_at")
                        or item.get("createdAt")
                    )
                    if after_ts_ms and received_at_ms and received_at_ms < max(0, after_ts_ms - 2000):
                        continue
                    haystack = " ".join(
                        [
                            str(item.get("from") or item.get("sender") or ""),
                            str(item.get("subject") or ""),
                            str(item.get("to") or item.get("recipients") or ""),
                        ]
                    )
                    body = ""
                    if msg_id:
                        detail = self._request("GET", f"{mail_url}/messages/{msg_id}")
                        if isinstance(detail, dict):
                            body = "\n".join(str(detail.get(key) or "") for key in ("subject", "body", "textBody", "htmlBody"))
                    text = "\n".join([haystack, body])
                    if not self._deps.matches_target_mail(target_email, text):
                        continue
                    code = self._deps.extract_code(text)
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
            self._deps.sleep(max(1, int(interval or 2)))
        return None


def _imap_internaldate_ms(metadata: Any, deps: ICloudImapDeps) -> int:
    try:
        parsed = deps.internaldate_to_tuple(metadata)
        if parsed:
            return int(deps.time_mktime(parsed) * 1000)
    except Exception:
        return 0
    return 0


def _fetch_icloud_imap_raw_message(conn: Any, msg_id: bytes | str, deps: ICloudImapDeps) -> tuple[str, bytes, int]:
    last_status = ""
    for fetch_spec in ("(INTERNALDATE RFC822)", "(INTERNALDATE BODY.PEEK[])", "(INTERNALDATE BODY.PEEK[])"):
        status, msg_data = conn.fetch(msg_id, fetch_spec)
        last_status = str(status or "")
        if status != "OK":
            continue
        for item in msg_data or []:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes) and item[1]:
                return fetch_spec, item[1], _imap_internaldate_ms(item[0], deps)
    return last_status, b"", 0


def _parse_icloud_imap_message(raw: bytes, internal_received_at_ms: int, deps: ICloudImapDeps) -> ICloudImapMessage:
    try:
        message = deps.message_from_bytes(raw)
        received_at_ms = deps.to_timestamp_ms(message.get("Date")) or internal_received_at_ms
        recipients = " ".join(
            [
                str(message.get("To") or ""),
                str(message.get("Delivered-To") or ""),
                str(message.get("X-Original-To") or ""),
            ]
        ).lower()
        subject = str(message.get("Subject") or "")
        sender = str(message.get("From") or "")
        text = deps.extract_text_from_raw_email(raw.decode("utf-8", errors="replace"))
    except Exception:
        received_at_ms = internal_received_at_ms
        recipients = ""
        subject = ""
        sender = ""
        text = raw.decode("utf-8", errors="replace")
    return ICloudImapMessage(received_at_ms=received_at_ms, combined="\n".join([recipients, subject, sender, text]))


def _extract_icloud_imap_code_from_message(
    *,
    target_email: str,
    msg_key: str,
    fetch_spec: str,
    raw: bytes,
    internal_received_at_ms: int,
    after_ts_ms: int,
    excluded: set[str],
    mailbox: dict[str, Any],
    deps: ICloudImapDeps,
) -> str | None:
    parsed = _parse_icloud_imap_message(raw, internal_received_at_ms, deps)
    if after_ts_ms and parsed.received_at_ms and parsed.received_at_ms < max(0, after_ts_ms - 2000):
        return None
    if not deps.matches_target_mail(target_email, parsed.combined):
        return None
    code = deps.extract_code(parsed.combined)
    if code and code not in excluded:
        deps.record_code_match(
            mailbox,
            code=code,
            message_id=msg_key,
            received_at_ms=parsed.received_at_ms,
            provider="icloud",
            preview=f"{fetch_spec}\n{parsed.combined}",
        )
        return code
    return None


def wait_imap_code(
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
    deps: ICloudImapDeps,
) -> str | None:
    started = deps.time_time()
    seen: set[str] = set()

    while deps.time_time() - started <= timeout:
        conn = None
        try:
            conn = deps.imap4_ssl("imap.mail.me.com", 993, timeout=max(5, int(request_timeout or 30)))
            conn.login(imap_user, imap_password)
            conn.select("INBOX")
            status, data = conn.search(None, "ALL")
            if status != "OK":
                deps.sleep(max(1, int(interval or 2)))
                continue
            ids = (data[0].split() if data and data[0] else [])[-25:]
            for msg_id in reversed(ids):
                msg_key = msg_id.decode("ascii", errors="ignore") if isinstance(msg_id, bytes) else str(msg_id)
                if msg_key in seen:
                    continue
                seen.add(msg_key)
                fetch_spec, raw, internal_received_at_ms = _fetch_icloud_imap_raw_message(conn, msg_id, deps)
                if not raw:
                    continue
                code = _extract_icloud_imap_code_from_message(
                    target_email=target_email,
                    msg_key=msg_key,
                    fetch_spec=fetch_spec,
                    raw=raw,
                    internal_received_at_ms=internal_received_at_ms,
                    after_ts_ms=after_ts_ms,
                    excluded=excluded,
                    mailbox=mailbox,
                    deps=deps,
                )
                if code:
                    return code
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
        deps.sleep(max(1, int(interval or 2)))
    return None
