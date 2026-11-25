"""cost-per-task CLI."""
import argparse
import json
import sys

from .load import load_prices, load_runs, session_records
from .report import breakeven, headline, money, summarize, tok


def _load(args):
    rows = load_runs(args.runs)
    prices = load_prices(args.prices)
    return summarize(rows, prices, common_only=not args.all_tasks)


def render(s):
    ms = sorted(s["models"], key=lambda m: m["rank_pass"])
    head = "%-12s %13s %6s %6s %8s %8s %6s %9s %9s" % (
        "model", "$/M in/out", "tasks", "pass", "in/task", "out/task", "cache", "$/task", "$/pass")
    out = [head, "-" * len(head)]
    for m in ms:
        pr = "%g/%g" % (m["price_in"], m["price_out"])
        pr_ = "n/a" if m["pass_rate"] is None else "%d%%" % round(100 * m["pass_rate"])
        out.append("%-12s %13s %6d %6s %8s %8s %5d%% %9s %9s" % (
            m["model"][:12], pr, m["tasks"], pr_, tok(m["input_per_task"]), tok(m["output_per_task"]),
            round(100 * m["cache_share"]), money(m["cost_per_task"]), money(m["cost_per_pass"])))
    out.append("")
    out.extend(headline(s))
    notes = []
    if s["dropped_tasks"]:
        notes.append("left out %d task(s) not run by every model (use --all-tasks to keep them)"
                     % len(s["dropped_tasks"]))
    for m in ms:
        for n in m["price_notes"]:
            notes.append("%s: %s" % (m["price_key"], n))
    if notes:
        out.append("")
        out.extend("note: " + n for n in sorted(set(notes)))
    return "\n".join(out)


def cmd_report(args):
    s = _load(args)
    print(json.dumps(s, indent=2) if args.json else render(s))
    return 0


def cmd_check(args):
    s = _load(args)
    ms = [m for m in s["models"] if args.model is None or m["model"] == args.model]
    if not ms:
        print("no such model in runs: %s" % args.model, file=sys.stderr)
        return 2
    bad = 0
    for m in sorted(ms, key=lambda m: m["rank_pass"]):
        cpp = m["cost_per_pass"]
        over = cpp is None or cpp > args.budget
        if args.min_pass_rate is not None and (m["pass_rate"] or 0) < args.min_pass_rate:
            over = True
        bad += over
        print("%-5s %-14s %s per passed task, %s pass rate (budget %s)" % (
            "OVER" if over else "OK", m["model"], money(cpp),
            "n/a" if m["pass_rate"] is None else "%d%%" % round(100 * m["pass_rate"]), money(args.budget)))
    return 1 if bad else 0


def cmd_breakeven(args):
    s = _load(args)
    b = breakeven(s, args.base, args.other)
    o, wrote = b["other"], tok(b["other_output_per_task"])

    def line(label, cap):
        if cap is None:
            return "  %s: %s's input side alone already costs more. No output budget gets it there." % (label, o)
        verdict = "under, so cheaper" if b["other_output_per_task"] <= cap else "over, so pricier"
        return "  %s: %s has to write under %s output tokens per task. It wrote %s (%s)." % (
            label, o, tok(cap), wrote, verdict)

    print("%s: %s per task, %s per passed task." % (
        b["base"], money(b["base_cost_per_task"]), money(b["base_cost_per_pass"])))
    print("To beat %s, keeping its own input tokens, retries, and pass rate:" % b["base"])
    print(line("per task", b["max_output_per_task"]))
    if b["base_cost_per_pass"] is None or b["other_pass_rate"] in (None, 0):
        print("  per passed task: n/a (no passes to compare)")
    else:
        print(line("per passed task", b["max_output_per_pass"]))
    return 0


def cmd_ingest(args):
    passed = {"pass": True, "fail": False, None: None}[args.result]
    n = 0
    for path in args.sessions:
        for rec in session_records(path, task=args.task, passed=passed, kind=args.kind):
            print(json.dumps(rec))
            n += 1
    if not n:
        print("no token usage found", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="cost-per-task",
                                description="What a finished task costs per model, not what a token costs.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("runs", help="JSONL (or JSON array) of calls: model, task, passed, usage")
        sp.add_argument("--prices", required=True, help="price table JSON, USD per 1M tokens")
        sp.add_argument("--all-tasks", action="store_true",
                        help="count tasks even if not every model ran them")

    r = sub.add_parser("report", help="per-model cost per task and per passed task")
    common(r)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_report)

    c = sub.add_parser("check", help="exit 1 if cost per passed task is over budget (for CI)")
    common(c)
    c.add_argument("--budget", type=float, required=True, help="max USD per passed task")
    c.add_argument("--model", help="only check this model")
    c.add_argument("--min-pass-rate", type=float, help="also fail under this pass rate (0-1)")
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("breakeven", help="output tokens per task OTHER can spend before it costs as much as BASE")
    common(b)
    b.add_argument("base")
    b.add_argument("other")
    b.set_defaults(fn=cmd_breakeven)

    g = sub.add_parser("ingest", help="turn Claude Code / Codex session files into run records")
    g.add_argument("sessions", nargs="+")
    g.add_argument("--task", help="task name (default: session file name)")
    g.add_argument("--result", choices=["pass", "fail"], help="mark the task passed or failed")
    g.add_argument("--kind", choices=["claude", "codex"], help="default: detect")
    g.set_defaults(fn=cmd_ingest)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, KeyError, OSError) as e:
        print("error: %s" % (e.args[0] if e.args else e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
