# Lolita Radar OS

Lightweight feed app for monitoring public Lolita and Japanese fashion release
pages, reservation/preorder news, restock notices, public proxy-shop pages, and
secondary-market premium signals.

This project is an alerting and change-detection helper. It is not a purchasing
bot.

## Compliance Boundaries

- No automatic checkout, cart actions, payment, or order submission.
- No CAPTCHA bypass, queue bypass, risk-control bypass, account automation, or
  bulk purchasing.
- Only public page fetching, text extraction, keyword matching, change
  detection, SQLite persistence, and notifications.
- Keep schedules conservative and follow each site's terms and robots guidance.

## Architecture

```text
source -> fetcher -> parser -> normalized item -> storage -> diff event -> rule -> notifier
```

Product structure:

```text
lolita-radar
├── feed/        home feed stream
├── trend/       rule-based premium trend analysis
├── shop/        public shop and item drop model
├── crawler/     source health and crawler observability
├── core/        shared primitives
```

The current OS includes:

- `SourceAdapter` abstraction.
- Feed home with Release, Drop, Trend, and Alert streams.
- Rule-based trend engine with rising/stable/cooling, confidence, price_delta,
  and reasons.
- Shop -> Item model for public proxy-shop/Taobao-style DROP signals.
- Official brand adapters for Angelic Pretty, BABY, AATP, Metamorphose, and
  Moi-meme-Moitie public release/news pages.
- `GenericPageAdapter` for arbitrary public pages with keyword matching.
- SQLite `items`, `events`, and `source_runs` tables.
- Deduplication by `source + url/title hash`.
- `new_item` events for first-seen items.
- `update` events when title or status changes.
- `content_changed` events when a tracked page/item keeps the same title and
  status but its normalized content hash changes.
- Per-source health records for successful checks, latency, item counts, event
  counts, and adapter errors.
- Console, Telegram, and Discord webhook notifiers.
- GitHub Actions manual and scheduled runs.

## Install

```bash
git clone https://github.com/Daiobs/lolita-premium-radar.git
cd lolita-premium-radar
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Configure

Edit [config/sources.yaml](./config/sources.yaml).

```yaml
sources:
  angelic_pretty:
    type: angelic_pretty
    enabled: true
    url: "https://angelicpretty.com/"

  baby_ssb:
    type: baby_ssb
    enabled: true
    url: "https://www.babyssb.co.jp/news/"

  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"

  proxy_shop_template:
    type: generic_page
    enabled: false
    url: "https://example.com/replace-with-public-shop-page"
    keywords: ["Lolita", "JSK", "OP", "预约", "现货", "再贩"]
    options:
      title_template: "{source} public page"
      min_keyword_hits: 1
      ignore_patterns:
        - "updated at: [0-9: /.-]+"
      content_change_alert: true
      max_content_chars: 12000
```

To add a proxy or shopping agent page, use only a public product/listing page
URL, keep the source disabled until you have checked the page manually, and add
keywords that describe visible release text. Do not configure login-only pages,
cart URLs, queue pages, payment pages, or private member URLs.

Environment variables are optional. See [.env.example](./.env.example).

```bash
export TELEGRAM_BOT_TOKEN="123456:..."
export TELEGRAM_CHAT_ID="123456789"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

## Run

Inspect sources before writing anything:

```bash
python -m lolita_radar.cli inspect --all --limit 3
python -m lolita_radar.cli inspect --source angelic_pretty --limit 10
```

For first deployment, build a quiet baseline before enabling normal alerts:

```bash
python -m lolita_radar.cli check --all --baseline-only
```

`baseline-only` stores current items but writes no events and sends no
notifications, so historical releases do not flood the first alert run.
It is intended only for first deployment or rebuilding a database. If selected
sources already have tracked items, the command stops with a guardrail error;
use `--force-baseline` only when you intentionally want to overwrite existing
tracked state.

Check one source:

```bash
python -m lolita_radar.cli check --source metamorphose
```

Check all enabled sources:

```bash
python -m lolita_radar.cli check --all
```

If you intentionally want to write initial `new_item` events but keep the first
run quiet, use:

```bash
python -m lolita_radar.cli check --all --suppress-initial-notify
```

Show latest source health:

```bash
python -m lolita_radar.cli health
```

Run a 24-hour lightweight check loop:

```bash
mkdir -p .data/soak
python -m lolita_radar.cli run-loop \
  --db .data/soak/lolita-radar-os-24h.sqlite \
  --cycles 288 \
  --interval-seconds 300 \
  --log-file .data/soak/lolita-radar-os-24h.log \
  --exit-file .data/soak/lolita-radar-os-24h.exit
```

The loop keeps notifications off by default and records source health every
cycle. It also writes a machine-checkable audit log and exit-code file when
`--log-file` and `--exit-file` are set. The loop audit table includes
`cycle | checked_at | ok | event_count | error_message`, so each cycle can be
matched back to the 24-hour evidence window. Add `--notify` only when you
intentionally want live alerts during the long run.
If the loop is stopped with Ctrl-C or SIGTERM, the exit-code file records the
interruption as a non-zero code so the run cannot be mistaken for a clean soak.

Verify a completed long run before calling it stable:

```bash
python -m lolita_radar.cli verify-loop \
  --log .data/soak/lolita-radar-os-24h.log \
  --db .data/soak/lolita-radar-os-24h.sqlite \
  --exit-file .data/soak/lolita-radar-os-24h.exit \
  --expected-cycles 288
```

`verify-loop` reports `complete` only when the loop log has the expected cycle
coverage, the log proves at least 86400 seconds elapsed, the exit file is `0`,
every enabled source has enough recent `source_runs` records in the database,
and those recent source runs are healthy. When the log contains `started_at` and
`finished_at`, source runs must fall inside that same window. Duplicate cycle
numbers, partially missing cycle `checked_at` values, and cycle `checked_at`
values outside the evidence window are rejected.
This keeps the 24-hour stability check auditable and prevents old failures,
duplicate log lines, mismatched cycle timestamps, or fast synthetic cycles from
producing a false result.
Add `--json` when saving review evidence or wiring a CI/manual gate:

```bash
python -m lolita_radar.cli verify-loop \
  --log .data/soak/lolita-radar-os-24h.log \
  --db .data/soak/lolita-radar-os-24h.sqlite \
  --exit-file .data/soak/lolita-radar-os-24h.exit \
  --expected-cycles 288 \
  --json > .data/soak/lolita-radar-os-24h.verify.json
```

For local smoke tests that intentionally are not 24-hour evidence, pass:

```bash
python -m lolita_radar.cli verify-loop \
  --log .data/soak/short.log \
  --db .data/soak/short.sqlite \
  --exit-file .data/soak/short.exit \
  --expected-cycles 2 \
  --min-duration-seconds 0
```

Audit the Feed OS acceptance evidence:

```bash
python -m lolita_radar.cli audit-feed-os \
  --brands config/brand_weights.json \
  --market config/market_observations.json \
  --loop-log .data/soak/lolita-radar-os-24h.log \
  --loop-exit-file .data/soak/lolita-radar-os-24h.exit \
  --expected-cycles 288
```

`audit-feed-os` checks the current product contract: module structure, Feed OS
UI tokens, Release/Drop/Trend/Alert fields, the current config/database feed
state, rule-based Trend output, Shop DROP signals, crawler health fields,
GenericPage noise controls, and optional loop evidence. When loop evidence is
provided, the JSON output includes `window_start`, `window_end`,
`duplicate_cycles`, `missing_cycle_timestamps`, `cycle_time_mismatches`, source
cycle counts, and source health summaries. Without a loop log it reports the
stability evidence as `missing` instead of treating the system as fully
accepted.
Add `--json` when a CI job or review script needs machine-readable status,
counts, and per-check details.

Start the local feed app:

```bash
python -m lolita_radar.cli web
```

Then open [http://127.0.0.1:8766](http://127.0.0.1:8766). The home page is a
feed stream with Release Feed, Drop Feed, Trend Feed, and Alert Feed filters.
It keeps the product focused on daily scanning instead of heavy analysis panels.
The feed app renders four lightweight streams:

- Release Feed: brand release, preorder, and restock events from AP, BABY, AATP, Meta, and MMM.
- Drop Feed: public proxy-shop or Taobao-style page changes from `generic_page` sources.
- Trend Feed: rule-based rising/stable/cooling premium signals with confidence, price_delta, and reason codes.
- Alert Feed: new releases, high-premium signals, sample gaps, and source-health warnings.

No AI/ML model, checkout automation, login automation, CAPTCHA bypass, queue bypass, or risk-control bypass is included.

## GitHub Actions

The workflow in [.github/workflows/check.yml](./.github/workflows/check.yml)
supports:

- `workflow_dispatch` for manual runs.
- `pull_request` for branch review before merging to `main`.
- `schedule` for cron-based checks.
- Unit tests before live source checks.
- Feed OS audit JSON generation. The workflow fails on audit `fail` checks and
  uploads `feed-os-audit.json`; `missing` stability evidence remains visible
  without pretending that a 24-hour soak has completed.

Recommended GitHub Secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DISCORD_WEBHOOK_URL`

The workflow restores and saves `.data` through `actions/cache`, so scheduled
runs can compare against the previous SQLite state. Local usage persists in
`.data/lolita_radar.sqlite`.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests
```

## Source Types

`angelic_pretty`

- Fetches Angelic Pretty public news/release pages.
- Extracts release-like links and classifies preorder, restock, and new-arrival
  signals from Japanese/English title keywords.

`baby_ssb`

- Fetches BABY, THE STARS SHINE BRIGHT public news pages.
- Uses the shared brand-release parser and records brand metadata for downstream
  weighting.

`alice_and_the_pirates`

- Fetches AATP public release/news pages, including shared BABY news feeds when
  configured that way.
- Classifies `new_arrival`, `preorder`, `restock`, or `shop_news`.

`metamorphose`

- Fetches the Metamorphose English News page.
- Extracts `title`, `url`, and `published_at` when a date is present.
- Classifies each item as `new_arrival`, `preorder`, `restock`, or `shop_news`
  from title keywords.

`moitie`

- Fetches Moi-meme-Moitie public news pages.
- Extracts release-like links and stores normalized content hashes for diffing.

`innocent_world`

- Optional adapter for Innocent World public news pages.
- Disabled in the default config until the target public URL is confirmed.

`generic_page`

- Fetches any public URL.
- Extracts visible text.
- Extracts matching public item links as separate Shop -> Item candidates.
- Falls back to one synthetic page item when no matching item links are found.
- Adds structured `shop`, `item`, and `drop_keywords` metadata for Drop Feed.
- A text-only edit on the same page can generate `content_changed` without
  creating duplicate `new_item` events.
- `min_keyword_hits` controls how many configured keywords must match.
- `ignore_patterns` removes volatile text before keyword matching and hashing.
- `content_change_alert` can suppress content-only events for noisy pages.
- `max_content_chars` limits stored/hashable text size.
- `title_template` lets a source keep a stable item title.

Drop Feed treats matching `generic_page` rows as public Shop -> Item signals.
DROP candidates require one of the configured item/action keywords such as
`JSK`, `OP`, `再贩`, `预约`, or `尾款`; new page items and reservation/restock
keywords are ranked as higher urgency.

## Notifications

Console, Telegram, and Discord notifications include `brand`, `source`,
`event_type`, `status`, `title`, `published_at`, `url`, and matched keywords
when present. `content_changed` messages include short previous/current content
hashes instead of sending long page bodies.

## Roadmap

- Better page-specific parsers for public proxy-shop pages.
- Persistent GitHub Actions cache.
- Structured release calendar export.
