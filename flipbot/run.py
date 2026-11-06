from __future__ import annotations

import argparse
import asyncio
import time
from typing import Optional

from rich.console import Console

from .config import load_config
from .doctor import run_doctor
from .interactive import login_flow
from .pipeline import render_ranking, scan_all


def _add_scan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default="./config.yaml")
    p.add_argument(
        "--source",
        choices=["live", "sample", "auto"],
        default=None,
        help="live=実サイト / sample=同梱サンプル / auto=実サイト0件ならサンプル（既定: config の app.data_source）",
    )
    p.add_argument("--sample", action="store_true", help="--source sample の省略形")
    p.add_argument("--sort", choices=["roi", "profit", "score"], default="score", help="並び順（新着は常に上位）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flipbot")

    sub = p.add_subparsers(dest="cmd", required=True)
    _add_scan_args(sub.add_parser("once", help="1回だけ収集してランキング表示"))
    _add_scan_args(sub.add_parser("loop", help="定期的に収集してランキング表示（無限ループ）"))

    serve = sub.add_parser("serve", help="Web画面（候補一覧・収益機会マップ・資本シミュレーター・仕入れキュー）を起動")
    _add_scan_args(serve)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-initial-scan", action="store_true", help="起動時にスキャンしない（前回結果を表示）")

    login = sub.add_parser("login", help="手動でログイン/人間確認してプロファイル保存")
    login.add_argument("--config", default="./config.yaml")

    val = sub.add_parser("value", help="ジャンルの監視価値（1日あたりの期待利益）を一覧表示（DBを読むだけ）")
    val.add_argument("--config", default="./config.yaml")
    val.add_argument("--days", type=float, default=3, help="何日分の取得結果で計算するか")
    val.add_argument("--csv", default=None, help="結果をCSVにも保存するパス")

    dg = sub.add_parser("diagnose", help="仕入れ候補の件数と、どこで落ちているかを診断（DBを読むだけ。サイトにはアクセスしない）")
    dg.add_argument("--config", default="./config.yaml")
    dg.add_argument("--days", type=float, default=3, help="何日分の取得結果で数えるか")
    dg.add_argument("--goal", type=int, default=50, help="目標の候補件数")
    dg.add_argument("--json", default=None, help="結果をJSONにも保存するパス")

    doc = sub.add_parser("doctor", help="設定・Chromeプロファイルロック等を診断")
    doc.add_argument("--config", default="./config.yaml")
    return p


def _source(args) -> Optional[str]:
    return "sample" if getattr(args, "sample", False) else args.source


async def run_once(cfg_path: str, *, source: Optional[str] = None, sort_by: str = "score") -> int:
    cfg = load_config(cfg_path)
    console = Console()

    if not cfg.all_targets and (source or cfg.app.data_source) != "sample":
        console.print("[red]config.yaml に genres / targets がありません（サンプルで試すなら --sample）[/red]")
        return 2

    res = await scan_all(cfg, data_source=source, sort_by=sort_by, console=console)
    for n in res["notes"]:
        color = {"error": "red", "warn": "yellow"}.get(n["level"], "cyan")
        console.print(f"[{color}]{n['target']}: {n['message']}[/{color}]")
    st = res["stats"]
    console.print(
        f"データ: {res['data_source']} / 走査 Mercari {res['mercari_count']}件・eBay {res['ebay_count']}件 → "
        f"候補 {st['passed']}件（除外 {st['excluded']} / 相場なし {st['no_comp']} / 基準未満 {st['below_threshold']}）"
    )
    if res["candidates"]:
        render_ranking(console, res["candidates"])
    return 0


def run_value(cfg_path: str, *, days: float, csv_path: Optional[str]) -> int:
    from rich.table import Table

    from . import db
    from .genre_value import genre_values

    cfg = load_config(cfg_path)
    con = db.connect(cfg.app.db_path)
    rows = genre_values(cfg, con, days=days)
    basis = {"sold": "売れた相場", "active": "暫定", None: "相場なし"}
    t = Table(title=f"ジャンルの監視価値（直近{days:g}日）")
    for c in ("ジャンル", "相場", "新着/日", "見た出品", "照合率", "基準を満たす", "平均利益", "価値/日"):
        t.add_column(c, justify="left" if c in ("ジャンル", "相場") else "right")
    for r in rows:
        t.add_row(
            r["name"], basis[r["basis"]],
            "-" if r["inflow_per_day"] is None else f"{r['inflow_per_day']:.0f}",
            str(r["listings"]), f"{r['match_rate']:.0%}", f"{r['passed']}（{r['pass_rate']:.1%}）",
            f"{r['avg_profit_jpy']:,}" if r["passed"] else "-",
            "-" if r["value_per_day_jpy"] is None else f"{r['value_per_day_jpy']:,}",
        )
    Console().print(t)
    if csv_path:
        import csv

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["id"])
            w.writeheader()
            w.writerows(rows)
    return 0


def run_diagnose(cfg_path: str, *, days: float, goal: int, json_path: Optional[str]) -> int:
    """基準（貢献利益・ROI・照合の下限）は変えずに、保存済みの出品とeBay相場で候補を数え直す。"""
    import json

    from rich.table import Table

    from . import db
    from .genre_value import genre_values

    cfg = load_config(cfg_path)
    con = db.connect(cfg.app.db_path)
    db.migrate(con)
    rows = genre_values(cfg, con, days=days)
    con.close()
    urls = {h["url"] for r in rows for h in r["hits"]}
    keys = ("listings", "excluded", "over_budget", "low_demand", "no_comp", "accessory", "below", "passed")
    tot = {k: sum(r[k] for r in rows) for k in keys}
    console = Console()
    basis = {"sold": "売れた相場", "pooled": "同カテゴリ", "active": "暫定", None: "相場なし"}
    t = Table(title=f"仕入れ候補の診断（直近{days:g}日の取得結果・基準は現在のまま）")
    for c in ("ジャンル", "相場", "出品", "除外", "上限超", "需要不足", "相場なし", "付属品疑い", "基準未満", "候補"):
        t.add_column(c, justify="left" if c in ("ジャンル", "相場") else "right")
    for r in rows:
        t.add_row(r["name"], basis[r["basis"]], *(str(r[k]) for k in keys))
    t.add_row("[b]合計[/b]", "", *(f"[b]{tot[k]}[/b]" for k in keys))
    console.print(t)
    n = len(urls)
    console.print(f"[bold]候補 {n}件 / 目標 {goal}件[/bold]（{'達成' if n >= goal else f'あと{goal - n}件'}。同じ出品が複数ジャンルに出た分は1件と数える）")
    no_market = [r["name"] for r in rows if r["basis"] is None]
    if no_market:
        console.print(f"[yellow]eBayの売れた相場がないジャンル（{len(no_market)}）: {'、'.join(no_market)}[/yellow]")
        console.print("  → 「eBay相場の取り込み」画面から、普段のChromeで売れた商品のページを送ると候補になります（人の作業）")
    misses = sorted((m | {"genre": r["name"]} for r in rows for m in r["misses"]), key=lambda m: -m["best_score"])[:15]
    if misses:
        console.print(f"\n相場なしのうち、eBayの成約に近かった出品（照合のしきい値 {cfg.thresholds.match_threshold}）:")
        for m in misses:
            console.print(f"  {m['best_score']:.2f} [{m['genre']}] {m['title']}  ⇔  {m['best_comp']}")
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"candidates": n, "goal": goal, "totals": tot, "genres": rows}, fh, ensure_ascii=False, indent=1)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    cfg_path: str = args.config

    if args.cmd == "doctor":
        return run_doctor(cfg_path)

    if args.cmd == "diagnose":
        return run_diagnose(cfg_path, days=args.days, goal=args.goal, json_path=args.json)

    if args.cmd == "value":
        return run_value(cfg_path, days=args.days, csv_path=args.csv)

    if args.cmd == "login":
        cfg = load_config(cfg_path)
        return asyncio.run(login_flow(cfg))

    if args.cmd == "once":
        return asyncio.run(run_once(cfg_path, source=_source(args), sort_by=args.sort))

    if args.cmd == "serve":
        from .web import serve

        return serve(
            cfg_path,
            source=_source(args),
            port=args.port,
            initial_scan=not args.no_initial_scan,
        )

    if args.cmd == "loop":
        cfg = load_config(cfg_path)
        interval = max(60, int(cfg.app.poll_interval_seconds))
        console = Console()
        while True:
            try:
                rc = asyncio.run(run_once(cfg_path, source=_source(args), sort_by=args.sort))
                if rc != 0:
                    console.print(f"[yellow]run_once exit={rc}[/yellow]")
            except KeyboardInterrupt:
                console.print("\n[bold]stopped[/bold]")
                return 0
            except Exception as e:
                console.print(f"[red]error: {e!r}[/red]")
            time.sleep(interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
