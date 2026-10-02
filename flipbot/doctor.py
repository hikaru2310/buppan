from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .config import load_config


def run_doctor(cfg_path: str) -> int:
    console = Console()
    cfg = load_config(cfg_path)
    app = cfg.app

    console.print("[bold]flipbot doctor[/bold]")
    console.print(f"- use_config_user_agent: {app.use_config_user_agent}")
    console.print(f"- browser_channel: {app.browser_channel!r}")
    console.print(f"- strip_playwright_automation_args: {app.strip_playwright_automation_args}")
    console.print(f"- headless: {app.headless}")
    console.print(f"- user_data_dir: {app.user_data_dir!r}")
    console.print(f"- chrome_profile_directory: {app.chrome_profile_directory!r}")

    console.print(f"- chrome_cdp_url: {app.chrome_cdp_url!r}")

    if app.chrome_cdp_url:
        console.print(
            "[bold]CDPモード[/bold]: 次のようにChromeを起動してから `once` を実行してください（例）:\n"
            f'  open -na "Google Chrome" --args --remote-debugging-port=9222 '
            f'--user-data-dir="{Path.home()}/Library/Application Support/Google/Chrome" '
            f'--profile-directory="{app.chrome_profile_directory or "Default"}"\n'
            "その後 `chrome_cdp_url: \"http://127.0.0.1:9222\"` に設定します。"
        )

    ud = app.user_data_dir
    if ud and not app.chrome_cdp_url:
        root = Path(ud).expanduser()
        if not root.exists():
            console.print(f"[red]user_data_dir が存在しません: {root}[/red]")
            return 2
        singleton_lock = root / "SingletonLock"
        singleton_socket = root / "SingletonSocket"
        if singleton_lock.exists() or singleton_socket.exists():
            console.print(
                "[yellow]Chrome のプロファイルがロックされています（多くの場合、Chrome が起動中）。[/yellow]\n"
                "対策: Google Chrome を「完全終了」してから `once` を再実行してください。"
            )
            if not app.chrome_cdp_url:
                prof = app.chrome_profile_directory or "Default"
                udd = Path(app.user_data_dir).expanduser() if app.user_data_dir else Path.home()
                console.print(
                    "[bold]代替案（Chromeを開いたまま）[/bold]: 一度Chromeを終了し、次で起動し直してから "
                    "`chrome_cdp_url: \"http://127.0.0.1:9222\"` を設定してください:\n"
                    f'  open -na "Google Chrome" --args --remote-debugging-port=9222 '
                    f'--user-data-dir="{udd}" '
                    f'--profile-directory="{prof}"'
                )
        else:
            console.print("[green]Singleton ロックは見つかりません（このプロファイルで起動できる見込み）[/green]")

    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome.exists():
        console.print(f"[green]Google Chrome が見つかりました: {chrome}[/green]")
    else:
        console.print("[yellow]/Applications/Google Chrome.app が見つかりません（channel=chrome が使えない可能性）[/yellow]")

    return 0
