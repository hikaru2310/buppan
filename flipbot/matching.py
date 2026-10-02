"""Mercari 出品 ↔ eBay 成約品の突き合わせ（精度の本丸）。"""
from __future__ import annotations

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

    # 箱あり/本体のみ の食い違いは相場が大きく変わるので減点
    # （"body only" はレンズなしの意味なので箱の有無とは無関係）
    m_box, e_box = "box" in mt, bool({"box", "cib"} & et) and "no box" not in e_norm
    m_only = any(p in m_norm for p in ("unit のみ", "console only", "箱なし", "no box"))
    e_only = any(p in e_norm for p in ("console only", "unit only", "system only", "loose", "no box"))
    if (m_only and e_box) or (m_box and e_only):
        score *= 0.5
    return round(min(score, 1.0), 3)


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
