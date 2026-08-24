#!/usr/bin/env bash
set -euo pipefail

# Run once on the existing VPS from /opt/ai-playground-src-v2.
# It updates only the deployed source checkout and installs the two SWIPE_INFO
# systemd definitions; it never touches generated review packages or secrets.
REPO_DIR="/opt/ai-playground-src-v2"
UNIT_DIR="${REPO_DIR}/social-automation/deploy/systemd"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "Expected deployment checkout not found: ${REPO_DIR}" >&2
  exit 1
fi

git -C "${REPO_DIR}" fetch origin main
git -C "${REPO_DIR}" reset --hard origin/main

sudo install -m 0644 "${UNIT_DIR}/social-automation-swipe-info.service" /etc/systemd/system/
sudo install -m 0644 "${UNIT_DIR}/social-automation-swipe-info.timer" /etc/systemd/system/
sudo install -m 0644 "${UNIT_DIR}/social-automation-swipe-info-failure@.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now social-automation-swipe-info.timer
sudo systemctl status social-automation-swipe-info.timer --no-pager
