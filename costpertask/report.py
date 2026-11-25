"""Turn call records + prices into per-task and per-passed-task cost, per model."""
from statistics import median

from .load import price_for

M = 1_000_000.0
PARTS = ("input", "cached", "cache_write", "output")


def call_cost(rec, price):
    return sum(rec[k] * price[k] for k in PARTS) / M


def tasks_by_model(rows, prices):
    """Group call records into tasks. A task's cost is every call made for it (retries included).
    A task passed if any record for it says passed; failed if records say failed and none passed;
    otherwise it's unscored."""
    missing = sorted({r["model"] for r in rows if price_for(r["model"], prices)[1] is None})
    if missing:
        raise KeyError("no price for: " + ", ".join(missing))
    out = {}
    for r in rows:
        key, price = price_for(r["model"], prices)
        t = out.setdefault(r["model"], {}).setdefault(r["task"], {
            "cost": 0.0, "calls": 0, "passed": None, "price_key": key,
            "input": 0, "cached": 0, "cache_write": 0, "output": 0, "reasoning": 0})
        for k in PARTS + ("reasoning",):
            t[k] += r[k]
        t["cost"] += call_cost(r, price)
        t["calls"] += 1
        if r["passed"] is True:
            t["passed"] = True
        elif r["passed"] is False and t["passed"] is None:
            t["passed"] = False
    return out


def summarize(rows, prices, common_only=True):
    """Per-model summary. With common_only, only tasks every model ran are counted, so the
    comparison is like for like."""
    by = tasks_by_model(rows, prices)
    all_tasks = [set(t) for t in by.values()]
    common = set.intersection(*all_tasks) if all_tasks else set()
    dropped = sorted(set.union(*all_tasks) - common) if all_tasks else []
    models = []
    for model, tasks in by.items():
        keep = {k: v for k, v in tasks.items() if (k in common or not common_only)}
        ts = list(keep.values())
        if not ts:
            continue
        key = ts[0]["price_key"]
        p = prices[key]
        total = sum(t["cost"] for t in ts)
        scored = [t for t in ts if t["passed"] is not None]
        passed = [t for t in scored if t["passed"]]
        inp = sum(t["input"] + t["cached"] + t["cache_write"] for t in ts)
        models.append({
            "model": model, "price_key": key, "price_in": p["input"], "price_out": p["output"],
            "price_notes": p["notes"],
            "tasks": len(ts), "scored": len(scored), "passed": len(passed),
            "pass_rate": (len(passed) / len(scored)) if scored else None,
            "calls_per_task": sum(t["calls"] for t in ts) / len(ts),
            "input_per_task": inp / len(ts),
            "output_per_task": sum(t["output"] for t in ts) / len(ts),
            "reasoning_per_task": sum(t["reasoning"] for t in ts) / len(ts),
            "cache_share": (sum(t["cached"] for t in ts) / inp) if inp else 0.0,
            "total_cost": total,
            "cost_per_task": total / len(ts),
            "median_cost_per_task": median(t["cost"] for t in ts),
            # Failed tasks still cost money; charge them to the tasks that passed.
            "cost_per_pass": (total / len(passed)) if passed else None,
        })
    _rank(models, "rank_price", lambda m: (m["price_out"], m["price_in"]))
    _rank(models, "rank_task", lambda m: m["cost_per_task"])
    _rank(models, "rank_pass", lambda m: (m["cost_per_pass"] is None, m["cost_per_pass"] or 0))
    return {"models": models, "common_tasks": len(common), "dropped_tasks": dropped if common_only else []}


def _rank(models, field, key):
    for i, m in enumerate(sorted(models, key=key), 1):
        m[field] = i


def headline(summary):
    ms = summary["models"]
    if len(ms) < 2:
        return []
    by_price = min(ms, key=lambda m: m["rank_price"])
    by_task = min(ms, key=lambda m: m["rank_task"])
    by_pass = min(ms, key=lambda m: m["rank_pass"])
    lines = ["cheapest per output token: %s ($%.2f/M out)" % (by_price["model"], by_price["price_out"]),
             "cheapest per task:         %s (%s)" % (by_task["model"], money(by_task["cost_per_task"]))]
    if by_pass["cost_per_pass"] is not None:
        lines.append("cheapest per passed task:  %s (%s)" % (by_pass["model"], money(by_pass["cost_per_pass"])))
    flips = [m for m in ms if m["rank_pass"] != m["rank_price"] and m["cost_per_pass"] is not None]
    if flips and by_price is not by_pass:
        lines.append("rank flipped: price order %s -> per-pass order %s" % (
            " < ".join(m["model"] for m in sorted(ms, key=lambda m: m["rank_price"])),
            " < ".join(m["model"] for m in sorted(ms, key=lambda m: m["rank_pass"]))))
        a, b = by_price, by_pass
        if a["cost_per_pass"] and b["price_out"] and a["price_out"]:
            lines.append("%s: %.1fx cheaper per output token than %s, %.1fx the cost per passed task" % (
                a["model"], b["price_out"] / a["price_out"], b["model"], a["cost_per_pass"] / b["cost_per_pass"]))
    return lines


def breakeven(summary, base, other):
    """How many output tokens per task can `other` write before it costs as much as `base`,
    keeping `other`'s measured input side, pass rate, and retries. Two answers: matching
    `base`'s cost per task, and matching its cost per passed task."""
    ms = {m["model"]: m for m in summary["models"]}
    if base not in ms or other not in ms:
        raise KeyError("unknown model: %s" % (base if base not in ms else other))
    a, b = ms[base], ms[other]
    b_in_cost = b["cost_per_task"] - b["output_per_task"] * b["price_out"] / M

    def room(target):
        r = target - b_in_cost
        if r < 0:
            return None
        return r / b["price_out"] * M if b["price_out"] > 0 else float("inf")

    per_pass = None
    if a["cost_per_pass"] is not None and b["passed"]:
        # other's cost per pass = cost_per_task * tasks / passed, so match on that.
        per_pass = room(a["cost_per_pass"] * b["passed"] / b["tasks"])
    return {"base": base, "other": other,
            "base_cost_per_task": a["cost_per_task"], "base_cost_per_pass": a["cost_per_pass"],
            "other_input_cost_per_task": b_in_cost, "other_output_per_task": b["output_per_task"],
            "max_output_per_task": room(a["cost_per_task"]), "max_output_per_pass": per_pass,
            "other_pass_rate": b["pass_rate"]}


def money(x):
    if x is None:
        return "n/a"
    if x >= 1:
        return "$%.2f" % x
    if x >= 0.1:
        return "$%.3f" % x
    return "$%.4f" % x


def tok(x):
    x = float(x)
    if x >= 1e6:
        return "%.1fM" % (x / 1e6)
    if x >= 1e4:
        return "%.0fk" % (x / 1e3)
    if x >= 1e3:
        return "%.1fk" % (x / 1e3)
    return "%.0f" % x
