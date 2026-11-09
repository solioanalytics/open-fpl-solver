#!/usr/bin/env python3
"""Read-only auth check: log in and confirm the private my-team endpoint resolves."""
import os
import requests

def _load_env(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))
team_id = os.getenv("FPL_TEAM_ID")

s = requests.Session()
s.headers.update({
    "Origin": "https://fantasy.premierleague.com",
    "Referer": "https://fantasy.premierleague.com/",
    "User-Agent": "open-fpl-solver/0.1",
})
r = s.post(
    "https://users.premierleague.com/accounts/login/",
    json={
        "email": os.getenv("FPL_EMAIL"),
        "password": os.getenv("FPL_PASSWORD"),
        "app": "plfpl-web",
        "redirectUri": "https://fantasy.premierleague.com/",
    },
    timeout=20,
)
print("login HTTP:", r.status_code)
print("cookies:", len(s.cookies))
if s.cookies and team_id:
    p = s.get(f"https://fantasy.premierleague.com/api/my-team/{team_id}/", timeout=20)
    print("my-team HTTP:", p.status_code)
    if p.status_code == 200:
        d = p.json()
        picks = d.get("current_squad") or []
        print("squad_size:", len(picks))
        print("bank:", d.get("transfers", {}).get("bank"), "| total value:", d.get("value"))
    else:
        print("body:", p.text[:200])
