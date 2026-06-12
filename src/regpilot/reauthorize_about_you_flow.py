from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReauthorizeAboutYouDeps:
    auth_base: str
    load_continue_page_fn: Callable[[Any, str], dict[str, Any]]
    safe_response_summary_fn: Callable[[dict[str, Any]], dict[str, Any]]
    callback_params_from_url_fn: Callable[[str], dict[str, str] | None]
    registration_state_from_info_fn: Callable[[dict[str, Any]], dict[str, Any]]
    random_name_fn: Callable[[], tuple[str, str]]
    random_birthdate_fn: Callable[[], str]
    log_stage_fn: Callable[[str], None]
    about_you_shape_log_summary_fn: Callable[[str], str]
    accounts_error_code_fn: Callable[[dict[str, Any]], str]
    submit_about_you_form_fn: Callable[..., tuple[str, str]]
    short_url_fn: Callable[[Any], str]
    zh_bool_fn: Callable[[Any], str]
    resolve_callback_step_fn: Callable[..., str]
    resolve_oauth_callback_fn: Callable[..., str]


@dataclass(frozen=True)
class AboutYouIdentity:
    full_name: str
    birthdate: str


@dataclass(frozen=True)
class AboutYouCreateFailureResult:
    callback: str
    create_info: dict[str, Any]
    stop: bool


def _probe_about_you_page(registrar: Any, target: str, deps: ReauthorizeAboutYouDeps) -> tuple[dict[str, Any], dict[str, Any]]:
    probe = deps.load_continue_page_fn(registrar, target)
    debug = {
        "target": target,
        "probe": deps.safe_response_summary_fn(
            {
                "ok": bool(probe.get("ok", True)),
                "status": probe.get("status"),
                "json": probe.get("json") if isinstance(probe.get("json"), dict) else {},
                "text": str(probe.get("text") or "")[:2000],
                "location": probe.get("location") or "",
                "final_url": probe.get("continue_url") or target,
            }
        ),
    }
    return probe, debug


def _about_you_probe_state(probe: dict[str, Any], page_url: str, deps: ReauthorizeAboutYouDeps) -> dict[str, Any]:
    return deps.registration_state_from_info_fn(
        {
            "json": probe.get("json") if isinstance(probe.get("json"), dict) else {},
            "text": str(probe.get("text") or ""),
            "location": probe.get("location") or "",
            "final_url": page_url,
        }
    )


def _random_about_you_identity(page_html: str, deps: ReauthorizeAboutYouDeps) -> AboutYouIdentity:
    first_name, last_name = deps.random_name_fn()
    identity = AboutYouIdentity(full_name=f"{first_name} {last_name}", birthdate=deps.random_birthdate_fn())
    deps.log_stage_fn(f"about-you 页面识别：{deps.about_you_shape_log_summary_fn(page_html)}")
    deps.log_stage_fn("提交 about-you 姓名/生日")
    return identity


def _log_about_you_payload_attempts(create_info: dict[str, Any], deps: ReauthorizeAboutYouDeps) -> None:
    payload_attempts = create_info.get("payload_attempts") or []
    if not payload_attempts:
        return
    attempt_summary = ", ".join(
        (
            f"{'+'.join(str(key) for key in (attempt.get('keys') or []) if key != 'name') or 'name'}:"
            f"{attempt.get('status')}"
            f"{('/' + str(attempt.get('error_code'))) if attempt.get('error_code') else ''}"
        )
        for attempt in payload_attempts[:6]
    )
    deps.log_stage_fn(f"about-you 创建账号接口尝试：{attempt_summary}")


def _create_about_you_account(
    registrar: Any,
    page_url: str,
    page_html: str,
    bind_email: str,
    identity: AboutYouIdentity,
    deps: ReauthorizeAboutYouDeps,
) -> dict[str, Any]:
    create_kwargs = {"referer": page_url, "page_context": page_html}
    if bind_email:
        create_kwargs["email"] = bind_email
    create_info = registrar.create_account(identity.full_name, identity.birthdate, **create_kwargs)
    _log_about_you_payload_attempts(create_info, deps)
    deps.log_stage_fn(
        "about-you 创建账号请求："
        f"状态码={create_info.get('status') or '-'}，成功={deps.zh_bool_fn(create_info.get('ok'))}，"
        f"跳转地址={deps.short_url_fn(create_info.get('location'))}，最终地址={deps.short_url_fn(create_info.get('final_url'))}"
    )
    return create_info


def _submit_about_you_form_fallback(
    registrar: Any,
    page_url: str,
    page_html: str,
    create_info: dict[str, Any],
    identity: AboutYouIdentity,
    deps: ReauthorizeAboutYouDeps,
) -> tuple[str, str]:
    return deps.submit_about_you_form_fn(
        registrar,
        page_url=page_url,
        page_html=page_html or str(create_info.get("text") or ""),
        full_name=identity.full_name,
        birthdate=identity.birthdate,
    )


def _handle_missing_email_about_you(
    registrar: Any,
    page_url: str,
    page_html: str,
    create_info: dict[str, Any],
    identity: AboutYouIdentity,
    debug: dict[str, Any],
    deps: ReauthorizeAboutYouDeps,
) -> AboutYouCreateFailureResult:
    deps.log_stage_fn("about-you 返回 missing_email，需要先绑定邮箱")
    try:
        submitted_url, submitted_html = _submit_about_you_form_fallback(registrar, page_url, page_html, create_info, identity, deps)
        submitted_state = deps.registration_state_from_info_fn({"final_url": str(submitted_url or ""), "json": {}, "text": str(submitted_html or "")})
        debug["missing_email_form_submit"] = {
            "url": str(submitted_url or ""),
            "kind": str(submitted_state.get("kind") or ""),
            "html_len": len(str(submitted_html or "")),
        }
        if deps.callback_params_from_url_fn(str(submitted_url or "")):
            return AboutYouCreateFailureResult(str(submitted_url), create_info, True)
        if str(submitted_state.get("kind") or "") == "add_email":
            debug["missing_email_continue_url"] = str(submitted_url or "")
            deps.log_stage_fn(f"about-you 已切换到绑定邮箱页：地址={deps.short_url_fn(submitted_url)}")
    except Exception as exc:
        debug["missing_email_form_submit_error"] = str(exc)
        deps.log_stage_fn(f"about-you 缺少邮箱时切换绑定邮箱页失败：{exc}")
    return AboutYouCreateFailureResult("", create_info, True)


def _handle_about_you_create_failure(
    registrar: Any,
    page_url: str,
    page_html: str,
    create_info: dict[str, Any],
    identity: AboutYouIdentity,
    debug: dict[str, Any],
    deps: ReauthorizeAboutYouDeps,
) -> AboutYouCreateFailureResult:
    error_code = deps.accounts_error_code_fn(create_info)
    debug["create_account_error_code"] = error_code
    if error_code == "registration_disallowed":
        raise RuntimeError("registration_disallowed")
    if error_code == "missing_email":
        return _handle_missing_email_about_you(registrar, page_url, page_html, create_info, identity, debug, deps)
    try:
        submitted_url, submitted_html = _submit_about_you_form_fallback(registrar, page_url, page_html, create_info, identity, deps)
        debug["form_submit"] = {"url": str(submitted_url or ""), "html_len": len(str(submitted_html or ""))}
        if submitted_url:
            create_info = {
                **create_info,
                "ok": "/about-you" not in str(submitted_url).lower(),
                "location": str(submitted_url),
                "final_url": str(submitted_url),
            }
            deps.log_stage_fn(f"about-you 页面表单提交结果：最终地址={deps.short_url_fn(submitted_url)}")
    except Exception as exc:
        debug["form_submit_error"] = str(exc)
        deps.log_stage_fn(f"about-you 页面表单提交失败：{exc}")
    return AboutYouCreateFailureResult("", create_info, False)


def _about_you_callback_target(create_info: dict[str, Any], page_url: str) -> str:
    return str(
        ((create_info.get("json") or {}).get("continue_url") if isinstance(create_info.get("json"), dict) else "")
        or create_info.get("location")
        or create_info.get("final_url")
        or page_url
    ).strip()


def _resolve_about_you_callback(
    registrar: Any,
    create_info: dict[str, Any],
    callback_target: str,
    state: str,
    deps: ReauthorizeAboutYouDeps,
) -> str:
    callback = deps.resolve_callback_step_fn(registrar, create_info, state, allow_state_resume=True)
    if not callback and callback_target:
        callback = deps.resolve_oauth_callback_fn(registrar, callback_target, state)
    return callback


def continue_with_optional_about_you(
    registrar: Any,
    continue_url: str,
    state: str,
    bind_email: str = "",
    *,
    deps: ReauthorizeAboutYouDeps,
) -> tuple[str, dict[str, Any]]:
    target = str(continue_url or "").strip() or f"{deps.auth_base}/about-you"
    probe, debug = _probe_about_you_page(registrar, target, deps)
    callback = str(probe.get("callback_url") or "").strip()
    if callback and deps.callback_params_from_url_fn(callback):
        return callback, debug
    page_url = str(probe.get("continue_url") or target).strip()
    page_html = str(probe.get("text") or "")
    state_info = _about_you_probe_state(probe, page_url, deps)
    debug["state"] = state_info
    if str(state_info.get("kind") or "") != "about_you":
        return "", debug

    identity = _random_about_you_identity(page_html, deps)
    create_info = _create_about_you_account(registrar, page_url, page_html, bind_email, identity, deps)
    debug["create_account"] = deps.safe_response_summary_fn(create_info)
    if not create_info.get("ok"):
        failure = _handle_about_you_create_failure(registrar, page_url, page_html, create_info, identity, debug, deps)
        create_info = failure.create_info
        if failure.stop:
            return failure.callback, debug
    callback_target = _about_you_callback_target(create_info, page_url)
    callback = _resolve_about_you_callback(registrar, create_info, callback_target, state, deps)
    debug["callback_ready"] = bool(callback)
    debug["callback_target"] = callback_target[:160]
    debug["next_url"] = callback_target[:300]
    return callback, debug
