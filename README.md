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
simple premium-signal score for prioritizing attention.

Brand weights live in [config/brand_weights.json](./config/brand_weights.json).
The default first-pass priority is AP 100, BABY 95, AATP 90, Meta 80, MMM 75,
and IW/VM/MM/JetJ 65. The dashboard also builds a focus queue from those weights
and the currently observed items/events.

Use another brand-weight file:

```bash
python -m lolita_radar.cli web --brands config/brand_weights.json
```

Market observations live in
[config/market_observations.json](./config/market_observations.json). Add local
price samples with the same currency for `retail_price` and `resale_price`; the
dashboard calculates premium rates and brand-level averages. You can edit this
file directly or add samples from the web dashboard's resale-premium form. Brand
premium rows include a weighted priority score that combines resale premium,
sample count, and the configured brand weight.

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
    "observed_at": "2026-06-29"
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
