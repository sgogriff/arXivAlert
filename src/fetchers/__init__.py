"""Multi-source paper fetching and deduplication."""

from __future__ import annotations

import logging

from src.config import AppConfig, SelectionConfig
from src.fetcher import Paper
from src.fetchers._base import DateRange, scope_allows_record, version_group_key
from src.fetchers.arxiv_fetcher import ArxivFetcher
from src.fetchers.biorxiv_fetcher import BiorxivFetcher
from src.fetchers.europepmc_fetcher import EuropepmcFetcher
from src.fetchers.openalex_fetcher import OpenalexFetcher
from src.fetchers.osf_fetcher import OsfFetcher
from src.fetchers.zenodo_fetcher import ZenodoFetcher

logger = logging.getLogger("paperpress.fetchers")


def _cache_key(paper: Paper) -> str:
    return f"{paper.source}:{paper.paper_id}"


def _include_by_selection(paper: Paper, selection: SelectionConfig) -> bool:
    if not scope_allows_record(selection.content_scope, paper.record_kind):
        return False
    if paper.record_kind == "preprint":
        return selection.include_preprints
    return selection.include_published


def _source_rank(paper: Paper) -> tuple[int, int]:
    record_rank = 1 if paper.record_kind == "published" else 0
    version_rank = {"vor": 3, "accepted": 2, "preprint": 1, "unknown": 0}.get(
        paper.version_stage,
        0,
    )
    return record_rank, version_rank


def _choose_preferred_paper(papers: list[Paper], preference: str) -> Paper:
    if preference == "prefer_published":
        return max(papers, key=lambda p: (_source_rank(p), p.published))
    if preference == "prefer_preprint":
        return max(
            papers,
            key=lambda p: (
                1 if p.record_kind == "preprint" else 0,
                p.published,
            ),
        )
    return max(papers, key=lambda p: p.published)


def _dedupe_papers(papers: list[Paper], selection: SelectionConfig) -> list[Paper]:
    if selection.version_preference == "show_all":
        unique: dict[str, Paper] = {}
        for paper in papers:
            unique[_cache_key(paper)] = paper
        return sorted(unique.values(), key=lambda item: item.published, reverse=True)

    grouped: dict[str, list[Paper]] = {}
    for paper in papers:
        grouped.setdefault(version_group_key(paper), []).append(paper)

    collapsed = [
        _choose_preferred_paper(group, selection.version_preference)
        for group in grouped.values()
    ]
    return sorted(collapsed, key=lambda item: item.published, reverse=True)


def fetch_all_papers(config: AppConfig, date_range: DateRange) -> list[Paper]:
    fetch_jobs: list[tuple[str, object, int]] = []
    sources = config.sources
    if sources.arxiv.enabled:
        fetch_jobs.append(
            ("arxiv", ArxivFetcher(sources.arxiv.categories), sources.arxiv.max_results)
        )
    if sources.biorxiv.enabled:
        fetch_jobs.append(
            ("biorxiv", BiorxivFetcher("biorxiv"), sources.biorxiv.max_results)
        )
    if sources.medrxiv.enabled:
        fetch_jobs.append(
            ("medrxiv", BiorxivFetcher("medrxiv"), sources.medrxiv.max_results)
        )
    if sources.osf.enabled:
        fetch_jobs.append(("osf", OsfFetcher(sources.osf.providers), sources.osf.max_results))
    if sources.europepmc.enabled:
        fetch_jobs.append(
            (
                "europepmc",
                EuropepmcFetcher(sources.europepmc.extra_query),
                sources.europepmc.max_results,
            )
        )
    if sources.zenodo.enabled:
        fetch_jobs.append(
            (
                "zenodo",
                ZenodoFetcher(
                    communities=sources.zenodo.communities,
                    content_scope=config.selection.content_scope,
                    api_token=config.zenodo_api_key,
                ),
                sources.zenodo.max_results,
            )
        )
    if sources.openalex.enabled:
        fetch_jobs.append(
            (
                "openalex",
                OpenalexFetcher(
                    api_key=config.openalex_api_key,
                    content_scope=config.selection.content_scope,
                    primary_topics=sources.openalex.primary_topics,
                    journals=sources.openalex.journals,
                    institutions=sources.openalex.institutions,
                ),
                sources.openalex.max_results,
            )
        )

    fetched: list[Paper] = []
    for source_name, fetcher, max_results in fetch_jobs:
        try:
            logger.info("Fetching from %s", source_name)
            fetched.extend(fetcher.fetch(date_range, max_results))
        except Exception as e:
            logger.warning("Failed to fetch from %s: %s", source_name, e)

    eligible = [paper for paper in fetched if _include_by_selection(paper, config.selection)]
    deduped = _dedupe_papers(eligible, config.selection)
    logger.info(
        "Fetched %d papers across %d enabled sources; %d remain after filters and dedupe",
        len(fetched),
        len(fetch_jobs),
        len(deduped),
    )
    return deduped


__all__ = ["fetch_all_papers", "_dedupe_papers"]
