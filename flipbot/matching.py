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
        # eBay 側は数字だけの型番（IXY 510）: メルカリの型番（50S）の数字と重ならなければ別機種
        be_ = _bare_numbers(e_norm)
        score *= 0.3 if be_ and not any(re.sub(r"\D", "", m) in be_ for m in mm) else 0.85
    elif (em or _bare_numbers(e_norm)) and not mm and not _bare_numbers(m_norm):
        # eBay は型番付きなのにメルカリ側は型番なし（「IXY DIGITAL 本体」）→ どの機種か分からない
        score *= 0.6

    # レンズの焦点距離・F値が違えば別商品（50mm F1.4 と F1.8 は相場が倍違う）
    for get, factor in ((_focals, 0.2), (lambda n: set(_APERTURE_RE.findall(n)), 0.25)):
        a, b = get(m_norm), get(e_norm)
        if a and b and not (a & b):
            score *= factor
    # 型式の末尾違い（GR III ↔ GR IIIx、Ai ↔ Ai-s）: メルカリ側の語が eBay 側では接尾辞付きでしか出てこない
    #   （数字を含む型番は上の型番判定で扱うのでここでは除外）
    words_m = {t for t in mt if not any(ch.isdigit() for ch in t)}
    words_e = {u for u in et if not any(ch.isdigit() for ch in u)}
    if any(len(t) >= 2 and t not in et and any(u.startswith(t) and 0 < len(u) - len(t) <= 2 for u in words_e) for t in words_m) \
            or any(len(u) >= 2 and u not in mt and any(t.startswith(u) and 0 < len(t) - len(u) <= 2 for t in words_m) for u in words_e):
        score *= 0.4
    # 「GR DIGITAL」↔「GR」、「IXY DIGITAL」↔「IXY」は別シリーズ
    for a, b in _SERIES_PAIRS:
        if (a in mt and b in et and a not in et) or (a in et and b in mt and a not in mt):
            score *= 0.3
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

    # 種類の食い違い：付属品 ↔ 本体、レンズ単体 ↔ カメラ（本体・セット）は相場が桁違い
    mk, ek = item_kind(m_norm), item_kind(e_norm)
    if (mk == "accessory") != (ek == "accessory"):
        score *= 0.1
    elif mk == ek == "accessory" and not (_accessory_types(m_norm) & _accessory_types(e_norm)):
        score *= 0.2  # 付属品どうしでも種類違い（ストラップ ↔ アダプター）
    elif {mk, ek} in ({"lens", "body"}, {"lens", "kit"}):
        score *= 0.15
    # ゲーム機：eBay が本体なのに、メルカリ側に本体の語がない（ソフト等）
    #   機種名（gameboy advance 等）しか重ならないなら別物とみなす（ポケモン サファイア GBA ↔ GBA本体）
    if ek == "console" and mk not in ("console", "body", "kit") and not (common - _PLATFORM_WORDS):
        score *= 0.5
    # 世代違い（RX100 ↔ RX100 III、GR DIGITAL ↔ GR DIGITAL II、RZ67 PRO ↔ PRO II）
    if _generation(mt) != _generation(et):
        score *= 0.35
    # 型番の数字違い（IXY DIGITAL 70 ↔ 920）：両方に2〜3桁の数字があって1つも重ならない
    bm, be = _bare_numbers(m_norm), _bare_numbers(e_norm)
    if bm and be and not (bm & be):
        score *= 0.3
    return round(min(score, 1.0), 3)


# 付属品の語（本体・レンズの説明がなく、これがあれば付属品とみなす）
_ACCESSORY_WORDS = (
    "ファインダー", "フード", "ホルダー", "ストラップ", "バッテリー", "充電器", "チャージャー", "ハウジング", "ケース",
    "キャップ", "アダプター", "アダプタ", "コンバーター", "コンバータ", "グリップ", "フィルター", "留め金", "ラグ",
    "三脚", "リモコン", "ケーブル", "付属品", "マウント", "接眼", "アイカップ", "フラッシュ", "ストロボ",
)
_ACCESSORY_TOKENS = {
    "finder", "viewfinder", "hood", "holder", "strap", "battery", "batteries", "charger", "housing", "case", "cap", "caps",
    "adapter", "adaptor", "converter", "teleconverter", "grip", "filter", "lug", "tripod", "remote", "cable", "eyecup",
    "flash", "strobe", "back",
}
# 付属品の種類（日本語 → 英語の代表語）。付属品どうしは同じ種類のときだけ比べる
_ACCESSORY_TYPES = {
    "ファインダー": "finder", "viewfinder": "finder", "finder": "finder", "フード": "hood", "hood": "hood",
    "ホルダー": "holder", "holder": "holder", "back": "holder", "ストラップ": "strap", "strap": "strap", "ラグ": "strap", "lug": "strap",
    "留め金": "strap", "バッテリー": "battery", "battery": "battery", "batteries": "battery", "充電器": "charger",
    "チャージャー": "charger", "charger": "charger", "ハウジング": "housing", "housing": "housing", "ケース": "case", "case": "case",
    "キャップ": "cap", "cap": "cap", "caps": "cap", "アダプター": "adapter", "アダプタ": "adapter", "adapter": "adapter",
    "adaptor": "adapter", "マウント": "adapter", "コンバーター": "converter", "コンバータ": "converter", "converter": "converter",
    "teleconverter": "converter", "グリップ": "grip", "grip": "grip", "フィルター": "filter", "filter": "filter",
    "三脚": "tripod", "tripod": "tripod", "リモコン": "remote", "remote": "remote", "ケーブル": "cable", "cable": "cable",
    "フラッシュ": "flash", "ストロボ": "flash", "flash": "flash", "strobe": "flash", "アイカップ": "eyecup", "eyecup": "eyecup",
}
_SERIES_PAIRS = (("grdigital", "gr"), ("ixydigital", "ixy"))
_PLATFORM_WORDS = {"nintendo", "gameboy", "advance", "color", "pocket", "light", "sp", "switch", "playstation", "ps", "3ds", "ds",
                   "xl", "lite", "famicom", "super", "64", "sega", "vita", "psp", "new", "console", "system", "japan"}
_BODY_WORDS = ("body", "camera", "unit", "console", "system", "mirrorless", "slr", "dslr", "rangefinder", "ミラーレス", "カメラ")
_CONSOLE_WORDS = ("console", "unit", "system", "handheld")
_LENS_SERIES = {"lens", "nikkor", "takumar", "rokkor", "zuiko", "sekor", "sonnar", "planar", "biogon", "distagon", "tessar",
                "summicron", "summilux", "elmar", "hexanon", "serenar", "fujinon"}
_SPEC_RE = re.compile(r"\b\d(?:\.\d)?/\d{2,3}(?!\d)|\b\d{2,3}/\d(?:\.\d)?(?![\d.])")  # 「4.5/180」「28/2.8」表記
_GEN_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}
_GEN_RE = re.compile(r"^(?:m|mk|mark)([2-7])$")
_BARE_RE = re.compile(r"(?<![\w.])(\d{2,3})(?![\w.])")


def item_kind(norm: str) -> Optional[str]:
    """正規化済みタイトルの種類: accessory / lens / body / kit（本体＋レンズ）/ console / None（判定できない）。"""
    toks = set(_WORD_RE.findall(norm))
    has_spec = bool(_FOCAL_RE.search(norm) and _APERTURE_RE.search(norm)) or bool(_SPEC_RE.search(norm))
    bodyish = any(w in toks or (not w.isascii() and w in norm) for w in _BODY_WORDS)
    acc = bool(toks & _ACCESSORY_TOKENS) or any(w in norm for w in _ACCESSORY_WORDS)
    # 「〜用」「for 〜」付きの付属品はレンズの仕様が書いてあっても付属品（「75mm F4.5用 フード」）
    if acc and (("用" in norm or "for" in toks) or (not has_spec and not bodyish)):
        return "accessory"
    consoleish = any(w in toks for w in _CONSOLE_WORDS)
    lensy = has_spec or bool(toks & _LENS_SERIES)
    if consoleish and not lensy and any(w in norm for w in ("gameboy", "nintendo", "playstation", "famicom", "3ds", "vita", "psp", "sega")):
        return "console"
    if bodyish and lensy:
        return "kit"
    if bodyish:
        return "body"
    if lensy:
        return "lens"
    return None


def _accessory_types(norm: str) -> set[str]:
    toks = set(_WORD_RE.findall(norm))
    return {v for k, v in _ACCESSORY_TYPES.items() if (k in toks if k.isascii() else k in norm)}


def _focals(norm: str) -> set[str]:
    """焦点距離。「50mm」に加えて「4.5/180」「28/2.8」表記からも取る。"""
    out = set(_FOCAL_RE.findall(norm))
    for m in _SPEC_RE.finditer(norm):
        a, b = m.group(0).split("/")
        out.add(b if "." in a or len(a) == 1 else a)
    return out


def _generation(toks: set[str]) -> set[int]:
    out = {_GEN_ROMAN[t] for t in toks if t in _GEN_ROMAN}
    for t in toks:
        m = _GEN_RE.match(t)
        if m:
            out.add(int(m.group(1)))
    return out


def _bare_numbers(norm: str) -> set[str]:
    """単独の2〜3桁の数字（IXY 70 の 70）。24.2MP の 24 や 50mm の 50 のような一部の数字は含めない。"""
    return set(_BARE_RE.findall(norm))


_WORD_RE = re.compile(r"[a-z0-9]+")


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
