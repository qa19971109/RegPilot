from __future__ import annotations

import re
from typing import Any


_TASK_ERROR_EXACT = {
    "job_stopped_by_user": "任务已被手动停止",
    "registration_target_not_reached": "注册目标未达成",
    "missing_callback_after_auth": "授权完成后未拿到 OAuth 回调",
    "missing_callback_after_phone_verification": "手机二次验证后未拿到 OAuth 回调",
    "phone_verification_required_sms_key_missing": "需要手机二次验证，但未配置接码服务",
    "manual_phone_verification_required": "无法自动授权：需要人工完成手机二次验证",
    "wait_for_code_timeout": "等待验证码超时",
}

_JOB_MESSAGE_EXACT = {
    "CPA callback submitted": "CPA 回调已提交",
    "missing_callback_after_auth": "授权完成后未拿到 OAuth 回调",
    "missing_callback_after_phone_verification": "手机二次验证后未拿到 OAuth 回调",
    "phone_verification_required_sms_key_missing": "需要手机二次验证，但未配置接码服务",
    "manual_phone_verification_required": "无法自动授权：需要人工完成手机二次验证",
    "wait_for_code_timeout": "等待邮箱验证码超时",
    "job_stopped_by_user": "任务已被手动停止",
    "session_establishment_failed": "登录会话建立失败",
}

_JOB_MESSAGE_REPLACEMENTS = (
    ("phone_verification_after_email_otp_failed", "邮箱验证码后手机二次验证失败"),
    ("phone_verification_after_password_failed", "密码登录后手机二次验证失败"),
    ("phone_verification_after_bind_email_failed", "绑定邮箱后手机二次验证失败"),
    ("email_otp_fallback_failed", "邮箱验证码兜底登录失败"),
)


def _normalize(message: Any) -> str:
    return str(message or "").strip()


def _replace_common_codes(text: str, *, email_otp: bool) -> str:
    send_label = "发送邮箱验证码失败" if email_otp else "发送验证码失败"
    validate_label = "邮箱验证码校验失败" if email_otp else "验证码校验失败"
    text = re.sub(r"login_password_(\d+)", r"密码登录失败（状态码=\1）", text)
    text = text.replace("account_not_created_registration_disallowed", "账号未创建：about-you 被上游拒绝")
    text = re.sub(r"send_otp_(\d+)", rf"{send_label}（状态码=\1）", text)
    text = re.sub(r"validate_otp_(\d+)", rf"{validate_label}（状态码=\1）", text)
    return text


def zh_task_error(message: Any) -> str:
    text = _normalize(message)
    if text in _TASK_ERROR_EXACT:
        return _TASK_ERROR_EXACT[text]
    return _replace_common_codes(text, email_otp=False) or "-"


def zh_job_message(message: Any) -> str:
    text = _normalize(message)
    if text in _JOB_MESSAGE_EXACT:
        return _JOB_MESSAGE_EXACT[text]
    text = _replace_common_codes(text, email_otp=True)
    for old, new in _JOB_MESSAGE_REPLACEMENTS:
        text = text.replace(old, new)
    return text or "-"
