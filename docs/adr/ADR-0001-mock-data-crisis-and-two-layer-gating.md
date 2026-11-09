# ADR-0001: The GW5 mock-data failure and two-layer data defense

Date: 2026-09-26
Status: Accepted
Deciders: Benzie (direction + specs), Nyx (implementation + verification)

## Context

Gameweek 5: the MILP solver produced a "captain Bogle" plan on data where every player carried
an identical projection for every gameweek (`8.40 forever` flat columns). It scored 72 against a
manually-selected benchmark of 126. The optimizer wasn't wrong — its inputs were fiction, and the
pipeline happily optimized fiction because nothing inspected data shape before solving.

Root cause chain: `free_xp_model.py` fetched projections from an "Understat" call that was a dead
stub returning `{}`; the CSV generator substituted placeholder values; no consumer checked. The
failure was silent by construction — garbage looked exactly like data.

## Decision

Two independent deterministic layers, on the correct statistical axis:

1. **gate.py (supervisor)** — pre-flight critic per gate-agent-schema.pdf + review round:
   per-player **temporal** variance (across GWs), within-GW discrimination, live bootstrap/fixture
   cross-check, completeness, audited `pass_kind`. Exit 2 blocks the solve chain.
2. **`validate_fpl_projections()` tripwire in dev/data_parser.py (seatbelt)** — crashes the solver
   itself when temporal flatline > 50% of players, within-GW variance < 0.5, or premium reference
   names are missing (fuzzy-match sentinel). No opt-out path; wired into all four readers.

## The axis error (the actual lesson)

The first written spec checked *across-player* variance in a single GW column. Tested against the
real GW5 mock file it **passed** (variance 4.07 ≫ 0.5): the mock differentiated players fine; its
failure mode was identical values **across time** for each player (406/406 flat players, 100%).
A lock on the wrong axis is worse than no lock — it certifies the disease.

Verification discipline adopted as a rule: **every data guard must be replay-tested against the
historical artifact that motivated it.** The GW5 mock file is kept (`solio_mock_backup.csv`) as
that permanent test fixture. The same discipline already caught the "varies across GW but
identical within GW" adversarial case during gate design.

## Consequences

- Solves on placeholder data are now structurally impossible; an honest-but-wrong model is still
  possible (that's the gate's job to score, not eliminate).
- Pipeline contract is now: `build_projection.py → gate.py → solve.py`, documented in PIPELINE.md.
- LLM arbitration inside the gate is confidence-scaled, never a hard block — deterministic checks
  carry the veto; opinions only adjust trust.
- Data quality became a first-class release gate for a project that previously treated it as
  input plumbing. Generalizes beyond FPL: any optimizer inherits this pattern.
