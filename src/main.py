"""Paper Press — orchestration entry point."""

import argparse
import logging
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import load_config
from src.fetcher import Paper, calculate_date_range, load_last_run, save_last_run
from src.fetchers import fetch_all_papers
from src.formatter import generate_digest, generate_plain_text
from src.fx import convert_usd, fetch_rates
from src.mailer import send_digest
from src.pricing import estimate_cost_usd
from src.scorer import (
    AnalysisStats,
    ScoredPaper,
    TokenUsage,
    build_analysis_stats,
    clear_paper_cache,
    generate_digest_overview,
    load_pending_scored_papers,
    mark_papers_emailed,
    score_papers,
)
from src.usage_state import clear_usage_state, load_usage_state, save_usage_state

# Lookback schedule for --forcemin: cumulative days to expand window
FORCEMIN_LOOKBACK_DAYS = [1, 3, 7, 14, 30, 60, 90]


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _create_placeholder_papers() -> list[ScoredPaper]:
    """Create placeholder papers for testing the email template."""
    now = datetime.now(timezone.utc)
    placeholders = [
        ScoredPaper(
            paper=Paper(
                paper_id="2024.01234",
                source="arXiv",
                record_kind="Preprint",
                version_stage="v1",
                doi="10.48550/arXiv.2024.01234",
                openalex_id=None,
                title="Advanced Techniques in Large Language Model Optimization and Inference",
                authors=["Alice Smith", "Bob Johnson", "Carol Williams", "David Brown"],
                abstract="This paper presents novel optimization techniques for training large language models at scale. We introduce several architectural improvements and training strategies that reduce memory footprint by 40% while maintaining performance.",
                categories=["cs.LG", "cs.CL"],
                primary_category="cs.LG",
                published=now - timedelta(days=2),
                pdf_url="https://arxiv.org/pdf/2024.01234.pdf",
                abs_url="https://arxiv.org/abs/2024.01234",
            ),
            score=9,
            summary="Proposes novel optimization techniques for LLM training that reduce memory footprint by 40%. Introduces architectural improvements applicable across different model families.",
            relevance_reason="Directly addresses efficient scaling techniques relevant to practical model deployment.",
        ),
        ScoredPaper(
            paper=Paper(
                paper_id="2024.05678",
                source="arXiv",
                record_kind="Preprint",
                version_stage="v2",
                doi="10.48550/arXiv.2024.05678",
                openalex_id=None,
                title="Multimodal Learning: Bridging Vision and Language Understanding",
                authors=["Elena Rodriguez", "Frank Martinez"],
                abstract="We present a comprehensive framework for multimodal learning that effectively combines visual and textual information. Our approach achieves state-of-the-art results on multiple benchmarks.",
                categories=["cs.CV", "cs.LG"],
                primary_category="cs.CV",
                published=now - timedelta(days=1),
                pdf_url="https://arxiv.org/pdf/2024.05678.pdf",
                abs_url="https://arxiv.org/abs/2024.05678",
            ),
            score=8,
            summary="Presents a framework combining visual and textual information for multimodal understanding. Achieves SOTA on multiple benchmarks including ImageNet and MS-COCO variants.",
            relevance_reason="Explores the intersection of vision and language, relevant to cross-modal representation learning.",
        ),
        ScoredPaper(
            paper=Paper(
                paper_id="2024.91011",
                source="arXiv",
                record_kind="Preprint",
                version_stage="v1",
                doi="10.48550/arXiv.2024.91011",
                openalex_id=None,
                title="Efficient Attention Mechanisms for Transformer Models",
                authors=["Grace Lee"],
                abstract="This work introduces a novel attention mechanism that reduces computational complexity from O(n²) to O(n log n) while maintaining competitive performance on standard benchmarks.",
                categories=["cs.LG"],
                primary_category="cs.LG",
                published=now - timedelta(days=3),
                pdf_url="https://arxiv.org/pdf/2024.91011.pdf",
                abs_url="https://arxiv.org/abs/2024.91011",
            ),
            score=7,
            summary="Introduces an efficient attention mechanism reducing computational complexity from O(n²) to O(n log n). Maintains competitive performance on GLUE and SuperGLUE benchmarks.",
            relevance_reason="Offers practical efficiency improvements for transformer-based models.",
        ),
    ]
    return placeholders


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Paper Press: AI-curated research digest powered by Claude"
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Path to config file (default: config.yaml)"
    )
    parser.add_argument(
        "--no-email", action="store_true", help="Skip email delivery, only save HTML digest"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch and score only — don't save digest or send email"
    )
    parser.add_argument(
        "--forcemax", "--force-fetch", "--force-max", action="store_true",
        help="Ignore last_run.json and fetch up to each source's max_results (useful for testing)",
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Clear paper_cache.json (and token usage state) before running",
    )
    parser.add_argument(
        "--test-email", action="store_true",
        help="Generate and send a test email digest (uses cached papers or placeholders)",
    )
    parser.add_argument(
        "--forcemin", action="store_true",
        help="Iteratively expand lookback window until min_papers_to_email are ready. Caps at 90 days.",
    )
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("paperpress")

    # Mutual-exclusion checks
    if args.forcemax and args.forcemin:
        logger.error("--forcemax and --forcemin are mutually exclusive")
        return 1
    if args.forcemin and args.dry_run:
        logger.error("--forcemin and --dry-run cannot be used together")
        return 1

    if args.clear_cache:
        clear_paper_cache()
        clear_usage_state()
        try:
            Path("last_run.json").unlink(missing_ok=True)
            logger.info("Cleared last run state at last_run.json")
        except OSError as e:
            logger.warning("Failed to clear last_run.json: %s", e)

    # 1. Load config
    try:
        config = load_config(args.config)
    except (ValueError, FileNotFoundError) as e:
        logger.error("Configuration error: %s", e)
        return 1

    # 2. Handle --test-email mode (skip pipeline, use cache or placeholders)
    if args.test_email:
        logger.info("--test-email: generating and sending test digest")
        now = datetime.now(timezone.utc)

        # Try to load cached papers; fall back to placeholders
        pending = load_pending_scored_papers(config.scoring.threshold)
        if not pending:
            logger.info("No cached papers found, using placeholder papers")
            pending = _create_placeholder_papers()

        logger.info("Using %d papers for test digest", len(pending))

        # Generate digest with minimal metadata
        digest_range = (now - timedelta(days=7), now)
        html, filepath = generate_digest(
            pending,
            digest_range,
            config.digest.output_dir,
            total_scanned=len(pending),
            digest_overview="Test digest generated for email template verification.",
            token_usage=TokenUsage(),
            analysis_stats=build_analysis_stats(digest_range[0], pending),
            cost_usd=0.0,
            cost_gbp=0.0,
            cost_eur=0.0,
            model_name="test",
            threshold=config.scoring.threshold,
        )
        plain = generate_plain_text(
            pending,
            digest_range,
            digest_overview="Test digest generated for email template verification.",
            token_usage=TokenUsage(),
            analysis_stats=build_analysis_stats(digest_range[0], pending),
            cost_usd=0.0,
            cost_gbp=0.0,
            cost_eur=0.0,
            model_name="test",
            threshold=config.scoring.threshold,
        )
        logger.info("Digest written to %s", filepath)

        if send_digest(html, plain, digest_range, config.smtp):
            logger.info("Test email sent successfully")
            return 0
        else:
            logger.error("Test email delivery failed")
            return 1

    # 4. Calculate date range / handle --forcemin
    if args.forcemax:
        now = datetime.now(timezone.utc)
        date_range = (now - timedelta(days=365), now)
        logger.info("--forcemax: ignoring last_run.json, looking back 365 days")

        # 5. Fetch papers (new since last run)
        papers = fetch_all_papers(config, date_range)
        logger.info("Fetched %d papers", len(papers))

        # 6. Score with Claude (new papers only; pending items are loaded from cache later)
        scored_new: list = []
        run_usage = TokenUsage()
        run_stats = AnalysisStats()
        if papers:
            scored_new, run_usage, run_stats = score_papers(papers, config)
            logger.info("%d new papers above threshold", len(scored_new))

        if args.dry_run:
            logger.info("Dry run — skipping digest generation and email")
            for item in scored_new:
                logger.info("  [%d/10] %s", item.score, item.paper.title)
            logger.info(
                "Token usage: %d in / %d out (total %d)",
                run_usage.input_tokens,
                run_usage.output_tokens,
                run_usage.total_tokens,
            )
            return 0

        # 7. Accumulate usage + scanned count until we have enough papers to send
        state = load_usage_state()
        if state.window_start is None:
            state.window_start = date_range[0]
        state.total_scanned += len(papers)
        state.usage_by_model.setdefault(config.scoring.model, TokenUsage()).add(run_usage)
        state.analysis_stats.add(run_stats)
        save_usage_state(state)

    elif args.forcemin:
        # Iteratively expand lookback window until min_papers_to_email threshold met
        now = datetime.now(timezone.utc)
        min_ready = config.digest.min_papers_to_email
        state = load_usage_state()
        earliest_start: datetime | None = None

        for step_idx, total_days in enumerate(FORCEMIN_LOOKBACK_DAYS):
            prev_days = FORCEMIN_LOOKBACK_DAYS[step_idx - 1] if step_idx > 0 else 0
            window_start = now - timedelta(days=total_days)
            window_end = now - timedelta(days=prev_days)
            earliest_start = window_start

            logger.info(
                "--forcemin step %d/%d: fetching %s → %s (cumulative %d days)",
                step_idx + 1,
                len(FORCEMIN_LOOKBACK_DAYS),
                window_start.strftime("%Y-%m-%d %H:%M UTC"),
                window_end.strftime("%Y-%m-%d %H:%M UTC"),
                total_days,
            )

            iter_papers = fetch_all_papers(config, (window_start, window_end))
            logger.info("  %d papers in slice", len(iter_papers))

            iter_usage = TokenUsage()
            iter_stats = AnalysisStats()
            if iter_papers:
                _, iter_usage, iter_stats = score_papers(iter_papers, config)

            # Unconditional overwrite — we expand backwards, so this always gets earlier
            state.window_start = window_start
            state.total_scanned += len(iter_papers)
            state.usage_by_model.setdefault(config.scoring.model, TokenUsage()).add(iter_usage)
            state.analysis_stats.add(iter_stats)
            save_usage_state(state)

            pending = load_pending_scored_papers(config.scoring.threshold)
            logger.info(
                "  --forcemin: %d/%d papers ready (cumulative %d-day lookback)",
                len(pending),
                min_ready,
                total_days,
            )

            if len(pending) >= min_ready:
                logger.info("--forcemin: threshold met — proceeding to digest")
                break

            if step_idx == len(FORCEMIN_LOOKBACK_DAYS) - 1:
                logger.warning(
                    "--forcemin: 90-day cap reached with %d/%d papers — proceeding anyway",
                    len(pending),
                    min_ready,
                )

        date_range = (earliest_start or (now - timedelta(days=90)), now)

    else:
        last_run = load_last_run()
        date_range = calculate_date_range(config.digest.initial_search_window, last_run)
        logger.info(
            "Fetching papers from %s to %s",
            date_range[0].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            date_range[1].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

        # 5. Fetch papers (new since last run)
        papers = fetch_all_papers(config, date_range)
        logger.info("Fetched %d papers", len(papers))

        # 6. Score with Claude (new papers only; pending items are loaded from cache later)
        scored_new: list = []
        run_usage = TokenUsage()
        run_stats = AnalysisStats()
        if papers:
            scored_new, run_usage, run_stats = score_papers(papers, config)
            logger.info("%d new papers above threshold", len(scored_new))

        if args.dry_run:
            logger.info("Dry run — skipping digest generation and email")
            for item in scored_new:
                logger.info("  [%d/10] %s", item.score, item.paper.title)
            logger.info(
                "Token usage: %d in / %d out (total %d)",
                run_usage.input_tokens,
                run_usage.output_tokens,
                run_usage.total_tokens,
            )
            return 0

        # 7. Accumulate usage + scanned count until we have enough papers to send
        state = load_usage_state()
        if state.window_start is None:
            state.window_start = date_range[0]
        state.total_scanned += len(papers)
        state.usage_by_model.setdefault(config.scoring.model, TokenUsage()).add(run_usage)
        state.analysis_stats.add(run_stats)
        save_usage_state(state)

        pending = load_pending_scored_papers(config.scoring.threshold)
        min_ready = config.digest.min_papers_to_email
        if len(pending) < min_ready:
            logger.info(
                "Not emailing yet: %d/%d papers ready (accumulating in paper_cache.json)",
                len(pending),
                min_ready,
            )
            save_last_run(datetime.now(timezone.utc))
            return 0

    # 8. Build + (optionally) send digest using accumulated cached papers
    now = datetime.now(timezone.utc)
    digest_range = (state.window_start or date_range[0], now)

    digest_ids = sorted([f"{sp.paper.source}:{sp.paper.paper_id}" for sp in pending])
    digest_key = hashlib.sha256(",".join(digest_ids).encode("utf-8")).hexdigest()

    digest_overview = state.overview_text
    if state.overview_key != digest_key or not digest_overview:
        digest_overview, overview_usage = generate_digest_overview(pending, config)
        state.overview_key = digest_key
        state.overview_text = digest_overview
        state.usage_by_model.setdefault(config.scoring.model, TokenUsage()).add(overview_usage)
        save_usage_state(state)

    total_usage = state.total_usage()
    cost_usd_total = 0.0
    missing_pricing = False
    for model, usage in state.usage_by_model.items():
        c, _pricing = estimate_cost_usd(model, usage)
        if c is None:
            missing_pricing = True
        else:
            cost_usd_total += c
    cost_usd = None if missing_pricing else cost_usd_total
    cost_gbp = None
    cost_eur = None
    if cost_usd is not None:
        rates = fetch_rates()
        cost_gbp, cost_eur, cost_usd = convert_usd(cost_usd, rates)

    chart_stats = build_analysis_stats(digest_range[0], pending)

    html, filepath = generate_digest(
        pending,
        digest_range,
        config.digest.output_dir,
        total_scanned=state.total_scanned,
        digest_overview=digest_overview,
        token_usage=total_usage,
        analysis_stats=chart_stats,
        cost_usd=cost_usd,
        cost_gbp=cost_gbp,
        cost_eur=cost_eur,
        model_name=config.scoring.model,
        threshold=config.scoring.threshold,
    )
    plain = generate_plain_text(
        pending,
        digest_range,
        digest_overview=digest_overview,
        token_usage=total_usage,
        analysis_stats=chart_stats,
        cost_usd=cost_usd,
        cost_gbp=cost_gbp,
        cost_eur=cost_eur,
        model_name=config.scoring.model,
        threshold=config.scoring.threshold,
    )
    logger.info("Digest written to %s", filepath)

    emailed = False
    if not args.no_email:
        if send_digest(html, plain, digest_range, config.smtp):
            emailed = True
            logger.info("Email sent successfully")
            mark_papers_emailed(digest_ids)
        else:
            logger.warning("Email delivery failed — digest is still available at %s", filepath)
    else:
        logger.info("--no-email: skipping SMTP delivery")

    # 9. If we successfully emailed, reset the accumulation window + token counter
    if emailed:
        clear_usage_state()
        logger.info("Reset accumulated token usage after successful email")

    # 10. Update last run
    save_last_run(now)
    logger.info("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
