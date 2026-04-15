from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import load_config


class ConfigLoadTests(unittest.TestCase):
    def _write_config(self, config_text: str, env_text: str) -> tuple[str, str]:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        config_path = root / "config.yaml"
        env_path = root / ".env"
        config_path.write_text(textwrap.dedent(config_text).strip() + "\n", encoding="utf-8")
        env_path.write_text(textwrap.dedent(env_text).strip() + "\n", encoding="utf-8")
        return str(config_path), str(env_path)

    def test_loads_valid_hybrid_config(self) -> None:
        config_path, env_path = self._write_config(
            """
            sources:
              arxiv:
                enabled: true
                categories: ["cs.LG"]
                max_results: 10
              biorxiv:
                enabled: false
                max_results: 20
              medrxiv:
                enabled: false
                max_results: 20
              osf:
                enabled: false
                max_results: 20
                providers: []
              europepmc:
                enabled: false
                max_results: 20
                extra_query: ""
              zenodo:
                enabled: false
                max_results: 20
                communities: []
              openalex:
                enabled: true
                max_results: 20
                primary_topics: []
                journals: []
                institutions: []

            selection:
              include_preprints: true
              include_published: true
              content_scope: "articles_only"
              version_preference: "prefer_published"
              digest_view: "single_ranked"

            interests: |
              Relevant ML and robotics papers.

            scoring:
              model: "claude-sonnet-4-20250514"
              threshold: 7
              batch_size: 8
              score_batch_size: 24
              abstract_truncation_words: 150

            digest:
              initial_search_window: "weekly"
              output_dir: "output"
              min_papers_to_email: 3
            """,
            """
            ANTHROPIC_API_KEY=test-ant
            OPENALEX_API_KEY=test-openalex
            """,
        )

        with patch.dict("os.environ", {}, clear=True):
            config = load_config(config_path, env_path)
        self.assertTrue(config.sources.arxiv.enabled)
        self.assertTrue(config.sources.openalex.enabled)
        self.assertEqual(config.sources.arxiv.categories, ["cs.LG"])
        self.assertEqual(config.selection.version_preference, "prefer_published")

    def test_rejects_legacy_top_level_arxiv_config(self) -> None:
        config_path, env_path = self._write_config(
            """
            arxiv:
              categories: ["cs.LG"]
              max_results: 10
            interests: test
            """,
            """
            ANTHROPIC_API_KEY=test-ant
            """,
        )

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "legacy top-level 'arxiv'"):
                load_config(config_path, env_path)

    def test_requires_openalex_key_only_when_openalex_enabled(self) -> None:
        enabled_config, env_path = self._write_config(
            """
            sources:
              arxiv:
                enabled: true
                categories: ["cs.LG"]
                max_results: 10
              biorxiv: {enabled: false, max_results: 20}
              medrxiv: {enabled: false, max_results: 20}
              osf: {enabled: false, max_results: 20, providers: []}
              europepmc: {enabled: false, max_results: 20, extra_query: ""}
              zenodo: {enabled: false, max_results: 20, communities: []}
              openalex:
                enabled: true
                max_results: 20
                primary_topics: []
                journals: []
                institutions: []
            selection:
              include_preprints: true
              include_published: true
              content_scope: "articles_only"
              version_preference: "prefer_published"
              digest_view: "single_ranked"
            interests: test
            """,
            """
            ANTHROPIC_API_KEY=test-ant
            """,
        )

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENALEX_API_KEY"):
                load_config(enabled_config, env_path)

        disabled_config, disabled_env = self._write_config(
            """
            sources:
              arxiv:
                enabled: true
                categories: ["cs.LG"]
                max_results: 10
              biorxiv: {enabled: false, max_results: 20}
              medrxiv: {enabled: false, max_results: 20}
              osf: {enabled: false, max_results: 20, providers: []}
              europepmc: {enabled: false, max_results: 20, extra_query: ""}
              zenodo: {enabled: false, max_results: 20, communities: []}
              openalex:
                enabled: false
                max_results: 20
                primary_topics: []
                journals: []
                institutions: []
            selection:
              include_preprints: true
              include_published: true
              content_scope: "articles_only"
              version_preference: "prefer_published"
              digest_view: "single_ranked"
            interests: test
            """,
            """
            ANTHROPIC_API_KEY=test-ant
            """,
        )

        with patch.dict("os.environ", {}, clear=True):
            config = load_config(disabled_config, disabled_env)
        self.assertFalse(config.sources.openalex.enabled)


if __name__ == "__main__":
    unittest.main()
