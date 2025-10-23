#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN is required" >&2
  exit 1
fi

if [[ -z "${WEBHOOK_EXTERNAL_URL:-}" ]]; then
  echo "WEBHOOK_EXTERNAL_URL is required" >&2
  exit 1
fi

if [[ -z "${WEBHOOK_SECRET_TOKEN:-}" ]]; then
  echo "WEBHOOK_SECRET_TOKEN is required" >&2
  exit 1
fi

curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d url="${WEBHOOK_EXTERNAL_URL}" \
  -d secret_token="${WEBHOOK_SECRET_TOKEN}"
