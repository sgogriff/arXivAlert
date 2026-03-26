"""Loads and validates config.yaml and .env."""

import os
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv


@dataclass
class ArxivConfig:
    categories: list[str]
    max_results: int = 100


@dataclass
class ScoringConfig:
    model: str = "claude-sonnet-4-20250514"
    threshold: int = 7
    batch_size: int = 8               # pass 2: summarisation batch size
    score_batch_size: int = 24        # pass 1: scoring-only batch size
    abstract_truncation_words: int = 150  # pass 1: max words per abstract


@dataclass
class DigestConfig:
    schedule: str = "daily"
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
    arxiv: ArxivConfig = field(default_factory=lambda: ArxivConfig(categories=[]))
    interests: str = ""
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    anthropic_api_key: str = ""


_VALID_SCHEDULES = ("daily", "weekly", "fortnightly", "monthly")


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """Load and validate configuration from config.yaml and .env.

    Raises ValueError for missing or invalid required fields.
    """
    load_dotenv(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Config file is empty: {config_path}")

    # Parse arxiv section
    arxiv_raw = raw.get("arxiv", {})
    categories = arxiv_raw.get("categories", [])
    if not categories:
        raise ValueError("config.yaml: arxiv.categories must be a non-empty list")
    arxiv_config = ArxivConfig(
        categories=categories,
        max_results=arxiv_raw.get("max_results", 100),
    )

    # Parse interests
    interests = raw.get("interests", "").strip()
    if not interests:
        raise ValueError("config.yaml: interests must be a non-empty string")

    # Parse scoring section
    scoring_raw = raw.get("scoring", {})
    scoring_config = ScoringConfig(
        model=scoring_raw.get("model", "claude-sonnet-4-20250514"),
        threshold=scoring_raw.get("threshold", 7),
        batch_size=scoring_raw.get("batch_size", 8),
        score_batch_size=scoring_raw.get("score_batch_size", 24),
        abstract_truncation_words=scoring_raw.get("abstract_truncation_words", 150),
    )

    # Parse digest section
    digest_raw = raw.get("digest", {})
    schedule = digest_raw.get("schedule", "daily")
    if schedule not in _VALID_SCHEDULES:
        raise ValueError(
            f"config.yaml: digest.schedule must be one of {_VALID_SCHEDULES}, got '{schedule}'"
        )
    output_dir = digest_raw.get("output_dir", "output")
    min_papers_to_email = int(digest_raw.get("min_papers_to_email", 3))
    if min_papers_to_email < 1:
        raise ValueError("config.yaml: digest.min_papers_to_email must be >= 1")
    os.makedirs(output_dir, exist_ok=True)
    digest_config = DigestConfig(
        schedule=schedule,
        output_dir=output_dir,
        min_papers_to_email=min_papers_to_email,
    )

    # Parse environment variables
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_api_key:
        raise ValueError(".env: ANTHROPIC_API_KEY is required")

    smtp_config = SmtpConfig(
        host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=os.environ.get("SMTP_USER", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        recipient=os.environ.get("RECIPIENT_EMAIL", ""),
    )

    return AppConfig(
        arxiv=arxiv_config,
        interests=interests,
        scoring=scoring_config,
        digest=digest_config,
        smtp=smtp_config,
        anthropic_api_key=anthropic_api_key,
    )
