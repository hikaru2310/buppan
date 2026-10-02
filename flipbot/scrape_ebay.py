from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup


EBAY_SOLD_URL = (
    "https://www.ebay.com/sch/i.html"
    "?_nkw={q}"
    "&LH_Sold=1"
    "&LH_Complete=1"
    "&rt=nc"
)


@dataclass(frozen=True)
class EbaySold:
    title: str
    price_usd: float
    shipping_usd: Optional[float]
    total_usd: float
    url: str


# eBay price strings vary: "US $12.34", "$12.34", "US $1,234.56" etc.
_USD_RE = re.compile(r"(?:US\s*)?\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)")


def build_ebay_sold_url(query: str) -> str:
    return EBAY_SOLD_URL.format(q=quote_plus(query))


def _parse_usd(text: str) -> Optional[float]:
    m = _USD_RE.search(text.replace("\u00a0", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


_TITLE_JUNK_RE = re.compile(r"^(new listing|新規出品)\s*|\s*(opens in a new window or tab|新しいウィンドウまたはタブで開く)$", re.I)


def _parse_ebay_li(li) -> Optional[EbaySold]:
    """旧 s-item / 新 s-card（2025〜）の両方に対応。"""
    a = (
        li.select_one("a.s-item__link")
        or li.select_one("a.su-card-container__header")
        or li.select_one('a[href*="/itm/"]')
    )
    if not a:
        return None
    url = (a.get("href") or "").split("?")[0]
    if not url.startswith("http") or "/itm/" not in url or url.endswith("/itm/123456"):
        return None
    title_el = (
        li.select_one(".s-card__title")
        or li.select_one(".s-item__title")
        or li.select_one('[role="heading"]')
        or a
    )
    title = _TITLE_JUNK_RE.sub("", title_el.get_text(" ", strip=True)) if title_el else ""
    if not title or title.lower() in ("shop on ebay", "results matching fewer words"):
        return None

    price_el = li.select_one(".s-card__price, .s-item__price") or li.select_one('[class*="price"]')
    price_text = price_el.get_text(" ", strip=True) if price_el else li.get_text(" ", strip=True)
    if " to " in price_text:  # 価格帯表示（バリエーション）は相場がぶれるので除外
        return None
    price_usd = _parse_usd(price_text)
    if price_usd is None:
        return None

    shipping_usd: Optional[float] = None
    ship_el = li.select_one(".s-item__shipping, .s-item__logisticsCost") or li.select_one(
        '[class*="s-item__shipping"], [class*="logisticsCost"]'
    )
    ship_text = ship_el.get_text(" ", strip=True) if ship_el else ""
    if not ship_text:
        # s-card は配送料が独立クラスを持たないので、行テキストから拾う
        for t in li.stripped_strings:
            if re.search(r"delivery|shipping|送料", t, re.I):
                ship_text = t
                break
    if ship_text:
        if re.search(r"free|無料", ship_text, re.I):
            shipping_usd = 0.0
        else:
            shipping_usd = _parse_usd(ship_text)

    total_usd = price_usd + (shipping_usd or 0.0)
    return EbaySold(
        title=title[:200],
        price_usd=price_usd,
        shipping_usd=shipping_usd,
        total_usd=total_usd,
        url=url,
    )


def parse_ebay_sold_html(html: str) -> list[EbaySold]:
    soup = BeautifulSoup(html, "lxml")
    items: list[EbaySold] = []

    lis = list(soup.select("li.s-card, li.s-item"))
    if not lis:
        lis = [li for li in soup.find_all("li") if li.select_one('a[href*="/itm/"]')]

    for li in lis:
        sold = _parse_ebay_li(li)
        if sold is not None:
            items.append(sold)

    # Dedup by URL
    seen: set[str] = set()
    out: list[EbaySold] = []
    for it in items:
        if it.url in seen:
            continue
        seen.add(it.url)
        out.append(it)
    return out


def build_ebay_research_url(query: str) -> str:
    """Terapeak（eBay公式の販売実績リサーチ。セラーハブにログインが必要）。"""
    return (
        "https://www.ebay.com/sh/research?marketplace=EBAY-US&tabName=SOLD&dayRange=90&keywords="
        + quote_plus(query)
    )


def median_total_usd(items: list[EbaySold], *, min_samples: int = 3) -> Optional[float]:
    vals = [x.total_usd for x in items if x.total_usd > 0]
    if len(vals) < min_samples:
        return None
    return float(median(vals))

