"""ジャンルの監視価値（KPIツリー 3章 D）。DBを読むだけで、スキャン結果や新着の記録は書き換えない。

監視価値（1日あたりの期待利益）＝ メルカリの新着/日 × 基準を満たす出品の割合 × 1件の利益（平均） × 購入成功率

相場は eBay の売れた相場（手動取り込み・Insights）を優先し、なければ出品中の価格 × active_discount を「暫定」として使う。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from . import fx as fx_mod
from . import plan as plan_mod
from .config import Config, Target
from .market import select_comps
from .matching import find_comps, market_price
from .pipeline import Learned, _cached_ebay, _is_excluded, active_genres, max_buy_for, mercari_inflow
from .profit import estimate_profit
from .scrape_ebay import EbaySold

# 事業計画「仕入れファネル」の購入成功 320 ÷ 購入判断 430
PURCHASE_SUCCESS_RATE = 320 / 430


def _mercari_window(con, target_id: str, days: float) -> list[dict[str, Any]]:
    """直近 days 日に見た出品（同じ出品は最新の値）。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT m.item_id, m.title, m.price_jpy, m.status, m.url FROM mercari_listings m JOIN snapshots s ON s.id=m.snapshot_id "
        "WHERE s.source='mercari' AND s.target_id=? AND s.captured_at > ? ORDER BY s.id",
        (target_id, since),
    )
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        latest[r["item_id"] or r["url"]] = dict(r)
    return [r for r in latest.values() if r["status"] == "on_sale"]


def _active_comps(cfg: Config, con, target_id: str) -> tuple[list[EbaySold], Optional[int]]:
    row = con.execute(
        "SELECT id, meta_json FROM snapshots WHERE target_id=? AND source='ebay_active' ORDER BY id DESC LIMIT 1", (target_id,)
    ).fetchone()
    if not row:
        return [], None
    import json

    total = (json.loads(row["meta_json"] or "{}") or {}).get("total")
    d = cfg.ebay_api.active_discount
    items = [
        EbaySold(title=r["title"], price_usd=r["price_usd"] * d, shipping_usd=(r["shipping_usd"] or 0) * d,
                 total_usd=r["total_usd"] * d, url=r["url"], condition=r["condition"])
        for r in con.execute("SELECT * FROM ebay_sold WHERE snapshot_id=?", (row["id"],))
    ]
    return items, total


def genre_value(cfg: Config, con, target: Target, *, plan: dict[str, Any], inflow: dict[str, dict[str, Any]], days: float) -> dict[str, Any]:
    th = cfg.thresholds
    cat_min = plan_mod.category_min_profit(plan, target.category)
    min_profit = target.min_profit_jpy if target.min_profit_jpy is not None else cat_min if cat_min is not None else th.min_profit_jpy
    min_roi = target.min_roi if target.min_roi is not None else th.min_roi

    cached = _cached_ebay(con, target, cfg.app.ebay_cache_hours, cfg.app.ebay_manual_days)
    active_items, active_total = _active_comps(cfg, con, target.id)
    if cached and cached[1]:
        basis, comps_pool, sold_n = "sold", cached[1], len(cached[1])
    elif active_items:
        basis, comps_pool, sold_n = "active", active_items, None
    else:
        basis, comps_pool, sold_n = None, [], None

    items = _mercari_window(con, target.id, days)
    learned = Learned(con, target.id)
    cap = max_buy_for(cfg, target)
    n = {"listings": len(items), "excluded": 0, "over_budget": 0, "no_comp": 0, "accessory": 0, "below": 0, "passed": 0}
    profits: list[int] = []
    hits: list[dict[str, Any]] = []
    prices = [r["price_jpy"] for r in items]
    for r in items:
        if _is_excluded(cfg, target, r["title"]):
            n["excluded"] += 1
            continue
        if cap and r["price_jpy"] > cap:
            n["over_budget"] += 1
            continue
        comps = learned.filter(r["title"], find_comps(r["title"], comps_pool, title_of=lambda e: e.title, threshold=th.match_threshold))
        comps, _ = select_comps(comps, None, th.min_comps)
        market = market_price([c.item.total_usd for c in comps], min_samples=th.min_comps)
        if market is None:
            n["no_comp"] += 1
            continue
        if th.min_price_ratio and r["price_jpy"] < market * cfg.fx.jpy_per_usd * th.min_price_ratio:
            n["accessory"] += 1
            continue
        est = estimate_profit(cfg=cfg, target=target, buy_price_jpy=r["price_jpy"], sell_total_usd=market,
                              mercari_title=r["title"], mercari_url=r["url"])
        if est.profit_jpy < min_profit or est.roi < min_roi:
            n["below"] += 1
        else:
            n["passed"] += 1
            profits.append(est.profit_jpy)
            hits.append({"title": r["title"], "url": r["url"], "buy_jpy": r["price_jpy"], "market_usd": round(market, 2),
                         "profit_jpy": est.profit_jpy, "comp": comps[0].item.title, "comp_score": comps[0].score, "comps_n": len(comps)})

    considered = n["listings"] - n["excluded"]
    f = inflow.get(target.id) or {}
    per_day = (f.get("per_hour") or 0) * 24 if f.get("per_hour") is not None else None
    pass_rate = n["passed"] / considered if considered else 0.0
    avg_profit = sum(profits) / len(profits) if profits else 0
    value = per_day * pass_rate * avg_profit * PURCHASE_SUCCESS_RATE if per_day is not None else None
    return {
        "id": target.id, "name": target.name, "category": target.category,
        "basis": basis, "sold_n": sold_n, "active_total": active_total,
        "inflow_per_day": per_day, "median_buy_jpy": int(median(prices)) if prices else None,
        **n,
        "match_rate": (considered - n["over_budget"] - n["no_comp"]) / considered if considered else 0.0,
        "pass_rate": pass_rate, "avg_profit_jpy": int(avg_profit), "min_profit_jpy": min_profit,
        "value_per_day_jpy": int(value) if value is not None else None,
        "hits": sorted(hits, key=lambda h: -h["profit_jpy"]),
    }


def genre_values(cfg: Config, con, *, days: float = 3) -> list[dict[str, Any]]:
    """有効な全ジャンルの監視価値。価値の高い順（相場がないジャンルは最後）。"""
    fx_mod.apply(cfg)
    plan = plan_mod.load(cfg)
    plan_mod.apply_assumptions(cfg, plan)
    inflow = mercari_inflow(con, days=days)
    rows = [genre_value(cfg, con, t, plan=plan, inflow=inflow, days=days) for t in active_genres(con, cfg) if not t.is_master]
    return sorted(rows, key=lambda r: (r["basis"] is not None, r["value_per_day_jpy"] or 0), reverse=True)
