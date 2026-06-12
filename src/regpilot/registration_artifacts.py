from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .json_store import write_json_atomic, write_text_atomic


def _about_you_artifact_paths(data_dir: Path, prefix: str) -> tuple[Path, Path]:
    debug_dir = data_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{prefix}_{stamp}"
    return debug_dir / f"{stem}.json", debug_dir / f"{stem}.html"


def save_about_you_failure_artifacts(
    data_dir: Path,
    *,
    state: dict[str, Any] | None,
    create_info: dict[str, Any] | None,
    page_snapshot: dict[str, Any] | None,
    page_context: str = "",
) -> dict[str, str]:
    json_path, html_path = _about_you_artifact_paths(data_dir, "about_you_failure")

    state_raw = state.get("raw") if isinstance(state, dict) else {}
    state_url = str((state or {}).get("url") or "").strip()
    html_text = str((page_snapshot or {}).get("text") or "").strip()
    if not html_text:
        html_text = str((state_raw or {}).get("text") or "").strip()
    if not html_text:
        html_text = str((create_info or {}).get("text") or "").strip()

    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "state_url": state_url,
        "page_context": page_context[:4000],
        "state": state or {},
        "create_info": create_info or {},
        "page_snapshot": page_snapshot or {},
    }
    write_json_atomic(json_path, payload)
    if html_text:
        write_text_atomic(html_path, html_text)
    return {
        "json_path": str(json_path),
        "html_path": str(html_path) if html_text else "",
    }


def save_about_you_presubmit_artifacts(
    data_dir: Path,
    *,
    state: dict[str, Any] | None,
    page_snapshot: dict[str, Any] | None,
    page_context: str = "",
) -> dict[str, str]:
    json_path, html_path = _about_you_artifact_paths(data_dir, "about_you_presubmit")

    state_raw = state.get("raw") if isinstance(state, dict) else {}
    state_url = str((state or {}).get("url") or "").strip()
    html_text = str((page_snapshot or {}).get("text") or "").strip()
    if not html_text:
        html_text = str((state_raw or {}).get("text") or "").strip()

    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "state_url": state_url,
        "page_context": page_context[:4000],
        "state": state or {},
        "page_snapshot": page_snapshot or {},
    }
    write_json_atomic(json_path, payload)
    if html_text:
        write_text_atomic(html_path, html_text)
    return {
        "json_path": str(json_path),
        "html_path": str(html_path) if html_text else "",
    }
