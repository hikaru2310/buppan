"""Mercari 出品 ↔ eBay 成約品の突き合わせ（精度の本丸）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median
from typing import Generic, Optional, Sequence, TypeVar

from .normalize import model_numbers, normalize, tokens

T = TypeVar("T")


@dataclass(frozen=True)
class Comp(Generic[T]):
    item: T
    score: float


def match_score(mercari_title: str, ebay_title: str) -> float:
    """0〜1。型番一致で加点、型番不一致は強く減点。"""
    m_norm, e_norm = normalize(mercari_title), normalize(ebay_title)
    # 日本語の残りかすは eBay 側と一致しようがないので英数字トークンだけで比較
    mt = {t for t in tokens(m_norm) if t.isascii()}
    et = tokens(e_norm)
    if not mt or not et:
        return 0.0
    common = mt & et
    containment = len(common) / len(mt)
    jaccard = len(common) / len(mt | et)
    score = 0.75 * containment + 0.25 * jaccard

    mm, em = model_numbers(m_norm), model_numbers(e_norm)
    if mm and em:
        if _models_overlap(mm, em):
            score += 0.3
        else:
            score *= 0.3
    elif mm and not em:
        score *= 0.85

    # レンズの焦点距離・F値が違えば別商品（50mm F1.4 と F1.8 は相場が倍違う）
    for rx, factor in ((_FOCAL_RE, 0.2), (_APERTURE_RE, 0.25)):
        a, b = set(rx.findall(m_norm)), set(rx.findall(e_norm))
        if a and b and not (a & b):
            score *= factor
    # 型式の末尾違い（GR III ↔ GR IIIx、Ai ↔ Ai-s）: メルカリ側の語が eBay 側では接尾辞付きでしか出てこない
    #   （数字を含む型番は上の型番判定で扱うのでここでは除外）
    words_m = {t for t in mt if not any(ch.isdigit() for ch in t)}
    words_e = {u for u in et if not any(ch.isdigit() for ch in u)}
    if any(len(t) >= 2 and t not in et and any(u.startswith(t) and 0 < len(u) - len(t) <= 2 for u in words_e) for t in words_m) \
            or any(len(u) >= 2 and u not in mt and any(t.startswith(u) and 0 < len(t) - len(u) <= 2 for t in words_m) for u in words_e):
        score *= 0.4
    # 短い型式（T2/T3, M6/M3 など英字1〜2字+数字1〜2桁）の食い違い
    short_m = {t for t in mt if _SHORT_MODEL_RE.match(t)}
    short_e = {u for u in et if _SHORT_MODEL_RE.match(u)}
    if short_m and not (short_m & et) and any(u[:1] == t[:1] for t in short_m for u in short_e):
        score *= 0.3

    # 箱あり/本体のみ の食い違いは相場が大きく変わるので減点
    # （"body only" はレンズなしの意味なので箱の有無とは無関係）
    m_box, e_box = "box" in mt, bool({"box", "cib"} & et) and "no box" not in e_norm
    m_only = any(p in m_norm for p in ("unit のみ", "console only", "箱なし", "no box"))
    e_only = any(p in e_norm for p in ("console only", "unit only", "system only", "loose", "no box"))
    if (m_only and e_box) or (m_box and e_only):
        score *= 0.5
    return round(min(score, 1.0), 3)


_FOCAL_RE = re.compile(r"(\d{2,3})\s?mm")
_SHORT_MODEL_RE = re.compile(r"^[a-z]{1,2}\d{1,2}$")
_APERTURE_RE = re.compile(r"\bf\s?(\d{1,2}(?:\.\d)?)\b")


def _models_overlap(a: set[str], b: set[str]) -> bool:
    """GWF-A1000 と GWF-A1000-1AJF のような末尾サフィックス違いは同一型番とみなす。"""
    for x in a:
        for y in b:
            if x.replace("-", "") == y.replace("-", ""):
                return True
            # 最後のハイフン区切り（色・地域コード等）以外が一致し、共通部が5文字以上なら同一
            xs, ys = x.split("-"), y.split("-")
            n = min(len(xs), len(ys))
            shared = 0
            while shared < n and xs[shared] == ys[shared]:
                shared += 1
            if shared >= max(n - 1, 1) and len("".join(xs[:shared])) >= 5:
                return True
    return False


def find_comps(
    mercari_title: str,
    ebay_items: Sequence[T],
    *,
    title_of,
    threshold: float,
) -> list[Comp[T]]:
    comps = [Comp(item=it, score=match_score(mercari_title, title_of(it))) for it in ebay_items]
    comps = [c for c in comps if c.score >= threshold]
    comps.sort(key=lambda c: c.score, reverse=True)
    return comps


def market_price(values: Sequence[float], *, min_samples: int) -> Optional[float]:
    vals = [v for v in values if v > 0]
    if len(vals) < min_samples:
        return None
    return float(median(vals))
