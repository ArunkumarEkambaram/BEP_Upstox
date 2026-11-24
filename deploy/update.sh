#!/usr/bin/env bash
# deploy/update.sh - update the BEP Upstox server to the latest merged master and restart the app (PR 5).
#     bash deploy/update.sh            (refused during market hours, Mon-Fri 09:00-15:35 IST)
#     bash deploy/update.sh --force    (update anyway; open positions resume from the orders file)
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICE=bep-upstox

now=$(TZ=Asia/Kolkata date +%H%M)
dow=$(TZ=Asia/Kolkata date +%u)
if [ "${1:-}" != "--force" ] && [ "$dow" -le 5 ] && [ $((10#$now)) -ge 900 ] && [ $((10#$now)) -lt 1535 ]; then
    echo "Market hours (IST $now): an update restarts the app. Run it after 15:35, or use --force."
    exit 1
fi

echo "== git pull (master)"
git pull --ff-only origin master
git log --oneline -1

echo "== packages"
UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
"$UV" pip install --python .venv/bin/python -r requirements.txt

echo "== restart"
sudo systemctl restart $SERVICE
sleep 4
systemctl --no-pager --lines=0 status $SERVICE | head -4 || true
journalctl -u $SERVICE -n 15 --no-pager
