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
    p.add_argument("--sort", choices=["roi", "profit", "score"], default="roi", help="並び順（新着は常に上位）")


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

    doc = sub.add_parser("doctor", help="設定・Chromeプロファイルロック等を診断")
    doc.add_argument("--config", default="./config.yaml")
    return p


def _source(args) -> Optional[str]:
    return "sample" if getattr(args, "sample", False) else args.source


async def run_once(cfg_path: str, *, source: Optional[str] = None, sort_by: str = "roi") -> int:
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


def main() -> int:
    args = build_parser().parse_args()
    cfg_path: str = args.config

    if args.cmd == "doctor":
        return run_doctor(cfg_path)

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
