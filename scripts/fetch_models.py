#!/usr/bin/env python3
"""Fetch free models from OpenRouter."""
import os, requests
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    print("No API key")
    exit(1)

resp = requests.get("https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"})
data = resp.json()

print("=== FREE models ===")
for m in data['data']:
    pricing = m.get('pricing', {})
    if pricing.get('prompt') == '0' or 'free' in m['id'].lower():
        print(f"  {m['id']} - prompt:{pricing.get('prompt')} completion:{pricing.get('completion')}")

print("\n=== ALL models (first 30) ===")
for m in data['data'][:30]:
    pricing = m.get('pricing', {})
    print(f"  {m['id']} - prompt:{pricing.get('prompt')} completion:{pricing.get('completion')}")