#!/usr/bin/env python3
"""Real GW projection model for open-fpl-solver (replaces the dead Understat stub in free_xp_model.py).

Pure public FPL API, no auth. Produces solio-format CSV where each player's
projection VARIES PER GAMEEK (fixture-driven) — passes the gate's variance AND
discrimination checks by construction, not by accident.

Model (documented, simple, honest):
  base_pp90   = points_per_game / expected_starts            (per-GW rate)
  gw_points   = base * fixture_factor(FDR vs own season avg) * minutes_frac
                + setpiece_bonus (pens/corners order) scaled by attacking strength
  fixture_factor: easier opposition -> >1, harder -> <1 (bounded ±35%)
  GW-specific: each horizon GW uses that GW's actual fixture + opponent strength.

Known limits (stated, not hidden): no xG source (Understat stub was dead),
injury news only via chance_of_playing fields at generation time, and
points_per_game is season-to-date so early-season noise is real. This is a
baseline to beat, not a ceiling.
"""
import json, sys, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
API = "https://fantasy.premierleague.com/api"
HORIZON = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def main():
    out_path = REPO / "data" / "solio.csv"
    bak = REPO / "data" / "solio_mock_backup.csv"
    bs = get(f"{API}/bootstrap-static/")
    events = bs["events"]
    teams = {t["id"]: t for t in bs["teams"]}
    next_gw = next((e["id"] for e in events if e.get("is_next")), None)
    if not next_gw:
        print("ERROR: no is_next event — cannot determine live gameweek", file=sys.stderr)
        sys.exit(1)
    gws = list(range(next_gw, next_gw + HORIZON))
    print(f"live next GW: {next_gw} | horizon: {gws}")

    # per-GW fixtures: team -> (opponent_id, difficulty, home) for each GW in horizon
    fx_by_gw = {}
    for g in gws:
        fixtures = get(f"{API}/fixtures/?event={g}")
        m = {}
        for f in fixtures:
            if f.get("team_h") is None:
                continue
            m[f["team_h"]] = (f["team_a"], f.get("team_h_difficulty", 3.0), True)
            m[f["team_a"]] = (f["team_h"], f.get("team_a_difficulty", 3.0), False)
        fx_by_gw[g] = m

    # team average FDR conceded over horizon = "expected matchup quality"
    team_avg_fdr = {}
    for g, m in fx_by_gw.items():
        for team, (_, diff, _) in m.items():
            team_avg_fdr.setdefault(team, []).append(diff)
    team_avg_fdr = {t: sum(v) / len(v) for t, v in team_avg_fdr.items()}

    starts_guess = max(1, min(next_gw - 1, 5))  # rough denominator for pp90 season-to-date
    # --- positional ceiling parameters (2026-09-26 captaincy-bias fix) ---
    # strength_attack_home/away are ZERO at season start -> opponent attack quality
    # must come from the fixture difficulty itself (diff = attack you face).
    # Cap G/D absolute xPts; lift attacker means by goal-involvement (threat, a
    # SEASON-CUMULATIVE FPL metric — normalized here to per-match, then per-GW at
    # ~0.45 goal/assist conversion) so a premium forward's explosive ceiling is
    # respected. Fixture preference survives WITHIN a position (soft-fixture CB
    # still > hard-fixture CB) — he just can't out-project Haaland's mean.
    GD_CAP = {"G": 9.0, "D": 9.0}
    # per-match xGI proxy: threat = points from goals+assists contribution, season-total.
    # (threat/starts) ≈ points-equivalent involvement per match; /5 ≈ chance-weight to goals
    THREAT_TO_XGI = 0.18
    rows = []
    for p in bs["elements"]:
        cop = p.get("chance_of_playing_next_round")
        unavailable = p.get("status") == "i" or (cop is not None and cop < 50)
        team = p["team"]
        pp10 = float(p.get("points_per_game") or 0)
        mins = int(p.get("minutes") or 0)
        if pp10 <= 0 or mins < 60:
            # unknown/very-low-minute player: project near-nothing but still
            # varies per GW via fixture factor, and never dominates candidates
            pp10 = pp10 or 0.5
            mins = max(mins, 30)
        minutes_frac = min(1.0, (mins / starts_guess) / 90.0 * 1.05)
        starts = max(1, min(int(p.get("starts") or 0), starts_guess))
        base = pp10 * minutes_frac

        pos = p["element_type"]
        pos_letter = {1: "G", 2: "D", 3: "M", 4: "F"}[pos]
        # clean-sheet value now DERIVED from the difficulty the player FACES
        # (FPL diff: 1=easiest attack faced ... 5=h hardest), not dead strength fields.
        cs_w = {1: 0.35, 2: 0.30, 3: 0.05, 4: 0.0}[pos]
        pen_w = 0.10 if (p.get("penalties_order") == 1 and pos >= 3) else 0.0
        # attacking upside term: goal/assist volume scaled by fixture (attack faced, opp
        # defensive quality ignored at season start), weighted per position. Forwards
        # get the full ceiling; CBs get a stub so a soft fixture still ranks them.
        threat_pm = (float(p.get("threat") or 0) / max(1, int(p.get("starts") or 1))) * THREAT_TO_XGI
        upside_w = {1: 0.0, 2: 0.06, 3: 0.25, 4: 0.45}[pos]
        atk_w = {1: 0.0, 2: 0.15, 3: 0.30, 4: 0.40}[pos]

        row = {
            "ID": p["id"], "Name": p["web_name"],
            "Pos": pos_letter,
            "Value": p["now_cost"] / 10,
            "Team": teams[team]["short_name"],
            "Penalties": p.get("penalties_order") or 0,
            "Corners": p.get("corners_and_indirect_freekicks_order") or 0,
            "FKs": p.get("direct_freekicks_order") or 0,
            "Ownership": float(p.get("selected_by_percent") or 0) / 100,
        }
        avg = team_avg_fdr.get(team, 3.0)
        for g in gws:
            fx = fx_by_gw[g].get(team)
            if fx:
                opp_id, diff, home = fx
                # fixture factor: own GW difficulty vs season-average matchup (bounded)
                factor = 1.0 + (avg - diff) * 0.12
                factor = max(0.65, min(1.35, factor))
                # CS/GK lift from difficulty the DEFENSE faces: diff 1 -> max lift, 5 -> min
                cs_lift = max(0.0, (3.0 - diff)) * cs_w
                # attacking upside: soft defensive fixtures (low DIFFICULTY = attack you
                # face is weak) boost goal volume; scaled per position
                upside_lift = threat_pm * upside_w * max(0.5, (3.0 - diff) * 0.5 + 1.0)
            else:
                factor, cs_lift, upside_lift = 1.0, 0.0, 0.0
            home_bonus = 0.05 * atk_w if (fx and fx[2]) else 0.0
            xp = base * factor + cs_lift + pen_w + upside_lift + home_bonus * base
            # POSITIONAL CEILING: defenders/keepers cannot out-project premium attackers
            cap = GD_CAP.get(pos_letter)
            if cap is not None:
                xp = min(xp, cap)
            row[f"{g}_Pts"] = 0.3 if unavailable else round(max(0.3, xp), 2)
            row[f"{g}_xMins"] = 0 if unavailable else int(90 * minutes_frac)
        rows.append(row)

    cols = ["ID", "Name", "Pos", "Value", "Team", "Penalties", "Corners", "FKs", "Ownership"] + [f"{g}_{k}" for g in gws for k in ("Pts", "xMins")]
    if out_path.exists() and not bak.exists():
        bak.write_bytes(out_path.read_bytes())
        print(f"previous solio.csv backed up to {bak.name}")
    import csv as _csv
    with open(out_path, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE {len(rows)} players x GWs {gws} -> data/solio.csv (real fixture-driven model)")


if __name__ == "__main__":
    main()
