# Lolita Feed OS

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
├── collector/   server-side collectors for public shop and market pages
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
- Server Collector MVP for public shop item cards and fixture-backed market
  samples.
- Shop -> Item model for official-shop, secondhand-shop, and public item card
  DROP signals.
- Official brand adapters for Angelic Pretty, BABY, AATP, Metamorphose, and
  Moi-meme-Moitie public release/news pages.
- `GenericPageAdapter` for arbitrary public pages with keyword matching.
- SQLite `items`, `events`, `source_runs`, `collector_jobs`,
  `collector_runs`, `shop_sources`, `shop_items`, `shop_events`,
  `market_sources`, and `market_samples` tables.
- Deduplication by `source + url/title hash`.
- `new_item` events for first-seen items.
- `update` events when title or status changes.
- `content_changed` events when a tracked page/item keeps the same title and
  status but its normalized content hash changes.
- Per-source health records for successful checks, latency, item counts, event
  counts, and adapter errors.
- Console-only local notifications.
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

No external notification tokens are required. See [.env.example](./.env.example).

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

Run enabled server collectors:

```bash
python -m lolita_radar.cli collect
```

Collector jobs are stored in SQLite. `official_shop` writes `shop_items` and
`shop_events`; `fixture_market` writes `market_samples`. A failed collector run
is recorded as failed or degraded and does not stop other collectors.

```python
from pathlib import Path
from lolita_radar.storage import connect, upsert_collector_job

connection = connect(Path(".data/lolita_radar.sqlite"))
upsert_collector_job(
    connection,
    name="baby_official_new",
    collector_type="official_shop",
    url="tests/fixtures/official_shop_products.html",
    options={
        "shop_name": "BABY Official Store",
        "platform": "official_store",
        "keywords": ["JSK", "OP", "Reservation", "予約"],
    },
)
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
intentionally want local console alerts during the long run.
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
  --market config/market_observations.json
```

To include optional long-run stability evidence in the same audit, pass the
loop log and exit-code file:

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
cycle counts, and source health summaries. Without a loop log, the stability
evidence check is recorded as optional and does not block the local Feed OS
audit result.
Add `--json` when a CI job or review script needs machine-readable status,
counts, and per-check details.

Start the local feed app:

```bash
python -m lolita_radar.cli web
```

Then open [http://127.0.0.1:8766](http://127.0.0.1:8766). The home page is a
feed stream with Release Feed, Drop Feed, Trend Feed, and Alert Feed filters.
It keeps the product focused on daily scanning instead of heavy analysis panels.
The combined home feed keeps at most 30 linked cards. Release and Drop cards
must use the original source publish date, stay in the current year, and fall
inside the recent 90-day source window so old news does not crowd the home page.
Trend `release_activity` uses the same source-date window.
The feed app renders four lightweight streams:

- Release Feed: brand release, preorder, and restock events from AP, BABY, AATP, Meta, and MMM.
- Drop Feed: first-seen public `shop_events` from official-shop and public item-card collectors.
- Trend Feed: rule-based rising/stable/cooling premium signals from `market_samples` with confidence, price_delta, and reason codes.
- Alert Feed: system-level market and source-health warnings such as
  high-premium signals, sale-window reminders, high-priority drops, stock
  availability, degraded sources, and failed sources.

Public Web API responses are also Feed OS shaped:

- `GET /api/feed` returns the public Feed OS payload.
- `GET /api/state` returns the same public Feed OS payload for compatibility.
- `POST /api/check`, `POST /api/market/observations`, and
  `PUT /api/brand-weights` return the Feed OS payload plus the operation result.
- Internal state blocks such as raw `items`, raw `events`, full `market`,
  `market_alerts`, `focus_queue`, and `opportunity_radar` are not exposed by
  the public Web API.

No AI/ML model, checkout automation, login automation, CAPTCHA bypass, queue bypass, or risk-control bypass is included.

## Server Collector MVP

Collectors are server-side monitors for public pages. They only fetch, parse,
structure, compare, persist, and alert. They never add to cart, submit orders,
pay, reserve queue slots, bypass platform controls, or manage account pools.
Every purchase-related CTA must open a source URL for human review and manual
action. The UI CTA for purchase-assist cards is `Open shop manually`.

Collector tables:

- `collector_jobs`: configured collector name, type, URL, options, enabled flag,
  consecutive failures, and degraded state.
- `collector_runs`: each run's ok/degraded/failed status, latency, item count,
  and error message.
- `shop_sources`, `shop_items`, `shop_events`: public shop item state and
  derived `DROP`, `PRICE_CHANGED`, and `STOCK_CHANGED` events.
- `market_sources`, `market_samples`: secondhand market samples used by Trend
  Feed.

Supported MVP collector types:

- `official_shop`: parses public product/listing cards into `shop_items`.
  Intended first targets are BABY official store new/reservation pages,
  Metamorphose official new arrivals, Wunderwelt new arrivals, and Closet Child
  Lolita new arrivals.
- `fixture_market`: fixture-backed market sample collector for tests and local
  validation.

Placeholder collector types are present but disabled by default:

- `mercari_market`
- `yahoo_auction_market`
- `lace_market`
- `wunderwelt_market`
- `closet_child_market`
- `taobao_public_shop`
- `goofish_market`

Taobao and Goofish/Xianyu are intentionally placeholders in this phase. Stable
collection may require an authorized session, platform permission, browser
fallback, or a public API. The default project does not include login
automation, automated purchasing, queue bypass, risk-control bypass, proxy
pools, or account pools.

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

`official_shop`

- Parses public product cards into `ShopItem` rows.
- Stores `shop_name`, `platform`, `title`, `price`, `currency`, `image_url`,
  `item_url`, `availability`, `matched_keywords`, `observed_at`, optional
  `sale_at`, `remind_at`, `purchase_url`, and `priority`.
- First-seen item URL/title hash creates a `DROP` event.
- Price changes create `PRICE_CHANGED`.
- Availability changes create `STOCK_CHANGED`.
- Drop Feed renders image, title, price, shop, platform, URL, and keyword chips.

`fixture_market`

- Parses fixture market cards into `MarketSample` rows.
- Trend Feed groups samples by `brand_alias + pattern + platform`.
- The current 7-day median asking price is compared with the previous 7-day
  median.
- `delta >= 15%` is `rising`; `delta <= -15%` is `cooling`; otherwise `stable`.
- `sample_count < 3` is low confidence.

`generic_page`

- Fetches any public URL.
- Extracts visible text.
- Extracts matching public item links as separate Shop -> Item candidates.
- Keeps public item card dates as Drop Feed source time when present.
- Keeps public item link images as Drop Feed card visuals when present.
- Keeps public item prices as Drop Feed card chips when present.
- Keeps linked item hashes scoped to the item link and parent item context so
  unrelated outer-page banner or notice edits do not create item updates.
- Falls back to one synthetic page-level item when no matching item links are
  found.
- Marks synthetic page-level fallback rows with `page_level` so broad page text
  matches can be tracked without becoming Drop Feed item cards.
- Adds structured `shop`, `item`, and `drop_keywords` metadata for linked item
  candidates and explicit `item_title` fallbacks.
- A text-only edit on the same page can generate `content_changed` without
  creating duplicate `new_item` events.
- `min_keyword_hits` controls how many configured keywords must match.
- `ignore_patterns` removes volatile text before keyword matching and hashing.
- `content_change_alert` can suppress content-only events for noisy pages.
- `max_content_chars` limits stored/hashable text size.
- `title_template` lets a source keep a stable item title.

`generic_page` remains available for public page monitoring, but Drop Feed now
prefers structured `shop_events` from collectors. Legacy `generic_page` DROP
candidates still require a first-seen `new_item` event, concrete item context,
and one of the configured item/action keywords such as `JSK`, `OP`, `再贩`,
`预约`, or `尾款`. Explicit `content_changed` rows are kept out of Drop Feed so
copy edits and outer-page noise do not masquerade as new items.
Reservation/restock keywords are ranked as higher urgency.

## Notifications

Console notifications render local Feed OS card-style summaries with bilingual
labels for source publish time, status, source, price, keywords, and URL.
`content_changed` messages include short previous/current content hashes instead
of sending long page bodies. No external notification API is used.

## Roadmap

- Better page-specific parsers for public proxy-shop pages.
- Persistent GitHub Actions cache.
- Structured release calendar export.
