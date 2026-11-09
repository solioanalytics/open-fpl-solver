#!/usr/bin/env python3
"""Generate realistic fixture/form/availability/setpiece/value data for current GW, call OpenRouter experts + Nyx."""
import os, json, logging, pandas as pd
from pathlib import Path
from openclaw.openrouter_client import OpenRouterClient

logging.basicConfig(level=logging.INFO)
LOG_DIR = Path(os.getenv("FPL_LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

def get_mock_expert_data():
    """Realistic mock data for current GW - shortlist ~50 players."""
    return {
        "fixture": [
            {"player_id": 1, "name": "Salah", "team": "LIV", "opp": "ARS", "venue": "H", "difficulty": 3},
            {"player_id": 2, "name": "Haaland", "team": "MCI", "opp": "CHE", "venue": "A", "difficulty": 4},
            {"player_id": 3, "name": "Son", "team": "TOT", "opp": "BRE", "venue": "H", "difficulty": 2},
            {"player_id": 4, "name": "Watkins", "team": "AVL", "opp": "WHU", "venue": "A", "difficulty": 3},
            {"player_id": 5, "name": "Isak", "team": "NEW", "opp": "FUL", "venue": "H", "difficulty": 2},
            {"player_id": 6, "name": "Saka", "team": "ARS", "opp": "LIV", "venue": "A", "difficulty": 5},
            {"player_id": 7, "name": "Palmer", "team": "CHE", "opp": "MCI", "venue": "H", "difficulty": 4},
        ],
        "form": [
            {"player_id": 1, "recent_points": [12, 8, 15, 6, 9], "avg_mins": 90},
            {"player_id": 2, "recent_points": [2, 18, 0, 14, 7], "avg_mins": 85},
            {"player_id": 3, "recent_points": [5, 11, 3, 8, 10], "avg_mins": 88},
            {"player_id": 4, "recent_points": [8, 4, 12, 2, 6], "avg_mins": 75},
            {"player_id": 5, "recent_points": [9, 6, 0, 13, 5], "avg_mins": 82},
            {"player_id": 6, "recent_points": [14, 7, 10, 4, 11], "avg_mins": 90},
            {"player_id": 7, "recent_points": [6, 15, 8, 12, 4], "avg_mins": 78},
        ],
        "availability": [
            {"player_id": 1, "status": "fit", "injury_risk": 0.05, "suspension": False},
            {"player_id": 2, "status": "fit", "injury_risk": 0.1, "suspension": False},
            {"player_id": 3, "status": "fit", "injury_risk": 0.05, "suspension": False},
            {"player_id": 4, "status": "minor_knock", "injury_risk": 0.25, "suspension": False},
            {"player_id": 5, "status": "fit", "injury_risk": 0.08, "suspension": False},
            {"player_id": 6, "status": "fit", "injury_risk": 0.03, "suspension": False},
            {"player_id": 7, "status": "recovering", "injury_risk": 0.35, "suspension": False},
        ],
        "setpiece": [
            {"player_id": 1, "pen_taker": True, "corners": False, "fks": False, "opp_concedes_setpiece": 0.15},
            {"player_id": 2, "pen_taker": True, "corners": False, "fks": False, "opp_concedes_setpiece": 0.1},
            {"player_id": 3, "pen_taker": False, "corners": True, "fks": True, "opp_concedes_setpiece": 0.2},
            {"player_id": 4, "pen_taker": False, "corners": False, "fks": False, "opp_concedes_setpiece": 0.12},
            {"player_id": 5, "pen_taker": False, "corners": False, "fks": False, "opp_concedes_setpiece": 0.18},
            {"player_id": 6, "pen_taker": False, "corners": True, "fks": True, "opp_concedes_setpiece": 0.08},
            {"player_id": 7, "pen_taker": True, "corners": True, "fks": False, "opp_concedes_setpiece": 0.11},
        ],
        "value": [
            {"player_id": 1, "price": 12.5, "ownership": 45.2, "ev_per_m": 0.85},
            {"player_id": 2, "price": 15.0, "ownership": 38.7, "ev_per_m": 0.78},
            {"player_id": 3, "price": 9.8, "ownership": 28.4, "ev_per_m": 0.92},
            {"player_id": 4, "price": 8.9, "ownership": 15.3, "ev_per_m": 1.02},
            {"player_id": 5, "price": 8.5, "ownership": 22.1, "ev_per_m": 0.98},
            {"player_id": 6, "price": 10.2, "ownership": 35.6, "ev_per_m": 0.88},
            {"player_id": 7, "price": 10.5, "ownership": 31.8, "ev_per_m": 0.91},
        ]
    }

def _derive_mock_from_real(df: pd.DataFrame, max_players: int = 50):
    """Build expert-input structures from solio.csv projection data."""
    if df.empty:
        return get_mock_expert_data()

    df = df.copy()
    gw_cols = [c for c in df.columns if "_Pts" in c]
    if not gw_cols:
        return get_mock_expert_data()

    next_gw = gw_cols[0]
    df[next_gw] = pd.to_numeric(df[next_gw], errors="coerce")
    df = df.dropna(subset=[next_gw])
    df = df.sort_values(next_gw, ascending=False).head(max_players)

    xmins_col = gw_cols[0].replace("_Pts", "_xMins")
    if xmins_col not in df.columns:
        df[xmins_col] = 90

    players = []
    for _, row in df.iterrows():
        players.append({
            "player_id": int(row["ID"]) if "ID" in row else int(row.get("ID", 0)),
            "name": str(row.get("Name", "")),
            "team": str(row.get("Team", "")),
            "next_gw_points": float(row[next_gw]),
            "minutes": float(row.get(xmins_col, 90) or 90),
            "value_price": float(row.get("Value", 0.0) or 0.0),
        })

    fixture = [
        {
            "player_id": p["player_id"],
            "name": p["name"],
            "team": p["team"],
            "difficulty": 3.0,
            "venue": "H",
            "opp": "",
            "score": max(0.0, min(1.0, p["next_gw_points"] / 15.0)),
            "confidence": 0.7,
            "rationale": "Derived from projected points",
        }
        for p in players
    ]

    form = [
        {
            "player_id": p["player_id"],
            "recent_points": [round(p["next_gw_points"], 2)] * 5,
            "avg_mins": min(90, int(p["minutes"])),
            "score": max(0.0, min(1.0, p["next_gw_points"] / 15.0)),
            "confidence": 0.75,
            "rationale": "Projected points proxy for form",
        }
        for p in players
    ]

    availability = [
        {
            "player_id": p["player_id"],
            "status": "fit",
            "injury_risk": 0.05 if p["minutes"] >= 60 else 0.35,
            "suspension": False,
            "score": 0.95 if p["minutes"] >= 60 else 0.55,
            "confidence": 0.8,
            "rationale": "Inferred from projected minutes",
        }
        for p in players
    ]

    setpiece = [
        {
            "player_id": p["player_id"],
            "pen_taker": False,
            "corners": False,
            "fks": False,
            "opp_concedes_setpiece": 0.15,
            "score": 0.5,
            "confidence": 0.4,
            "rationale": "No setpiece metadata in solio.csv",
        }
        for p in players
    ]

    value = [
        {
            "player_id": p["player_id"],
            "price": p["value_price"],
            "ownership": 0.0,
            "ev_per_m": round(p["next_gw_points"] / max(0.1, p["value_price"]), 3),
            "score": max(0.0, min(1.0, (p["next_gw_points"] / max(0.1, p["value_price"])) / 1.5)),
            "confidence": 0.8,
            "rationale": "EV per million from solio projection",
        }
        for p in players
    ]

    return {
        "fixture": fixture,
        "form": form,
        "availability": availability,
        "setpiece": setpiece,
        "value": value,
    }

def load_real_projection_data(csv_path="data/solio.csv", max_players=50):
    """Load real projection data from solio.csv and convert to expert format."""
    path = Path(csv_path)
    if not path.exists():
        return get_mock_expert_data()

    try:
        df = pd.read_csv(path)
    except Exception:
        return get_mock_expert_data()

    if df.empty:
        return get_mock_expert_data()

    return _derive_mock_from_real(df, max_players=max_players)

def build_expert_prompts(data):
    """Build batched prompts for each expert - ONE call per expert."""
    prompts = {}
    
    # Fixture expert
    prompts["fixture"] = f"""Score each player's fixture difficulty for the upcoming gameweek.
Input: {json.dumps(data['fixture'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text."""

    # Form expert
    prompts["form"] = f"""Score each player's current form based on recent points and minutes.
Input: {json.dumps(data['form'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text."""

    # Availability expert
    prompts["availability"] = f"""Score each player's availability risk (1 = certain start, 0 = likely out).
Input: {json.dumps(data['availability'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text."""

    # Setpiece expert
    prompts["setpiece"] = f"""Score each player's setpiece advantage for this gameweek.
Input: {json.dumps(data['setpiece'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text."""

    # Value expert
    prompts["value"] = f"""Score each player's value (EV per million) for this gameweek.
Input: {json.dumps(data['value'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text."""

    return prompts

def main():
    # Load profiles
    profiles = {}
    with open("openclaw/agent_profiles.conf") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if ":" in line and "primary" in line:
                    parts = line.split(":")
                    expert = parts[0].strip()
                    rest = ":".join(parts[1:]).strip()
                    primary = rest.split("primary=")[1].split(",")[0].strip()
                    fallbacks_str = rest.split("fallbacks=[")[1].split("]")[0].strip()
                    fallbacks = [f.strip() for f in fallbacks_str.split(",")]
                    profiles[expert] = {"primary": primary, "fallbacks": fallbacks}
    
    print("Loaded profiles:", list(profiles.keys()))
    
    # Get mock data and build prompts
    data = load_real_projection_data()
    prompts = build_expert_prompts(data)
    
    # Initialize OpenRouter client
    client = OpenRouterClient()
    gw = int(os.getenv("CURRENT_GW", "5"))
    
    # Call each expert ONCE
    expert_results = {}
    print(f"\n=== Calling 5 experts (GW {gw}) ===")
    for expert_name, prompt in prompts.items():
        profile = profiles.get(expert_name, {})
        result, model_used = client.call_expert(
            expert_name, prompt,
            profile.get("primary", "openrouter/openrouter/free"),
            profile.get("fallbacks", []),
            gw
        )
        expert_results[expert_name] = {"data": result, "model_used": model_used}
    
    # Call Nyx ONCE
    print(f"\n=== Calling Nyx orchestrator (GW {gw}) ===")
    expert_data_for_nyx = {k: v["data"] for k, v in expert_results.items()}
    nyx_result, nyx_model = client.call_nyx(
        expert_data_for_nyx,
        profiles.get("nyx", {}).get("primary", "openrouter/openrouter/free"),
        profiles.get("nyx", {}).get("fallbacks", []),
        gw
    )
    expert_results["nyx"] = {"data": nyx_result, "model_used": nyx_model}

    # Compute blended_scores from expert averages + Nyx adjustment
    nyx_data = expert_results["nyx"]["data"]
    if isinstance(nyx_data, dict) and "player_adjustments" in nyx_data:
        player_ids = set()
        for exp in ["fixture", "form", "availability", "setpiece", "value"]:
            ed = expert_results[exp]["data"]
            if isinstance(ed, list):
                player_ids.update(d["player_id"] for d in ed if "player_id" in d)
        blended_scores = {}
        for pid in sorted(player_ids):
            scores = []
            for exp in ["fixture", "form", "availability", "setpiece", "value"]:
                ed = expert_results[exp]["data"]
                if isinstance(ed, list):
                    m = next((d for d in ed if d.get("player_id") == pid), None)
                    if m and "score" in m:
                        scores.append(m["score"])
            if scores:
                avg = sum(scores) / len(scores)
                adj = nyx_data["player_adjustments"].get(str(pid), 0.0)
                blended_scores[str(pid)] = round(max(0.0, min(1.0, avg + adj)), 4)
        expert_results["nyx"]["data"]["blended_scores"] = blended_scores
        print(f"\\n=== NYX BLENDING ===")
        print(f"Nyx adjustment model: {nyx_model}")
        for pid in sorted(player_ids, key=lambda x: int(x)):
            adj = nyx_data["player_adjustments"].get(pid, 0.0)
            bs = blended_scores.get(pid, 0.0)
            print(f"  Player {pid}: adjustment={adj:+.3f} -> blended={bs:.3f}")
    else:
        print(f"\\n  NYX: could not compute blended_scores (no player_adjustments key)")
    
    # Print summary
    summary = client.get_call_summary()
    print(f"\n=== CALL SUMMARY ===")
    print(f"Total API calls: {summary['total_calls']}")
    print(f"Models resolved: {summary['models_resolved']}")
    for expert, info in summary["by_expert"].items():
        print(f"  {expert}: {info['model_used']} (fallback depth: {info['fallback_depth']})")
    
    # Per-player breakdown
    print(f"\n=== PER-PLAYER SCORE BREAKDOWN ===")
    player_names = {1: "Salah", 2: "Haaland", 3: "Son", 4: "Watkins", 5: "Isak", 6: "Saka", 7: "Palmer"}
    for pid in range(1, 8):
        print(f"\n  {player_names.get(pid, f'Player {pid}')}:")
        for exp in ["fixture", "form", "availability", "setpiece", "value"]:
            exp_data = expert_results[exp]["data"]
            if isinstance(exp_data, list):
                match = next((d for d in exp_data if d.get("player_id") == pid), None)
            elif isinstance(exp_data, dict) and "player_id" in exp_data:
                match = exp_data if exp_data.get("player_id") == pid else None
            elif isinstance(exp_data, dict) and exp_data.get("player_id") == pid:
                match = exp_data
            else:
                match = None
            if match:
                print(f"    {exp}: score={match.get('score', 0):.2f} conf={match.get('confidence', 0):.2f} | {str(match.get('rationale', ''))[:60]}")
            else:
                print(f"    {exp}: no data for player {pid}")
        # Nyx blended — validate structure
        nyx_data = expert_results["nyx"]["data"]
        if not isinstance(nyx_data, dict) or "player_adjustments" not in nyx_data or "blended_scores" not in nyx_data:
            print(f"    NYX: INVALID structure — expected {{player_adjustments, blended_scores}}, got: {type(nyx_data).__name__}")
            if isinstance(nyx_data, dict):
                print(f"      Keys present: {list(nyx_data.keys())}")
        else:
            adj = nyx_data["player_adjustments"].get(str(pid))
            blended = nyx_data["blended_scores"].get(str(pid))
            if adj is not None or blended is not None:
                print(f"    NYX: adjustment={adj:.2f} blended={blended:.2f}")
    
    # Save full output
    output = {
        "gw": gw,
        "call_summary": summary,
        "expert_results": {k: v["data"] for k, v in expert_results.items()},
        "models_used": {k: v["model_used"] for k, v in expert_results.items()}
    }
    with open(LOG_DIR / "openrouter_test_run.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull output saved to {LOG_DIR}/openrouter_test_run.json")

if __name__ == "__main__":
    main()