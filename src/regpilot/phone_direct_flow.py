from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config import RegisterConfig
from .oauth_token_flow import _save_partial_hero_phone_bind_result, import_result_to_codex2api
from .register_core import save_result


__all__ = [
    "attach_phone_attempt_context",
    "attach_phone_direct_exception_context",
    "build_phone_direct_signup_flow",
    "build_phone_account_exchange_config",
    "continue_phone_signup_after_sms",
    "enrich_mailbox_with_bind_mail_provider",
    "finish_phone_token_result",
    "merge_phone_attempt_context_from_error",
    "merge_phone_attempt_context_from_item",
    "record_phone_activation_attempt",
    "phone_direct_batch_result",
    "phone_direct_error_item",
    "phone_direct_single_result",
]


@dataclass(frozen=True)
class PhoneSignupContinuationDeps:
    load_continue_page_fn: Callable[[Any, str], dict[str, Any]]
    about_you_shape_log_summary_fn: Callable[[str], str]
    accounts_error_code_fn: Callable[[dict[str, Any]], str]
    submit_about_you_form_fn: Callable[..., tuple[str, str]]
    resolve_oauth_callback_fn: Callable[[Any, str, str], str]
    log_fn: Callable[[str], None] = print


def _provider_matches_email(provider: dict[str, Any], email: str) -> bool:
    normalized_email = str(email or "").strip().lower()
    if not normalized_email or "@" not in normalized_email:
        return False
    domain = normalized_email.rsplit("@", 1)[-1]
    for key in ("email", "address", "alias", "login"):
        value = str(provider.get(key) or "").strip().lower()
        if value and value == normalized_email:
            return True
    provider_domain = str(provider.get("domain") or "").strip().lower().lstrip("@")
    return bool(provider_domain and provider_domain == domain)


def _append_unique_phone(target: list[str], value: Any) -> None:
    phone = str(value or "").strip()
    if phone and phone not in target:
        target.append(phone)


def merge_phone_attempt_context_from_item(item: dict[str, Any], attempted_phones: list[str], attempted_phone_prices: list[str]) -> dict[str, Any]:
    for phone in item.get("phones_attempted") or []:
        _append_unique_phone(attempted_phones, phone)
    prices = [str(price or "") for price in (item.get("phone_prices_attempted") or [])]
    if prices:
        attempted_phone_prices.extend(prices)
    if attempted_phones:
        item["phones_attempted"] = list(attempted_phones)
    if attempted_phone_prices:
        item["phone_prices_attempted"] = list(attempted_phone_prices)
    return item


def merge_phone_attempt_context_from_error(exc: BaseException, attempted_phones: list[str], attempted_phone_prices: list[str]) -> None:
    phones = getattr(exc, "phones_attempted", None)
    if phones:
        for phone in phones:
            _append_unique_phone(attempted_phones, phone)
    else:
        _append_unique_phone(attempted_phones, getattr(exc, "phone_number", ""))
    prices = [str(price or "") for price in (getattr(exc, "phone_prices_attempted", None) or [])]
    if prices:
        attempted_phone_prices.extend(prices)


def attach_phone_attempt_context(target: BaseException, attempted_phones: list[str], attempted_phone_prices: list[str], source: BaseException | None = None) -> BaseException:
    source = source or target
    setattr(target, "phones_attempted", list(attempted_phones))
    setattr(target, "phone_prices_attempted", list(attempted_phone_prices))
    if attempted_phones:
        setattr(target, "phone_number", attempted_phones[-1])
    price = str(getattr(source, "activation_price", "") or getattr(source, "phone_price", "") or "")
    if not price and attempted_phone_prices:
        price = attempted_phone_prices[-1]
    setattr(target, "activation_price", price)
    setattr(target, "phone_price", price)
    return target


def attach_phone_direct_exception_context(
    target: BaseException,
    *,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    phone_number: str = "",
    phone_price: str = "",
) -> BaseException:
    setattr(target, "phones_attempted", list(attempted_phones))
    setattr(target, "phone_prices_attempted", list(attempted_phone_prices))
    setattr(target, "phone_number", str(phone_number or ""))
    price = str(phone_price or "")
    setattr(target, "activation_price", price)
    setattr(target, "phone_price", price)
    return target


def record_phone_activation_attempt(
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    activation: dict[str, Any],
) -> tuple[str, str, str]:
    phone_number = str(activation.get("phone_number") or "").strip()
    raw_phone_price = activation.get("price")
    phone_price = "" if raw_phone_price is None else str(raw_phone_price).strip()
    activation_id = str(activation.get("activation_id") or "").strip()
    if phone_number:
        attempted_phones.append(phone_number)
        attempted_phone_prices.append(phone_price)
    return phone_number, phone_price, activation_id


def build_phone_direct_signup_flow(
    *,
    phone_number: str,
    activation_id: str,
    phone_price: str,
    provider: str,
) -> dict[str, Any]:
    return {
        "phone_number": str(phone_number or "").strip(),
        "activation_id": str(activation_id or "").strip(),
        "activation_price": str(phone_price or "").strip(),
        "provider": str(provider or "").strip(),
        "stage": "signup_phone_acquired",
        "status": "phone_ready",
        "purpose": "signup",
        "bind_email": "",
        "callback": {"url": "", "source": ""},
        "import_submit_ok": None,
        "import_submit_message": "",
        "error": {"code": "", "message": "", "retryable": False, "recovery_action": "stop"},
    }


def _phone_signup_needs_about_you(probed_page_type: str, probed_continue_url: str, continue_text: str) -> bool:
    return bool(
        probed_page_type == "about_you"
        or probed_continue_url.endswith("/about-you")
        or "/about-you" in probed_continue_url
        or "about-you" in continue_text
        or "autocomplete=\"name\"" in continue_text
        or "name=\"birthday\"" in continue_text
        or "name=\"age\"" in continue_text
    )


def _load_phone_signup_continue_page(
    registrar: Any,
    validate_info: dict[str, Any],
    deps: PhoneSignupContinuationDeps,
) -> tuple[str, dict[str, Any], str, str, str]:
    validate_continue_url = str(((validate_info.get("json") or {}).get("continue_url") or validate_info.get("final_url") or "")).strip()
    continue_probe = deps.load_continue_page_fn(registrar, validate_continue_url)
    probed_continue_url = str(continue_probe.get("continue_url") or validate_continue_url or "").strip()
    probed_page_type = str(continue_probe.get("page_type") or "").strip()
    continue_text = str(continue_probe.get("text") or "")
    deps.log_fn(f"阶段：短信校验后继续页：页面类型={probed_page_type or '-'}，最终地址={probed_continue_url}")
    return validate_continue_url, continue_probe, probed_continue_url, probed_page_type, continue_text


def _log_phone_signup_about_you_payload_attempts(payload_attempts: list[dict[str, Any]], deps: PhoneSignupContinuationDeps) -> None:
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
    deps.log_fn(f"阶段：about-you 创建账号接口尝试：{attempt_summary}")


def _create_phone_signup_about_you_account(
    registrar: Any,
    *,
    full_name: str,
    birthdate: str,
    probed_continue_url: str,
    validate_continue_url: str,
    continue_text: str,
    deps: PhoneSignupContinuationDeps,
) -> dict[str, Any]:
    deps.log_fn(f"阶段：about-you 页面识别 {deps.about_you_shape_log_summary_fn(continue_text)}")
    try:
        create_info = registrar.create_account(
            full_name,
            birthdate,
            referer=probed_continue_url or validate_continue_url,
            page_context=continue_text,
        )
    except TypeError:
        create_info = registrar.create_account(full_name, birthdate, referer=probed_continue_url or validate_continue_url)
    deps.log_fn(
        "阶段：about-you 创建账号请求 "
        f"状态码={create_info.get('status') or '-'} 成功={'是' if create_info.get('ok') else '否'} 跳转地址={create_info.get('location') or '-'} 最终地址={create_info.get('final_url') or '-'}"
    )
    _log_phone_signup_about_you_payload_attempts(create_info.get("payload_attempts") or [], deps)
    return create_info


def _phone_signup_about_you_api_url(create_info: dict[str, Any], probed_continue_url: str) -> str:
    return str(((create_info.get("json") or {}).get("continue_url") or create_info.get("location") or create_info.get("final_url") or probed_continue_url)).strip()


def _handle_phone_signup_about_you(
    registrar: Any,
    *,
    full_name: str,
    birthdate: str,
    probed_continue_url: str,
    validate_continue_url: str,
    continue_text: str,
    deps: PhoneSignupContinuationDeps,
) -> dict[str, Any]:
    create_info = _create_phone_signup_about_you_account(
        registrar,
        full_name=full_name,
        birthdate=birthdate,
        probed_continue_url=probed_continue_url,
        validate_continue_url=validate_continue_url,
        continue_text=continue_text,
        deps=deps,
    )
    if deps.accounts_error_code_fn(create_info) == "registration_disallowed":
        deps.log_fn("阶段：about-you 被上游拒绝，尝试用手机号+密码登录继续授权")
        return {"registration_disallowed": True, "probed_continue_url": probed_continue_url, "create_info": create_info}
    if create_info.get("ok"):
        submitted_url = _phone_signup_about_you_api_url(create_info, probed_continue_url)
        if submitted_url:
            probed_continue_url = submitted_url
            deps.log_fn(f"阶段：about-you 已通过创建账号接口提交：最终地址={probed_continue_url}")
    if not create_info.get("ok") or not probed_continue_url or "/about-you" in probed_continue_url:
        submitted_url, _ = deps.submit_about_you_form_fn(
            registrar,
            page_url=probed_continue_url or validate_continue_url,
            page_html=continue_text,
            full_name=full_name,
            birthdate=birthdate,
        )
        probed_continue_url = str(submitted_url or probed_continue_url).strip()
        deps.log_fn(f"阶段：about-you 已通过页面表单提交：最终地址={probed_continue_url}")
    return {"registration_disallowed": False, "probed_continue_url": probed_continue_url, "create_info": create_info}


def _resolve_phone_signup_callback_after_sms(
    registrar: Any,
    *,
    initial_info: dict[str, Any],
    probed_continue_url: str,
    phone_number: str,
    deps: PhoneSignupContinuationDeps,
) -> str:
    callback_url = deps.resolve_oauth_callback_fn(registrar, probed_continue_url, str((initial_info or {}).get("state") or ""))
    if callback_url:
        return callback_url
    deps.log_fn("阶段：手机号注册完成，开始后续 OAuth 回调")
    authorize_info = registrar.start_authorize(email=phone_number, screen_hint="login")
    deps.log_fn(f"阶段：后续 OAuth 入口已打开（状态 {authorize_info.get('status')}）")
    return deps.resolve_oauth_callback_fn(registrar, str(authorize_info.get("final_url") or ""), str((authorize_info or {}).get("state") or ""))


def continue_phone_signup_after_sms(
    registrar: Any,
    *,
    initial_info: dict[str, Any],
    validate_info: dict[str, Any],
    full_name: str,
    birthdate: str,
    phone_number: str,
    deps: PhoneSignupContinuationDeps,
) -> dict[str, Any]:
    validate_continue_url, continue_probe, probed_continue_url, probed_page_type, continue_text = _load_phone_signup_continue_page(
        registrar,
        validate_info,
        deps,
    )
    if _phone_signup_needs_about_you(probed_page_type, probed_continue_url, continue_text):
        about_you_result = _handle_phone_signup_about_you(
            registrar,
            full_name=full_name,
            birthdate=birthdate,
            probed_continue_url=probed_continue_url,
            validate_continue_url=validate_continue_url,
            continue_text=continue_text,
            deps=deps,
        )
        probed_continue_url = str(about_you_result.get("probed_continue_url") or "").strip()
        if about_you_result.get("registration_disallowed"):
            return {
                "callback_url": "",
                "registration_disallowed": True,
                "probed_continue_url": probed_continue_url,
                "continue_probe": continue_probe,
                "create_info": about_you_result.get("create_info") or {},
            }
    callback_url = _resolve_phone_signup_callback_after_sms(
        registrar,
        initial_info=initial_info,
        probed_continue_url=probed_continue_url,
        phone_number=phone_number,
        deps=deps,
    )
    return {
        "callback_url": callback_url,
        "registration_disallowed": False,
        "probed_continue_url": probed_continue_url,
        "continue_probe": continue_probe,
    }


def phone_direct_error_item(worker: int, exc: BaseException) -> dict[str, Any]:
    error_item: dict[str, Any] = {"ok": False, "worker": worker, "error": str(exc)}
    phones_attempted = getattr(exc, "phones_attempted", None)
    if phones_attempted:
        error_item["phones_attempted"] = list(phones_attempted)
        error_item["phone_number"] = str(error_item["phones_attempted"][-1] or "")
    phone_prices_attempted = getattr(exc, "phone_prices_attempted", None)
    if phone_prices_attempted:
        error_item["phone_prices_attempted"] = [str(price or "") for price in phone_prices_attempted]
    phone_price = str(getattr(exc, "activation_price", "") or getattr(exc, "phone_price", "") or "")
    if not phone_price and error_item.get("phone_prices_attempted"):
        phone_price = str(error_item["phone_prices_attempted"][-1] or "")
    if phone_price:
        error_item["activation_price"] = phone_price
        error_item["phone_price"] = phone_price
    return error_item


def phone_direct_single_result(item: dict[str, Any]) -> dict[str, Any]:
    ok = bool(item.get("ok"))
    return {
        **item,
        "ok": ok,
        "target_total": 1,
        "success_count": 1 if ok else 0,
        "failure_count": 0 if ok else 1,
        "items": [item] if ok else [],
        "failures": [] if ok else [item],
    }


def phone_direct_batch_result(*, requested_total: int, worker_count: int, successes: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    ok = len(successes) >= requested_total
    result: dict[str, Any] = {
        "ok": ok,
        "target_total": requested_total,
        "threads": worker_count,
        "success_count": len(successes),
        "failure_count": len(failures),
        "items": successes,
        "failures": failures[-10:],
    }
    if successes:
        last = successes[-1]
        result.update(
            {
                "phone_number": str(last.get("phone_number") or ""),
                "password": str(last.get("password") or ""),
                "bind_email": str(last.get("bind_email") or ""),
                "email": str(last.get("email") or ""),
                "callback_url": str(last.get("callback_url") or ""),
            }
        )
    if not ok:
        result["error"] = "phone_direct_target_not_reached"
    return result


def enrich_mailbox_with_bind_mail_provider(mailbox: dict[str, Any], mail_config: dict[str, Any], bind_email: str) -> dict[str, Any]:
    if not isinstance(mailbox, dict):
        mailbox = {}
    email = str(bind_email or mailbox.get("bind_email") or mailbox.get("email") or "").strip()
    providers = mail_config.get("providers") if isinstance(mail_config, dict) else []
    provider = providers[0] if isinstance(providers, list) and providers and isinstance(providers[0], dict) else {}
    if isinstance(providers, list):
        mailbox_provider = str(mailbox.get("provider") or "").strip().lower()
        matched = next(
            (
                item
                for item in providers
                if isinstance(item, dict)
                and (
                    (mailbox_provider and str(item.get("type") or "").strip().lower() == mailbox_provider)
                    or _provider_matches_email(item, email)
                )
            ),
            None,
        )
        if isinstance(matched, dict):
            provider = matched
    provider_type = str(provider.get("type") or mailbox.get("provider") or "").strip()
    if not email or not provider_type:
        return mailbox
    out = dict(mailbox)
    out.setdefault("provider", provider_type)
    out.setdefault("email", email)
    out.setdefault("bind_email", email)
    for key in (
        "base_url",
        "api_key",
        "domain",
        "admin_auth",
        "custom_auth",
        "imap_user",
        "imap_password",
        "cookies_json",
        "cookies_path",
        "host",
        "hme_label",
        "base_email",
        "client_id",
        "refresh_token",
        "microsoft_account_id",
        "mailboxes",
        "sender_filters",
        "subject_filters",
        "required_keywords",
        "alias_enabled",
        "alias_max_per_account",
    ):
        value = provider.get(key)
        if value not in (None, "") and not out.get(key):
            out[key] = value
    return out


def build_phone_account_exchange_config(
    *,
    payload: dict[str, Any],
    effective_proxy: str,
    hero_sms: Any,
    codex2api_url: str,
    codex2api_admin_key: str,
    wants_codex2api: bool,
    mail_config: dict[str, Any],
) -> RegisterConfig:
    exchange_config = RegisterConfig(
        proxy=effective_proxy,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        codex2api_proxy_url=str(payload.get("codex2api_proxy_url") or "").strip(),
        codex2api_auto_import=wants_codex2api,
        sms_provider=hero_sms.provider,
        sms_api_key=hero_sms.api_key,
        hero_sms_api_key=hero_sms.api_key if hero_sms.provider == "hero_sms" else "",
        smsbower_api_key=hero_sms.api_key if hero_sms.provider == "smsbower" else "",
        hero_sms_base_url=hero_sms.base_url if hero_sms.provider == "hero_sms" else "",
        smsbower_base_url=hero_sms.base_url if hero_sms.provider == "smsbower" else "",
        hero_sms_country=hero_sms.country,
        hero_sms_service=hero_sms.service,
        hero_sms_min_price=hero_sms.min_price,
        hero_sms_max_price=hero_sms.max_price,
        hero_sms_wait_timeout=hero_sms.wait_timeout,
        hero_sms_wait_interval=hero_sms.wait_interval,
        hero_sms_auto_retry=False,
    )
    exchange_config.mail.request_timeout = int(mail_config.get("request_timeout") or 30)
    exchange_config.mail.wait_timeout = int(mail_config.get("wait_timeout") or 60)
    exchange_config.mail.wait_interval = int(mail_config.get("wait_interval") or 2)
    exchange_config.mail.providers = list(mail_config.get("providers") or [])
    exchange_config.mail.proxy = effective_proxy
    return exchange_config


def _prepare_phone_token_result(
    *,
    token_result: Any,
    mailbox: dict[str, Any] | None,
    password: str,
    phone_number: str,
    mail_config: dict[str, Any],
    callback_url: str,
) -> tuple[str, str]:
    if not token_result.ok:
        raise RuntimeError(str(token_result.error or "token_exchange_failed"))
    token_result.password = password
    token_result.mailbox = token_result.mailbox or (mailbox if isinstance(mailbox, dict) else {})
    bind_email = str((token_result.mailbox or {}).get("bind_email") or "")
    token_result.mailbox = enrich_mailbox_with_bind_mail_provider(token_result.mailbox or {}, mail_config, bind_email)
    resolved_callback_url = str(getattr(token_result, "callback_url", "") or callback_url).strip()
    token_result.email = bind_email or str(getattr(token_result, "email", "") or "") or phone_number
    token_result.callback_url = resolved_callback_url
    return bind_email, resolved_callback_url


def _save_phone_token_account(token_result: Any, save_account_fn: Callable[..., Any] | None) -> None:
    try:
        if save_account_fn is None:
            from .accounts_store import save_registration_result_to_account as save_account_fn

        save_account_fn(token_result, source="phone_signup")
        print("阶段：账号已保存到账号池")
    except Exception as exc:
        print(f"阶段：账号池保存失败：{exc}")


def _import_phone_token_to_codex2api(
    *,
    token_result: Any,
    bind_email: str,
    phone_number: str,
    wants_codex2api: bool,
    codex2api_url: str,
    codex2api_admin_key: str,
    codex2api_proxy_url: str,
    import_result_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if not wants_codex2api:
        return {}
    if bool((token_result.mailbox or {}).get("_cpa_submit_ok")):
        return {"ok": True, "message": str((token_result.mailbox or {}).get("_cpa_submit_message") or "CPA callback submitted")}
    try:
        return import_result_fn(
            token_result,
            codex2api_url=codex2api_url,
            admin_key=codex2api_admin_key,
            account_name=token_result.email or bind_email or phone_number,
            proxy_url=codex2api_proxy_url,
        )
    except Exception as exc:
        result = {"ok": False, "message": str(exc or "codex2api_import_failed")}
        print(f"阶段：Codex2API 导入失败：{result['message']}")
        return result


def _apply_phone_token_import_result(phone_flow: dict[str, Any], codex2api_result: dict[str, Any]) -> None:
    import_ok = bool(codex2api_result.get("ok")) if codex2api_result else False
    import_message = str(codex2api_result.get("message") or "") if codex2api_result else ""
    phone_flow["stage"] = "callback_submitted"
    phone_flow["status"] = "callback_submitted"
    phone_flow["import_submit_ok"] = import_ok
    phone_flow["import_submit_message"] = import_message
    phone_flow["codex2api_import_submit_ok"] = import_ok
    phone_flow["codex2api_import_submit_message"] = import_message


def _phone_token_result_payload(
    *,
    phone_number: str,
    password: str,
    bind_email: str,
    resolved_callback_url: str,
    codex2api_result: dict[str, Any],
    phone_price: str,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
) -> dict[str, Any]:
    import_ok = bool(codex2api_result.get("ok")) if codex2api_result else False
    import_message = str(codex2api_result.get("message") or "") if codex2api_result else ""
    return {
        "ok": True,
        "phone_number": phone_number,
        "password": password,
        "bind_email": bind_email,
        "email": bind_email,
        "callback_url": resolved_callback_url,
        "import_submit_ok": import_ok,
        "import_submit_message": import_message,
        "codex2api_import_submit_ok": import_ok,
        "codex2api_import_submit_message": import_message,
        "activation_price": phone_price,
        "phone_price": phone_price,
        "phones_attempted": list(attempted_phones),
        "phone_prices_attempted": list(attempted_phone_prices),
    }


def finish_phone_token_result(
    *,
    token_result: Any,
    mailbox: dict[str, Any] | None = None,
    password: str,
    phone_number: str,
    phone_price: str,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    phone_flow: dict[str, Any],
    mail_config: dict[str, Any],
    callback_url: str = "",
    wants_codex2api: bool = False,
    codex2api_url: str = "",
    codex2api_admin_key: str = "",
    codex2api_proxy_url: str = "",
    save_result_fn: Callable[..., Any] = save_result,
    import_result_fn: Callable[..., dict[str, Any]] = import_result_to_codex2api,
    save_partial_fn: Callable[..., Any] = _save_partial_hero_phone_bind_result,
    save_account_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    bind_email, resolved_callback_url = _prepare_phone_token_result(
        token_result=token_result,
        mailbox=mailbox,
        password=password,
        phone_number=phone_number,
        mail_config=mail_config,
        callback_url=callback_url,
    )
    phone_flow["bind_email"] = bind_email
    phone_flow["callback"] = {"url": resolved_callback_url, "source": "resolved"}
    save_result_fn(token_result)
    _save_phone_token_account(token_result, save_account_fn)
    codex2api_result = _import_phone_token_to_codex2api(
        token_result=token_result,
        bind_email=bind_email,
        phone_number=phone_number,
        wants_codex2api=wants_codex2api,
        codex2api_url=codex2api_url,
        codex2api_admin_key=codex2api_admin_key,
        codex2api_proxy_url=codex2api_proxy_url,
        import_result_fn=import_result_fn,
    )
    if wants_codex2api and not bool(codex2api_result.get("ok")):
        raise RuntimeError(str(codex2api_result.get("message") or "import_submit_failed"))
    _apply_phone_token_import_result(phone_flow, codex2api_result)
    save_partial_fn(phone_flow=phone_flow, password=password, note="已完成 token 导入")
    return _phone_token_result_payload(
        phone_number=phone_number,
        password=password,
        bind_email=bind_email,
        resolved_callback_url=resolved_callback_url,
        codex2api_result=codex2api_result,
        phone_price=phone_price,
        attempted_phones=attempted_phones,
        attempted_phone_prices=attempted_phone_prices,
    )
