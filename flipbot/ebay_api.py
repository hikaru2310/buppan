"""eBay公式API（Buy APIs）。ツールが規約に沿って24時間自動で相場を取るための経路。

- Browse API（誰でも使える）: 出品中の商品の件数と価格 → 競合の数（売れるまでの日数の推定）と価格帯
- Marketplace Insights API（承認制）: 直近90日に売れた商品の価格・日付 → 相場そのもの
認証は client credentials（アプリのキー）。キーは config.local.yaml の ebay_api に書く（GitHubに上げない）。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .scrape_ebay import EbaySold

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
INSIGHTS_URL = "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
SCOPE_BASE = "https://api.ebay.com/oauth/api_scope"
SCOPE_INSIGHTS = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
TOKEN_CACHE = Path("./data/ebay_token.json")


class EbayApiError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def _condition(cid: Any, text: str = "") -> Optional[str]:
    try:
        c = int(cid)
    except (TypeError, ValueError):
        t = (text or "").lower()
        return "parts" if "parts" in t else "new" if "new" in t else "used" if t else None
    if c == 7000:
        return "parts"
    if c in (1000, 1500, 1750):
        return "new"
    return "used"


class EbayApi:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.c = cfg.ebay_api

    @property
    def configured(self) -> bool:
        return bool(self.c.client_id and self.c.client_secret)

    # ---------------------------------------------------------------- 認証
    def token(self, scope: str) -> str:
        cache: dict[str, Any] = {}
        try:
            cache = json.loads(TOKEN_CACHE.read_text())
        except Exception:  # noqa: BLE001
            pass
        hit = cache.get(scope)
        if hit and hit.get("client_id") == self.c.client_id and hit["exp"] > time.time() + 60:
            return hit["token"]
        basic = base64.b64encode(f"{self.c.client_id}:{self.c.client_secret}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, headers={
            "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
        data = self._open(req)
        cache[scope] = {"token": data["access_token"], "exp": time.time() + int(data.get("expires_in", 7200)), "client_id": self.c.client_id}
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps(cache))
        try:
            TOKEN_CACHE.chmod(0o600)
        except OSError:
            pass
        return data["access_token"]

    def _open(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                err = json.load(e)
                detail = (err.get("errors") or [{}])[0].get("message") or err.get("error_description") or err.get("error") or ""
            except Exception:  # noqa: BLE001
                pass
            if e.code in (401, 403) and "insights" in req.full_url:
                raise EbayApiError("Marketplace Insights API の利用が承認されていません（eBayへの利用申請が必要）", e.code) from e
            if e.code == 401 or "invalid_client" in detail:
                raise EbayApiError("eBay APIのキー（App ID / Cert ID）が正しくありません", e.code) from e
            if e.code == 429:
                raise EbayApiError("eBay APIの1日の呼び出し上限に達しました", e.code) from e
            raise EbayApiError(f"eBay APIエラー（HTTP {e.code}）{detail}", e.code) from e
        except urllib.error.URLError as e:
            raise EbayApiError(f"eBay APIに接続できません: {e.reason}") from e

    def _get(self, url: str, params: dict[str, Any], scope: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        req = urllib.request.Request(f"{url}?{q}", headers={
            "Authorization": f"Bearer {self.token(scope)}",
            "X-EBAY-C-MARKETPLACE-ID": self.c.marketplace,
            "Accept": "application/json",
        })
        return self._open(req)

    # ---------------------------------------------------------------- Browse（出品中）
    def active(self, query: str, category_ids: Optional[list[str]] = None) -> dict[str, Any]:
        """出品中（即決）の件数と価格帯。"""
        d = self._get(BROWSE_URL, {"q": query, "limit": 200, "category_ids": ",".join(category_ids or []) or None,
                                   "filter": "buyingOptions:{FIXED_PRICE}"}, SCOPE_BASE)
        items: list[EbaySold] = []
        for it in d.get("itemSummaries") or []:
            try:
                p = float(it["price"]["value"])
                ship = float((it.get("shippingOptions") or [{}])[0].get("shippingCost", {}).get("value") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            items.append(EbaySold(title=(it.get("title") or "")[:200], price_usd=p, shipping_usd=ship, total_usd=p + ship,
                                  url=it.get("itemWebUrl") or "", condition=_condition(it.get("conditionId"), it.get("condition", ""))))
        prices = sorted(i.total_usd for i in items)
        return {"total": int(d.get("total") or len(prices)), "n": len(prices), "items": items,
                "median_usd": prices[len(prices) // 2] if prices else None, "min_usd": prices[0] if prices else None}

    # ---------------------------------------------------------------- Marketplace Insights（売れた商品）
    def sold(self, query: str, category_ids: list[str]) -> tuple[list[EbaySold], int]:
        if not category_ids:
            raise EbayApiError("Marketplace Insights にはeBayのカテゴリIDが必要です（ジャンルの ebay_category_ids）")
        d = self._get(INSIGHTS_URL, {"q": query, "category_ids": ",".join(category_ids), "limit": 200}, SCOPE_INSIGHTS)
        out = []
        for it in d.get("itemSales") or []:
            try:
                price = float((it.get("lastSoldPrice") or it.get("price"))["value"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append(EbaySold(
                title=(it.get("title") or "")[:200], price_usd=price, shipping_usd=None, total_usd=price,
                url=it.get("itemWebUrl") or f"https://www.ebay.com/itm/{it.get('itemId', '')}",
                condition=_condition(it.get("conditionId"), it.get("condition", "")),
                sold_date=(it.get("lastSoldDate") or "")[:10] or None,
            ))
        return out, int(d.get("total") or len(out))


def status(cfg: Config) -> dict[str, Any]:
    """画面用: キーの有無・Browse/Insights が使えるかを実際に1回呼んで確かめる。"""
    api = EbayApi(cfg)
    if not api.configured:
        return {"configured": False, "message": "キー未設定（config.local.yaml の ebay_api に client_id / client_secret）"}
    out: dict[str, Any] = {"configured": True}
    try:
        a = api.active("Nikon F3")
        out["browse"] = {"ok": True, "message": f"Browse API OK（Nikon F3 の出品中 {a['total']}件）"}
    except EbayApiError as e:
        out["browse"] = {"ok": False, "message": str(e)}
    try:
        items, total = api.sold("Nikon F3", ["15230"])
        out["insights"] = {"ok": True, "message": f"Marketplace Insights OK（Nikon F3 の成約 {total}件）"}
    except EbayApiError as e:
        out["insights"] = {"ok": False, "message": str(e)}
    return out
