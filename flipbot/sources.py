"""同梱サンプルデータ（実サイトが取れない時の代替・デモ用）。"""
from __future__ import annotations

import json
from pathlib import Path

from .config import Target
from .scrape_ebay import EbaySold, build_ebay_sold_url
from .scrape_mercari import MercariListing, build_mercari_search_url

SAMPLE_PATH = Path(__file__).parent / "fixtures" / "sample.json"


def _raw() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def sample_targets() -> list[Target]:
    return [Target.model_validate(g) for g in _raw()["genres"]]


def sample_items(target_id: str) -> tuple[list[MercariListing], list[EbaySold]]:
    raw = _raw()
    mercari = [
        MercariListing(
            title=m["title"],
            price_jpy=int(m["price_jpy"]),
            shipping_included=bool(m["shipping_included"]),
            status="on_sale",
            # サンプルは実在の出品ではないので、タイトル検索ページへ飛ばす
            url=build_mercari_search_url(m["title"]),
            item_id=m["item_id"],
        )
        for m in raw["mercari"].get(target_id, [])
    ]
    ebay = [
        EbaySold(
            title=e["title"],
            price_usd=float(e["price_usd"]),
            shipping_usd=float(e["shipping_usd"]),
            total_usd=float(e["price_usd"]) + float(e["shipping_usd"]),
            url=build_ebay_sold_url(e["title"]),
        )
        for e in raw["ebay"].get(target_id, [])
    ]
    return mercari, ebay
