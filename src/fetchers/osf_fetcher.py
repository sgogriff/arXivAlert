"""OSF preprint fetcher."""

from __future__ import annotations

import logging

from src.fetcher import Paper
from src.fetchers._base import DateRange, build_client, clean_text, normalize_doi, parse_datetime

logger = logging.getLogger("paperpress.fetchers.osf")


class OsfFetcher:
    def __init__(self, providers: list[str]) -> None:
        self.providers = providers

    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]:
        provider_filters = self.providers or [""]
        papers: list[Paper] = []
        start_date, end_date = date_range

        with build_client() as client:
            for provider in provider_filters:
                next_url = "https://api.osf.io/v2/preprints/"
                params = {
                    "page[size]": 100,
                    "filter[date_published][gte]": start_date.date().isoformat(),
                    "filter[date_published][lte]": end_date.date().isoformat(),
                }
                if provider:
                    params["filter[provider]"] = provider

                while next_url and len(papers) < max_results:
                    response = client.get(next_url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    data = payload.get("data", []) or []
                    params = None
                    if not data:
                        break

                    for entry in data:
                        attributes = entry.get("attributes", {}) or {}
                        title = clean_text(attributes.get("title"))
                        abstract = clean_text(attributes.get("description"))
                        published = parse_datetime(attributes.get("date_published"))
                        if not title or not abstract or published is None:
                            continue

                        subjects = []
                        for group in attributes.get("subjects", []) or []:
                            if isinstance(group, list):
                                for subject in group:
                                    if isinstance(subject, dict):
                                        label = clean_text(
                                            subject.get("text") or subject.get("name")
                                        )
                                        if label:
                                            subjects.append(label)
                            elif isinstance(group, dict):
                                label = clean_text(group.get("text") or group.get("name"))
                                if label:
                                    subjects.append(label)

                        authors: list[str] = []
                        for key in ("author_names", "authors"):
                            raw_authors = attributes.get(key)
                            if isinstance(raw_authors, list):
                                authors = [clean_text(name) for name in raw_authors if clean_text(name)]
                                break

                        links = entry.get("links", {}) or {}
                        abs_url = (
                            links.get("html")
                            or attributes.get("html_url")
                            or f"https://osf.io/preprints/{entry.get('id', '')}/"
                        )
                        pdf_url = links.get("download") or abs_url
                        papers.append(
                            Paper(
                                paper_id=str(entry.get("id")),
                                source="osf",
                                record_kind="preprint",
                                version_stage="preprint",
                                doi=normalize_doi(attributes.get("doi")),
                                openalex_id=None,
                                title=title,
                                authors=authors,
                                abstract=abstract,
                                categories=subjects,
                                primary_category=subjects[0] if subjects else "",
                                published=published,
                                pdf_url=str(pdf_url),
                                abs_url=str(abs_url),
                            )
                        )
                        if len(papers) >= max_results:
                            break

                    next_url = ((payload.get("links") or {}).get("next") or "").strip() or ""

        logger.info("Fetched %d OSF preprints", len(papers))
        return papers
