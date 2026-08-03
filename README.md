# Scurfa M.S.26 cloud stock watch

Watches [scurfawatches.com](https://www.scurfawatches.com) for the **M.S.26 Diver One
Limited Edition** (blue + black, £334, 150 pieces) and emails you the moment it drops.

Runs entirely on GitHub Actions — no laptop, no server, no credentials.

## How the alert reaches your inbox

A hit opens a GitHub issue in this repo. You automatically watch your own repos, so
GitHub emails you the issue. That's the whole mechanism — no SMTP, no app password,
no secrets to configure.

The job then deliberately exits non-zero, which makes GitHub send a *second*
"workflow failed" email. Two independent emails for one drop.

## How many emails to expect

Every alert is deduplicated through an open issue, so a condition emails you **once**,
not once per run.

| Situation | Emails |
|-----------|--------|
| Out of stock (the normal case, every 5 min) | **none** — completely silent |
| M.S.26 drops | **2** (the issue + the deliberate job failure), then silent |
| Watcher can't reach the site | **1**, then silent until it recovers |
| A new M.S.26 listing appears | **1** |

The blind-monitor case is why it opens an issue rather than failing the job: failing
would email you on *every* run for the whole outage — roughly 288 emails a day. It also
closes that issue by itself once fetches start working again.

Nothing here is silent-by-default in the dangerous direction: if the watcher stops
working, it tells you rather than letting you assume you're still covered.

## Detection

Four independent signals; any one fires the alarm. The site runs WooCommerce:

| # | Signal | While unreleased | On drop |
|---|--------|------------------|---------|
| 1 | JSON-LD `offers.availability` | `OutOfStock` | `InStock` / `PreOrder` |
| 2 | Product body class | `outofstock` | `instock` |
| 3 | `<p class="stock ...">` | "COMING SOON" | "In stock" |
| 4 | `form.cart` + `add-to-cart` | **absent entirely** | present |

It also sweeps `/shop/` and `/categories/limited-edition/` for a *new* M.S.26 slug,
catching a third variant launching at a URL nobody is watching.

⚠️ **Do not use the "Add to basket" text as a signal.** It appears 9 times on the page
*while sold out* — it belongs to the related-products carousel. Every signal above is
scoped to the M.S.26 product itself (`product_tag-ms26` / `post-26349`).

Detection leans toward firing: for a 150-piece limited edition, a false alarm costs a
glance and a miss costs the watch.

## Timing

GitHub's cron is not punctual — scheduled runs get queued and can land minutes late.
So the workflow runs every 5 minutes *and* each run loops internally 5 times at ~55s
intervals, covering its own window. Effective resolution is about one minute, without
depending on GitHub firing on time.

Tune via `PASSES` and `GAP_SECONDS` in `.github/workflows/watch.yml`.

## Keeping it alive

GitHub disables scheduled workflows after 60 days of repo inactivity. The last step
writes today's UTC date to `last_seen.txt` and commits only when it changes — exactly
one commit per day, which is enough to keep the schedule running indefinitely.

## After a drop

Close the alert issue to re-arm. While an alert issue is open, the workflow won't open
another, so you get one email per drop instead of one every five minutes.

## Testing it

Run the detection tests against real saved HTML, mutated the ways WooCommerce would
change on a drop:

```bash
python3 check_stock.py           # live check, exits 0 (out of stock) or 7 (in stock)
```

Trigger a real run by hand from the repo's **Actions** tab → *Scurfa M.S.26 stock
watch* → **Run workflow**.

## Note on Actions usage

This is a scheduled poller on free public-repo minutes. It is a light job, but it isn't
software CI, which is what GitHub Actions is nominally for. Public repos have unlimited
minutes so there's no cost, just be aware it's an unconventional use.
