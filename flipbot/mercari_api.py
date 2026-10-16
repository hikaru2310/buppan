"""メルカリの検索をブラウザなしで取得する（内部検索API・DPoP署名）。

0001_sedori（リサーチ用ツール）で検証した方式を移植:
  POST https://api.mercari.jp/v2/entities:search に、使い捨てES256鍵で署名したDPoPヘッダを付ける。
  ブラウザ版より速く（1ジャンル1秒前後）、商品状態・出品日時・送料負担・ショップ出品かどうかも取れる。

注意:
  - 非公式の取得方法で、規約・仕様変更・IPブロックのリスクがある（外部環境ページ参照）。
  - 低頻度・ランダム間隔でアクセスする。壊れたら app.mercari_source: browser に切り替える。
"""
from __future__ import annotations

import base64
import json
import random
import time
import uuid
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .scrape_mercari import MercariListing

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

# メルカリの商品状態ID
CONDITIONS = {1: "新品、未使用", 2: "未使用に近い", 3: "目立った傷や汚れなし", 4: "やや傷や汚れあり", 5: "傷や汚れあり", 6: "全体的に状態が悪い"}

_last_call = 0.0


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _dpop(url: str, method: str = "POST") -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    n = key.public_key().public_numbers()
    i2b = lambda x: x.to_bytes(32, "big")  # noqa: E731
    jwk = {"crv": "P-256", "kty": "EC", "x": _b64u(i2b(n.x)), "y": _b64u(i2b(n.y))}
    hdr = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    pl = {"iat": int(time.time()), "jti": str(uuid.uuid4()), "htu": url, "htm": method, "uuid": str(uuid.uuid4())}
    signing = (_b64u(json.dumps(hdr, separators=(",", ":")).encode()) + "." + _b64u(json.dumps(pl, separators=(",", ":")).encode())).encode()
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    return signing.decode() + "." + _b64u(i2b(r) + i2b(s))


def _throttle(min_sec: float, max_sec: float) -> None:
    """連続アクセスの間にランダムな間隔を入れる（礼儀・ブロック回避）。"""
    global _last_call
    wait = random.uniform(min_sec, max_sec)
    elapsed = time.time() - _last_call
    if elapsed < wait:
        time.sleep(wait - elapsed)
    _last_call = time.time()


def search(
    keyword: str,
    *,
    max_price_jpy: Optional[int] = None,
    exclude_keyword: Optional[str] = None,
    condition_ids: Optional[list[int]] = None,
    page_size: int = 120,
    newest_first: bool = True,
    min_interval_sec: float = 3.0,
    max_interval_sec: float = 7.0,
) -> list[MercariListing]:
    _throttle(min_interval_sec, max_interval_sec)
    body = {
        "userId": "", "pageSize": page_size, "pageToken": "", "searchSessionId": str(uuid.uuid4()),
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED", "thumbnailTypes": [],
        "searchCondition": {
            "keyword": keyword, "excludeKeyword": exclude_keyword or "",
            # 新着順（新しく出た良品を先に拾う）
            "sort": "SORT_CREATED_TIME" if newest_first else "SORT_SCORE", "order": "ORDER_DESC",
            "status": ["STATUS_ON_SALE"], "sizeId": [], "categoryId": [], "brandId": [], "sellerId": [],
            "priceMin": 0, "priceMax": int(max_price_jpy) if max_price_jpy else 0,
            "itemConditionId": list(condition_ids or []), "shippingPayerId": [], "shippingFromArea": [],
            "shippingMethod": [], "colorId": [], "hasCoupon": False, "attributes": [], "itemTypes": [],
            "skuIds": [], "shopIds": [],
        },
        "defaultDatasets": [], "serviceFrom": "suruga", "withItemBrand": True, "withItemPromotions": True,
        "withItemSizes": True, "useDynamicAttribute": True, "withSuggestedItems": False,
        "withOfferPricePromotion": True, "withProductSuggest": False, "withParentProducts": False,
        "withProductArticles": False, "withSearchConditionId": False,
    }
    headers = {
        "DPoP": _dpop(SEARCH_URL), "X-Platform": "web", "Content-Type": "application/json", "Accept": "*/*",
        "User-Agent": UA, "Origin": "https://jp.mercari.com", "Referer": "https://jp.mercari.com/",
    }
    resp = requests.post(SEARCH_URL, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    out: list[MercariListing] = []
    for it in resp.json().get("items", []):
        try:
            price = int(it.get("price", 0))
        except (TypeError, ValueError):
            continue
        if price <= 0 or (max_price_jpy and price > max_price_jpy):
            continue
        item_id = str(it.get("id", ""))
        thumbs = it.get("thumbnails") or []
        cond = it.get("itemConditionId")
        is_shop = bool(it.get("shop")) or it.get("itemType") == "ITEM_TYPE_BEYOND"
        url = f"https://jp.mercari.com/shops/product/{item_id}" if is_shop else f"https://jp.mercari.com/item/{item_id}"
        out.append(MercariListing(
            title=(it.get("name") or "")[:200],
            price_jpy=price,
            # shippingPayerId: 2 = 送料込み（出品者負担）
            shipping_included=str(it.get("shippingPayerId", "2")) != "1",
            status="on_sale" if it.get("status", "").endswith("ON_SALE") else "sold",
            url=url,
            item_id=item_id,
            image_url=thumbs[0] if thumbs else None,
            is_auction=bool(it.get("auction")),
            condition_id=int(cond) if cond not in (None, "") else None,
            listed_at=_ts(it.get("created")),
            is_shop=is_shop,
        ))
    return out


def _ts(v) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(int(v)))
    except (TypeError, ValueError):
        return None
