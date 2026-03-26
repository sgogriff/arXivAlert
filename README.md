# arXivAlert
![logo](templates/logo_smaller.png)

AI-curated arXiv digests powered by Anthropic Claude. arXivAlert fetches recent papers from your chosen arXiv categories, scores them against your research interests in a two-step process (reducing token use), generates short summaries for the papers that pass your threshold, and can email the result as an HTML digest. It's designed to be run as a cron job for iterative scanning of new papers in your field of interest.

## How It Works

1. Fetches papers (via arXiv API) from the configured arXiv categories, deduplicated across categories by arXiv ID.
2. Scores new papers with Claude in a first pass using truncated abstracts.
3. Summarises only the papers above your relevance threshold in a second pass using full abstracts.
4. Stores scored papers in `paper_cache.json` and keeps unemailed papers queued until at least `digest.min_papers_to_email` are ready.
5. Builds an HTML digest plus plain-text fallback, adds a short digest-level overview from Claude, and optionally sends the email via SMTP.

## Requirements

- Python 3.10+
- An Anthropic API key
- An SMTP account that supports `STARTTLS` if you want email delivery

Gmail works, but it is not the only option.

## Installation

```bash
git clone https://github.com/sgogriff/arXivAlert.git
cd arXivAlert
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Configuration

### 1. Create `config.yaml`

```bash
cp config.yaml.template config.yaml
```

Example:

```yaml
arxiv:
  categories:
    - "nucl-ex"
    - "physics.ins-det"
  max_results: 200

interests: |
    I'm fascinated by the intersection of computer vision and robotics. My research focuses on [...] Additionally, I have a curiosity about applying AI to tackle complex societal challenges, such as environmental monitoring or disaster response.

scoring:
  model: "claude-sonnet-4-20250514"
  threshold: 7
  batch_size: 8
  score_batch_size: 24
  abstract_truncation_words: 150

digest:
  schedule: "weekly"
  output_dir: "output"
  min_papers_to_email: 3
```

Config fields:

- `arxiv.categories`: non-empty list of arXiv category codes.
- `arxiv.max_results`: maximum results fetched per category.
- `interests`: free-text description of your research interests.
- `scoring.model`: Claude model name.
- `scoring.threshold`: minimum score to keep a paper.
- `scoring.batch_size`: pass-2 summarisation batch size.
- `scoring.score_batch_size`: pass-1 scoring batch size.
- `scoring.abstract_truncation_words`: abstract length used in pass 1.
- `digest.schedule`: one of `daily`, `weekly`, `fortnightly`, `monthly`.
- `digest.output_dir`: directory for saved HTML digests. It is created automatically.
- `digest.min_papers_to_email`: minimum queued relevant papers required before generating/sending a digest.

`digest.schedule` controls the initial lookback only when `last_run.json` is missing or unreadable:

- `daily`: 1 day
- `weekly`: 7 days
- `fortnightly`: 14 days
- `monthly`: 30 days

After that, arXivAlert uses `last_run.json` as the fetch start time.

Find category codes at [arXiv archive](https://arxiv.org/archive).

### 2. Create `.env`

```bash
cp .env.template .env
```

Example:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=your-app-password
RECIPIENT_EMAIL=you@example.com
```

Environment variables:

- `ANTHROPIC_API_KEY` is required for all runs.
- `SMTP_HOST` defaults to `smtp.gmail.com` if omitted.
- `SMTP_PORT` defaults to `587` if omitted.
- `SMTP_USER`, `SMTP_PASSWORD`, and `RECIPIENT_EMAIL` are only needed if you want email delivery.

If you run without `--no-email` and SMTP credentials are missing, the digest is still written to disk when generated, but email delivery fails and papers remain queued as unemailed.

### Gmail App Password

If you use Gmail:

1. Enable 2-Step Verification for the account.
2. Go to Google Account `Security` > `App passwords`.
3. Create a new app password for Mail.
4. Put that value in `SMTP_PASSWORD`.

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
- `--forcemax` / `--force-fetch` / `--force-max`: ignore `last_run.json` and fetch up to `max_results` papers per category from the last 365 days.
- `--clear-cache`: clear `paper_cache.json`, reset `token_usage.json`, remove `last_run.json`, then continue the run.

### Normal run

```bash
.venv/bin/python -m src.main
```

Behaviour:

- Fetches papers in the current date window.
- Scores and summarises new relevant papers.
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

This ignores `last_run.json` and looks back 365 days, still bounded by `arxiv.max_results` per category.

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

arXivAlert keeps its working state in a few small files at the project root:

- `last_run.json`: timestamp of the last completed non-dry run.
- `paper_cache.json`: cached paper metadata, scores, summaries, and emailed status.
- `token_usage.json`: accumulated token usage, digest window start, scanned-paper count, and cached digest overview text.

The accumulation model matters:

- Papers are only marked as emailed after a successful SMTP send.
- Token usage is accumulated across runs until a digest is successfully emailed.
- If email is skipped or fails, queued papers and accumulated usage remain in place.

## Output

When a digest is generated, arXivAlert writes:

- `output/digest_YYYY-MM-DD.html`
- `output/logo.png` if it is not already present

The digest includes:

- the digest date range
- number of relevant papers
- total scanned papers in the current accumulation window
- a Claude-written overview paragraph
- token usage totals
- approximate cost in USD, GBP, and EUR when pricing is known

If the configured model is not in `src/pricing.py`, cost is shown as unavailable.

## Scheduling with cron

The program is intended to be run periodically by cron or another scheduler.

Examples:

Daily at 08:00:

```cron
0 8 * * * cd /path/to/arXivAlert && /path/to/arXivAlert/.venv/bin/python -m src.main >> /tmp/arxivalert.log 2>&1
```

Weekly at 08:00 on Monday:

```cron
0 8 * * 1 cd /path/to/arXivAlert && /path/to/arXivAlert/.venv/bin/python -m src.main >> /tmp/arxivalert.log 2>&1
```

Fortnightly on the 1st and 15th at 08:00:

```cron
0 8 1,15 * * cd /path/to/arXivAlert && /path/to/arXivAlert/.venv/bin/python -m src.main >> /tmp/arxivalert.log 2>&1
```

Monthly on the 1st at 08:00:

```cron
0 8 1 * * cd /path/to/arXivAlert && /path/to/arXivAlert/.venv/bin/python -m src.main >> /tmp/arxivalert.log 2>&1
```

Keep `digest.schedule` aligned with how often you run the job, because it determines the fallback lookback when `last_run.json` is missing.

## Docker

### Build and run

```bash
docker compose build
docker compose run --rm arxivalert
```

### Pass CLI flags through to the container

```bash
docker compose run --rm arxivalert --no-email
docker compose run --rm arxivalert --dry-run
docker compose run --rm arxivalert --forcemax
```

### What the current `docker-compose.yml` persists

The checked-in compose file mounts:

- `./config.yaml:/app/config.yaml:ro`
- `./output:/app/output`
- `./last_run.json:/app/last_run.json`

It also loads environment variables from `.env`.

Important limitation: `paper_cache.json` and `token_usage.json` are not mounted in the current compose file. With `docker compose run --rm`, those files live only inside the temporary container, so queued-paper accumulation and accumulated token tracking do not persist across container runs.

If you want Docker runs to behave like local runs across time, either:

- set `digest.min_papers_to_email: 1`, or
- add bind mounts for `paper_cache.json` and `token_usage.json` to `docker-compose.yml`

## Project Structure

```text
arXivAlert/
├── src/
│   ├── config.py
│   ├── fetcher.py
│   ├── formatter.py
│   ├── fx.py
│   ├── mailer.py
│   ├── main.py
│   ├── pricing.py
│   ├── scorer.py
│   └── usage_state.py
├── templates/
│   ├── digest.html
│   ├── logo.png
│   └── logo_orig.png
├── output/
├── config.yaml.template
├── .env.template
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Troubleshooting

### No papers found

- Check the category codes.
- Increase `arxiv.max_results`.
- On a first run, use a broader `digest.schedule` or `--forcemax`.

### Papers are found but no digest is generated

- Lower `scoring.threshold`.
- Set `digest.min_papers_to_email: 1` for testing.
- Remember that summaries are only generated for papers at or above the threshold.

### Email was not sent

- Verify `SMTP_USER`, `SMTP_PASSWORD`, and `RECIPIENT_EMAIL`.
- Make sure your SMTP server supports `STARTTLS`.
- Test with `--no-email` first to confirm the rest of the pipeline works.

### Cost shows as unavailable

- The configured model name is not covered by the pricing table in `src/pricing.py`.

### Docker runs do not accumulate papers across executions

- This is expected with the current compose file unless you also persist `paper_cache.json` and `token_usage.json`.

## License

MIT
