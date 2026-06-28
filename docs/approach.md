# Approach

This project borrows architecture patterns from existing monitor projects. The
first milestone is narrow: detect new items and notify a human. The next product
direction is documented in [north-star.md](./north-star.md): a Lolita premium
radar that ranks brands, series, and releases worth watching.

## Repository Patterns Considered

- Web page change monitors, such as
  [changedetection.io](https://github.com/dgtlmoon/changedetection.io), show
  that the stable core is: fetch page, normalize useful content, compare with
  stored state, send notifications.
- Stock monitor projects, such as
  [Shopify_Stock_Checker](https://github.com/Kuuuube/Shopify_Stock_Checker),
  commonly separate target lists, polling, filtering, and webhook delivery. This
  project follows that split instead of baking everything into one script.
- Taobao/Tmall scraper demos, such as
  [DrissionPage_taobao_monitor_shop](https://github.com/xiuyegege/DrissionPage_taobao_monitor_shop),
  usually need browser automation once pages depend on login cookies or
  JavaScript rendering. This project starts with a plain HTTP/file fetcher and
  keeps a Playwright fetcher behind config for that reason.
- Auto-purchase projects exist, but they tend to depend on fragile UI automation,
  login state, risk controls, and payment flows. This project intentionally does
  not implement auto-order or auto-pay.

## Milestones

1. Local HTML/HTTP monitor with keyword, price, dedupe, and console/webhook
   notifications.
2. Playwright login-profile workflow for Taobao pages that need rendering.
3. Mobile-friendly push targets such as Bark, ServerChan, Feishu, or WeCom.
4. More precise extractors for specific shop page layouts after real target URLs
   are known.
5. Local dashboard for target editing, config saving, manual scans, and scan
   results.

## Boundaries

The intended workflow is alert-first:

1. Monitor sees a matching new item.
2. Phone/desktop notification opens the product URL.
3. A person reviews size, color, price, shipping, and purchase terms.
4. A person completes checkout in the official app or browser.

This keeps the tool useful without trying to bypass platform controls.
