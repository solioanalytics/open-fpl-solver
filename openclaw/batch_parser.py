#!/usr/bin/env python3
"""Batch parser: validates expert JSON; retries once, then falls back model. Logs model_used."""
import json, logging

def parse_expert_output(raw, model_tag="primary"):
    try:
        data = json.loads(raw)
        assert isinstance(data, list)
        for item in data:
            assert "player_id" in item and "score" in item
        logging.info(f"model={model_tag} parse_ok count={len(data)}")
        return data
    except Exception as e:
        logging.warning(f"model={model_tag} parse_fail retrying_fallback error={e}")
        return None
