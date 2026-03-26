"""Claude API scoring and summarisation of arXiv papers."""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from random import random

import anthropic
from tqdm import tqdm

from src.config import AppConfig
from src.fetcher import Paper

logger = logging.getLogger("arXivAlert.scorer")

CACHE_FILE = "paper_cache.json"

SYSTEM_PROMPT_SCORE = """\
You are a research paper relevance scorer. You will be given a list of \
arXiv paper titles and truncated abstracts, along with the user's research interests. \
For each paper, assess its relevance to the user's interests.

Scoring guidelines:
- 9-10: Directly in the user's core research area, likely must-read
- 7-8: Closely related, techniques or results applicable to user's work
- 5-6: Tangentially related, interesting but not essential
- 3-4: Loosely connected, different subfield
- 1-2: Not relevant to the user's interests

You MUST respond with valid JSON only — no markdown, no commentary. \
The JSON must be an array of objects in the same order as the papers presented. \
Each object must have exactly these fields:
- "index": integer (1-indexed, matching the input order)
- "score": integer 1-10\
"""

SYSTEM_PROMPT_SUMMARISE = """\
You are a research paper summariser. You will be given a list of \
arXiv paper abstracts that have already been identified as relevant, \
along with the user's research interests. For each paper, provide:

- A 2-3 sentence plain-English summary of the paper's contribution
- A 1-sentence explanation of why it is relevant to the user's interests

You MUST respond with valid JSON only — no markdown, no commentary. \
The JSON must be an array of objects in the same order as the papers presented. \
Each object must have exactly these fields:
- "index": integer (1-indexed, matching the input order)
- "summary": string (2-3 sentences)
- "reason": string (1 sentence explaining the relevance)\
"""

SYSTEM_PROMPT_DIGEST_OVERVIEW = """\
You are a research digest editor. You will be given a list of relevant papers \
and the user's research interests.

Write a short, high-level overview of the digest as a whole (1–2 sentences). \
Do NOT list or describe each paper individually. Focus on themes and trends \
across the set.

You MUST respond with valid JSON only — no markdown, no commentary. \
The JSON must have exactly this field:
- "overview": string (1–2 sentences)\
"""


@dataclass
class ScoredPaper:
    paper: Paper
    score: int
    summary: str
    relevance_reason: str


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_message(cls, message: object) -> "TokenUsage":
        usage = getattr(message, "usage", None)
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens

    @classmethod
    def from_dict(cls, data: dict) -> "TokenUsage":
        return cls(
            input_tokens=int(data.get("input_tokens", 0) or 0),
            output_tokens=int(data.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(data.get("cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(data.get("cache_read_input_tokens", 0) or 0),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# Paper cache
# ---------------------------------------------------------------------------

def load_paper_cache(path: str = CACHE_FILE) -> dict[str, dict]:
    """Load the paper cache from disk. Returns empty dict if missing or corrupt."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read paper cache (%s), starting fresh: %s", path, e)
        return {}


def save_paper_cache(cache: dict[str, dict], path: str = CACHE_FILE) -> None:
    """Persist the paper cache to disk."""
    try:
        Path(path).write_text(json.dumps(cache, indent=2) + "\n")
    except OSError as e:
        logger.error("Failed to save paper cache: %s", e)


def clear_paper_cache(path: str = CACHE_FILE) -> None:
    """Remove all entries from the paper cache."""
    try:
        Path(path).write_text("{}\n")
        logger.info("Cleared paper cache at %s", path)
    except OSError as e:
        logger.error("Failed to clear paper cache: %s", e)


def _paper_to_cache(p: Paper) -> dict:
    published = p.published
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return {
        "arxiv_id": p.arxiv_id,
        "title": p.title,
        "authors": p.authors,
        "abstract": p.abstract,
        "categories": p.categories,
        "primary_category": p.primary_category,
        "published": published.isoformat(),
        "pdf_url": p.pdf_url,
        "abs_url": p.abs_url,
    }


def _paper_from_cache(data: dict) -> Paper | None:
    try:
        published = datetime.fromisoformat(data["published"])
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return Paper(
            arxiv_id=str(data["arxiv_id"]),
            title=str(data["title"]),
            authors=list(data["authors"]),
            abstract=str(data.get("abstract", "")),
            categories=list(data["categories"]),
            primary_category=str(data.get("primary_category", "")),
            published=published,
            pdf_url=str(data["pdf_url"]),
            abs_url=str(data["abs_url"]),
        )
    except Exception:
        return None


def load_pending_scored_papers(
    threshold: int,
    path: str = CACHE_FILE,
) -> list["ScoredPaper"]:
    """Load all cached papers that are ready to be emailed (emailed=false, above threshold, summary present)."""
    cache = load_paper_cache(path)
    pending: list[ScoredPaper] = []
    for entry in cache.values():
        if entry.get("emailed", False):
            continue
        score = entry.get("score")
        if score is None or int(score) < threshold:
            continue
        summary = entry.get("summary")
        reason = entry.get("reason")
        if not summary or not reason:
            continue
        paper_data = entry.get("paper")
        if not isinstance(paper_data, dict):
            continue
        paper = _paper_from_cache(paper_data)
        if paper is None:
            continue
        pending.append(
            ScoredPaper(
                paper=paper,
                score=int(score),
                summary=str(summary),
                relevance_reason=str(reason),
            )
        )

    pending.sort(key=lambda s: (s.score, s.paper.published), reverse=True)
    return pending


def mark_papers_emailed(arxiv_ids: list[str], path: str = CACHE_FILE) -> None:
    """Mark papers as emailed so they are excluded from future digests."""
    cache = load_paper_cache(path)
    for aid in arxiv_ids:
        if aid in cache:
            cache[aid]["emailed"] = True
        else:
            # Paper was in a digest but not in cache — shouldn't happen, but handle gracefully
            cache[aid] = {"score": None, "summary": None, "reason": None, "emailed": True}
    save_paper_cache(cache, path)
    logger.info("Marked %d papers as emailed in cache", len(arxiv_ids))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _truncate_abstract(abstract: str, max_words: int) -> str:
    """Truncate abstract to max_words words, appending '[...]' if cut."""
    words = abstract.split()
    if len(words) <= max_words:
        return abstract
    return " ".join(words[:max_words]) + " [...]"


def _make_user_blocks(interests: str, papers_text: str) -> list[dict]:
    """Build two content blocks for a user message.

    The interests block is marked for prompt caching (static across all calls).
    The papers block changes per batch and is not cached.
    """
    return [
        {
            "type": "text",
            "text": f"My research interests are:\n{interests}\n",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": papers_text,
        },
    ]


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_papers_for_scoring(papers: list[Paper], max_words: int) -> str:
    """Build the papers listing for pass 1 (title + truncated abstract, no authors)."""
    lines = [f"Please score the following {len(papers)} papers:\n"]
    for i, p in enumerate(papers, 1):
        truncated = _truncate_abstract(p.abstract, max_words)
        lines.append(f"###\n{i}. Title: {p.title}")
        lines.append(f"{i}. Abstract: {truncated}\n")
    return "\n".join(lines)


def _build_papers_for_summarising(papers: list[Paper]) -> str:
    """Build the papers listing for pass 2 (title + full abstract, no authors)."""
    lines = [f"Please summarise the following {len(papers)} papers:\n"]
    for i, p in enumerate(papers, 1):
        lines.append(f"###\n{i}. Title: {p.title}")
        lines.append(f"{i}. Abstract: {p.abstract}\n")
    return "\n".join(lines)


def _build_papers_for_overview(scored_papers: list["ScoredPaper"], max_items: int = 30) -> str:
    """Build a compact listing for digest-level overview generation."""
    selected = scored_papers[:max_items]
    lines = [f"Please write a digest overview for {len(selected)} papers:\n"]
    for i, sp in enumerate(selected, 1):
        lines.append(f"###\n{i}. Title: {sp.paper.title}")
        lines.append(f"{i}. Score: {sp.score}/10")
        lines.append(f"{i}. Summary: {sp.summary}")
        lines.append(f"{i}. Relevance: {sp.relevance_reason}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def _call_claude(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    user_blocks: list[dict],
    max_tokens: int,
) -> tuple[str | None, TokenUsage]:
    """Call Claude with retry/backoff.

    Returns (response_text_or_none, token_usage_for_this_call).

    The system prompt is passed with cache_control so it is cached after the
    first call. The user_blocks list may also contain cached blocks (interests).
    """
    def _retry_after_seconds(err: Exception) -> float | None:
        resp = getattr(err, "response", None)
        if resp is None:
            return None
        headers = getattr(resp, "headers", None)
        if not headers:
            return None
        value = headers.get("retry-after")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    max_attempts = 8
    for attempt in range(max_attempts):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=0.3,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_blocks}],
            )
            return response.content[0].text, TokenUsage.from_message(response)
        except anthropic.RateLimitError as e:
            retry_after = _retry_after_seconds(e)
            base_wait = min(60, 2 ** attempt)
            wait = max(base_wait, int(retry_after) if retry_after is not None else 0)
            wait = wait + (random() * min(1.0, wait * 0.25))
            logger.warning(
                "Rate limited (attempt %d/%d) — retrying in %.1fs",
                attempt + 1,
                max_attempts,
                wait,
            )
            time.sleep(wait)
        except anthropic.APIError as e:
            wait = min(30, 2 ** attempt)
            logger.warning(
                "API error (attempt %d/%d): %s — retrying in %ds",
                attempt + 1,
                max_attempts,
                e,
                wait,
            )
            time.sleep(wait)
    logger.error("Failed after %d attempts, skipping batch", max_attempts)
    return None, TokenUsage()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_scores(text: str, batch_len: int) -> dict[int, int]:
    """Parse pass 1 JSON response into {local 0-indexed paper index: score}."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        entries = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse score response as JSON: %s", e)
        logger.debug("Raw response: %s", text[:500])
        return {}

    if not isinstance(entries, list):
        logger.error("Expected JSON array for scores, got %s", type(entries).__name__)
        return {}

    result: dict[int, int] = {}
    for entry in entries:
        try:
            idx = int(entry["index"]) - 1
            if idx < 0 or idx >= batch_len:
                logger.warning("Score index %d out of range, skipping", idx + 1)
                continue
            result[idx] = int(entry["score"])
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed score entry: %s — %s", entry, e)
    return result


def _parse_summaries(text: str, batch_len: int) -> dict[int, tuple[str, str]]:
    """Parse pass 2 JSON response into {local 0-indexed paper index: (summary, reason)}."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        entries = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse summary response as JSON: %s", e)
        logger.debug("Raw response: %s", text[:500])
        return {}

    if not isinstance(entries, list):
        logger.error("Expected JSON array for summaries, got %s", type(entries).__name__)
        return {}

    result: dict[int, tuple[str, str]] = {}
    for entry in entries:
        try:
            idx = int(entry["index"]) - 1
            if idx < 0 or idx >= batch_len:
                logger.warning("Summary index %d out of range, skipping", idx + 1)
                continue
            result[idx] = (str(entry["summary"]), str(entry["reason"]))
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed summary entry: %s — %s", entry, e)
    return result


def _parse_overview(text: str) -> str | None:
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse overview response as JSON: %s", e)
        logger.debug("Raw response: %s", text[:500])
        return None
    if not isinstance(payload, dict) or "overview" not in payload:
        logger.error("Expected JSON object with 'overview' field for digest overview")
        return None
    overview = str(payload["overview"]).strip()
    return overview if overview else None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_digest_overview(
    scored_papers: list[ScoredPaper],
    config: AppConfig,
) -> tuple[str, TokenUsage]:
    """Generate a short, digest-level overview paragraph via Claude."""
    if not scored_papers:
        return "", TokenUsage()

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    sc = config.scoring
    interests = config.interests

    papers_text = _build_papers_for_overview(scored_papers)
    user_blocks = _make_user_blocks(interests, papers_text)
    text, usage = _call_claude(
        client,
        sc.model,
        SYSTEM_PROMPT_DIGEST_OVERVIEW,
        user_blocks,
        max_tokens=160,
    )
    if text is None:
        return "", usage
    overview = _parse_overview(text)
    return (overview or ""), usage


def score_papers(papers: list[Paper], config: AppConfig) -> tuple[list[ScoredPaper], TokenUsage]:
    """Score papers in two passes using Claude, with a persistent cache.

    Pass 0: Filter out already-emailed papers and split remaining into
            cached (score known) vs uncached (need API).
    Pass 1: Score uncached papers using truncated abstracts in large batches.
            Output is only {index, score} — minimal tokens.
    Pass 2: Summarise only papers above the threshold that lack a cached summary.
            Uses full abstracts in smaller batches.

    Returns papers meeting the threshold, sorted by score descending.
    """
    if not papers:
        return [], TokenUsage()

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    sc = config.scoring
    interests = config.interests
    total_usage = TokenUsage()

    # ------------------------------------------------------------------ #
    # Pre-filter: exclude emailed papers, split cached vs uncached        #
    # ------------------------------------------------------------------ #
    cache = load_paper_cache()

    eligible = [p for p in papers if not cache.get(p.arxiv_id, {}).get("emailed", False)]
    emailed_count = len(papers) - len(eligible)
    if emailed_count:
        logger.info("Skipping %d previously emailed papers", emailed_count)

    needs_scoring: list[Paper] = []
    already_scored: list[Paper] = []
    for p in eligible:
        if p.arxiv_id in cache:
            # Backfill paper metadata for older cache entries.
            cache[p.arxiv_id].setdefault("paper", _paper_to_cache(p))
            already_scored.append(p)
        else:
            needs_scoring.append(p)

    logger.info(
        "%d papers total: %d need scoring, %d have cached scores",
        len(eligible), len(needs_scoring), len(already_scored),
    )

    # ------------------------------------------------------------------ #
    # PASS 1 — Score uncached papers (truncated abstracts, large batches) #
    # ------------------------------------------------------------------ #
    paper_scores: dict[int, int] = {}  # index into needs_scoring -> score

    if needs_scoring:
        score_batches = [
            needs_scoring[i:i + sc.score_batch_size]
            for i in range(0, len(needs_scoring), sc.score_batch_size)
        ]

        for batch_start, batch in zip(
            range(0, len(needs_scoring), sc.score_batch_size),
            tqdm(score_batches, desc="Pass 1: Scoring", unit="batch"),
        ):
            papers_text = _build_papers_for_scoring(batch, sc.abstract_truncation_words)
            user_blocks = _make_user_blocks(interests, papers_text)
            text, usage = _call_claude(
                client, sc.model, SYSTEM_PROMPT_SCORE, user_blocks,
                max_tokens=30 * len(batch),
            )
            total_usage.add(usage)
            if text is None:
                continue

            for local_idx, score in _parse_scores(text, len(batch)).items():
                paper_scores[batch_start + local_idx] = score
                # Cache the score immediately (summary added in pass 2 if above threshold)
                cache[needs_scoring[batch_start + local_idx].arxiv_id] = {
                    "score": score,
                    "summary": None,
                    "reason": None,
                    "emailed": False,
                    "paper": _paper_to_cache(needs_scoring[batch_start + local_idx]),
                }

            if len(score_batches) > 1:
                time.sleep(0.5)

    # ------------------------------------------------------------------ #
    # Build survivors list                                                 #
    # ------------------------------------------------------------------ #
    # Papers needing pass 2 (above threshold, no cached summary yet)
    survivors: list[tuple[Paper, int]] = []
    # Papers with a full cached entry (skip pass 2 entirely)
    cached_with_summary: list[tuple[Paper, int, str, str]] = []

    # Newly scored
    for i, score in paper_scores.items():
        if score >= sc.threshold:
            survivors.append((needs_scoring[i], score))

    # Cache hits
    for p in already_scored:
        entry = cache[p.arxiv_id]
        score = entry["score"]
        if score is not None and score >= sc.threshold:
            if entry.get("summary"):
                cached_with_summary.append((p, score, entry["summary"], entry["reason"]))
            else:
                survivors.append((p, score))  # cached score but no summary yet

    logger.info(
        "Pass 1 complete: %d above threshold (%d), %d from cache with summaries",
        len(survivors), sc.threshold, len(cached_with_summary),
    )

    # If pass 1 made lots of back-to-back requests, pass 2 can immediately hit
    # account rate limits. A short cooldown avoids the first pass-2 call failing.
    if needs_scoring and survivors:
        time.sleep(3.0)

    # ------------------------------------------------------------------ #
    # PASS 2 — Summarise survivors (full abstracts, original batch size)  #
    # ------------------------------------------------------------------ #
    summaries: dict[int, tuple[str, str]] = {}  # survivor-local index -> (summary, reason)

    if survivors:
        sum_batches = [
            survivors[i:i + sc.batch_size]
            for i in range(0, len(survivors), sc.batch_size)
        ]

        for batch_start, batch in zip(
            range(0, len(survivors), sc.batch_size),
            tqdm(sum_batches, desc="Pass 2: Summarising", unit="batch"),
        ):
            batch_papers = [p for p, _ in batch]
            papers_text = _build_papers_for_summarising(batch_papers)
            user_blocks = _make_user_blocks(interests, papers_text)
            text, usage = _call_claude(
                client, sc.model, SYSTEM_PROMPT_SUMMARISE, user_blocks,
                max_tokens=250 * len(batch_papers),
            )
            total_usage.add(usage)
            if text is None:
                continue

            for local_idx, pair in _parse_summaries(text, len(batch_papers)).items():
                summaries[batch_start + local_idx] = pair

            if len(sum_batches) > 1:
                time.sleep(0.5)

    # ------------------------------------------------------------------ #
    # Assembly + cache update                                              #
    # ------------------------------------------------------------------ #
    results: list[ScoredPaper] = []

    for i, (paper, score) in enumerate(survivors):
        if i not in summaries:
            logger.warning("No summary generated for '%s', skipping", paper.title)
            continue
        summary, reason = summaries[i]
        cache[paper.arxiv_id]["summary"] = summary
        cache[paper.arxiv_id]["reason"] = reason
        results.append(ScoredPaper(
            paper=paper,
            score=score,
            summary=summary,
            relevance_reason=reason,
        ))

    for paper, score, summary, reason in cached_with_summary:
        results.append(ScoredPaper(
            paper=paper,
            score=score,
            summary=summary,
            relevance_reason=reason,
        ))

    save_paper_cache(cache)

    results.sort(key=lambda s: s.score, reverse=True)

    logger.info(
        "Pass 2 complete: %d papers with summaries returned",
        len(results),
    )
    return results, total_usage
