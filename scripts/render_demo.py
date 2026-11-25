"""Render demo.png from the real output of the tool on the bundled SAMPLE DATA. Dev-only; needs Pillow."""
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from costpertask.load import load_prices, load_runs  # noqa: E402
from costpertask.report import money, summarize  # noqa: E402

RUNS, PRICES = "examples/runs.jsonl", "examples/prices.json"


def cli(*a):
    r = subprocess.run([sys.executable, "-m", "costpertask"] + list(a), cwd=ROOT, capture_output=True, text=True)
    return ["$ cost-per-task " + " ".join(a)] + r.stdout.rstrip("\n").split("\n")


lines = cli("report", RUNS, "--prices", PRICES) + [""] + cli("breakeven", RUNS, "--prices", PRICES, "mid-b", "small-a")
s = summarize(load_runs(os.path.join(ROOT, RUNS)), load_prices(os.path.join(ROOT, PRICES)))
ms = sorted(s["models"], key=lambda m: m["rank_price"])


def font(size, bold=False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono%s.ttf" % ("-Bold" if bold else "")
    return ImageFont.truetype(p, size) if os.path.exists(p) else ImageFont.load_default()


F, FB, FT, FS = font(18), font(18, True), font(44, True), font(22)
W, PAD, LH = 1400, 56, 26
CH = 300
H = 175 + CH + 40 + LH * len(lines) + 40
img = Image.new("RGB", (W, H), "#0d1117")
d = ImageDraw.Draw(img)
d.text((PAD, 40), "cost-per-task", font=FT, fill="#e6edf3")
d.text((W - PAD - 330, 52), "Applied Logic · a-l.studio", font=FS, fill="#8b949e")
d.text((PAD, 100), "What a finished task costs per model, not what a token costs.", font=FS, fill="#8b949e")
d.text((PAD, 132), "SAMPLE DATA: made-up models, prices, and token counts", font=FB, fill="#d29922")

# two bar panels: list price per output token vs measured cost per passed task
COL = {"small-a": "#58a6ff", "mid-b": "#3fb950", "large-c": "#bc8cff"}
top = 175
panels = [("list price, $ per 1M output tokens", lambda m: m["price_out"], lambda v: "$%.2f" % v),
          ("cost per passed task (retries + failures included)", lambda m: m["cost_per_pass"], money)]
pw = (W - 2 * PAD - 60) // 2
for i, (title, get, fmt) in enumerate(panels):
    x0 = PAD + i * (pw + 60)
    d.text((x0, top), title, font=FB, fill="#e6edf3")
    vmax = max(get(m) for m in ms)
    bw, gap, y = pw - 230, 18, top + 50
    for m in ms:
        v = get(m)
        d.text((x0, y + 8), m["model"], font=F, fill="#c9d1d9")
        L = max(4, int(bw * v / vmax))
        d.rectangle([x0 + 110, y, x0 + 110 + L, y + 44], fill=COL.get(m["model"], "#8b949e"))
        d.text((x0 + 120 + L, y + 10), fmt(v), font=FB, fill="#e6edf3")
        y += 44 + gap
    d.line([x0 + 110, top + 42, x0 + 110, y - gap + 6], fill="#30363d", width=2)
d.text((PAD, top + CH - 70), "Cheapest tokens (small-a) is not the cheapest finished task (mid-b) in this sample.",
       font=F, fill="#8b949e")

y = top + CH
d.line([PAD, y - 12, W - PAD, y - 12], fill="#30363d", width=1)
for ln in lines:
    st = ln.strip()
    if ln.startswith("$"):
        d.text((PAD, y), ln, font=FB, fill="#e6edf3")
    elif st.startswith("cheapest per passed") or "under, so cheaper" in st:
        d.text((PAD, y), ln, font=FB, fill="#3fb950")
    elif st.startswith("rank flipped") or "over, so pricier" in st or "x the cost per passed" in st:
        d.text((PAD, y), ln, font=FB, fill="#f85149")
    else:
        d.text((PAD, y), ln, font=F, fill="#c9d1d9")
    y += LH
img.save(os.path.join(ROOT, "demo.png"))
print("wrote demo.png")
