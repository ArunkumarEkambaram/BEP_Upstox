#!/usr/bin/env bash
# deploy/setup_server.sh - one-time setup of the BEP Upstox cloud server (BEP Upstox PR 5).
# Ubuntu 24.04 (AWS Lightsail, Mumbai). Run as the normal login user (ubuntu), from inside the cloned repo:
#     bash deploy/setup_server.sh
# It installs Python 3.14 (uv), the app's packages, a system service that starts the app at boot and restarts it
# if it stops, the IST clock, a firewall (SSH only) and Tailscale (the app is reachable only from your own
# devices, over HTTPS). Safe to run again. Full guide: deploy/README.md
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
PY_VERSION="3.14"
SERVICE=bep-upstox
PORT=8084

step() { printf '\n== %s\n' "$1"; }

step "1/6 Packages and the IST clock"
sudo apt-get update -y
sudo apt-get install -y git curl ufw
sudo timedatectl set-timezone Asia/Kolkata
timedatectl | grep -i "time zone"

step "2/6 Python $PY_VERSION (uv) and the app's packages"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
"$UV" python install "$PY_VERSION"
cd "$APP_DIR"
[ -x .venv/bin/python ] || "$UV" venv --python "$PY_VERSION" .venv
"$UV" pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python --version

step "3/6 System service ($SERVICE): starts at boot, restarts within 5 s if it stops"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=BEP Upstox (NiceGUI, port $PORT)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=BEP_HOST=127.0.0.1
Environment=TZ=Asia/Kolkata
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
sleep 3
systemctl --no-pager --lines=0 status $SERVICE | head -4 || true

step "4/6 Firewall: SSH and Tailscale only (the app port is never open to the internet)"
sudo ufw allow OpenSSH
sudo ufw allow in on tailscale0
sudo ufw --force enable
sudo ufw status | head -8

step "5/6 Tailscale (private HTTPS address for your PC and phone)"
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
echo "If a login link is shown below, open it and log in with your Tailscale account."
sudo tailscale up
sudo tailscale serve --bg $PORT
sudo tailscale serve status || true

step "6/6 Done"
cat <<EOF
Next:
  1. Set the app password (asked twice, nothing is shown):
         cd $APP_DIR && .venv/bin/python auth.py set-password && sudo systemctl restart $SERVICE
  2. Open the https://....ts.net address shown above on your PC / phone (Tailscale app installed and logged in).
  3. Register this server's static IP in your Upstox app AFTER market hours (it logs your token out).
  4. Later updates:  bash deploy/update.sh
  Logs:  journalctl -u $SERVICE -f
EOF
