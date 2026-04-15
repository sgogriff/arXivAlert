"""Shared paper model and run-state helpers."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("paperpress.fetcher")

_INITIAL_SEARCH_WINDOW_LOOKBACK = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "fortnightly": timedelta(days=14),
    "monthly": timedelta(days=30),
}


@dataclass
class Paper:
    paper_id: str
    source: str
    record_kind: str
    version_stage: str
    doi: str | None
    openalex_id: str | None
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    primary_category: str
    published: datetime
    pdf_url: str
    abs_url: str


def load_last_run(state_file: str = "last_run.json") -> datetime | None:
    """Load the last run timestamp. Returns None if missing or corrupt."""
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return datetime.fromisoformat(data["last_run"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Could not parse %s: %s — treating as first run", state_file, e)
        return None


def save_last_run(timestamp: datetime, state_file: str = "last_run.json") -> None:
    """Save the current run timestamp."""
    Path(state_file).write_text(
        json.dumps({"last_run": timestamp.isoformat()}, indent=2) + "\n"
    )


def calculate_date_range(
    initial_search_window: str, last_run: datetime | None
) -> tuple[datetime, datetime]:
    """Determine fetch date range based on initial window and last run.

    If last_run exists, use it as start. Otherwise fall back to
    initial-search-window lookback (1d / 7d / 14d / 30d).
    """
    now = datetime.now(timezone.utc)
    if last_run is not None:
        start = last_run
    else:
        lookback = _INITIAL_SEARCH_WINDOW_LOOKBACK.get(
            initial_search_window, timedelta(days=1)
        )
        start = now - lookback
    return (start, now)
