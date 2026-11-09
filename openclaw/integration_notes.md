fixture_expert, form_expert, availability_expert, setpiece_expert, value_expert -> pull real FPL/bootstrap data, hand to OpenClaw agent (batch=1 call/GW), return JSON array.
Nyx -> takes 5 JSON arrays, blends once (batch=1/GW), returns blended adjustments + conflicts.
Output contract unchanged: optimizer.py / run/solve.py read same format; no optimizer changes needed.
OpenRouter key: set in .env as OPENROUTER_API_KEY; onboard via openclaw onboard (interactive, masked).
