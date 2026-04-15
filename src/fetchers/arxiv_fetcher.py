"""arXiv fetcher."""

from __future__ import annotations

import logging
import re
import time
from random import random

import arxiv

from src.fetcher import Paper
from src.fetchers._base import DateRange, clean_text

logger = logging.getLogger("paperpress.fetchers.arxiv")


def _extract_arxiv_id(entry_id: str) -> str:
    match = re.search(r"(\d{4}\.\d{4,5})", entry_id)
    return match.group(1) if match else entry_id


class ArxivFetcher:
    def __init__(self, categories: list[str]) -> None:
        self.categories = categories

    def _build_client(self, page_size: int) -> arxiv.Client:
        client = arxiv.Client(page_size=page_size, delay_seconds=5.0, num_retries=0)
        try:
            client._session.headers.update(  # type: ignore[attr-defined]
                {"User-Agent": "PaperPress/1.0 (+https://github.com/sgogriff/arXivAlert)"}
            )
        except Exception:
            logger.debug("Could not update arXiv client user-agent header")
        return client

    def _category_results(
        self,
        category: str,
        max_results: int,
        page_size: int,
    ) -> list[object]:
        search = arxiv.Search(
            query=f"cat:{category}",
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                client = self._build_client(page_size)
                return list(client.results(search))
            except arxiv.HTTPError as e:
                if getattr(e, "status", None) != 429 or attempt == max_attempts - 1:
                    raise
                wait_seconds = (20 * (2 ** attempt)) + random()
                logger.warning(
                    "arXiv rate limit for category %s (attempt %d/%d). Retrying in %.1fs",
                    category,
                    attempt + 1,
                    max_attempts,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
        return []

    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]:
        start_date, end_date = date_range
        seen: dict[str, Paper] = {}
        page_size = max(1, min(int(max_results), 300))

        for category in self.categories:
            logger.info("Fetching from arXiv category: %s", category)
            for result in self._category_results(category, max_results, page_size):
                published = result.published
                if published.tzinfo is None:
                    published = published.replace(tzinfo=start_date.tzinfo)
                if published < start_date:
                    break
                if published > end_date:
                    continue

                paper_id = _extract_arxiv_id(result.entry_id)
                if paper_id in seen:
                    continue
                seen[paper_id] = Paper(
                    paper_id=paper_id,
                    source="arxiv",
                    record_kind="preprint",
                    version_stage="preprint",
                    doi=getattr(result, "doi", None),
                    openalex_id=None,
                    title=clean_text(result.title),
                    authors=[clean_text(author.name) for author in result.authors],
                    abstract=clean_text(result.summary),
                    categories=[clean_text(cat) for cat in result.categories],
                    primary_category=clean_text(result.primary_category),
                    published=published,
                    pdf_url=result.pdf_url,
                    abs_url=result.entry_id,
                )

        papers = sorted(seen.values(), key=lambda p: p.published, reverse=True)
        logger.info("Fetched %d unique arXiv papers", len(papers))
        return papers
