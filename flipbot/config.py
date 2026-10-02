from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, Field


class MercariFees(BaseModel):
    buyer_fee_jpy: int = 0
    # 購入側の手数料率（原則0）
    buyer_fee_rate: float = 0.0


class EbayFees(BaseModel):
    final_value_fee_rate: float = 0.1325
    payment_fixed_fee_usd: float = 0.30
    # Payoneer / Wise の USD→JPY 換金スプレッド
    payout_fx_spread_rate: float = 0.02


class Fees(BaseModel):
    mercari: MercariFees = Field(default_factory=MercariFees)
    ebay: EbayFees = Field(default_factory=EbayFees)


class Fx(BaseModel):
    jpy_per_usd: float = 150.0


class Target(BaseModel):
    """ジャンル（＝監視対象）。旧 targets 形式とも互換。"""

    id: str
    name: str
    mercari_query: str
    ebay_query: str
    # 表示用のジャンル名（例: "ゲーム機・レトロゲーム"）。未指定なら name。
    genre: Optional[str] = None
    # メルカリ側タイトルに必須の語（どれか1つでも含めば可）。空なら制限なし。
    include_keywords: list[str] = Field(default_factory=list)
    # これを含むメルカリ出品は除外（ジャンク等）。グローバル除外語に追加される。
    exclude_keywords: list[str] = Field(default_factory=list)
    # 候補に載せる下限（未指定ならグローバル thresholds を使用）
    min_profit_jpy: Optional[int] = None
    min_roi: Optional[float] = None
    extra_buy_cost_jpy: int = 0  # 梱包・雑費
    shipping_cost_jpy: int = 0  # 国際送料
    # ジャンル別の eBay FVF 上書き
    final_value_fee_rate: Optional[float] = None

    @property
    def genre_label(self) -> str:
        return self.genre or self.name


class Thresholds(BaseModel):
    min_profit_jpy: int = 2000
    min_roi: float = 0.20
    # Mercari品 ↔ eBay成約品 のマッチスコア下限（0〜1）
    match_threshold: float = 0.55
    # 相場算出に必要なマッチ件数
    min_comps: int = 2


class AppConfig(BaseModel):
    db_path: str = "./data/flips.db"
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
    # If False, do not override the browser UA (recommended when using a real Chrome profile).
    use_config_user_agent: bool = True
    # Use installed Google Chrome instead of bundled Chromium ("chrome", "chrome-beta", etc.).
    browser_channel: Optional[str] = None
    # Strip Playwright's --enable-automation (reduces bot walls on some sites).
    strip_playwright_automation_args: bool = True
    headless: bool = True
    slow_mo_ms: int = 0
    # If set, Playwright uses a persistent browser profile (often reduces bot blocks).
    # Example: "./data/chrome-profile"
    user_data_dir: Optional[str] = None
    # For Google Chrome user data dirs, pick which profile to use (e.g. "Profile 33").
    chrome_profile_directory: Optional[str] = None
    # If set (e.g. "http://127.0.0.1:9222"), attach to a running Chrome with --remote-debugging-port.
    # Use this when Chrome must stay open (avoids profile Singleton lock).
    chrome_cdp_url: Optional[str] = None
    # 旧設定（互換用）。thresholds.min_comps が優先。
    ebay_min_median_samples: int = 3
    poll_interval_seconds: int = 900
    # live: 実サイト収集 / sample: 同梱サンプル / auto: 実サイトが0件ならサンプルで代替
    data_source: str = "live"
    # Web画面
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    # 何時間以内に初めて見た出品を NEW とするか
    new_window_hours: int = 24


class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    # genres と targets は同じもの。両方書いた場合は連結。
    genres: list[Target] = Field(default_factory=list)
    targets: list[Target] = Field(default_factory=list)
    fx: Fx = Field(default_factory=Fx)
    fees: Fees = Field(default_factory=Fees)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # 全ジャンル共通の除外語
    exclude_keywords: list[str] = Field(
        default_factory=lambda: ["ジャンク", "部品取り", "動作未確認", "難あり", "空箱", "箱のみ"]
    )

    @property
    def all_targets(self) -> list[Target]:
        return [*self.genres, *self.targets]


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Union[str, Path]) -> Config:
    """config.yaml（GitHubで共有）に、同じ場所の config.local.yaml（このPC専用・非共有）を上書きして読む。"""
    p = Path(path)
    data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    local = p.with_name("config.local.yaml")
    if local.exists():
        data = _deep_merge(data, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return Config.model_validate(data)
