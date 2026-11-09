# PIPELINE — projection → gate → solver

Operating pipeline added 2026-09-23..26. One direction: data in, transfer plan out, no path from
live trading. Read-only by design: nothing here submits. Nothing here can flip `DRY_RUN`.

```
build_projection.py  →  gate.py  →  run/solve.py --team_id <ID>  →  human clicks plan
   (fixture-driven       (deterministic     (HiGHS MILP, read-only)
    projections from      critic + audit)
    public FPL API)
```

## 1. build_projection.py — the supply line

Rebuilds `data/solio.csv` from the public FPL API (no auth, no keys). 487 players × horizon
(default 5 GWs). Replaces the dead `free_xp_model.py` Understat stub, which silently produced the
flat mock CSV behind the GW5 disaster (see ADR-0001).

Model (documented, deliberately simple — a baseline to beat, not a truth engine):
- `base = points_per_game × minutes_frac` — season-to-date rate, minutes-weighted
- per-GW fixture factor: FDR vs the team's own season-average matchup, bounded ±35%
- clean-sheet/GK lift derived from the **difficulty the defense faces** (`(3 − diff) × cs_w`) —
  added 2026-09-26 because `strength_attack_home/away` are all **zero at season start**; the old
  CS-lift multiplied against dead fields and silently mis-weighted defenders
- attacking upside from `threat` (FPL goal-involvement metric), per-GW conversion `THREAT_TO_XGI`
- **positional ceilings**: G/D xPts capped at 9.0 post-fixture — a CB can still rank by fixture
  (soft 9.0 vs hard 6.77) but can never out-project a premium forward's mean. This fixed the
  Tarkowski-captain-over-Haaland inversion; horizon xPts 472 → 543 (see ADR-0002)
- retains unavailable players (`status == "i"` or `chance_of_playing_next_round < 50`) with 0 expected minutes and 0.3 xPts, so current squads can still be valued and those players can be transferred out
- output columns: `ID,Name,Pos,Value,Team,Penalties,Corners,FKs,Ownership,{gw}_Pts,{gw}_xMins`

Run: `python build_projection.py [horizon]` (backup of previous CSV kept only if no backup exists).

Known limits: S2D form is noisy early season; no xG source; injuries only as of generation time —
always rebuild fresh before a GW decision (the Oct 6 cron exists for exactly this).

## 2. gate.py — the critic gate

Deterministic pre-flight before any solve; writes a JSON trace to `logs/gate/` every run.

| Check | Catches |
|---|---|
| temporal flatline (per-player across-GW variance) | the GW5 mock signature: identical values every GW |
| within-GW flatline (across-player variance in one column) | fixture-within-one-column mock |
| discrimination (across-player std; team-code R²) | player-blind / fixture-only models |
| bootstrap + fixture cross-check | stale ids, wrong team mapping, nonexistent GW |
| completeness (with upstream artifacts) | players absent from research coverage |
| LLM arbitration (OpenRouter, when key present) | pattern-vs-domain disagreements; else confidence-halved soft-pass |

Exit codes: `0` = pass, `2` = reject. `pass_kind` in every trace: `clean-pass` /
`arbitrated-pass` / `soft-pass` — so a shrugged-through gate is auditable vs a confident one.

Proven: rejects GW5-era mock data (replay-verified); rejects adversarial "varies across GW,
identical within GW" fixture-only model; passes live data at confidence 0.8.

## 3. dev/data_parser.py tripwire — second lock, correct axis

`validate_fpl_projections()` is wired into every CSV reader (`read_solio`, `read_fplreview`,
`read_mikkel`, auto-latest). It crashes the solve (not warns) when:
1. **temporal flatline**: >50% of players have identical projections across all GWs — the actual
   GW5 failure axis (added after an initial spec check measured only across-player variance,
   which the real mock file passed at 4.07; see ADR-0001)
2. within-GW variance < 0.5 in the first points column
3. premium reference names (Haaland/Salah) absent → fuzzy ID matching likely failed

The solver can no longer run on garbage regardless of whether the gate was invoked. Defense in
depth: gate = supervisor with audit trail, tripwire = seatbelt with no opt-out.

## 4. run/solve.py — unchanged, read-only

HiGHS MILP over the horizon. `--team_id` only **reads** your public picks. Chip economics,
FTS, decay, locks/bans as per `data/user_settings.json` (config layering:
user_settings < `--config` < CLI < `runtime_options`). Submission path (`fpl_submit.py`)
exists but requires `DRY_RUN=false` + a session — login is dead (PingOne) and DRY_RUN stays
`true`. The plan is clicked by a human.

## Canonical run (weekly)

```bash
cd /home/benzie/projects/open-fpl-solver
python build_projection.py 5 \
  && python gate.py --gw <N> --csv data/solio.csv \
  && timeout 280 python run/solve.py --team_id <ID> --horizon 5
```
Gate exit ≠ 0 → stop, read `logs/gate/`, fix the data. Never skip the gate to "just see a run".

## Data files

- `data/solio.csv` — current live projections (gitignored by design; rebuild, don't commit)
- `data/solio_mock_backup.csv` — the GW5 mock file, kept as the tripwire's permanent test fixture
  (gitignored; regenerate with anything if lost — the test is only meaningful vs real flat mock)
- `logs/gate/*.json` — gate audit trail, committed
- `logs/gw_*_analysis.json` — expert-panel outputs (OpenRouter), committed as history
