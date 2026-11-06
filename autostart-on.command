#!/bin/zsh
# ダブルクリックで設定: Macにログインしたら物販ツールを自動で起動する（ブックマークからいつでも開けるようにする）
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
LABEL="com.buppan.flipbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
URL="http://127.0.0.1:8000/"

finish() { echo; echo "このウィンドウは閉じてかまいません。"; exit "$1"; }

case "$DIR" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "⚠ このフォルダ（$DIR）は「デスクトップ」「書類」「ダウンロード」の中にあります。"
    echo "  macOSの保護機能により、自動起動したツールがこのフォルダを読めないことがあります。"
    echo "  うまく起動しない場合は、フォルダをホーム直下（例: $HOME/buppan）へ移してから、もう一度実行してください。"
    echo
    ;;
esac

# start.command で動かしている画面が残っていると、同じポートを取り合って起動できない
if [ ! -f "$PLIST" ] && lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✖ ツールがすでに別のウィンドウで動いています（start.command など）。"
  echo "  そのウィンドウで Ctrl+C を押して止めてから、もう一度これを実行してください。"
  finish 1
fi

chmod +x autostart/run.sh
mkdir -p data/logs "$HOME/Library/LaunchAgents"

# plist（XML）に入れるパスは & < > をエスケープ
xml() { local s="$1"; s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; print -r -- "$s"; }

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$(xml "$DIR/autostart/run.sh")</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml "$DIR")</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$(xml "$DIR/data/logs/launchd.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$DIR/data/logs/launchd.log")</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
  echo "✖ 自動起動の登録に失敗しました。"
  finish 1
fi

echo "▶ 自動起動を登録しました。ツールの起動を待っています（初回は数分かかることがあります）..."
for i in {1..180}; do
  if curl -fsS -o /dev/null "$URL" 2>/dev/null; then
    open "$URL"
    echo
    echo "✔ 起動しました。これからはMacにログインすると、裏で自動的にツールが起動します。"
    echo
    echo "【ブックマークの登録】開いた画面で ⌘ + D を押し、名前を「物販ツール」などにして保存してください。"
    echo "  以後はブックマークをクリックするだけで開けます（$URL）。"
    echo
    echo "・最新版にするとき: start.command をダブルクリック（取り込んで再起動します）"
    echo "・自動起動をやめるとき: autostart-off.command をダブルクリック"
    echo "・動作の記録: data/logs/server.log"
    finish 0
  fi
  sleep 1
done

echo "✖ 3分待っても画面が開けませんでした。記録の最後を表示します:"
echo "---------------------------------------------"
tail -n 30 data/logs/server.log 2>/dev/null
tail -n 10 data/logs/launchd.log 2>/dev/null
echo "---------------------------------------------"
echo "「Operation not permitted」と出ている場合は、上の⚠の案内どおりフォルダを移してから再実行してください。"
finish 1
