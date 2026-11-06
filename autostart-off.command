#!/bin/zsh
# ダブルクリックで解除: Macにログインしたときの自動起動をやめ、裏で動いているツールを止める
LABEL="com.buppan.flipbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1
rm -f "$PLIST"
echo "✔ 自動起動を解除し、ツールを止めました。"
echo "  使うときは start.command をダブルクリックしてください（自動起動に戻すには autostart-on.command）。"
echo
echo "このウィンドウは閉じてかまいません。"
