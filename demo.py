"""Demo on SAMPLE DATA: three made-up models, ten made-up tasks. No model, keys, or network.

examples/runs.jsonl    39 logged calls (retries included), each model in a different usage format
examples/prices.json   made-up prices, USD per 1M tokens
Regenerate both with: python3 examples/make_sample.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*args):
    r = subprocess.run([sys.executable, "-m", "costpertask"] + list(args), cwd=HERE, capture_output=True, text=True)
    print("$ cost-per-task " + " ".join(args))
    print((r.stdout + r.stderr).rstrip())
    print("(exit code %d)" % r.returncode)
    print()


P = ["--prices", "examples/prices.json"]
print("SAMPLE DATA: made-up models, prices, and token counts\n")
run("report", "examples/runs.jsonl", *P)
run("breakeven", "examples/runs.jsonl", *P, "mid-b", "small-a")
run("check", "examples/runs.jsonl", *P, "--budget", "0.025")
