"""Does the indicator's walk (RangeCore.SessionWalker) find the same touches as touch_test.py? Stdlib only.

Reads Harness's walk CSV (the walker on the NinjaTrader 1-minute bars) and, for every session with a
forecast, runs touch_test.find_touch on the same bars with the same forecast. Compares whether and when the
day reached its forecast, the side, the level (touch_test rounds it to the tick), the day's range, and the
closed-beyond tally.   py -3.14 compare_walk.py walk_out.csv
"""
from __future__ import annotations

import csv
import math
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import touch_test as tt  # noqa: E402


def main(path: str) -> int:
    rth = tt.nt_rth()
    n = agree = range_agree = 0
    mism = []
    py_tally = [0, 0, 0]          # days, reached before 15:30, closed beyond
    cs_tally = [0, 0, 0]
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        fc = float(r["fc"])
        if math.isnan(fc):
            continue
        d = date.fromisoformat(r["session"])
        bars = rth.get(d)
        if not bars:
            continue
        n += 1
        reached, side, minute = r["reached"] == "1", int(r["side"]), int(r["minute"])
        level, last_close = float(r["level"]), float(r["last_close"])
        t = tt.find_touch(bars, fc)
        if t is None:
            ok = not reached
        elif t == "ambiguous":
            ok = reached and side == 0
        else:
            ok = reached and side == t["side"] and minute == t["minute"] and abs(level - t["level"]) < tt.TICK
        agree += ok
        if not ok and len(mism) < 10:
            mism.append((d, t, reached, side, minute, level))
        actual = float(r["actual"])
        py_range = max(b[2] for b in bars) - min(b[3] for b in bars)
        if r["complete"] == "1":
            range_agree += abs(actual - py_range) < 1e-9
            py_tally[0] += 1
            cs_tally[0] += 1
            if isinstance(t, dict) and t["minute"] < tt.LAST_TOUCH:
                py_tally[1] += 1
                py_tally[2] += t["side"] * (bars[-1][4] - t["level"]) > 0
            if reached and side != 0 and minute < tt.LAST_TOUCH:
                cs_tally[1] += 1
                cs_tally[2] += side * (last_close - level) > 0
        else:
            range_agree += 1                      # the walker records no range for a half day
    complete = sum(r["complete"] == "1" for r in rows if not math.isnan(float(r["fc"])))
    print(f"sessions with a forecast and NinjaTrader bars: {n}")
    print(f"touch (reached or not, bar, side, level within a tick) agrees with touch_test.find_touch: {agree} / {n}")
    for m in mism:
        print("  differs:", m)
    print(f"day's range agrees on {range_agree} / {n} ({complete} complete)")
    print(f"tally, touch_test: {py_tally[1]} of {py_tally[0]} reached before 15:30, {py_tally[2]} closed beyond"
          f" ({py_tally[2] / max(py_tally[1], 1):.1%})")
    print(f"tally, walker:     {cs_tally[1]} of {cs_tally[0]} reached before 15:30, {cs_tally[2]} closed beyond"
          f" ({cs_tally[2] / max(cs_tally[1], 1):.1%})")
    ok = agree == n and range_agree == n and py_tally[:2] == cs_tally[:2] and abs(py_tally[2] - cs_tally[2]) <= 3
    print("WALK MATCHES touch_test" if ok else "WALK DIFFERS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
