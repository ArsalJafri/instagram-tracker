#!/usr/bin/env bash
# Provision the tracker on a fresh Debian/Ubuntu VM (Oracle Always Free, a Pi, anything).
#
# Run this from inside a checkout of the repo, on the VM:
#
#     bash deploy/setup-linux.sh
#
# It installs dependencies, builds the venv and registers a systemd service. It never
# creates or edits .env — secrets are yours to place before running this.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="instagram-tracker"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$USER}"

echo "==> Project:  ${PROJECT_DIR}"
echo "==> Run user: ${RUN_USER}"

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    cat >&2 <<EOF

ERROR: ${PROJECT_DIR}/.env is missing.

Copy .env.example to .env and fill in DISCORD_WEBHOOK_URL before running this script.
Do not commit that file.
EOF
    exit 1
fi

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip

echo "==> Building the virtualenv"
# A venv bakes in absolute paths, so it is always rebuilt here rather than copied.
rm -rf "${PROJECT_DIR}/.venv"
python3 -m venv "${PROJECT_DIR}/.venv"
"${PROJECT_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install --quiet -r "${PROJECT_DIR}/requirements.txt"

echo "==> Verifying the story sources reach the outside world"
# Datacenter IPs are treated more harshly by Instagram than residential ones, so this
# is the moment to find out whether the instagram_bio source still returns links here.
if ! "${PROJECT_DIR}/.venv/bin/python" -m instagram_tracker --once --verbose 2>&1 | tee /tmp/tracker-firstrun.log; then
    echo "WARNING: the first run exited non-zero; check /tmp/tracker-firstrun.log" >&2
fi

if grep -q "Instagram bio exposed 0 distinct" /tmp/tracker-firstrun.log; then
    cat >&2 <<EOF

WARNING: the instagram_bio source returned no links from this host.

Instagram commonly restricts datacenter IP ranges. IGExport should still work, so the
tracker remains functional, but the auth-free second source may be dead here. Consider
dropping instagram_bio from STORY_PROVIDER, or running on residential hardware instead.
EOF
fi

echo "==> Installing the systemd service"
sudo tee "${UNIT_PATH}" >/dev/null <<EOF
[Unit]
Description=Instagram Story job tracker
Documentation=https://github.com/ArsalJafri/instagram-tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python -m instagram_tracker
Restart=always
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo
echo "==> Done. Useful commands:"
echo "    sudo systemctl status ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo "    sudo systemctl restart ${SERVICE_NAME}    # after editing .env"
