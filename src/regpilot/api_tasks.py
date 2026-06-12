from __future__ import annotations

from concurrent.futures import wait
import threading
import time
from typing import Any

from .cli import load_config
from .config import DATA_DIR, LOG_DIR, ensure_dirs
from .job_runner import run_job as _run_job_impl
from .job_store import JobCancelledError, JobStore as _BaseJobStore
from .json_store import write_json_atomic
from .logging_utils import reset_log_context, set_log_context
from .register_core import run_placeholder, save_result, PlatformRegistrar, _exchange_registered_account_tokens
from .registration_about_you import about_you_shape_log_summary as _about_you_shape_log_summary
from .registration_environment import environment_profile_context, prepare_environment_profile_from_payload, summarize_environment_profile
from .registration_responses import accounts_error_code as _accounts_error_code
from .registration_identity import random_birthdate as _random_birthdate, random_name as _random_name, random_password as _random_password
from .oauth_token_flow import HERO_SMS_MAX_RETRY_COUNT, HERO_SMS_RELEASE_AFTER_SECONDS, HERO_SMS_RESEND_AFTER_SECONDS, _continue_with_optional_add_email, _load_continue_page, _probe_phone_signup_password_page, _resolve_oauth_callback, _save_partial_hero_phone_bind_result, _set_phone_flow_stage, _submit_about_you_form, acquire_hero_sms_phone, import_result_to_codex2api, poll_hero_sms_code, set_hero_sms_status
from .phone_direct_flow import (
    _provider_matches_email as _phone_direct_provider_matches_email,
    attach_phone_direct_exception_context,
    build_phone_direct_signup_flow,
    build_phone_account_exchange_config,
    PhoneSignupContinuationDeps,
    continue_phone_signup_after_sms,
    enrich_mailbox_with_bind_mail_provider,
    finish_phone_token_result,
    record_phone_activation_attempt,
)
from .phone_direct_batch import PhoneDirectBatchDeps, run_phone_direct_batch
from .phone_direct_once_runner import PhoneDirectOnceDeps, run_phone_direct_once
from .webui_config_defaults import WEBUI_CONFIG_DEFAULTS
from . import task_payload_config, task_register_runner, task_sms_config, task_sms_lookup, webui_config_store
from . import job_log_maintenance, phone_direct_diagnostics, task_error_messages


WEBUI_CONFIG_PATH = DATA_DIR / "webui_config.json"
WEBUI_CONFIG_LAST_VALID_PATH = DATA_DIR / "webui_config.last_valid.json"
PHONE_DIRECT_FUTURE_WAIT_HEARTBEAT_SECONDS = 15.0


def _zh_task_error(message: Any) -> str:
    return task_error_messages.zh_task_error(message)


DEFAULT_HERO_COUNTRY_LABEL_BY_ID = task_sms_lookup.DEFAULT_HERO_COUNTRY_LABEL_BY_ID


def _hero_country_label(country_id: str, eng_name: str = "") -> str:
    return task_sms_lookup.hero_country_label(country_id, eng_name)


LEGACY_WEBUI_REMOVED = True

class JobStore(_BaseJobStore):
    def __init__(self, *, restore: bool = False) -> None:
        super().__init__(
            restore=restore,
            log_dir_getter=lambda: LOG_DIR,
            prune_callback=lambda: _prune_job_logs(),
            error_translator=_zh_task_error,
        )


JOBS = JobStore(restore=True)
_JOB_EXECUTION_LOCK = threading.Lock()
_CPA_OAUTH_LOCK = threading.Lock()
# phone_direct_once_runner receives this lock and enters `with _CPA_OAUTH_LOCK:` to 避免并发授权 state 被覆盖.


def _job_log_max_bytes() -> int:
    return job_log_maintenance.job_log_max_bytes(_load_webui_config)


def _prune_job_logs() -> None:
    job_log_maintenance.prune_job_logs(LOG_DIR / "jobs", _job_log_max_bytes)


def _clone_webui_config_defaults() -> dict[str, dict[str, Any]]:
    return webui_config_store.clone_webui_config_defaults(WEBUI_CONFIG_DEFAULTS)


_WEBUI_LEGACY_KEY_MIGRATIONS = webui_config_store.WEBUI_LEGACY_KEY_MIGRATIONS


def _migrate_legacy_webui_config(config: dict[str, Any]) -> dict[str, Any]:
    return webui_config_store.migrate_legacy_webui_config(config)


def _merge_webui_config(raw: Any) -> dict[str, dict[str, Any]]:
    return webui_config_store.merge_webui_config(raw, WEBUI_CONFIG_DEFAULTS)


def _read_webui_config_json(path) -> Any:
    return webui_config_store.read_webui_config_json(path)


def _backup_corrupt_webui_config() -> None:
    webui_config_store.backup_corrupt_webui_config(WEBUI_CONFIG_PATH)


def _webui_config_last_valid_path():
    return webui_config_store.webui_config_last_valid_path(DATA_DIR, WEBUI_CONFIG_PATH, WEBUI_CONFIG_LAST_VALID_PATH)


def _write_last_valid_webui_config(config: dict[str, dict[str, Any]]) -> None:
    webui_config_store.write_last_valid_webui_config(
        config,
        data_dir=DATA_DIR,
        config_path=WEBUI_CONFIG_PATH,
        configured_last_valid_path=WEBUI_CONFIG_LAST_VALID_PATH,
        ensure_dirs_fn=ensure_dirs,
        write_json_atomic_fn=write_json_atomic,
    )


def _load_webui_config() -> dict[str, dict[str, Any]]:
    return webui_config_store.load_webui_config(
        defaults=WEBUI_CONFIG_DEFAULTS,
        data_dir=DATA_DIR,
        config_path=WEBUI_CONFIG_PATH,
        configured_last_valid_path=WEBUI_CONFIG_LAST_VALID_PATH,
        ensure_dirs_fn=ensure_dirs,
        write_json_atomic_fn=write_json_atomic,
    )


_WEBUI_CONFIG_CACHE = webui_config_store.WEBUI_CONFIG_CACHE
_WEBUI_CONFIG_LOCK = webui_config_store.WEBUI_CONFIG_LOCK


def _webui_config_signature() -> tuple[Any, Any]:
    return webui_config_store.webui_config_signature(WEBUI_CONFIG_PATH, DATA_DIR)


def _load_webui_config_uncached() -> dict[str, dict[str, Any]]:
    return webui_config_store.load_webui_config_uncached(
        defaults=WEBUI_CONFIG_DEFAULTS,
        data_dir=DATA_DIR,
        config_path=WEBUI_CONFIG_PATH,
        configured_last_valid_path=WEBUI_CONFIG_LAST_VALID_PATH,
        ensure_dirs_fn=ensure_dirs,
        write_json_atomic_fn=write_json_atomic,
    )


def _get_cached_webui_config() -> dict[str, dict[str, Any]]:
    return webui_config_store.get_cached_webui_config(
        defaults=WEBUI_CONFIG_DEFAULTS,
        data_dir=DATA_DIR,
        config_path=WEBUI_CONFIG_PATH,
        configured_last_valid_path=WEBUI_CONFIG_LAST_VALID_PATH,
        ensure_dirs_fn=ensure_dirs,
        write_json_atomic_fn=write_json_atomic,
    )


def _invalidate_webui_config_cache() -> None:
    webui_config_store.invalidate_webui_config_cache()


_WEBUI_BOOL_KEYS = webui_config_store.WEBUI_BOOL_KEYS
_WEBUI_POSITIVE_INT_KEYS = webui_config_store.WEBUI_POSITIVE_INT_KEYS


def _positive_int_from_payload(payload: dict[str, Any], key: str, default: int = 1) -> int:
    return webui_config_store.positive_int_from_payload(payload, key, default)


def _sanitize_webui_config_value(key: str, value: Any, default: Any) -> Any:
    return webui_config_store.sanitize_webui_config_value(key, value, default)


def _save_webui_config(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return webui_config_store.save_webui_config(
        payload,
        defaults=WEBUI_CONFIG_DEFAULTS,
        data_dir=DATA_DIR,
        config_path=WEBUI_CONFIG_PATH,
        configured_last_valid_path=WEBUI_CONFIG_LAST_VALID_PATH,
        ensure_dirs_fn=ensure_dirs,
        write_json_atomic_fn=write_json_atomic,
    )


def _reset_webui_config() -> dict[str, dict[str, Any]]:
    return webui_config_store.reset_webui_config(
        defaults=WEBUI_CONFIG_DEFAULTS,
        data_dir=DATA_DIR,
        config_path=WEBUI_CONFIG_PATH,
        configured_last_valid_path=WEBUI_CONFIG_LAST_VALID_PATH,
        ensure_dirs_fn=ensure_dirs,
        write_json_atomic_fn=write_json_atomic,
    )


def _bool_from_payload(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    return webui_config_store.bool_from_payload(payload, key, default)


def _bool_from_renamed_payload(payload: dict[str, Any], key: str, legacy_key: str, default: bool = False) -> bool:
    return task_payload_config.bool_from_renamed_payload(payload, key, legacy_key, default)


def _renamed_payload_value(payload: dict[str, Any], key: str, legacy_key: str, default: Any = None) -> Any:
    return task_payload_config.renamed_payload_value(payload, key, legacy_key, default)


def _positive_int_from_renamed_payload(payload: dict[str, Any], key: str, legacy_key: str, default: int, minimum: int = 1) -> int:
    return task_payload_config.positive_int_from_renamed_payload(payload, key, legacy_key, default, minimum)


def _cloudflare_mail_provider_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return task_payload_config.cloudflare_mail_provider_from_payload(payload)


def _cloudflare_mail_provider_is_ready(provider: dict[str, Any]) -> bool:
    return task_payload_config.cloudflare_mail_provider_is_ready(provider)


def _hotmail_mail_provider_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return task_payload_config.hotmail_mail_provider_from_payload(payload)


def _register_config_from_payload(payload: dict[str, Any]):
    return task_payload_config.register_config_from_payload(payload, load_config_fn=load_config)


def _mail_config_dict_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return task_payload_config.mail_config_dict_from_payload(payload)


def _provider_matches_email(provider: dict[str, Any], email: str) -> bool:
    return _phone_direct_provider_matches_email(provider, email)


def _enrich_mailbox_with_bind_mail_provider(mailbox: dict[str, Any], mail_config: dict[str, Any], bind_email: str) -> dict[str, Any]:
    return enrich_mailbox_with_bind_mail_provider(mailbox, mail_config, bind_email)


def _phone_signup_entry_error(*items: Any) -> str:
    return phone_direct_diagnostics.phone_signup_entry_error(*items)


def _sms_retry_exhausted_message(provider: str, attempts: int, error: str) -> str:
    return phone_direct_diagnostics.sms_retry_exhausted_message(provider, attempts, error)


def _unwrap_sms_retry_error(error: str) -> str:
    return phone_direct_diagnostics.unwrap_sms_retry_error(error)


def _is_sms_inventory_error(error: str) -> bool:
    return phone_direct_diagnostics.is_sms_inventory_error(error)


def _sms_retry_count_from_payload(payload: dict[str, Any], auto_retry: bool) -> int:
    return phone_direct_diagnostics.sms_retry_count_from_payload(payload, auto_retry, default_retry_count=HERO_SMS_MAX_RETRY_COUNT)


def _safe_register_failure_summary(info: dict[str, Any]) -> str:
    return phone_direct_diagnostics.safe_register_failure_summary(info)


def _run_job(kind: str, func, *args: Any, **kwargs: Any) -> dict[str, str]:
    return _run_job_impl(
        JOBS,
        _JOB_EXECUTION_LOCK,
        kind,
        func,
        *args,
        error_translator=_zh_task_error,
        cancelled_error_type=JobCancelledError,
        **kwargs,
    )


def _run_register(payload: dict[str, Any]) -> dict[str, Any]:
    return task_register_runner.run_register_task(
        payload,
        register_config_from_payload=_register_config_from_payload,
        run_placeholder=run_placeholder,
        save_result=save_result,
        sleep=time.sleep,
    )


def _hero_sms_payload_with_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    return task_sms_config.sms_payload_with_webui_fallback(payload, _load_webui_config)


def _sms_provider_from_payload(payload: dict[str, Any]) -> str:
    return task_sms_config.sms_provider_from_payload(payload)


def _sms_api_key_from_payload(payload: dict[str, Any], provider: str) -> str:
    return task_sms_config.sms_api_key_from_payload(payload, provider)


def _sms_config_from_payload(payload: dict[str, Any]):
    return task_sms_config.sms_config_from_payload(payload, _load_webui_config)


def _hero_price_lookup(payload: dict[str, Any]) -> dict[str, Any]:
    return task_sms_lookup.hero_price_lookup(payload, sms_config_from_payload_fn=_sms_config_from_payload)


def _hero_country_lookup(payload: dict[str, Any]) -> dict[str, Any]:
    return task_sms_lookup.hero_country_lookup(payload, sms_config_from_payload_fn=_sms_config_from_payload)


def _sms_wait_progress_message(info: dict[str, Any]) -> str:
    return phone_direct_diagnostics.sms_wait_progress_message(
        info,
        default_resend_after_seconds=HERO_SMS_RESEND_AFTER_SECONDS,
    )


def _phone_signup_probe_is_login_password(probe: dict[str, Any]) -> bool:
    return phone_direct_diagnostics.phone_signup_probe_is_login_password(probe)


def _phone_direct_once_deps() -> PhoneDirectOnceDeps:
    return PhoneDirectOnceDeps(
        about_you_shape_log_summary_fn=_about_you_shape_log_summary,
        accounts_error_code_fn=_accounts_error_code,
        acquire_hero_sms_phone_fn=acquire_hero_sms_phone,
        attach_phone_direct_exception_context_fn=attach_phone_direct_exception_context,
        bool_from_payload_fn=_bool_from_payload,
        build_phone_account_exchange_config_fn=build_phone_account_exchange_config,
        build_phone_direct_signup_flow_fn=build_phone_direct_signup_flow,
        continue_phone_signup_after_sms_fn=continue_phone_signup_after_sms,
        cpa_oauth_lock=_CPA_OAUTH_LOCK,
        environment_profile_context_fn=environment_profile_context,
        exchange_registered_account_tokens_fn=_exchange_registered_account_tokens,
        finish_phone_token_result_fn=finish_phone_token_result,
        import_result_to_codex2api_fn=import_result_to_codex2api,
        load_continue_page_fn=_load_continue_page,
        mail_config_dict_from_payload_fn=_mail_config_dict_from_payload,
        phone_signup_continuation_deps_cls=PhoneSignupContinuationDeps,
        phone_signup_entry_error_fn=_phone_signup_entry_error,
        phone_signup_probe_is_login_password_fn=_phone_signup_probe_is_login_password,
        platform_registrar_cls=PlatformRegistrar,
        poll_hero_sms_code_fn=poll_hero_sms_code,
        prepare_environment_profile_from_payload_fn=prepare_environment_profile_from_payload,
        probe_phone_signup_password_page_fn=_probe_phone_signup_password_page,
        random_birthdate_fn=_random_birthdate,
        random_name_fn=_random_name,
        random_password_fn=_random_password,
        record_phone_activation_attempt_fn=record_phone_activation_attempt,
        resolve_oauth_callback_fn=_resolve_oauth_callback,
        safe_register_failure_summary_fn=_safe_register_failure_summary,
        save_partial_hero_phone_bind_result_fn=_save_partial_hero_phone_bind_result,
        save_result_fn=save_result,
        set_hero_sms_status_fn=set_hero_sms_status,
        sms_config_from_payload_fn=_sms_config_from_payload,
        sms_retry_count_from_payload_fn=_sms_retry_count_from_payload,
        sms_retry_exhausted_message_fn=_sms_retry_exhausted_message,
        sms_wait_progress_message_fn=_sms_wait_progress_message,
        submit_about_you_form_fn=_submit_about_you_form,
        summarize_environment_profile_fn=summarize_environment_profile,
    )


def _phone_direct_once(
    payload: dict[str, Any],
    *,
    env_profile: Any = None,
    effective_proxy: str = "",
    manage_environment: bool = True,
    log_environment: bool = True,
    worker_index: int = 0,
    worker_total: int = 0,
) -> dict[str, Any]:
    return run_phone_direct_once(
        payload,
        env_profile=env_profile,
        effective_proxy=effective_proxy,
        manage_environment=manage_environment,
        log_environment=log_environment,
        worker_index=worker_index,
        worker_total=worker_total,
        deps=_phone_direct_once_deps(),
    )


def _hero_phone_bind(payload: dict[str, Any]) -> dict[str, Any]:
    return _phone_direct_once(payload)


def _phone_direct(payload: dict[str, Any]) -> dict[str, Any]:
    return run_phone_direct_batch(
        payload,
        PhoneDirectBatchDeps(
            phone_direct_once=_phone_direct_once,
            bool_from_payload=_bool_from_payload,
            prepare_environment_profile_from_payload=prepare_environment_profile_from_payload,
            summarize_environment_profile=summarize_environment_profile,
            environment_profile_context=environment_profile_context,
            sms_config_from_payload=_sms_config_from_payload,
            sms_retry_count_from_payload=_sms_retry_count_from_payload,
            unwrap_sms_retry_error=_unwrap_sms_retry_error,
            is_sms_inventory_error=_is_sms_inventory_error,
            sms_retry_exhausted_message=_sms_retry_exhausted_message,
            set_log_context=set_log_context,
            reset_log_context=reset_log_context,
            wait=wait,
            job_cancelled_error=JobCancelledError,
            heartbeat_seconds=PHONE_DIRECT_FUTURE_WAIT_HEARTBEAT_SECONDS,
        ),
    )
