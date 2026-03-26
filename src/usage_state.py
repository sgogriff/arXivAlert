"""Persistent state for accumulated token usage and digest windowing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.scorer import TokenUsage


STATE_FILE = "token_usage.json"


@dataclass
class UsageState:
    window_start: datetime | None = None
    usage_by_model: dict[str, TokenUsage] = field(default_factory=dict)
    total_scanned: int = 0
    overview_key: str = ""
    overview_text: str = ""

    def total_usage(self) -> TokenUsage:
        total = TokenUsage()
        for usage in self.usage_by_model.values():
            total.add(usage)
        return total


def load_usage_state(path: str = STATE_FILE) -> UsageState:
    p = Path(path)
    if not p.exists():
        return UsageState()
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return UsageState()

    window_start = None
    ws = raw.get("window_start")
    if ws:
        try:
            window_start = datetime.fromisoformat(ws)
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=timezone.utc)
        except ValueError:
            window_start = None

    usage_by_model_raw = raw.get("usage_by_model", {}) or {}
    usage_by_model = {
        str(model): TokenUsage.from_dict(u or {})
        for model, u in usage_by_model_raw.items()
    }
    # Backward-compat (older single-model format)
    if not usage_by_model and raw.get("usage") and raw.get("model"):
        usage_by_model = {str(raw.get("model")): TokenUsage.from_dict(raw.get("usage") or {})}

    return UsageState(
        window_start=window_start,
        usage_by_model=usage_by_model,
        total_scanned=int(raw.get("total_scanned", 0) or 0),
        overview_key=str(raw.get("overview_key", "") or ""),
        overview_text=str(raw.get("overview_text", "") or ""),
    )


def save_usage_state(state: UsageState, path: str = STATE_FILE) -> None:
    payload = {
        "window_start": state.window_start.isoformat() if state.window_start else "",
        "usage_by_model": {m: u.to_dict() for m, u in state.usage_by_model.items()},
        "total_scanned": state.total_scanned,
        "overview_key": state.overview_key,
        "overview_text": state.overview_text,
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def clear_usage_state(path: str = STATE_FILE) -> None:
    save_usage_state(UsageState(), path=path)
