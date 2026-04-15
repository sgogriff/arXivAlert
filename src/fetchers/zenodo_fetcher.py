"""Zenodo record fetcher."""

from __future__ import annotations

import logging

from src.fetcher import Paper
from src.fetchers._base import (
    DateRange,
    build_client,
    clean_text,
    normalize_doi,
    parse_datetime,
    scope_allows_record,
    strip_html,
)

logger = logging.getLogger("paperpress.fetchers.zenodo")

_ZENODO_SUBTYPE_MAP = {
    "preprint": "preprint",
    "journal article": "published",
    "conference paper": "published",
    "dataset": "dataset",
}


class ZenodoFetcher:
    def __init__(self, communities: list[str], content_scope: str, api_token: str = "") -> None:
        self.communities = communities
        self.content_scope = content_scope
        self.api_token = api_token.strip()

    def _iter_record_queries(self) -> list[tuple[str, str]]:
        queries = [
            ("publication", "preprint"),
            ("publication", "article"),
            ("publication", "conferencepaper"),
        ]
        if self.content_scope != "articles_only":
            queries.append(("dataset", ""))
        return queries

    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]:
        start_date, end_date = date_range
        size = 100 if self.api_token else 25
        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        papers: list[Paper] = []
        communities = self.communities or [""]
        with build_client() as client:
            if headers:
                client.headers.update(headers)

            for community in communities:
                for record_type, subtype in self._iter_record_queries():
                    page = 1
                    while len(papers) < max_results:
                        params = {
                            "page": page,
                            "size": min(size, max_results - len(papers)),
                            "sort": "mostrecent",
                            "all_versions": "0",
                            "type": record_type,
                            "q": (
                                "publication_date:["
                                f"{start_date.date().isoformat()} TO {end_date.date().isoformat()}]"
                            ),
                        }
                        if subtype:
                            params["subtype"] = subtype
                        if community:
                            params["communities"] = community
                        response = client.get(
                            "https://zenodo.org/api/records",
                            params=params,
                        )
                        response.raise_for_status()
                        payload = response.json()
                        rows = ((payload.get("hits") or {}).get("hits") or [])
                        if not rows:
                            break

                        for row in rows:
                            metadata = row.get("metadata", {}) or {}
                            title = clean_text(metadata.get("title"))
                            abstract = strip_html(metadata.get("description"))
                            if not title or not abstract:
                                continue

                            subtype_name = clean_text(
                                ((metadata.get("resource_type") or {}).get("title"))
                                or subtype
                            ).lower()
                            record_kind = _ZENODO_SUBTYPE_MAP.get(subtype_name, "other")
                            if not scope_allows_record(self.content_scope, record_kind):
                                continue

                            published = parse_datetime(
                                metadata.get("publication_date") or row.get("created")
                            )
                            if published is None:
                                continue

                            files = row.get("files", []) or []
                            pdf_url = ""
                            for file_info in files:
                                if not isinstance(file_info, dict):
                                    continue
                                key = str(file_info.get("key") or "").lower()
                                if key.endswith(".pdf"):
                                    pdf_url = str(
                                        (file_info.get("links") or {}).get("self") or ""
                                    ).strip()
                                    break

                            doi = normalize_doi(metadata.get("doi") or row.get("doi"))
                            links = row.get("links", {}) or {}
                            abs_url = (
                                str(links.get("self_html") or "").strip()
                                or str(row.get("doi_url") or "").strip()
                                or str(links.get("self") or "").strip()
                            )

                            keywords = [
                                clean_text(keyword)
                                for keyword in metadata.get("keywords", []) or []
                                if clean_text(keyword)
                            ]
                            authors = []
                            for creator in metadata.get("creators", []) or []:
                                if isinstance(creator, dict):
                                    name = clean_text(creator.get("name"))
                                    if name:
                                        authors.append(name)

                            papers.append(
                                Paper(
                                    paper_id=str(row.get("id")),
                                    source="zenodo",
                                    record_kind=record_kind,
                                    version_stage="preprint" if record_kind == "preprint" else "vor",
                                    doi=doi,
                                    openalex_id=None,
                                    title=title,
                                    authors=authors,
                                    abstract=abstract,
                                    categories=keywords,
                                    primary_category=keywords[0] if keywords else "",
                                    published=published,
                                    pdf_url=pdf_url or abs_url,
                                    abs_url=abs_url,
                                )
                            )
                            if len(papers) >= max_results:
                                break

                        if len(rows) < size:
                            break
                        page += 1

        logger.info("Fetched %d Zenodo records", len(papers))
        return papers
