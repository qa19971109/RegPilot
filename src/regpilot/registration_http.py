from __future__ import annotations

import base64
import hashlib
import random
import secrets
import time
import uuid
from typing import Any, Callable

import requests
from curl_cffi import requests as curl_requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .registration_environment import get_common_headers
from .registration_sentinel import build_sentinel_token


def make_trace_headers() -> dict[str, str]:
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    return {
        "traceparent": f"00-{uuid.uuid4().hex}-{format(int(parent_id), '016x')}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


def generate_pkce() -> tuple[str, str]:
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def is_socks_proxy(proxy: str) -> bool:
    candidate = str(proxy or "").strip().lower()
    return candidate.startswith("socks5://") or candidate.startswith("socks5h://")


def create_session(proxy: str = "") -> Any:
    if str(proxy or "").strip():
        return curl_requests.Session(impersonate="chrome", verify=False, proxy=proxy)
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.verify = False
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def request_with_local_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    default_timeout: int = 30,
    retry_attempts: int = 3,
    **kwargs: Any,
):
    last_error = ""
    timeout = kwargs.pop("timeout", default_timeout)
    attempts = max(1, retry_attempts)
    for index in range(attempts):
        try:
            return session.request(method.upper(), url, timeout=timeout, **kwargs), ""
        except Exception as error:
            last_error = str(error)
            if index < attempts - 1:
                time.sleep(1)
    return None, last_error


RequestWithRetry = Callable[..., Any]
TraceHeaders = Callable[[], dict[str, str]]
SentinelBuilder = Callable[..., str]


def validate_otp(
    session: requests.Session,
    device_id: str,
    code: str,
    *,
    auth_base: str,
    request_with_retry_fn: RequestWithRetry,
    trace_headers_fn: TraceHeaders = make_trace_headers,
    sentinel_builder_fn: SentinelBuilder = build_sentinel_token,
):
    headers = get_common_headers()
    headers["referer"] = f"{auth_base}/create-account/email-verification"
    headers["oai-device-id"] = device_id
    headers.update(trace_headers_fn())
    try:
        headers["openai-sentinel-token"] = sentinel_builder_fn(session, device_id, "authorize_continue")
    except Exception:
        pass
    return request_with_retry_fn(
        session,
        "post",
        f"{auth_base}/api/accounts/email-otp/validate",
        json={"code": str(code).strip()},
        headers=headers,
        verify=False,
    )
