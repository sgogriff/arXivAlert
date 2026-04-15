"""bioRxiv and medRxiv fetcher."""

from __future__ import annotations

import logging

from src.fetcher import Paper
from src.fetchers._base import DateRange, build_client, clean_text, normalize_doi, parse_datetime

logger = logging.getLogger("paperpress.fetchers.biorxiv")


_SOURCE_DOMAINS = {
    "biorxiv": "https://www.biorxiv.org",
    "medrxiv": "https://www.medrxiv.org",
}


class BiorxivFetcher:
    def __init__(self, source: str, api_base: str = "https://api.medrxiv.org/details") -> None:
        self.source = source
        self.api_base = api_base.rstrip("/")
        self.domain = _SOURCE_DOMAINS[source]

    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]:
        start_date, end_date = date_range
        start_str = start_date.date().isoformat()
        end_str = end_date.date().isoformat()
        papers: list[Paper] = []
        cursor = 0

        with build_client() as client:
            while len(papers) < max_results:
                url = f"{self.api_base}/{self.source}/{start_str}/{end_str}/{cursor}/json"
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("collection", []) or []
                if not rows:
                    break

                for row in rows:
                    published = parse_datetime(str(row.get("date") or ""))
                    if published is None or published < start_date or published > end_date:
                        continue
                    doi = normalize_doi(str(row.get("doi") or ""))
                    if not doi:
                        continue

                    title = clean_text(row.get("title"))
                    abstract = clean_text(row.get("abstract"))
                    if not title or not abstract:
                        continue

                    version = str(row.get("version", "") or "").strip()
                    content_id = f"{doi}v{version}" if version else doi
                    category = clean_text(row.get("category"))
                    papers.append(
                        Paper(
                            paper_id=doi,
                            source=self.source,
                            record_kind="preprint",
                            version_stage="preprint",
                            doi=doi,
                            openalex_id=None,
                            title=title,
                            authors=[
                                clean_text(author)
                                for author in str(row.get("authors", "") or "").split(";")
                                if clean_text(author)
                            ],
                            abstract=abstract,
                            categories=[category] if category else [],
                            primary_category=category,
                            published=published,
                            pdf_url=f"{self.domain}/content/{content_id}.full.pdf",
                            abs_url=f"{self.domain}/content/{content_id}",
                        )
                    )
                    if len(papers) >= max_results:
                        break

                cursor += len(rows)
                if len(rows) < 100:
                    break

        logger.info("Fetched %d %s papers", len(papers), self.source)
        return papers
