#!/usr/bin/env python3
import os, json, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from openclaw.run_real_test import main as run_pipeline

load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    raise RuntimeError("OPENROUTER_API_KEY not set")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

print("Checking rate limit status...")
try:
    test_resp = client.chat.completions.create(
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        messages=[{"role": "user", "content": "test"}],
        max_tokens=5
    )
    headers = test_resp.headers if hasattr(test_resp, 'headers') else {}
    rl_remaining = headers.get("x-ratelimit-remaining", "unknown")
    rl_reset = headers.get("x-ratelimit-reset", "unknown")
    print(f"Rate limit: remaining={rl_remaining}, reset={rl_reset}")
    
    if rl_remaining != "unknown" and int(rl_remaining) < 10:
        reset_ts = int(rl_reset) if rl_reset else 0
        if reset_ts:
            reset_time = time.ctime(reset_ts / 1000)
            print(f"⚠ Low rate limit ({rl_remaining} remaining). Resets at {reset_time}")
        else:
            print("⚠ Low rate limit, proceeding with caution")
    else:
        print(f"✓ Rate limit OK: {rl_remaining} remaining")
except Exception as e:
    print(f"Could not check rate limit: {e}")

print("\nRunning pipeline...")
try:
    run_pipeline()
except RuntimeError as e:
    if "rate" in str(e).lower() or "429" in str(e):
        print(f"\n❌ Rate limited: {e}")
        reset_ts = int(os.getenv("RATE_LIMIT_RESET", "0"))
        if reset_ts:
            print(f"Resets at: {time.ctime(reset_ts / 1000)}")
    raise
