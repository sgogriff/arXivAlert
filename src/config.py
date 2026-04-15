"""Loads and validates config.yaml and .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv


@dataclass
class ArxivSourceConfig:
    enabled: bool = True
    categories: list[str] = field(default_factory=list)
    max_results: int = 100


@dataclass
class BiorxivSourceConfig:
    enabled: bool = False
    max_results: int = 200


@dataclass
class MedrxivSourceConfig:
    enabled: bool = False
    max_results: int = 200


@dataclass
class OsfSourceConfig:
    enabled: bool = False
    max_results: int = 100
    providers: list[str] = field(default_factory=list)


@dataclass
class EuropepmcSourceConfig:
    enabled: bool = False
    max_results: int = 100
    extra_query: str = ""


@dataclass
class ZenodoSourceConfig:
    enabled: bool = False
    max_results: int = 100
    communities: list[str] = field(default_factory=list)


@dataclass
class OpenalexSourceConfig:
    enabled: bool = True
    max_results: int = 200
    primary_topics: list[str] = field(default_factory=list)
    journals: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)


@dataclass
class SourcesConfig:
    arxiv: ArxivSourceConfig = field(default_factory=ArxivSourceConfig)
    biorxiv: BiorxivSourceConfig = field(default_factory=BiorxivSourceConfig)
    medrxiv: MedrxivSourceConfig = field(default_factory=MedrxivSourceConfig)
    osf: OsfSourceConfig = field(default_factory=OsfSourceConfig)
    europepmc: EuropepmcSourceConfig = field(default_factory=EuropepmcSourceConfig)
    zenodo: ZenodoSourceConfig = field(default_factory=ZenodoSourceConfig)
    openalex: OpenalexSourceConfig = field(default_factory=OpenalexSourceConfig)


@dataclass
class SelectionConfig:
    include_preprints: bool = True
    include_published: bool = True
    content_scope: str = "articles_only"
    version_preference: str = "prefer_published"
    digest_view: str = "single_ranked"


@dataclass
class ScoringConfig:
    model: str = "claude-sonnet-4-20250514"
    threshold: int = 7
    batch_size: int = 8
    score_batch_size: int = 24
    abstract_truncation_words: int = 150


@dataclass
class DigestConfig:
    initial_search_window: str = "daily"
    output_dir: str = "output"
    min_papers_to_email: int = 3


@dataclass
class SmtpConfig:
    host: str = "smtp.gmail.com"
    port: int = 587
    user: str = ""
    password: str = ""
    recipient: str = ""


@dataclass
class AppConfig:
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    interests: str = ""
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    anthropic_api_key: str = ""
    zenodo_api_key: str = ""
    openalex_api_key: str = ""


_VALID_SCHEDULES = ("daily", "weekly", "fortnightly", "monthly")
_VALID_CONTENT_SCOPES = (
    "articles_only",
    "articles_and_datasets",
    "all_supported_types",
)
_VALID_VERSION_PREFERENCES = (
    "prefer_published",
    "prefer_newest",
    "prefer_preprint",
    "show_all",
)
_VALID_DIGEST_VIEWS = ("single_ranked",)


def _require_dict(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(
            f"config.yaml: '{key}' is required and must be a mapping. "
            "Copy config.yaml.template to create a valid config."
        )
    return value


def _as_str_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"config.yaml: {field_name} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"config.yaml: {field_name} must be a list of strings")
        text = item.strip()
        if text:
            result.append(text)
    return result


def _parse_int(raw: dict, key: str, default: int) -> int:
    lookup_key = key.rsplit(".", 1)[-1]
    try:
        value = int(raw.get(lookup_key, default))
    except (TypeError, ValueError) as e:
        raise ValueError(f"config.yaml: {key} must be an integer") from e
    if value < 1:
        raise ValueError(f"config.yaml: {key} must be >= 1")
    return value


def _parse_sources(raw: dict) -> SourcesConfig:
    sources_raw = _require_dict(raw, "sources")

    arxiv_raw = sources_raw.get("arxiv", {}) or {}
    biorxiv_raw = sources_raw.get("biorxiv", {}) or {}
    medrxiv_raw = sources_raw.get("medrxiv", {}) or {}
    osf_raw = sources_raw.get("osf", {}) or {}
    europepmc_raw = sources_raw.get("europepmc", {}) or {}
    zenodo_raw = sources_raw.get("zenodo", {}) or {}
    openalex_raw = sources_raw.get("openalex", {}) or {}

    sources = SourcesConfig(
        arxiv=ArxivSourceConfig(
            enabled=bool(arxiv_raw.get("enabled", True)),
            categories=_as_str_list(arxiv_raw.get("categories", []), "sources.arxiv.categories"),
            max_results=_parse_int(arxiv_raw, "sources.arxiv.max_results", 100),
        ),
        biorxiv=BiorxivSourceConfig(
            enabled=bool(biorxiv_raw.get("enabled", False)),
            max_results=_parse_int(biorxiv_raw, "sources.biorxiv.max_results", 200),
        ),
        medrxiv=MedrxivSourceConfig(
            enabled=bool(medrxiv_raw.get("enabled", False)),
            max_results=_parse_int(medrxiv_raw, "sources.medrxiv.max_results", 200),
        ),
        osf=OsfSourceConfig(
            enabled=bool(osf_raw.get("enabled", False)),
            max_results=_parse_int(osf_raw, "sources.osf.max_results", 100),
            providers=_as_str_list(osf_raw.get("providers", []), "sources.osf.providers"),
        ),
        europepmc=EuropepmcSourceConfig(
            enabled=bool(europepmc_raw.get("enabled", False)),
            max_results=_parse_int(europepmc_raw, "sources.europepmc.max_results", 100),
            extra_query=str(europepmc_raw.get("extra_query", "") or "").strip(),
        ),
        zenodo=ZenodoSourceConfig(
            enabled=bool(zenodo_raw.get("enabled", False)),
            max_results=_parse_int(zenodo_raw, "sources.zenodo.max_results", 100),
            communities=_as_str_list(
                zenodo_raw.get("communities", []),
                "sources.zenodo.communities",
            ),
        ),
        openalex=OpenalexSourceConfig(
            enabled=bool(openalex_raw.get("enabled", True)),
            max_results=_parse_int(openalex_raw, "sources.openalex.max_results", 200),
            primary_topics=_as_str_list(
                openalex_raw.get("primary_topics", []),
                "sources.openalex.primary_topics",
            ),
            journals=_as_str_list(
                openalex_raw.get("journals", []),
                "sources.openalex.journals",
            ),
            institutions=_as_str_list(
                openalex_raw.get("institutions", []),
                "sources.openalex.institutions",
            ),
        ),
    )

    enabled_sources = [
        sources.arxiv.enabled,
        sources.biorxiv.enabled,
        sources.medrxiv.enabled,
        sources.osf.enabled,
        sources.europepmc.enabled,
        sources.zenodo.enabled,
        sources.openalex.enabled,
    ]
    if not any(enabled_sources):
        raise ValueError("config.yaml: at least one source must be enabled")
    if sources.arxiv.enabled and not sources.arxiv.categories:
        raise ValueError(
            "config.yaml: sources.arxiv.categories must be a non-empty list when arXiv is enabled"
        )
    return sources


def _parse_selection(raw: dict) -> SelectionConfig:
    selection_raw = _require_dict(raw, "selection")
    selection = SelectionConfig(
        include_preprints=bool(selection_raw.get("include_preprints", True)),
        include_published=bool(selection_raw.get("include_published", True)),
        content_scope=str(selection_raw.get("content_scope", "articles_only") or "").strip(),
        version_preference=str(
            selection_raw.get("version_preference", "prefer_published") or ""
        ).strip(),
        digest_view=str(selection_raw.get("digest_view", "single_ranked") or "").strip(),
    )

    if not selection.include_preprints and not selection.include_published:
        raise ValueError(
            "config.yaml: at least one of selection.include_preprints or "
            "selection.include_published must be true"
        )
    if selection.content_scope not in _VALID_CONTENT_SCOPES:
        raise ValueError(
            "config.yaml: selection.content_scope must be one of "
            f"{_VALID_CONTENT_SCOPES}, got '{selection.content_scope}'"
        )
    if selection.version_preference not in _VALID_VERSION_PREFERENCES:
        raise ValueError(
            "config.yaml: selection.version_preference must be one of "
            f"{_VALID_VERSION_PREFERENCES}, got '{selection.version_preference}'"
        )
    if selection.digest_view not in _VALID_DIGEST_VIEWS:
        raise ValueError(
            "config.yaml: selection.digest_view must be one of "
            f"{_VALID_DIGEST_VIEWS}, got '{selection.digest_view}'"
        )
    return selection


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """Load and validate configuration from config.yaml and .env."""
    load_dotenv(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Config file is empty: {config_path}")
    if not isinstance(raw, dict):
        raise ValueError(
            "config.yaml: root must be a mapping. Copy config.yaml.template to create a valid config."
        )
    if "arxiv" in raw and "sources" not in raw:
        raise ValueError(
            "config.yaml: legacy top-level 'arxiv' config is no longer supported. "
            "Copy config.yaml.template and update your config to the new 'sources' format."
        )

    sources = _parse_sources(raw)
    selection = _parse_selection(raw)

    interests = str(raw.get("interests", "") or "").strip()
    if not interests:
        raise ValueError("config.yaml: interests must be a non-empty string")

    scoring_raw = raw.get("scoring", {}) or {}
    scoring_config = ScoringConfig(
        model=str(scoring_raw.get("model", "claude-sonnet-4-20250514") or "").strip(),
        threshold=_parse_int(scoring_raw, "scoring.threshold", 7),
        batch_size=_parse_int(scoring_raw, "scoring.batch_size", 8),
        score_batch_size=_parse_int(scoring_raw, "scoring.score_batch_size", 24),
        abstract_truncation_words=_parse_int(
            scoring_raw,
            "scoring.abstract_truncation_words",
            150,
        ),
    )

    digest_raw = raw.get("digest", {}) or {}
    initial_search_window = str(
        digest_raw.get("initial_search_window", digest_raw.get("schedule", "daily")) or ""
    ).strip()
    if initial_search_window not in _VALID_SCHEDULES:
        raise ValueError(
            "config.yaml: digest.initial_search_window must be one of "
            f"{_VALID_SCHEDULES}, got '{initial_search_window}'"
        )
    output_dir = str(digest_raw.get("output_dir", "output") or "").strip() or "output"
    min_papers_to_email = _parse_int(
        digest_raw,
        "digest.min_papers_to_email",
        3,
    )
    os.makedirs(output_dir, exist_ok=True)
    digest_config = DigestConfig(
        initial_search_window=initial_search_window,
        output_dir=output_dir,
        min_papers_to_email=min_papers_to_email,
    )

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_api_key:
        raise ValueError(".env: ANTHROPIC_API_KEY is required")

    openalex_api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if sources.openalex.enabled and not openalex_api_key:
        raise ValueError(
            ".env: OPENALEX_API_KEY is required when sources.openalex.enabled is true"
        )

    smtp_config = SmtpConfig(
        host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=os.environ.get("SMTP_USER", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        recipient=os.environ.get("RECIPIENT_EMAIL", ""),
    )

    return AppConfig(
        sources=sources,
        selection=selection,
        interests=interests,
        scoring=scoring_config,
        digest=digest_config,
        smtp=smtp_config,
        anthropic_api_key=anthropic_api_key,
        zenodo_api_key=os.environ.get("ZENODO_API_KEY", "").strip(),
        openalex_api_key=openalex_api_key,
    )
