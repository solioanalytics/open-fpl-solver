#!/usr/bin/env python3
"""Authenticated FPL submission helpers.

Sessions and credentials are deliberately process-local: neither cookies nor
passwords are logged or written to disk. A team-changing request can only be
made when the caller passes the exact confirmation text ``submit live``.
"""

import json
import logging
import os
from pathlib import Path

import requests

LOGIN_URL = "https://users.premierleague.com/accounts/login/"
LIVE_CONFIRMATION = "submit live"
LOG_DIR = Path(os.getenv("FPL_LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(filename=LOG_DIR / "fpl_submissions.log", level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def create_authenticated_session(email=None, password=None, session=None, confirmation=""):
    """Log in and return a cookie-bearing :class:`requests.Session` in memory."""
    if confirmation != LIVE_CONFIRMATION:
        raise PermissionError("FPL authentication is blocked until confirmation='submit live' is supplied.")

    email = email or os.getenv("FPL_EMAIL")
    password = password or os.getenv("FPL_PASSWORD")
    if not email or not password:
        raise ValueError("Set FPL_EMAIL and FPL_PASSWORD in the environment before submitting live.")

    session = session or requests.Session()
    session.headers.update(
        {
            "Origin": "https://fantasy.premierleague.com",
            "Referer": "https://fantasy.premierleague.com/",
            "User-Agent": "open-fpl-solver/0.1",
        }
    )
    response = session.post(
        LOGIN_URL,
        json={"email": email, "password": password, "app": "plfpl-web", "redirectUri": "https://fantasy.premierleague.com/"},
        timeout=20,
    )
    response.raise_for_status()
    if not session.cookies:
        raise RuntimeError("FPL login completed without a session cookie; check the credentials or any required account challenge.")
    # Keep the returned login cookie on the live Session only. This replaces the
    # former manually copied FPL_SESSION_COOKIE value without persisting it.
    session.fpl_session_cookie = next(iter(session.cookies.values()))
    return session


def submit_team(team_id, squad, transfers, chips, confirmation=""):
    """Authenticate only after an explicit live confirmation.

    The reviewed implementation does not send a team-changing endpoint/payload,
    so it cannot mutate an FPL team yet.
    """
    record = {"team_id": team_id, "squad_size": len(squad), "transfers": transfers, "chips": chips}
    if confirmation != LIVE_CONFIRMATION:
        logging.info("Submission blocked: confirmation missing; %s", json.dumps(record))
        print("BLOCKED: no FPL POST was made. Re-run with confirmation='submit live' to authenticate for a live submission.")
        return False

    session = create_authenticated_session(confirmation=confirmation)
    logging.info("Live confirmation accepted for team_id=%s; authenticated session has %s in-memory cookies.", team_id, len(session.cookies))
    print("Authenticated session created in memory. No team-changing FPL POST is implemented or was made.")
    return False
