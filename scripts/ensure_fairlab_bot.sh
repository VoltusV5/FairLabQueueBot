#!/usr/bin/env bash
# Если unit не active — пробуем поднять (systemd сам не стартует failed без Restart=always).
set -u
UNIT="fairlab-queue-bot.service"
if systemctl is-active --quiet "$UNIT"; then
  exit 0
fi
systemctl start "$UNIT" || exit 1
logger -t fairlab-watch "ensure_fairlab_bot: started ${UNIT} (was not active)"
