"""Checks run before the real result is read. Stdlib only.  py -3.14 verify.py [--data]"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta

import range_model as rm

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


# 1. OLS recovers known coefficients
rng = random.Random(7)
true = [0.3, 1.5, -2.0, 0.25]
xs = [[rng.gauss(0, 1) for _ in range(3)] for _ in range(500)]
ys = [true[0] + sum(b * v for b, v in zip(true[1:], x)) for x in xs]
beta = rm.ols(xs, ys)
err = max(abs(a - b) for a, b in zip(beta, true))
check("OLS recovers exact coefficients on noiseless data", err < 1e-9, f"max err {err:.1e}")

# 2. Range and overnight on hand-built bars
D = date(2024, 5, 7)        # a Tuesday
bars = [
    (D - timedelta(days=1), 1080, 100.0, 99.0),   # 18:00 the day before -> D's overnight
    (D - timedelta(days=1), 1079, 500.0, 1.0),    # 17:59 the day before: maintenance hour, belongs to nobody
    (D, 300, 103.0, 98.0),                        # 05:00 -> overnight
    (D, 569, 104.0, 100.0),                       # 09:29 -> overnight (opens before 09:30)
    (D, 570, 110.0, 105.0),                       # 09:30 -> regular session
    (D, 959, 112.0, 101.0),                       # 15:59 -> regular session
    (D, 960, 200.0, 50.0),                        # 16:00 -> neither
]
s = rm.daily_stats(iter(bars))[D]
check("regular-session range = 112 - 101", s["hi"] - s["lo"] == 11.0 and s["n"] == 2, str(s))
check("overnight range = 104 - 98 over [18:00 d-1, 09:30 d)", s["on_hi"] - s["on_lo"] == 6.0 and s["on_n"] == 3, str(s))
mon = date(2024, 5, 6)
s2 = rm.daily_stats(iter([(mon - timedelta(days=1), 1080, 10.0, 9.0)]))
check("Sunday 18:00 bar goes to Monday's overnight", mon in s2 and s2[mon]["on_n"] == 1)

# 3. No look-ahead: change every bar at/after 09:30 on day d, d's features must not move
def synth(seed, days):
    r = random.Random(seed)
    out, p = [], 1000.0
    for d in days:
        for m in list(range(0, 1020)) + list(range(1080, 1440)):
            p += r.gauss(0, 1)
            out.append((d, m, p + abs(r.gauss(0, 1)), p - abs(r.gauss(0, 1))))
    return out

days = []
d = date(2024, 1, 2)
while len(days) < 40:
    if d.weekday() < 5:
        days.append(d)
    d += timedelta(days=1)
target = days[30]
base = synth(1, days)
fut = [(dd, m, h * 3, l * 0.5) if (dd, m) >= (target, rm.RTH_OPEN) else (dd, m, h, l) for dd, m, h, l in base]
vf = lambda _d: 15.0
r1 = {r["d"]: r for r in rm.build_rows(rm.daily_stats(iter(base)), days[0], days[-1], 380, vf)[0]}
r2 = {r["d"]: r for r in rm.build_rows(rm.daily_stats(iter(fut)), days[0], days[-1], 380, vf)[0]}
same = all(r1[target][f] == r2[target][f] for f in rm.FEATURES)
check("features of day d unchanged when bars from d 09:30 on change", same)
check("...while d's target does change (the test can see a change)", r1[target]["y"] != r2[target]["y"])
earlier = all(r1[dd]["y"] == r2[dd]["y"] for dd in r1 if dd < target)
check("...and earlier days are untouched", earlier)

# 4. VIX lookup is strictly before d
vix = [(date(2024, 1, 2), 10.0), (date(2024, 1, 3), 20.0)]
f = rm.vix_before(vix)
check("VIX for d uses the close dated before d", f(date(2024, 1, 3)) == 10.0 and f(date(2024, 1, 4)) == 20.0
      and f(date(2024, 1, 2)) is None)

# 5. Band coverage on i.i.d. normal residuals
r = random.Random(3)
train = [r.gauss(0, 1) for _ in range(2000)]
q10, q90 = rm.quantile(train, .1), rm.quantile(train, .9)
test = [r.gauss(0, 1) for _ in range(20000)]
cov = sum(q10 <= x <= q90 for x in test) / len(test)
check("80% band covers about 80% of fresh normal draws", 0.78 <= cov <= 0.82, f"{cov:.3f}")

# 6. Newey-West with 0 lags is the plain t (population variance)
x = [r.gauss(0.1, 1) for _ in range(300)]
mu = sum(x) / len(x)
plain = mu / math.sqrt(sum((v - mu) ** 2 for v in x) / len(x) / len(x))
check("Newey-West t with 0 lags equals the plain t", abs(rm.newey_west_t(x, 0) - plain) < 1e-12)

# 7. Walk-forward never trains on its own test year
rows = [{"d": date(2020 + i // 50, 1, 1) + timedelta(days=i % 50), "y": r.gauss(0, 1),
         **{f: r.gauss(0, 1) for f in rm.FEATURES}} for i in range(200)]
for row in rows:
    row["range"] = math.exp(row["y"])
oos = rm.walk_forward(rows)
check("first year is training only", min(o["d"].year for o in oos) == 2021)
y21 = [rw for rw in rows if rw["d"].year < 2021]
b21 = rm.ols([[rw[f] for f in rm.FEATURES] for rw in y21], [rw["y"] for rw in y21])
check("2021 forecasts use the fit on years before 2021 only",
      all(abs(a - b) < 1e-12 for a, b in zip(oos[0]["beta"], b21)))

# 8. Real data: session sizes (slow, optional)
if "--data" in sys.argv:
    nt = rm.daily_stats(rm.nt_bars())
    ns = [v["n"] for k, v in nt.items() if k.weekday() < 5 and v["n"] > 0]
    full = sum(n == 390 for n in ns) / len(ns)
    check("era A: most sessions have exactly 390 regular minutes", full > 0.9, f"{full:.1%}")
    lse = rm.daily_stats(rm.lse_bars())
    ls = [v["n"] for k, v in lse.items() if date(2016, 7, 1) <= k <= date(2020, 12, 31) and k.weekday() < 5 and v["n"] > 0]
    full = sum(n == 78 for n in ls) / len(ls)
    check("era B: most sessions have exactly 78 regular bars", full > 0.9, f"{full:.1%}")
    # the two eras do not overlap, so cross-check NT against LSE on 2021 where both exist
    both = [k for k in nt if k in lse and date(2021, 2, 1) <= k <= date(2021, 2, 28) and nt[k]["n"] == 390 and lse[k]["n"] == 78]
    diffs = [abs((nt[k]["hi"] - nt[k]["lo"]) - (lse[k]["hi"] - lse[k]["lo"])) for k in both]
    check("NT and LSE agree on Feb 2021 regular-session ranges", both and max(diffs) <= 2.0,
          f"{len(both)} days, max diff {max(diffs) if diffs else None}")

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
