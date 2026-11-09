#!/usr/bin/env bash
# Weekly automation hook — pulls fresh projections, runs dry-run optimizer, logs output.
# Requires: data/*.csv projections + .env (DRY_RUN=true by default)
echo "[$(date -Iseconds)] Auto-run start" >> logs/weekly_audit.log
python -m run.solve >> logs/weekly_audit.log 2>&1 || echo "Optimizer failed (no projection CSV?)" >> logs/weekly_audit.log
