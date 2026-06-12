from __future__ import annotations

import base64
import json
import random
import re
import time
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .registration_environment import (
    get_accept_language,
    get_sec_ch_ua,
    get_timezone,
    get_user_agent,
    get_viewport_height,
    get_viewport_width,
)
from .registration_responses import response_json


class SentinelTokenGenerator:
    MAX_ATTEMPTS = 500000
    MAX_SECONDS = 8.0
    MAX_EXPECTED_ATTEMPTS = 150000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(
        self,
        device_id: str,
        ua: str,
        *,
        accept_language: str = "",
        timezone_name: str = "",
        viewport_width: int = 1920,
        viewport_height: int = 1080,
    ):
        self.device_id = device_id
        self.user_agent = ua
        self.accept_language = str(accept_language or "en-US").split(",", 1)[0].strip() or "en-US"
        self.timezone_name = str(timezone_name or "UTC").strip() or "UTC"
        self.viewport_width = int(viewport_width or 1920)
        self.viewport_height = int(viewport_height or 1080)
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _get_config(self) -> list:
        perf_now = random.uniform(1000, 50000)
        tz_name = self.timezone_name
        dt_value: datetime
        try:
            dt_value = datetime.now(ZoneInfo(tz_name))
        except Exception:
            tz_name = "UTC"
            dt_value = datetime.now(ZoneInfo("UTC"))
        tz_abbr = dt_value.tzname() or tz_name
        offset = dt_value.utcoffset()
        total_minutes = int(offset.total_seconds() // 60) if offset else 0
        sign = "+" if total_minutes >= 0 else "-"
        abs_minutes = abs(total_minutes)
        tz_label = f"GMT{sign}{abs_minutes // 60:02d}{abs_minutes % 60:02d} ({tz_abbr})"
        return [
            f"{self.viewport_width}x{self.viewport_height}",
            dt_value.strftime(f"%a %b %d %Y %H:%M:%S {tz_label}"),
            4294705152,
            random.random(),
            self.user_agent,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None,
            None,
            self.accept_language,
            random.random(),
            random.choice(["vendorSub-undefined", "plugins-undefined", "mimeTypes-undefined", "hardwareConcurrency-undefined"]),
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _b64(data: Any) -> str:
        return base64.b64encode(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii")

    def generate_requirements_token(self) -> str:
        data = self._get_config()
        data[3] = 1
        data[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(data)

    def generate_token(self, seed: str, difficulty: str) -> str:
        difficulty = str(difficulty or "0").strip().lower() or "0"
        if not re.fullmatch(r"[0-9a-f]+", difficulty):
            raise TimeoutError("sentinel_pow_invalid_difficulty")
        search_space = 16 ** len(difficulty)
        threshold = int(difficulty, 16)
        expected_attempts = search_space // max(1, threshold + 1)
        if expected_attempts > self.MAX_EXPECTED_ATTEMPTS:
            raise TimeoutError(f"sentinel_pow_too_hard:{difficulty}")
        start = time.time()
        data = self._get_config()
        for i in range(self.MAX_ATTEMPTS):
            if i and i % 1024 == 0 and time.time() - start > self.MAX_SECONDS:
                raise TimeoutError("sentinel_pow_timeout")
            data[3] = i
            data[9] = round((time.time() - start) * 1000)
            payload = self._b64(data)
            if self._fnv1a_32(seed + payload)[: len(difficulty)] <= difficulty:
                return "gAAAAAB" + payload + "~S"
        return "gAAAAAB" + self.ERROR_PREFIX + self._b64(str(None))


def build_sentinel_token(session: Any, device_id: str, flow: str) -> str:
    generator = SentinelTokenGenerator(
        device_id,
        get_user_agent(),
        accept_language=get_accept_language(),
        timezone_name=get_timezone(),
        viewport_width=get_viewport_width(),
        viewport_height=get_viewport_height(),
    )
    resp = session.post(
        "https://sentinel.openai.com/backend-api/sentinel/req",
        data=json.dumps({"p": generator.generate_requirements_token(), "id": device_id, "flow": flow}),
        headers={
            "Content-Type": "text/plain;charset=UTF-8",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
            "Origin": "https://sentinel.openai.com",
            "User-Agent": get_user_agent(),
            "sec-ch-ua": get_sec_ch_ua(),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        timeout=20,
        verify=False,
    )
    data = response_json(resp)
    token = str(data.get("token") or "").strip()
    if resp.status_code != 200 or not token:
        raise RuntimeError(f"sentinel_req_failed_{resp.status_code}")
    pow_data = data.get("proofofwork") or {}
    p_value = (
        generator.generate_token(str(pow_data.get("seed") or ""), str(pow_data.get("difficulty") or "0"))
        if pow_data.get("required") and pow_data.get("seed")
        else generator.generate_requirements_token()
    )
    return json.dumps({"p": p_value, "t": "", "c": token, "id": device_id, "flow": flow}, separators=(",", ":"))
