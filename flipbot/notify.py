"""通知（0001_sedori の設計：Slack第一・差し替え可能な Notifier）。

- Slack Incoming Webhook に、画像・貢献利益・ROI・リンク付きで送る。
- webhook URL は config.local.yaml の notify.slack_webhook_url に書く（GitHubに上げない）。
- 同じ出品は2回通知しない（notified テーブル）。
- LINE Notify は2025-03-31で終了したため未対応。LINEに送る場合は Messaging API の Notifier を追加する。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Optional

from . import db
from .config import Config


class Notifier:
    name = "none"

    def send(self, text: str, blocks: Optional[list[dict[str, Any]]] = None) -> None:
        print(text)


class SlackNotifier(Notifier):
    name = "slack"

    def __init__(self, webhook_url: str) -> None:
        self.url = webhook_url

    def send(self, text: str, blocks: Optional[list[dict[str, Any]]] = None) -> None:
        body = {"text": text, **({"blocks": blocks} if blocks else {})}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status >= 300:
                raise RuntimeError(f"Slack通知に失敗しました（HTTP {r.status}）")


def get_notifier(cfg: Config) -> Optional[Notifier]:
    if cfg.notify.slack_webhook_url:
        return SlackNotifier(cfg.notify.slack_webhook_url)
    return None


def _blocks(c: dict[str, Any], app_url: str) -> list[dict[str, Any]]:
    head = ("★マスター " if c.get("is_master") else "") + ("NEW " if c.get("is_new") else "")
    text = (f"*{head}<{c['mercari_url']}|{c['mercari_title']}>*\n"
            f"貢献利益 *¥{c['profit_jpy']:,}*（仕入ROI {c['roi']*100:.0f}%）／ 仕入 ¥{c['buy_price_jpy']:,} ／ "
            f"eBay相場 ${c['market_usd']:.0f}（成約{c['comps_count']}件・照合{c['match_score']*100:.0f}）\n"
            f"{c.get('category') or c.get('genre')}{' ・ ' + c['condition'] if c.get('condition') else ''} ・ "
            f"<{c.get('ebay_sold_search_url') or app_url}|eBay相場> ・ <{app_url}|ツールで開く>")
    b: dict[str, Any] = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    if c.get("image_url"):
        b["accessory"] = {"type": "image", "image_url": c["image_url"], "alt_text": "item"}
    return [b]


def notify_scan(cfg: Config, con, candidates: list[dict[str, Any]], *, data_source: str) -> dict[str, Any]:
    """スキャン結果から通知対象を選んで送る。送った件数とエラーを返す。"""
    n = get_notifier(cfg)
    if n is None or data_source == "sample":
        return {"sent": 0, "skipped": "通知先が未設定" if n is None else "サンプルデータのため通知しません"}
    picked = []
    for c in candidates:
        if c.get("near_miss"):
            continue
        hit = (cfg.notify.on_master_hits and c.get("is_master")) or (cfg.notify.on_new_candidates and c.get("is_new"))
        if not hit:
            continue
        if con.execute("SELECT 1 FROM notified WHERE item_key=?", (c["item_key"],)).fetchone():
            continue
        picked.append(c)
    sent, error = 0, None
    for c in picked[: cfg.notify.max_per_scan]:
        try:
            n.send(f"仕入れ候補：{c['mercari_title']}（貢献利益 ¥{c['profit_jpy']:,}）", _blocks(c, cfg.notify.app_url))
            con.execute("INSERT OR IGNORE INTO notified(item_key, notified_at) VALUES(?, ?)", (c["item_key"], db.utc_now_iso()))
            sent += 1
        except Exception as e:  # noqa: BLE001 - 通知失敗でスキャンは止めない
            error = str(e)
            break
    con.commit()
    return {"sent": sent, "pending": max(len(picked) - sent, 0), "error": error}


def send_test(cfg: Config) -> tuple[bool, str]:
    n = get_notifier(cfg)
    if n is None:
        return False, "通知先が未設定です（config.local.yaml に notify.slack_webhook_url を書いてください）"
    try:
        n.send("Arbiter 物販コンソールからのテスト通知です。この通知が届いていれば設定は完了です。")
        return True, "テスト通知を送りました"
    except Exception as e:  # noqa: BLE001
        return False, f"送信に失敗しました: {e}"


def alert_once(cfg: Config, con, key: str, text: str, *, every_hours: float = 24) -> bool:
    """運用アラート（ログイン切れ・取得停止・再確認期限など）。同じ key は every_hours に1回だけ送る。"""
    from datetime import datetime, timedelta, timezone

    row = con.execute("SELECT sent_at FROM alerts_sent WHERE key=?", (key,)).fetchone()
    if row and datetime.fromisoformat(row["sent_at"]) > datetime.now(timezone.utc) - timedelta(hours=every_hours):
        return False
    n = get_notifier(cfg)
    if n is None:
        return False
    try:
        n.send(text)
    except Exception:  # noqa: BLE001 - アラート失敗で本処理は止めない
        return False
    con.execute("INSERT INTO alerts_sent(key, sent_at) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET sent_at=excluded.sent_at",
                (key, db.utc_now_iso()))
    con.commit()
    return True


def check_environment_reviews(cfg: Config, con, env: dict) -> int:
    """外部環境の再確認期限が過ぎた項目を通知（項目ごとに7日に1回）。"""
    from datetime import date

    today = date.today().isoformat()
    n = 0
    for area in (env or {}).get("areas", []):
        for it in area.get("items", []):
            if it.get("review_by") and it["review_by"] <= today:
                n += alert_once(cfg, con, f"env:{it.get('title')}",
                                f"🗓 外部環境の再確認期限が過ぎています：{it.get('title')}（期限 {it['review_by']}）。事実が変わっていないか確認し、environment.yaml を更新してください。",
                                every_hours=24 * 7)
    return n
