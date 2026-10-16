"""照合（商品同一性判定）の回帰テスト。ロードマップP0「正解率95%以上」の土台。

実行: .venv/bin/python tests/test_matching.py
誤マッチ・取りこぼしを見つけたら、ここにケースを足してから直す。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flipbot.matching import match_score  # noqa: E402

THRESHOLD = 0.55
# (メルカリのタイトル, eBayのタイトル, 同じ商品か)
CASES = [
    # レンズ: 焦点距離・F値・Ai/Ai-s/非Ai/Ai改・セット品
    ("Nikon ニコン Ai-s Nikkor 50mm F1.4 単焦点 オールドレンズ", "Nikon Nikkor 50mm f/1.4 Ai-s Manual Focus Lens", True),
    ("【返品保証】 ニコン Nikon NIKKOR Ai-s 50mm F1.4 レンズ e17648", "Nikon Nikkor 50mm f/1.4 Ai-s Lens", True),
    ("Nikon ニコン Ai-s Nikkor 50mm F1.4", "Nikon Nikkor 50mm f/1.8 Ai-s Lens", False),
    ("Nikon Ai Nikkor 50mm F1.4", "Nikon Nikkor 50mm f/1.4 Ai-s Lens", False),
    ("ニコン Nikon NIKKOR-S Auto 50mm F1.4 非Ai 前後キャップ付", "Nikon Nikkor 50mm f/1.4 Ai-s Lens", False),
    ("ニコン Nikon F アイレベル + Nikkor-S Auto 50mm F1.4 ボディ レンズセット", "Nikon Nikkor 50mm f/1.4 Ai-s Lens", False),
    ("Nikon Ai-s Nikkor 105mm F2.5", "Nikon Nikkor 50mm f/1.4 Ai-s Lens", False),
    ("Canon New FD 85mm F1.8 キャノン", "Canon New FD 85mm f/1.8 Lens", True),
    ("Canon FD 50mm F1.4 S.S.C.", "Canon FD 50mm f/1.4 S.S.C. lens", True),
    ("SMC PENTAX67 105mm F2.4 中判", "Pentax SMC Takumar 6x7 105mm f/2.4", True),
    # カメラ本体: 短い型式・末尾違い
    ("CONTAX T2 チタンシルバー フィルムカメラ", "Contax T2 35mm Point & Shoot Film Camera Titanium", True),
    ("CONTAX T2 チタンシルバー フィルムカメラ", "Contax T3 Film Camera", False),
    ("RICOH GR III デジタルカメラ 美品", "Ricoh GR III 24.2MP Digital Camera Black", True),
    ("RICOH GR III デジタルカメラ 美品", "Ricoh GR IIIx Digital Camera", False),
    ("FUJIFILM X100V シルバー", "Fujifilm X100V Silver Digital Camera", True),
    ("FUJIFILM X100V シルバー", "Fujifilm X100VI Silver", False),
    ("Nikon D750 ボディ シャッター数少 元箱付 美品", "Nikon D750 24.3MP Digital SLR Camera Body Only", True),
    ("Nikon D750 ボディ", "Nikon D7500 Body", False),
    # ゲーム
    ("ニンテンドー3DS LL 本体 ピカチュウエディション", "Nintendo 3DS XL Pikachu Edition Console Japan", True),
    ("任天堂 ゲームボーイ 本体 DMG-01 動作確認済 送料込み", "Nintendo Game Boy DMG-01 Original Console Tested Working", True),
    ("ゲームボーイアドバンスSP AGS-101 バックライト 美品", "Nintendo Game Boy Advance SP AGS-101 Backlit Pearl Blue", True),
    ("ゲームボーイアドバンスSP AGS-101", "Game Boy Advance SP AGS-001 Console", False),
    ("ゲームボーイライト アストロボーイ 本体のみ", "Game Boy Light Astro Boy Limited Edition with box", False),
    ("Nintendo Switch 有機ELモデル ホワイト", "Nintendo Switch OLED Model White Console", True),
    ("Nintendo Switch 有機ELモデル ホワイト", "Nintendo Switch Lite Gray", False),
    # 楽器・時計
    ("BOSS SD-1 スーパーオーバードライブ 日本製", "Boss SD-1 Super OverDrive Pedal Made in Japan", True),
    ("BOSS DS-1 ディストーション 日本製", "Boss SD-1 Super OverDrive Pedal Made in Japan", False),
    ("セイコー 6139-6002 クロノグラフ 自動巻き", "Seiko 6139-6002 Chronograph Automatic Pogue", True),
    ("G-SHOCK GWF-A1000-1AJF フロッグマン 美品", "Casio G-Shock Frogman GWF-A1000-1A Titanium", True),
    ("カシオ G-SHOCK フロッグマン GWF-A1000 チタン", "Casio G-Shock GWF-1000 Frogman", False),
]


def main() -> int:
    ng = 0
    for m, e, same in CASES:
        s = match_score(m, e)
        ok = (s >= THRESHOLD) == same
        ng += not ok
        if not ok:
            print(f"NG {s:.2f} 期待={'同一' if same else '別物'} | {m} <-> {e}")
    print(f"{len(CASES) - ng}/{len(CASES)} 正解（正解率 {(len(CASES) - ng) / len(CASES):.0%}）")
    return 1 if ng else 0


if __name__ == "__main__":
    raise SystemExit(main())
