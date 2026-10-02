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
from .scrape_ebay import EbaySold, build_ebay_sold_url, parse_ebay_sold_html
from .scrape_mercari import (
    MercariListing,
    build_mercari_search_url,
    parse_mercari_search_html,
)


async def fetch_html(page, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=25_000)
    except PlaywrightError:
        pass
    try:
        await page.wait_for_timeout(1800)
    except PlaywrightError:
        await asyncio.sleep(1.8)
    return await page.content()


def detect_block(source: str, html: str) -> Optional[str]:
    h = html.lower()
    if source == "mercari":
        if "お使いのブラウザがwebサイトに対応" in html or "not supported" in h:
            return "Mercariが「ブラウザ非対応」ページを返しています（自動操作検知の可能性）"
    if source == "ebay":
        if "captcha" in h or "recaptcha" in h or "pardon our interruption" in h:
            return "eBayがcaptcha/人間確認ページを返しています"
        if "robot check" in h or "verify you're a human" in h or "verify you are a human" in h:
            return "eBayがボット確認ページを返しています"
        if "security measure" in h and "ebay" in h:
            return "eBayがセキュリティ確認ページを返しています"
    return None


async def crawl_target_once(cfg: Config, target: Target) -> dict[str, Any]:
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    db.upsert_target(con, target.id, target.name)

    session = await open_page(
        user_agent=cfg.app.user_agent if cfg.app.use_config_user_agent else None,
        headless=cfg.app.headless,
        slow_mo_ms=cfg.app.slow_mo_ms,
        user_data_dir=cfg.app.user_data_dir,
        chrome_profile_directory=cfg.app.chrome_profile_directory,
        browser_channel=cfg.app.browser_channel,
        strip_playwright_automation_args=cfg.app.strip_playwright_automation_args,
        chrome_cdp_url=cfg.app.chrome_cdp_url,
    )
    try:
        # Mercari
        mercari_url = build_mercari_search_url(target.mercari_query)
        mercari_html = await fetch_html(session.page, mercari_url)
        mercari_items = parse_mercari_search_html(mercari_html)
        mercari_block = detect_block("mercari", mercari_html)
        if len(mercari_items) == 0 and not mercari_block:
            mercari_block = (
                "Mercariの商品一覧を解析できませんでした（0件）。"
                "Chromeを終了してから再実行するか、`login` で検索ページまで通してください。"
            )
        if len(mercari_items) == 0:
            Path("./data").mkdir(parents=True, exist_ok=True)
            Path(f"./data/debug_mercari_{target.id}.html").write_text(
                mercari_html, encoding="utf-8"
            )
        mercari_snap = db.create_snapshot(
            con,
            target_id=target.id,
            source="mercari",
            meta_json=json.dumps({"url": mercari_url}, ensure_ascii=False),
        )
        for it in mercari_items[:80]:
            con.execute(
                "INSERT INTO mercari_listings(snapshot_id, title, price_jpy, shipping_included, status, url, item_id) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    mercari_snap.id,
                    it.title,
                    it.price_jpy,
                    1 if it.shipping_included else 0,
                    it.status,
                    it.url,
                    it.item_id,
                ),
            )
        con.commit()

        # eBay sold
        ebay_url = build_ebay_sold_url(target.ebay_query)
        ebay_html = await fetch_html(session.page, ebay_url)
        ebay_items = parse_ebay_sold_html(ebay_html)
        ebay_block = detect_block("ebay", ebay_html)
        if len(ebay_items) == 0 and not ebay_block:
            ebay_block = (
                "eBayの成約一覧を解析できませんでした（0件）。"
                "Sold検索ページで人間確認が出ていないか、`login` で同URLを開いて確認してください。"
            )
        if len(ebay_items) == 0:
            Path("./data").mkdir(parents=True, exist_ok=True)
            Path(f"./data/debug_ebay_{target.id}.html").write_text(
                ebay_html, encoding="utf-8"
            )
        ebay_snap = db.create_snapshot(
            con,
            target_id=target.id,
            source="ebay",
            meta_json=json.dumps({"url": ebay_url}, ensure_ascii=False),
        )
        for it in ebay_items[:120]:
            con.execute(
                "INSERT INTO ebay_sold(snapshot_id, title, price_usd, shipping_usd, total_usd, url, ended_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    ebay_snap.id,
                    it.title,
                    it.price_usd,
                    it.shipping_usd,
                    it.total_usd,
                    it.url,
                    None,
                ),
            )
        con.commit()

        return {
            "mercari": {"url": mercari_url, "count": len(mercari_items), "block_reason": mercari_block},
            "ebay": {"url": ebay_url, "count": len(ebay_items), "block_reason": ebay_block},
            "mercari_items": mercari_items,
            "ebay_items": ebay_items,
        }
    finally:
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
        )
        return d


@dataclass
class RankStats:
    scanned: int = 0
    excluded: int = 0
    no_comp: int = 0
    below_threshold: int = 0
    passed: int = 0


SORT_KEYS = {
    "roi": lambda c: (c.est.roi, c.est.profit_jpy),
    "profit": lambda c: (c.est.profit_jpy, c.est.roi),
    # 総合: 利益額 × ROI × マッチ信頼度
    "score": lambda c: (max(c.est.profit_jpy, 0) * max(c.est.roi, 0) * c.match_score, c.est.profit_jpy),
}


def sort_candidates(cands: list[Candidate], sort_by: str = "roi") -> list[Candidate]:
    key = SORT_KEYS.get(sort_by, SORT_KEYS["roi"])
    # 新着は常に上位へ
    return sorted(cands, key=lambda c: (c.is_new, *key(c)), reverse=True)


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
        if est.profit_jpy < min_profit or est.roi < min_roi:
            stats.below_threshold += 1
            continue
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

    if source in ("live", "auto"):
        for t in cfg.all_targets:
            if console:
                console.rule(f"[bold]{t.name}[/bold]")
            try:
                res = await crawl_target_once(cfg, t)
            except (RuntimeError, PlaywrightError) as e:
                notes.append({"target": t.name, "level": "error", "message": str(e)})
                continue
            for src in ("mercari", "ebay"):
                if res[src].get("block_reason"):
                    notes.append({"target": t.name, "level": "warn", "message": f"{src}: {res[src]['block_reason']}"})
            collected.append((t, res["mercari_items"], res["ebay_items"]))

    used = source
    if source == "sample" or (source == "auto" and not any(m for _, m, _ in collected)):
        if source == "auto":
            notes.append({"target": "-", "level": "info", "message": "実サイトから0件だったため、サンプルデータで代替表示しています"})
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
