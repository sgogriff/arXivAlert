"""HTML digest generation from scored papers."""

import base64
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.scorer import AnalysisStats, ScoredPaper, TokenUsage

logger = logging.getLogger("paperpress.formatter")


def _get_template_dir() -> str:
    """Resolve templates directory relative to project root."""
    # Works whether run from project root or via python -m src.main
    here = Path(__file__).resolve().parent.parent
    return str(here / "templates")


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _scaled_bar_height(count: int, max_count: int, chart_height: int, min_height: int) -> int:
    if count <= 0 or max_count <= 0:
        return 0
    return max(min_height, round((count / max_count) * chart_height))


def _generate_chart_image(analysis_stats: AnalysisStats | None, threshold: int) -> str:
    """Generate analysis chart as PNG (grouped bars) matching original HTML design."""
    analysis_stats = analysis_stats or AnalysisStats()
    threshold = max(1, min(10, int(threshold)))

    # Color definitions matching digest_color (darkest shade)
    colors = {
        "preprint": "#164e63",
        "published": "#064e3b",
        "dataset": "#7c2d12",
        "other": "#5b21b6",
    }
    sources = ["preprint", "published", "dataset", "other"]
    labels = ["Preprint", "Published", "Dataset", "Other"]

    # Build data for chart (use analysed_by_kind to show ALL papers analyzed, not just digest)
    scores = list(range(1, 11))
    data_by_source = {source: [] for source in sources}

    for score in scores:
        for source in sources:
            count = analysis_stats.analysed_by_kind.get(source, {}).get(str(score), 0)
            data_by_source[source].append(count)

    # Create figure with light background
    fig, ax = plt.subplots(figsize=(12, 3.8), dpi=96)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#fafafa")

    # Plot grouped bars (one group per score, 4 bars per group)
    bar_width = 0.2
    x_positions = list(range(len(scores)))

    for idx, (source, label, color) in enumerate(zip(sources, labels, [colors[s] for s in sources])):
        offsets = [x + (idx - 1.5) * bar_width for x in x_positions]
        ax.bar(offsets, data_by_source[source], bar_width, label=label, color=color, edgecolor="none")

        # Add count labels on top of bars (only if count > 0 to avoid clutter)
        for bar_offset, count in zip(offsets, data_by_source[source]):
            if count > 0:
                ax.text(
                    bar_offset,
                    count + 0.5,
                    str(int(count)),
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    color="#6b7280",
                    fontweight="500",
                )

    # Styling
    ax.set_xlabel("Relevance Score", fontsize=11, fontweight="600", color="#374151")
    ax.set_ylabel("Papers Included", fontsize=11, fontweight="600", color="#374151")
    ax.set_xticks(x_positions)
    ax.set_xticklabels([str(s) for s in scores], fontsize=10, color="#6b7280")
    ax.tick_params(axis="y", labelsize=10, colors="#6b7280")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb")
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.grid(axis="y", alpha=0.15, linestyle="-", linewidth=0.5, color="#d1d5db")

    # Add threshold line
    threshold_x = threshold - 1.5
    ax.axvline(x=threshold_x, color="#1f2937", linestyle="--", linewidth=1.5, alpha=0.5)

    # Legend
    ax.legend(loc="upper right", frameon=False, fontsize=10, labelcolor="#374151")

    # Set y-axis to start from 0
    ax.set_ylim(bottom=0)

    # Tight layout
    plt.tight_layout()

    # Convert to base64
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="#ffffff")
    buf.seek(0)
    image_base64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    return f"data:image/png;base64,{image_base64}"


def _build_analysis_chart(analysis_stats: AnalysisStats | None, threshold: int) -> dict:
    analysis_stats = analysis_stats or AnalysisStats()
    threshold = max(1, min(10, int(threshold)))
    series_defs = [
        ("preprint", "Preprint", "#06b6d4", "#0891b2", "#164e63"),
        ("published", "Published", "#10b981", "#059669", "#064e3b"),
        ("dataset", "Dataset", "#f97316", "#ea580c", "#7c2d12"),
        ("other", "Other", "#8b5cf6", "#7c3aed", "#5b21b6"),
    ]
    max_total = 0
    groups: list[dict] = []
    for score in range(1, 11):
        bars = []
        group_total = 0
        for key, label, total_color, pass2_color, digest_color in series_defs:
            total_count = analysis_stats.analysed_by_kind.get(key, {}).get(str(score), 0)
            pass2_count = min(
                total_count,
                analysis_stats.second_pass_by_kind.get(key, {}).get(str(score), 0),
            )
            digest_count = min(
                pass2_count,
                analysis_stats.digest_by_kind.get(key, {}).get(str(score), 0),
            )
            max_total = max(max_total, total_count)
            group_total += total_count
            bars.append(
                {
                    "key": key,
                    "label": label,
                    "total_count": total_count,
                    "pass2_count": pass2_count,
                    "digest_count": digest_count,
                    "total_color": total_color,
                    "pass2_color": pass2_color,
                    "digest_color": digest_color,
                }
            )
        groups.append(
            {
                "score": score,
                "bars": bars,
                "total_count": group_total,
                "is_threshold_region": score >= threshold,
                "is_threshold_start": score == threshold,
                "background_color": "#ffffff",
                "threshold_border": "0",
            }
        )

    chart_height = 132
    for group in groups:
        for bar in group["bars"]:
            total_count = int(bar["total_count"])
            pass2_count = int(bar["pass2_count"])
            digest_count = int(bar["digest_count"])
            total_height = _scaled_bar_height(total_count, max_total, chart_height, min_height=2)
            pass2_height = min(
                total_height,
                _scaled_bar_height(pass2_count, max_total, chart_height, min_height=1),
            )
            digest_height = min(
                pass2_height,
                _scaled_bar_height(digest_count, max_total, chart_height, min_height=1),
            )
            base_height = max(0, total_height - pass2_height)
            pass2_only_height = max(0, pass2_height - digest_height)
            bar["base_height"] = base_height
            bar["pass2_only_height"] = pass2_only_height
            bar["digest_height"] = digest_height
            bar["spacer_height"] = max(0, chart_height - total_height)

    chart_image = _generate_chart_image(analysis_stats, threshold) if analysis_stats.total_analysed() > 0 else None

    return {
        "chart_height": chart_height,
        "groups": groups,
        "legend": [
            {
                "label": label,
                "total_color": total_color,
                "pass2_color": pass2_color,
                "digest_color": digest_color,
            }
            for _, label, total_color, pass2_color, digest_color in series_defs
        ],
        "has_data": analysis_stats.total_analysed() > 0,
        "total_analysed": analysis_stats.total_analysed(),
        "total_second_pass": analysis_stats.total_second_pass(),
        "total_digest": analysis_stats.total_digest(),
        "threshold": threshold,
        "chart_image": chart_image,
    }


def generate_digest(
    scored_papers: list[ScoredPaper],
    date_range: tuple[datetime, datetime],
    output_dir: str,
    total_scanned: int = 0,
    digest_overview: str = "",
    token_usage: TokenUsage | None = None,
    analysis_stats: AnalysisStats | None = None,
    cost_usd: float | None = None,
    cost_gbp: float | None = None,
    cost_eur: float | None = None,
    model_name: str = "",
    threshold: int = 7,
) -> tuple[str, str]:
    """Render HTML digest and save to output directory.

    Returns (email_html_content, output_filepath).
    """
    env = Environment(
        loader=FileSystemLoader(_get_template_dir()),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("digest.html")

    start_date, end_date = date_range
    start_date = _to_utc(start_date)
    end_date = _to_utc(end_date)
    token_usage = token_usage or TokenUsage()
    analysis_chart = _build_analysis_chart(analysis_stats, threshold)

    # Saved HTML should work in a browser: copy logo next to the digest and
    # reference it by filename.
    os.makedirs(output_dir, exist_ok=True)
    logo_src_path = Path(_get_template_dir()) / "logo.png"
    logo_dst_path = Path(output_dir) / "logo.png"
    try:
        if logo_src_path.exists() and not logo_dst_path.exists():
            logo_dst_path.write_bytes(logo_src_path.read_bytes())
    except OSError as e:
        logger.warning("Could not copy logo to output directory: %s", e)

    file_html = template.render(
        papers=scored_papers,
        start_date=start_date,
        end_date=end_date,
        generation_date=datetime.now(timezone.utc),
        total_scanned=total_scanned,
        digest_overview=digest_overview,
        token_usage=token_usage,
        analysis_chart=analysis_chart,
        cost_usd=cost_usd,
        cost_gbp=cost_gbp,
        cost_eur=cost_eur,
        model_name=model_name,
        logo_src="logo.png",
    )

    filename = f"digest_{end_date.strftime('%Y-%m-%d')}.html"
    filepath = os.path.join(output_dir, filename)
    Path(filepath).write_text(file_html, encoding="utf-8")

    email_html = template.render(
        papers=scored_papers,
        start_date=start_date,
        end_date=end_date,
        generation_date=datetime.now(timezone.utc),
        total_scanned=total_scanned,
        digest_overview=digest_overview,
        token_usage=token_usage,
        analysis_chart=analysis_chart,
        cost_usd=cost_usd,
        cost_gbp=cost_gbp,
        cost_eur=cost_eur,
        model_name=model_name,
        logo_src="cid:arxivalert-logo",
    )

    logger.info("Digest saved to %s", filepath)
    return email_html, filepath


def generate_plain_text(
    scored_papers: list[ScoredPaper],
    date_range: tuple[datetime, datetime],
    digest_overview: str = "",
    token_usage: TokenUsage | None = None,
    analysis_stats: AnalysisStats | None = None,
    cost_usd: float | None = None,
    cost_gbp: float | None = None,
    cost_eur: float | None = None,
    model_name: str = "",
    threshold: int = 7,
) -> str:
    """Generate plain-text fallback for email."""
    start_date, end_date = date_range
    start_date = _to_utc(start_date)
    end_date = _to_utc(end_date)
    token_usage = token_usage or TokenUsage()
    analysis_chart = _build_analysis_chart(analysis_stats, threshold)
    lines = [
        "Paper Press Digest",
        f"{start_date.strftime('%Y-%m-%d %H:%M UTC')} — {end_date.strftime('%Y-%m-%d %H:%M UTC')}",
        f"{len(scored_papers)} relevant paper(s)",
        "",
        digest_overview if digest_overview else "Claude overview unavailable for this run.",
        "",
        (
            "Token usage: "
            f"{token_usage.input_tokens} base input / {token_usage.output_tokens} output"
            + (
                f" / cache writes {token_usage.cache_creation_input_tokens}"
                f" / cache reads {token_usage.cache_read_input_tokens}"
                if token_usage.cache_creation_input_tokens or token_usage.cache_read_input_tokens
                else ""
            )
            + f" (processed total {token_usage.total_processed_tokens})"
        ),
        (
            "Price estimate: "
            f"£{cost_gbp:.4f} / €{cost_eur:.4f} / ${cost_usd:.4f}. "
            "Billed totals: Anthropic Console https://console.anthropic.com/ , "
            "or https://docs.anthropic.com/en/api/usage-cost-api"
            if cost_usd is not None and cost_gbp is not None and cost_eur is not None
            else (
                "Price estimate: unavailable. Billed totals: "
                "Anthropic Console https://console.anthropic.com/ , "
                "or https://docs.anthropic.com/en/api/usage-cost-api"
            )
        ),
        (
            f"Analysed this window: {analysis_chart['total_analysed']} "
            f"(completed pass 2: {analysis_chart['total_second_pass']}, "
            f"included in digest: {analysis_chart['total_digest']}, "
            f"pass 2 threshold: >= {analysis_chart['threshold']}/10)"
        ),
        "",
        "=" * 60,
    ]

    for item in scored_papers:
        authors = ", ".join(item.paper.authors[:3])
        if len(item.paper.authors) > 3:
            authors += " et al."
        if not authors:
            authors = "Authors unavailable"

        lines.extend([
            "",
            f"[{item.score}/10] {item.paper.title}",
            f"  Authors: {authors}",
            f"  Source: {item.paper.source}",
            f"  Type: {item.paper.record_kind}",
            f"  {item.summary}",
            f"  Relevance: {item.relevance_reason}",
            f"  PDF: {item.paper.pdf_url}",
            f"  Abstract: {item.paper.abs_url}",
            "",
            "-" * 60,
        ])

    lines.extend([
        "",
        f"Generated by Paper Press on {datetime.now(timezone.utc).strftime('%d %b %Y at %H:%M UTC')}",
    ])

    return "\n".join(lines)
