"""Read run records, price tables, and agent session files."""
import json
import os

from .usage import normalize_usage

USAGE_KEYS = ("usage", "token_usage", "tokens")


def _records(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    s = text.lstrip()
    if s.startswith("["):
        return json.loads(s)
    if s.startswith("{") and "\n" not in s.strip():
        return [json.loads(s)]
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError("%s line %d: not JSON (%s)" % (path, n, e.msg))
    return out


def _passed(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "yes", "pass", "passed", "ok", "1", "success"):
            return True
        if t in ("false", "no", "fail", "failed", "error", "0", "failure"):
            return False
    return None


def load_runs(path):
    """Return a list of normalized call records:
    {model, task, passed (True/False/None), input, cached, cache_write, output, reasoning, style}."""
    rows = []
    for i, r in enumerate(_records(path), 1):
        if not isinstance(r, dict):
            raise ValueError("%s record %d: expected an object" % (path, i))
        model = r.get("model")
        task = r.get("task", r.get("task_id"))
        if not model or task is None:
            raise ValueError("%s record %d: needs 'model' and 'task'" % (path, i))
        u = next((r[k] for k in USAGE_KEYS if isinstance(r.get(k), dict)), None)
        if u is None:
            u = r  # flat record: token fields at the top level
        counts, style = normalize_usage(u)
        passed = _passed(r.get("passed", r.get("success", r.get("pass"))))
        rows.append(dict(counts, model=str(model), task=str(task), passed=passed, style=style))
    return rows


def load_prices(path):
    """Price table in USD per 1M tokens: {"models": {name: {input, output, cached?, cache_write?}}}.
    Missing cached / cache_write prices fall back to the input price and are flagged."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    table = data.get("models", data) if isinstance(data, dict) else None
    if not isinstance(table, dict):
        raise ValueError("%s: expected {\"models\": {name: {input, output}}}" % path)
    prices = {}
    for name, p in table.items():
        if name.startswith("_") or not isinstance(p, dict):
            continue
        if "input" not in p or "output" not in p:
            raise ValueError("%s: %s needs 'input' and 'output' prices" % (path, name))
        notes = []
        cached = p.get("cached")
        if cached is None:
            cached = p["input"]
            notes.append("cached input priced at the input rate")
        cw = p.get("cache_write")
        if cw is None:
            cw = p["input"]
        prices[name] = {"input": float(p["input"]), "output": float(p["output"]),
                        "cached": float(cached), "cache_write": float(cw), "notes": notes}
    return prices


def price_for(model, prices):
    """Exact match first, then the longest price key the model name starts with
    (so 'vendor-model-2026-11-01' uses the 'vendor-model' row)."""
    if model in prices:
        return model, prices[model]
    hits = [k for k in prices if model.startswith(k)]
    if hits:
        k = max(hits, key=len)
        return k, prices[k]
    return None, None


# ---------------------------------------------------------------- session files

def claude_session(path):
    """Token usage from a Claude Code session file (~/.claude/projects/*/<session>.jsonl).
    Assistant entries repeat the same message once per content block, so usage is counted
    once per message id. Returns {model: counts}."""
    seen = {}
    for r in _records(path):
        if not isinstance(r, dict) or r.get("type") != "assistant":
            continue
        m = r.get("message") or {}
        u = m.get("usage")
        if not isinstance(u, dict):
            continue
        key = m.get("id") or r.get("uuid") or len(seen)
        seen[key] = (m.get("model") or "unknown", u)
    per = {}
    for model, u in seen.values():
        if model == "<synthetic>":
            continue
        counts, _ = normalize_usage(u)
        acc = per.setdefault(model, {"input": 0, "cached": 0, "cache_write": 0, "output": 0, "reasoning": 0, "calls": 0})
        for k in ("input", "cached", "cache_write", "output", "reasoning"):
            acc[k] += counts[k]
        acc["calls"] += 1
    return per


def codex_session(path):
    """Token usage from a Codex CLI rollout file (~/.codex/sessions/**/rollout-*.jsonl).
    Uses the last cumulative total_token_usage from token_count events, and the model from
    turn_context. Returns {model: counts}."""
    model, last = "unknown", None
    for r in _records(path):
        if not isinstance(r, dict):
            continue
        p = r.get("payload") or {}
        if r.get("type") == "turn_context" and p.get("model"):
            model = p["model"]
        if r.get("type") == "session_meta" and p.get("model") and model == "unknown":
            model = p["model"]
        if p.get("type") == "token_count":
            info = p.get("info") or {}
            if isinstance(info.get("total_token_usage"), dict):
                last = info["total_token_usage"]
    if last is None:
        return {}
    counts, _ = normalize_usage(last)
    counts["calls"] = None
    return {model: counts}


def session_records(path, task=None, passed=None, kind=None):
    """Turn a Claude Code or Codex session file into run records (one per model in the session)."""
    if kind is None:
        kind = "claude"
        for r in _records(path)[:50]:
            if isinstance(r, dict) and r.get("type") in ("session_meta", "turn_context", "event_msg", "response_item"):
                kind = "codex"
                break
    per = claude_session(path) if kind == "claude" else codex_session(path)
    task = task or os.path.splitext(os.path.basename(path))[0]
    out = []
    for model, c in per.items():
        rec = {"task": task, "model": model, "passed": passed,
               "usage": {k: c[k] for k in ("input", "cached", "cache_write", "output", "reasoning")},
               "source": kind}
        if c.get("calls") is not None:
            rec["calls"] = c["calls"]
        out.append(rec)
    return out
