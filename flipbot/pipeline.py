from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.table import Table

from playwright.async_api import Error as PlaywrightError

from . import db
from .config import Config, Target
from .http import close_session, open_page
from .matching import find_comps, market_price
from .profit import ProfitEstimate, estimate_profit
from .sources import sample_items, sample_targets
from .scrape_ebay import EbaySold, build_ebay_research_url, build_ebay_sold_url, parse_ebay_sold_html
from .scrape_mercari import (
    MercariListing,
    build_mercari_keyword_url,
    build_mercari_search_url,
    parse_mercari_search_html,
)


async def fetch_html(page, url: str, *, scroll_steps: int = 0) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=25_000)
    except PlaywrightError:
        pass
    try:
        await page.wait_for_timeout(1800)
        # 一覧は遅延読み込みなので、少しずつ下までスクロールして全件を描画させる
        for _ in range(scroll_steps):
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(450)
    except PlaywrightError:
        await asyncio.sleep(1.8)
    return await page.content()


def detect_block(source: str, html: str, page_url: str = "") -> Optional[str]:
    h = html.lower()
    u = page_url.lower()
    if source == "mercari":
        if "お使いのブラウザがwebサイトに対応" in html or "not supported" in h:
            return "Mercariが「ブラウザ非対応」ページを返しています（自動操作検知の可能性）"
    if source == "ebay":
        if "signin.ebay" in u or "splashui/captcha" in u:
            return (
                "eBayの売れた商品（Sold）検索にはログインが必要です。"
                "ダッシュボードの「eBayにログインする」ボタンから、ツール用ブラウザでeBayにログインしてください（初回のみ）"
            )
        if "captcha" in h or "recaptcha" in h or "pardon our interruption" in h:
            return "eBayがcaptcha/人間確認ページを返しています（`login` で一度通してください）"
        if "robot check" in h or "verify you're a human" in h or "verify you are a human" in h:
            return "eBayがボット確認ページを返しています"
        if "security measure" in h and "ebay" in h:
            return "eBayがセキュリティ確認ページを返しています"
        if "<title>error page | ebay" in h:
            return "eBayがアクセスを拒否しました（403）。ダッシュボードの「eBayにログインする」からログインしてください"
    return None


def _cached_ebay(con, target: Target, max_age_hours: float) -> Optional[tuple[str, list[EbaySold]]]:
    """直近の eBay 成約データが新しければ再利用（Sold相場は数時間で大きく変わらない）。"""
    if max_age_hours <= 0:
        return None
    row = con.execute(
        "SELECT s.id, s.captured_at FROM snapshots s WHERE s.target_id=? AND s.source='ebay' "
        "AND EXISTS (SELECT 1 FROM ebay_sold e WHERE e.snapshot_id=s.id) ORDER BY s.id DESC LIMIT 1",
        (target.id,),
    ).fetchone()
    if not row:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["captured_at"])
    if age > timedelta(hours=max_age_hours):
        return None
    items = [
        EbaySold(title=r["title"], price_usd=r["price_usd"], shipping_usd=r["shipping_usd"], total_usd=r["total_usd"], url=r["url"])
        for r in con.execute("SELECT * FROM ebay_sold WHERE snapshot_id=?", (row["id"],))
    ]
    return row["captured_at"], items


def _save_debug(name: str, html: str) -> None:
    Path("./data").mkdir(parents=True, exist_ok=True)
    Path(f"./data/debug_{name}.html").write_text(html, encoding="utf-8")


async def open_session(cfg: Config):
    return await open_page(
        user_agent=cfg.app.user_agent if cfg.app.use_config_user_agent else None,
        headless=cfg.app.headless,
        slow_mo_ms=cfg.app.slow_mo_ms,
        user_data_dir=cfg.app.user_data_dir,
        chrome_profile_directory=cfg.app.chrome_profile_directory,
        browser_channel=cfg.app.browser_channel,
        strip_playwright_automation_args=cfg.app.strip_playwright_automation_args,
        chrome_cdp_url=cfg.app.chrome_cdp_url,
    )


async def crawl_target_once(cfg: Config, target: Target, session=None, *, fetch_ebay: bool = True) -> dict[str, Any]:
    """1ジャンル分を収集。session を渡せばブラウザを使い回す（渡さなければ自分で開閉）。"""
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    db.upsert_target(con, target.id, target.name)
    own_session = session is None
    if own_session:
        session = await open_session(cfg)
    try:
        # Mercari（販売中・新着順）
        mercari_url = build_mercari_search_url(target.mercari_query)
        mercari_html = await fetch_html(session.page, mercari_url, scroll_steps=cfg.app.mercari_scroll_steps)
        mercari_items = parse_mercari_search_html(mercari_html)
        mercari_block = detect_block("mercari", mercari_html, session.page.url)
        if len(mercari_items) == 0 and not mercari_block:
            mercari_block = "Mercariの商品一覧を解析できませんでした（0件。検索語を見直すか、ページ構造が変わった可能性）"
        if len(mercari_items) == 0:
            _save_debug(f"mercari_{target.id}", mercari_html)
        mercari_snap = db.create_snapshot(
            con, target_id=target.id, source="mercari",
            meta_json=json.dumps({"url": mercari_url}, ensure_ascii=False),
        )
        con.executemany(
            "INSERT INTO mercari_listings(snapshot_id, title, price_jpy, shipping_included, status, url, item_id) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            [
                (mercari_snap.id, it.title, it.price_jpy, 1 if it.shipping_included else 0, it.status, it.url, it.item_id)
                for it in mercari_items
            ],
        )
        con.commit()

        # eBay sold（キャッシュが新しければ再利用してアクセスを減らす）
        ebay_url = build_ebay_sold_url(target.ebay_query)
        ebay_block: Optional[str] = None
        cached = _cached_ebay(con, target, cfg.app.ebay_cache_hours)
        if cached:
            ebay_cached_at, ebay_items = cached
        elif not fetch_ebay:
            # 既にブロックされているので、同じスキャン中は eBay に繰り返しアクセスしない
            ebay_cached_at, ebay_items = None, []
        else:
            ebay_cached_at = None
            ebay_html = await fetch_html(session.page, ebay_url)
            ebay_items = parse_ebay_sold_html(ebay_html)
            ebay_block = detect_block("ebay", ebay_html, session.page.url)
            if len(ebay_items) == 0 and not ebay_block:
                ebay_block = "eBayの成約一覧を解析できませんでした（0件。検索語を見直してください）"
            if len(ebay_items) == 0:
                _save_debug(f"ebay_{target.id}", ebay_html)
            else:
                ebay_snap = db.create_snapshot(
                    con, target_id=target.id, source="ebay",
                    meta_json=json.dumps({"url": ebay_url}, ensure_ascii=False),
                )
                con.executemany(
                    "INSERT INTO ebay_sold(snapshot_id, title, price_usd, shipping_usd, total_usd, url, ended_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    [(ebay_snap.id, it.title, it.price_usd, it.shipping_usd, it.total_usd, it.url, None) for it in ebay_items],
                )
                con.commit()

        return {
            "mercari": {"url": mercari_url, "count": len(mercari_items), "block_reason": mercari_block},
            "ebay": {"url": ebay_url, "count": len(ebay_items), "block_reason": ebay_block, "cached_at": ebay_cached_at},
            "mercari_items": mercari_items,
            "ebay_items": ebay_items,
        }
    finally:
        if own_session:
            await close_session(session)
        con.close()


@dataclass
class Candidate:
    est: ProfitEstimate
    genre: str
    item_key: str
    market_usd: float
    match_score: float
    comps: list[dict[str, Any]]
    comps_count: int
    shipping_included: bool
    first_seen: str
    is_new: bool
    image_url: Optional[str] = None
    is_auction: bool = False
    # 根拠ページ
    ebay_sold_search_url: str = ""
    ebay_research_url: str = ""
    mercari_similar_url: str = ""
    # 基準（下限利益・ROI）未達だが利益は出る「惜しい候補」
    near_miss: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self.est)
        d.update(
            genre=self.genre,
            item_key=self.item_key,
            market_usd=round(self.market_usd, 2),
            match_score=self.match_score,
            comps=self.comps,
            comps_count=self.comps_count,
            shipping_included=self.shipping_included,
            first_seen=self.first_seen,
            is_new=self.is_new,
            image_url=self.image_url,
            is_auction=self.is_auction,
            ebay_sold_search_url=self.ebay_sold_search_url,
            ebay_research_url=self.ebay_research_url,
            mercari_similar_url=self.mercari_similar_url,
            near_miss=self.near_miss,
        )
        return d


@dataclass
class RankStats:
    scanned: int = 0
    excluded: int = 0
    no_comp: int = 0
    below_threshold: int = 0
    passed: int = 0
    near_miss: int = 0


SORT_KEYS = {
    "roi": lambda c: (c.est.roi, c.est.profit_jpy),
    "profit": lambda c: (c.est.profit_jpy, c.est.roi),
    # 総合: 利益額 × ROI × マッチ信頼度
    "score": lambda c: (max(c.est.profit_jpy, 0) * max(c.est.roi, 0) * c.match_score, c.est.profit_jpy),
}


def sort_candidates(cands: list[Candidate], sort_by: str = "roi") -> list[Candidate]:
    key = SORT_KEYS.get(sort_by, SORT_KEYS["roi"])
    # 基準を満たす候補 → 惜しい候補 の順。その中で新着は常に上位へ
    return sorted(cands, key=lambda c: (not c.near_miss, c.is_new, *key(c)), reverse=True)


def _is_excluded(cfg: Config, target: Target, title: str) -> bool:
    t = title.lower()
    if any(k.lower() in t for k in [*cfg.exclude_keywords, *target.exclude_keywords]):
        return True
    if target.include_keywords and not any(k.lower() in t for k in target.include_keywords):
        return True
    return False


def rank_target(
    *,
    cfg: Config,
    target: Target,
    mercari_items: list[MercariListing],
    ebay_items: list[EbaySold],
    con,
) -> tuple[list[Candidate], RankStats]:
    th = cfg.thresholds
    min_profit = target.min_profit_jpy if target.min_profit_jpy is not None else th.min_profit_jpy
    min_roi = target.min_roi if target.min_roi is not None else th.min_roi
    # 初回スキャン時点で既にあった出品は「新着」ではない
    baseline = db.baseline_seen_at(con, target.id)
    new_cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.app.new_window_hours)

    stats = RankStats()
    out: list[Candidate] = []
    for it in mercari_items:
        if it.status != "on_sale":
            continue
        stats.scanned += 1
        item_key = it.item_id or it.url
        first_seen = db.mark_seen(con, source="mercari", item_key=item_key, target_id=target.id)
        is_new = (
            baseline is not None
            and first_seen > baseline
            and datetime.fromisoformat(first_seen) >= new_cutoff
        )

        if _is_excluded(cfg, target, it.title):
            stats.excluded += 1
            continue
        comps = find_comps(it.title, ebay_items, title_of=lambda e: e.title, threshold=th.match_threshold)
        market = market_price([c.item.total_usd for c in comps], min_samples=th.min_comps)
        if market is None:
            stats.no_comp += 1
            continue
        est = estimate_profit(
            cfg=cfg,
            target=target,
            buy_price_jpy=it.price_jpy,
            sell_total_usd=market,
            mercari_title=it.title,
            mercari_url=it.url,
        )
        near_miss = est.profit_jpy < min_profit or est.roi < min_roi
        if near_miss:
            stats.below_threshold += 1
            if est.profit_jpy <= 0:
                continue
            stats.near_miss += 1
        else:
            stats.passed += 1
        out.append(
            Candidate(
                est=est,
                genre=target.genre_label,
                item_key=item_key,
                market_usd=market,
                match_score=comps[0].score,
                comps=[
                    {"title": c.item.title, "total_usd": c.item.total_usd, "url": c.item.url, "score": c.score}
                    for c in comps[:5]
                ],
                comps_count=len(comps),
                shipping_included=it.shipping_included,
                first_seen=first_seen,
                is_new=is_new,
                image_url=it.image_url,
                is_auction=it.is_auction,
                ebay_sold_search_url=build_ebay_sold_url(target.ebay_query),
                ebay_research_url=build_ebay_research_url(target.ebay_query),
                mercari_similar_url=build_mercari_keyword_url(target.mercari_query),
                near_miss=near_miss,
            )
        )
    con.commit()
    return out, stats


async def scan_all(
    cfg: Config,
    *,
    data_source: Optional[str] = None,
    sort_by: str = "roi",
    console: Optional[Console] = None,
) -> dict[str, Any]:
    """全ジャンルを1周して候補を作り、DBに保存して要約を返す。"""
    source = (data_source or cfg.app.data_source).lower()
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    started = db.utc_now_iso()
    notes: list[dict[str, Any]] = []
    collected: list[tuple[Target, list[MercariListing], list[EbaySold]]] = []

    if source in ("live", "auto") and cfg.all_targets:
        session = None
        try:
            session = await open_session(cfg)
        except (RuntimeError, PlaywrightError) as e:
            notes.append({"target": "ブラウザ", "level": "error", "message": str(e)})
        if session is not None:
            ebay_ok = True
            try:
                for t in cfg.all_targets:
                    if console:
                        console.rule(f"[bold]{t.name}[/bold]")
                    try:
                        res = await crawl_target_once(cfg, t, session=session, fetch_ebay=ebay_ok)
                    except (RuntimeError, PlaywrightError) as e:
                        notes.append({"target": t.name, "level": "error", "message": str(e)})
                        continue
                    for src in ("mercari", "ebay"):
                        if res[src].get("block_reason"):
                            notes.append({"target": t.name, "level": "warn", "message": f"{src}: {res[src]['block_reason']}"})
                    eb = res["ebay"].get("block_reason") or ""
                    if "ログイン" in eb or "拒否" in eb or "captcha" in eb:
                        ebay_ok = False
                    collected.append((t, res["mercari_items"], res["ebay_items"]))
            finally:
                await close_session(session)

    used = source
    live_ok = any(m and e for _, m, e in collected)
    if source == "sample" or (source == "auto" and not live_ok):
        if source == "auto":
            notes.append({"target": "-", "level": "info", "message": "実サイトから相場を作れなかったため、サンプルデータで代替表示しています（上の警告を確認してください）"})
        used = "sample"
        collected = []
        for t in sample_targets():
            con.execute("INSERT INTO targets(id, name) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (t.id, t.name))
            m, e = sample_items(t.id)
            collected.append((t, m, e))

    all_cands: list[Candidate] = []
    total = RankStats()
    mercari_count = ebay_count = 0
    for t, m_items, e_items in collected:
        mercari_count += len(m_items)
        ebay_count += len(e_items)
        cands, st = rank_target(cfg=cfg, target=t, mercari_items=m_items, ebay_items=e_items, con=con)
        all_cands.extend(cands)
        for k in asdict(st):
            setattr(total, k, getattr(total, k) + getattr(st, k))

    ranked = sort_candidates(all_cands, sort_by)
    finished = db.utc_now_iso()
    summary = {
        "started_at": started,
        "finished_at": finished,
        "data_source": used,
        "mercari_count": mercari_count,
        "ebay_count": ebay_count,
        "genres": [t.genre_label for t, _, _ in collected],
        "stats": asdict(total),
        "notes": notes,
        "jpy_per_usd": cfg.fx.jpy_per_usd,
        "min_profit_jpy": cfg.thresholds.min_profit_jpy,
        "min_roi": cfg.thresholds.min_roi,
    }
    cur = con.execute(
        "INSERT INTO scans(started_at, finished_at, data_source, summary_json) VALUES(?, ?, ?, ?)",
        (started, finished, used, json.dumps(summary, ensure_ascii=False)),
    )
    scan_id = int(cur.lastrowid)
    con.executemany(
        "INSERT INTO candidates(scan_id, target_id, item_key, profit_jpy, roi, payload_json) VALUES(?, ?, ?, ?, ?, ?)",
        [
            (scan_id, c.est.target_id, c.item_key, c.est.profit_jpy, c.est.roi, json.dumps(c.to_dict(), ensure_ascii=False))
            for c in ranked
        ],
    )
    con.commit()
    con.close()
    summary["scan_id"] = scan_id
    summary["candidates"] = [c.to_dict() for c in ranked]
    return summary


def render_ranking(console: Console, candidates: list[dict[str, Any]], *, limit: int = 20) -> None:
    table = Table(title="利益ランキング（想定）", show_lines=False)
    for col, j in [
        ("順位", "right"), ("", "left"), ("ジャンル", "left"), ("利益(円)", "right"), ("ROI", "right"),
        ("仕入(円)", "right"), ("eBay相場", "right"), ("マッチ", "right"), ("損益分岐", "right"),
    ]:
        table.add_column(col, justify=j)
    table.add_column("タイトル", overflow="fold")
    table.add_column("URL", overflow="fold")
    candidates = [x for x in candidates if not x.get("near_miss")]
    for i, x in enumerate(candidates[:limit], start=1):
        table.add_row(
            str(i),
            "[bold yellow]NEW[/bold yellow]" if x["is_new"] else "",
            x["genre"],
            f"{x['profit_jpy']:,}",
            f"{x['roi']*100:.0f}%",
            f"{x['buy_price_jpy']:,}",
            f"${x['market_usd']:.0f} ({x['comps_count']}件)",
            f"{x['match_score']*100:.0f}",
            f"{x['breakeven_buy_jpy']:,}",
            x["mercari_title"],
            x["mercari_url"],
        )
    console.print(table)
