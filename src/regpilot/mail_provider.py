from __future__ import annotations

import email
import imaplib
import random
import re
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests

from . import mail_provider_cloudflare
from . import mail_provider_hotmail
from . import mail_provider_icloud


@dataclass(frozen=True)
class ICloudCodeWaitContext:
    email_addr: str
    after_ts_ms: int
    excluded: set[str]
    imap_user: str
    imap_password: str
    web_client: Any
    timeout: int
    interval: int
    request_timeout: int


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
    return mail_provider_cloudflare.normalize_domain(raw)


def _normalize_icloud_host(raw: str) -> str:
    return mail_provider_icloud.normalize_host(raw)


def _parse_cookie_header(value: str) -> dict[str, str]:
    return mail_provider_icloud.parse_cookie_header(value)


def _load_icloud_cookies(provider: dict[str, Any]) -> dict[str, str]:
    return mail_provider_icloud.load_cookies(provider)


def _icloud_hme_deps() -> mail_provider_icloud.ICloudHMEDeps:
    return mail_provider_icloud.default_hme_deps(
        to_timestamp_ms=_to_timestamp_ms,
        matches_target_mail=_matches_target_mail,
        extract_code=_extract_code,
    )


class _ICloudHMEClient(mail_provider_icloud.ICloudHMEClient):
    def __init__(self, cookies: dict[str, str], *, host: str = "icloud.com", timeout: int = 30) -> None:
        super().__init__(cookies, host=host, timeout=timeout, deps=_icloud_hme_deps())


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


def _cloudflare_temp_email_deps() -> mail_provider_cloudflare.CloudflareTempEmailDeps:
    return mail_provider_cloudflare.CloudflareTempEmailDeps(
        normalize_base_url=_normalize_base_url,
        first_non_empty=_first_non_empty,
        request=_request,
        extract_text_from_raw_email=_extract_text_from_raw_email,
        extract_code=_extract_code,
        record_code_match=_record_code_match,
        to_timestamp_ms=_to_timestamp_ms,
        random_local_part=_random_local_part,
    )


def _cloudflare_temp_email_headers(provider: dict[str, Any], *, json_body: bool = False) -> dict[str, str]:
    return mail_provider_cloudflare.headers(provider, json_body=json_body, deps=_cloudflare_temp_email_deps())


def _cloudflare_temp_email_request(
    provider: dict[str, Any],
    method: str,
    path: str,
    *,
    timeout: int = 30,
    json_body: dict | None = None,
    params: dict | None = None,
):
    return mail_provider_cloudflare.request(
        provider,
        method,
        path,
        timeout=timeout,
        json_body=json_body,
        params=params,
        deps=_cloudflare_temp_email_deps(),
    )


def _cloudflare_temp_email_get_rows(payload: Any) -> list[dict[str, Any]]:
    return mail_provider_cloudflare.get_rows(payload)


def _cloudflare_temp_email_extract_text(row: dict[str, Any]) -> str:
    return mail_provider_cloudflare.extract_text(row, deps=_cloudflare_temp_email_deps())


def _create_cloudflare_temp_email_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30) -> dict[str, Any]:
    return mail_provider_cloudflare.create_mailbox(
        provider,
        username=username,
        request_timeout=request_timeout,
        deps=_cloudflare_temp_email_deps(),
    )


def _normalize_hotmail_api_base_url(raw: str) -> str:
    return mail_provider_hotmail.normalize_base_url(raw)


def _split_csv(value: Any, default: list[str] | None = None) -> list[str]:
    return mail_provider_hotmail.split_csv(value, default)


def _hotmail_api_deps() -> mail_provider_hotmail.HotmailApiDeps:
    return mail_provider_hotmail.HotmailApiDeps(
        request=_request,
        first_non_empty=_first_non_empty,
        record_code_match=_record_code_match,
        to_timestamp_ms=_to_timestamp_ms,
    )


def _create_hotmail_api_mailbox(provider: dict[str, Any], username: str | None = None, request_timeout: int = 30) -> dict[str, Any]:
    return mail_provider_hotmail.create_mailbox(provider, username=username, request_timeout=request_timeout)


def _hotmail_api_request(mailbox: dict[str, Any], path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    return mail_provider_hotmail.api_request(
        mailbox,
        path,
        payload,
        timeout=timeout,
        deps=_hotmail_api_deps(),
    )


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
    return mail_provider_hotmail.wait_for_code(
        mailbox,
        timeout=timeout,
        interval=interval,
        request_timeout=request_timeout,
        deps=_hotmail_api_deps(),
    )


def _wait_cloudflare_temp_email_code(mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30) -> str | None:
    return mail_provider_cloudflare.wait_for_code(
        mailbox,
        timeout=timeout,
        interval=interval,
        request_timeout=request_timeout,
        deps=_cloudflare_temp_email_deps(),
    )


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


def _record_icloud_web_code(mailbox: dict[str, Any], web_client: Any, code: str) -> None:
    meta = getattr(web_client, "last_code_meta", {}) or {}
    _record_code_match(
        mailbox,
        code=code,
        message_id=str(meta.get("message_id") or ""),
        received_at_ms=int(meta.get("received_at_ms") or 0),
        provider="icloud",
        preview=str(meta.get("preview") or "iCloud maildomainws"),
    )


def _poll_icloud_web_code(context: ICloudCodeWaitContext, mailbox: dict[str, Any], *, timeout: int, interval: int) -> str | None:
    code = context.web_client.poll_mail_for_code(
        context.email_addr,
        timeout=timeout,
        interval=interval,
        exclude_codes=context.excluded,
        after_ts_ms=context.after_ts_ms,
    )
    if code:
        _record_icloud_web_code(mailbox, context.web_client, code)
    return code


def _poll_icloud_imap_code(context: ICloudCodeWaitContext, mailbox: dict[str, Any], *, timeout: int, interval: int) -> str | None:
    return _wait_icloud_imap_code(
        context.email_addr,
        imap_user=context.imap_user,
        imap_password=context.imap_password,
        timeout=timeout,
        interval=interval,
        request_timeout=context.request_timeout,
        after_ts_ms=context.after_ts_ms,
        excluded=context.excluded,
        mailbox=mailbox,
    )


def _poll_icloud_web_and_imap_code(context: ICloudCodeWaitContext, mailbox: dict[str, Any]) -> str | None:
    started = time.time()
    slice_timeout = max(3, min(6, int(context.interval or 2) * 2))
    slice_interval = max(1, min(2, int(context.interval or 2)))
    while time.time() - started <= context.timeout:
        remaining = max(1, int(context.timeout - (time.time() - started)))
        code = _poll_icloud_web_code(mailbox=mailbox, context=context, timeout=min(slice_timeout, remaining), interval=slice_interval)
        if code:
            return code
        remaining = max(1, int(context.timeout - (time.time() - started)))
        code = _poll_icloud_imap_code(mailbox=mailbox, context=context, timeout=min(slice_timeout, remaining), interval=slice_interval)
        if code:
            return code
    return None


def _icloud_code_wait_context(
    provider: dict[str, Any],
    mailbox: dict[str, Any],
    *,
    timeout: int,
    interval: int,
    request_timeout: int,
) -> ICloudCodeWaitContext | None:
    email_addr = str(mailbox.get("email") or provider.get("email") or provider.get("address") or provider.get("alias") or "").strip().lower()
    if not email_addr:
        return None
    imap_user = _first_non_empty(provider.get("imap_user"), provider.get("imapUser"), provider.get("icloud_imap_user"))
    imap_password = _first_non_empty(provider.get("imap_password"), provider.get("imapPassword"), provider.get("icloud_imap_password"))
    cookies = _load_icloud_cookies(provider)
    web_client = _ICloudHMEClient(cookies, host=str(provider.get("host") or mailbox.get("host") or "icloud.com"), timeout=request_timeout) if cookies else None
    return ICloudCodeWaitContext(
        email_addr=email_addr,
        after_ts_ms=int(mailbox.get("_code_after_ts") or 0),
        excluded=set(str(item) for item in (mailbox.get("_exclude_codes") or []) if item),
        imap_user=imap_user,
        imap_password=imap_password,
        web_client=web_client,
        timeout=timeout,
        interval=interval,
        request_timeout=request_timeout,
    )


def _wait_icloud_code(config: dict[str, Any], mailbox: dict[str, Any], timeout: int, interval: int, request_timeout: int = 30) -> str | None:
    provider = _provider_config_for_mailbox(config, mailbox)
    context = _icloud_code_wait_context(provider, mailbox, timeout=timeout, interval=interval, request_timeout=request_timeout)
    if context is None:
        return None
    if context.web_client and context.imap_user and context.imap_password:
        return _poll_icloud_web_and_imap_code(context, mailbox)
    if context.imap_user and context.imap_password:
        code = _poll_icloud_imap_code(context, mailbox, timeout=context.timeout, interval=context.interval)
        if code:
            return code
    if context.web_client:
        return _poll_icloud_web_code(context, mailbox, timeout=context.timeout, interval=context.interval)
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
    return mail_provider_icloud.wait_imap_code(
        target_email,
        imap_user=imap_user,
        imap_password=imap_password,
        timeout=timeout,
        interval=interval,
        request_timeout=request_timeout,
        after_ts_ms=after_ts_ms,
        excluded=excluded,
        mailbox=mailbox,
        deps=mail_provider_icloud.ICloudImapDeps(
            imap4_ssl=imaplib.IMAP4_SSL,
            internaldate_to_tuple=imaplib.Internaldate2tuple,
            time_time=time.time,
            time_mktime=time.mktime,
            sleep=time.sleep,
            message_from_bytes=email.message_from_bytes,
            to_timestamp_ms=_to_timestamp_ms,
            extract_text_from_raw_email=_extract_text_from_raw_email,
            matches_target_mail=_matches_target_mail,
            extract_code=_extract_code,
            record_code_match=_record_code_match,
        ),
    )


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
