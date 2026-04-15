from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.fetcher import Paper
from src.scorer import (
    ScoredPaper,
    _paper_from_cache,
    build_analysis_stats,
    load_pending_scored_papers,
    mark_papers_emailed,
)


class ScorerCacheTests(unittest.TestCase):
    def _paper(self, paper_id: str, source: str, record_kind: str, published_day: int) -> Paper:
        return Paper(
            paper_id=paper_id,
            source=source,
            record_kind=record_kind,
            version_stage="preprint" if record_kind == "preprint" else "vor",
            doi=None,
            openalex_id=None,
            title=f"{source} {paper_id}",
            authors=["Jane Doe"],
            abstract="Example abstract",
            categories=["test"],
            primary_category="test",
            published=datetime(2026, 4, published_day, tzinfo=timezone.utc),
            pdf_url=f"https://example.org/{source}/{paper_id}.pdf",
            abs_url=f"https://example.org/{source}/{paper_id}",
        )

    def test_old_cache_shape_deserializes(self) -> None:
        paper = _paper_from_cache(
            {
                "arxiv_id": "2604.12345",
                "title": "Legacy Title",
                "authors": ["Jane Doe"],
                "abstract": "Legacy abstract",
                "categories": ["cs.LG"],
                "primary_category": "cs.LG",
                "published": "2026-04-01T00:00:00+00:00",
                "pdf_url": "https://arxiv.org/pdf/2604.12345v1",
                "abs_url": "https://arxiv.org/abs/2604.12345v1",
            }
        )

        self.assertIsNotNone(paper)
        assert paper is not None
        self.assertEqual(paper.paper_id, "2604.12345")
        self.assertEqual(paper.source, "arxiv")
        self.assertEqual(paper.record_kind, "preprint")

    def test_mark_papers_emailed_updates_legacy_bare_keys(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        cache_path = Path(tempdir.name) / "paper_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "2604.12345": {
                        "score": 9,
                        "summary": "summary",
                        "reason": "reason",
                        "emailed": False,
                        "paper": {
                            "arxiv_id": "2604.12345",
                            "title": "Legacy Title",
                            "authors": ["Jane Doe"],
                            "abstract": "Legacy abstract",
                            "categories": ["cs.LG"],
                            "primary_category": "cs.LG",
                            "published": "2026-04-01T00:00:00+00:00",
                            "pdf_url": "https://arxiv.org/pdf/2604.12345v1",
                            "abs_url": "https://arxiv.org/abs/2604.12345v1",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        mark_papers_emailed(["arxiv:2604.12345"], str(cache_path))
        stored = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertIn("arxiv:2604.12345", stored)
        self.assertTrue(stored["arxiv:2604.12345"]["emailed"])

    def test_load_pending_scored_papers_reads_legacy_entries(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        cache_path = Path(tempdir.name) / "paper_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "arxiv:2604.12345": {
                        "score": 8,
                        "summary": "summary",
                        "reason": "reason",
                        "emailed": False,
                        "paper": {
                            "arxiv_id": "2604.12345",
                            "title": "Legacy Title",
                            "authors": ["Jane Doe"],
                            "abstract": "Legacy abstract",
                            "categories": ["cs.LG"],
                            "primary_category": "cs.LG",
                            "published": "2026-04-01T00:00:00+00:00",
                            "pdf_url": "https://arxiv.org/pdf/2604.12345v1",
                            "abs_url": "https://arxiv.org/abs/2604.12345v1",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        pending = load_pending_scored_papers(7, str(cache_path))

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].paper.paper_id, "2604.12345")

    def test_build_analysis_stats_counts_cache_and_digest_outcomes(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        cache_path = Path(tempdir.name) / "paper_cache.json"

        low_score = self._paper("low-1", "arxiv", "preprint", 2)
        digest_hit = self._paper("pub-1", "openalex", "published", 3)
        pass2_failed = self._paper("pass2-fail", "medrxiv", "preprint", 4)
        old_paper = self._paper("old-1", "arxiv", "preprint", 1)

        cache_path.write_text(
            json.dumps(
                {
                    "arxiv:low-1": {
                        "score": 3,
                        "summary": None,
                        "reason": None,
                        "emailed": False,
                        "scored_at": "2026-04-10T09:00:00+00:00",
                        "paper": {
                            "paper_id": low_score.paper_id,
                            "source": low_score.source,
                            "record_kind": low_score.record_kind,
                            "version_stage": low_score.version_stage,
                            "title": low_score.title,
                            "authors": low_score.authors,
                            "abstract": low_score.abstract,
                            "categories": low_score.categories,
                            "primary_category": low_score.primary_category,
                            "published": low_score.published.isoformat(),
                            "pdf_url": low_score.pdf_url,
                            "abs_url": low_score.abs_url,
                        },
                    },
                    "openalex:pub-1": {
                        "score": 8,
                        "summary": "summary",
                        "reason": "reason",
                        "emailed": False,
                        "scored_at": "2026-04-11T09:00:00+00:00",
                        "paper": {
                            "paper_id": digest_hit.paper_id,
                            "source": digest_hit.source,
                            "record_kind": digest_hit.record_kind,
                            "version_stage": digest_hit.version_stage,
                            "title": digest_hit.title,
                            "authors": digest_hit.authors,
                            "abstract": digest_hit.abstract,
                            "categories": digest_hit.categories,
                            "primary_category": digest_hit.primary_category,
                            "published": digest_hit.published.isoformat(),
                            "pdf_url": digest_hit.pdf_url,
                            "abs_url": digest_hit.abs_url,
                        },
                    },
                    "medrxiv:pass2-fail": {
                        "score": 7,
                        "summary": None,
                        "reason": None,
                        "emailed": False,
                        "scored_at": "2026-04-12T09:00:00+00:00",
                        "paper": {
                            "paper_id": pass2_failed.paper_id,
                            "source": pass2_failed.source,
                            "record_kind": pass2_failed.record_kind,
                            "version_stage": pass2_failed.version_stage,
                            "title": pass2_failed.title,
                            "authors": pass2_failed.authors,
                            "abstract": pass2_failed.abstract,
                            "categories": pass2_failed.categories,
                            "primary_category": pass2_failed.primary_category,
                            "published": pass2_failed.published.isoformat(),
                            "pdf_url": pass2_failed.pdf_url,
                            "abs_url": pass2_failed.abs_url,
                        },
                    },
                    "arxiv:old-1": {
                        "score": 10,
                        "summary": "summary",
                        "reason": "reason",
                        "emailed": False,
                        "scored_at": "2026-04-01T09:00:00+00:00",
                        "paper": {
                            "paper_id": old_paper.paper_id,
                            "source": old_paper.source,
                            "record_kind": old_paper.record_kind,
                            "version_stage": old_paper.version_stage,
                            "title": old_paper.title,
                            "authors": old_paper.authors,
                            "abstract": old_paper.abstract,
                            "categories": old_paper.categories,
                            "primary_category": old_paper.primary_category,
                            "published": old_paper.published.isoformat(),
                            "pdf_url": old_paper.pdf_url,
                            "abs_url": old_paper.abs_url,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        uncached_digest_paper = self._paper("digest-1", "biorxiv", "preprint", 12)
        digest_papers = [
            ScoredPaper(
                paper=digest_hit,
                score=8,
                summary="summary",
                relevance_reason="reason",
            ),
            ScoredPaper(
                paper=uncached_digest_paper,
                score=9,
                summary="summary",
                relevance_reason="reason",
            ),
        ]

        stats = build_analysis_stats(
            datetime(2026, 4, 9, tzinfo=timezone.utc),
            digest_papers,
            str(cache_path),
        )

        self.assertEqual(stats.total_analysed(), 4)
        self.assertEqual(stats.total_second_pass(), 2)
        self.assertEqual(stats.total_digest(), 2)
        self.assertEqual(stats.analysed_by_kind["preprint"]["3"], 1)
        self.assertEqual(stats.analysed_by_kind["preprint"]["7"], 1)
        self.assertEqual(stats.analysed_by_kind["preprint"]["9"], 1)
        self.assertEqual(stats.analysed_by_kind["published"]["8"], 1)
        self.assertEqual(stats.second_pass_by_kind["published"]["8"], 1)
        self.assertEqual(stats.second_pass_by_kind["preprint"]["9"], 1)
        self.assertNotIn("7", stats.second_pass_by_kind.get("preprint", {}))
        self.assertEqual(stats.digest_by_kind["published"]["8"], 1)
        self.assertEqual(stats.digest_by_kind["preprint"]["9"], 1)


if __name__ == "__main__":
    unittest.main()
