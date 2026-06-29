# Lolita Premium Radar

Python MVP for monitoring public Lolita and Japanese fashion release pages,
reservation/preorder news, restock notices, and public Taobao proxy-shop pages.

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

The first MVP includes:

- `SourceAdapter` abstraction.
- `MetamorphoseAdapter` for the Metamorphose English News page.
- `GenericPageAdapter` for arbitrary public pages with keyword matching.
- SQLite `items` and `events` tables.
- Deduplication by `source + url/title hash`.
- `new_item` events for first-seen items.
- `update` events when title or status changes.
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
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"

  taobao_proxy_sample:
    type: generic_page
    enabled: false
    url: "https://example.com/public-shop-page"
    keywords: ["Lolita", "JSK", "OP", "预约", "现货", "再贩"]
```

Environment variables are optional. See [.env.example](./.env.example).

```bash
export TELEGRAM_BOT_TOKEN="123456:..."
export TELEGRAM_CHAT_ID="123456789"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

## Run

Check one source:

```bash
python -m lolita_radar.cli check --source metamorphose
```

Check all enabled sources:

```bash
python -m lolita_radar.cli check --all
```

Start the local web dashboard:

```bash
python -m lolita_radar.cli web
```

Then open [http://127.0.0.1:8766](http://127.0.0.1:8766). The dashboard shows
configured sources, stored events, tracked items, and buttons for manual checks.
The UI defaults to Chinese and includes a `中文 / EN` language switch. It also
includes a Lolita secondary-market view with brand weights, status mix, and a
simple premium-signal score for prioritizing attention. The first screen pairs
the operational controls with a local lace-and-radar visual asset so the app
feels closer to a Lolita market desk without depending on remote media. A
North Star radar band combines brand portfolio health, weighted evidence
coverage, release heat, run-sheet pressure, and premium-backed brands into one
top-level maturity score, with four lane cards that explain whether today's work
is evidence collection, release watching, premium confirmation, or execution. A
brand crown queue sits underneath it and ranks the highest-priority labels by
brand weight, release hits, resale premium, sample evidence, and signature style
terms such as AP shell keywords, with direct actions for adding brand samples,
prefilling pattern samples, or jumping to the release queue.
Brand weights, scorecards, core watch gaps, and sample plans are also summarized
into a daily radar brief near the top of the dashboard, turning AP/BABY/AATP-style
priority checks into a short action queue with jump, sample, and pattern-prefill
buttons, plus grouped action lanes that can be clicked to filter the brief by
price anchors, evidence collection, tuning, or sampling; the same brief can be
exported as `lolita-daily-radar.csv` for a daily watch checklist. A release
watch queue matches tracked release, preorder, restock, and shop-news items
against each brand's alias, official keywords, and market pattern terms, then
scores each signal with brand weight, resale premium, status, and sample gaps so
new AP/BABY/AATP-style drops can move straight into source review, resale search,
or price-sample entry; the same rows now flow into the resale run sheet and can
be exported as `lolita-release-watch.csv`. Brand weight cards use Lolita-style
visual cues such as themed brand cameos, tier
ribbons, market-keyword pearls, and per-brand visual identity metadata for
palette, motif, and radar cue, while keeping the weight sliders and saved draft
state visible for fast tuning. A brand-weight scenario comparison panel previews
release-first, premium-first, and evidence-first target weights side by side,
including average target, up/down counts, top moves, and one-click draft apply
buttons; the expanded scenario table can be exported as
`lolita-weight-scenarios.csv` for comparing all three weighting modes in a
spreadsheet before saving. A sticky radar navigation strip jumps between
weights, brand identity, matrix, premium, evidence, and source sections so the
long dashboard stays usable. The brand radar matrix
puts weight, average resale premium, sample count, radar score, and next action
in one scan-friendly view, with focused filters and sorting by score, premium,
weight, sample count, or draft-score movement. A focus-brand filter keeps core,
high-weight, sampled, or clearly premium-supported labels in view for daily
watching, and each matrix action includes a short reason such as core brand,
sample gap, or premium support. The opportunity radar turns brand
weights, sample counts, and resale-premium rates into watch actions such as
tracking releases, collecting more price samples, or cooling down a brand, with
band counts, filters, and score breakdowns for scanning each action tier. A
radar alert line elevates collector/hot premium spikes, brand-level heat, and
core-brand sample gaps into one action summary. A resale-momentum panel compares
each brand's latest sample against its previous average so rising and cooling
premium signals are visible before they become broad averages. A
sample-coverage panel shows how much price evidence backs the radar and which
high-weight brands need samples next. A weight-profile panel summarizes average
weight, tier distribution, market-evidence coverage, and the highest-priority
sample gaps so brand weights are easier to audit before saving; it also renders
a brand-weight radar map where AP, BABY, AATP, and other high-priority labels are
plotted by current weight with sample progress beside each label. A brand-weight
lookbook adds a Lolita-style rack of the highest-fit labels, combining palette,
motif, fit score, premium rate, sample count, and one-click sample/pattern
actions so the dashboard reads more like a focused resale atelier while still
explaining why each brand is weighted. A brand-weight scorecard turns the same
formula into auditable tickets for each label, showing current weight, target
weight, confidence, baseline, premium, evidence, keyword, and watchability parts
with direct actions to apply a draft target or collect missing samples; the full
scorecard set can be exported as `lolita-weight-scorecards.csv` for audit or
spreadsheet review. A brand-weight guardrail panel flags mismatches such as
core labels without price anchors, strong premium with low weight, or discounted
resale with high weight, then offers direct sample-entry and target-weight draft
actions; the full guardrail queue can be exported as
`lolita-weight-guardrails.csv` for a daily evidence/audit checklist. A
brand-weight strategy panel converts the current saved or draft weights into calibration
moves such as collecting evidence, raising premium-supported brands, or cooling
discounted brands. A weight-trajectory panel then turns the same evidence into
current-to-target paths with confidence, average shift, and direct actions to
apply a target draft or collect the missing sample. A brand-weight composition
panel explains each brand's configured weight, monitoring role, premium heat,
evidence level, and pattern keywords in one card. A brand identity matrix
compares each label's palette, motif, radar cue, weight, premium rate, and sample
evidence in one scan-friendly view. A hot-pattern keyword radar turns item-level
terms such as `AP` + `贝壳` into one-click sample entry seeds, and the
pattern-premium radar groups recorded samples by those
keywords to show item-level premium, sample count, and weighted priority. The
core brand watch desk then brings high-weight brands, signature pattern terms,
sample progress, one-click sample prefill, and Goofish/Taobao/Mercari/Yahoo JP
search links into one daily watch surface for AP shell-line style tasks and
other core-label checks; each card also explains why it is on watch with chips
for core weight, thin evidence, positive/strong premium, discount review, and
keyword depth. Core watch cards include a price-anchor strip for average retail,
average resale, and average spread when samples exist, or a clear missing-anchor
state when the next task is still price collection. The watch-desk summary counts
price-anchor gaps, each card marks the next action such as adding a price anchor,
adding a second sample, tracking spread, reviewing discount, or holding watch,
and clicking a core watch term opens the resale-sample form with a visible
price-anchor task hint before you enter retail and resale prices; it also fills
empty source/notes fields with core-watch context so evidence stays auditable.
The whole watch desk can be exported as
`lolita-core-watch.csv` for a daily search/sample checklist. The
market action desk turns the highest-priority pattern rows into search links for
Goofish, Taobao, Mercari JP, and Yahoo Japan Auctions plus one-click sample
prefill, and exports the same search/sample tasks as
`lolita-market-actions.csv` for a daily resale-check run sheet. A resale run
sheet near the top of the dashboard merges the daily radar brief, market search
tasks, and price-discipline anchor gaps into one execution surface, with direct
jump/sample/pattern actions and `lolita-run-sheet.csv` export for daily checks.
A price-discipline
panel converts brand weight, average retail price, average resale price, and
sample count into a chase ceiling, current resale average, and room/near/hot
status so overheated secondhand prices are visible before sampling or buying;
when a high-weight brand does not yet have price anchors, the same panel marks
it as anchor-needed and sends it back to the sample form.
An evidence-health panel scores sample quality from source, link, date,
condition, and notes so weak evidence is visible before it drives weighting.
Recorded resale samples are also segmented into collector, hot, premium,
near-retail, and discount bands, with dashboard filters for scanning the
strongest signals first by band and brand. The currently filtered sample set can
be exported as CSV for spreadsheet review or sharing.

The web dashboard includes a Lolita theme switcher with Sweet, Classic, and
Gothic palettes. The setting is saved locally in the browser, so visual review
can move between a sweeter AP-like mood, a calmer classic archive desk, and a
darker gothic radar without changing market data or brand weights. The hero area
also renders a style compass from the active brand weights, so Sweet, Classic,
Gothic, Release, and Art Print lanes are visible immediately and update while
you tune draft weights.

Brand weights live in [config/brand_weights.json](./config/brand_weights.json).
The default first-pass priority is AP 100, BABY 95, AATP 90, Meta 80, MMM 75,
and IW/VM/MM/JetJ 65. The dashboard also builds a focus queue from those weights
and the currently observed items/events. You can adjust existing brand weights
from the web dashboard, preview how draft weights change opportunity radar
scores and deltas, review unsaved changes, reset drafts, and save them back to
the configured brand-weight file. Scenario buttons can also draft a full weighting
mode for release-first, premium-first, or evidence-first reviews without saving
immediately. A style ledger above the weight cards groups labels into Sweet,
Classic, Gothic, Release, and Art Print lanes, showing each lane's brand count,
average draft weight, lead labels, core share, and style keywords so the Lolita
visual strategy is visible while tuning weights. A premium seed radar turns those
weights and signature market keywords into the next sample-collection targets
before enough resale observations exist, so terms such as AP `贝壳`, BABY
`Usakumya`, or AATP `Vampire Requiem` can be pushed straight into the price
sample form or exported as one seed-task row per brand/term in
`lolita-premium-seeds.csv`; the same panel summarizes total seed tasks, core
brand evidence gaps, top seed, and average seed score before export, and marks
each seed as seed-sample, add-second, expand-samples, or keep-watching based on
current sample count. A brand portfolio overview summarizes evidence coverage,
core-brand gaps, premium heat, and weight drift into one health score with jump
and sample actions plus `lolita-brand-portfolio.csv` export, so the dashboard
has a first-pass answer for whether today's work is sampling, price checking, or
weight review. A brand-weight rubric splits
the 0-100 scale into core
release, priority watch, sample-seed, and archive lanes, showing each lane's
brand count, average weight, average premium, sample gaps, lead label, and a
one-click sample-gap action so AP/BABY/AATP-style priorities are easier to
explain before changing weights. A brand playbook then turns each highest-priority
label into an execution card with current weight, formula target, confidence,
average premium, sample count, primary market term, direct search links, and
next action such as adding a price anchor, adding a second sample, tracking
premium spread, preparing a weight raise, or cooling down. A draft audit list shows the
exact saved weight, draft weight, and
delta for every unsaved brand, plus average shift, raise/lower counts, and the
largest move before you persist changes. The same audit flags save-before-review
risks such as lowering core brands, raising thin-evidence brands, making large
weight moves, or promoting archive labels into the watch tier, and the toolbar
status includes the current risk count while a draft is active. The current
saved/draft brand-weight table can also be exported as CSV with identity
metadata, premium evidence, sample counts, market keywords, and an auditable
formula target for spreadsheet review. The
formula panel breaks each target into baseline tier weight, resale premium,
sample evidence, keyword depth, and watch-link readiness; applying a formula
target, from either the formula cards or the trajectory path, only creates an
unsaved draft until you choose to save the brand weights.
Each brand entry can also carry `visual`
metadata (`palette`, `accent`, `paper`, `motif`, and `radar_cue`) plus
`watch_urls` search links so AP, BABY, AATP, and other labels keep distinct
Lolita identities across the dashboard and expose quick secondary-market watch
entry points from the brand identity matrix.
Each brand card also explains the current weight band, monitoring intent, and
keyword coverage so the score is easier to audit before changing it. The API
also exposes `brand_weight_profile`, a
full-brand view with weight band, weight role, evidence level, score breakdown,
and market keywords, plus `market_alerts` for critical/watch/sample-gap
recommendations. A weight-tuning queue turns current premium, sample count, and
weight into concrete actions such as collecting price samples, considering an
upgrade, holding, or cooling down; the action buttons can prefill the sample form
or apply a suggested target weight as an unsaved draft. When multiple suggestions
change weights, the dashboard can batch-apply all suggested target weights as a
draft before you decide whether to save or reset them.
Use `market_keywords` on each brand for secondary-market search/sample seeds;
these are kept separate from broad source-matching `keywords` to avoid noisy
alerts from generic item names.

Use another brand-weight file:

```bash
python -m lolita_radar.cli web --brands config/brand_weights.json
```

Market observations live in
[config/market_observations.json](./config/market_observations.json). Add local
price samples with the same currency for `retail_price` and `resale_price`; the
dashboard calculates premium rates and brand-level averages. You can edit this
file directly or add samples from the web dashboard's resale-premium form, which
previews premium rate, spread, and a single-sample score before submission.
Sample links and notes are kept as evidence, and matching pattern-premium cards
show the highest-premium evidence rows that support the radar score. Once a
brand has at least two samples, the dashboard also calculates a momentum row
with the latest premium, previous average, delta, direction, and weighted score.
Brand premium rows now include a price corridor with average retail/resale
prices, observed price ranges, average spread, and a band label for the brand's
average premium, making it easier to compare original-price anchors against
secondary-market asking prices.
The dashboard also builds a sample collection plan from brand weights and
current evidence gaps: core brands target five resale samples, watch brands
target three, and archive brands target two. Each task carries suggested market
keywords, watch links, missing-sample counts, and a button to prefill the sample
form for the next observation. Market keywords in each task can also be clicked
to prefill both the brand and item/pattern term for faster secondhand-price
sampling. The same task queue can be exported as `lolita-sample-plan.csv` for
spreadsheet review or a shopping/research checklist.
The sample plan board also shows completion rate, open brand count, core gaps,
total missing samples, and average task priority so the next research session is
easy to scan.
Each sample
also gets a quality score based on source, URL, observed date, condition, and
notes. Each recorded sample also gets a `premium_band` so the dashboard can
filter collector-level, hot, ordinary premium, near-retail, and discount samples.
Brand premium rows include a weighted priority score that combines resale
premium, sample count, and the configured brand weight.

```json
[
  {
    "brand_alias": "AP",
    "item_name": "Example JSK",
    "retail_price": 2000,
    "resale_price": 3200,
    "currency": "CNY",
    "condition": "used",
    "source": "xianyu",
    "url": "https://example.com/listing",
    "observed_at": "2026-06-29",
    "notes": "with headbow"
  }
]
```

Use another market-observation file:

```bash
python -m lolita_radar.cli web --market config/market_observations.json
```

Use a custom database path:

```bash
python -m lolita_radar.cli check --all --db .data/lolita_radar.sqlite
```

The first run stores a baseline and emits `new_item` events for first-seen
items. Later runs only emit events for new items or changed title/status.

## GitHub Actions

The workflow in [.github/workflows/check.yml](./.github/workflows/check.yml)
supports:

- `workflow_dispatch` for manual runs.
- `schedule` for cron-based checks.

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

`metamorphose`

- Fetches the Metamorphose English News page.
- Extracts `title`, `url`, and `published_at` when a date is present.
- Classifies each item as `new_arrival`, `preorder`, `restock`, or `shop_news`
  from title keywords.

`generic_page`

- Fetches any public URL.
- Extracts visible text.
- Emits one synthetic page item when configured keywords match.

## Roadmap

- More official brand adapters.
- Better page-specific parsers for Taobao proxy shops.
- Persistent GitHub Actions cache.
- Structured release calendar export.
