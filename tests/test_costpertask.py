import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from costpertask.cli import main  # noqa: E402
from costpertask.load import claude_session, codex_session, load_prices, load_runs, price_for, session_records  # noqa: E402
from costpertask.report import breakeven, call_cost, headline, money, summarize, tasks_by_model  # noqa: E402
from costpertask.usage import normalize_usage  # noqa: E402

EX = os.path.join(ROOT, "examples")
RUNS, PRICES = os.path.join(EX, "runs.jsonl"), os.path.join(EX, "prices.json")


def write(d, name, obj):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        if isinstance(obj, list):
            f.write("\n".join(json.dumps(x) for x in obj) + "\n")
        else:
            json.dump(obj, f)
    return p


def run_cli(*args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(list(args))
    return code, buf.getvalue()


PRICE = {"models": {"a": {"input": 1.0, "output": 10.0, "cached": 0.1},
                    "b": {"input": 2.0, "output": 4.0}}}


class Usage(unittest.TestCase):
    def test_openai_chat_subtracts_cached(self):
        c, style = normalize_usage({"prompt_tokens": 1000, "completion_tokens": 200,
                                    "prompt_tokens_details": {"cached_tokens": 600},
                                    "completion_tokens_details": {"reasoning_tokens": 150}})
        self.assertEqual(style, "openai-chat")
        self.assertEqual((c["input"], c["cached"], c["output"], c["reasoning"]), (400, 600, 200, 150))

    def test_anthropic_input_excludes_cache(self):
        c, style = normalize_usage({"input_tokens": 50, "cache_read_input_tokens": 900,
                                    "cache_creation_input_tokens": 100, "output_tokens": 30})
        self.assertEqual(style, "anthropic")
        self.assertEqual((c["input"], c["cached"], c["cache_write"], c["output"]), (50, 900, 100, 30))

    def test_responses_subtracts_cached(self):
        c, style = normalize_usage({"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 250},
                                    "output_tokens": 80, "output_tokens_details": {"reasoning_tokens": 40}})
        self.assertEqual(style, "openai-responses")
        self.assertEqual((c["input"], c["cached"], c["output"], c["reasoning"]), (750, 250, 80, 40))

    def test_codex_style_totals(self):
        c, _ = normalize_usage({"input_tokens": 21000, "cached_input_tokens": 14000, "output_tokens": 1400,
                                "reasoning_output_tokens": 900})
        self.assertEqual((c["input"], c["cached"], c["reasoning"]), (7000, 14000, 900))

    def test_plain_and_normalized(self):
        self.assertEqual(normalize_usage({"input_tokens": 5, "output_tokens": 6})[0]["input"], 5)
        c, style = normalize_usage({"input": 1, "cached": 2, "output": 3})
        self.assertEqual((style, c["cache_write"]), ("normalized", 0))

    def test_no_counts_is_error(self):
        with self.assertRaises(ValueError):
            normalize_usage({"foo": 1})

    def test_bools_are_not_counts(self):
        self.assertEqual(normalize_usage({"input": True, "output": 3})[0]["input"], 0)


class Loading(unittest.TestCase):
    def test_flat_and_nested_records_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.jsonl")
            with open(p, "w") as f:
                f.write("// comment\n")
                f.write(json.dumps({"model": "a", "task": 1, "passed": "yes", "input": 10, "output": 5}) + "\n\n")
                f.write(json.dumps({"model": "a", "task_id": "t2", "success": 0,
                                    "usage": {"prompt_tokens": 3, "completion_tokens": 1}}) + "\n")
            rows = load_runs(p)
        self.assertEqual([r["task"] for r in rows], ["1", "t2"])
        self.assertEqual([r["passed"] for r in rows], [True, False])

    def test_json_array_and_bad_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            with open(p, "w") as f:
                json.dump([{"model": "a", "task": "x", "input": 1, "output": 1}], f, indent=1)
            self.assertEqual(len(load_runs(p)), 1)
            with open(p, "w") as f:
                f.write('{"model":"a","task":"x","input":1}\nnot json\n')
            with self.assertRaises(ValueError):
                load_runs(p)

    def test_missing_model_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                load_runs(write(d, "r.jsonl", [{"task": "x", "input": 1}]))

    def test_prices_fallbacks_and_prefix_match(self):
        with tempfile.TemporaryDirectory() as d:
            prices = load_prices(write(d, "p.json", PRICE))
        self.assertEqual(prices["b"]["cached"], 2.0)
        self.assertTrue(prices["b"]["notes"])
        self.assertFalse(prices["a"]["notes"])
        self.assertEqual(price_for("a-2026-11-01", {"a": 1, "a-2026": 2})[0], "a-2026")
        self.assertEqual(price_for("zzz", prices), (None, None))

    def test_price_needs_input_and_output(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                load_prices(write(d, "p.json", {"models": {"a": {"input": 1}}}))


class Sessions(unittest.TestCase):
    def test_claude_dedupes_message_ids_and_skips_synthetic(self):
        per = claude_session(os.path.join(EX, "claude_session.jsonl"))
        self.assertEqual(list(per), ["sample-claude"])
        c = per["sample-claude"]
        self.assertEqual((c["calls"], c["input"], c["cached"], c["cache_write"], c["output"]),
                         (2, 52, 9000, 10200, 800))

    def test_codex_uses_last_cumulative_total(self):
        per = codex_session(os.path.join(EX, "codex_session.jsonl"))
        c = per["sample-codex"]
        self.assertEqual((c["input"], c["cached"], c["output"]), (7000, 14000, 1400))

    def test_session_records_detects_kind_and_roundtrips(self):
        recs = session_records(os.path.join(EX, "codex_session.jsonl"), task="t", passed=True)
        self.assertEqual(recs[0]["source"], "codex")
        with tempfile.TemporaryDirectory() as d:
            rows = load_runs(write(d, "r.jsonl", recs))
        self.assertEqual((rows[0]["style"], rows[0]["input"], rows[0]["passed"]), ("normalized", 7000, True))


class Report(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.prices = load_prices(write(self.d.name, "p.json", PRICE))

    def tearDown(self):
        self.d.cleanup()

    def rows(self, recs):
        return load_runs(write(self.d.name, "r.jsonl", recs))

    def test_call_cost(self):
        rec = {"input": 1_000_000, "cached": 1_000_000, "cache_write": 0, "output": 100_000}
        self.assertAlmostEqual(call_cost(rec, self.prices["a"]), 1.0 + 0.1 + 1.0)

    def test_retries_add_up_and_any_pass_counts(self):
        rows = self.rows([
            {"model": "a", "task": "t", "passed": False, "input": 1_000_000, "output": 0},
            {"model": "a", "task": "t", "passed": True, "input": 1_000_000, "output": 0},
            {"model": "a", "task": "t", "input": 0, "output": 100_000},
        ])
        t = tasks_by_model(rows, self.prices)["a"]["t"]
        self.assertAlmostEqual(t["cost"], 3.0)
        self.assertEqual((t["calls"], t["passed"]), (3, True))

    def test_failed_tasks_are_charged_to_passes(self):
        rows = self.rows([
            {"model": "a", "task": "t1", "passed": True, "input": 1_000_000, "output": 0},
            {"model": "a", "task": "t2", "passed": False, "input": 1_000_000, "output": 0},
            {"model": "a", "task": "t3", "input": 1_000_000, "output": 0},
        ])
        m = summarize(rows, self.prices)["models"][0]
        self.assertAlmostEqual(m["cost_per_task"], 1.0)
        self.assertEqual((m["scored"], m["passed"]), (2, 1))
        self.assertAlmostEqual(m["pass_rate"], 0.5)
        self.assertAlmostEqual(m["cost_per_pass"], 3.0)

    def test_no_passes_gives_none(self):
        rows = self.rows([{"model": "a", "task": "t", "passed": False, "input": 10, "output": 1}])
        self.assertIsNone(summarize(rows, self.prices)["models"][0]["cost_per_pass"])

    def test_common_tasks_only_by_default(self):
        rows = self.rows([
            {"model": "a", "task": "t1", "passed": True, "input": 10, "output": 1},
            {"model": "a", "task": "t2", "passed": True, "input": 10, "output": 1},
            {"model": "b", "task": "t1", "passed": True, "input": 10, "output": 1},
        ])
        s = summarize(rows, self.prices)
        self.assertEqual((s["common_tasks"], s["dropped_tasks"]), (1, ["t2"]))
        self.assertEqual({m["model"]: m["tasks"] for m in s["models"]}["a"], 1)
        s2 = summarize(rows, self.prices, common_only=False)
        self.assertEqual({m["model"]: m["tasks"] for m in s2["models"]}["a"], 2)

    def test_unpriced_model_is_error(self):
        rows = self.rows([{"model": "zzz", "task": "t", "input": 1, "output": 1}])
        with self.assertRaises(KeyError):
            summarize(rows, self.prices)

    def test_breakeven_math(self):
        # a: 1 task, $1.00 (1M input). b: input side $0.20 (100k input at $2), output price $4/M.
        rows = self.rows([
            {"model": "a", "task": "t", "passed": True, "input": 1_000_000, "output": 0},
            {"model": "b", "task": "t", "passed": True, "input": 100_000, "output": 500_000},
        ])
        b = breakeven(summarize(rows, self.prices), "a", "b")
        self.assertAlmostEqual(b["other_input_cost_per_task"], 0.2)
        self.assertAlmostEqual(b["max_output_per_task"], 200_000)
        self.assertAlmostEqual(b["max_output_per_pass"], 200_000)

    def test_breakeven_input_alone_over(self):
        rows = self.rows([
            {"model": "a", "task": "t", "passed": True, "input": 1000, "output": 0},
            {"model": "b", "task": "t", "passed": True, "input": 1_000_000, "output": 0},
        ])
        self.assertIsNone(breakeven(summarize(rows, self.prices), "a", "b")["max_output_per_task"])

    def test_money(self):
        self.assertEqual([money(x) for x in (None, 2, 0.25, 0.0181)], ["n/a", "$2.00", "$0.250", "$0.0181"])


class SampleData(unittest.TestCase):
    """The bundled sample shows the cheapest-token model losing per passed task."""

    def test_sample_is_labeled(self):
        with open(RUNS) as f:
            self.assertIn("SAMPLE DATA", f.readline())
        with open(PRICES) as f:
            self.assertIn("SAMPLE", json.load(f)["_note"])

    def test_rank_flip_on_sample(self):
        s = summarize(load_runs(RUNS), load_prices(PRICES))
        ranks = {m["model"]: (m["rank_price"], m["rank_pass"]) for m in s["models"]}
        self.assertEqual(ranks["small-a"][0], 1)
        self.assertEqual(ranks["mid-b"][1], 1)
        self.assertTrue(any(l.startswith("rank flipped") for l in headline(s)))

    def test_sample_mixes_usage_formats(self):
        styles = {r["model"]: r["style"] for r in load_runs(RUNS)}
        self.assertEqual(styles, {"small-a": "openai-chat", "mid-b": "anthropic", "large-c": "openai-responses"})


class CLI(unittest.TestCase):
    def test_report_text_and_json(self):
        code, out = run_cli("report", RUNS, "--prices", PRICES)
        self.assertEqual(code, 0)
        self.assertIn("cheapest per passed task:  mid-b", out)
        code, out = run_cli("report", RUNS, "--prices", PRICES, "--json")
        self.assertEqual(len(json.loads(out)["models"]), 3)

    def test_check_exit_codes(self):
        self.assertEqual(run_cli("check", RUNS, "--prices", PRICES, "--budget", "1")[0], 0)
        self.assertEqual(run_cli("check", RUNS, "--prices", PRICES, "--budget", "0.025")[0], 1)
        self.assertEqual(run_cli("check", RUNS, "--prices", PRICES, "--budget", "0.025", "--model", "mid-b")[0], 0)
        self.assertEqual(run_cli("check", RUNS, "--prices", PRICES, "--budget", "1", "--model", "mid-b",
                                 "--min-pass-rate", "0.95")[0], 1)
        self.assertEqual(run_cli("check", RUNS, "--prices", PRICES, "--budget", "1", "--model", "nope")[0], 2)

    def test_breakeven_cli(self):
        code, out = run_cli("breakeven", RUNS, "--prices", PRICES, "mid-b", "small-a")
        self.assertEqual(code, 0)
        self.assertIn("per passed task: small-a has to write under", out)
        self.assertIn("over, so pricier", out)

    def test_ingest_cli(self):
        code, out = run_cli("ingest", os.path.join(EX, "claude_session.jsonl"), "--task", "t", "--result", "fail")
        rec = json.loads(out.splitlines()[0])
        self.assertEqual((code, rec["model"], rec["passed"]), (0, "sample-claude", False))

    def test_bad_input_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = write(d, "r.jsonl", [{"model": "zzz", "task": "t", "input": 1, "output": 1}])
            self.assertEqual(run_cli("report", p, "--prices", PRICES)[0], 2)

    def test_demo_runs(self):
        r = subprocess.run([sys.executable, "demo.py"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SAMPLE DATA", r.stdout)


if __name__ == "__main__":
    unittest.main()
