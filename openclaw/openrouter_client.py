#!/usr/bin/env python3
"""Real OpenRouter integration for 5 experts + Nyx orchestrator.
Calls OpenRouter API directly, logs model metadata, enforces 1 call/expert/GW."""
import os, json, logging, time, re
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# Load .env file
load_dotenv()

LOG_DIR = Path(os.getenv("FPL_LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

class OpenRouterClient:
    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={"HTTP-Referer": "https://github.com/benzie/open-fpl-solver", "X-Title": "FPL-Optimizer"}
        )
        self.call_log = []

    def _repair_truncated_array(self, content):
        """Recover complete objects from a JSON array cut off mid-stream (e.g.
        finish_reason=length). Drops the partial tail object, re-closes the bracket."""
        start = content.find('[')
        if start == -1:
            return None
        depth = 0
        last_complete = -1
        for i in range(start, len(content)):
            c = content[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    last_complete = i
        if last_complete == -1:
            return None
        candidate = content[start:last_complete + 1] + ']'
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, list) and parsed:
            print(f"      ⚠ recovered truncated JSON array ({len(parsed)} objects)")
            return parsed
        return None

    def _extract_json(self, content):
        """Extract first valid JSON array or object from content.

        Uses brace/bracket-depth counting so nested objects are matched
        correctly instead of stopping at the first inner closing brace.
        Falls back to truncated-array repair (finish_reason=length mid-output).
        """
        if not content:
            return None
        # Try flat arrays first (no nesting) — fast path.
        # Require all-objects lists: a truncated array can fake a closed
        # flat bracket and yield a dict-item list we'd later reject anyway.
        for match in re.finditer(r'\[([^\[\]]*)\]', content):
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue
        # Truncated-array recovery (finish_reason=length mid-output):
        # salvage the complete objects before the cut instead of falling
        # through to the single-object scan below.
        repaired = self._repair_truncated_array(content)
        if repaired is not None:
            return repaired
        # For objects/arrays with possible nesting, find balanced bracket
        for start, ch in enumerate(content):
            if ch not in ('{', '['):
                continue
            depth = 0
            closing = '}' if ch == '{' else ']'
            for i in range(start, len(content)):
                c = content[i]
                if c == ch:
                    depth += 1
                elif c == closing:
                    depth -= 1
                    if depth == 0:
                        candidate = content[start:i + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, (list, dict)):
                                return parsed
                        except json.JSONDecodeError:
                            break
                        break
        return None

    def _call_with_json_mode(self, model, messages, temperature=0.1, max_tokens=4000):
        """Call OpenRouter; prefer json_object mode when the model supports it, but never treat a
        non-json_model error as a reason to retry the same model forever — fall through to the
        next model inside call_expert."""
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": "You must output valid JSON. Always include the word 'json'."}, *messages],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                return response
            except Exception as exc:
                error_str = str(exc)
                if "429" in error_str or "rate_limit" in error_str.lower():
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        print(f"    ⚠ Rate limited ({model}, attempt {attempt + 1}/{max_retries}), waiting {delay}s...")
                        time.sleep(delay)
                        continue
                # If JSON mode itself is the problem, fall back for this attempt
                if "response_format" in error_str.lower() or "json_object" in error_str.lower():
                    try:
                        return self.client.chat.completions.create(
                            model=model,
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                    except Exception as e2:
                        error_str2 = str(e2)
                        if "429" in error_str2 or "rate_limit" in error_str2.lower():
                            if attempt < max_retries - 1:
                                delay = base_delay * (2 ** attempt)
                                print(f"    ⚠ Rate limited fallback ({model}, attempt {attempt + 1}/{max_retries}), waiting {delay}s...")
                                time.sleep(delay)
                                continue
                        raise e2
                raise exc

    @staticmethod
    def _normalize_rows(parsed):
        """Coerce expert rows: player_id->int, score/confidence->float. Drops junk rows."""
        rows = []
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return []
        for item in parsed:
            if not isinstance(item, dict) or "player_id" not in item:
                continue
            try:
                item["player_id"] = int(str(item["player_id"]).strip())
            except (ValueError, TypeError):
                continue
            for key in ("score", "confidence"):
                try:
                    item[key] = float(item.get(key, 0.0))
                except (ValueError, TypeError):
                    item[key] = 0.0
            rows.append(item)
        return rows

    def call_expert(self, expert_name, prompt, primary_model, fallbacks, gw, min_rows=0):
        """Call expert with primary + fallbacks, return (data, model_used).

        min_rows: experts whose parsed rows fall below this are treated as
        degraded and the next fallback model is tried instead.
        """
        models = [primary_model] + fallbacks
        for i, model in enumerate(models):
            try:
                print(f"  Calling {expert_name} with {model}...")
                response = self._call_with_json_mode(model, [{"role": "user", "content": prompt}])
                content = response.choices[0].message.content
                model_used = response.model
                self.call_log.append({
                    "expert": expert_name, "model_requested": model,
                    "model_used": model_used, "gw": gw,
                    "tokens_in": response.usage.prompt_tokens,
                    "tokens_out": response.usage.completion_tokens,
                    "fallback_depth": i
                })
                print(f"    ✓ {expert_name} -> {model_used} ({response.usage.prompt_tokens}in/{response.usage.completion_tokens}out)")
                # Parse JSON with fallback
                parsed = self._extract_json(content)
                if isinstance(parsed, dict) and len(parsed) == 1:
                    (only_key,) = parsed.keys()
                    if only_key in ("scores", "results", "data", "players", "adjustments"):
                        if isinstance(parsed[only_key], list):
                            parsed = parsed[only_key]
                if expert_name != "nyx" and isinstance(parsed, (list, dict)):
                    rows = self._normalize_rows(parsed)
                    if rows and len(rows) >= max(1, min_rows):
                        return rows, model_used
                    if rows:
                        print(f"    ✗ {expert_name} only {len(rows)} usable rows (<{min_rows}); trying next model")
                    parsed = None  # fall into the no-JSON handler below
                elif expert_name == "nyx" and isinstance(parsed, dict) and "player_adjustments" in parsed:
                    return parsed, model_used
                if parsed is None and not (expert_name == "nyx" and isinstance(parsed, dict)):
                    print(f"    ✗ {expert_name} no valid JSON found in response")
                    if content:
                        print(f"      Raw content: {content[:300]}")
                    else:
                        print(f"      Raw content: (empty — finish_reason={response.choices[0].finish_reason})")
                    if i == len(models) - 1:
                        raise RuntimeError(f"All models failed for {expert_name}: no JSON found")
                    continue
            except Exception as e:
                print(f"    ✗ {expert_name} fallback {i} ({model}): {e}")
                if i == len(models) - 1:
                    # Last model failed — don't chain its exception; raise a clean message
                    raise RuntimeError(f"All models failed for {expert_name}: {type(e).__name__}")
        return None, None

    def call_nyx(self, expert_outputs, primary_model, fallbacks, gw):
        """Nyx blends 5 expert outputs. Returns (blended, model_used)."""
        prompt = f"""Given 5 expert JSON arrays (one per player-dimension), return a per-player
adjustment score for each player.

Return ONLY a valid JSON object. No other text. No markdown. No explanations.

The JSON object must have exactly this one key:
  "player_adjustments": {{ "1": 0.43, "2": 0.12, "3": 0.67, ... }}

Keys are player_id strings. Values are float adjustments in range [-0.5, +0.5].
Positive = upregulate, negative = downregulate, 0 = neutral.

Example output:
{{
  "player_adjustments": {{"1": 0.43, "2": 0.12, "3": 0.67}}
}}

Expert data:
{json.dumps(expert_outputs)}"""
        return self.call_expert("nyx", prompt, primary_model, fallbacks, gw)

    def get_call_summary(self):
        return {
            "total_calls": len(self.call_log),
            "by_expert": {e["expert"]: {"model_used": e["model_used"], "fallback_depth": e["fallback_depth"]} for e in self.call_log},
            "models_resolved": [e["model_used"] for e in self.call_log]
        }