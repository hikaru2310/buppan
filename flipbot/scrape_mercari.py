from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup


# 販売中のみ・新着順（新しく出品されたものから見る）
MERCARI_SEARCH_URL = "https://jp.mercari.com/search?keyword={q}&status=on_sale&sort=created_time&order=desc"
MERCARI_KEYWORD_URL = "https://jp.mercari.com/search?keyword={q}"


@dataclass(frozen=True)
class MercariListing:
    title: str
    price_jpy: int
    shipping_included: bool
    status: str
    url: str
    item_id: Optional[str]
    image_url: Optional[str] = None
    # 「現在 ¥」表記＝オークション形式（価格が上がる可能性あり）
    is_auction: bool = False


_PRICE_RE = re.compile(r"([0-9][0-9,]*)\s*円")
_DIGITS_RE = re.compile(r"[0-9][0-9,]*")


def _parse_price(text: str) -> Optional[int]:
    m = _PRICE_RE.search(text.replace(" ", " "))
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _normalize_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"https://jp.mercari.com{href}"


def _item_id(href: str) -> str:
    return href.split("?")[0].strip("/").split("/")[-1]


def _parse_cells(soup: BeautifulSoup) -> list[MercariListing]:
    """2025年以降の検索結果（li[data-testid=item-cell]）。"""
    out: list[MercariListing] = []
    for cell in soup.select('[data-testid="item-cell"]'):
        a = cell.select_one('a[href*="/item/"], a[href*="/shops/product/"]')
        if not a:
            continue
        href = a.get("href") or ""
        name_el = cell.select_one('[data-testid="thumbnail-item-name"]')
        img = cell.select_one("img")
        title = name_el.get_text(" ", strip=True) if name_el else ""
        if not title and img and img.get("alt"):
            title = re.sub(r"のサムネイル$|の画像.*$", "", img["alt"])
        price_el = cell.select_one('[data-testid="item-tile-price"], [data-testid="price"]')
        price_text = price_el.get_text("", strip=True) if price_el else ""
        m = _DIGITS_RE.search(price_text)
        price = int(m.group(0).replace(",", "")) if m else _parse_price(a.get("aria-label") or "")
        if not title or price is None:
            continue
        text = cell.get_text(" ", strip=True)
        sold = bool(cell.select_one('[data-testid="thumbnail-sticker"]')) and ("SOLD" in text or "売り切れ" in text)
        out.append(
            MercariListing(
                title=title[:200],
                price_jpy=price,
                shipping_included="送料別" not in text,
                status="sold" if sold or "売り切れ" in text else "on_sale",
                url=_normalize_url(href.split("?")[0]),
                item_id=_item_id(href),
                image_url=img.get("src") if img else None,
                is_auction="現在" in price_text,
            )
        )
    return out


def _parse_legacy(soup: BeautifulSoup) -> list[MercariListing]:
    """旧マークアップ（アンカー内テキスト / aria-label に価格）。"""
    listings: list[MercariListing] = []
    seen_href: set[str] = set()
    for a in soup.select('a[href*="/item/"]'):
        href = (a.get("href") or "").strip()
        if not href or href in seen_href:
            continue
        low = href.lower()
        if any(x in low for x in ("help", "guide", "policy", "terms")):
            continue
        seen_href.add(href)
        thumb = a.select_one('[aria-label*="の画像"]')
        label = thumb.get("aria-label") if thumb else ""
        text = " ".join(a.stripped_strings) + " " + (label or "")
        price = _parse_price(text)
        if price is None:
            continue
        title = re.sub(r"の画像.*$", "", label) if label else a.get_text(" ", strip=True)
        if not title:
            continue
        listings.append(
            MercariListing(
                title=title[:200],
                price_jpy=price,
                shipping_included="送料込み" in text,
                status="sold" if ("SOLD" in text or "売り切れ" in text) else "on_sale",
                url=_normalize_url(href),
                item_id=_item_id(href),
            )
        )
    return listings


def parse_mercari_search_html(html: str) -> list[MercariListing]:
    """Mercari のマークアップは頻繁に変わるので、新旧どちらでも拾えるようにしている。"""
    soup = BeautifulSoup(html, "lxml")
    listings = _parse_cells(soup) or _parse_legacy(soup)
    # Deduplicate by URL keeping cheapest
    by_url: dict[str, MercariListing] = {}
    for it in listings:
        prev = by_url.get(it.url)
        if prev is None or it.price_jpy < prev.price_jpy:
            by_url[it.url] = it
    return list(by_url.values())


def build_mercari_search_url(query: str) -> str:
    return MERCARI_SEARCH_URL.format(q=quote(query))


def build_mercari_keyword_url(query: str) -> str:
    return MERCARI_KEYWORD_URL.format(q=quote(query))
