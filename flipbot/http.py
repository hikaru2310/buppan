from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


@dataclass(frozen=True)
class BrowserSession:
    browser: Optional[Browser]
    context: BrowserContext
    page: Page
    # True when attached via connect_over_cdp (do not close the user's Chrome).
    is_cdp: bool = False


_STEALTH_INIT_SCRIPT = """
// Minimal stealth: reduce obvious automation fingerprints.
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


async def open_page(
    *,
    user_agent: Optional[str],
    headless: bool = True,
    slow_mo_ms: int = 0,
    user_data_dir: Optional[str] = None,
    chrome_profile_directory: Optional[str] = None,
    browser_channel: Optional[str] = None,
    strip_playwright_automation_args: bool = True,
    chrome_cdp_url: Optional[str] = None,
) -> BrowserSession:
    pw = await async_playwright().start()
    ignore_default_args = (
        ["--enable-automation"] if strip_playwright_automation_args else None
    )
    common_launch: dict = {}
    if browser_channel:
        common_launch["channel"] = browser_channel
    if ignore_default_args is not None:
        common_launch["ignore_default_args"] = ignore_default_args

    if chrome_cdp_url:
        # Attach to an already running Chrome started with --remote-debugging-port=...
        try:
            browser = await pw.chromium.connect_over_cdp(chrome_cdp_url)
        except Exception as e:
            await pw.stop()
            raise RuntimeError(
                f"CDP接続に失敗しました: {chrome_cdp_url!r}。"
                "Chromeを `--remote-debugging-port=9222` 付きで起動しているか確認してください。"
            ) from e
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        browser._pw = pw  # type: ignore[attr-defined]
        try:
            await context.add_init_script(_STEALTH_INIT_SCRIPT)
        except Exception:
            pass
        return BrowserSession(browser=browser, context=context, page=page, is_cdp=True)

    if user_data_dir:
        args = ["--disable-blink-features=AutomationControlled"]
        if chrome_profile_directory:
            args.append(f"--profile-directory={chrome_profile_directory}")
        # Persistent profile (keeps cookies/localStorage) — often improves access.
        ctx_kwargs = dict(
            user_data_dir=user_data_dir,
            headless=headless,
            slow_mo=slow_mo_ms if slow_mo_ms > 0 else None,
            args=args,
            locale="ja-JP",
            viewport={"width": 1280, "height": 800},
        )
        ctx_kwargs.update(common_launch)
        if user_agent:
            ctx_kwargs["user_agent"] = user_agent
        try:
            context = await pw.chromium.launch_persistent_context(**ctx_kwargs)
        except Exception as e:
            await pw.stop()
            msg = str(e)
            if "ProcessSingleton" in msg or "Singleton" in msg:
                raise RuntimeError(
                    "Chromeのユーザーデータがロックされています（Chromeが起動中の可能性が高い）。"
                    "Google Chromeを完全終了してから再実行するか、"
                    "`app.chrome_cdp_url` で既存ChromeにCDP接続してください。"
                ) from e
            if "Target page" in msg or "has been closed" in msg:
                raise RuntimeError(
                    "ブラウザがすぐ終了しました（既存Chromeセッションとの衝突、またはプロファイル競合の可能性）。"
                    "Chromeを完全終了してから再試行するか、`chrome_cdp_url` を利用してください。"
                ) from e
            raise
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        page = context.pages[0] if context.pages else await context.new_page()

        # There is no separate Browser object for persistent contexts.
        browser = None
    else:
        launch_kwargs = dict(
            headless=headless,
            slow_mo=slow_mo_ms if slow_mo_ms > 0 else None,
            args=["--disable-blink-features=AutomationControlled"],
        )
        launch_kwargs.update(common_launch)
        browser = await pw.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict = dict(locale="ja-JP", viewport={"width": 1280, "height": 800})
        if user_agent:
            ctx_kwargs["user_agent"] = user_agent
        context = await browser.new_context(**ctx_kwargs)
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        page = await context.new_page()

    # The playwright driver is tied to the browser lifetime.
    # We attach it to the browser object so closing browser closes everything.
    if browser is not None:
        browser._pw = pw  # type: ignore[attr-defined]
    else:
        context._pw = pw  # type: ignore[attr-defined]
    return BrowserSession(browser=browser, context=context, page=page, is_cdp=False)


async def close_session(session: BrowserSession) -> None:
    pw = (
        getattr(session.browser, "_pw", None)
        if session.browser is not None
        else getattr(session.context, "_pw", None)
    )
    if session.is_cdp:
        if session.browser is not None:
            await session.browser.close()
        if pw is not None:
            await pw.stop()
        return

    await session.context.close()
    if session.browser is not None:
        await session.browser.close()
    if pw is not None:
        await pw.stop()

