from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
import time
from typing import Any, Callable

from .phone_direct_flow import (
    attach_phone_attempt_context,
    merge_phone_attempt_context_from_error,
    merge_phone_attempt_context_from_item,
    phone_direct_batch_result,
    phone_direct_error_item,
    phone_direct_single_result,
)


@dataclass(frozen=True)
class PhoneDirectBatchDeps:
    phone_direct_once: Callable[..., dict[str, Any]]
    bool_from_payload: Callable[..., bool]
    prepare_environment_profile_from_payload: Callable[..., Any]
    summarize_environment_profile: Callable[[Any], str]
    environment_profile_context: Callable[[Any], Any]
    sms_config_from_payload: Callable[[dict[str, Any]], Any]
    sms_retry_count_from_payload: Callable[[dict[str, Any], bool], int]
    unwrap_sms_retry_error: Callable[[str], str]
    is_sms_inventory_error: Callable[[str], bool]
    sms_retry_exhausted_message: Callable[[str, int, str], str]
    set_log_context: Callable[..., Any]
    reset_log_context: Callable[[Any], None]
    wait: Callable[..., Any]
    job_cancelled_error: type[BaseException]
    heartbeat_seconds: float = 15.0


@dataclass(frozen=True)
class PhoneDirectBatchRuntime:
    requested_total: int
    worker_count: int
    rotate_environment: bool
    env_profile: Any
    effective_proxy: str


def _build_batch_runtime(payload: dict[str, Any], deps: PhoneDirectBatchDeps) -> PhoneDirectBatchRuntime:
    requested_total = max(1, int(payload.get("total") or 1))
    requested_threads = max(1, int(payload.get("threads") or 1))
    worker_count = max(1, min(requested_total, requested_threads))
    rotate_environment = deps.bool_from_payload(payload, "env_random_enabled")
    env_profile = None
    if not rotate_environment:
        env_profile = deps.prepare_environment_profile_from_payload(payload, fallback_proxy=str(payload.get("proxy") or ""))
    effective_proxy = "" if env_profile is None else str(env_profile.proxy or payload.get("proxy") or "").strip()
    return PhoneDirectBatchRuntime(requested_total, worker_count, rotate_environment, env_profile, effective_proxy)


def _single_worker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    worker_payload = dict(payload or {})
    worker_payload["total"] = 1
    worker_payload["threads"] = 1
    return worker_payload


def _raise_phone_attempt_error(
    message: str,
    attempted_phones: list[str],
    attempted_phone_prices: list[str],
    cause: Exception,
) -> None:
    error = RuntimeError(message)
    try:
        attach_phone_attempt_context(error, attempted_phones, attempted_phone_prices, cause)
    except Exception:
        pass
    raise error


def _run_rotating_environment_worker(
    index: int,
    worker_payload: dict[str, Any],
    deps: PhoneDirectBatchDeps,
    requested_total: int,
) -> dict[str, Any]:
    hero_sms = deps.sms_config_from_payload(worker_payload)
    attempt_limit = deps.sms_retry_count_from_payload(worker_payload, hero_sms.auto_retry)
    attempted_phones: list[str] = []
    attempted_phone_prices: list[str] = []
    last_error = ""
    for attempt_index in range(1, attempt_limit + 1):
        attempt_payload = dict(worker_payload)
        attempt_payload["sms_retry_count"] = 1
        attempt_payload["hero_sms_retry_count"] = 1
        attempt_env_profile = deps.prepare_environment_profile_from_payload(
            attempt_payload,
            fallback_proxy=str(attempt_payload.get("proxy") or ""),
        )
        attempt_proxy = str(attempt_env_profile.proxy or attempt_payload.get("proxy") or "").strip()
        print(
            f"阶段：并发单元 {index}/{requested_total} 第 {attempt_index}/{attempt_limit} 次环境 "
            f"{deps.summarize_environment_profile(attempt_env_profile)}"
        )
        try:
            item = deps.phone_direct_once(
                attempt_payload,
                env_profile=attempt_env_profile,
                effective_proxy=attempt_proxy,
                manage_environment=True,
                log_environment=False,
                worker_index=index,
                worker_total=requested_total,
            )
            item = merge_phone_attempt_context_from_item(item, attempted_phones, attempted_phone_prices)
            print(f"阶段：手机直注并发单元 {index}/{requested_total} 已完成")
            return item
        except Exception as exc:
            last_error = deps.unwrap_sms_retry_error(str(exc))
            merge_phone_attempt_context_from_error(exc, attempted_phones, attempted_phone_prices)
            if not hero_sms.auto_retry:
                _raise_phone_attempt_error(last_error or str(exc) or "phone_direct_failed", attempted_phones, attempted_phone_prices, exc)
            if deps.is_sms_inventory_error(last_error) or attempt_index >= attempt_limit:
                retry_message = deps.sms_retry_exhausted_message(hero_sms.provider, attempt_limit, last_error)
                _raise_phone_attempt_error(retry_message, attempted_phones, attempted_phone_prices, exc)
            print(
                f"阶段：并发单元 {index}/{requested_total} 当前环境/号码失败，"
                f"将重新抽取环境并换号重试（{attempt_index}/{attempt_limit}），错误={last_error}"
            )
    raise RuntimeError(last_error or "phone_direct_failed")


def _run_fixed_environment_worker(
    index: int,
    worker_payload: dict[str, Any],
    deps: PhoneDirectBatchDeps,
    runtime: PhoneDirectBatchRuntime,
) -> dict[str, Any]:
    item = deps.phone_direct_once(
        worker_payload,
        env_profile=runtime.env_profile,
        effective_proxy=runtime.effective_proxy,
        manage_environment=False,
        log_environment=False,
        worker_index=index,
        worker_total=runtime.requested_total,
    )
    print(f"阶段：手机直注并发单元 {index}/{runtime.requested_total} 已完成")
    return item


def _run_phone_direct_worker(
    index: int,
    payload: dict[str, Any],
    deps: PhoneDirectBatchDeps,
    runtime: PhoneDirectBatchRuntime,
) -> dict[str, Any]:
    worker_log_tokens = deps.set_log_context(worker_id=f"{index}/{runtime.requested_total}")
    try:
        worker_payload = _single_worker_payload(payload)
        print(f"阶段：手机直注并发单元 {index}/{runtime.requested_total} 已启动")
        if runtime.rotate_environment:
            return _run_rotating_environment_worker(index, worker_payload, deps, runtime.requested_total)
        return _run_fixed_environment_worker(index, worker_payload, deps, runtime)
    finally:
        deps.reset_log_context(worker_log_tokens)


def _record_phone_direct_item(
    item: dict[str, Any],
    index: int,
    successes: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    if item.get("ok"):
        successes.append(item)
    else:
        failures.append({"ok": False, "worker": index, "error": str(item.get("error") or "phone_direct_failed"), **item})


def _collect_phone_direct_batch_results(
    futures: dict[Any, int],
    deps: PhoneDirectBatchDeps,
    requested_total: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    pending = set(futures)
    last_wait_heartbeat = time.monotonic()
    while pending:
        done, pending = deps.wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
        if not done:
            now = time.monotonic()
            if now - last_wait_heartbeat >= deps.heartbeat_seconds:
                print(f"阶段：手机直注批量等待并发单元完成，剩余 {len(pending)} 个")
                last_wait_heartbeat = now
            continue
        for future in done:
            index = futures[future]
            try:
                _record_phone_direct_item(future.result(), index, successes, failures)
            except deps.job_cancelled_error:
                raise
            except Exception as exc:
                failures.append(phone_direct_error_item(index, exc))
                print(f"阶段：手机直注并发单元 {index}/{requested_total} 失败：{exc}")
    return successes, failures


def run_phone_direct_batch(payload: dict[str, Any], deps: PhoneDirectBatchDeps) -> dict[str, Any]:
    runtime = _build_batch_runtime(payload, deps)
    requested_total = runtime.requested_total
    worker_count = runtime.worker_count
    if requested_total == 1:
        item = deps.phone_direct_once(payload)
        return phone_direct_single_result(item)

    print(f"阶段：手机直注批量启动 目标={requested_total} 线程={worker_count}")
    if runtime.env_profile is None:
        print("阶段：环境模块 随机环境已启用：每个并发单元/换号都会重新抽取 UA/语言/时区/视口/代理")
    else:
        print(f"阶段：环境模块 {deps.summarize_environment_profile(runtime.env_profile)}")

    executor_context = nullcontext() if runtime.rotate_environment else deps.environment_profile_context(runtime.env_profile)
    with executor_context:
        executor = ThreadPoolExecutor(max_workers=worker_count)
        try:
            futures = {
                executor.submit(_run_phone_direct_worker, index, payload, deps, runtime): index
                for index in range(1, requested_total + 1)
            }
            successes, failures = _collect_phone_direct_batch_results(futures, deps, requested_total)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    result = phone_direct_batch_result(
        requested_total=requested_total,
        worker_count=worker_count,
        successes=successes,
        failures=failures,
    )
    print(f"阶段：手机直注批量完成 成功={len(successes)}/{requested_total} 失败={len(failures)}")
    return result
