#!/usr/bin/env python3
"""Batching guard: fails if any agent exceeds 1 call/GW."""
CALL_LIMIT = 6  # 5 experts + Nyx

def check_batch(count, gw):
    if count > CALL_LIMIT:
        raise RuntimeError(f"RATE LIMIT BREACH: gw={gw} calls={count} > {CALL_LIMIT}. Batching broken.")
    print(f"BATCH OK gw={gw} calls={count} (limit {CALL_LIMIT})")
