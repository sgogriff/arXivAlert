from __future__ import annotations

import unittest
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import arxiv
from src.config import SelectionConfig
from src.fetcher import Paper
from src.fetchers import _dedupe_papers
from src.fetchers.arxiv_fetcher import ArxivFetcher
from src.fetchers.biorxiv_fetcher import BiorxivFetcher
from src.fetchers.europepmc_fetcher import EuropepmcFetcher
from src.fetchers.openalex_fetcher import OpenalexFetcher
from src.fetchers.osf_fetcher import OsfFetcher
from src.fetchers.zenodo_fetcher import ZenodoFetcher


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, payloads: Iterable[dict]) -> None:
        self._payloads = list(payloads)
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, params=None) -> FakeResponse:
        if not self._payloads:
            raise AssertionError(f"No fake response configured for {url}")
        return FakeResponse(self._payloads.pop(0))


class FetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.date_range = (
            datetime(2026, 4, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 10, tzinfo=timezone.utc),
        )

    def test_arxiv_fetcher_normalizes_results(self) -> None:
        fake_result = SimpleNamespace(
            published=datetime(2026, 4, 5, tzinfo=timezone.utc),
            entry_id="http://arxiv.org/abs/2604.12345v1",
            title=" Test Title \n",
            authors=[SimpleNamespace(name="A. Author"), SimpleNamespace(name="B. Author")],
            summary=" Test abstract\n",
            categories=["cs.LG"],
            primary_category="cs.LG",
            pdf_url="https://arxiv.org/pdf/2604.12345v1",
            doi="10.1234/example",
        )

        class FakeArxivClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def results(self, search) -> list[SimpleNamespace]:
                return [fake_result]

        with patch("src.fetchers.arxiv_fetcher.arxiv.Client", FakeArxivClient):
            fetcher = ArxivFetcher(["cs.LG"])
            papers = fetcher.fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].paper_id, "2604.12345")
        self.assertEqual(papers[0].record_kind, "preprint")
        self.assertEqual(papers[0].doi, "10.1234/example")

    def test_arxiv_fetcher_retries_429_with_backoff(self) -> None:
        fake_result = SimpleNamespace(
            published=datetime(2026, 4, 5, tzinfo=timezone.utc),
            entry_id="http://arxiv.org/abs/2604.54321v1",
            title="Retry Title",
            authors=[SimpleNamespace(name="A. Author")],
            summary="Retry abstract",
            categories=["nucl-ex"],
            primary_category="nucl-ex",
            pdf_url="https://arxiv.org/pdf/2604.54321v1",
            doi=None,
        )
        attempts = {"count": 0}

        class FakeSession:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

        class FakeArxivClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self._session = FakeSession()

            def results(self, search) -> list[SimpleNamespace]:
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise arxiv.HTTPError("https://export.arxiv.org/api/query", 0, 429)
                return [fake_result]

        with patch("src.fetchers.arxiv_fetcher.arxiv.Client", FakeArxivClient), patch(
            "src.fetchers.arxiv_fetcher.time.sleep"
        ) as sleep_mock:
            fetcher = ArxivFetcher(["nucl-ex"])
            papers = fetcher.fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(attempts["count"], 2)
        sleep_mock.assert_called_once()

    def test_biorxiv_fetcher_normalizes_results(self) -> None:
        payload = {
            "collection": [
                {
                    "doi": "10.1101/2026.04.01.123456",
                    "title": "bioRxiv title",
                    "authors": "A. Author; B. Author",
                    "abstract": "bioRxiv abstract",
                    "category": "neuroscience",
                    "date": "2026-04-04",
                    "version": "1",
                }
            ]
        }
        with patch(
            "src.fetchers.biorxiv_fetcher.build_client",
            return_value=FakeClient([payload]),
        ):
            papers = BiorxivFetcher("biorxiv").fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].source, "biorxiv")
        self.assertEqual(papers[0].record_kind, "preprint")

    def test_osf_fetcher_normalizes_results(self) -> None:
        payload = {
            "data": [
                {
                    "id": "abc12",
                    "attributes": {
                        "title": "OSF Title",
                        "description": "OSF Abstract",
                        "date_published": "2026-04-03",
                        "doi": "10.31234/osf.io/abc12",
                        "subjects": [[{"text": "Psychology"}]],
                        "author_names": ["Jane Doe"],
                    },
                    "links": {
                        "html": "https://osf.io/preprints/osf/abc12/",
                        "download": "https://osf.io/download/abc12/",
                    },
                }
            ],
            "links": {"next": None},
        }
        with patch(
            "src.fetchers.osf_fetcher.build_client",
            return_value=FakeClient([payload]),
        ):
            papers = OsfFetcher([]).fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].source, "osf")
        self.assertEqual(papers[0].categories, ["Psychology"])

    def test_europepmc_fetcher_normalizes_results(self) -> None:
        payload = {
            "resultList": {
                "result": [
                    {
                        "id": "PPR123",
                        "source": "PPR",
                        "title": "Europe PMC title",
                        "abstractText": "Europe PMC abstract",
                        "authorString": "A. Author; B. Author",
                        "firstPublicationDate": "2026-04-02",
                        "doi": "10.1101/12345",
                        "fullTextUrlList": {
                            "fullTextUrl": [{"url": "https://example.org/paper.pdf"}]
                        },
                    }
                ]
            }
        }
        with patch(
            "src.fetchers.europepmc_fetcher.build_client",
            return_value=FakeClient([payload]),
        ):
            papers = EuropepmcFetcher("").fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].source, "europepmc")
        self.assertEqual(papers[0].pdf_url, "https://example.org/paper.pdf")

    def test_zenodo_fetcher_normalizes_results(self) -> None:
        empty_payload = {"hits": {"hits": []}}
        payload = {
            "hits": {
                "hits": [
                    {
                        "id": 123,
                        "metadata": {
                            "title": "Zenodo Title",
                            "description": "<p>Zenodo abstract</p>",
                            "publication_date": "2026-04-03",
                            "doi": "10.5281/zenodo.123",
                            "resource_type": {"title": "Journal article"},
                            "creators": [{"name": "Doe, Jane"}],
                            "keywords": ["physics"],
                        },
                        "links": {"self_html": "https://zenodo.org/records/123"},
                        "files": [
                            {
                                "key": "paper.pdf",
                                "links": {"self": "https://zenodo.org/files/paper.pdf"},
                            }
                        ],
                    }
                ]
            }
        }
        with patch(
            "src.fetchers.zenodo_fetcher.build_client",
            return_value=FakeClient([empty_payload, payload, empty_payload]),
        ):
            papers = ZenodoFetcher([], "articles_only").fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].record_kind, "published")
        self.assertEqual(papers[0].categories, ["physics"])

    def test_openalex_fetcher_normalizes_results(self) -> None:
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "doi": "https://doi.org/10.1000/test",
                    "display_name": "OpenAlex Title",
                    "publication_date": "2026-04-04",
                    "abstract_inverted_index": {"OpenAlex": [0], "abstract": [1]},
                    "authorships": [{"author": {"display_name": "Jane Doe"}}],
                    "primary_topic": {"display_name": "Machine Learning"},
                    "best_oa_location": {
                        "landing_page_url": "https://publisher.example/paper",
                        "pdf_url": "https://publisher.example/paper.pdf",
                    },
                    "primary_location": {
                        "landing_page_url": "https://publisher.example/paper",
                        "version": "publishedVersion",
                    },
                    "type": "article",
                    "type_crossref": "journal-article",
                    "versions": None,
                }
            ]
        }
        with patch(
            "src.fetchers.openalex_fetcher.build_client",
            return_value=FakeClient([payload]),
        ):
            papers = OpenalexFetcher(
                api_key="test",
                content_scope="articles_only",
                primary_topics=[],
                journals=[],
                institutions=[],
            ).fetch(self.date_range, 10)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].source, "openalex")
        self.assertEqual(papers[0].record_kind, "published")
        self.assertEqual(papers[0].version_stage, "vor")

    def test_dedupe_prefers_published_version(self) -> None:
        published = Paper(
            paper_id="oa-1",
            source="openalex",
            record_kind="published",
            version_stage="vor",
            doi="10.1000/test",
            openalex_id="https://openalex.org/W1",
            title="Shared title",
            authors=["Jane Doe"],
            abstract="A",
            categories=[],
            primary_category="",
            published=datetime(2026, 4, 5, tzinfo=timezone.utc),
            pdf_url="https://example.org/published.pdf",
            abs_url="https://example.org/published",
        )
        preprint = Paper(
            paper_id="10.1000/test",
            source="biorxiv",
            record_kind="preprint",
            version_stage="preprint",
            doi="10.1000/test",
            openalex_id=None,
            title="Shared title",
            authors=["Jane Doe"],
            abstract="A",
            categories=[],
            primary_category="",
            published=datetime(2026, 4, 3, tzinfo=timezone.utc),
            pdf_url="https://example.org/preprint.pdf",
            abs_url="https://example.org/preprint",
        )
        selection = SelectionConfig(version_preference="prefer_published")

        deduped = _dedupe_papers([preprint, published], selection)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source, "openalex")


if __name__ == "__main__":
    unittest.main()
