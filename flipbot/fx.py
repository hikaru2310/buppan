"""USD/JPY の自動取得（無料API・6時間キャッシュ）。取れなければ config の値を使う。"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

from .config import Config

CACHE = Path("./data/fx_cache.json")
TTL_SEC = 6 * 3600
SOURCES = [
    ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["JPY"]),
    ("https://api.frankfurter.dev/v1/latest?base=USD&symbols=JPY", lambda d: d["rates"]["JPY"]),
]


def _fetch() -> Optional[float]:
    for url, pick in SOURCES:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                rate = float(pick(json.load(r)))
            if 50 < rate < 400:
                return rate
        except Exception:  # noqa: BLE001 - 次のソースへ
            continue
    return None


def current_rate(cfg: Config) -> tuple[float, str, Optional[str]]:
    """(レート, 出所, 取得時刻ISO) を返す。"""
    if not cfg.fx.auto:
        return cfg.fx.jpy_per_usd, "設定値", None
    try:
        cached = json.loads(CACHE.read_text())
        if time.time() - cached["ts"] < TTL_SEC:
            return cached["rate"], "自動（キャッシュ）", cached["iso"]
    except Exception:  # noqa: BLE001
        cached = None
    rate = _fetch()
    if rate is not None:
        iso = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"rate": rate, "ts": time.time(), "iso": iso}))
        return rate, "自動", iso
    if cached:
        return cached["rate"], "自動（前回値）", cached["iso"]
    return cfg.fx.jpy_per_usd, "設定値（自動取得失敗）", None


def apply(cfg: Config) -> Config:
    """スキャン前に呼ぶ。cfg.fx.jpy_per_usd を最新レートで上書き。"""
    rate, _, _ = current_rate(cfg)
    cfg.fx.jpy_per_usd = round(rate, 2)
    return cfg
