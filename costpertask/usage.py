"""Normalize token usage from different providers and log formats into one shape.

Every record ends up as four counts that never overlap:
  input       uncached input tokens
  cached      input tokens read from cache
  cache_write input tokens written to cache (Anthropic-style cache creation)
  output      output tokens, reasoning included
plus `reasoning` (already inside output, reported for information only).
"""

FIELDS = ("input", "cached", "cache_write", "output")


def _i(d, *keys):
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return 0


def normalize_usage(u):
    """Return (counts dict, style name). Raises ValueError if nothing usable is found."""
    if not isinstance(u, dict):
        raise ValueError("usage must be an object")
    # Anthropic Messages API: input_tokens excludes cache reads and writes.
    if "cache_read_input_tokens" in u or "cache_creation_input_tokens" in u:
        return {
            "input": _i(u, "input_tokens"),
            "cached": _i(u, "cache_read_input_tokens"),
            "cache_write": _i(u, "cache_creation_input_tokens"),
            "output": _i(u, "output_tokens"),
            "reasoning": 0,
        }, "anthropic"
    # OpenAI Chat Completions: prompt_tokens includes cached tokens.
    if "prompt_tokens" in u or "completion_tokens" in u:
        cached = _i(u.get("prompt_tokens_details") or {}, "cached_tokens")
        return {
            "input": max(0, _i(u, "prompt_tokens") - cached),
            "cached": cached,
            "cache_write": 0,
            "output": _i(u, "completion_tokens"),
            "reasoning": _i(u.get("completion_tokens_details") or {}, "reasoning_tokens"),
        }, "openai-chat"
    # OpenAI Responses API / Codex token_count: input_tokens includes cached tokens.
    if "input_tokens_details" in u or "output_tokens_details" in u or "cached_input_tokens" in u:
        cached = _i(u.get("input_tokens_details") or {}, "cached_tokens") or _i(u, "cached_input_tokens")
        reasoning = _i(u.get("output_tokens_details") or {}, "reasoning_tokens") or _i(u, "reasoning_output_tokens")
        return {
            "input": max(0, _i(u, "input_tokens") - cached),
            "cached": cached,
            "cache_write": 0,
            "output": _i(u, "output_tokens"),
            "reasoning": reasoning,
        }, "openai-responses"
    # Already normalized (this tool's own format).
    if any(k in u for k in FIELDS):
        return {
            "input": _i(u, "input"),
            "cached": _i(u, "cached"),
            "cache_write": _i(u, "cache_write"),
            "output": _i(u, "output"),
            "reasoning": _i(u, "reasoning"),
        }, "normalized"
    # Bare input_tokens / output_tokens with no cache detail: treat input as uncached.
    if "input_tokens" in u or "output_tokens" in u:
        return {
            "input": _i(u, "input_tokens"),
            "cached": 0,
            "cache_write": 0,
            "output": _i(u, "output_tokens"),
            "reasoning": 0,
        }, "plain"
    raise ValueError("no token counts found in usage: %s" % sorted(u))
