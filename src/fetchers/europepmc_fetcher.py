"""Europe PMC preprint fetcher."""

from __future__ import annotations

import logging

from src.fetcher import Paper
from src.fetchers._base import (
    DateRange,
    build_client,
    clean_text,
    normalize_doi,
    parse_datetime,
    split_author_string,
)

logger = logging.getLogger("paperpress.fetchers.europepmc")


class EuropepmcFetcher:
    def __init__(self, extra_query: str = "") -> None:
        self.extra_query = extra_query.strip()

    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]:
        start_date, end_date = date_range
        papers: list[Paper] = []
        page = 1
        query_parts = [
            "PUB_TYPE:preprint",
            f'FIRST_PDATE:[{start_date.date().isoformat()} TO {end_date.date().isoformat()}]',
            "HAS_ABSTRACT:y",
        ]
        if self.extra_query:
            query_parts.append(f"({self.extra_query})")
        query = " AND ".join(query_parts)

        with build_client() as client:
            while len(papers) < max_results:
                page_size = min(100, max_results - len(papers))
                response = client.get(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={
                        "query": query,
                        "resultType": "core",
                        "format": "json",
                        "pageSize": page_size,
                        "page": page,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                rows = ((payload.get("resultList") or {}).get("result") or [])
                if not rows:
                    break

                for row in rows:
                    title = clean_text(row.get("title"))
                    abstract = clean_text(row.get("abstractText"))
                    if not title or not abstract:
                        continue

                    published = parse_datetime(
                        row.get("firstPublicationDate")
                        or row.get("electronicPublicationDate")
                        or row.get("firstIndexDate")
                    )
                    if published is None:
                        continue

                    doi = normalize_doi(row.get("doi"))
                    paper_id = doi or clean_text(str(row.get("id") or ""))
                    if not paper_id:
                        continue

                    full_text_urls = ((row.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
                    pdf_url = ""
                    for item in full_text_urls:
                        if not isinstance(item, dict):
                            continue
                        url = str(item.get("url") or "").strip()
                        if url:
                            pdf_url = url
                            if "pdf" in url.lower():
                                break

                    source_id = clean_text(str(row.get("source") or "PPR"))
                    article_id = clean_text(str(row.get("id") or paper_id))
                    abs_url = f"https://europepmc.org/article/{source_id}/{article_id}"
                    papers.append(
                        Paper(
                            paper_id=paper_id,
                            source="europepmc",
                            record_kind="preprint",
                            version_stage="preprint",
                            doi=doi,
                            openalex_id=None,
                            title=title,
                            authors=split_author_string(row.get("authorString")),
                            abstract=abstract,
                            categories=[],
                            primary_category="",
                            published=published,
                            pdf_url=pdf_url or abs_url,
                            abs_url=abs_url,
                        )
                    )
                    if len(papers) >= max_results:
                        break

                if len(rows) < page_size:
                    break
                page += 1

        logger.info("Fetched %d Europe PMC preprints", len(papers))
        return papers
