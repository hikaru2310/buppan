#!/bin/zsh
# ダブルクリックで起動: GitHubの最新版を取り込み → 物販ツールを起動 → ブラウザで開く
cd "$(dirname "$0")"
echo "▶ GitHub から最新版を取り込み中..."
git pull --ff-only || echo "（取り込みをスキップしました。オフラインか、手元に未保存の変更があります）"
if [ ! -x .venv/bin/python ]; then
  echo "▶ 初回セットアップ中..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && .venv/bin/python -m playwright install chromium
fi
( sleep 3; open "http://127.0.0.1:8000/" ) &
echo "▶ 起動します。終了するときはこのウィンドウで Ctrl+C を押してください。"
.venv/bin/python -m flipbot.run serve
