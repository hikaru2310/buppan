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
    # 2026-11 売れた相場での本判定で見つかった誤照合（検索語をシリーズ単位に広げた後）
    # 付属品 ↔ 本体・セット
    ("ペンタックス PENTAX 67 純正 ストラップ ラグ 留め金", "Pentax Adaptor K for 67 Lenses Boxed", False),
    ("【中古】PENTAX ペンタックス 67 アングルファインダー 6x7 67", "[Near MINT] Pentax 6x7 67 TTL Camera SMC T 90mm F2.8", False),
    ("【中古】PENTAX ペンタックス 67 REAR CONVERTER 2x", "[ N.MINT ] Pentax Rear Converter 2X Teleconverter for 67", True),
    ("【中古】PENTAX ペンタックス PH-SA 82mm レンズフード 67", "*EXCELLENT+4 / Caps* Pentax SMC 67 75mm f/4.5 Lens", False),
    ("【中古】Mamiya マミヤ RB用120フィルムホルダー", "[TOP MINT] Mamiya M645 Super Camera Body Only AE Finder", False),
    ("ソニー SONY MPK-URX100A アンダーウォーター ハウジング", "Sony Cyber-shot DSC-RX100 III M3 Digital Camera with Charger", False),
    ("Canon IXY digital 30 バッテリー 充電器 純正品 付属品", "Excellent+++++ Canon IXY 510 IS Pink Digital Camera", False),
    ("平成モデル！ライカ純正28mm光学ファインダー Leica 12009", "LCD Perfect [Near MINT w/ Hood] Ricoh GR1V Black 28mm f2.8", False),
    # レンズ単体 ↔ カメラ（本体・セット）
    ("contax g 45mm f2 Lens convert to Leica", "Contax G1 green label camera with planar f2 45mm Lens", False),
    ("CONTAX Carl Zeiss T* Biogon 28mm F2.8", "Contax G1 Green Label 35mm Film Camera w/ Carl Zeiss Biogon 28mm F2.8", False),
    ("Contax CarlZeiss Sonnar 90mm Biogon 28", "[Top MINT] Contax G1 Rangefinder 35mm Camera Biogon 28mm F2.8", False),
    ("美品 マミヤ RZ67 110mm F2.8 W 中判 レンズ", "[Near MINT] Mamiya RZ67 Pro II Medium Format Film Camera Body", False),
    ("【中古】Mamiya マミヤ SEKOR 127mm f/3.8 RB67用", "Exc+4 : Mamiya RB67 Pro Medium Format Film Camera Body", False),
    ("OLYMPUS M.ZUIKO 14-42mm F3.5-5.6 EZ シルバー", "Olympus PEN E-P1 Silver Mirrorless Camera M.Zuiko 14-42mm", False),
    ("Mamiya Sekor C 80mm F2.8 N 645用", "Mamiya Sekor C 80mm f/2.8 N Lens for M645", True),
    # 世代・型番違い
    ("I21215 【格安•動作品】Cyber-shot DSC-RX100", "Sony Cyber-shot DSC-RX100 III M3 Digital Camera", False),
    ("ソニー SONY RX100 III【動作確認済】#10558", "Sony rx100 iii 20.1 MP premium compact camera", True),
    ("RICOH GR DIGITAL 本体セット", "Ricoh GR Digital II Compact Digital Camera", False),
    ("Mamiya RZ67 PRO ボディ", "Mamiya RZ67 Pro II Medium Format Body", False),
    ("Olympus PEN E-PL3 M.ZUIKO 14-42mm ⅡR", "Olympus PEN E-P1 Silver Mirrorless Camera M.Zuiko 14-42mm", False),
    ("【スマホ転送OK】キャノン Canon IXY DIGITAL 70", "N MINT Canon IXY DIGITAL 920 IS SD880 IS IXUS 870", False),
    ("Canon キヤノン IXY 600F パープル", "[ TOP MINT ] Canon IXY 600F PowerShot ELPH 310HS IXUS 230", True),
    ("OLYMPUS PEN Lite E-PL7 ミラーレス一眼", "Olympus PEN E-PL7 Digital Camera White with 2 Lens Kit", True),
    ("MAMIYA RB67 PROFESSIONAL 中判フィルムカメラ", "Mamiya RB67 Pro S Medium Format 6x7 Film Camera Body", True),
    ("【中古】 【並品】 コンタックス ビオゴン T* G 28/2.8 クローム", "[Top MINT] Contax G2 Film Camera Biogon 28mm f/2.8 Lens", False),
    ("コンタックス ビオゴン T* G 28/2.8", "Contax Carl Zeiss Biogon T* 28mm f/2.8 G Lens", True),
    ("RICOH GR DIGITAL III", "Ricoh GR III x Black Digital Camera", False),
    ("【中古】PENTAX ペンタックス PH-SA 82mm レンズフード 67 75mm F4.5用", "*EXCELLENT+4 / Caps* Pentax SMC 67 75mm f/4.5 Lens", False),
    ("Mamiya本体 RB67 proSD LENS KL 4.5/180L-A", "Mamiya RB67 Pro SD Film Camera 127mm f/3.8 KL Lens", False),
    ("Canon IXY DIGITAL コンパクトデジタルカメラ 本体", "Excellent+++++ Canon IXY 510 IS Pink Digital Camera", False),
    ("Canon キャノン IXY 50S ブラウン デジタルカメラ", "Excellent+++++ Canon IXY 510 IS Pink Digital Camera", False),
    ("リコー RICOH GR DIGITAL III 《元箱付属》", "S/C 546[Almost Unused w/Box] RICOH GR III 24.2MP Digital Camera", False),
    ("RICOH GR DIGITAL III", "RICOH GR DIGITAL III GR DIGITAL 3 10MP Digital Camera", True),
    ("Canon IXY DIGITAL 200 本体", "Canon IXY 200 PowerShot ELPH 185 IXUS 185 Digital Camera", False),
    ("Mamiya RB67 KL 4.5/180L-A レンズ付き ボディ", "New Seal [Near MINT] Mamiya RB67 Film Camera 127mm f/3.8 Lens", False),
    # 別の商品（ソフト ↔ 本体）
    ("ポケットモンスター サファイア GBA", "Boxed Nintendo Game Boy Advance Console- Indigo", False),
    ("ゲームボーイカラー 本体 クリアパープル", "Nintendo Game Boy Color Console Clear Purple Japan", True),
]

# 分かっている取りこぼし（精度を優先して見送ったもの）。数えないが、実行時に結果を表示する
KNOWN_MISSES = [
    # IXY DIGITAL 10 = IXUS 70 = SD1000。eBay側が「IXY 10」と略していて、別シリーズの IXY と区別できない
    ("キヤノン IXY DIGITAL 10（シルバー）", "Canon IXUS 70 SD1000 IXY 10 CCD Compact Digital Camera", True),
]


def main() -> int:
    ng = 0
    for m, e, same in CASES:
        s = match_score(m, e)
        ok = (s >= THRESHOLD) == same
        ng += not ok
        if not ok:
            print(f"NG {s:.2f} 期待={'同一' if same else '別物'} | {m} <-> {e}")
    for m, e, same in KNOWN_MISSES:
        print(f"（既知の取りこぼし）{match_score(m, e):.2f} 期待={'同一' if same else '別物'} | {m} <-> {e}")
    print(f"{len(CASES) - ng}/{len(CASES)} 正解（正解率 {(len(CASES) - ng) / len(CASES):.0%}）")
    return 1 if ng else 0


if __name__ == "__main__":
    raise SystemExit(main())
