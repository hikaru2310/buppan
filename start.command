#!/bin/zsh
# ダブルクリックで起動: GitHubの最新版を取り込み → 物販ツールを起動 → ブラウザで開く
cd "$(dirname "$0")"
LABEL="com.buppan.flipbot"
URL="http://127.0.0.1:8000/"

# 自動起動（autostart-on.command）を設定済みなら、裏のツールを再起動して最新版にする
if [ -f "$HOME/Library/LaunchAgents/$LABEL.plist" ]; then
  echo "▶ 自動起動中のツールを再起動して、GitHubの最新版を取り込みます..."
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
  sleep 3
  for i in {1..120}; do
    curl -fsS -o /dev/null "$URL" 2>/dev/null && break
    sleep 1
  done
  open "$URL"
  echo "✔ 開きました（動作の記録: data/logs/server.log）。このウィンドウは閉じてかまいません。"
  exit 0
fi

# すでに別のウィンドウで動いているなら、画面を開くだけ
if curl -fsS -o /dev/null "$URL" 2>/dev/null; then
  open "$URL"
  echo "✔ ツールはすでに動いています。画面を開きました。"
  exit 0
fi

echo "▶ GitHub から最新版を取り込み中..."
git pull --ff-only || echo "（取り込みをスキップしました。オフラインか、手元に未保存の変更があります）"
if [ ! -x .venv/bin/python ]; then
  echo "▶ 初回セットアップ中..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && .venv/bin/python -m playwright install chromium
fi
( sleep 3; open "$URL" ) &
echo "▶ 起動します（15分おきに自動スキャン）。終了するときはこのウィンドウで Ctrl+C を押してください。"
echo "  （ブックマークから開けるよう、Macのログイン時に自動起動するには autostart-on.command をダブルクリック）"
# caffeinate: ツールが動いている間はMacを自動スリープさせない（ふたを閉じた時は pmset の設定が必要）
caffeinate -is .venv/bin/python -m flipbot.run serve
