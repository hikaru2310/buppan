from __future__ import annotations

import asyncio

from rich.console import Console

from playwright.async_api import Error as PlaywrightError

from .config import Config
from .http import close_session, open_page
from .scrape_ebay import build_ebay_sold_url
from .scrape_mercari import build_mercari_search_url


async def login_flow(cfg: Config) -> int:
    console = Console()
    if not cfg.app.user_data_dir and not cfg.app.chrome_cdp_url:
        console.print(
            "[red]config.yaml の app.user_data_dir か app.chrome_cdp_url のどちらかを設定してください[/red]"
        )
        return 2

    # Force headful for manual verification/login.
    session = await open_page(
        user_agent=cfg.app.user_agent if cfg.app.use_config_user_agent else None,
        headless=False,
        slow_mo_ms=cfg.app.slow_mo_ms,
        user_data_dir=cfg.app.user_data_dir,
        chrome_profile_directory=cfg.app.chrome_profile_directory,
        browser_channel=cfg.app.browser_channel,
        strip_playwright_automation_args=cfg.app.strip_playwright_automation_args,
        chrome_cdp_url=cfg.app.chrome_cdp_url,
    )
    try:
        console.print(
            "[bold]ブラウザが開きます。[/bold]\n"
            "- Mercari と eBay のページで必要な確認（ログイン/人間確認）を済ませてください。\n"
            "- 特に、検索結果ページでブロックされることがあるので、下記の検索URLまで進んでください。\n"
            "- 終わったらこのターミナルで [bold]Ctrl+C[/bold] を押して終了してください。\n"
        )

        if cfg.all_targets:
            t = cfg.all_targets[0]
            mercari_search = build_mercari_search_url(t.mercari_query)
            ebay_sold = build_ebay_sold_url(t.ebay_query)
            console.print(f"- Mercari検索: {mercari_search}")
            console.print(f"- eBay Sold検索: {ebay_sold}")
            await session.page.goto(mercari_search, wait_until="domcontentloaded")
            try:
                await session.page.wait_for_timeout(1500)
            except PlaywrightError:
                await asyncio.sleep(1.5)
            await session.page.goto(ebay_sold, wait_until="domcontentloaded")
        else:
            await session.page.goto("https://jp.mercari.com/", wait_until="domcontentloaded")
            try:
                await session.page.wait_for_timeout(1000)
            except PlaywrightError:
                await asyncio.sleep(1.0)
            await session.page.goto("https://www.ebay.com/", wait_until="domcontentloaded")

        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        await close_session(session)

