"""eBayの需要（回転）と、状態をそろえた比較対象の選び方。

需要の考え方（「在庫が何日分あるか」）:
  1日あたりの成約数 r = 成約件数 ÷ 成約が分布している日数（Sold検索は新しい順なので、1ページが埋まっていれば実際より短い期間）
  売れるまでの日数 ≈ (出品中の件数 + 1) ÷ r      … 自分の1点が、今ある出品のあとに売れる想定
  60日以内に売れる見込み ≈ min(1, 60 ÷ 売れるまでの日数)
  消化率(STR) = 90日の成約見込み ÷ (90日の成約見込み + 出品中)
いずれも目安。計画シートの「60日以内販売率」と比べて回転リスクを判定する。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from .matching import Comp
from .scrape_ebay import EbaySold


def demand(items: Sequence[EbaySold], active_count: Optional[int], today: Optional[date] = None) -> dict[str, Any]:
    today = today or date.today()
    dates = sorted(date.fromisoformat(i.sold_date) for i in items if i.sold_date)
    n = len(items)
    if dates:
        span = max((today - dates[0]).days, 7)
        rate = len(dates) / span
    else:
        rate = n / 90 if n else 0.0  # 日付が読めない場合は「90日でn件」とみなす
    out: dict[str, Any] = {
        "sold_30d": round(rate * 30, 1),
        "sold_90d_est": round(rate * 90),
        "active_count": active_count,
        "str": None, "est_days": None, "sell60": None,
        "dated": bool(dates),
    }
    if rate > 0 and active_count is not None:
        days = (active_count + 1) / rate
        out["est_days"] = int(min(max(days, 1), 365))
        out["sell60"] = round(min(1.0, 60 / days), 2)
        out["str"] = round(rate * 90 / (rate * 90 + active_count), 2) if (rate * 90 + active_count) else None
    return out


def select_comps(comps: list[Comp], mercari_condition_id: Optional[int], min_comps: int) -> tuple[list[Comp], str]:
    """状態をそろえる。部品取りは常に除き、新品は新品同士・中古は中古同士で比べる（件数が足りなければ混ぜる）。"""
    usable = [c for c in comps if getattr(c.item, "condition", None) != "parts"]
    if mercari_condition_id == 1:
        new = [c for c in usable if getattr(c.item, "condition", None) == "new"]
        if len(new) >= min_comps:
            return new, "新品の成約と比較"
        return usable, "新品の成約が少ないため全状態で比較"
    used = [c for c in usable if getattr(c.item, "condition", None) != "new"]
    if len(used) >= min_comps:
        return used, "中古の成約と比較（新品・部品取りを除外）"
    return usable, "中古の成約が少ないため新品も含めて比較"
