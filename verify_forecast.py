"""Checks for the morning calculator. Stdlib only.  py -3.14 verify_forecast.py

Unit checks first, then the one that matters: on the days both sources cover, the Yahoo-built inputs
and forecast must match the NinjaTrader-built ones the model was fit and tested on.
"""
from __future__ import annotations

import copy
import math
import statistics
import sys
from datetime import date, datetime, timedelta

import forecast_today as ft
import range_model as rm

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


NY = rm.NY
T = lambda y, mo, d, h, mi: datetime(y, mo, d, h, mi, tzinfo=NY)

# 1. which session a run is for
check("Sunday 22:00 -> Monday", ft.target_day(T(2026, 10, 4, 22, 0)) == date(2026, 10, 5))
check("Monday 08:00 -> Monday", ft.target_day(T(2026, 10, 5, 8, 0)) == date(2026, 10, 5))
check("Monday 15:59 -> Monday", ft.target_day(T(2026, 10, 5, 15, 59)) == date(2026, 10, 5))
check("Monday 16:00 -> Tuesday", ft.target_day(T(2026, 10, 5, 16, 0)) == date(2026, 10, 6))
check("Friday 17:00 -> Monday", ft.target_day(T(2026, 10, 9, 17, 0)) == date(2026, 10, 12))

# 2. overnight membership matches daily_stats
check("Sunday 18:00 bar is Monday's overnight", ft.in_overnight(T(2026, 10, 4, 18, 0), date(2026, 10, 5)))
check("Monday 09:25 bar is Monday's overnight", ft.in_overnight(T(2026, 10, 5, 9, 25), date(2026, 10, 5)))
check("Monday 09:30 bar is not", not ft.in_overnight(T(2026, 10, 5, 9, 30), date(2026, 10, 5)))
check("Monday 17:55 bar is not Monday's", not ft.in_overnight(T(2026, 10, 5, 17, 55), date(2026, 10, 5)))

# 3. front contract, as NinjaTrader's series rolled it
check("2026-09-10 is still September", ft.front_contract(date(2026, 9, 10)) == (2026, 9))
check("2026-09-11 is December", ft.front_contract(date(2026, 9, 11)) == (2026, 12))
check("2026-10-05 is December, NQZ26.CME", ft.symbol(ft.front_contract(date(2026, 10, 5))) == "NQZ26.CME")
check("2026-12-11 is March 2027, NQH27.CME", ft.symbol(ft.front_contract(date(2026, 12, 11))) == "NQH27.CME")
check("2027-01-15 is March 2027", ft.front_contract(date(2027, 1, 15)) == (2027, 3))

# 4. mixed-contract detection
def bar(t, o, h, l, c):
    return (t, o, h, l, c)

ts = [T(2026, 9, 14, 11, 0) + timedelta(minutes=5 * i) for i in range(6)]
new = {t: bar(t, 30000 + i, 30010 + i, 29990 + i, 30000 + i) for i, t in enumerate(ts)}   # the new contract
old_then_new = [bar(t, b[1] - 300, b[2] - 300, b[3] - 300, b[4] - 300) if i < 3 else b
                for i, (t, b) in enumerate(new.items())]
check("bars that flip from one contract to the other are caught",
      ft.mixed(old_then_new, {"NEW": new}) is not None)
all_old = [bar(t, b[1] - 300, b[2] - 300, b[3] - 300, b[4] - 300) for t, b in new.items()]
check("bars all on the other contract are not flagged", ft.mixed(all_old, {"NEW": new}) is None)
inside = list(new.values())
inside[3] = bar(ts[3], 29700.0, 30013.0, 29690.0, 30003.0)      # opens on the old, closes on the new
check("a switch inside one bar is caught", ft.mixed(inside, {"NEW": new}) is not None)
gap = list(new.values())
gap[3] = bar(ts[3], 30303.0, 30313.0, 30293.0, 30303.0)
check("a 0.5% gap between adjacent bars is caught with no reference at all", ft.mixed(gap, {}) is not None)
thin = {t: bar(t, b[1] + 300, b[1] + 301, b[1] + 299, b[1] + 300) for t, b in new.items()}  # thin back month
wide = list(new.values())
wide[2] = bar(ts[2], 30000.0, 30200.0, 29950.0, 30100.0)        # a real 250-point bar
check("a wide real bar is not flagged against a thin back month", ft.mixed(wide, {"BACK": thin}) is None)
check("a clean window is not flagged", ft.mixed(list(new.values()), {"NEW": new}) is None)

# 5. features use only bars that open before 09:30 on d
days = []
k = date(2026, 6, 1)
while len(days) < 30:
    if k.weekday() < 5:
        days.append(k)
    k += timedelta(days=1)
syn = []
for dd in days:
    for m in list(range(0, 1020, 5)) + list(range(1080, 1440, 5)):
        p = 1000 + (m % 97) + dd.day
        syn.append((dd, m, p + 3.0, p - 3.0))
d = days[-1]
vix = [(dd, 15.0) for dd in days]
f1, _ = ft.features_for(rm.daily_stats(iter(syn)), d, vix)
syn2 = [(dd, m, h * 2, l / 2) if (dd, m) >= (d, rm.RTH_OPEN) else (dd, m, h, l) for dd, m, h, l in syn]
f2, _ = ft.features_for(rm.daily_stats(iter(syn2)), d, vix)
check("changing bars from 09:30 on d leaves d's inputs unchanged", f1 == f2)
check("VIX input is the close dated before d", math.exp(f1["lvix"]) == 15.0)
no_on = [x for x in syn if not ft.in_overnight(T(x[0].year, x[0].month, x[0].day, x[1] // 60, x[1] % 60), d)]
try:
    ft.features_for(rm.daily_stats(iter(no_on)), d, vix)
    check("no overnight bars -> refuses to forecast", False)
except ValueError:
    check("no overnight bars -> refuses to forecast", True)

# 6. Yahoo against NinjaTrader on the days both cover
print("\nfetching Yahoo and loading the NinjaTrader file...")
nqf, refs, yv = ft.fetch(datetime.now(NY).date())
print(f"  named contracts served: {sorted(refs)}")
ys, notes = ft.clean_stats(nqf, refs)
for k, msg in notes:
    print(f"  data: {k} {msg}")
check("the 2026-09-14 flip is found and that session rebuilt",
      any(k == date(2026, 9, 14) and "regular session rebuilt" in msg for k, msg in notes))
ns = rm.daily_stats(rm.nt_bars())
both = [k for k in sorted(ys) if k in ns and k.weekday() < 5 and k <= date(2026, 9, 25)
        and ys[k]["n"] >= ft.MIN_RTH_BARS and ns[k]["n"] >= 380]
print(f"  {len(both)} common complete sessions {both[0]} .. {both[-1]}")
rd = {k: (ys[k]["hi"] - ys[k]["lo"]) - (ns[k]["hi"] - ns[k]["lo"]) for k in both}
check("regular-session ranges agree (median |diff| <= 1 pt, max <= 5)",
      statistics.median(abs(x) for x in rd.values()) <= 1 and max(abs(x) for x in rd.values()) <= 5,
      f"max {max(abs(x) for x in rd.values()):.2f} on {max(rd, key=lambda k: abs(rd[k]))}")
od = {k: (ys[k]["on_hi"] - ys[k]["on_lo"]) - (ns[k]["on_hi"] - ns[k]["on_lo"]) for k in both
      if ys[k]["on_n"] and ns[k]["on_n"]}
wk = {k: v for k, v in od.items() if k.weekday() != 0}
mo = {k: v for k, v in od.items() if k.weekday() == 0}
check("Tuesday-Friday overnight ranges agree (median |diff| <= 1 pt, max <= 5)",
      statistics.median(abs(x) for x in wk.values()) <= 1 and max(abs(x) for x in wk.values()) <= 5,
      f"max {max(abs(x) for x in wk.values()):.2f} on {max(wk, key=lambda k: abs(wk[k]))}")
print(f"  Monday overnight differences (Yahoo minus NinjaTrader, the missing Sunday 18:00-18:10): "
      + ", ".join(f"{k:%m-%d} {v:+.1f}" for k, v in mo.items()))
check("Monday overnight is never LONGER on Yahoo (it can only miss bars)", all(v <= 0.01 for v in mo.values()))

# the whole pipeline: forecasts built from Yahoo vs from the NinjaTrader rows, same model.
# Mondays get the NinjaTrader overnight high/low, which is what --overnight does with the chart's.
model = ft.load_model()
nrows = {r["d"]: r for r in rm.build_rows(ns, date(2021, 1, 4), date(2026, 9, 25), 380,
                                           rm.vix_before(rm.load_vix()))[0]}
diffs = []
for k in both:
    if k not in nrows:
        continue
    st = ys
    if k.weekday() == 0:
        st = copy.deepcopy(ys)
        st[k]["on_hi"], st[k]["on_lo"] = ns[k]["on_hi"], ns[k]["on_lo"]
    try:
        yf, _ = ft.features_for(st, k, yv)
    except ValueError:
        continue
    diffs.append((k, ft.forecast(model, yf)[0] / ft.forecast(model, nrows[k])[0] - 1))
print(f"  {len(diffs)} sessions with 22 Yahoo sessions behind them")
worst = max(diffs, key=lambda x: abs(x[1])) if diffs else None
check("Yahoo-built forecast within 2% of the NinjaTrader-built one on every common session",
      bool(diffs) and abs(worst[1]) <= 0.02, f"worst {worst[1]:+.2%} on {worst[0]}" if diffs else "none")

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
