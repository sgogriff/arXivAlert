"""arXiv API interaction — fetches papers and manages date-range state."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import arxiv

logger = logging.getLogger("arXivAlert.fetcher")

_SCHEDULE_LOOKBACK = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "fortnightly": timedelta(days=14),
    "monthly": timedelta(days=30),
}


@dataclass
class Paper:
    arxiv_id: str
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
    schedule: str, last_run: datetime | None
) -> tuple[datetime, datetime]:
    """Determine fetch date range based on schedule and last run.

    If last_run exists, use it as start. Otherwise fall back to
    schedule-based lookback (1d / 7d / 14d).
    """
    now = datetime.now(timezone.utc)
    if last_run is not None:
        start = last_run
    else:
        lookback = _SCHEDULE_LOOKBACK.get(schedule, timedelta(days=1))
        start = now - lookback
    return (start, now)


def _extract_arxiv_id(entry_id: str) -> str:
    """Extract clean arXiv ID from full entry URL.

    e.g. 'http://arxiv.org/abs/2401.12345v1' -> '2401.12345'
    """
    match = re.search(r"(\d{4}\.\d{4,5})", entry_id)
    return match.group(1) if match else entry_id


def fetch_papers(
    categories: list[str],
    max_results: int,
    date_range: tuple[datetime, datetime],
) -> list[Paper]:
    """Fetch papers from arXiv for the given categories within date_range.

    Queries each category, deduplicates by arXiv ID, and filters by date.
    """
    start_date, end_date = date_range
    seen: dict[str, Paper] = {}
    # The arxiv library uses Client.page_size as the per-request "max_results" query parameter.
    # Use the configured max_results so we don't silently cap to 100 in the first request.
    page_size = max(1, min(int(max_results), 300))
    client = arxiv.Client(page_size=page_size, delay_seconds=3.0, num_retries=3)

    for category in categories:
        logger.info("Fetching category: %s", category)
        search = arxiv.Search(
            query=f"cat:{category}",
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        for result in client.results(search):
            # Make published timezone-aware if it isn't
            pub = result.published
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)

            # Early termination — results are sorted newest first
            if pub < start_date:
                break

            if pub > end_date:
                continue

            aid = _extract_arxiv_id(result.entry_id)
            if aid in seen:
                continue

            seen[aid] = Paper(
                arxiv_id=aid,
                title=result.title.replace("\n", " ").strip(),
                authors=[a.name for a in result.authors],
                abstract=result.summary.replace("\n", " ").strip(),
                categories=result.categories,
                primary_category=result.primary_category,
                published=pub,
                pdf_url=result.pdf_url,
                abs_url=result.entry_id,
            )

    papers = sorted(seen.values(), key=lambda p: p.published, reverse=True)
    logger.info("Fetched %d unique papers across %d categories", len(papers), len(categories))
    return papers
