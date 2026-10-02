# 転売bot（メルカリ→eBay 物販ツール / Arbiter）

メルカリの出品（仕入れ候補）と eBay の成約相場（Sold）を突き合わせ、**eBay手数料・換金スプレッド・送料・梱包費を差し引いた実質利益**で候補を並べます。あとは上から選んで「仕入れる」を押すだけ。

> 注意: メルカリ/eBayのサイトスクレイピングは各サービスの利用規約・robots・アクセス負荷に注意が必要です。まずは低頻度・少数クエリで運用し、必要に応じて公式APIや提携データへ置き換えてください。

## 何をしているか

```
収集（メルカリ検索 / eBay Sold検索）
  → 除外（ジャンク等の除外語・ジャンルの必須語）
  → マッチング（メルカリ1品ごとに eBay 成約品をスコアリング、しきい値以上だけ採用）
  → 相場（マッチした成約品の中央値）
  → 実質利益（FVF・固定費・換金スプレッド・国際送料・梱包費）／ROI／損益分岐の仕入れ上限
  → 下限（利益額・ROI）で絞り込み → 並び替え（新着は常に上位）
  → SQLite に保存 → CLI 表示 / Web画面
```

| 機能 | ファイル |
|---|---|
| ジャンル定義・利益ルール | `config.yaml`（`genres` / `fees` / `thresholds` / `exclude_keywords`） |
| 日英タイトル正規化・辞書・型番抽出 | `flipbot/normalize.py`（**精度はこの辞書が生命線**。語は `PHRASES` に追記） |
| マッチング（型番一致で加点・不一致で減点、箱あり/本体のみの食い違い減点） | `flipbot/matching.py` |
| 実質利益・ROI（利益÷総原価）・損益分岐 | `flipbot/profit.py` |
| 新着検知（初回スキャン後に現れた出品に NEW） | `flipbot/db.py` / `flipbot/pipeline.py` |
| Web画面（ダッシュボード・候補一覧＋内訳・収益機会マップ・資本シミュレーター・仕入れキュー＋CSV・ルール表示） | `flipbot/web.py` / `flipbot/web/index.html` |
| サンプルデータ（実サイトが取れない時の代替・デモ） | `flipbot/fixtures/sample.json` |

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp config.example.yaml config.yaml
mkdir -p data
```

## 使い方

まずサンプルデータで画面を確認（実サイトにアクセスしません）:

```bash
python -m flipbot.run serve --sample
```

→ http://127.0.0.1:8000/ を開く。

実サイトで収集して画面表示（`config.yaml` の `app.data_source: auto` なら、0件の時はサンプルで代替表示）:

```bash
python -m flipbot.run serve
```

CLI で1回だけ / 定期実行:

```bash
python -m flipbot.run once --sort profit      # roi / profit / score(総合)
python -m flipbot.run loop
```

### ブロックされる場合（手動ログイン）

1) `config.yaml` に `app.user_data_dir` を設定（例: `./data/profile`）
2) 次を実行して、開いたブラウザでログイン/人間確認を済ませる

```bash
python -m flipbot.run login
```

終わったらターミナルで `Ctrl+C`。以後 `once` / `loop` / `serve` が同じプロファイルで動きます。`python -m flipbot.run doctor` で設定を診断できます。

## 精度を上げるには

- 取りこぼし（相場なし）が多い → `normalize.py` の `PHRASES` に日英の対応語を追加
- 誤マッチが多い → `thresholds.match_threshold` を上げる / `min_comps` を増やす
- ジャンルで出品の質を絞る → `genres[].include_keywords` / `exclude_keywords`

## 次の拡張（打ち合わせで出た案）

- eBay 公式API（Browse / Marketplace Insights）への置き換え（`scrape_ebay.py` を同じ関数形で差し替え）
- 為替の自動更新
- 販売側の自動化: 在庫・出品管理、売れ残りの自動値下げ、出荷キュー（EMSラベル・追跡番号）
