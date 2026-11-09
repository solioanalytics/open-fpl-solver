#!/usr/bin/env python3
"""Free xP model: fetches FPL bootstrap-static + fixtures, Understat xG/xA (via public API).
Outputs CSV in solio.csv format for the optimizer pipeline."""
import os, json, requests, pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://fantasy.premierleague.com/api"
UNDERSTAT_URL = "https://understat.com/main"

def fetch_fpl_bootstrap():
    """Get players, teams, events from FPL."""
    resp = requests.get(f"{BASE_URL}/bootstrap-static/", timeout=30)
    resp.raise_for_status()
    return resp.json()

def fetch_fpl_fixtures():
    """Get all fixtures with difficulty ratings."""
    resp = requests.get(f"{BASE_URL}/fixtures/", timeout=30)
    resp.raise_for_status()
    return resp.json()

def fetch_understat_player_stats():
    """Fetch xG/xA from Understat (public endpoint, no auth needed)."""
    # Understat doesn't have a simple public API - using known player stats endpoint
    # This is a placeholder; real implementation would scrape or use understat package
    try:
        # Try known endpoint for current season
        resp = requests.get("https://understat.com/main/league/EPL/2024", timeout=30)
        # Parsing would be needed here
        return {}
    except Exception:
        return {}

def build_xp_csv(output_path="data/solio.csv"):
    """Build projection CSV in solio format."""
    print("Fetching FPL bootstrap...")
    bootstrap = fetch_fpl_bootstrap()
    
    print("Fetching FPL fixtures...")
    fixtures = fetch_fpl_fixtures()
    
    print("Fetching Understat data...")
    understat = fetch_understat_player_stats()
    
    # Current gameweek
    current_gw = next(e for e in bootstrap["events"] if e["is_next"])["id"]
    
    # Build team difficulty map from fixtures
    team_difficulty = {}
    for f in fixtures:
        if f["event"] and f["event"] <= current_gw + 5:  # Next 5 GWs
            home_diff = f.get("team_h_difficulty", 3)
            away_diff = f.get("team_a_difficulty", 3)
            team_difficulty.setdefault(f["team_h"], []).append(away_diff)
            team_difficulty.setdefault(f["team_a"], []).append(home_diff)
    
    # Average difficulty per team
    for team_id, diffs in team_difficulty.items():
        team_difficulty[team_id] = sum(diffs) / len(diffs)
    
    # Build player projections
    players = bootstrap["elements"]
    teams = {t["id"]: t for t in bootstrap["teams"]}
    pos_map = {1: "G", 2: "D", 3: "M", 4: "F"}
    
    rows = []
    for p in players:
        if p["minutes"] == 0:
            continue
            
        team_id = p["team"]
        team_name = teams[team_id]["short_name"]
        opp_difficulty = team_difficulty.get(team_id, 3.0)
        
        # Form: last 5 GW points (from history)
        form_points = p.get("form", 0)  # FPL provides this
        
        # xG/xA from Understat (if available)
        understat_key = f"{p['first_name']}_{p['second_name']}"
        xg = understat.get(understat_key, {}).get("xG", 0)
        xa = understat.get(understat_key, {}).get("xA", 0)
        
        # Simple xP model
        base_xp = float(form_points) if form_points else 0
        fixture_adj = 1.0 - (opp_difficulty - 3) * 0.05  # difficulty 2->1.05, 4->0.95
        minutes_prob = min(1.0, p["minutes"] / 90 / max(1, current_gw - 1) * 5)  # rough
        
        xp = base_xp * fixture_adj * minutes_prob
        if xg or xa:
            xp = xp * 0.7 + (xg * 0.6 + xa * 0.3) * 0.3  # blend with Understat
        
        # Build row for each future GW (simplified - same projection)
        row = {
            "ID": p["id"],
            "Name": p["web_name"],
            "Pos": pos_map[p["element_type"]],
            "Value": p["now_cost"] / 10,
            "Team": team_name,
            "Penalties": p.get("penalties_order") or 0,
            "Corners": p.get("corners_and_indirect_freekicks_order") or 0,
            "FKs": p.get("direct_freekicks_order") or 0,
            "Ownership": float(p.get("selected_by_percent", 0) or 0) / 100,
        }
        # Add projections for next 5 GWs
        for gw in range(current_gw, current_gw + 5):
            row[f"{gw}_Pts"] = round(xp, 2)
            row[f"{gw}_xMins"] = int(90 * minutes_prob)
        
        rows.append(row)
    
    df = pd.DataFrame(rows)
    Path(output_path).parent.mkdir(exist_ok=True)
    df.to_csv(output_path, index=False, float_format="%.2f")
    print(f"Written {len(df)} players to {output_path}")
    return df

if __name__ == "__main__":
    build_xp_csv()