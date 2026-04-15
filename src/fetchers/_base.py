"""Shared fetcher types and helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Protocol, TypeAlias

import httpx

from src.fetcher import Paper


DateRange: TypeAlias = tuple[datetime, datetime]


class FetcherProtocol(Protocol):
    def fetch(self, date_range: DateRange, max_results: int) -> list[Paper]: ...


def build_client() -> httpx.Client:
    return httpx.Client(
        timeout=30.0,
        headers={
            "User-Agent": "PaperPress/1.0",
            "Accept": "application/json",
        },
    )


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    if not doi:
        return None
    doi = doi.removeprefix("https://doi.org/")
    doi = doi.removeprefix("http://doi.org/")
    doi = doi.removeprefix("doi:")
    return doi or None


def split_author_string(value: str | None) -> list[str]:
    if not value:
        return []
    authors = re.split(r";\s*|\s+and\s+|,\s*(?=[A-Z][^,]+$)", value)
    return [clean_text(author) for author in authors if clean_text(author)]


def normalize_title_for_key(value: str) -> str:
    lowered = clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def first_author_surname(authors: list[str]) -> str:
    if not authors:
        return ""
    first = clean_text(authors[0])
    if not first:
        return ""
    return first.split()[-1].lower()


def version_group_key(paper: Paper) -> str:
    if paper.doi:
        return f"doi:{normalize_doi(paper.doi)}"
    normalized_title = normalize_title_for_key(paper.title)
    author_key = first_author_surname(paper.authors)
    year = paper.published.year
    digest = hashlib.sha256(
        f"{normalized_title}|{author_key}|{year}".encode("utf-8")
    ).hexdigest()
    return f"title:{digest}"


def scope_allows_record(content_scope: str, record_kind: str) -> bool:
    if content_scope == "articles_only":
        return record_kind in {"preprint", "published"}
    if content_scope == "articles_and_datasets":
        return record_kind in {"preprint", "published", "dataset"}
    return True


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(value)
    stripper.close()
    return clean_text(" ".join(stripper.parts))
