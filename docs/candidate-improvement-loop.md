# 仕入れ候補を目標件数まで増やす（AIの改善ループ）

このファイルは `improve-candidates.command` から Claude Code に渡す指示です。人が読んでも分かるように書いています。

## 目的

保存済みのメルカリ出品と eBay の売れた相場（`data/flips.db`）で数え直した **仕入れ候補が {GOAL} 件以上** になるまで、
**今の基準のまま** 照合の取りこぼしを減らす改善を繰り返す。

## 変えてはいけないもの（＝「今の水準」）

- `config.yaml` の `thresholds`（貢献利益・ROI・照合しきい値・成約件数の下限・付属品よけ・仕入れ上限）と、計画シートのカテゴリ別下限
- `ebay_api.active_as_market`（出品中の価格を相場にしない）
- 除外語（`exclude_keywords` / `category_excludes`）を減らすこと
- `matching.py` の減点ルールを弱めること（別物を同じ商品とみなす方向の変更）

## 守ること

- eBay・メルカリのサイトにはアクセスしない。`flipbot.run once / loop / serve / login` は実行しない
- `git commit` / `git push` はしない。`config.local.yaml` と `data/` の認証情報は読まない
- DBは `.venv/bin/python -m flipbot.run diagnose` か `sqlite3 -readonly data/flips.db` で読むだけ

## 手順（1周）

1. `.venv/bin/python -m flipbot.run diagnose --goal {GOAL} --json data/diagnose.json` を実行し、候補件数と、どこで落ちているかを見る
2. 候補が {GOAL} 件以上なら終了して報告する
3. 「相場なし」のうち eBay の成約に近かった出品（診断の下に出る一覧、JSON の `genres[].misses`）を見て、**同じ商品なのに一致しなかった**ものだけを直す
   - 日本語↔英語の語の対応が足りない → `flipbot/normalize.py` の `PHRASES` に追加（例: 「ロッコール」→ `rokkor`）
   - 表記ゆれ（全角・ハイフン・`F1.4`/`f/1.4` など）で型番が取れない → `normalize.py` を直す
   - 本当に別の商品（型番・焦点距離・F値・付属品の違い）は直さない
4. 直したら `tests/test_matching.py` に、その出品と成約の組み合わせが一致する（別物は一致しない）テストを足し、
   `.venv/bin/python tests/test_matching.py` と `.venv/bin/python tests/test_core.py` が両方合格することを確かめる
5. 1 に戻る。候補件数が2周続けて増えなければ、それ以上はデータ（人の作業）の問題なので終了する

## 最後の報告（日本語）

- 開始時と終了時の候補件数（目標 {GOAL} 件）と、各周で増えた件数
- 直したこと（どの語・どの表記ゆれ）と、変えたファイル
- 人がやること：eBayの売れた相場がないジャンル（「eBay相場の取り込み」画面で取り込む）、売れた相場が期限切れのジャンル
- 基準を変えないと増えない分（「基準未満」の件数）は、件数だけ挙げる（基準を変えるかは人が決める）
- 変更をGitHubに上げるには、人が内容を確認してから commit / push すること
