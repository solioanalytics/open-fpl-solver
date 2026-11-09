#!/usr/bin/env bash
# Run locally to create .env with masked input. Never commits credentials.
set -euo pipefail

echo -n "FPL Email: "
read -r email
echo -n "FPL Password (hidden): "
read -rs password; echo
echo -n "FPL Team ID (number in fantasy.premierleague.com/my-team/XXXXX): "
read -r team_id

cat > .env <<EOF
FPL_EMAIL=${email}
FPL_PASSWORD=${password}
FPL_TEAM_ID=${team_id}
DRY_RUN=true
FPL_API_BACKOFF_SEC=30
FPL_LOG_DIR=logs
EOF
echo ".env written (gitignored). Check .env.example for key format."
