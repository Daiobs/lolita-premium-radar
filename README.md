# Lolita Premium Radar

Local release monitor and resale premium radar for Lolita and Japanese fashion items.

This project is intentionally an alerting helper, not an auto-purchase bot. It
can watch configured pages, remember previously seen item IDs, filter by
keywords and price, then notify you when matching new items appear.

## What It Does

- Watches one or more shop/listing/search URLs.
- Extracts Taobao/Tmall item links from HTML.
- Uses a local JSON state file to detect newly seen items.
- Filters by include/exclude keywords and optional price bounds.
- Sends notifications to the console and optional HTTP webhooks.
- Supports a future Playwright-backed browser fetcher for pages that need login
  or JavaScript rendering.

## What It Does Not Do

- It does not bypass CAPTCHAs, queue systems, risk controls, or login checks.
- It does not submit orders, auto-pay, or operate multiple accounts.
- It does not scrape aggressively. Keep polling intervals conservative.

## Quick Start

```bash
cd lolita-premium-radar
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m tb_new_arrival_alert init --output config.json
```

Edit `config.json`, then run a first baseline scan:

```bash
python -m tb_new_arrival_alert once --config config.json
```

Run continuously:

```bash
python -m tb_new_arrival_alert run --config config.json
```

The first scan stores a baseline by default. New notifications start on later
scans unless `notify_on_first_scan` is set to `true`.

## Web Dashboard

Start the local dashboard:

```bash
python -m tb_new_arrival_alert web --config config.json
```

Open:

```text
http://127.0.0.1:8766
```

If `8766` is busy, the app automatically tries the next ports and prints the
actual URL, such as `http://127.0.0.1:8767`. You can also choose a port:

```bash
python -m tb_new_arrival_alert web --config config.json --port 8788
```

The dashboard can edit global settings, add or remove targets, save `config.json`,
run a manual scan, and show scan results. It uses Python's built-in web server,
so no Node.js or frontend build step is required.

## Config Example

See [config.example.json](./config.example.json).

## Premium Radar

The next product direction is a Lolita premium radar: compare Japanese original
prices, secondhand market prices, and Taobao/proxy prices, then rank brands and
series worth watching. See [docs/north-star.md](./docs/north-star.md).

The first radar foundation is already in place: SQLite storage, core premium
scoring, Taobao/proxy markup metrics, `GET /api/radar`, dashboard data-entry
forms, CSV import, item/brand/series ranking results, and watch-target
generation for the existing new-arrival monitor.

Import sample radar rows:

```bash
python -m tb_new_arrival_alert radar-import --config config.json --csv examples/radar-samples.csv
```

The dashboard's "溢价雷达" tab can add, edit, and delete items; maintain original
prices, landed-cost assumptions, release dates, and source URLs; append, edit,
and delete price samples; import a CSV path; generate monitor targets; and
refresh the ranking. See [examples/radar-samples.csv](./examples/radar-samples.csv)
for the CSV shape. Secondhand sources such as `xianyu`, `mercari`, and
`wunderwelt` feed premium metrics; domestic sale sources such as `taobao`,
`proxy`, and `daigou` feed markup metrics.

The brand/series board rolls up item attention scores so you can see broader
watch priorities. Aggregate score uses 60% average item attention and 40% best
item attention, balancing stable brand history with standout releases.

The release watch board sorts dated items by upcoming release date first, then
recently released items, so new Japanese releases can be checked against the
premium ranking before you turn them into shop or search monitors.

The collection queue points out missing evidence per item: original price,
release date, source URL, secondhand samples, and Taobao/proxy samples. Use it
as the next-to-collect list when maintaining the radar. Queue actions can load
the matching item or sample form with the item and suggested source preselected.

The watch recommendation board highlights high-priority, premium, or upcoming
items that are worth turning into shop/search monitors. Its action loads the
existing monitor-target form with the item and suggested price ceiling. Items
that already have generated monitor targets are marked as watched.

The review ledger records whether watched releases were later a hit, miss, or
still pending. For each reviewed item, it stores the observed 30/60/90-day price,
the observed premium ratio, and the prediction snapshot, then shows a local hit
rate for the radar's high-signal watchlist.

For official release collection, copy visible lines from a brand page and paste
them into the "粘贴发售行" box. Lines with JPY prices, such as
`2026年7月15日 Sample Print JSK Pink ¥32,780 https://...`, are parsed into radar
items with original prices, release dates, and source URLs.

For assisted collection, copy visible listing lines from a browser page and
paste them into the "粘贴商品行" box. Lines like `AP JSK Pink ￥2800 https://...`
are parsed into price samples for the selected item. The sample ledger below the
radar ranking lets you review, edit, and delete mistakes.

Important fields:

- `poll_interval_seconds`: keep this at 60 seconds or higher for real shops.
- `targets[].url`: a shop all-items page, shop search page, or saved search URL.
- `targets[].include_keywords`: at least one keyword must match title/text.
- `targets[].exclude_keywords`: any match suppresses the item.
- `notifications`: console is enabled by default; webhook can be enabled later.

## Taobao Notes

Taobao pages often rely on JavaScript, cookies, and app/browser login state. The
plain HTTP fetcher is useful for static pages, exported/saved HTML, and simple
tests. If a target page returns a login screen or blank shell, switch the config
fetcher to `playwright` after installing Playwright:

```bash
python -m pip install playwright
python -m playwright install chromium
```

Then set:

```json
{
  "fetcher": {
    "type": "playwright",
    "headless": false,
    "user_data_dir": ".browser-profile",
    "wait_seconds": 5
  }
}
```

On first browser run, log in manually in the opened browser profile. The monitor
will reuse that local profile.

## Webhook Notifications

Webhook notification posts JSON like:

```json
{
  "target": "shop name",
  "title": "item title",
  "url": "https://item.taobao.com/item.htm?id=...",
  "price": 399.0
}
```

You can point this at a small local service, ServerChan, Bark-compatible bridge,
Feishu/WeCom webhook adapter, or a personal notification endpoint.

## Development

```bash
python -m unittest discover -s tests
```
