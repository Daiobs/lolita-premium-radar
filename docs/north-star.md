# North Star Plan: Lolita Premium Radar

## North Star

Build a local decision system that answers one question:

> Which Japanese Lolita brands, series, and upcoming releases are worth watching
> because market resale prices reliably exceed estimated landed cost?

The product should turn scattered release pages, archive records, secondhand
prices, and Taobao listings into a ranked watchlist that can feed the existing
new-arrival alert workflow.

## North Star Metric

**High-signal watchlist hit rate**

The share of watched releases that later show a resale premium above the chosen
threshold within a review window.

Default threshold:

- Premium ratio >= 30%
- At least 3 comparable market samples
- At least 2 independent source types when possible
- Review window: 30, 60, and 90 days after release or domestic arrival

This metric keeps the project focused on useful decisions rather than raw scrape
volume.

## Guardrail Metrics

- False-positive rate: watched items that never show premium or liquidity.
- Data confidence: how many price points and source types support a conclusion.
- Freshness: how recently each source was checked.
- Manual verification load: how many records need human cleanup before use.
- Alert usefulness: how often an alert maps to a real item worth opening.

## Core Entities

### Brand

- name
- aliases
- country
- official_site_url
- notes

### Series

- brand
- series_name
- release_date
- release_region
- source_url
- tags, such as print, solid, collaboration, re-release

### Item

- brand
- series_name
- item_name
- category, such as JSK, OP, skirt, blouse, headbow, bag
- colorway
- size
- original_price_jpy
- original_price_cny_estimate
- source_url
- image_url

### Price Sample

- item_id
- source_type, such as xianyu, taobao, wunderwelt, closet_child, mercari
- source_url
- title
- listed_price_cny
- sold_price_cny
- condition
- listing_status, such as listed, sold, unknown
- captured_at
- confidence

### Watch Target

- brand
- series_name
- keywords
- expected_release_time
- priority
- reason
- target_sources, such as brand site, Taobao shop, Xianyu search

## Price Model

Estimated landed cost:

```text
landed_cost_cny =
  original_price_jpy * jpy_to_cny
  + japan_domestic_shipping_cny
  + international_shipping_cny
  + proxy_fee_cny
  + tax_or_buffer_cny
```

Market median:

```text
market_median_cny = median(valid comparable secondhand samples)
```

Premium:

```text
premium_cny = market_median_cny - landed_cost_cny
premium_ratio = premium_cny / landed_cost_cny
```

淘宝 markup:

```text
taobao_markup_cny = taobao_price_cny - landed_cost_cny
taobao_markup_ratio = taobao_markup_cny / landed_cost_cny
```

## Ranking Formula

Each item or series gets a 0-100 attention score:

```text
attention_score =
  premium_score * 0.45
  + liquidity_score * 0.25
  + release_signal_score * 0.15
  + confidence_score * 0.15
```

Premium score:

- 100: premium_ratio >= 80%
- 80: premium_ratio >= 50%
- 60: premium_ratio >= 30%
- 30: premium_ratio >= 10%
- 0: below 10% or negative

Liquidity score:

- Higher when there are more comparable samples, recent sales, and repeated
  search demand.
- Lower when prices are only high asking prices with no sale signal.

Release signal score:

- Higher for new releases from historically premium brands or series.
- Higher for rare colorways, limited reservation windows, collaborations, or
  popular cuts.
- Lower for frequent re-releases or items with abundant supply.

Confidence score:

- Higher when brand, series, colorway, and item category match cleanly across
  sources.
- Lower when samples are fuzzy, titles are ambiguous, or only one source exists.

## Priority Bands

- **A: Watch now**: attention_score >= 75. Add to alert targets immediately.
- **B: Observe**: 55-74. Collect more samples before acting.
- **C: Archive**: 35-54. Keep for history, do not actively monitor.
- **D: Ignore**: below 35. Not worth alert noise.

## Data Source Strategy

### Phase 1: Manual and Semi-Structured Data

Start with CSV/JSON import and manual entry. This is the fastest way to avoid
being blocked by login pages or anti-bot controls while validating the scoring
model.

Sources:

- Lolibrary or official brand pages for original prices and release data.
- Taobao product links and shop pages already supported by the monitor.
- Manually collected Xianyu comparable listings.
- Japanese secondhand shops such as Wunderwelt and Closet Child.

### Phase 2: Assisted Collection

Add local browser-assisted collection for pages that require login or rendering.
The user can inspect and verify records before saving them.

### Phase 3: Automated Watchlist Feed

High-priority brands and series generate monitor targets automatically:

- Brand official release page
- Taobao shop search or new-arrival page
- Xianyu search terms for post-release tracking

## MVP Milestones

### M1: Premium Data Library

Goal: store brands, items, and price samples locally.

Build:

- SQLite storage.
- CLI import for CSV price samples.
- Basic Web pages for item list and price sample list.
- Manual add/edit form.

Acceptance:

- Can enter 20 items and 100 price samples.
- Can view item-level landed cost, market median, and premium ratio.

### M2: Premium Ranking

Goal: rank brands, series, and items by attention score.

Build:

- Premium calculation.
- Liquidity and confidence scoring.
- Web "Premium Radar" page with filters by brand, category, and priority.

Acceptance:

- Produces A/B/C/D priority bands.
- Shows why each item received its score.
- Can export the A-priority watchlist.

### M3: Watchlist Integration

Goal: feed high-priority targets into the existing alert monitor.

Build:

- Generate monitor targets from A-priority items.
- Add source-specific keywords and exclusions.
- Show linked scan results from the current dashboard.

Acceptance:

- A-priority item can become a monitor target with one action.
- Manual scan checks the generated target.

### M4: Browser-Assisted Collection

Goal: make Xianyu/Taobao collection easier without bypassing platform controls.

Build:

- Save manually verified price samples from a browser-visible page.
- De-duplicate by source URL and normalized title.
- Track confidence and notes.

Acceptance:

- User can collect comparable samples in minutes instead of maintaining a
  separate spreadsheet.
- Records remain reviewable and editable.

### M5: Review Loop

Goal: learn whether the radar's predictions were useful.

Build:

- Mark watched releases as hit, miss, or pending.
- Compare predicted premium with observed 30/60/90-day prices.
- Brand and series historical performance page.

Acceptance:

- High-signal watchlist hit rate is visible.
- Ranking weights can be adjusted based on misses.

Status:

- Basic local review ledger is implemented: item-level pending/hit/miss status,
  observed price, observed premium, prediction snapshot, and hit-rate summary.
- Next refinement: brand/series historical performance views and weight
  adjustment based on repeated misses.

## First Implementation Slice

The next coding task should be M1 plus the simplest part of M2:

1. Add SQLite tables for brands, items, price samples, and radar scores.
2. Add a small sample CSV import path.
3. Add premium calculation using median secondhand price.
4. Add a Web "Premium Radar" tab showing item, landed cost, median price,
   premium ratio, sample count, confidence, and priority band.
5. Add tests for landed cost, median price, and priority band calculation.

Current implementation status:

- SQLite radar schema added in `src/tb_new_arrival_alert/radar.py`.
- Landed cost, market median, premium ratio, attention score, and A/B/C/D bands
  are implemented.
- Local Web API `GET /api/radar` returns ranked radar results from
  `.data/radar.sqlite`.
- Local Web APIs can create, update, and delete items; add price samples; and
  import CSV rows.
- Radar results distinguish secondhand premium from Taobao/proxy markup.
- Brand and series aggregate rankings roll item scores into broader watch
  priorities.
- Release dates and source URLs are stored with radar items and shown through a
  release watch list sorted by upcoming dates first.
- A collection queue flags missing original price, release date, source URL,
  secondhand samples, and Taobao/proxy samples so data maintenance has a clear
  next action. Queue actions can load the item or sample form with the relevant
  item and suggested source preselected.
- A watch recommendation board highlights high-priority, premium, or upcoming
  items, marks items that already have generated monitor targets, and loads the
  monitor-target form with the relevant item preselected.
- Brand release page text can be pasted locally; lines with JPY prices are
  parsed into radar items with original prices, release dates, and source URLs.
- The dashboard has a "溢价雷达" tab for item maintenance, data entry, CSV
  import, and ranking results.
- Browser-visible listing text can be pasted into a local assisted collector,
  parsed into price samples, reviewed in a sample ledger, edited, and deleted
  if wrong.
- Watched releases can be marked as pending, hit, or miss in a local review
  ledger. The review record stores observed price, 30/60/90-day window,
  observed premium ratio, prediction snapshot, and hit-rate summary.
- High-priority radar items can generate targets in the existing new-arrival
  monitor configuration.
- Unit tests cover scoring, item/brand/series ranking, item update/delete, price
  sample update/delete, collection task generation, release watch sorting,
  release text parsing, CSV import, pasted-text collection, review-loop storage,
  watch-target generation, and Web API integration.

## Explicit Non-Goals

- No automatic purchase.
- No CAPTCHA bypass.
- No scraping behind platform risk controls.
- No investment or profit guarantee.
- No alerting on items with weak or unverified data unless explicitly marked as
  low-confidence.
