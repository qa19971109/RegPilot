from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import MailConfig, RegisterConfig, ensure_dirs, parse_bool
from .register_core import run_placeholder, save_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="regpilot", description="RegPilot account registration flow runner")
    subparsers = parser.add_subparsers(dest="command")

    register_parser = subparsers.add_parser("register", help="run the mailbox + OTP registration flow")
    register_parser.add_argument("--config", default="", help="path to JSON config file")
    register_parser.add_argument("--proxy", default="", help="http/https/socks5 proxy")
    register_parser.add_argument("--env-random-enabled", action="store_true", default=None, help="enable random runtime environment profile")
    register_parser.add_argument("--env-proxy-pool", default="", help="proxy pool text, separated by newline/comma/semicolon")
    register_parser.add_argument("--env-ua-pool", default="", help="user-agent pool text")
    register_parser.add_argument("--env-accept-language-pool", default="", help="accept-language pool text")
    register_parser.add_argument("--env-timezone-pool", default="", help="timezone pool text")
    register_parser.add_argument("--env-viewport-pool", default="", help="viewport pool text, e.g. 1920x1080")
    register_parser.add_argument("--total", type=int, default=None)
    register_parser.add_argument("--threads", type=int, default=None)
    register_parser.add_argument("--codex2api-url", default="", help="Codex2API panel URL")
    register_parser.add_argument("--codex2api-admin-key", default="", help="Codex2API admin secret")
    register_parser.add_argument("--codex2api-proxy-url", default="", help="proxy_url assigned to imported Codex2API accounts")
    register_parser.add_argument("--codex2api-auto-import", action="store_true", default=None)
    register_parser.add_argument("--hero-sms-api-key", default="")
    register_parser.add_argument("--hero-sms-base-url", default="")
    register_parser.add_argument("--hero-sms-country", default="")
    register_parser.add_argument("--hero-sms-service", default="")
    register_parser.add_argument("--hero-sms-min-price", type=float, default=None)
    register_parser.add_argument("--hero-sms-max-price", type=float, default=None)
    register_parser.add_argument("--hero-sms-wait-timeout", type=int, default=None)
    register_parser.add_argument("--hero-sms-wait-interval", type=int, default=None)
    register_parser.add_argument("--hero-sms-auto-retry", dest="hero_sms_auto_retry", action="store_true", default=None)
    register_parser.add_argument("--no-hero-sms-auto-retry", dest="hero_sms_auto_retry", action="store_false")
    register_parser.add_argument("--hero-sms-retry-count", type=int, default=None)
    register_parser.add_argument("--reuse-phone-number", default="")
    register_parser.add_argument("--reuse-activation-id", default="")

    return parser


def _config_from_cli_args(args: argparse.Namespace) -> RegisterConfig:
    return RegisterConfig(
        proxy=args.proxy,
        env_random_enabled=parse_bool(getattr(args, "env_random_enabled", None), default=False, key="env_random_enabled"),
        env_proxy_pool=str(getattr(args, "env_proxy_pool", "") or ""),
        env_ua_pool=str(getattr(args, "env_ua_pool", "") or ""),
        env_accept_language_pool=str(getattr(args, "env_accept_language_pool", "") or ""),
        env_timezone_pool=str(getattr(args, "env_timezone_pool", "") or ""),
        env_viewport_pool=str(getattr(args, "env_viewport_pool", "") or ""),
        total=max(1, int(getattr(args, "total", None) or 1)),
        threads=max(1, int(getattr(args, "threads", None) or 1)),
        default_password=str(getattr(args, "default_password", "") or ""),
        codex2api_url=str(getattr(args, "codex2api_url", "") or ""),
        codex2api_admin_key=str(getattr(args, "codex2api_admin_key", "") or ""),
        codex2api_proxy_url=str(getattr(args, "codex2api_proxy_url", "") or ""),
        codex2api_auto_import=parse_bool(getattr(args, "codex2api_auto_import", None), default=False, key="codex2api_auto_import"),
        hero_sms_api_key=str(getattr(args, "hero_sms_api_key", "") or ""),
        hero_sms_base_url=str(getattr(args, "hero_sms_base_url", "") or RegisterConfig.hero_sms_base_url),
        hero_sms_country=str(getattr(args, "hero_sms_country", "") or RegisterConfig.hero_sms_country),
        hero_sms_service=str(getattr(args, "hero_sms_service", "") or RegisterConfig.hero_sms_service),
        hero_sms_min_price=float(getattr(args, "hero_sms_min_price", None) or 0.0),
        hero_sms_max_price=float(getattr(args, "hero_sms_max_price", None) or 0.0),
        hero_sms_wait_timeout=int(getattr(args, "hero_sms_wait_timeout", None) or RegisterConfig.hero_sms_wait_timeout),
        hero_sms_wait_interval=int(getattr(args, "hero_sms_wait_interval", None) or RegisterConfig.hero_sms_wait_interval),
        hero_sms_auto_retry=parse_bool(getattr(args, "hero_sms_auto_retry", None), default=False, key="hero_sms_auto_retry"),
        hero_sms_retry_count=max(1, int(getattr(args, "hero_sms_retry_count", None) or RegisterConfig.hero_sms_retry_count)),
        reuse_phone_number=str(getattr(args, "reuse_phone_number", "") or ""),
        reuse_activation_id=str(getattr(args, "reuse_activation_id", "") or ""),
    )


def _config_from_json_data(data: dict[str, object], base: RegisterConfig) -> RegisterConfig:
    mail_data = data.get("mail") or {}
    if not isinstance(mail_data, dict):
        mail_data = {}
    return RegisterConfig(
        proxy=str(data.get("proxy") or base.proxy),
        env_random_enabled=parse_bool(data.get("env_random_enabled", base.env_random_enabled), default=base.env_random_enabled, key="env_random_enabled"),
        env_proxy_pool=str(data.get("env_proxy_pool") or base.env_proxy_pool),
        env_ua_pool=str(data.get("env_ua_pool") or base.env_ua_pool),
        env_accept_language_pool=str(data.get("env_accept_language_pool") or base.env_accept_language_pool),
        env_timezone_pool=str(data.get("env_timezone_pool") or base.env_timezone_pool),
        env_viewport_pool=str(data.get("env_viewport_pool") or base.env_viewport_pool),
        total=max(1, int(data.get("total") or base.total)),
        threads=max(1, int(data.get("threads") or base.threads)),
        codex2api_url=str(data.get("codex2api_url") or base.codex2api_url),
        codex2api_admin_key=str(data.get("codex2api_admin_key") or base.codex2api_admin_key),
        codex2api_proxy_url=str(data.get("codex2api_proxy_url") or base.codex2api_proxy_url),
        codex2api_auto_import=parse_bool(data.get("codex2api_auto_import", base.codex2api_auto_import), default=base.codex2api_auto_import, key="codex2api_auto_import"),
        hero_sms_api_key=str(data.get("hero_sms_api_key") or base.hero_sms_api_key),
        hero_sms_base_url=str(data.get("hero_sms_base_url") or base.hero_sms_base_url),
        hero_sms_country=str(data.get("hero_sms_country") or base.hero_sms_country),
        hero_sms_service=str(data.get("hero_sms_service") or base.hero_sms_service),
        hero_sms_min_price=float(data.get("hero_sms_min_price") or base.hero_sms_min_price),
        hero_sms_max_price=float(data.get("hero_sms_max_price") or base.hero_sms_max_price),
        hero_sms_wait_timeout=int(data.get("sms_wait_timeout") or data.get("hero_sms_wait_timeout") or base.hero_sms_wait_timeout),
        hero_sms_wait_interval=int(data.get("sms_wait_interval") or data.get("hero_sms_wait_interval") or base.hero_sms_wait_interval),
        hero_sms_auto_retry=parse_bool(data.get("sms_auto_retry", data.get("hero_sms_auto_retry", base.hero_sms_auto_retry)), default=base.hero_sms_auto_retry, key="hero_sms_auto_retry"),
        hero_sms_retry_count=max(1, int(data.get("sms_retry_count") or data.get("hero_sms_retry_count") or base.hero_sms_retry_count)),
        reuse_phone_number=str(data.get("reuse_phone_number") or base.reuse_phone_number),
        reuse_activation_id=str(data.get("reuse_activation_id") or base.reuse_activation_id),
        mail=MailConfig(
            request_timeout=int(mail_data.get("request_timeout") or 30),
            wait_timeout=int(mail_data.get("wait_timeout") or 30),
            wait_interval=int(mail_data.get("wait_interval") or 2),
            providers=list(mail_data.get("providers") or []),
            proxy=str(mail_data.get("proxy") or ""),
        ),
    )


def _apply_cli_overrides(cfg: RegisterConfig, args: argparse.Namespace) -> RegisterConfig:
    if args.proxy:
        cfg.proxy = args.proxy
    if getattr(args, "env_random_enabled", None) is not None:
        cfg.env_random_enabled = parse_bool(args.env_random_enabled, default=cfg.env_random_enabled, key="env_random_enabled")
    if getattr(args, "env_proxy_pool", ""):
        cfg.env_proxy_pool = str(args.env_proxy_pool)
    if getattr(args, "env_ua_pool", ""):
        cfg.env_ua_pool = str(args.env_ua_pool)
    if getattr(args, "env_accept_language_pool", ""):
        cfg.env_accept_language_pool = str(args.env_accept_language_pool)
    if getattr(args, "env_timezone_pool", ""):
        cfg.env_timezone_pool = str(args.env_timezone_pool)
    if getattr(args, "env_viewport_pool", ""):
        cfg.env_viewport_pool = str(args.env_viewport_pool)
    if getattr(args, "total", None) is not None:
        cfg.total = max(1, int(args.total))
    if getattr(args, "threads", None) is not None:
        cfg.threads = max(1, int(args.threads))
    if getattr(args, "codex2api_url", ""):
        cfg.codex2api_url = str(args.codex2api_url)
    if getattr(args, "codex2api_admin_key", ""):
        cfg.codex2api_admin_key = str(args.codex2api_admin_key)
    if getattr(args, "codex2api_proxy_url", ""):
        cfg.codex2api_proxy_url = str(args.codex2api_proxy_url)
    if getattr(args, "codex2api_auto_import", None) is not None:
        cfg.codex2api_auto_import = parse_bool(args.codex2api_auto_import, default=cfg.codex2api_auto_import, key="codex2api_auto_import")
    if getattr(args, "hero_sms_api_key", ""):
        cfg.hero_sms_api_key = str(args.hero_sms_api_key)
    if getattr(args, "hero_sms_base_url", ""):
        cfg.hero_sms_base_url = str(args.hero_sms_base_url)
    if getattr(args, "hero_sms_country", ""):
        cfg.hero_sms_country = str(args.hero_sms_country)
    if getattr(args, "hero_sms_service", ""):
        cfg.hero_sms_service = str(args.hero_sms_service)
    if getattr(args, "hero_sms_min_price", None) is not None:
        cfg.hero_sms_min_price = float(args.hero_sms_min_price)
    if getattr(args, "hero_sms_max_price", None) is not None:
        cfg.hero_sms_max_price = float(args.hero_sms_max_price)
    if getattr(args, "hero_sms_wait_timeout", None) is not None:
        cfg.hero_sms_wait_timeout = int(args.hero_sms_wait_timeout)
    if getattr(args, "hero_sms_wait_interval", None) is not None:
        cfg.hero_sms_wait_interval = int(args.hero_sms_wait_interval)
    if getattr(args, "hero_sms_auto_retry", None) is not None:
        cfg.hero_sms_auto_retry = parse_bool(args.hero_sms_auto_retry, default=cfg.hero_sms_auto_retry, key="hero_sms_auto_retry")
    if getattr(args, "hero_sms_retry_count", None) is not None:
        cfg.hero_sms_retry_count = max(1, int(args.hero_sms_retry_count))
    if getattr(args, "reuse_phone_number", ""):
        cfg.reuse_phone_number = str(args.reuse_phone_number)
    if getattr(args, "reuse_activation_id", ""):
        cfg.reuse_activation_id = str(args.reuse_activation_id)
    return cfg


def load_config(args: argparse.Namespace) -> RegisterConfig:
    cfg = _config_from_cli_args(args)
    config_path = str(getattr(args, "config", "") or "").strip()
    if not config_path:
        return cfg
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    return _apply_cli_overrides(_config_from_json_data(data, cfg), args)


def run_register_command(args: argparse.Namespace) -> None:
    cfg = load_config(args)
    result = run_placeholder(cfg)
    path = save_result(result)
    summary = {
        "ok": bool(result.ok),
        "email": str(result.email or ""),
        "error": str(result.error or ""),
        "callback_url": str(result.callback_url or ""),
        "has_access_token": bool(result.access_token),
        "saved_result": str(path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if args.command in (None, "register"):
        run_register_command(args)
        return
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
