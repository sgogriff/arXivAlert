# Paper Press
![logo](templates/logo_smaller.png)

AI-curated research digests powered by Anthropic Claude. Paper Press combines preprints and published work in one relevance-ranked digest while keeping provider-native fetchers where they are cheapest and strongest.

Supported sources:

- arXiv
- bioRxiv
- medRxiv
- OSF preprints
- Europe PMC preprints
- Zenodo
- OpenAlex for published works and open-access enrichment

## How It Works

1. Fetches recent records from the enabled sources in `config.yaml`.
2. Normalizes them into one shared paper model.
3. Filters by preprint/published settings and content scope.
4. Deduplicates linked versions by DOI first, then title/author/year fallback.
5. Scores new papers with Claude using truncated abstracts.
6. Summarises only the papers above your threshold.
7. Stores results in `paper_cache.json` and queues relevant unemailed papers until enough are ready.
8. Builds an HTML digest plus plain-text fallback and optionally sends email via SMTP.

## Requirements

- Python 3.10+
- An Anthropic API key
- An OpenAlex API key if `sources.openalex.enabled: true`
- An SMTP account with `STARTTLS` support if you want email delivery

OpenAlex keys are free, but the API is metered (if you go over the free usage, it's paid). Paper Press minimizes OpenAlex usage by keeping direct preprint fetchers for arXiv, bioRxiv, medRxiv, OSF, Europe PMC, and Zenodo.

## Installation

```bash
git clone <repository-url> PaperPress
cd PaperPress
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Configuration

### 1. Create `config.yaml`

```bash
cp config.yaml.template config.yaml
```

`config.yaml.template` is the canonical config contract. The runtime loader expects `config.yaml` to match that shape. Old top-level `arxiv:` configs are rejected with a validation error.

The main top-level sections are:

- `sources`: which providers to query and how many records to fetch
- `selection`: whether to include preprints, published work, and broader output types
- `interests`: your natural-language relevance brief
- `scoring`: Claude model and batching thresholds
- `digest`: digest window and output behavior

Important `selection` fields:

- `include_preprints`: include preprints before scoring
- `include_published`: include published work before scoring
- `content_scope`:
  - `articles_only`: preprints plus journal/conference-style papers
  - `articles_and_datasets`: adds dataset-like records where supported
  - `all_supported_types`: keep all normalized supported types and let scoring filter more noise
- `version_preference`:
  - `prefer_published`: collapse linked preprint/published versions to the published one
  - `prefer_newest`: keep the newest linked version
  - `prefer_preprint`: keep the preprint when linked versions exist
  - `show_all`: do not collapse linked versions

Find arXiv category codes at [arXiv archive](https://arxiv.org/archive). OpenAlex filter IDs such as topics, journals, and institutions can be looked up in OpenAlex.

### 2. Create `.env`

```bash
cp .env.template .env
```

Example:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
OPENALEX_API_KEY=oa_your_free_key_here
ZENODO_API_KEY=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=your-app-password
RECIPIENT_EMAIL=you@example.com
```

Environment variables:

- `ANTHROPIC_API_KEY` is required for all runs.
- `OPENALEX_API_KEY` is required only when `sources.openalex.enabled: true`.
- `ZENODO_API_KEY` is optional and only helps with Zenodo rate limits / larger page sizes.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `RECIPIENT_EMAIL` are only needed for email delivery.

If you run without `--no-email` and SMTP credentials are missing, the digest is still written to disk when generated, but email delivery fails and papers remain queued as unemailed.

## Usage

With an activated virtual environment:

```bash
python -m src.main --help
```

Without activating:

```bash
.venv/bin/python -m src.main --help
```

Available flags:

- `--config PATH`: use a different config file.
- `--no-email`: generate/save the digest but skip SMTP delivery.
- `--dry-run`: fetch and score only; do not generate a digest or send email.
- `--forcemax` / `--force-fetch` / `--force-max`: ignore `last_run.json` and fetch up to each source's configured `max_results` from the last 365 days.
- `--forcemin`: iteratively expand the lookback window in non-overlapping slices until `min_papers_to_email` papers are ready, capping at 90 days. Cheaper than `--forcemax` for testing.
- `--clear-cache`: clear `paper_cache.json`, reset `token_usage.json`, remove `last_run.json`, then continue the run.
- `--test-email`: generate and send a test digest using cached papers or placeholder content. Useful for testing email template changes without running the full fetch/score pipeline.

### Normal run

```bash
.venv/bin/python -m src.main
```

Behavior:

- Fetches records in the current date window from all enabled sources.
- Filters, deduplicates, scores, and summarises new relevant papers.
- Updates the persistent paper cache.
- Generates and emails a digest only when at least `digest.min_papers_to_email` unemailed relevant papers are queued.

### Save digest but do not send email

```bash
.venv/bin/python -m src.main --no-email
```

This still updates `last_run.json` and `token_usage.json` and writes the HTML digest when enough queued papers are available. It does not mark papers as emailed, so the same queued papers remain eligible for later delivery.

### Dry run

```bash
.venv/bin/python -m src.main --dry-run
```

This fetches and scores papers, logs matching titles and token usage, and exits before digest generation, email sending, `last_run.json`, or `token_usage.json` updates.

Important: `--dry-run` still writes scored papers to `paper_cache.json`, because scoring always goes through the persistent cache layer.

### Force a large lookback

```bash
.venv/bin/python -m src.main --forcemax
```

This ignores `last_run.json` and looks back 365 days, still bounded by each enabled source's configured `max_results`. Useful for comprehensive testing, but expensive due to high API usage.

### Iteratively expand lookback (cheaper testing)

```bash
.venv/bin/python -m src.main --forcemin
```

This iteratively expands the lookback window in non-overlapping slices until `min_papers_to_email` papers are ready (or caps at 90 days). Lookback schedule: 1 → 3 → 7 → 14 → 30 → 60 → 90 days. Much cheaper than `--forcemax` for testing because it stops early when enough papers are accumulated. Already-scored papers are free cache hits, so expanding the window has minimal cost.

### Test email template

```bash
.venv/bin/python -m src.main --test-email
```

Generates and sends a test digest immediately without running the fetch/score pipeline. Uses previously cached papers if available, otherwise generates placeholder papers. Useful for verifying email formatting and SMTP configuration without incurring API costs.

### Clear state before running

```bash
.venv/bin/python -m src.main --clear-cache
```

This resets:

- `paper_cache.json`
- `token_usage.json`
- `last_run.json`

Then it proceeds with a normal run.

## State Files

Paper Press keeps its working state in a few small files at the project root:

- `last_run.json`: timestamp of the last completed non-dry run
- `paper_cache.json`: cached paper metadata, scores, summaries, and emailed state
- `token_usage.json`: accumulated token usage, digest window start, scanned-paper count, and cached digest overview text

The accumulation model matters:

- Papers are only marked as emailed after a successful SMTP send.
- Token usage is accumulated across runs until a digest is successfully emailed.
- If email is skipped or fails, queued papers and accumulated usage remain in place.

## Output

When a digest is generated, Paper Press writes:

- `output/digest_YYYY-MM-DD.html`
- `output/logo.png` if it is not already present

The digest includes:

- the digest date range
- number of relevant papers
- total scanned papers in the current accumulation window
- a Claude-written overview paragraph
- token usage totals
- approximate Claude API cost in USD, GBP, and EUR when pricing is known
- token accounting includes base input/output tokens plus Anthropic prompt-cache writes and reads when present
- source and preprint/published badges for each paper

If the configured model is not in `src/pricing.py`, cost is shown as unavailable.

## Scheduling with cron

Daily at 08:00:

```cron
0 8 * * * cd /path/to/PaperPress && /path/to/PaperPress/.venv/bin/python -m src.main >> /tmp/paperpress.log 2>&1
```

Weekly at 08:00 on Monday:

```cron
0 8 * * 1 cd /path/to/PaperPress && /path/to/PaperPress/.venv/bin/python -m src.main >> /tmp/paperpress.log 2>&1
```

Fortnightly on the 1st and 15th at 08:00:

```cron
0 8 1,15 * * cd /path/to/PaperPress && /path/to/PaperPress/.venv/bin/python -m src.main >> /tmp/paperpress.log 2>&1
```

Monthly on the 1st at 08:00:

```cron
0 8 1 * * cd /path/to/PaperPress && /path/to/PaperPress/.venv/bin/python -m src.main >> /tmp/paperpress.log 2>&1
```
