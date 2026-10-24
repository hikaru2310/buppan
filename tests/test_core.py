"""主要機能の回帰テスト（ネットワークに出ない）。

実行: .venv/bin/python tests/test_core.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flipbot import db, ebay_import, listing, notify, ops  # noqa: E402
from flipbot import plan as plan_mod  # noqa: E402
from flipbot.config import Config, Target, load_config  # noqa: E402
from flipbot.market import demand, select_comps  # noqa: E402
from flipbot.matching import Comp  # noqa: E402
from flipbot.pipeline import _pause_mercari, parse_mercari_item_html, scan_all, select_targets  # noqa: E402
from flipbot.profit import estimate_profit  # noqa: E402
from flipbot.scrape_ebay import EbaySold, parse_ebay_sold_html, parse_result_count  # noqa: E402

TMP = Path(tempfile.mkdtemp())


def make_cfg() -> Config:
    cfg = load_config(ROOT / "config.yaml")
    cfg.app.db_path = str(TMP / "test.db")
    cfg.fx.auto = False
    cfg.fx.jpy_per_usd = 150.0
    cfg.plan.sheet_id = None  # 計画シートには接続しない
    cfg.notify.slack_webhook_url = None
    return cfg


def con_for(cfg: Config):
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    return con


# ------------------------------------------------------------------ 利益
def test_profit_linear_and_breakeven():
    cfg = make_cfg()
    t = Target(id="t", name="T", mercari_query="", ebay_query="", shipping_cost_jpy=3000)
    a = estimate_profit(cfg=cfg, target=t, buy_price_jpy=20000, sell_total_usd=300, mercari_title="", mercari_url="")
    b = estimate_profit(cfg=cfg, target=t, buy_price_jpy=21000, sell_total_usd=300, mercari_title="", mercari_url="")
    assert a.profit_jpy - b.profit_jpy == 1000, "仕入れが1,000円上がれば利益は1,000円下がる"
    z = estimate_profit(cfg=cfg, target=t, buy_price_jpy=a.breakeven_buy_jpy, sell_total_usd=300, mercari_title="", mercari_url="")
    assert abs(z.profit_jpy) <= 2, f"損益分岐の仕入れ値で利益≒0 のはず: {z.profit_jpy}"
    assert a.duty_jpy > 0 and a.ad_fee_jpy > 0 and a.reserve_jpy > 0
    assert abs(a.roi - a.profit_jpy / 20000) < 1e-9, "仕入ROI = 貢献利益 ÷ 仕入価格"


# ------------------------------------------------------------------ eBay の読み取り
def test_ebay_parse_dates_condition_jpy():
    h = '''<ul><li class="s-card"><div class="s-card__caption">Sold  Sep 28, 2026</div><div class="s-card__title">Nikon F3 Body</div>
      <div class="s-card__subtitle">Pre-Owned</div><a href="https://www.ebay.com/itm/1?x">x</a><span class="s-card__price">$360.00</span><span>+$20.00 delivery</span></li>
      <li class="s-card"><span>販売済み 2026年10月2日</span><div class="s-card__title">Nikon F3 junk</div><div class="s-card__subtitle">For parts or not working</div>
      <a href="https://www.ebay.com/itm/2">x</a><span class="s-card__price">JPY 15,000</span></li></ul>'''
    a, b = parse_ebay_sold_html(h, 150)
    assert (a.total_usd, a.condition, a.sold_date, a.url) == (380.0, "used", "2026-09-28", "https://www.ebay.com/itm/1")
    assert (b.price_usd, b.condition, b.sold_date) == (100.0, "parts", "2026-10-02")
    assert parse_result_count('<h1 class="srp-controls__count-heading">1,234 results</h1>') == 1234


def test_demand_and_condition_comps():
    items = [EbaySold("A", 300, 0, 300, f"u{i}", condition="used", sold_date=f"2026-10-{1 + i:02d}") for i in range(10)]
    d = demand(items, 25, date(2026, 10, 17))
    assert d["est_days"] == 41 and d["sell60"] == 1.0 and 0.6 < d["str"] < 0.75
    cs = [Comp(EbaySold("x", 1, 0, 1, "a", condition=c), .9) for c in ("new", "new", "used", "used", "parts")]
    assert len(select_comps(cs, 1, 2)[0]) == 2  # 新品は新品同士
    assert all(c.item.condition == "used" for c in select_comps(cs, 3, 2)[0])  # 中古は中古同士
    assert all(c.item.condition != "parts" for c in select_comps(cs, None, 9)[0])  # 部品取りは常に除外


def test_mercari_block_detection_no_false_positive():
    from flipbot.pipeline import detect_block
    normal = '<script>{"unsupportedBrowserBody":"お使いのブラウザがWebサイトに対応していない"}</script><div data-testid="item-cell">x</div>'
    blocked = "<html><body><p>お使いのブラウザがWebサイトに対応していない、または最新版でない可能性があります</p></body></html>"
    assert detect_block("mercari", normal) is None, "正常なページ（文言データ内の文章）では検知しない"
    assert detect_block("mercari", blocked), "本当の非対応ページは検知する"


def test_mercari_item_page():
    html = ('<meta property="og:title" content="【美品】 ニコン Nikon Ai-s NIKKOR 50mm F1.4 by メルカリ">'
            '<meta property="og:image" content="https://static.mercdn.net/x.jpg">'
            '<div data-testid="price"><span class="currency">¥</span><span>21,800</span></div><p>(税込) 送料込み</p>'
            '<div>商品の状態</div><div>目立った傷や汚れなし</div>')
    it = parse_mercari_item_html(html, "https://jp.mercari.com/item/m123?x=1")
    assert it and it.price_jpy == 21800 and it.item_id == "m123" and it.condition_id == 3 and it.shipping_included


# ------------------------------------------------------------------ 実務フロー
def _payload(buy=20000):
    return {"item_key": "m1", "mercari_title": "Nikon F3", "genre": "Nikon F3", "category": "カメラ・交換レンズ",
            "mercari_url": "https://jp.mercari.com/item/m1", "buy_price_jpy": buy, "profit_jpy": 20000, "market_usd": 360,
            "breakeven_buy_jpy": 40000, "shipping_cost_jpy": 4000, "packing_jpy": 500, "labor_jpy": 1500,
            "comps": [{"title": "Nikon F3 HP Film Camera Body Excellent", "total_usd": 360, "url": "u", "score": .9}],
            "condition": "目立った傷なし"}


def test_sku_flow_edit_back_return():
    cfg = make_cfg()
    con = con_for(cfg)
    sku = ops.create_from_candidate(con, _payload(), actual_buy_jpy=18000, category="カメラ・交換レンズ")
    con.commit()
    assert con.execute("SELECT predicted_profit_jpy FROM skus WHERE sku=?", (sku,)).fetchone()[0] == 22000, "安く買えた分だけ予測利益が増える"
    for a, f in [("arrive", {}), ("inspect_pass", {"weight_g": 900}), ("list", {"list_price_usd": 380}),
                 ("sell", {"sold_price_usd": 370, "buyer_country": "US"}), ("ship", {"shipping_cost_jpy": 4200, "duty_jpy": 5000})]:
        ok, msg = ops.apply_action(con, sku, a, f)
        assert ok, (a, msg)
    assert ops.apply_action(con, sku, "back", {})[0]  # shipped → sold
    assert con.execute("SELECT status, shipped_at FROM skus WHERE sku=?", (sku,)).fetchone()[:] == ("sold", None)
    assert ops.apply_action(con, sku, "ship", {"shipping_cost_jpy": 4200, "duty_jpy": 5000})[0]
    assert ops.apply_action(con, sku, "edit", {"buy_price_jpy": 18500, "notes": "修正", "purchased_at": "2026-10-01"})[0]
    assert not ops.apply_action(con, sku, "edit", {"buy_price_jpy": "abc"})[0], "数値でない入力は弾く"
    assert ops.apply_action(con, sku, "pay", {})[0], "入金額なしで完了にできる"
    m = ops.metrics(con, cfg, {})
    s = next(x for x in m["skus"] if x["sku"] == sku)
    exp = int((370 - (370 * (0.1325 + 0.0135 + 0.02) + 0.30)) * 150 * 0.98) - 18500 - 4200 - 5000 - 500 - 1500
    assert abs(s["realized_jpy"] - exp) <= 1, (s["realized_jpy"], exp)
    # 返品 → 再検品 / 損失処理
    sku2 = ops.create_from_candidate(con, {**_payload(), "item_key": "m2"}, actual_buy_jpy=None, category="カメラ・交換レンズ")
    for a in ("arrive", "inspect_pass", "list"):
        ops.apply_action(con, sku2, a, {"list_price_usd": 380})
    ops.apply_action(con, sku2, "sell", {"sold_price_usd": 380})
    assert ops.apply_action(con, sku2, "return", {"return_reason": "動作不良", "return_loss_jpy": 3000})[0]
    assert ops.apply_action(con, sku2, "restock", {})[0]
    assert con.execute("SELECT status FROM skus WHERE sku=?", (sku2,)).fetchone()[0] == "arrived"
    m = ops.metrics(con, cfg, {})
    assert m["counts"]["paid"] == 1 and m["counts"]["arrived"] == 1
    assert "restrictions" in m and "ship_learn" in m


def test_ebay_csv_import():
    cfg = make_cfg()
    con = con_for(cfg)
    sku = ops.create_from_candidate(con, {**_payload(), "item_key": "m9"}, actual_buy_jpy=None, category="カメラ・交換レンズ")
    for a in ("arrive", "inspect_pass", "list"):
        ops.apply_action(con, sku, a, {"list_price_usd": 400})
    csv_text = ("Seller Hub Orders Report\n\n"
                "Sales Record Number,Order Number,Custom label,Item Title,Sold For,Shipping And Handling,Sale Date,Ship To Country,Shipped On Date,Tracking Number\n"
                f"101,12-34567-89012,{sku},Nikon F3,$370.00,$25.00,\"Oct 10, 2026\",United States,\"Oct 11, 2026\",EB123456789JP\n"
                "102,99,UNKNOWN-1,Other,$10,$0,\"Oct 10, 2026\",Germany,,\n")
    plan = ebay_import.plan_updates(con, csv_text)
    assert plan["ok"] and plan["unmatched"] == 1 and [c["action"] for c in plan["changes"]] == ["sell", "ship", "edit"], plan
    res = ebay_import.apply(con, csv_text)
    r = con.execute("SELECT status, sold_price_usd, buyer_country, tracking, ebay_order_id FROM skus WHERE sku=?", (sku,)).fetchone()
    assert tuple(r) == ("shipped", 395.0, "US", "EB123456789JP", "12-34567-89012"), (tuple(r), res)
    fees = "Transaction report\nCustom label,Final Value Fee - fixed,Final Value Fee - variable,International fee,Net amount\n" + f"{sku},-0.40,-52.40,-5.33,337.87\n"
    ebay_import.apply(con, fees)
    assert con.execute("SELECT fees_usd FROM skus WHERE sku=?", (sku,)).fetchone()[0] == 58.13


def test_manual_ebay_import_and_cache():
    from flipbot.pipeline import _cached_ebay, ebay_import_status, import_ebay_page
    cfg = make_cfg()
    con = con_for(cfg)
    t = cfg.all_targets[0]
    html = "".join(f'<li class="s-card"><div class="s-card__caption">Sold  Oct {i + 1}, 2026</div><div class="s-card__title">{t.ebay_query} #{i}</div>'
                   f'<div class="s-card__subtitle">Pre-Owned</div><a href="https://www.ebay.com/itm/{i}">x</a><span class="s-card__price">${200 + i}.00</span></li>' for i in range(8))
    url = f"https://www.ebay.com/sch/i.html?_nkw={t.ebay_query.replace(' ', '+')}&LH_Sold=1&LH_Complete=1"
    assert not import_ebay_page(cfg, html, url.replace("&LH_Sold=1", ""))["ok"], "売れた商品のページ以外は弾く"
    r = import_ebay_page(cfg, f"<ul>{html}</ul>", url)
    assert r["ok"] and r["target_id"] == t.id and r["count"] == 8, r
    got = _cached_ebay(con, t, cfg.app.ebay_cache_hours, cfg.app.ebay_manual_days)
    assert got and len(got[1]) == 8 and got[1][0].sold_date, "手動で取り込んだ相場を使う"
    st = {x["id"]: x for x in ebay_import_status(cfg, con)}
    assert st[t.id]["fresh"] and not st[cfg.all_targets[1].id]["fresh"]
    r2 = import_ebay_page(cfg, f"<ul>{html}</ul>", "https://www.ebay.com/sch/i.html?_nkw=zzzz+unknown&LH_Sold=1")
    assert r2.get("need_target"), "一致するジャンルがなければ選ばせる"


def test_listing_draft():
    d = listing.draft({"sku": "2610-001", "title": "Nikon F3", "category": "カメラ・交換レンズ", "payload_json": json.dumps(_payload())})
    assert d["title"].startswith("[Exc+5]") and len(d["title"]) <= 80 and "Excellent" not in d["title"]
    assert d["specifics"]["Brand"] == "Nikon" and "DDP" in d["description"] and d["custom_label"] == "2610-001"


# ------------------------------------------------------------------ 取得の運用
def test_rotation_pause_and_sample_scan():
    cfg = make_cfg()
    con = con_for(cfg)
    n = len(cfg.all_targets)
    seen = []
    for _ in range((n + 4) // 5):
        seen += [t.id for t in select_targets(con, cfg, rotate=True)]
    assert set(seen) >= {t.id for t in cfg.all_targets}, "5件ずつ回して全ジャンルを一巡する"
    hours = []
    for _ in range(4):
        _pause_mercari(con, cfg, "test")
        hours.append(int(db.get_setting(con, "mercari_strikes")))
    assert hours == [1, 2, 3, 4]
    db.set_setting(con, "mercari_pause_until", "")
    con.commit()
    r = asyncio.run(scan_all(cfg, data_source="sample"))
    assert r["data_source"] == "sample" and r["candidates"], "サンプルで候補が出る"
    c = r["candidates"][0]
    assert c["limit_price_jpy"] is not None and "demand" in c


def test_alert_once_and_assumptions():
    cfg = make_cfg()
    con = con_for(cfg)
    sent = []

    class Dummy(notify.Notifier):
        def send(self, text, blocks=None):
            sent.append(text)

    orig = notify.get_notifier
    notify.get_notifier = lambda c: Dummy()
    try:
        assert notify.alert_once(cfg, con, "k", "a") and not notify.alert_once(cfg, con, "k", "b")
    finally:
        notify.get_notifier = orig
    assert sent == ["a"]
    applied = plan_mod.apply_assumptions(cfg, {"assumptions": [{"項目": "米国の買い手比率", "基準（主計画）": 40, "単位": "%"},
                                                               {"項目": "米国関税率", "基準（主計画）": 0.15, "単位": ""}]})
    assert cfg.costs.us_buyer_share == 0.4 and cfg.costs.us_duty_rate == 0.15 and len(applied) == 2


def test_ebay_api_browse_insights(tmp_token=TMP / "tok.json"):
    """公式API: Browse/Insights の応答を相場データに変換、Insights未承認なら24時間止める。"""
    import urllib.error

    from flipbot import ebay_api
    from flipbot.pipeline import _ebay_api_active, _ebay_api_sold

    cfg = make_cfg()
    cfg.ebay_api.client_id, cfg.ebay_api.client_secret = "id", "secret"
    con = con_for(cfg)
    t = Target(id="f3", name="Nikon F3", mercari_query="ニコン F3", ebay_query="Nikon F3", ebay_category_ids=["15230"])
    ebay_api.TOKEN_CACHE = tmp_token
    calls = []

    def fake_open(self, req):
        calls.append(req.full_url)
        if "oauth2/token" in req.full_url:
            return {"access_token": "tok", "expires_in": 7200}
        if "browse" in req.full_url:
            return {"total": 321, "itemSummaries": [
                {"title": "Nikon F3 body", "price": {"value": "250.00"}, "conditionId": "3000",
                 "shippingOptions": [{"shippingCost": {"value": "20"}}], "itemWebUrl": "u1"},
                {"title": "Nikon F3 parts", "price": {"value": "90"}, "conditionId": "7000"}]}
        if insights_ok:
            return {"total": 2, "itemSales": [
                {"title": "Nikon F3 sold", "lastSoldPrice": {"value": "300"}, "lastSoldDate": "2026-10-01T00:00:00.000Z", "conditionId": "1000"},
                {"title": "broken", "lastSoldPrice": None}]}
        raise ebay_api.EbayApiError("未承認", 403)

    orig = ebay_api.EbayApi._open
    ebay_api.EbayApi._open = fake_open
    try:
        insights_ok = True
        a = _ebay_api_active(cfg, con, t)
        assert a["total"] == 321 and a["n"] == 2 and a["min_usd"] == 90 and a["median_usd"] == 270
        assert [i.condition for i in a["items"]] == ["used", "parts"]
        n = len(calls)
        assert _ebay_api_active(cfg, con, t)["total"] == 321 and len(calls) == n  # キャッシュ
        r = _ebay_api_sold(cfg, con, t)
        assert r["total"] == 2 and len(r["items"]) == 1
        it = r["items"][0]
        assert it.total_usd == 300 and it.sold_date == "2026-10-01" and it.condition == "new"
        assert sum(1 for c in calls if "oauth2/token" in c) == 2  # スコープごとに1回
        insights_ok = False
        assert "error" in _ebay_api_sold(cfg, con, t)
        assert db.get_setting(con, "insights_unavailable_until") > db.utc_now_iso()
        n = len(calls)
        assert _ebay_api_sold(cfg, con, t) is None and len(calls) == n  # 24時間は呼ばない
    finally:
        ebay_api.EbayApi._open = orig
    cfg.ebay_api.client_id = ""
    assert ebay_api.status(cfg)["configured"] is False


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    ng = 0
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except Exception:  # noqa: BLE001
            ng += 1
            print(f"NG  {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - ng}/{len(tests)} 合格")
    return 1 if ng else 0


if __name__ == "__main__":
    raise SystemExit(main())
