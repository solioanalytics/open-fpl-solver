#!/usr/bin/env bash
# Run locally to add OPENROUTER_API_KEY to .env with masked input
set -euo pipefail

if [ ! -f .env ]; then
    echo ".env not found. Run scripts/setup_env.sh first."
    exit 1
fi

echo -n "OpenRouter API Key (hidden, starts with sk-or-): "
read -rs key; echo

# Add or update OPENROUTER_API_KEY in .env
grep -q "^OPENROUTER_API_KEY=" .env && sed -i "s/^OPENROUTER_API_KEY=.*/OPENROUTER_API_KEY=${key}/" .env || echo "OPENROUTER_API_KEY=${key}" >> .env

echo "OPENROUTER_API_KEY added to .env"
echo "Current .env:"
cat .env | sed 's/OPENROUTER_API_KEY=.*/OPENROUTER_API_KEY=***/'