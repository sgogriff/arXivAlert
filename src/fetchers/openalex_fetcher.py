"""OpenAlex published-work fetcher."""

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
)

logger = logging.getLogger("paperpress.fetchers.openalex")


def _rebuild_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: dict[int, str] = {}
    for word, slots in index.items():
        for slot in slots:
            positions[int(slot)] = word
    ordered = [positions[i] for i in sorted(positions)]
    return clean_text(" ".join(ordered))


def _map_version_stage(value: str | None) -> str:
    normalized = clean_text(value).lower()
    if normalized in {"submittedversion", "submitted version"}:
        return "preprint"
    if normalized in {"acceptedversion", "accepted version"}:
        return "accepted"
    if normalized in {"publishedversion", "published version"}:
        return "vor"
    return "unknown"


def _map_record_kind(work: dict) -> str:
    work_type = clean_text(str(work.get("type") or "")).lower()
    type_crossref = clean_text(str(work.get("type_crossref") or "")).lower()
    if work_type == "dataset" or type_crossref == "dataset":
        return "dataset"
    if type_crossref == "posted-content":
        return "preprint"
    return "published"


class OpenalexFetcher:
    def __init__(
        self,
        api_key: str,
        content_scope: str,
        primary_topics: list[str],
        journals: list[str],
        institutions: list[str],
    ) -> None:
        self.api_key = api_key
        self.content_scope = content_scope
        self.primary_topics = primary_topics
        self.journals = journals
        self.institutions = institutions

    def _build_filter(self, start_date: str, end_date: str) -> str:
        filters = [
            f"from_publication_date:{start_date}",
            f"to_publication_date:{end_date}",
            "has_abstract:true",
            "is_paratext:false",
        ]
        if self.primary_topics:
            filters.append("primary_topic.id:" + "|".join(self.primary_topics))
        if self.journals:
            filters.append("primary_location.source.id:" + "|".join(self.journals))
        if self.institutions:
            filters.append("institutions.id:" + "|".join(self.institutions))
        return ",".join(filters)

    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]:
        start_date, end_date = date_range
        page = 1
        papers: list[Paper] = []
        filter_value = self._build_filter(
            start_date.date().isoformat(),
            end_date.date().isoformat(),
        )
        select = ",".join(
            [
                "id",
                "doi",
                "title",
                "display_name",
                "publication_date",
                "publication_year",
                "abstract_inverted_index",
                "authorships",
                "primary_topic",
                "primary_location",
                "best_oa_location",
                "type",
                "type_crossref",
                "versions",
            ]
        )

        with build_client() as client:
            while len(papers) < max_results:
                response = client.get(
                    "https://api.openalex.org/works",
                    params={
                        "api_key": self.api_key,
                        "filter": filter_value,
                        "sort": "publication_date:desc",
                        "per-page": min(200, max_results - len(papers)),
                        "page": page,
                        "select": select,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("results", []) or []
                if not rows:
                    break

                for row in rows:
                    record_kind = _map_record_kind(row)
                    if record_kind == "preprint" or not scope_allows_record(
                        self.content_scope,
                        record_kind,
                    ):
                        continue

                    title = clean_text(row.get("display_name") or row.get("title"))
                    abstract = _rebuild_abstract(row.get("abstract_inverted_index"))
                    if not title or not abstract:
                        continue

                    published = parse_datetime(row.get("publication_date"))
                    if published is None:
                        continue

                    authors = []
                    for authorship in row.get("authorships", []) or []:
                        if not isinstance(authorship, dict):
                            continue
                        author = authorship.get("author", {}) or {}
                        name = clean_text(author.get("display_name"))
                        if name:
                            authors.append(name)

                    primary_topic = row.get("primary_topic", {}) or {}
                    category = clean_text(primary_topic.get("display_name"))
                    best_oa = row.get("best_oa_location", {}) or {}
                    primary_location = row.get("primary_location", {}) or {}
                    version_value = (
                        primary_location.get("version")
                        or best_oa.get("version")
                    )
                    abs_url = (
                        clean_text(primary_location.get("landing_page_url"))
                        or clean_text(best_oa.get("landing_page_url"))
                        or clean_text(row.get("id"))
                    )
                    pdf_url = clean_text(best_oa.get("pdf_url")) or abs_url

                    papers.append(
                        Paper(
                            paper_id=clean_text(row.get("id")),
                            source="openalex",
                            record_kind=record_kind,
                            version_stage=_map_version_stage(version_value),
                            doi=normalize_doi(row.get("doi")),
                            openalex_id=clean_text(row.get("id")),
                            title=title,
                            authors=authors,
                            abstract=abstract,
                            categories=[category] if category else [],
                            primary_category=category,
                            published=published,
                            pdf_url=pdf_url,
                            abs_url=abs_url,
                        )
                    )
                    if len(papers) >= max_results:
                        break

                if len(rows) < min(200, max_results - len(papers) or 200):
                    break
                page += 1

        logger.info("Fetched %d OpenAlex works", len(papers))
        return papers
