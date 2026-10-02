"""日英タイトル正規化。

マッチング精度の大半はこの辞書で決まる。語を足すときは PHRASES に追記する。
置換は「長いキーから順」に行う（"ゲームボーイアドバンス" を "ゲームボーイ" が先に食わないように）。
"""
from __future__ import annotations

import re
import unicodedata

# 日本語 → 英語（eBay 側の表記に寄せる）。キーは NFKC + 小文字化後の表記で書く。
PHRASES: dict[str, str] = {
    # ゲーム機
    "ゲームボーイアドバンスsp": "gameboy advance sp",
    "ゲームボーイアドバンス": "gameboy advance",
    "ゲームボーイカラー": "gameboy color",
    "ゲームボーイポケット": "gameboy pocket",
    "ゲームボーイライト": "gameboy light",
    "ゲームボーイ": "gameboy",
    "game boy": "gameboy",
    "gba sp": "gameboy advance sp",
    "gbasp": "gameboy advance sp",
    "gba": "gameboy advance",
    "ファミコン": "famicom",
    "ファミリーコンピュータ": "famicom",
    "スーパーファミコン": "super famicom",
    "スーファミ": "super famicom",
    "ニンテンドースイッチ": "nintendo switch",
    "スイッチ": "switch",
    "プレイステーション": "playstation",
    "セガサターン": "sega saturn",
    "メガドライブ": "mega drive",
    "任天堂": "nintendo",
    "ニンテンドー": "nintendo",
    "有機el": "oled",
    "有機elモデル": "oled",
    "バックライト": "backlit",
    "アストロボーイ": "astro boy",
    "鉄腕アトム": "astro boy",
    # カメラ
    "ニコン": "nikon",
    "キヤノン": "canon",
    "キャノン": "canon",
    "ソニー": "sony",
    "富士フイルム": "fujifilm",
    "フジフイルム": "fujifilm",
    "オリンパス": "olympus",
    "ペンタックス": "pentax",
    "ライカ": "leica",
    "ボディ": "body",
    "レンズ": "lens",
    # 時計・その他
    "セイコー": "seiko",
    "カシオ": "casio",
    "ロレックス": "rolex",
    "ポケモン": "pokemon",
    "ポケモンカード": "pokemon card",
    "リザードン": "charizard",
    "ピカチュウ": "pikachu",
    # 色
    "ホワイト": "white",
    "ブラック": "black",
    "シルバー": "silver",
    "レッド": "red",
    "ブルー": "blue",
    "グレー": "gray",
    "grey": "gray",
    "パープル": "purple",
    "クリア": "clear",
    "ゴールド": "gold",
    # 状態・付属
    "箱付き": "box",
    "箱あり": "box",
    "元箱": "box",
    "箱": "box",
    "説明書": "manual",
    "未使用": "new",
    "新品": "new",
    "限定": "limited",
    "本体": "console",
}

# マッチングでは意味を持たない語（両言語）
STOPWORDS: set[str] = {
    # 日本語
    "美品", "中古", "送料込み", "送料無料", "動作確認済", "動作確認済み", "動作品", "即購入可",
    "匿名配送", "匿名", "まとめ", "セット", "付", "付き", "あり", "なし", "の", "品",
    "シャッター数少", "完品", "良品", "極美品", "保証残",
    # 英語
    "used", "tested", "working", "works", "japan", "japanese", "jp", "import", "original",
    "authentic", "genuine", "free", "shipping", "excellent", "mint", "near", "good", "very",
    "with", "and", "the", "for", "from", "of", "in", "a", "w", "only", "rare", "f/s", "fs",
    "nm", "exc", "+", "-", "/", "&", "item",
}

_PHRASE_KEYS = sorted(PHRASES, key=len, reverse=True)
# 型番: 英字と数字を両方含む塊（DMG-01, AGS-101, D750, 116610LN, GWF-A1000 など）
_MODEL_RE = re.compile(r"\b(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*\b")
_SPLIT_RE = re.compile(r"[^\w\-]+", re.UNICODE)
_SCRIPT_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[^\x00-\x7f])|(?<=[^\x00-\x7f])(?=[a-z0-9])")


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).lower()
    s = s.replace("【", " ").replace("】", " ").replace("（", " ").replace("）", " ")
    for k in _PHRASE_KEYS:
        if k in s:
            s = s.replace(k, f" {PHRASES[k]} ")
    # 英数字と日本語の境目に空白を入れる（"dmg-01動作確認" → "dmg-01 動作確認"）
    s = _SCRIPT_BOUNDARY_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def model_numbers(norm: str) -> set[str]:
    """正規化済み文字列から型番を抽出（ハイフンは保持。比較側で揺れを吸収する）。"""
    out: set[str] = set()
    for m in _MODEL_RE.findall(norm):
        if len(m) < 3:
            continue
        out.add(m)
    return out


def tokens(norm: str) -> set[str]:
    out: set[str] = set()
    for t in _SPLIT_RE.split(norm):
        t = t.strip("-_")
        if not t or t in STOPWORDS:
            continue
        if len(t) == 1 and not t.isdigit():
            continue
        out.add(t.replace("-", ""))
    return out
