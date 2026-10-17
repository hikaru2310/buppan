"""eBay Seller Hub のレポート（CSV）を取り込み、SKUの売約・発送・手数料を自動で反映する。

対応するCSV（ヘッダー名で判定するので、列の順番や余分な列は問わない）:
  - 注文レポート（Seller Hub → 注文 → ダウンロード）: Custom label / Sale date / Total price / Ship to country / Tracking number / Shipped on date
  - 取引レポート（支払い → レポート → Transaction report）: Custom label / Final Value Fee / International fee / Net amount
SKUとの対応は「Custom label」= ツールのSKU（例 2610-001）。出品時にカスタムラベルへSKUを入れておく。
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Optional

from dateutil import parser as dateparser

from . import ops

COUNTRY = {"united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB", "australia": "AU", "germany": "DE",
           "canada": "CA", "france": "FR", "italy": "IT", "spain": "ES", "netherlands": "NL", "hong kong": "HK",
           "taiwan": "TW", "singapore": "SG", "korea, south": "KR", "south korea": "KR", "japan": "JP"}
FIELDS = {
    "sku": ["custom label (sku)", "custom label", "sku"],
    "order": ["order number", "order id"],
    "sale_date": ["sale date", "transaction creation date", "order creation date"],
    "total": ["total price", "gross transaction amount"],
    "sold_for": ["sold for", "item subtotal"],
    "shipping": ["shipping and handling", "shipping and handling price"],
    "country": ["ship to country", "ship to country/region", "buyer country"],
    "shipped": ["shipped on date", "shipping date"],
    "tracking": ["tracking number"],
    "net": ["net amount"],
}
_MONEY = re.compile(r"-?\(?\$?\s*-?[0-9][0-9,]*(?:\.[0-9]+)?\)?")


def _money(v: str) -> Optional[float]:
    if not v:
        return None
    v = v.replace("US", "").replace("USD", "").strip()
    m = _MONEY.search(v)
    if not m:
        return None
    t = m.group(0)
    neg = t.startswith("-") or t.startswith("(") or "-" in t
    x = float(re.sub(r"[^0-9.]", "", t) or 0)
    return -x if neg else x


def _date(v: str) -> Optional[str]:
    if not v or not v.strip() or v.strip() in ("--", "-"):
        return None
    try:
        return dateparser.parse(v.strip()).date().isoformat() + "T12:00:00+00:00"
    except (ValueError, OverflowError):
        return None


def parse(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """前置きの行を飛ばしてヘッダー行を見つけ、行ごとの dict を返す。"""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    hi = next((i for i, r in enumerate(rows) if any(c.strip().lower() in ("custom label", "custom label (sku)", "sku") for c in r)), None)
    if hi is None:
        return [], []
    header = [h.strip().lower() for h in rows[hi]]
    out = [dict(zip(header, r)) for r in rows[hi + 1:] if any(c.strip() for c in r)]
    return out, header


def _get(row: dict[str, str], key: str) -> str:
    for name in FIELDS[key]:
        if row.get(name, "").strip():
            return row[name].strip()
    return ""


def plan_updates(con, text: str) -> dict[str, Any]:
    rows, header = parse(text)
    if not header:
        return {"ok": False, "message": "Custom label（SKU）の列が見つかりません。Seller Hubの注文レポートか取引レポートを選んでください", "changes": []}
    fee_cols = [h for h in header if re.search(r"final value fee|international fee|regulatory operating fee|per-order fee|promoted listing", h)]
    skus = {r["sku"]: dict(r) for r in con.execute("SELECT * FROM skus")}
    changes, unmatched = [], 0
    for row in rows:
        sku = _get(row, "sku")
        if sku not in skus:
            unmatched += 1
            continue
        cur = skus[sku]
        total = _money(_get(row, "total"))
        if total is None and _get(row, "sold_for"):
            total = (_money(_get(row, "sold_for")) or 0) + (_money(_get(row, "shipping")) or 0)
        country = _get(row, "country")
        country = COUNTRY.get(country.lower(), country[:2].upper() if len(country) == 2 else "その他") if country else ""
        if total and cur["status"] in ("listed", "ready"):
            changes.append({"sku": sku, "action": "sell", "fields": {"sold_price_usd": round(total, 2), "buyer_country": country,
                                                                     "at": _date(_get(row, "sale_date"))}, "label": f"売約 ${total:.2f}（{country}）"})
            cur["status"] = "sold"
        tracking, shipped = _get(row, "tracking"), _date(_get(row, "shipped"))
        if (tracking or shipped) and cur["status"] == "sold":
            changes.append({"sku": sku, "action": "ship", "fields": {"tracking": tracking, "at": shipped}, "label": f"発送（追跡 {tracking or '—'}）"})
            cur["status"] = "shipped"
        fees = sum(abs(_money(row.get(c, "")) or 0) for c in fee_cols)
        edits: dict[str, Any] = {}
        if fees:
            edits["fees_usd"] = round(fees, 2)
        if _get(row, "order"):
            edits["ebay_order_id"] = _get(row, "order")
        if edits:
            changes.append({"sku": sku, "action": "edit", "fields": edits,
                            "label": "手数料 $" + str(edits["fees_usd"]) if "fees_usd" in edits else "注文番号を記録"})
    return {"ok": True, "changes": changes, "rows": len(rows), "unmatched": unmatched,
            "message": f"{len(rows)}行を読み、SKUと一致した変更 {len(changes)}件（SKU不一致 {unmatched}行）"}


def apply(con, text: str) -> dict[str, Any]:
    plan = plan_updates(con, text)
    done, errors = 0, []
    for c in plan.get("changes", []):
        ok, msg = ops.apply_action(con, c["sku"], c["action"], {k: v for k, v in c["fields"].items() if v not in (None, "")})
        if ok:
            done += 1
        else:
            errors.append(f"{c['sku']} {c['label']}: {msg}")
    plan.update(applied=done, errors=errors, message=f"{done}件を反映しました" + (f"（{len(errors)}件は反映できず）" if errors else ""))
    return plan
