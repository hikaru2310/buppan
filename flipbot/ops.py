"""実務（仕入れ後）の管理と、計画対実績の集計。

SKUのステータス:
  ordered(購入済・到着待ち) → arrived(到着・検品待ち) → ready(検品合格・出品待ち) → listed(出品中)
  → sold(売約・発送待ち) → shipped(発送済・着金待ち) → paid(完了)
  例外: rejected(検品不合格・廃棄) / returned(返品) → 再検品（arrived）か 損失処理（rejected）

着金の考え方: Payoneer/Wise の入金は複数取引のまとめ払いなので、SKUごとの入金額は求めない。
  実現利益 = 売価から手数料モデルで計算した受取額（eBayの手数料実額があればそれ）− 仕入 − 実送料 − 関税 − 梱包 − 作業 − 返品損失
  月ごとの入金総額は「KPI・ファネル」の月次実績に入れ、計算上の受取額と照合する。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from . import db
from .config import Config
from .plan import month_index, month_label

STATUSES = {
    "ordered": "到着待ち",
    "arrived": "検品待ち",
    "ready": "出品待ち",
    "listed": "出品中",
    "sold": "発送待ち",
    "shipped": "着金待ち",
    "paid": "完了",
    "rejected": "検品不合格",
    "returned": "返品",
}
IN_STOCK = ("ordered", "arrived", "ready", "listed")
SOLD = ("sold", "shipped", "paid")
HANDLING_DAYS = 3  # 売れてから発送までの社内期限（config の app.handling_days で上書き）

# action -> (遷移元, 遷移先, 日時カラム, 受け付けるカラム)
ACTIONS: dict[str, tuple[tuple[str, ...], str, Optional[str], tuple[str, ...]]] = {
    "arrive": (("ordered",), "arrived", "arrived_at", ()),
    "inspect_pass": (("arrived",), "ready", "inspected_at", ("weight_g", "notes")),
    "inspect_fail": (("arrived",), "rejected", "inspected_at", ("defect_reason", "notes")),
    "list": (("ready", "listed"), "listed", "listed_at", ("list_price_usd", "ebay_url")),
    "reprice": (("listed",), "listed", None, ("list_price_usd",)),
    "sell": (("listed", "ready"), "sold", "sold_at", ("sold_price_usd", "buyer_country")),
    "ship": (("sold",), "shipped", "shipped_at", ("carrier", "tracking", "shipping_cost_jpy", "duty_jpy")),
    "pay": (("shipped", "sold"), "paid", "paid_at", ("payout_jpy", "fees_usd")),
    "return": (("sold", "shipped", "paid"), "returned", "returned_at", ("return_reason", "return_loss_jpy", "notes")),
    # 返品後: 手元に戻ったら再検品へ / 戻らない・売れない場合は損失として処理
    "restock": (("returned",), "arrived", "arrived_at", ("notes",)),
    "writeoff": (("returned", "ready", "listed", "arrived"), "rejected", None, ("return_loss_jpy", "defect_reason", "notes")),
    "note": (tuple(STATUSES), "", None, ("notes", "ebay_url", "weight_g", "category")),
    # 入力ミスの修正（状態は変えない）
    "edit": (tuple(STATUSES), "", None, (
        "title", "category", "buy_price_jpy", "purchased_at", "list_price_usd", "ebay_url", "sold_price_usd", "buyer_country",
        "sold_at", "carrier", "tracking", "shipping_cost_jpy", "duty_jpy", "shipped_at", "payout_jpy", "fees_usd", "paid_at",
        "weight_g", "return_loss_jpy", "return_reason", "defect_reason", "notes", "ebay_order_id")),
}
# 「一つ前に戻す」: 現在の状態 → (戻り先, 消す日時カラム)
BACK = {
    "arrived": ("ordered", "arrived_at"), "ready": ("arrived", "inspected_at"), "rejected": ("arrived", "inspected_at"),
    "listed": ("ready", "listed_at"), "sold": ("listed", "sold_at"), "shipped": ("sold", "shipped_at"),
    "paid": ("shipped", "paid_at"), "returned": ("shipped", "returned_at"),
}
EVENT_OF = {"arrive": "arrive", "inspect_pass": "inspect_pass", "inspect_fail": "inspect_fail", "list": "list",
            "sell": "sell", "ship": "ship", "pay": "pay", "return": "return"}
NUMERIC = {"weight_g", "shipping_cost_jpy", "duty_jpy", "payout_jpy", "buy_price_jpy", "return_loss_jpy"}
FLOATS = {"list_price_usd", "sold_price_usd", "fees_usd"}


def _now() -> str:
    return db.utc_now_iso()


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _days_since(s: Optional[str], now: datetime) -> Optional[int]:
    d = _dt(s)
    return None if d is None else max((now - d).days, 0)


# ---------------------------------------------------------------- 作成・更新
def new_sku_id(con: sqlite3.Connection) -> str:
    prefix = datetime.now().strftime("%y%m")
    row = con.execute("SELECT sku FROM skus WHERE sku LIKE ? ORDER BY sku DESC LIMIT 1", (f"{prefix}-%",)).fetchone()
    n = int(row["sku"].split("-")[1]) + 1 if row else 1
    return f"{prefix}-{n:03d}"


def create_from_candidate(con: sqlite3.Connection, payload: dict[str, Any], *, actual_buy_jpy: Optional[int], category: Optional[str]) -> str:
    sku = new_sku_id(con)
    buy = int(actual_buy_jpy or payload["buy_price_jpy"])
    # 実際の仕入れ値が候補と違えば、予測利益もその差だけ補正して保存（予測誤差の基準）
    predicted = int(payload["profit_jpy"]) - (buy - int(payload["buy_price_jpy"]))
    con.execute(
        "INSERT INTO skus(sku, item_key, status, title, genre, category, mercari_url, image_url, payload_json, purchased_at, "
        "buy_price_jpy, predicted_sell_usd, predicted_profit_jpy, breakeven_jpy, updated_at) "
        "VALUES(?, ?, 'ordered', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sku, payload.get("item_key"), payload["mercari_title"], payload.get("genre"), category,
         payload.get("mercari_url"), payload.get("image_url"), json.dumps(payload, ensure_ascii=False), _now(),
         buy, payload.get("market_usd"), predicted, payload.get("breakeven_buy_jpy"), _now()),
    )
    db.log_event(con, "purchase", sku)
    return sku


def apply_action(con: sqlite3.Connection, sku: str, action: str, fields: dict[str, Any]) -> tuple[bool, str]:
    row = con.execute("SELECT status FROM skus WHERE sku=?", (sku,)).fetchone()
    if not row:
        return False, "SKUが見つかりません"
    if action == "back":
        if row["status"] not in BACK:
            return False, "これ以上は戻せません"
        dest, col = BACK[row["status"]]
        extra = ", inspection=NULL" if col == "inspected_at" else ""
        con.execute(f"UPDATE skus SET status=?, {col}=NULL{extra}, updated_at=? WHERE sku=?", (dest, _now(), sku))
        db.log_event(con, "back", sku)
        con.commit()
        return True, f"「{STATUSES[dest]}」に戻しました"
    if action not in ACTIONS:
        return False, "不明な操作です"
    sources, dest, ts_col, allowed = ACTIONS[action]
    if row["status"] not in sources:
        return False, f"「{STATUSES.get(row['status'])}」の商品にはこの操作はできません"
    sets: dict[str, Any] = {}
    for k in allowed:
        if k in fields and fields[k] not in (None, ""):
            v = fields[k]
            if k.endswith("_at") and len(str(v)) == 10:  # 画面の日付入力（YYYY-MM-DD）は日本時間の正午として保存
                v = f"{v}T03:00:00+00:00"
            try:
                sets[k] = int(float(v)) if k in NUMERIC else float(v) if k in FLOATS else str(v)
            except ValueError:
                return False, f"「{k}」の値が数値ではありません: {v}"
        elif action == "edit" and k in fields and fields[k] == "" and k not in ("title", "buy_price_jpy", "purchased_at"):
            sets[k] = None  # 修正で空欄にした項目は消す
    if dest:
        sets["status"] = dest
    if ts_col:
        sets[ts_col] = fields.get("at") or _now()
    if action.startswith("inspect"):
        sets["inspection"] = "pass" if action == "inspect_pass" else "fail"
    sets["updated_at"] = _now()
    con.execute(f"UPDATE skus SET {', '.join(f'{k}=?' for k in sets)} WHERE sku=?", (*sets.values(), sku))
    if action in EVENT_OF:
        db.log_event(con, EVENT_OF[action], sku)
    con.commit()
    return True, "OK"


# ---------------------------------------------------------------- 1SKUの損益
def _payload(r: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(r["payload_json"] or "{}")
    except ValueError:
        return {}


def _contribution_rate(cfg: Config, p: dict[str, Any]) -> tuple[float, float]:
    """売価(USD)→貢献利益(円)の一次式  貢献利益 = a × 売価USD − b × 1 の a と、固定費部分。"""
    e, c, fx = cfg.fees.ebay, cfg.costs, cfg.fx.jpy_per_usd
    duty = c.us_buyer_share * c.us_duty_rate
    a = fx * ((1 - e.final_value_fee_rate - e.international_fee_rate - e.ad_rate - duty) * (1 - e.payout_fx_spread_rate) - c.reserve_rate)
    fixed = e.payment_fixed_fee_usd * fx * (1 - e.payout_fx_spread_rate)
    return a, fixed


def _other_costs(p: dict[str, Any], cfg: Config) -> int:
    """仕入れ以外の原価（送料・梱包・作業）。仕入れ時点の候補データから。"""
    return int(p.get("shipping_cost_jpy", 0)) + int(p.get("packing_jpy", cfg.costs.packing_material_jpy)) + int(p.get("labor_jpy", cfg.costs.labor_jpy))


def sku_economics(cfg: Config, r: sqlite3.Row) -> dict[str, Any]:
    p = _payload(r)
    a, fixed = _contribution_rate(cfg, p)
    other = _other_costs(p, cfg)
    cost = r["buy_price_jpy"] + other
    floor_usd = (cost + fixed) / a if a > 0 else None  # 貢献利益0になる売価
    realized: Optional[int] = None
    estimated: Optional[int] = None
    received = received_jpy(cfg, r)
    loss = r["return_loss_jpy"] or 0
    if received is not None:
        ship = r["shipping_cost_jpy"] if r["shipping_cost_jpy"] is not None else int(p.get("shipping_cost_jpy", 0))
        value = received - r["buy_price_jpy"] - ship - (r["duty_jpy"] or 0) - loss \
            - int(p.get("packing_jpy", cfg.costs.packing_material_jpy)) - int(p.get("labor_jpy", cfg.costs.labor_jpy))
        if r["status"] == "paid":
            realized = int(value)  # 完了＝送料・関税の実額が入っている前提で「実現」とする
        elif r["status"] in ("sold", "shipped"):
            estimated = int(value)
    if r["status"] in ("returned", "rejected") and (loss or r["status"] == "rejected"):
        # 返品・不合格の損失（仕入れ値は戻らない前提。返品で手元に戻り再販できるなら restock）
        realized = -int(loss or r["buy_price_jpy"])
    return {"floor_usd": round(floor_usd, 2) if floor_usd else None, "realized_jpy": realized,
            "estimated_jpy": estimated, "cost_jpy": cost, "received_jpy": received}


def received_jpy(cfg: Config, r: sqlite3.Row) -> Optional[int]:
    """売上から手元に残る円（換金後）。入金額が入っていればそれ、eBayの手数料実額があればそれで計算、なければ手数料モデル。"""
    if r["payout_jpy"]:
        return int(r["payout_jpy"])
    if not r["sold_price_usd"]:
        return None
    e = cfg.fees.ebay
    fees = r["fees_usd"] if r["fees_usd"] is not None else \
        r["sold_price_usd"] * (e.final_value_fee_rate + e.international_fee_rate + e.ad_rate) + e.payment_fixed_fee_usd
    return int((r["sold_price_usd"] - fees) * cfg.fx.jpy_per_usd * (1 - e.payout_fx_spread_rate))


def reprice_suggestion(cfg: Config, r: sqlite3.Row, econ: dict[str, Any], now: datetime) -> Optional[dict[str, Any]]:
    """30/60/90日ルール（開発ロードマップ「自動価格改定」）。損益分岐（floor）は割らない。"""
    if r["status"] != "listed" or not r["list_price_usd"]:
        return None
    days = _days_since(r["listed_at"], now) or 0
    market = r["predicted_sell_usd"] or r["list_price_usd"]
    floor = econ["floor_usd"] or 0
    if days >= 90:
        target, why = max(floor * 1.02, market * 0.85), "90日超：損益分岐の少し上まで下げて現金化"
    elif days >= 60:
        target, why = max(floor, market * 0.92), "60日超：相場の8%下"
    elif days >= 30:
        target, why = max(floor, market * 0.97), "30日超：相場の3%下"
    else:
        return None
    target = round(target, 2)
    if target >= r["list_price_usd"] - 0.5:
        return None
    return {"days": days, "suggested_usd": target, "reason": why}


# ---------------------------------------------------------------- 集計
def _month_bounds(cfg: Config, idx: int) -> tuple[str, str]:
    y, m = (int(x) for x in cfg.plan.start_month.split("-"))
    total = y * 12 + (m - 1) + (idx - 1)
    start = date(total // 12, total % 12 + 1, 1)
    nxt = date((total + 1) // 12, (total + 1) % 12 + 1, 1)
    # DBはUTC保存。日本時間の月初に合わせて9時間ずらす
    to = lambda d: (datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=9)))).astimezone(timezone.utc).isoformat(timespec="seconds")
    return to(start), to(nxt)


def _pct_threshold(text: str) -> Optional[tuple[str, float]]:
    """「20%超」「50%未満」「300万円未満」「40%超」→ (演算子, 値)。"""
    m = re.search(r"([0-9.]+)\s*(%|万円)\s*(超|未満|以上|以下)", str(text))
    if not m:
        return None
    v = float(m.group(1)) / 100 if m.group(2) == "%" else float(m.group(1)) * 10000
    return m.group(3), v


RISK_METRIC = {  # 計画シート「リスク管理」のリスク名 → 指標キー
    "利益計算誤差": "forecast_error",
    "AI誤照合": "mispurchase_rate",
    "在庫長期化": "over90_rate",
    "販売速度低下": "sell60_rate",
    "返品増加": "return_rate",
    "資金不足": "free_cash",
    "カテゴリ集中": "max_category_share",
    "仕入れ供給不足": "purchase_vs_plan",
    "為替・送料上昇": "profit_vs_plan",
}


def metrics(con: sqlite3.Connection, cfg: Config, plan: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    fx = cfg.fx.jpy_per_usd
    rows = list(con.execute("SELECT * FROM skus ORDER BY purchased_at DESC"))
    skus: list[dict[str, Any]] = []
    for r in rows:
        econ = sku_economics(cfg, r)
        d = {k: r[k] for k in r.keys() if k != "payload_json"}
        p = _payload(r)
        d.update(econ, status_label=STATUSES.get(r["status"], r["status"]),
                 days_in_stock=_days_since(r["purchased_at"], now),
                 days_listed=_days_since(r["listed_at"], now) if r["status"] == "listed" else None,
                 reprice=reprice_suggestion(cfg, r, econ, now),
                 comps=p.get("comps", [])[:3], ebay_sold_search_url=p.get("ebay_sold_search_url"),
                 ship_due=((_dt(r["sold_at"]) + timedelta(days=cfg.app.handling_days)).isoformat() if r["status"] == "sold" and r["sold_at"] else None))
        if r["status"] == "sold" and d["ship_due"]:
            d["ship_overdue"] = now > _dt(d["ship_due"])  # type: ignore[operator]
        skus.append(d)

    idx = month_index(cfg)
    cur = max(min(idx, 12), 1)
    months = []
    for i in range(1, max(min(idx, 12), 1) + 1):
        months.append(_month_actual(con, cfg, i, skus, fx))
    actual = months[cur - 1] if months else _month_actual(con, cfg, cur, skus, fx)

    # 在庫と資金
    stock = [s for s in skus if s["status"] in IN_STOCK]
    inv_cost = sum(s["buy_price_jpy"] for s in stock)
    buckets = {
        "購入済未到着": sum(s["buy_price_jpy"] for s in skus if s["status"] == "ordered"),
        "検品・出品待ち": sum(s["buy_price_jpy"] for s in skus if s["status"] in ("arrived", "ready")),
        "販売可能在庫": sum(s["buy_price_jpy"] for s in skus if s["status"] == "listed"),
        "販売済未入金": int(sum((s["sold_price_usd"] or 0) * fx * 0.82 for s in skus if s["status"] in ("sold", "shipped"))),
    }
    over90 = sum(s["buy_price_jpy"] for s in stock if (s["days_in_stock"] or 0) > 90)
    # 60日以内販売率: 購入から60日以上経ったSKUのうち、60日以内に売れた割合
    matured = [s for s in skus if (s["days_in_stock"] or 0) >= 60 and s["status"] not in ("rejected",)]
    sold60 = [s for s in matured if s["sold_at"] and (_dt(s["sold_at"]) - _dt(s["purchased_at"])).days <= 60]  # type: ignore[operator]
    paid = [s for s in skus if s["realized_jpy"] is not None and s["predicted_profit_jpy"]]
    errors = [abs(s["realized_jpy"] - s["predicted_profit_jpy"]) / abs(s["predicted_profit_jpy"]) for s in paid if s["predicted_profit_jpy"]]
    sold_all = [s for s in skus if s["status"] in SOLD or s["status"] == "returned"]
    returns = [s for s in skus if s["status"] == "returned"]
    mismatch = [s for s in skus if s["status"] in ("rejected", "returned") and re.search(r"違い|誤|別物|型番|付属", (s["defect_reason"] or "") + (s["return_reason"] or ""))]
    cats: dict[str, dict[str, Any]] = {}
    for s in skus:
        c = s["category"] or "未分類"
        x = cats.setdefault(c, {"inventory_jpy": 0, "sold": 0, "profit_sum": 0, "profit_n": 0, "returns": 0, "purchased": 0})
        x["purchased"] += 1
        if s["status"] in IN_STOCK:
            x["inventory_jpy"] += s["buy_price_jpy"]
        if s["status"] in SOLD:
            x["sold"] += 1
            v = s["realized_jpy"] if s["realized_jpy"] is not None else s["estimated_jpy"]
            if v is not None:
                x["profit_sum"] += v
                x["profit_n"] += 1
        if s["status"] == "returned":
            x["returns"] += 1
    for x in cats.values():
        x["share"] = x["inventory_jpy"] / inv_cost if inv_cost else 0
        x["avg_profit"] = x["profit_sum"] / x["profit_n"] if x["profit_n"] else None
    cash_row = con.execute("SELECT value FROM settings WHERE key='cash_jpy'").fetchone()
    cash = int(cash_row["value"]) if cash_row and cash_row["value"] else None
    plan_month = next((m for m in plan.get("monthly", []) if int(m.get("月", 0)) == cur), {})

    labels = con.execute("SELECT SUM(label) good, COUNT(*) n FROM match_labels").fetchone()
    indicators = {
        # 照合精度（実測）: 候補の内訳で ✓/✗ を付けた成約品のうち「同じ商品」だった割合
        "match_precision": (labels["good"] or 0) / labels["n"] if labels and labels["n"] else None,
        "match_labels_n": labels["n"] if labels else 0,
        "forecast_error": sum(errors) / len(errors) if errors else None,
        "forecast_n": len(errors),
        "mispurchase_rate": len(mismatch) / len(skus) if skus else None,
        "over90_rate": over90 / inv_cost if inv_cost else None,
        "sell60_rate": len(sold60) / len(matured) if matured else None,
        "return_rate": len(returns) / len(sold_all) if sold_all else None,
        "free_cash": cash,
        "max_category_share": max((x["share"] for x in cats.values()), default=None) if inv_cost else None,
        "purchase_vs_plan": None,
        "profit_vs_plan": None,
        "inventory_cost": inv_cost,
        "inventory_days": actual.get("inventory_days"),
    }
    if actual["purchases"] and plan_month:
        target_buy = _plan_purchases(plan, cur)
        if target_buy:
            indicators["purchase_vs_plan"] = actual["purchases"] / (target_buy * _month_progress(cfg, cur))
    if actual["profit_per_unit"] and plan_month.get("貢献利益/件(円)"):
        indicators["profit_vs_plan"] = actual["profit_per_unit"] / plan_month["貢献利益/件(円)"] - 1

    risks = []
    for rk in plan.get("risks", []):
        key = RISK_METRIC.get(rk.get("リスク", ""))
        val = indicators.get(key) if key else None
        th = _pct_threshold(rk.get("発動閾値", ""))
        status = "na"
        if key == "purchase_vs_plan" and val is not None:
            status = "alert" if val < 0.8 else "ok"
        elif key == "profit_vs_plan" and val is not None:
            status = "alert" if val < -0.10 else "ok"
        elif val is not None and th:
            op, v = th
            hit = val > v if op in ("超",) else val >= v if op == "以上" else val < v if op == "未満" else val <= v
            status = "alert" if hit else "ok"
        risks.append({**rk, "metric": key, "value": val, "status": status})

    # 営業利益の実績 = 貢献利益 − 固定費（実額が入っていれば実額、なければ計画値）
    for m in months:
        pm = next((x for x in plan.get("monthly", []) if int(x.get("月", 0)) == m["month"]), {})
        fixed = m["fixed_cost_man"] if m["fixed_cost_man"] is not None else pm.get("固定費(万円)")
        m["fixed_used_man"], m["fixed_is_actual"] = fixed, m["fixed_cost_man"] is not None
        m["op_profit_man"] = round(m["contribution_man"] - (fixed or 0), 1)
        days = (_dt(_month_bounds(cfg, m["month"])[1]) - _dt(_month_bounds(cfg, m["month"])[0])).days  # type: ignore[operator]
        m["owner_hours_week"] = round(m["owner_hours"] / (days / 7), 1) if m["owner_hours"] is not None else None
    indicators["owner_hours_week"] = actual.get("owner_hours_week")

    restrictions = _restrictions(plan, indicators, cats, skus, inv_cost, risks)
    # 実送料の学習: ジャンルごとの実送料の中央値（発送済みSKUから）
    ship_learn: dict[str, dict[str, Any]] = {}
    for s_ in skus:
        if s_["shipping_cost_jpy"] is not None and s_["genre"]:
            ship_learn.setdefault(s_["genre"], {"values": []})["values"].append(s_["shipping_cost_jpy"])
    for g, v in ship_learn.items():
        vals = sorted(v["values"])
        v.update(n=len(vals), median=vals[len(vals) // 2])
        del v["values"]

    return {
        "restrictions": restrictions,
        "ship_learn": ship_learn,
        "now": now.isoformat(),
        "month_index": idx,
        "month_label": month_label(cfg, cur),
        "plan_month": plan_month,
        "actual": actual,
        "months": months,
        "skus": skus,
        "counts": {k: sum(1 for s in skus if s["status"] == k) for k in STATUSES},
        "reprice_n": sum(1 for s in skus if s.get("reprice")),
        "ship_overdue_n": sum(1 for s in skus if s.get("ship_overdue")),
        "inventory": {"cost_jpy": inv_cost, "buckets": buckets, "over90_jpy": over90},
        "categories": cats,
        "indicators": indicators,
        "risks": risks,
        "gates": _gates(plan, months, indicators),
        "funnel": _funnel(con, cfg, plan, cur),
        "cash_jpy": cash,
    }


def _restrictions(plan: dict[str, Any], ind: dict[str, Any], cats: dict[str, Any], skus: list[dict[str, Any]],
                  inv_cost: int, risks: list[dict[str, Any]]) -> dict[str, Any]:
    """リスク管理・資金配分ルールの「自動対応」を、仕入れの制限として具体化する。
    global: 全体の制限（拡大停止・資金不足など） / categories: カテゴリ別の仕入れ抑制。"""
    out: dict[str, Any] = {"global": [], "categories": {}}
    alert = {r.get("リスク"): r for r in risks if r.get("status") == "alert"}
    if "利益計算誤差" in alert:
        out["global"].append("予測利益誤差が閾値超え → 仕入れの拡大を停止（今の量を維持）")
    if "資金不足" in alert:
        out["global"].append("自由現金が下限未満 → 全体の仕入れ上限を縮小")
    if "AI誤照合" in alert:
        out["global"].append("誤仕入れ率が閾値超え → 照合の確認を厳しく（内訳の成約品を必ず目視）")
    if "仕入れ供給不足" in alert:
        out["global"].append("購入成功数が計画比80%未満 → カテゴリ・マスターの再配分を検討")
    cat_plan = {c.get("カテゴリ"): c for c in plan.get("categories", [])}
    for name, c in cats.items():
        reasons = []
        # 在庫が少ないうちは比率が極端になるので、在庫5件以上・30万円以上になってから判定する
        if inv_cost >= 300000 and sum(1 for s_ in skus if s_["status"] in IN_STOCK) >= 5 and c["share"] > 0.40:
            reasons.append(f"在庫比率 {c['share']:.0%} が1カテゴリ上限40%超")
        lim = cat_plan.get(name, {}).get("返品率上限")
        sold_n = c["sold"] + c["returns"]
        if lim and sold_n >= 5 and c["returns"] / sold_n > lim:
            reasons.append(f"返品率 {c['returns'] / sold_n:.0%} が上限 {lim:.0%} 超")
        stock = [s for s in skus if (s["category"] or "未分類") == name and s["status"] in IN_STOCK]
        cost = sum(s["buy_price_jpy"] for s in stock)
        old = sum(s["buy_price_jpy"] for s in stock if (s["days_in_stock"] or 0) > 90)
        if cost and len(stock) >= 3 and old / cost > 0.10:
            reasons.append(f"90日超在庫 {old / cost:.0%} が10%超 → 仕入上限30%削減・値下げ")
        if reasons:
            out["categories"][name] = reasons
    return out


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month_progress(cfg: Config, idx: int) -> float:
    """当月の経過割合（月途中の計画比を出すため）。"""
    start, end = _month_bounds(cfg, idx)
    s, e, n = _dt(start), _dt(end), datetime.now(timezone.utc)
    if n >= e:  # type: ignore[operator]
        return 1.0
    return max((n - s).total_seconds() / (e - s).total_seconds(), 0.05)  # type: ignore[operator]


def _plan_purchases(plan: dict[str, Any], idx: int) -> Optional[float]:
    """当月の購入成功数の計画（シート「KPI実績」の 仕入成功数_計画）。"""
    m = next((m for m in plan.get("kpi_plan", []) if int(m.get("月", 0) or 0) == idx), None)
    return _num(m.get("仕入成功数_計画")) if m else None


def _month_actual(con, cfg: Config, idx: int, skus: list[dict[str, Any]], fx: float) -> dict[str, Any]:
    start, end = _month_bounds(cfg, idx)
    inm = lambda s: bool(s) and start <= s < end  # noqa: E731
    sold = [s for s in skus if inm(s["sold_at"]) and s["status"] in SOLD]
    purchases = [s for s in skus if inm(s["purchased_at"])]
    revenue = sum((s["sold_price_usd"] or 0) * fx for s in sold)
    profits = [s["realized_jpy"] if s["realized_jpy"] is not None else s["estimated_jpy"] for s in sold]
    profits = [p for p in profits if p is not None]
    cogs = sum(s["buy_price_jpy"] for s in sold)
    month_end = end if datetime.now(timezone.utc).isoformat() > end else datetime.now(timezone.utc).isoformat()
    inv_end = sum(s["buy_price_jpy"] for s in skus if s["purchased_at"] < month_end and (not s["sold_at"] or s["sold_at"] >= month_end)
                  and s["status"] not in ("rejected",))
    contrib = sum(profits)
    ma = con.execute("SELECT * FROM month_actuals WHERE month=?", (idx,)).fetchone()
    # 入金の照合: この月に完了したSKUの「計算上の受取額」と、実際の入金総額（月次実績に入力）を比べる
    expected_payout = sum((s.get("received_jpy") or 0) for s in skus if inm(s["paid_at"]))
    return {
        "fixed_cost_man": ma["fixed_cost_man"] if ma else None,
        "payout_man": ma["payout_man"] if ma else None,
        "owner_hours": ma["owner_hours"] if ma else None,
        "expected_payout_man": round(expected_payout / 10000, 1),
        "month": idx,
        "label": month_label(cfg, idx),
        "sales": len(sold),
        "revenue_man": round(revenue / 10000, 1),
        "contribution_man": round(contrib / 10000, 1),
        "profit_per_unit": int(contrib / len(profits)) if profits else None,
        "avg_sell_jpy": int(revenue / len(sold)) if sold else None,
        "avg_buy_jpy": int(sum(s["buy_price_jpy"] for s in purchases) / len(purchases)) if purchases else None,
        "purchases": len(purchases),
        "purchase_amount_man": round(sum(s["buy_price_jpy"] for s in purchases) / 10000, 1),
        "inventory_end_man": round(inv_end / 10000, 1),
        "inventory_days": round(inv_end / cogs * 30) if cogs else None,
        "returns": sum(1 for s in skus if inm(s["returned_at"])),
    }


def _funnel(con, cfg: Config, plan: dict[str, Any], idx: int) -> list[dict[str, Any]]:
    start, end = _month_bounds(cfg, idx)
    one = lambda sql, *a: con.execute(sql, (start, end, *a)).fetchone()[0]  # noqa: E731
    ev = lambda kind: one("SELECT COUNT(DISTINCT ref) FROM events WHERE ts>=? AND ts<? AND kind=?", kind)  # noqa: E731
    actual = {
        "取得商品データ": one("SELECT COUNT(*) FROM seen_items WHERE first_seen>=? AND first_seen<?"),
        "AI一次候補": one(
            "SELECT COUNT(DISTINCT c.item_key) FROM candidates c JOIN scans s ON s.id=c.scan_id "
            "WHERE s.finished_at>=? AND s.finished_at<? AND s.data_source!='sample' AND json_extract(c.payload_json,'$.near_miss')=0"),
        "人間確認対象": one("SELECT COUNT(DISTINCT ref) FROM events WHERE ts>=? AND ts<? AND kind IN ('queue','dismiss')"),
        "購入判断・交渉": ev("queue"),
        "購入成功": ev("purchase"),
        "検品合格": ev("inspect_pass"),
        "出品完了": ev("list"),
        "月間販売": ev("sell"),
    }
    progress = _month_progress(cfg, idx)
    # 計画は12か月目の基準値。当月の目標は 販売数比で按分する
    m = next((m for m in plan.get("monthly", []) if int(m.get("月", 0)) == idx), None)
    final_sales = next((f.get("月間目標件数") for f in plan.get("funnel", []) if f.get("工程") == "月間販売"), None)
    scale = (float(m["販売数"]) / float(final_sales)) if m and final_sales else 1.0
    out = []
    prev = None
    for f in plan.get("funnel", []) or [{"工程": k} for k in actual]:
        name = f.get("工程")
        target = _num(f.get("月間目標件数"))
        a = actual.get(name)
        month_target = target * scale if target else None
        out.append({
            "stage": name,
            "target_month": round(month_target) if month_target else None,
            "target_to_date": round(month_target * progress) if month_target else None,
            "actual": a,
            "conv_plan": f.get("前工程からの転換率"),
            "conv_actual": (a / prev) if (a is not None and prev) else None,
            "note": f.get("定義・管理ポイント"),
        })
        prev = a
    return out


_GATE_RE = re.compile(r"(累計販売|販売|貢献利益|予測誤差|返品|購入成功|在庫|営業利益|オーナー)\s*([0-9,.]+)\s*(件|円|%|日|万円|h)?")


def _gates(plan: dict[str, Any], months: list[dict[str, Any]], ind: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for g in plan.get("stage_gates", []):
        t = str(g.get("時点", ""))
        m = re.match(r"(\d+)か月", t)
        at = int(m.group(1)) if m else None
        upto = months[: at] if at else months
        last = upto[-1] if upto else {}
        checks = []
        for label, num, unit in _GATE_RE.findall(str(g.get("数値基準", ""))):
            target = float(num.replace(",", ""))
            if label == "累計販売":
                val, ok = sum(x["sales"] for x in upto), None
                ok = val >= target
            elif label == "販売":
                val = last.get("sales"); ok = val is not None and val >= target
            elif label == "貢献利益":
                val = last.get("profit_per_unit"); ok = val is not None and val >= target
            elif label == "予測誤差":
                val = ind.get("forecast_error"); ok = val is not None and val * 100 <= target
                val = round(val * 100, 1) if val is not None else None
            elif label == "返品":
                val = ind.get("return_rate"); ok = val is not None and val * 100 <= target
                val = round(val * 100, 1) if val is not None else None
            elif label == "購入成功":
                val = last.get("purchases"); ok = val is not None and val >= target
            elif label == "在庫":
                val = last.get("inventory_days"); ok = val is not None and val <= target
            elif label == "オーナー":
                val = last.get("owner_hours_week"); ok = val is not None and val <= target
            elif label == "営業利益":
                val = last.get("op_profit_man"); ok = val is not None and val >= target
            else:
                val, ok = None, None
            checks.append({"label": label, "target": target, "unit": unit, "value": val, "ok": ok if val is not None else None})
        out.append({**g, "month": at, "checks": checks, "reached": bool(at and len(months) >= at)})
    return out
