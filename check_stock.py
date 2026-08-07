#!/usr/bin/env python3
"""
Scurfa M.S.26 stock check — cloud edition (GitHub Actions).

Same four-signal detection as the local watcher, but with no macOS bits. On a
hit it prints a GITHUB_OUTPUT flag; the workflow turns that into a GitHub issue,
which GitHub emails to the repo owner.

Exit codes:
    0  checked fine, still out of stock
    7  IN STOCK  (workflow turns this into an issue + failure email)
    1  every attempt failed to fetch (workflow fails -> you get told it is blind)
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

PRODUCTS = [
    {
        "key": "blue",
        "name": "M.S.26 Blue Stainless Diver One LE",
        "url": "https://www.scurfawatches.com/product/m-s-26-blue-stainless-diver-one-limited-edition/",
    },
    {
        "key": "black",
        "name": "M.S.26 Black Stainless Diver One LE",
        "url": "https://www.scurfawatches.com/product/m-s-26-black-stainless-diver-one-limited-edition/",
    },
]

INDEX_PAGES = [
    "https://www.scurfawatches.com/shop/",
    "https://www.scurfawatches.com/categories/limited-edition/",
]

BUYABLE = ("instock", "preorder", "backorder", "limitedavailability", "onlineonly")


def log(msg):
    print("%s  %s" % (datetime.utcnow().strftime("%H:%M:%S"), msg), flush=True)


def fetch(url, timeout=25):
    """Return (html, error). Cache-busted so we never read a stale edge copy."""
    bust = "%s_=%d" % ("&" if "?" in url else "?", int(time.time()))
    req = urllib.request.Request(
        url + bust,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            if resp.status != 200:
                return None, "HTTP %s" % resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return None, "fetch error: %s" % exc
    if len(body) < 2000:
        return None, "suspiciously short response (%d bytes)" % len(body)
    return body, None


def check_page(html):
    """
    Four independent in-stock signals; any one fires.

    For a 150-piece limited edition a false alarm costs a glance and a miss
    costs the watch, so this leans deliberately toward firing.
    """
    signals = []
    detail_bits = []

    # 1) JSON-LD Product node — the authoritative machine-readable answer.
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = data.get("@graph") if isinstance(data, dict) and "@graph" in data else [data]
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict) or "Product" not in str(node.get("@type", "")):
                continue
            blob = "%s %s" % (node.get("name", ""), node.get("sku", ""))
            if "m.s.26" not in blob.lower() and "ms26" not in blob.lower().replace(".", ""):
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            avail = str(offers.get("availability", ""))
            detail_bits.append("schema=%s" % (avail.rsplit("/", 1)[-1] or "unknown"))
            if any(tok in avail.lower().replace(" ", "") for tok in BUYABLE):
                signals.append("schema.org availability = %s" % avail.rsplit("/", 1)[-1])

    # 2) WooCommerce product body class, scoped to the M.S.26 post.
    for cls in re.findall(r'class="([^"]*)"', html):
        low = cls.lower()
        if "product_tag-ms26" not in low and "post-26349" not in low:
            continue
        if re.search(r'(?<!out)\binstock\b', low):
            signals.append("product body class = instock")
            detail_bits.append("class=instock")
        elif "outofstock" in low:
            detail_bits.append("class=outofstock")
        break

    # 3) The visible stock paragraph: <p class="stock out-of-stock">COMING SOON</p>
    m = re.search(r'<p class="stock ([a-z-]+)"[^>]*>(.*?)</p>', html, re.S | re.I)
    if m:
        css, text = m.group(1).lower(), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        detail_bits.append('stock_p="%s"' % text[:40])
        if "out-of-stock" not in css:
            signals.append('stock label changed to "%s"' % text[:60])

    # 4) A real add-to-cart form. There are zero of these while unreleased, so
    #    any appearance is meaningful. ("Add to basket" text is NOT usable —
    #    it appears 9x from the related-products carousel while sold out.)
    if re.search(r'<form[^>]*class="[^"]*\bcart\b', html) and re.search(r'name="add-to-cart"', html):
        signals.append("add-to-cart form present")
        detail_bits.append("cart_form=yes")

    return bool(signals), signals, ", ".join(detail_bits) or "no markers found"


def scan_index(html, known_urls):
    """Find M.S.26 listings at URLs we are not already watching."""
    found = set()
    for url in re.findall(r'https://www\.scurfawatches\.com/product/[a-z0-9\-]+/', html, re.I):
        slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
        if "m-s-26" in slug or "ms26" in slug:
            found.add(url)
    return sorted(u for u in found if u not in known_urls)


def one_pass(scan_indexes=True):
    """
    One sweep of the product pages. Returns (hits, fetched_ok).

    `scan_indexes` is throttled by the caller: a new variant appearing is a
    slow-moving event, so sweeping the shop pages every ~10 minutes instead of
    every minute keeps request volume civil against a small business's site.
    """
    hits = []
    fetched_ok = 0

    for product in PRODUCTS:
        html, err = fetch(product["url"])
        if html is None:
            log("  %-6s FETCH FAILED — %s" % (product["key"], err))
            continue
        fetched_ok += 1
        available, signals, detail = check_page(html)
        if available:
            log("  %-6s *** IN STOCK *** %s" % (product["key"], "; ".join(signals)))
            hits.append({"name": product["name"], "url": product["url"], "signals": signals})
        else:
            log("  %-6s not yet — %s" % (product["key"], detail))

    known = set(p["url"] for p in PRODUCTS)
    for index_url in (INDEX_PAGES if scan_indexes else []):
        html, _ = fetch(index_url)
        if html is None:
            continue
        fetched_ok += 1
        for new_url in scan_index(html, known):
            known.add(new_url)
            log("  *** NEW M.S.26 LISTING *** %s" % new_url)
            hits.append({"name": "NEW M.S.26 listing", "url": new_url,
                         "signals": ["appeared on the shop/limited-edition page"]})

    return hits, fetched_ok


def emit_output(hits):
    """Hand the result to the workflow via GITHUB_OUTPUT."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    lines = ["in_stock=%s" % ("true" if hits else "false")]
    if hits:
        body = "\n".join(
            "- **%s**\n  %s\n  detected via: %s" % (h["name"], h["url"], "; ".join(h["signals"]))
            for h in hits
        )
        lines.append("names=%s" % ", ".join(h["name"] for h in hits))
        lines.append("body<<EOF_BODY\n%s\nEOF_BODY" % body)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    passes = int(os.environ.get("PASSES", "5"))
    gap = int(os.environ.get("GAP_SECONDS", "55"))

    index_every = int(os.environ.get("INDEX_EVERY", "10"))

    total_ok = 0
    for i in range(passes):
        log("pass %d/%d" % (i + 1, passes))
        hits, ok = one_pass(scan_indexes=(i % index_every == 0))
        total_ok += ok
        if hits:
            emit_output(hits)
            log("HIT — stopping early so the workflow can raise the alarm.")
            return 7
        if i < passes - 1:
            time.sleep(gap)

    emit_output([])
    if total_ok == 0:
        log("ERROR: every fetch failed across all passes — the monitor is blind.")
        return 1
    log("Done. Still out of stock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
