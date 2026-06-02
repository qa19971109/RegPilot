from __future__ import annotations

import re
from typing import Any


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lk = str(key).lower()
            if (
                lk in {"token", "access_token", "refresh_token", "id_token"}
                or (lk.endswith("_token") and not lk.startswith("has_"))
                or "password" in lk
                or "cookie" in lk
                or "secret" in lk
                or "api_key" in lk
                or lk.endswith("_auth")
                or lk.endswith("_key")
                or lk in {"key", "admin_auth", "custom_auth", "access_token", "refresh_token", "id_token"}
            ):
                out[key] = "***" if item else ""
            else:
                out[key] = _redact_sensitive(item)
        return out
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    patterns = [
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;&)}]+", r"\1[hidden]"),
        (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [hidden]"),
        (r"(?i)(api[_-]?key|sms[_-]?api[_-]?key|hero[_-]?sms[_-]?api[_-]?key|smsbower[_-]?api[_-]?key|mail[_-]?api[_-]?key)(\s*[=:]\s*)[^\s,;&)}]+", r"\1\2[hidden]"),
        (r"(?i)(password|cookie|cookies|admin[_-]?auth|custom[_-]?auth|admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token)(\s*[=:]\s*)[^\s,;&)}]+", r"\1\2[hidden]"),
        (r'(?i)("(?:api[_-]?key|sms[_-]?api[_-]?key|hero[_-]?sms[_-]?api[_-]?key|smsbower[_-]?api[_-]?key|mail[_-]?api[_-]?key|password|cookie|cookies|admin[_-]?auth|custom[_-]?auth|admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token)"\s*:\s*")[^"]*(")', r"\1[hidden]\2"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _strip_visible_debug_output(output: str) -> str:
    lines: list[str] = []
    for line in str(output or "").splitlines():
        if line.startswith("Flow debug summary:") or line.startswith("Flow debug encode failed:"):
            continue
        lines.append(_redact_sensitive_text(line))
    return "\n".join(lines)


def _zh_job_message(message: Any) -> str:
    text = str(message or "").strip()
    exact = {
        "CPA callback submitted": "CPA 回调已提交",
        "missing_callback_after_auth": "授权完成后未拿到 OAuth 回调",
        "missing_callback_after_phone_verification": "手机二次验证后未拿到 OAuth 回调",
        "phone_verification_required_sms_key_missing": "需要手机二次验证，但未配置接码服务",
        "manual_phone_verification_required": "无法自动授权：需要人工完成手机二次验证",
        "wait_for_code_timeout": "等待邮箱验证码超时",
        "job_stopped_by_user": "任务已被手动停止",
        "session_establishment_failed": "登录会话建立失败",
    }
    if text in exact:
        return exact[text]
    text = re.sub(r"login_password_(\d+)", r"密码登录失败（状态码=\1）", text)
    text = text.replace("account_not_created_registration_disallowed", "账号未创建：about-you 被上游拒绝")
    text = re.sub(r"send_otp_(\d+)", r"发送邮箱验证码失败（状态码=\1）", text)
    text = re.sub(r"validate_otp_(\d+)", r"邮箱验证码校验失败（状态码=\1）", text)
    text = text.replace("phone_verification_after_email_otp_failed", "邮箱验证码后手机二次验证失败")
    text = text.replace("phone_verification_after_password_failed", "密码登录后手机二次验证失败")
    text = text.replace("phone_verification_after_bind_email_failed", "绑定邮箱后手机二次验证失败")
    text = text.replace("email_otp_fallback_failed", "邮箱验证码兜底登录失败")
    return text or "-"


def _safe_job(job: dict[str, Any]) -> dict[str, Any]:
    safe = _redact_sensitive(job)
    if isinstance(safe, dict) and "output" in safe:
        safe["output"] = _strip_visible_debug_output(str(safe.get("output") or ""))
    error = safe.get("error") if isinstance(safe, dict) else None
    if isinstance(error, dict) and "traceback" in error:
        error["traceback"] = "[hidden; see server logs]"
    return safe
