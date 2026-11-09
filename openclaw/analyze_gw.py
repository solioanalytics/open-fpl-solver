#!/usr/bin/env python3
"""Weekly FPL analyzer: refresh projections, run 5 experts + Nyx, print ranked table.

Usage:
    python openclaw/analyze_gw.py                  # analyze with existing solio.csv
    python openclaw/analyze_gw.py --refresh        # re-fetch FPL + Understat first
    python openclaw/analyze_gw.py --refresh --players 30
    python openclaw/analyze_gw.py --players 50

Outputs:
    logs/gw_<N>_analysis.json   full structured results
    logs/gw_<N>_summary.md     human-readable ranked summary
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from openclaw.openrouter_client import OpenRouterClient

LOG_DIR = Path(os.getenv("FPL_LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)
SOLIO_PATH = Path("data/solio.csv")


# ---------------------------------------------------------------------------
# Profile loading (same logic as run_real_test.py)
# ---------------------------------------------------------------------------
def load_profiles(path="openclaw/agent_profiles.conf"):
    profiles = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if ":" in line and "primary" in line:
                    parts = line.split(":")
                    expert = parts[0].strip()
                    rest = ":".join(parts[1:]).strip()
                    primary = rest.split("primary=")[1].split(",")[0].strip()
                    fallbacks_str = rest.split("fallbacks=[")[1].split("]")[0].strip()
                    fallbacks = [fb.strip() for fb in fallbacks_str.split(",")]
                    profiles[expert] = {"primary": primary, "fallbacks": fallbacks}
    return profiles


# ---------------------------------------------------------------------------
# Data loading + expert-input construction from solio.csv
# ---------------------------------------------------------------------------
def load_sOLIO(max_players=50):
    """Load solio.csv, pick top players by next-GW projected points.

    Returns (df, next_gw_col, players_list, gw) where players_list is a list of
    dicts with keys: player_id, name, team, pos, price, next_gw_points,
    projected_minutes, ev_per_m, penalties, corners, fks, ownership.
    """
    if not SOLIO_PATH.exists():
        print(f"ERROR: {SOLIO_PATH} not found. Run with --refresh or fetch manually.")
        sys.exit(1)

    df = pd.read_csv(SOLIO_PATH)
    gw_cols = [c for c in df.columns if c.endswith("_Pts")]
    if not gw_cols:
        print("ERROR: no _Pts columns in solio.csv")
        sys.exit(1)

    next_gw_col = gw_cols[0]  # e.g. "5_Pts"
    gw = int(next_gw_col.split("_")[0])
    xmins_col = next_gw_col.replace("_Pts", "_xMins")

    df[next_gw_col] = pd.to_numeric(df[next_gw_col], errors="coerce")
    if xmins_col in df.columns:
        df[xmins_col] = pd.to_numeric(df[xmins_col], errors="coerce")
    df[xmins_col] = df[xmins_col].fillna(90)

    df = df.dropna(subset=[next_gw_col])
    df = df.sort_values(next_gw_col, ascending=False).head(max_players)

    players = []
    for _, row in df.iterrows():
        price = float(row.get("Value", 0) or 0)
        pts = float(row[next_gw_col])
        minutes = float(row.get(xmins_col, 90) or 90)
        players.append({
            "player_id": int(row["ID"]),
            "name": str(row["Name"]),
            "team": str(row["Team"]),
            "pos": str(row.get("Pos", "")),
            "price": price,
            "next_gw_points": pts,
            "projected_minutes": minutes,
            "ev_per_m": round(pts / max(0.1, price), 3) if price > 0 else 0.0,
            # Setpiece order fields from FPL bootstrap (0 = no involvement)
            "penalties": int(row.get("Penalties", 0) or 0),
            "corners": int(row.get("Corners", 0) or 0),
            "fks": int(row.get("FKs", 0) or 0),
            # Team ownership fraction (0-1)
            "ownership": float(row.get("Ownership", 0) or 0),
        })

    return df, next_gw_col, players, gw


def build_expert_inputs(players):
    """Convert player list into the 5 expert input structures.

    Fixture/availability/setpiece are approximated from solio projections
    (real data for those dimensions would need FPL API + injury scraping).
    Form uses projected points as a proxy. Value uses ev_per_m.
    """
    fixture = []
    form = []
    availability = []
    setpiece = []
    value = []

    for p in players:
        pid = p["player_id"]
        pts = p["next_gw_points"]
        price = p["price"]
        minutes = p["projected_minutes"]

        # Fixture: normalized projected points as difficulty proxy
        fixture.append({
            "player_id": pid,
            "name": p["name"],
            "team": p["team"],
            "opp": "",
            "venue": "H",
            "difficulty": 3.0,
            "score": round(max(0.0, min(1.0, pts / 15.0)), 3),
            "confidence": 0.7,
            "rationale": f"Projected {pts:.1f} pts; difficulty approximated from projection.",
        })

        # Form: projected points as form proxy
        form.append({
            "player_id": pid,
            "recent_points": [round(pts, 2)] * 5,
            "avg_mins": int(minutes),
            "score": round(max(0.0, min(1.0, pts / 15.0)), 3),
            "confidence": 0.75,
            "rationale": f"Projection {pts:.1f} pts as form proxy; {int(minutes)} proj. minutes.",
        })

        # Availability: inferred from projected minutes
        inj_risk = 0.05 if minutes >= 60 else 0.35
        avail_score = 0.95 if minutes >= 60 else 0.55
        availability.append({
            "player_id": pid,
            "status": "fit" if minutes >= 60 else "uncertain",
            "injury_risk": inj_risk,
            "suspension": False,
            "score": avail_score,
            "confidence": 0.8,
            "rationale": f"Projected {int(minutes)} mins → {'likely' if minutes >= 60 else 'uncertain'} start.",
        })

        # Setpiece: score from FPL setpiece order data (penalties, corners, FKs).
        # Order 1 = primary taker (high value), higher numbers = lower priority.
        # Score = function of involvement breadth + order quality, normalized 0-1.
        pen = int(p.get("penalties", 0) or 0)
        cor = int(p.get("corners", 0) or 0)
        fk = int(p.get("fks", 0) or 0)
        has_sp = pen > 0 or cor > 0 or fk > 0
        if not has_sp:
            sp_score = 0.10  # low baseline — no setpiece involvement
            sp_conf = 0.7
            sp_rationale = "No setpiece involvement (no penalties, corners, or FKs)."
        else:
            # Order 1 = primary (best), 2 = secondary, etc. Lower is better.
            # Score: primary takers get 0.7-1.0, secondary 0.3-0.6, dependent on breadth
            pen_score = max(0.0, 1.0 - (pen - 1) * 0.15) if pen > 0 else 0.0
            cor_score = max(0.0, 1.0 - (cor - 1) * 0.12) if cor > 0 else 0.0
            fk_score = max(0.0, 1.0 - (fk - 1) * 0.12) if fk > 0 else 0.0
            # Weighted: penalties most valuable (direct pts), corners/FKs for assist upside
            sp_score = round(min(1.0, pen_score * 0.5 + cor_score * 0.25 + fk_score * 0.25), 3)
            sp_conf = round(min(0.95, 0.5 + has_sp * 0.1 + (1 if pen > 0 else 0) * 0.2), 2)
            parts = []
            if pen > 0: parts.append(f"penalties (order {pen})")
            if cor > 0: parts.append(f"corners (order {cor})")
            if fk > 0: parts.append(f"FKs (order {fk})")
            sp_rationale = f"Setpiece involvement: {', '.join(parts)}."
        setpiece.append({
            "player_id": pid,
            "pen_taker": pen > 0,
            "corners": cor > 0,
            "fks": fk > 0,
            "pen_order": pen,
            "corners_order": cor,
            "fk_order": fk,
            "opp_concedes_setpiece": 0.15,
            "score": sp_score,
            "confidence": sp_conf,
            "rationale": sp_rationale,
        })

        # Value: ev_per_m with ownership context
        ev = p["ev_per_m"]
        own = float(p.get("ownership", 0) or 0)
        value.append({
            "player_id": pid,
            "name": p["name"],
            "price": price,
            "ownership": round(own, 3),
            "ev_per_m": ev,
            "score": round(max(0.0, min(1.0, ev / 1.5)), 3),
            "confidence": round(min(0.9, 0.6 + (1 if own > 0.1 else 0) * 0.2), 2),
            "rationale": f"EV/m = {ev:.2f} from projection; ownership {own*100:.1f}%.",
        })

    return {
        "fixture": fixture,
        "form": form,
        "availability": availability,
        "setpiece": setpiece,
        "value": value,
    }


def build_expert_prompts(data):
    """One batched prompt per expert."""
    return {
        "fixture": f"""Score each player's fixture difficulty for the upcoming gameweek.
Input: {json.dumps(data['fixture'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text.""",
        "form": f"""Score each player's current form based on recent points and minutes.
Input: {json.dumps(data['form'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text.""",
        "availability": f"""Score each player's availability risk (1 = certain start, 0 = likely out).
Input: {json.dumps(data['availability'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text.""",
        "setpiece": f"""Score each player's setpiece advantage for this gameweek.
Input: {json.dumps(data['setpiece'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text.""",
        "value": f"""Score each player's value (EV per million) for this gameweek.
Input: {json.dumps(data['value'])}
Return ONLY a JSON array of objects: [{{"player_id": int, "score": float (0-1), "confidence": float (0-1), "rationale": "str"}}]. No other text.""",
    }


# ---------------------------------------------------------------------------
# Nyx blending (same logic as run_real_test.py)
# ---------------------------------------------------------------------------
def blend_nyx(expert_results, nyx_data, nyx_model):
    """Compute blended_scores = avg of 5 expert scores + Nyx adjustment."""
    if not isinstance(nyx_data, dict) or "player_adjustments" not in nyx_data:
        return {}
    player_ids = set()
    for exp in ["fixture", "form", "availability", "setpiece", "value"]:
        ed = expert_results[exp]
        if isinstance(ed, list):
            player_ids.update(d["player_id"] for d in ed if "player_id" in d)
    blended = {}
    for pid in sorted(player_ids):
        scores = []
        for exp in ["fixture", "form", "availability", "setpiece", "value"]:
            ed = expert_results[exp]
            if isinstance(ed, list):
                m = next((d for d in ed if d.get("player_id") == pid), None)
                if m and "score" in m:
                    scores.append(m["score"])
        if scores:
            avg = sum(scores) / len(scores)
            adj = nyx_data["player_adjustments"].get(str(pid), 0.0)
            blended[str(pid)] = round(max(0.0, min(1.0, avg + adj)), 4)
    return blended


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def print_ranked_table(players, expert_results, blended, nyx_data, gw):
    """Print a ranked per-player table sorted by blended score."""
    name_by_id = {p["player_id"]: p["name"] for p in players}
    team_by_id = {p["player_id"]: p["team"] for p in players}
    pos_by_id = {p["player_id"]: p["pos"] for p in players}
    price_by_id = {p["player_id"]: p["price"] for p in players}
    pts_by_id = {p["player_id"]: p["next_gw_points"] for p in players}

    rows = []
    for pid_str, bs in blended.items():
        pid = int(pid_str)
        adj = nyx_data.get("player_adjustments", {}).get(pid_str, 0.0) if isinstance(nyx_data, dict) else 0.0
        rows.append((pid, bs, adj))

    rows.sort(key=lambda r: r[1], reverse=True)

    print(f"\n{'='*90}")
    print(f"  RANKED PLAYERS — GW {gw} (by blended score)")
    print(f"{'='*90}")
    if not rows:
        print("  (no blended scores available)")
        return
    hdr = f"  {'#':>2}  {'Player':<14} {'Team':<5} {'Pos':<3} {'Price':>5} {'ProjPts':>7} {'Blended':>7} {'NyxAdj':>6}"
    print(hdr)
    print(f"  {'-'*2}  {'-'*14} {'-'*5} {'-'*3} {'-'*5} {'-'*7} {'-'*7} {'-'*6}")
    for rank, (pid, bs, adj) in enumerate(rows, 1):
        name = name_by_id.get(pid, f"P{pid}")
        team = team_by_id.get(pid, "")
        pos = pos_by_id.get(pid, "")
        price = price_by_id.get(pid, 0)
        pts = pts_by_id.get(pid, 0)
        print(f"  {rank:>2}  {name:<14} {team:<5} {pos:<3} {price:>5.1f} {pts:>7.1f} {bs:>7.3f} {adj:>+6.2f}")

    print(f"\n  Legend: Blended = avg(5 expert scores) + Nyx adjustment, clamped 0–1.")
    print(f"  NyxAdj +ve = Nyx upvotes, -ve = Nyx downvotes.")


def print_expert_breakdown(players, expert_results, blended, gw):
    """Print per-player per-expert detail for the top 15."""
    name_by_id = {p["player_id"]: p["name"] for p in players}
    top_ids = sorted(blended, key=lambda k: blended[k], reverse=True)[:15]

    print(f"\n{'='*90}")
    print(f"  PER-EXPERT DETAIL — top 15 by blended score (GW {gw})")
    print(f"{'='*90}")
    for pid_str in top_ids:
        pid = int(pid_str)
        name = name_by_id.get(pid, f"P{pid}")
        print(f"\n  --- {name} (ID {pid}) | blended={blended[pid_str]:.3f} ---")
        for exp in ["fixture", "form", "availability", "setpiece", "value"]:
            ed = expert_results[exp]
            if isinstance(ed, list):
                m = next((d for d in ed if d.get("player_id") == pid), None)
            else:
                m = None
            if m and "score" in m:
                rationale = str(m.get("rationale", ""))[:70]
                print(f"    {exp:<12}: score={m['score']:.2f} conf={m.get('confidence', 0):.2f} | {rationale}")
            else:
                print(f"    {exp:<12}: no data")


def save_json(players, expert_results, blended, nyx_data, nyx_model, gw, profiles_used, output_path):
    """Save full structured results."""
    output = {
        "gw": gw,
        "generated": pd.Timestamp.now().isoformat(),
        "call_summary": {
            "total_calls": len(profiles_used),
            "by_expert": {e: {"model_used": m, "fallback_depth": 0} for e, m in profiles_used.items()},
            "models_resolved": list(profiles_used.values()),
        },
        "players_analyzed": len(players),
        "expert_results": {k: v for k, v in expert_results.items()},
        "nyx": {
            "player_adjustments": nyx_data.get("player_adjustments", {}) if isinstance(nyx_data, dict) else {},
            "blended_scores": blended,
            "model_used": nyx_model,
        },
        "models_used": profiles_used,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull results → {output_path}")


def save_markdown(players, expert_results, blended, nyx_data, gw, md_path):
    """Save a human-readable markdown summary."""
    name_by_id = {p["player_id"]: p["name"] for p in players}
    team_by_id = {p["player_id"]: p["team"] for p in players}
    pos_by_id = {p["player_id"]: p["pos"] for p in players}
    price_by_id = {p["player_id"]: p["price"] for p in players}
    pts_by_id = {p["player_id"]: p["next_gw_points"] for p in players}

    rows = []
    for pid_str, bs in blended.items():
        pid = int(pid_str)
        adj = nyx_data.get("player_adjustments", {}).get(pid_str, 0.0) if isinstance(nyx_data, dict) else 0.0
        rows.append((pid, bs, adj))
    rows.sort(key=lambda r: r[1], reverse=True)

    lines = []
    lines.append(f"# GW {gw} Analyzer — Ranked Player Summary")
    lines.append(f"")
    lines.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Players analyzed: {len(players)}")
    lines.append(f"")
    lines.append(f"## Ranked Table")
    lines.append(f"")
    lines.append(f"| # | Player | Team | Pos | Price | Proj Pts | Blended | Nyx Adj | Setpiece |")
    lines.append(f"|---|--------|------|-----|------:|---------:|--------:|-------:|--------:|")
    for rank, (pid, bs, adj) in enumerate(rows, 1):
        name = name_by_id.get(pid, f"P{pid}")
        team = team_by_id.get(pid, "")
        pos = pos_by_id.get(pid, "")
        price = price_by_id.get(pid, 0)
        pts = pts_by_id.get(pid, 0)
        sp_data = next((d for d in expert_results["setpiece"] if d.get("player_id") == pid), None)
        sp_str = f"{sp_data.get('score', 0):.2f}" if sp_data else "—"
        lines.append(f"| {rank} | {name} | {team} | {pos} | {price:.1f} | {pts:.1f} | {bs:.3f} | {adj:+.2f} | {sp_str} |")
    lines.append(f"")
    lines.append(f"## Per-Expert Detail (top 15)")
    lines.append(f"")
    for pid_str in sorted(blended, key=lambda k: blended[k], reverse=True)[:15]:
        pid = int(pid_str)
        name = name_by_id.get(pid, f"P{pid}")
        lines.append(f"### {name} (ID {pid}) — blended={blended[pid_str]:.3f}")
        lines.append(f"")
        for exp in ["fixture", "form", "availability", "setpiece", "value"]:
            ed = expert_results[exp]
            if isinstance(ed, list):
                m = next((d for d in ed if d.get("player_id") == pid), None)
            else:
                m = None
            if m and "score" in m:
                rat = str(m.get("rationale", ""))
                lines.append(f"- **{exp}**: score={m['score']:.2f} conf={m.get('confidence', 0):.2f} — {rat}")
            else:
                lines.append(f"- **{exp}**: no data")
        lines.append(f"")
    lines.append(f"---")
    lines.append(f"Blended = avg(5 expert scores) + Nyx adjustment, clamped 0–1.")
    lines.append(f"NyxAdj +ve = Nyx upvotes, -ve = Nyx downvotes.")
    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Markdown summary → {md_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Weekly FPL analyzer")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch FPL + Understat projections")
    parser.add_argument("--players", type=int, default=50, help="Number of top players to analyze (default 50)")
    parser.add_argument("--use-paid", action="store_true",
                        help="Use paid models for the 5 experts (bypasses free-tier 50/day cap)")
    args = parser.parse_args()

    # Step 1: optionally refresh projection data
    if args.refresh:
        print("=== Refreshing projections ===")
        try:
            from free_xp_model import build_xp_csv
            build_xp_csv(str(SOLIO_PATH))
        except Exception as e:
            print(f"WARNING: projection refresh failed ({e}). Using existing solio.csv if present.")
        print()

    # Step 2: load data
    print("=== Loading projections ===")
    df, next_gw_col, players, gw = load_sOLIO(max_players=args.players)
    print(f"GW {gw} | {len(players)} players loaded from {SOLIO_PATH}")
    print(f"Top 5 by projected points:")
    for p in players[:5]:
        print(f"  {p['name']:<14} {p['team']:<5} {p['pos']:<3} {p['price']:>5.1f}  {p['next_gw_points']:.1f} pts  EV/m={p['ev_per_m']:.2f}")
    print()

    # Step 3: build expert inputs + prompts
    print("=== Building expert inputs ===")
    data = build_expert_inputs(players)
    prompts = build_expert_prompts(data)
    print(f"  {len(players)} players → 5 expert prompts")
    print()

    # Step 4: load profiles + run
    print("=== Loading agent profiles ===")
    profiles = load_profiles()
    print(f"  Profiles: {list(profiles.keys())}")
    print()

    client = OpenRouterClient()
    expert_results = {}

    paid_override = args.use_paid
    # Paid-model overrides: bypasses free-tier 50/day cap when it's exhausted.
    # NOTE: google/gemini-3.0-flash was rejected by OpenRouter as invalid; use
    # openai/gpt-4o-mini which was verified working as a paid model.
    PAID_MODELS = {
        "fixture": ("openai/gpt-4o-mini", []),
        "form": ("openai/gpt-4o-mini", []),
        "availability": ("openai/gpt-4o-mini", []),
        "setpiece": ("openai/gpt-4o-mini", []),
        "value": ("openai/gpt-4o-mini", []),
    }

    print(f"=== Calling 5 experts (GW {gw}) ===")
    for expert_name, prompt in prompts.items():
        profile = profiles.get(expert_name, {})
        if paid_override and expert_name in PAID_MODELS:
            primary, fallbacks = PAID_MODELS[expert_name]
            print(f"  Using paid override for {expert_name}: {primary}")
        else:
            primary = profile.get("primary", "openrouter/free")
            fallbacks = profile.get("fallbacks", [])
        try:
            result, model_used = client.call_expert(
                expert_name, prompt,
                primary,
                fallbacks,
                gw,
                min_rows=max(5, len(players) // 5),
            )
            expert_results[expert_name] = result
            print(f"  ✓ {expert_name} -> {model_used}")
        except RuntimeError as e:
            print(f"  !! {expert_name} exhausted all models ({e}); continuing without it")
            expert_results[expert_name] = []
    print()

    print(f"=== Calling Nyx orchestrator (GW {gw}) ===")
    expert_data_for_nyx = {k: v for k, v in expert_results.items()}
    nyx_profile = profiles.get("nyx", {})
    nyx_primary = nyx_profile.get("primary", "openai/gpt-4o-mini")
    nyx_fallbacks = nyx_profile.get("fallbacks", [])
    nyx_result, nyx_model = client.call_nyx(
        expert_data_for_nyx,
        nyx_primary,
        nyx_fallbacks,
        gw,
    )
    expert_results["nyx"] = nyx_result
    print(f"  ✓ nyx -> {nyx_model}")
    print()

    # Step 5: normalize + blend
    # A degraded model call can return a single {id: score} pair where a full
    # adjustment map was expected. Merge it over a neutral zero-map so blending
    # proceeds; log exactly how many real adjustments survived.
    if isinstance(nyx_result, dict) and "player_adjustments" not in nyx_result:
        valid = {k: v for k, v in nyx_result.items() if isinstance(v, (int, float))}
        all_ids = {str(p["player_id"]) for p in players}
        if valid and len(valid) < len(all_ids) and all(k in all_ids for k in valid):
            print(f"  WARNING: Nyx returned partial adjustments ({len(valid)}/{len(all_ids)}); filling rest with 0.0")
            nyx_result = {"player_adjustments": {pid: float(valid.get(pid, 0.0)) for pid in all_ids}}

    blended = blend_nyx(expert_results, nyx_result, nyx_model)
    if not blended:
        print("ERROR: Nyx returned no player_adjustments — cannot compute blended scores.")
        print(f"  Nyx raw: {nyx_result}")
        sys.exit(1)

    # Step 6: output
    print_ranked_table(players, expert_results, blended, nyx_result, gw)
    print_expert_breakdown(players, expert_results, blended, gw)

    # Save artifacts
    profiles_used = {e["expert"]: e["model_used"] for e in client.call_log}
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    json_path = LOG_DIR / f"gw_{gw}_{ts}_analysis.json"
    md_path = LOG_DIR / f"gw_{gw}_{ts}_summary.md"
    save_json(players, expert_results, blended, nyx_result, nyx_model, gw, profiles_used, json_path)
    save_markdown(players, expert_results, blended, nyx_result, gw, md_path)

    print(f"\nDone. GW {gw} analyzed — {len(players)} players, {len(blended)} scored.")


if __name__ == "__main__":
    main()
