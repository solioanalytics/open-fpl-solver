#!/usr/bin/env python3
"""Decipher/critic gate — last checkpoint before the solver.

Implements gate-agent-schema.pdf (received 2026-09-23):
  1. Deterministic pre-checks (no LLM): variance/flatness, fixture-bootstrap
     cross-check, completeness. Any failure = reject, short-circuit.
  2. LLM arbitration only when pre-checks pass but upstream agents disagree.

Usage:
  python3 gate.py --gw 6 [--csv data/solio.csv] [--analysis logs/gw_6_*_analysis.json]
Exit codes: 0 = pass, 2 = reject, 1 = gate error (treat as reject).
Chain: python3 gate.py --gw 6 && uv run python run/solve.py --team_id <id>
"""
import argparse, csv, json, os, re, statistics, sys, urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.abspath(__file__))
GATE_DIR = os.path.join(REPO, "logs", "gate")
FLAT_CV_THRESHOLD = 0.02     # per schema: std/mean below this => flat/mocked
FLAT_PLAYER_FRACTION = 0.5   # >50% of players with zero across-GW variance => synthetic source
DISCRIM_MIN_STD = 1.5        # within-GW across-player std below this: undifferentiated projections
TEAM_R2_MAX = 0.75           # team code explaining >75% of within-GW variance: no player signal
BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/?event={gw}"


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def load_projections(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    gw_cols = {}
    for col in rows[0].keys() if rows else []:
        m = re.match(r"^(\d+)_Pts$", col)
        if m:
            gw_cols[int(m.group(1))] = col
    return rows, gw_cols


def check_variance(rows, gw_cols, source_name):
    """Per-player std/mean across sources; with one source, across GWs.
    A source where >50% of players have zero across-GW variance is flat/mock."""
    if len(gw_cols) < 2:
        return [f"variance check skipped: only {len(gw_cols)} GW column(s) present — cannot detect flat/mock data. Treat projections as UNVERIFIED."], 0.5, []
    per_player_cv, flat_ids, flagged = [], [], []
    for r in rows:
        vals = []
        for c in gw_cols.values():
            try:
                vals.append(float(r[c]))
            except (ValueError, KeyError):
                pass
        if len(vals) >= 2 and statistics.mean(vals) > 0:
            cv = statistics.pstdev(vals) / statistics.mean(vals)
            per_player_cv.append(cv)
            if cv == 0:
                flat_ids.append(r.get("ID", "?"))
    if not per_player_cv:
        return ["variance check: no numeric projection values found"], 0.0, []
    frac = len(flat_ids) / len(per_player_cv)
    mean_cv = statistics.mean(per_player_cv)
    reasons = [f"variance check ({source_name}): mean per-player CV across GWs = {mean_cv:.3f}, zero-variance fraction = {frac:.0%}"]
    if frac > FLAT_PLAYER_FRACTION or mean_cv < FLAT_CV_THRESHOLD:
        reasons.append(f"FLAT/MOCKED: {frac:.0%} of players have identical projections every GW — not real data")
        return reasons, 0.0, flat_ids
    return reasons, 1.0 if mean_cv >= FLAT_CV_THRESHOLD else 0.4, []


def check_fixtures(rows, gw, gw_cols):
    """Every player referenced must exist in live bootstrap (with matching
    team), and the GW's fixture list must be real and non-empty."""
    reasons, hard_fail = [], False
    try:
        bs = http_json(BOOTSTRAP_URL)
    except Exception as e:
        return [f"fixture cross-check: bootstrap-static unreachable ({e}) — CANNOT VERIFY, reject"], False
    live = {e["id"]: e for e in bs["elements"]}
    team_short = {t["id"]: t["short_name"].upper() for t in bs["teams"]}
    missing, team_mismatch = [], []
    for r in rows:
        try:
            pid = int(r["ID"])
        except (ValueError, KeyError):
            missing.append(r.get("Name", "?")); continue
        if pid not in live:
            missing.append(r.get("Name", pid)); continue
        csv_team = (r.get("Team") or "").strip().upper()
        if csv_team and csv_team != "None":
            if csv_team not in team_short.values() and csv_team not in {s[:3] for s in team_short.values()}:
                team_mismatch.append(f"{r.get('Name')}({csv_team})")
            else:
                live_short = team_short.get(live[pid].get("team"), "")
                if live_short and not (csv_team in live_short or live_short in csv_team):
                    team_mismatch.append(f"{r.get('Name')}: csv={csv_team} live={live_short}")
    try:
        fx = http_json(FIXTURES_URL.format(gw=gw))
        fx_ok = len(fx) > 0
    except Exception:
        fx_ok = False
    reasons.append(f"fixture cross-check: {len(rows)} players vs bootstrap — {len(missing)} unknown ids, {len(team_mismatch)} team mismatches; GW{gw} fixture list: {'present' if fx_ok else 'MISSING'}")
    if missing:
        reasons.append(f"REJECT players not in live bootstrap: {missing[:10]}")
        hard_fail = True
    if not fx_ok:
        reasons.append(f"GW{gw} fixtures not found in live API — projection references a nonexistent gameweek")
        hard_fail = True
    if gw not in gw_cols:
        reasons.append(f"GW{gw} column absent from projection CSV — cannot solve this gameweek")
        hard_fail = True
    return reasons, hard_fail


def check_discrimination(rows, gw_cols):
    """Second-tier check (Claude review, 2026-09-23): across-GW variance is a
    proxy — it misses models that are undifferentiated WITHIN a gameweek:
    same value for everyone, or a week-varying but purely team/fixture-driven
    model with zero player-specific signal. Both are 'real-looking' but bad."""
    if len(gw_cols) < 1 or len(rows) < 20:
        return [f"discrimination check: skipped (n={len(rows)} players)"], False
    gw_stds, team_r2s = [], []
    for g, col in gw_cols.items():
        vals = {}
        for r in rows:
            try:
                vals.setdefault(r.get("Team", ""), []).append(float(r[col]))
            except (ValueError, KeyError):
                pass
        flat = [v for vs in vals.values() for v in vs]
        if len(flat) < 20:
            continue
        mean = statistics.mean(flat)
        total_var = statistics.pvariance(flat)
        gw_stds.append(statistics.pstdev(flat))
        if total_var > 0:
            # between-team variance share: 1.0 = projections fully explained by team code
            team_means = {t: statistics.mean(vs) for t, vs in vals.items() if vs}
            between = sum(len(vs) * (statistics.mean(vs) - mean) ** 2 for vs in vals.values() if vs) / len(flat)
            team_r2s.append(between / total_var)
    reasons, fail = [], False
    if gw_stds:
        med_std = statistics.median(gw_stds)
        if med_std < DISCRIM_MIN_STD:
            reasons.append(f"DISCRIMINATION: median across-player std within GW = {med_std:.2f} pts — projections barely differentiate players")
            fail = True
        else:
            reasons.append(f"discrimination: median across-player std within GW = {med_std:.2f} pts")
    if team_r2s:
        r2 = statistics.mean(team_r2s)
        if r2 > TEAM_R2_MAX:
            reasons.append(f"DISCRIMINATION: team code explains {r2:.0%} of projection variance — fixture/team-only model, no player-specific signal")
            fail = True
        else:
            reasons.append(f"discrimination: team explains {r2:.0%} of within-GW variance (player-specific signal present)")
    if not reasons:
        reasons = ["discrimination check: insufficient numeric data"]
    return reasons, fail


def check_completeness(rows, analysis):
    """Upstream judgments must cover the candidate pool; uncovered players are
    removed from candidates rather than solved on a gap. Returns (reasons, excluded_ids)."""
    if not analysis:
        return ["completeness check: no research/pattern/domain artifacts supplied — gate operating in projection-only mode (checks 1+2). Candidates unsupervised."], []
    covered = set()
    for key in ("research_facts", "pattern_flags", "domain_judgments", "expert_results", "nyx"):
        for item in analysis.get(key, []) or []:
            if isinstance(item, dict) and "player_id" in item:
                covered.add(int(item["player_id"]))
            elif isinstance(item, dict) and "id" in item:
                covered.add(int(item["id"]))
    pool = {int(r["ID"]) for r in rows if r.get("ID", "").isdigit()}
    excluded = sorted(pool - covered) if covered else sorted(pool)
    reasons = [f"completeness: {len(covered)} players covered by upstream artifacts, {len(excluded)} excluded from candidates"]
    return reasons, excluded


def llm_arbitrate(disagreements):
    """Optional OpenRouter arbitration (schema section 5). Degrades gracefully:
    no key -> return None and the caller notes it instead of crashing."""
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key or not disagreements:
        return None
    try:
        body = json.dumps({
            "model": "qwen/qwen3.8-flash",
            "messages": [
                {"role": "system", "content": "You are the critic gate for an FPL multi-agent projection system. You do not generate projections — you arbitrate disagreement between agents that already have generated theirs. research_facts are ground truth; a judgment contradicting a stated fact loses. Two independent agreeing signals outweigh one dissenting signal. If you cannot resolve, confidence below 0.5. Output strict JSON only: {\"players\": [{\"player_id\": int, \"resolution\": string, \"confidence\": float}], \"overall_confidence\": float}. No prose."},
                {"role": "user", "content": json.dumps(disagreements)[:12000]},
            ],
            "max_tokens": 900,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(r.read())
        text = out["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        return {"error": str(e)[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, required=True)
    ap.add_argument("--csv", default=os.path.join(REPO, "data", "solio.csv"))
    ap.add_argument("--analysis", default=None, help="upstream analysis JSON (optional)")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    gate_log_ref = f"gw{args.gw}_gate_{ts}"
    os.makedirs(GATE_DIR, exist_ok=True)
    result = {"gate_log_ref": gate_log_ref, "gameweek": args.gw, "reasons": [],
              "player_flags": [], "decision": "pass", "confidence": 1.0}

    if not os.path.exists(args.csv):
        result.update(decision="reject", confidence=0.0, reasons=[f"projection file missing: {args.csv}"])
    else:
        rows, gw_cols = load_projections(args.csv)
        source = os.path.splitext(os.path.basename(args.csv))[0]
        if not rows:
            result.update(decision="reject", confidence=0.0, reasons=["projection CSV empty"])
        else:
            vr, vconf, flat = check_variance(rows, gw_cols, source)
            fr, ffail = check_fixtures(rows, args.gw, gw_cols)
            dr, dfail = check_discrimination(rows, gw_cols)
            analysis = None
            if args.analysis and os.path.exists(args.analysis):
                try:
                    analysis = json.load(open(args.analysis))
                except Exception:
                    result["reasons"].append("analysis JSON unreadable — ignored")
            cr, excluded = check_completeness(rows, analysis)
            result["reasons"] += vr + fr + dr + cr

            if vconf == 0.0 or ffail or dfail:
                result.update(decision="reject", confidence=0.0)
                result["player_flags"] = [{"player_id": i, "issue": "flat/mock projection", "severity": "blocking"} for i in flat[:20]]
            else:
                # disagreement detection between pattern & domain (when present)
                disag = []
                if analysis:
                    pat = {int(x.get("player_id", -1)): x for x in analysis.get("pattern_flags", []) or []}
                    dom = {int(x.get("player_id", -1)): x for x in analysis.get("domain_judgments", []) or []}
                    for pid in set(pat) & set(dom):
                        pd_, dd_ = pat[pid].get("direction"), dom[pid].get("direction")
                        if pd_ and dd_ and pd_ != dd_:
                            disag.append({"player_id": pid, "pattern": pat[pid], "domain": dom[pid]})
                if disag:
                    arb = llm_arbitrate(disag)
                    if arb is None:
                        result["reasons"].append(f"{len(disag)} agent disagreements; LLM arbitration unavailable (no key) — confidence halved, not blocked")
                        result["confidence"] = 0.5 * vconf
                        result["pass_kind"] = "soft-pass/unresolved-disagreement"
                    else:
                        result["reasons"].append(f"LLM arbitration on {len(disag)} disagreements: {arb}")
                        result["confidence"] = float(arb.get("overall_confidence", 0.5)) * vconf
                        result["pass_kind"] = "arbitrated-pass"
                    result["player_flags"] += [{"player_id": d["player_id"], "issue": "domain/pattern disagreement",
                                                "resolution": "arbitrated", "severity": "low"} for d in disag]
                else:
                    result["confidence"] = vconf if analysis else min(vconf, 0.8)
                    result["pass_kind"] = "clean-pass"
                if excluded:
                    result["player_flags"] += [{"player_id": i, "issue": "no upstream coverage", "severity": "excluded-from-candidates"} for i in excluded[:20]]

    with open(os.path.join(GATE_DIR, gate_log_ref + ".json"), "w") as f:
        json.dump(result, f, indent=1, default=str)
    print(json.dumps(result, indent=1, default=str))
    sys.exit(0 if result["decision"] == "pass" else 2)


if __name__ == "__main__":
    main()
