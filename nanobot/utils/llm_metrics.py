"""Helpers for writing per-call LLM metrics to a JSONL log file."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_METRICS_PATH_ENV = "NANOBOT_LLM_METRICS_FILE"
_DEFAULT_METRICS_PATH = Path.home() / ".nanobot" / "logs" / "llm_metrics.jsonl"


def _metrics_path() -> Path:
    raw = os.environ.get(_METRICS_PATH_ENV, "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_METRICS_PATH


def get_llm_metrics_path() -> Path:
    """Return the current LLM metrics JSONL path."""
    return _metrics_path()


def log_llm_metrics(record: dict[str, Any]) -> None:
    """Append one metrics record as a JSON line.

    Best-effort only: this function must never break request flow.
    """
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **record,
        }
        path = _metrics_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        return
