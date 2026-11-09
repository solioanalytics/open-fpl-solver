# ADR-0002: Positional xPts ceilings in projections

Date: 2026-09-26
Status: Accepted
Deciders: Benzie (go + design constraint), Nyx (implementation)

## Context

Post-gate, the first honest solve still produced implensible captaincy: **James Tarkowski (CB,
Everton) captained over Erling Haaland** in multiple horizon GWs. Diagnosis: the baseline
projection model (points_per_game × minutes × fixture factor + clean-sheet lift) systematically
over-weights defensive floors. Defenders have a **rigid binary points ceiling** (CS + BPS, ~1-2
goal involvements per season); premium forwards scale with goal volume (Haaland: hat-trick = 3x
his mean). Fixture-softness multipliers lifted a CB's *floor* above a forward's *mean*, and the
solver — rational on those numbers — picked the inflated floor every time.

Second, latent defect found during the fix: `strength_attack_home/away` are **all zero at season
start** (verified live, GW5 bootstrap). The original CS-lift multiplied against those dead fields,
silently zeroing defensive matchup quality mid-season and distorting it arbitrarily. Fixture
difficulty (`team_x_difficulty`, populated immediately) is the season-proof signal.

## Decision

1. **Cap absolute xPts for G and D at 9.0** post-fixture. Preference is preserved *within*
   position (soft-fixture CB still > hard-fixture CB: 9.0 vs 6.77) — only the cross-position
   inversion is removed. M/F keep uncapped, goal-volume-driven projections.
2. **CS lift derives from difficulty faced**: `max(0, (3 − diff)) × cs_w` — dead-field-free.
3. **Attacking upside term from FPL `threat`** (goal-involvement metric), converted per-GW with
   positional weights (F 0.45 > M 0.25 > D 0.06 > G 0.0). Premium attackers earn mean projections
   that respect ceiling asymmetry; budget players don't gain a free ride.
4. The cap is a **projection-layer constraint, not a solver constraint**: MILP economics (price,
   FTS, chips, 3-per-team) are untouched. We fixed the expectation estimates, not the optimizer.

## Consequences

- Horizon xPts on identical fixtures: 472.5 → 542.6; captaincy rotation became Haaland → (fixture
  swings among premium assets) instead of a defender armband.
- 9.0 is a judgment parameter (GW1-era evidence: elite CB weeks cluster ~6-9; forwards' means at
  premium price exceed it), not a fitted constant. If evidence shifts (double GWs, extreme
  fixtures), it's one line to change — and the gate would still vet the file either way.
- Ceiling caps create discontinuities a sophisticated opponent (a better projection source)
  could exploit; acceptable for a baseline. When the expert-panel layer lands, its per-GW
  distributions should *replace* these caps, not add to them — revisit this ADR then.
- A general rule for projection systems: **never let a low-variance asset's floor beat a
  high-variance asset's mean** through multiplication alone. Variance asymmetry belongs in the
  model shape, not in the optimizer's tolerance for bad inputs.
