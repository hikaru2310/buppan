#!/bin/zsh
# ダブルクリックで実行: Claude Code が、保存済みのデータで仕入れ候補を数え直しながら、
# 基準を変えずに照合の取りこぼしを減らす改善を、目標件数（既定50件）まで繰り返します。
# eBay・メルカリのサイトにはアクセスしません。終わったら変更内容を確認してください。
cd "$(dirname "$0")" || exit 1
GOAL="${1:-50}"
CLAUDE="$(command -v claude || ls "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude 2>/dev/null | head -1)"
if [ -z "$CLAUDE" ]; then
  echo "✖ Claude Code（claude コマンド）が見つかりません。インストールしてログインしてください。"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "✖ 先に start.command で一度ツールを起動してください（初回セットアップが必要です）。"
  exit 1
fi
echo "▶ 現在の候補件数:"
.venv/bin/python -m flipbot.run diagnose --goal "$GOAL" | tail -n 25
echo
echo "▶ Claude Code で改善を始めます（目標 ${GOAL}件）。画面の指示に従ってください。"
PROMPT="$(sed "s/{GOAL}/$GOAL/g" docs/candidate-improvement-loop.md)

上の手順で作業してください。"
"$CLAUDE" --permission-mode acceptEdits \
  --allowedTools "Read" "Glob" "Grep" "Edit" "Write" \
    "Bash(.venv/bin/python -m flipbot.run diagnose:*)" "Bash(.venv/bin/python -m flipbot.run value:*)" \
    "Bash(.venv/bin/python tests/*)" "Bash(sqlite3 -readonly:*)" "Bash(git status:*)" "Bash(git diff:*)" \
  --disallowedTools "Bash(git commit:*)" "Bash(git push:*)" "WebFetch" \
    "Bash(.venv/bin/python -m flipbot.run once:*)" "Bash(.venv/bin/python -m flipbot.run loop:*)" \
    "Bash(.venv/bin/python -m flipbot.run serve:*)" "Bash(.venv/bin/python -m flipbot.run login:*)" \
    "Read(./config.local.yaml)" "Read(./data/ebay_token.json)" "Read(./data/bot-profile/**)" \
  "$PROMPT"
