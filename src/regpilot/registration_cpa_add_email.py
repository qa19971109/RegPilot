from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RegistrationCpaAddEmailHandler:
    registrar: Any
    mailbox: dict[str, Any]
    email: str
    cpa_oauth: dict[str, Any]
    expected_state: str
    log_fn: Callable[[str], None]
    brief_flow_url_fn: Callable[[str], str]
    mail_config_for_add_email_fn: Callable[[], dict[str, Any]]
    continue_with_optional_add_email_fn: Callable[..., tuple[str, str]]
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    resolve_oauth_callback_fn: Callable[[Any, str, str], str]
    resolve_callback_step_fn: Callable[..., str]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, str]]
    resolved_callback: str = ""
    attempt_count: int = 0

    def continue_if_needed(self, continue_url: str, *, source: str) -> str:
        target = str(continue_url or "").strip()
        if not target:
            return ""
        if self.attempt_count >= 2:
            self.log_fn("注册后 CPA OAuth 绑定邮箱已连续处理 2 次仍未完成，本次停止重复提交")
            return target
        self.attempt_count += 1
        self.log_fn(
            f"注册后 CPA OAuth 需要绑定邮箱，开始处理：source={source} "
            f"attempt={self.attempt_count}/2 url={self.brief_flow_url_fn(target)}"
        )
        verified_url, resolved_bind_email = self.continue_with_optional_add_email_fn(
            self.registrar,
            continue_url=target,
            bind_email=str(self.mailbox.get("bind_email") or ""),
            bind_email_code=str(self.mailbox.get("bind_email_code") or ""),
            bind_mail_config=self.mail_config_for_add_email_fn(),
        )
        verified_url = str(verified_url or "").strip()
        pending_bind_email = str(resolved_bind_email or "").strip()
        if pending_bind_email:
            self.mailbox["bind_email_pending"] = pending_bind_email
            self.log_fn(f"注册后 CPA OAuth 绑定邮箱验证码已提交，等待确认最终绑定：{pending_bind_email}")
        self._resolve_callback_after_verified_url(verified_url)
        if not self.resolved_callback:
            self._reopen_authorize_after_email_bind()
        if self.resolved_callback and pending_bind_email:
            self.mailbox["bind_email"] = pending_bind_email
            self.mailbox.pop("bind_email_pending", None)
            self.log_fn(f"注册后 CPA OAuth 已确认绑定邮箱并拿到回调：{pending_bind_email}")
        self.log_fn(f"注册后 CPA OAuth 绑定邮箱后回调：{'ready' if self.resolved_callback else 'missing'}")
        return verified_url

    def _resolve_callback_after_verified_url(self, verified_url: str) -> None:
        if self.callback_params_from_url_fn(verified_url):
            self.resolved_callback = verified_url
        if not self.resolved_callback:
            self.resolved_callback = self.resolve_oauth_callback_fn(self.registrar, verified_url, self.expected_state)
        if not self.resolved_callback:
            self.resolved_callback = self.resolve_callback_step_fn(
                self.registrar,
                {"status": 200, "ok": True, "json": {}, "text": "", "location": "", "final_url": verified_url},
                self.expected_state,
                allow_state_resume=True,
            )

    def _reopen_authorize_after_email_bind(self) -> None:
        self.log_fn("注册后 CPA OAuth 绑定邮箱已通过但未返回回调，重新打开授权入口继续获取回调")
        try:
            reopened_info = self.registrar.start_authorize(
                email=self.email,
                authorize_url=str(self.cpa_oauth.get("authorize_url") or ""),
                screen_hint="login",
            )
        except Exception as exc:
            reopened_info = {"ok": False, "status": 0, "final_url": "", "error": str(exc)}
        self.log_fn(
            "注册后 CPA OAuth 绑定后重开授权入口："
            f"status={reopened_info.get('status')} final_url={self.brief_flow_url_fn(str(reopened_info.get('final_url') or ''))}"
        )
        self.resolved_callback = self.resolve_callback_step_fn(
            self.registrar,
            reopened_info,
            self.expected_state,
            allow_state_resume=True,
        )
        if self.resolved_callback:
            return
        reopened_state = self.registration_state_from_info_fn(
            {
                "final_url": str(reopened_info.get("final_url") or ""),
                "json": reopened_info.get("json") if isinstance(reopened_info.get("json"), dict) else {},
                "text": str(reopened_info.get("text") or ""),
            }
        )
        self.log_fn(f"注册后 CPA OAuth 绑定后仍未拿到回调：当前页面类型={reopened_state.get('kind') or '-'}")
        if str(reopened_state.get("kind") or "") == "add_email" and self.attempt_count < 2:
            self.log_fn("注册后 CPA OAuth 重开后仍停在绑定邮箱页，继续提交绑定验证码流程")
            self.continue_if_needed(
                str(reopened_state.get("url") or reopened_info.get("final_url") or ""),
                source="reopen",
            )
