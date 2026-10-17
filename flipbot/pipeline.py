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
from . import fx as fx_mod
from . import plan as plan_mod
from .market import demand, select_comps
from .matching import find_comps, market_price
from .profit import ProfitEstimate, estimate_profit
from .sources import sample_items, sample_targets
from .scrape_ebay import (
    EbaySold,
    build_ebay_active_url,
    build_ebay_research_url,
    build_ebay_sold_url,
    parse_ebay_sold_html,
    parse_result_count,
)
from .scrape_mercari import (
    MercariListing,
    build_mercari_keyword_url,
    build_mercari_search_url,
    parse_mercari_search_html,
)


async def fetch_html(page, url: str, *, scroll_steps: int = 0) -> str:
    html, _ = await fetch_html_status(page, url, scroll_steps=scroll_steps)
    return html


async def fetch_html_status(page, url: str, *, scroll_steps: int = 0) -> tuple[str, Optional[int]]:
    """HTMLと HTTPステータス（403/429 の検知用）を返す。"""
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    status = resp.status if resp else None
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
    return await page.content(), status


def detect_block(source: str, html: str, page_url: str = "") -> Optional[str]:
    h = html.lower()
    u = page_url.lower()
    if source == "mercari":
        if "お使いのブラウザがwebサイトに対応" in h or "not supported" in h:
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


def _cached_ebay(con, target: Target, max_age_hours: float) -> Optional[tuple[str, list[EbaySold], Optional[int]]]:
    """直近の eBay 成約データが新しければ再利用（Sold相場は数時間で大きく変わらない）。"""
    if max_age_hours <= 0:
        return None
    row = con.execute(
        "SELECT s.id, s.captured_at, s.meta_json FROM snapshots s WHERE s.target_id=? AND s.source='ebay' "
        "AND EXISTS (SELECT 1 FROM ebay_sold e WHERE e.snapshot_id=s.id) ORDER BY s.id DESC LIMIT 1",
        (target.id,),
    ).fetchone()
    if not row:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["captured_at"])
    if age > timedelta(hours=max_age_hours):
        return None
    items = [
        EbaySold(title=r["title"], price_usd=r["price_usd"], shipping_usd=r["shipping_usd"], total_usd=r["total_usd"], url=r["url"],
                 condition=r["condition"], sold_date=r["sold_date"])
        for r in con.execute("SELECT * FROM ebay_sold WHERE snapshot_id=?", (row["id"],))
    ]
    try:
        active = json.loads(row["meta_json"] or "{}").get("active_count")
    except ValueError:
        active = None
    return row["captured_at"], items, active


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
        block_resources=True,
    )


CONDITION_LABELS = {1: "新品・未使用", 2: "未使用に近い", 3: "目立った傷なし", 4: "やや傷あり", 5: "傷あり", 6: "状態が悪い"}


class LazySession:
    """ブラウザは必要になったときだけ開く（メルカリをAPIで取り、eBayがキャッシュならブラウザ不要）。"""

    def __init__(self, cfg: Config, session=None) -> None:
        self.cfg, self.session = cfg, session
        self.error: Optional[Exception] = None
        self.mercari_anomaly: Optional[str] = None  # 止められている兆候（あればスキャンを中断）
        self.prev_mercari_count: dict[str, int] = {}  # ジャンルごとの前回の取得件数（急な0件の検知用）

    async def get(self):
        if self.error is not None:  # 一度起動に失敗したら同じスキャン中は再挑戦しない
            raise RuntimeError(str(self.error))
        if self.session is None:
            try:
                self.session = await open_session(self.cfg)
            except (RuntimeError, PlaywrightError) as e:
                self.error = e
                raise
        return self.session

    async def close(self) -> None:
        if self.session is not None:
            await close_session(self.session)
            self.session = None


def excludes_for(cfg: Config, target: Target) -> list[str]:
    """除外語の3階層（全体 ＋ カテゴリ ＋ ジャンル）。0001_sedori の設計を踏襲。"""
    return [*cfg.exclude_keywords, *cfg.category_excludes.get(target.category or "", []), *target.exclude_keywords]


def all_genres(con, cfg: Config) -> list[tuple[Target, bool, bool]]:
    """(ジャンル, 有効か, 画面で追加したものか)。config.yaml のジャンル＋画面で追加したジャンル。"""
    ov = {r["target_id"]: r for r in con.execute("SELECT * FROM genre_overrides")}
    out = [(t, bool(ov[t.id]["active"]) if t.id in ov else True, False) for t in cfg.all_targets]
    for tid, r in ov.items():
        if r["custom_json"] and tid not in {t.id for t in cfg.all_targets}:
            try:
                out.append((Target.model_validate(json.loads(r["custom_json"])), bool(r["active"]), True))
            except ValueError:
                continue
    return out


def active_genres(con, cfg: Config) -> list[Target]:
    return [t for t, on, _ in all_genres(con, cfg) if on]


class Learned:
    """照合で「別物」と判定した組み合わせ。判定した出品と似たタイトル（一致0.8以上）の出品にだけ効かせる。"""

    def __init__(self, con, target_id: str) -> None:
        self.bad: dict[str, list[str]] = {}
        for r in con.execute("SELECT ebay_url, ebay_title, mercari_title FROM match_labels WHERE label=0 AND target_id=?", (target_id,)):
            self.bad.setdefault(r["ebay_url"] or r["ebay_title"], []).append(r["mercari_title"])

    def filter(self, mercari_title: str, comps: list) -> list:
        if not self.bad:
            return comps
        from .matching import match_score

        def is_bad(c) -> bool:
            titles = self.bad.get(c.item.url) or self.bad.get(c.item.title) or []
            return any(t == mercari_title or match_score(t, mercari_title) >= 0.8 for t in titles)

        return [c for c in comps if not is_bad(c)]


def learned_shipping(con, cfg: Config) -> dict[str, int]:
    """ジャンル（表示名）→ 実送料の中央値。件数が learn_min_n 以上のものだけ。"""
    if not cfg.costs.learn_shipping:
        return {}
    vals: dict[str, list[int]] = {}
    for r in con.execute("SELECT genre, shipping_cost_jpy FROM skus WHERE shipping_cost_jpy IS NOT NULL AND genre IS NOT NULL"):
        vals.setdefault(r["genre"], []).append(r["shipping_cost_jpy"])
    return {g: sorted(v)[len(v) // 2] for g, v in vals.items() if len(v) >= cfg.costs.learn_min_n}


def master_targets(con) -> list[Target]:
    """マスター（継続仕入れ）登録品をジャンルとして返す。"""
    out = []
    for r in con.execute("SELECT * FROM watch_items WHERE active=1 ORDER BY id"):
        out.append(Target(
            id=f"m{r['id']}", name=f"★{r['label']}", genre=f"マスター：{r['label']}", category=r["category"],
            mercari_query=r["mercari_query"], ebay_query=r["ebay_query"], max_buy_jpy=r["max_buy_jpy"] or None,
            shipping_cost_jpy=r["shipping_jpy"] or 0, min_profit_jpy=r["min_profit_jpy"],
            exclude_keywords=[w for w in (r["exclude"] or "").split() if w], is_master=True,
        ))
    return out


def max_buy_for(cfg: Config, target: Target) -> Optional[int]:
    v = target.max_buy_jpy if target.max_buy_jpy is not None else cfg.thresholds.max_buy_jpy
    return int(v) if v else None


async def _mercari_items(cfg: Config, target: Target, lazy: "LazySession") -> tuple[list[MercariListing], Optional[str], str]:
    """メルカリの出品を取る。api 指定でも失敗したらブラウザに切り替える。"""
    note = None
    if cfg.app.mercari_source == "api":
        from . import mercari_api

        lo, hi = (cfg.app.mercari_api_interval + [3.0, 7.0])[:2]
        try:
            items = await asyncio.to_thread(
                mercari_api.search, target.mercari_query, max_price_jpy=max_buy_for(cfg, target),
                exclude_keyword=" ".join(excludes_for(cfg, target)) or None, condition_ids=target.conditions,
                min_interval_sec=lo, max_interval_sec=hi,
            )
            return items, None, "api"
        except Exception as e:  # noqa: BLE001 - ブラウザ取得にフォールバック
            note = f"メルカリAPIで取得できなかったため、ブラウザで取得しました（{type(e).__name__}）"
    session = await lazy.get()
    url = build_mercari_search_url(target.mercari_query)
    html, status = await fetch_html_status(session.page, url, scroll_steps=cfg.app.mercari_scroll_steps)
    items = parse_mercari_search_html(html)
    block = detect_block("mercari", html, session.page.url)
    # 異常の検知（アクセスを止められている兆候）→ scan_all がメルカリ取得を一時停止する
    if status in (403, 429) or (status and status >= 500):
        lazy.mercari_anomaly = f"メルカリがHTTP {status} を返しました"
    elif block or any(k in session.page.url for k in ("challenge", "captcha")):
        lazy.mercari_anomaly = block or "メルカリで人間確認ページが表示されました"
    elif not items and lazy.prev_mercari_count.get(target.id, 0) >= 10:
        lazy.mercari_anomaly = f"前回{lazy.prev_mercari_count[target.id]}件あった検索が急に0件になりました"
    if not items:
        _save_debug(f"mercari_{target.id}", html)
        block = block or "Mercariの商品一覧を解析できませんでした（0件。検索語を見直すか、ページ構造が変わった可能性）"
    return items, block or note, "browser"


async def crawl_target_once(cfg: Config, target: Target, session=None, *, fetch_ebay: bool = True, lazy: Optional["LazySession"] = None) -> dict[str, Any]:
    """1ジャンル分を収集。lazy/session を渡せばブラウザを使い回す（渡さなければ自分で開閉）。"""
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    db.upsert_target(con, target.id, target.name)
    own = lazy is None
    lazy = lazy or LazySession(cfg, session)
    try:
        # Mercari（販売中・新着順）
        mercari_url = build_mercari_search_url(target.mercari_query)
        mercari_items, mercari_block, mercari_via = await _mercari_items(cfg, target, lazy)
        mercari_snap = db.create_snapshot(
            con, target_id=target.id, source="mercari",
            meta_json=json.dumps({"url": mercari_url, "via": mercari_via}, ensure_ascii=False),
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
        active_count: Optional[int] = None
        if cached:
            ebay_cached_at, ebay_items, active_count = cached
        elif not fetch_ebay:
            # 既にブロックされているので、同じスキャン中は eBay に繰り返しアクセスしない
            ebay_cached_at, ebay_items = None, []
        else:
            ebay_cached_at = None
            session = await lazy.get()
            ebay_html = await fetch_html(session.page, ebay_url)
            ebay_items = parse_ebay_sold_html(ebay_html, cfg.fx.jpy_per_usd)
            ebay_block = detect_block("ebay", ebay_html, session.page.url)
            if len(ebay_items) == 0 and not ebay_block:
                ebay_block = "eBayの成約一覧を解析できませんでした（0件。検索語を見直してください）"
            if len(ebay_items) == 0:
                _save_debug(f"ebay_{target.id}", ebay_html)
            else:
                # 出品中（競合）の件数：消化率・売れるまでの日数の推定に使う（成約データと一緒に12時間キャッシュ）
                try:
                    active_html = await fetch_html(session.page, build_ebay_active_url(target.ebay_query))
                    active_count = parse_result_count(active_html)
                except PlaywrightError:
                    active_count = None
                ebay_snap = db.create_snapshot(
                    con, target_id=target.id, source="ebay",
                    meta_json=json.dumps({"url": ebay_url, "active_count": active_count}, ensure_ascii=False),
                )
                con.executemany(
                    "INSERT INTO ebay_sold(snapshot_id, title, price_usd, shipping_usd, total_usd, url, ended_at, condition, sold_date) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ebay_snap.id, it.title, it.price_usd, it.shipping_usd, it.total_usd, it.url, None, it.condition, it.sold_date) for it in ebay_items],
                )
                con.commit()

        return {
            "mercari": {"url": mercari_url, "count": len(mercari_items), "block_reason": mercari_block},
            "ebay": {"url": ebay_url, "count": len(ebay_items), "block_reason": ebay_block, "cached_at": ebay_cached_at,
                     "active_count": active_count},
            "mercari_items": mercari_items,
            "ebay_items": ebay_items,
        }
    finally:
        if own:
            await lazy.close()
        con.close()


@dataclass
class Candidate:
    est: ProfitEstimate
    genre: str
    category: Optional[str]
    min_profit_jpy: int
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
    condition: Optional[str] = None  # メルカリの商品状態（API取得時）
    is_shop: bool = False
    listed_at: Optional[str] = None
    demand_count: int = 0  # eBayで同じ検索語で売れた件数（需要）
    is_master: bool = False
    demand: Optional[dict[str, Any]] = None  # 月間成約・出品中・消化率・売れるまでの日数・60日以内の見込み
    comp_note: Optional[str] = None  # 比較対象の状態のそろえ方
    plan_sell60: Optional[float] = None  # 計画シートのカテゴリ別「60日以内販売率」
    limit_price_jpy: Optional[int] = None  # 指値：下限利益を確保できる仕入れ値の上限

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self.est)
        d.update(
            genre=self.genre,
            category=self.category,
            min_profit_jpy=self.min_profit_jpy,
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
            condition=self.condition,
            is_shop=self.is_shop,
            listed_at=self.listed_at,
            demand_count=self.demand_count,
            is_master=self.is_master,
            demand=self.demand,
            comp_note=self.comp_note,
            plan_sell60=self.plan_sell60,
            limit_price_jpy=self.limit_price_jpy,
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
    low_demand: int = 0  # eBayの成約件数が下限未満
    over_budget: int = 0  # 仕入れ上限超え
    accessory: int = 0  # 相場に対して安すぎる＝付属品・別物の疑い


SORT_KEYS = {
    "roi": lambda c: (c.est.roi, c.est.profit_jpy),
    "profit": lambda c: (c.est.profit_jpy, c.est.roi),
    # 総合: 貢献利益額 × マッチ信頼度 × 需要（成約件数。5件以上で満点）
    #   意思決定ログ 2026-07-21「ROIだけでなく利益額・売却確率・在庫日数を重視」
    #   需要は「60日以内に売れる見込み」があればそれを、なければ成約件数（5件で満点）を使う
    "score": lambda c: (max(c.est.profit_jpy, 0) * c.match_score
                        * ((c.demand or {}).get("sell60") if (c.demand or {}).get("sell60") is not None else min(1.0, c.comps_count / 5)),
                        c.est.profit_jpy),
}


def sort_candidates(cands: list[Candidate], sort_by: str = "score") -> list[Candidate]:
    key = SORT_KEYS.get(sort_by, SORT_KEYS["roi"])
    # 基準を満たす候補 → 惜しい候補 の順。その中で新着は常に上位へ
    return sorted(cands, key=lambda c: (not c.near_miss, c.is_new, *key(c)), reverse=True)


def _is_excluded(cfg: Config, target: Target, title: str) -> bool:
    t = title.lower()
    if any(k.lower() in t for k in excludes_for(cfg, target)):
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
    plan: Optional[dict[str, Any]] = None,
    active_count: Optional[int] = None,
) -> tuple[list[Candidate], RankStats]:
    th = cfg.thresholds
    learned = Learned(con, target.id)
    dem = demand(ebay_items, active_count)
    plan_sell60 = plan_mod.category_row(plan or {}, target.category).get("60日以内販売率")
    # 下限: ジャンル個別 → 計画シートのカテゴリ別「貢献利益下限」 → 全体設定
    cat_min = plan_mod.category_min_profit(plan or {}, target.category)
    min_profit = target.min_profit_jpy if target.min_profit_jpy is not None else cat_min if cat_min is not None else th.min_profit_jpy
    min_roi = target.min_roi if target.min_roi is not None else th.min_roi
    # 初回スキャン時点で既にあった出品は「新着」ではない
    baseline = db.baseline_seen_at(con, target.id)
    new_cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.app.new_window_hours)

    stats = RankStats()
    out: list[Candidate] = []
    # 需要の下限（0001_sedori の min_sold_count）：eBayで売れていないジャンルは候補にしない
    low_demand = len(ebay_items) < th.min_sold_count
    cap = max_buy_for(cfg, target)
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

        if _is_excluded(cfg, target, it.title) or (target.conditions and it.condition_id and it.condition_id not in target.conditions):
            stats.excluded += 1
            continue
        if cap and it.price_jpy > cap:
            stats.over_budget += 1
            continue
        if low_demand:
            stats.low_demand += 1
            continue
        comps = learned.filter(it.title, find_comps(it.title, ebay_items, title_of=lambda e: e.title, threshold=th.match_threshold))
        comps, comp_note = select_comps(comps, it.condition_id, th.min_comps)
        market = market_price([c.item.total_usd for c in comps], min_samples=th.min_comps)
        if market is None:
            stats.no_comp += 1
            continue
        # 付属品・別物よけ（0001_sedori の min_price_ratio）：相場の2割未満の出品は本体ではない可能性が高い
        if th.min_price_ratio and it.price_jpy < market * cfg.fx.jpy_per_usd * th.min_price_ratio:
            stats.accessory += 1
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
                category=target.category,
                min_profit_jpy=min_profit,
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
                condition=CONDITION_LABELS.get(it.condition_id) if it.condition_id else None,
                is_shop=it.is_shop,
                listed_at=it.listed_at,
                demand_count=len(ebay_items),
                is_master=target.is_master,
                demand=dem,
                comp_note=comp_note,
                plan_sell60=plan_sell60,
                limit_price_jpy=max(est.breakeven_buy_jpy - min_profit, 0),
            )
        )
    con.commit()
    return out, stats


def select_targets(con, cfg: Config, *, rotate: bool) -> list[Target]:
    """今回取得するジャンル。設定ジャンルは genres_per_scan 件ずつ順番に回し、マスターは毎回入れる。"""
    if cfg.app.scan_mode == "masters_only":
        return master_targets(con)
    genres = active_genres(con, cfg)
    n = cfg.app.genres_per_scan
    if rotate and n and len(genres) > n:
        start = int(db.get_setting(con, "rotation_index", "0") or 0) % len(genres)
        genres = [genres[(start + i) % len(genres)] for i in range(n)]
        db.set_setting(con, "rotation_index", (start + n) % len(active_genres(con, cfg)))
        con.commit()
    return [*genres, *master_targets(con)]


def _prev_counts(con) -> dict[str, int]:
    rows = con.execute(
        "SELECT s.target_id, COUNT(m.id) n FROM snapshots s LEFT JOIN mercari_listings m ON m.snapshot_id=s.id "
        "WHERE s.source='mercari' AND s.id IN (SELECT MAX(id) FROM snapshots WHERE source='mercari' GROUP BY target_id) "
        "GROUP BY s.target_id"
    )
    return {r["target_id"]: r["n"] for r in rows}


def _pause_mercari(con, cfg: Config, reason: str) -> str:
    strikes = int(db.get_setting(con, "mercari_strikes", "0") or 0) + 1
    hours = min(cfg.app.mercari_pause_hours * (2 ** (strikes - 1)), cfg.app.mercari_pause_max_hours)
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
    db.set_setting(con, "mercari_strikes", strikes)
    db.set_setting(con, "mercari_pause_until", until)
    db.set_setting(con, "mercari_pause_reason", reason)
    con.commit()
    return until


def _jst(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")
    except ValueError:
        return iso


async def scan_all(
    cfg: Config,
    *,
    data_source: Optional[str] = None,
    sort_by: str = "score",
    console: Optional[Console] = None,
    rotate: bool = False,
) -> dict[str, Any]:
    """全ジャンルを1周して候補を作り、DBに保存して要約を返す。"""
    source = (data_source or cfg.app.data_source).lower()
    fx_mod.apply(cfg)
    plan = plan_mod.load(cfg)
    from_sheet = plan_mod.apply_assumptions(cfg, plan)
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    started = db.utc_now_iso()
    notes: list[dict[str, Any]] = []
    collected: list[tuple[Target, list[MercariListing], list[EbaySold]]] = []
    actives: dict[str, Optional[int]] = {}

    targets = select_targets(con, cfg, rotate=rotate)
    paused_until = db.get_setting(con, "mercari_pause_until")
    paused = bool(paused_until and paused_until > db.utc_now_iso())
    if source in ("live", "auto") and paused:
        notes.append({"target": "メルカリ", "level": "warn",
                      "message": f"アクセスを止められている兆候があったため、{_jst(paused_until)} まで取得を一時停止しています（画面の「ルール・前提」から再開できます）"})
        targets = []
    if source in ("live", "auto") and targets:
        lazy = LazySession(cfg)
        lazy.prev_mercari_count = _prev_counts(con)
        ebay_ok = True
        try:
            for t in targets:
                if console:
                    console.rule(f"[bold]{t.name}[/bold]")
                try:
                    res = await crawl_target_once(cfg, t, fetch_ebay=ebay_ok, lazy=lazy)
                except (RuntimeError, PlaywrightError) as e:
                    notes.append({"target": t.name, "level": "error", "message": str(e)})
                    continue
                for src in ("mercari", "ebay"):
                    if res[src].get("block_reason"):
                        notes.append({"target": t.name, "level": "warn", "message": f"{src}: {res[src]['block_reason']}"})
                eb = res["ebay"].get("block_reason") or ""
                if "ログイン" in eb or "拒否" in eb or "captcha" in eb:
                    if ebay_ok:
                        from .notify import alert_once

                        alert_once(cfg, con, "ebay_login",
                                   "🔑 eBayの相場を取得できません（ログイン切れ・アクセス拒否）。ツールのダッシュボードの「eBayにログインする」からログインしてください。",
                                   every_hours=12)
                    ebay_ok = False
                collected.append((t, res["mercari_items"], res["ebay_items"]))
                actives[t.id] = res["ebay"].get("active_count")
                if lazy.mercari_anomaly:
                    # 止められている兆候：残りのジャンルは取得せず、一定時間メルカリへのアクセスを止める
                    until = _pause_mercari(con, cfg, lazy.mercari_anomaly)
                    notes.append({"target": "メルカリ", "level": "error",
                                  "message": f"{lazy.mercari_anomaly}。{_jst(until)} まで取得を自動で止めます"})
                    from .notify import alert_once

                    alert_once(cfg, con, "mercari_pause", f"⚠️ メルカリの取得を一時停止しました：{lazy.mercari_anomaly}（{_jst(until)} まで）", every_hours=1)
                    break
            else:
                if collected:
                    db.set_setting(con, "mercari_strikes", 0)
        finally:
            await lazy.close()

    con.commit()
    used = source
    live_ok = any(m and e for _, m, e in collected)
    if source == "sample" or (source == "auto" and not live_ok and not paused):
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
    learned_ship = learned_shipping(con, cfg)
    for t, m_items, e_items in collected:
        if t.genre_label in learned_ship:
            t.shipping_cost_jpy = learned_ship[t.genre_label]
        mercari_count += len(m_items)
        ebay_count += len(e_items)
        cands, st = rank_target(cfg=cfg, target=t, mercari_items=m_items, ebay_items=e_items, con=con, plan=plan,
                                active_count=actives.get(t.id))
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
        "assumptions_from_sheet": from_sheet,
        "learned_shipping": learned_ship,
    }
    cur = con.execute(
        "INSERT INTO scans(started_at, finished_at, data_source, summary_json) VALUES(?, ?, ?, ?)",
        (started, finished, used, json.dumps(summary, ensure_ascii=False)),
    )
    scan_id = int(cur.lastrowid)
    con.executemany("INSERT OR IGNORE INTO scan_targets(scan_id, target_id) VALUES(?, ?)", [(scan_id, t.id) for t, _, _ in collected])
    con.executemany(
        "INSERT INTO candidates(scan_id, target_id, item_key, profit_jpy, roi, payload_json) VALUES(?, ?, ?, ?, ?, ?)",
        [
            (scan_id, c.est.target_id, c.item_key, c.est.profit_jpy, c.est.roi, json.dumps(c.to_dict(), ensure_ascii=False))
            for c in ranked
        ],
    )
    con.commit()
    # 通知（基準を満たした新着・マスターのヒット）
    from .notify import notify_scan

    summary["notify"] = notify_scan(cfg, con, [c.to_dict() for c in ranked], data_source=used)
    con.execute("UPDATE scans SET summary_json=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), scan_id))
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


# ---------------------------------------------------------------- URLを貼って判定
import re as _re

_ITEM_ID_RE = _re.compile(r"(?:/item/|/shops/product/)([A-Za-z0-9]+)")


def parse_mercari_item_html(html: str, url: str) -> Optional[MercariListing]:
    """メルカリ商品ページ1件から、タイトル・価格・状態・画像を読む。"""
    t = _re.search(r'<meta property="og:title" content="([^"]*)"', html)
    p = _re.search(r'data-testid="price"[^>]*>.*?<span>([0-9,]+)</span>', html, _re.S) or _re.search(r'"price"\s*:\s*"?([0-9]+)', html)
    if not t or not p:
        return None
    img = _re.search(r'<meta property="og:image" content="([^"]*)"', html)
    text = _re.sub(r"<[^>]+>", " ", html)
    cond = None
    m = _re.search(r"商品の状態\s+(\S+)", text)
    if m:
        cond = next((k for k, v in CONDITION_LABELS.items() if v[:4] in m.group(1) or m.group(1)[:4] in v), None)
        cond = cond or {"新品、未使用": 1, "未使用に近い": 2, "目立った傷や汚れなし": 3, "やや傷や汚れあり": 4, "傷や汚れあり": 5, "全体的に状態が悪い": 6}.get(m.group(1))
    sold = "売り切れ" in text[:20000] and "購入手続きへ" not in text
    mid = _ITEM_ID_RE.search(url)
    return MercariListing(
        title=_re.sub(r"\s*by メルカリ\s*$", "", t.group(1)).strip()[:200],
        price_jpy=int(p.group(1).replace(",", "")),
        shipping_included="送料込み" in text[:200000],
        status="sold" if sold else "on_sale",
        url=url.split("?")[0],
        item_id=mid.group(1) if mid else url,
        image_url=img.group(1) if img else None,
        condition_id=cond,
        is_shop="/shops/product/" in url,
    )


def _best_target(cfg: Config, con, title: str) -> tuple[Optional[Target], float]:
    """タイトルに一番近いジャンル（ジャンルのeBay検索語・名前との照合）。"""
    from .matching import match_score

    best, score = None, 0.0
    for t in [*active_genres(con, cfg), *master_targets(con)]:
        sc = max(match_score(title, t.ebay_query), match_score(title, t.name))
        if sc > score:
            best, score = t, sc
    return best, score


async def evaluate_url(cfg: Config, url: str, *, target_id: Optional[str] = None) -> dict[str, Any]:
    """メルカリの商品URLを1件読んで、相場・貢献利益・判定を返す（候補としても保存し、キュー・マスター登録に使える）。"""
    if not _ITEM_ID_RE.search(url or ""):
        return {"ok": False, "message": "メルカリの商品URL（https://jp.mercari.com/item/... または /shops/product/...）を貼ってください"}
    fx_mod.apply(cfg)
    plan = plan_mod.load(cfg)
    plan_mod.apply_assumptions(cfg, plan)
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    lazy = LazySession(cfg)
    try:
        session = await lazy.get()
        html, status = await fetch_html_status(session.page, url.split("?")[0])
        it = parse_mercari_item_html(html, url)
        if not it:
            return {"ok": False, "message": f"商品ページを読めませんでした（HTTP {status}）。URLを確認してください"}
        targets = {t.id: t for t in [*active_genres(con, cfg), *master_targets(con)]}
        target = targets.get(target_id or "")
        fit = 1.0
        if target is None:
            target, fit = _best_target(cfg, con, it.title)
        if target is None or fit < 0.3:
            return {"ok": False, "need_target": True, "item": asdict(it),
                    "message": "どのジャンルの商品か判定できませんでした。ジャンルを選んで再判定してください"}
        # 相場：そのジャンルの成約データ（キャッシュがあれば使う。古くても使い、取得日を表示）
        cached = _cached_ebay(con, target, 24 * 30)
        ebay_items, ebay_at, note, active_count = [], None, None, None
        if cached:
            ebay_at, ebay_items, active_count = cached
        else:
            html_e, _ = await fetch_html_status(session.page, build_ebay_sold_url(target.ebay_query))
            ebay_items = parse_ebay_sold_html(html_e, cfg.fx.jpy_per_usd)
            note = detect_block("ebay", html_e, session.page.url) if not ebay_items else None
        th = cfg.thresholds
        comps = Learned(con, target.id).filter(it.title, find_comps(it.title, ebay_items, title_of=lambda e: e.title, threshold=th.match_threshold))
        comps, comp_note = select_comps(comps, it.condition_id, th.min_comps)
        market = market_price([c.item.total_usd for c in comps], min_samples=1)
        dem = demand(ebay_items, active_count)
        plan_sell60 = plan_mod.category_row(plan or {}, target.category).get("60日以内販売率")
        reasons: list[str] = []
        if it.status != "on_sale":
            reasons.append("売り切れています")
        if _is_excluded(cfg, target, it.title):
            reasons.append("除外語に当たっています（付属品・ジャンク等）")
        if market is None:
            return {"ok": True, "item": asdict(it), "target": target.name, "verdict": "相場なし",
                    "message": note or f"「{target.name}」のeBay成約データに、この商品と一致するものがありません（照合しきい値 {th.match_threshold}）",
                    "reasons": reasons}
        est = estimate_profit(cfg=cfg, target=target, buy_price_jpy=it.price_jpy, sell_total_usd=market,
                              mercari_title=it.title, mercari_url=it.url)
        cat_min = plan_mod.category_min_profit(plan or {}, target.category)
        min_profit = target.min_profit_jpy if target.min_profit_jpy is not None else cat_min if cat_min is not None else th.min_profit_jpy
        if len(comps) < th.min_comps:
            reasons.append(f"一致する成約が{len(comps)}件だけで、相場の信頼性が低い")
        if est.profit_jpy < min_profit:
            reasons.append(f"貢献利益 ¥{est.profit_jpy:,} が下限 ¥{min_profit:,} 未満")
        if est.roi < th.min_roi:
            reasons.append(f"仕入ROI {est.roi:.0%} が下限 {th.min_roi:.0%} 未満")
        if th.min_price_ratio and it.price_jpy < market * cfg.fx.jpy_per_usd * th.min_price_ratio:
            reasons.append("相場に対して安すぎます（付属品・別物の疑い）")
        cap = max_buy_for(cfg, target)
        if cap and it.price_jpy > cap:
            reasons.append(f"仕入れ上限 ¥{cap:,} を超えています")
        cand = Candidate(
            est=est, genre=target.genre_label, category=target.category, min_profit_jpy=min_profit,
            item_key=it.item_id or it.url, market_usd=market, match_score=comps[0].score,
            comps=[{"title": c.item.title, "total_usd": c.item.total_usd, "url": c.item.url, "score": c.score} for c in comps[:5]],
            comps_count=len(comps), shipping_included=it.shipping_included, first_seen=db.utc_now_iso(), is_new=False,
            image_url=it.image_url, ebay_sold_search_url=build_ebay_sold_url(target.ebay_query),
            ebay_research_url=build_ebay_research_url(target.ebay_query), mercari_similar_url=build_mercari_keyword_url(target.mercari_query),
            near_miss=bool(reasons), condition=CONDITION_LABELS.get(it.condition_id) if it.condition_id else None,
            is_shop=it.is_shop, demand_count=len(ebay_items), is_master=target.is_master,
            demand=dem, comp_note=comp_note, plan_sell60=plan_sell60, limit_price_jpy=max(est.breakeven_buy_jpy - min_profit, 0),
        )
        if dem.get("sell60") is not None and plan_sell60 and dem["sell60"] < plan_sell60:
            reasons.append(f"60日以内に売れる見込み {dem['sell60']:.0%} が計画 {plan_sell60:.0%} を下回る（回転リスク）")
            cand.near_miss = True
        d = cand.to_dict()
        # 「キューへ」「マスター登録」が使えるよう、手動判定のスキャンとして保存
        now = db.utc_now_iso()
        cur = con.execute("INSERT INTO scans(started_at, finished_at, data_source, summary_json) VALUES(?, ?, 'manual', ?)",
                          (now, now, json.dumps({"url": url}, ensure_ascii=False)))
        con.execute("INSERT INTO candidates(scan_id, target_id, item_key, profit_jpy, roi, payload_json) VALUES(?, ?, ?, ?, ?, ?)",
                    (int(cur.lastrowid), target.id, cand.item_key, est.profit_jpy, est.roi, json.dumps(d, ensure_ascii=False)))
        con.commit()
        return {"ok": True, "target": target.name, "fit": round(fit, 2), "ebay_at": ebay_at, "candidate": d,
                "verdict": "仕入れ候補" if not reasons else ("条件付き" if est.profit_jpy > 0 else "見送り"),
                "reasons": reasons}
    finally:
        await lazy.close()
        con.close()
