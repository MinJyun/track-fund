#!/bin/zsh
# 每日排程入口：抓持股、產報告，有新資料就 commit 進 git（launchd 呼叫）
set -u
cd "$(dirname "$0")"

/usr/bin/python3 -W ignore main.py daily
status=$?

git add -A data reports
if ! git diff --cached --quiet; then
    git commit -q -m "daily snapshot $(date +%Y-%m-%d)"
fi

exit $status
