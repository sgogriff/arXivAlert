"""arXivAlert — orchestration entry point."""

import argparse
import logging
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import load_config
from src.fetcher import calculate_date_range, fetch_papers, load_last_run, save_last_run
from src.formatter import generate_digest, generate_plain_text
from src.fx import convert_usd, fetch_rates
from src.mailer import send_digest
from src.pricing import estimate_cost_usd
from src.scorer import (
    TokenUsage,
    clear_paper_cache,
    generate_digest_overview,
    load_pending_scored_papers,
    mark_papers_emailed,
    score_papers,
)
from src.usage_state import clear_usage_state, load_usage_state, save_usage_state


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="arXivAlert: AI-curated arXiv digest powered by Claude"
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
        help="Ignore last_run.json and fetch max_results papers per category (useful for testing)",
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Clear paper_cache.json (and token usage state) before running",
    )
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("arXivAlert")

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

    # 2. Calculate date range
    if args.forcemax:
        now = datetime.now(timezone.utc)
        date_range = (now - timedelta(days=365), now)
        logger.info("--forcemax: ignoring last_run.json, looking back 365 days")
    else:
        last_run = load_last_run()
        date_range = calculate_date_range(config.digest.schedule, last_run)
    logger.info(
        "Fetching papers from %s to %s",
        date_range[0].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        date_range[1].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

    # 3. Fetch papers (new since last run)
    papers = fetch_papers(config.arxiv.categories, config.arxiv.max_results, date_range)
    logger.info("Fetched %d papers", len(papers))

    # 4. Score with Claude (new papers only; pending items are loaded from cache later)
    scored_new: list = []
    run_usage = TokenUsage()
    if papers:
        scored_new, run_usage = score_papers(papers, config)
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

    # 5. Accumulate usage + scanned count until we have enough papers to send
    state = load_usage_state()
    if state.window_start is None:
        state.window_start = date_range[0]
    state.total_scanned += len(papers)
    state.usage_by_model.setdefault(config.scoring.model, TokenUsage()).add(run_usage)
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

    # 6. Build + (optionally) send digest using accumulated cached papers
    now = datetime.now(timezone.utc)
    digest_range = (state.window_start or date_range[0], now)

    digest_ids = sorted([sp.paper.arxiv_id for sp in pending])
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

    html, filepath = generate_digest(
        pending,
        digest_range,
        config.digest.output_dir,
        total_scanned=state.total_scanned,
        digest_overview=digest_overview,
        token_usage=total_usage,
        cost_usd=cost_usd,
        cost_gbp=cost_gbp,
        cost_eur=cost_eur,
        model_name=config.scoring.model,
    )
    plain = generate_plain_text(
        pending,
        digest_range,
        digest_overview=digest_overview,
        token_usage=total_usage,
        cost_usd=cost_usd,
        cost_gbp=cost_gbp,
        cost_eur=cost_eur,
        model_name=config.scoring.model,
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

    # 7. If we successfully emailed, reset the accumulation window + token counter
    if emailed:
        clear_usage_state()
        logger.info("Reset accumulated token usage after successful email")

    # 8. Update last run
    save_last_run(now)
    logger.info("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
