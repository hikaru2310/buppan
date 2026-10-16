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

## いちばん簡単な使い方

1. GitHub（https://github.com/hikaru2310/buppan ）で `config.yaml` などを編集して保存（Commit changes）
2. Mac で `start.command` をダブルクリック → 最新版を取り込んで起動し、ブラウザが開きます

このPC専用の設定（Chromeプロファイルのパス等）は `config.local.yaml` に書きます。GitHubには上がりません。

### 初回だけ：eBayにログイン

eBayの「売れた商品（Sold）」検索はログインが必要です。画面のダッシュボードに出る **「eBayにログインする」** を押すと、ツール専用のChrome（普段のChromeとは別）が開くので、eBayにログインしてください。検索結果が表示されたら自動で閉じて再スキャンします。ログイン状態は `data/bot-profile` に保存され、次回以降は不要です。

### 画面の構成（上流 → 実務の工程順）

| 区分 | 画面 | できること |
|---|---|---|
| 上流｜戦略・計画 | 経営ダッシュボード | 今日やること（工程別件数）、今月の計画対実績、リスク監視、次のステージゲート、経営判断ルール |
| | 外部環境 | メルカリ・eBay・物流/関税・為替の事実／影響／ツールでの扱い／出典（`flipbot/knowledge/environment.yaml`） |
| | 戦略・カテゴリ | カテゴリ別の計画と実績、資金配分ルール、当たりジャンル仮説のカバー率、有効な意思決定 |
| | KPI・ファネル | 仕入れファネル・12か月計画と実績。シート「KPI実績」「仕入れファネル」に貼れる形でコピー |
| | 計画タスク | 計画シートのタスクのうち、期限切れ・7日以内のもの。ツールで対応したタスク |
| 実務1｜仕入れ | 仕入れ候補 / 収益機会マップ / 仕入れキュー / 見送りリスト | 貢献利益順の候補、根拠リンク、キュー →「仕入れ済み」でSKUを採番 |
| 実務2｜入荷・出品・在庫 | 入荷・検品 / 出品 / 在庫・値付け | 到着・検品（不合格理由）→ 出品価格・eBay URL → 30/60/90日の値下げ提案・「売れた」 |
| 実務3｜販売・発送 | 発送 | 発送期限、DDP、送料と関税の実額、追跡番号 |
| 実務4｜着金・精算 | 着金・精算 / SKU台帳 | 入金額 → 実現利益と予測誤差、返品、全SKUのCSV |
| 設定 | ルール・前提 | 手数料・費用・関税の前提、現金残高の入力 |

### 事業計画シートとの連携

`config.yaml` の `plan.sheet_id` に「AI物販_事業計画マスター」を設定しています（共有設定は「リンクを知っている人は閲覧可」が必要）。計画値（月次計画・カテゴリ基準・ファネル・リスク閾値・ステージゲート・タスク・当たりジャンルDB）は、シートから6時間ごとに読み込みます。数値の正本はシートで、ツールは実績だけを持ちます。

照合精度の回帰テスト: `.venv/bin/python tests/test_matching.py`

外部環境の分析・工程別の改善計画・シートとの役割分担は [docs/business-flow-improvements.md](docs/business-flow-improvements.md) を参照。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
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
