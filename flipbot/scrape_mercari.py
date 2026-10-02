from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup


MERCARI_SEARCH_URL = "https://jp.mercari.com/search?keyword={q}"


@dataclass(frozen=True)
class MercariListing:
    title: str
    price_jpy: int
    shipping_included: bool
    status: str
    url: str
    item_id: Optional[str]


_PRICE_RE = re.compile(r"([0-9][0-9,]*)\s*円")


def _parse_price(text: str) -> Optional[int]:
    m = _PRICE_RE.search(text.replace("\u00a0", " "))
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _normalize_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"https://jp.mercari.com{href}"


def parse_mercari_search_html(html: str) -> list[MercariListing]:
    """
    Mercari's markup changes frequently. We keep parsing tolerant:
    - look for product card anchors linking to /item/...
    - extract title and price from nearby text
    """
    soup = BeautifulSoup(html, "lxml")
    listings: list[MercariListing] = []

    anchors = soup.select('a[href*="/item/"]')
    seen_href: set[str] = set()
    for a in anchors:
        href = (a.get("href") or "").strip()
        if not href or href in seen_href:
            continue
        if "/item/" not in href:
            continue
        low = href.lower()
        if any(x in low for x in ("help", "guide", "policy", "terms")):
            continue
        seen_href.add(href)
        url = _normalize_url(href)
        item_id = href.strip("/").split("/")[-1] if "/item/" in href else None

        text = " ".join(a.stripped_strings)
        price = _parse_price(text)
        if price is None:
            continue

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        # Heuristic: if the card contains '送料込み' / '送料別'
        shipping_included = "送料込み" in text
        status = "on_sale"
        if "SOLD" in text or "売り切れ" in text:
            status = "sold"

        listings.append(
            MercariListing(
                title=title[:200],
                price_jpy=price,
                shipping_included=shipping_included,
                status=status,
                url=url,
                item_id=item_id,
            )
        )

    # Deduplicate by URL keeping cheapest
    by_url: dict[str, MercariListing] = {}
    for it in listings:
        prev = by_url.get(it.url)
        if prev is None or it.price_jpy < prev.price_jpy:
            by_url[it.url] = it
    return sorted(by_url.values(), key=lambda x: x.price_jpy)


def build_mercari_search_url(query: str) -> str:
    return MERCARI_SEARCH_URL.format(q=quote(query))

