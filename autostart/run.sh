#!/bin/zsh
# Macのログイン時に launchd から呼ばれる起動スクリプト（手で実行する必要はありません）
# 最新版を取り込み → 必要ならセットアップ → 物販ツールを起動
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="ja_JP.UTF-8" PYTHONUNBUFFERED=1

# ログは data/logs/server.log（5MBを超えたら server.log.1 に回す）
mkdir -p data/logs
LOG="data/logs/server.log"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ]; then
  mv -f "$LOG" "$LOG.1"
fi
exec >>"$LOG" 2>&1

echo "==== $(date '+%Y-%m-%d %H:%M:%S') 起動 ===="
git pull --ff-only || echo "（取り込みをスキップしました。オフラインか、手元に未保存の変更があります）"

if [ ! -x .venv/bin/python ]; then
  echo "▶ 初回セットアップ中..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && .venv/bin/python -m playwright install chromium || exit 1
  touch .venv/.requirements-installed
elif [ requirements.txt -nt .venv/.requirements-installed ]; then
  echo "▶ 必要なライブラリを更新中..."
  .venv/bin/pip install -q -r requirements.txt && touch .venv/.requirements-installed
fi

# caffeinate: ツールが動いている間はMacを自動スリープさせない（ふたを閉じた時は pmset の設定が必要）
exec /usr/bin/caffeinate -is .venv/bin/python -m flipbot.run serve
