"""Does the TradingView indicator's design reproduce the model's inputs? Stdlib only.

NQ_Range_Forecast.pine builds its inputs from 30-minute bars of the extended session (f_ctx) and
reads them on the first 09:30 bar. This replays that state machine line for line on 30-minute bars
aggregated from the NinjaTrader 1-minute file, applies the indicator's VIX and calendar rules, and
compares every session's inputs and forecast with range_model's (the numbers the model was fit and
tested on).   py -3.14 pine_port_check.py
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import forecast_today as ft
import range_model as rm

HERE = Path(__file__).resolve().parent


def bars_30m():
    """Clock-aligned 30-minute (date, minute at open, high, low) from the 1-minute file."""
    cur = None
    for d, m, h, l in rm.nt_bars():
        key = (d, m - m % 30)
        if cur is None or key != cur[0]:
            if cur is not None:
                yield cur[0][0], cur[0][1], cur[1], cur[2]
            cur = [key, h, l]
        else:
            cur[1], cur[2] = max(cur[1], h), min(cur[2], l)
    yield cur[0][0], cur[0][1], cur[1], cur[2]


def replay_ctx():
    """f_ctx() from the .pine file, one 30-minute bar at a time. Returns {date: tuple as of the last
    bar before 09:30 on that date}, which is what the chart's 09:30 bar reads."""
    rngs: list[float] = []
    r_hi = r_lo = None
    r_n = 0
    o_hi = o_lo = None
    o_n = 0
    mp = -1
    snap = {}
    for d, m0, h, l in bars_30m():
        rth = 570 <= m0 < 960
        was_r = 570 <= mp < 960
        if was_r and not rth:
            if r_n >= 13:
                rngs.append(r_hi - r_lo)
                if len(rngs) > 22:
                    rngs.pop(0)
            r_hi = r_lo = None
            r_n = 0
        if rth:
            r_hi = h if r_hi is None else max(r_hi, h)
            r_lo = l if r_lo is None else min(r_lo, l)
            r_n += 1
        if m0 >= 1080 and mp < 1080:
            o_hi, o_lo, o_n = h, l, 1
        elif m0 >= 1080 or m0 < 570:
            o_hi = h if o_hi is None else max(o_hi, h)
            o_lo = l if o_lo is None else min(o_lo, l)
            o_n += 1
        mp = m0
        if m0 < 570:
            n = len(rngs)
            snap[d] = (rngs[-1] if n else None,
                       sum(rngs[-5:]) / 5 if n >= 5 else None,
                       sum(rngs) / 22 if n >= 22 else None,
                       n, (o_hi - o_lo) if o_hi is not None else None, o_n)
    return snap


def main() -> str:
    model = json.loads((HERE / "model.json").read_text(encoding="utf-8"))
    vf = rm.vix_before(rm.load_vix())
    rows, _ = rm.build_rows(rm.daily_stats(rm.nt_bars()), rm.date(2021, 1, 4), rm.date(2026, 9, 25), 380, vf)
    snap = replay_ctx()
    out = []
    same = {f: 0 for f in rm.FEATURES}
    diffs, worst, missing, short_on = [], [], 0, 0
    for r in rows:
        s = snap.get(r["d"])
        if s is None or s[2] is None or s[3] < 22 or not s[4]:
            missing += 1
            continue
        r1, m5, m22, n, on_r, on_n = s
        short_on += on_n < 29
        pine = {"lr1": math.log(r1), "lr5": math.log(m5), "lr22": math.log(m22), "lon": math.log(on_r),
                "lvix": math.log(vf(r["d"])), "fomc": 1.0 if r["d"] in ft.FOMC else 0.0,
                "mon": 1.0 if r["d"].weekday() == 0 else 0.0, "fri": 1.0 if r["d"].weekday() == 4 else 0.0}
        for f in rm.FEATURES:
            same[f] += abs(pine[f] - r[f]) < 1e-9
        fp = ft.forecast(model, pine)[0]
        fm = ft.forecast(model, r)[0]
        diffs.append(abs(fp / fm - 1))
        worst.append((abs(fp / fm - 1), r["d"], fp, fm,
                      [f for f in rm.FEATURES if abs(pine[f] - r[f]) >= 1e-9]))
    n = len(diffs)
    out.append(f"{len(rows)} model sessions 2021-02 .. 2026-09; {n} compared, {missing} without a 22-session 30-minute history")
    out.append("inputs identical to 1e-9 (share of sessions):  " + "  ".join(f"{f} {same[f] / n:.1%}" for f in rm.FEATURES))
    out.append(f"forecast difference: median {statistics.median(diffs):.4%}, within 0.1% on {sum(x < 0.001 for x in diffs) / n:.1%},"
               f" within 1% on {sum(x < 0.01 for x in diffs) / n:.1%}, max {max(diffs):.2%}")
    out.append(f"sessions whose overnight had fewer than 29 of 31 half-hours: {short_on}")
    out.append("largest differences (|diff|, session, indicator, model, inputs that differ):")
    for w in sorted(worst, reverse=True)[:8]:
        out.append(f"  {w[0]:.2%}  {w[1]}  {w[2]:.1f}  {w[3]:.1f}  {w[4]}")
    return "\n".join(out)


if __name__ == "__main__":
    text = main()
    print(text)
    (HERE / "pine_port_check.txt").write_text(text + "\n", encoding="utf-8")
