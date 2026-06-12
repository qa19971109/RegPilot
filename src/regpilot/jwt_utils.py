from __future__ import annotations

import base64
import json


def decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}
