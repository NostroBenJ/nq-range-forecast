"""Reverse or continue when the day's range reaches its forecast? Stdlib only.
See PREREGISTRATION_TOUCH.md.   py -3.14 touch_test.py
"""
from __future__ import annotations

import csv
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import range_model as rm

HERE = Path(__file__).resolve().parent
TICK = 0.25
COMM = 0.50                  # points: $1.00 round trip on one MNQ
PT_USD = 2.0                 # MNQ dollars per point
LAST_TOUCH = 15 * 60 + 30    # touches in bars opening at or after 15:30 are not used
GAP = 0.005                  # adjacent regular-session bars this far apart: a data error, day dropped
STOP_K = 0.25                # protective stop distance, in forecasts
LEVELS = (0.8, 1.0, 1.2)


# ----------------------------------------------------------------------------- bars


def nt_rth() -> dict[date, list[tuple]]:
    """Regular-session 1-minute bars per day: (minute at open, o, h, l, c). NT stamps bars at their close."""
    out: dict[date, list[tuple]] = {}
    with open(rm.NT_1M, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            s = r[0]
            m = int(s[11:13]) * 60 + int(s[14:16]) - 1
            if rm.RTH_OPEN <= m < rm.RTH_CLOSE:
                d = date(int(s[:4]), int(s[5:7]), int(s[8:10]))
                out.setdefault(d, []).append((m, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
    return out


def lse_rth() -> dict[date, list[tuple]]:
    """Regular-session 5-minute bars per day. The LSE file is stamped at the bar's open in UTC."""
    out: dict[date, list[tuple]] = {}
    memo: dict[str, tuple[date, int]] = {}
    with open(rm.LSE_5M, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            s = r[0]
            hit = memo.get(s[:13])
            if hit is None:
                u = datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), tzinfo=timezone.utc)
                e = u.astimezone(rm.NY)
                hit = memo[s[:13]] = (e.date(), e.hour * 60)
            d, base = hit
            m = base + int(s[14:16])
            if rm.RTH_OPEN <= m < rm.RTH_CLOSE:
                out.setdefault(d, []).append((m, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
    return out


def has_gap(bars: list[tuple], step: int) -> bool:
    return any(b[0] - a[0] == step and abs(b[1] - a[4]) > GAP * a[4] for a, b in zip(bars, bars[1:]))


# ----------------------------------------------------------------------------- the event


def find_touch(bars: list[tuple], F: float):
    """The first bar whose running range reaches F.
    Returns {"i", "minute", "side", "level"}, the string "ambiguous" when that bar made both a new
    high and a new low (or alone spans F), or None when the range never gets there."""
    H = L = None
    for i, (m, o, h, l, c) in enumerate(bars):
        if H is None:
            if h - l >= F:
                return "ambiguous"
            H, L = h, l
            continue
        new_hi, new_lo = h > H, l < L
        if max(H, h) - min(L, l) >= F:
            if new_hi and new_lo:
                return "ambiguous"
            if new_hi:
                return {"i": i, "minute": m, "side": 1, "level": math.ceil((L + F) / TICK - 1e-9) * TICK}
            return {"i": i, "minute": m, "side": -1, "level": math.floor((H - F) / TICK + 1e-9) * TICK}
        H, L = max(H, h), min(L, l)
    return None


def signed(bars: list[tuple], side: int) -> list[tuple]:
    """Mirror a down-touch day so that every rule below reads as an up-touch: + is continuation."""
    if side == 1:
        return bars
    return [(m, -o, -l, -h, -c) for m, o, h, l, c in bars]


def close_return(bars: list[tuple], t: dict, F: float) -> float:
    """Primary statistic R: side * (close - level) / F."""
    return t["side"] * (bars[-1][4] - t["level"]) / F


def first_hit(bars: list[tuple], t: dict, F: float, k: float):
    """+1 if P + kF comes before P - kF, -1 if the reverse, 0 if neither by the close, None if a bar
    leaves the order unknown. In the touch bar every price above P is after the touch (the bar opened
    below P) and so is the close, but the low may be from before the touch."""
    sb = signed(bars, t["side"])
    P = t["side"] * t["level"]
    U, D = P + k * F, P - k * F
    i = t["i"]
    _, o, h, l, c = sb[i]
    up = h >= U
    if c <= D:
        return None if up else -1
    if l <= D:
        return None
    if up:
        return 1
    for _, o, h, l, c in sb[i + 1:]:
        up, dn = h >= U, l <= D
        if up and dn:
            return None
        if up:
            return 1
        if dn:
            return -1
    return 0


def follow(bars: list[tuple], t: dict, F: float) -> float:
    """Stop order at P with the touch, stop STOP_K*F back, else out at the close. Net points.
    A stop the touch bar's low could have reached counts as hit (worst case)."""
    sb = signed(bars, t["side"])
    P = t["side"] * t["level"]
    i = t["i"]
    _, o, h, l, c = sb[i]
    entry = max(P, o) + TICK
    stop = P - STOP_K * F
    if l <= stop:
        return stop - TICK - entry - COMM
    for _, o, h, l, c in sb[i + 1:]:
        if o <= stop:
            return o - TICK - entry - COMM
        if l <= stop:
            return stop - TICK - entry - COMM
    return sb[-1][4] - TICK - entry - COMM


def fade(bars: list[tuple], t: dict, F: float) -> float | None:
    """Limit order at P against the touch, stop STOP_K*F beyond, else out at the close. Net points,
    or None when price never traded a tick through P (no fill)."""
    sb = signed(bars, t["side"])
    P = t["side"] * t["level"]
    i = t["i"]
    _, o, h, l, c = sb[i]
    if h < P + TICK:
        return None
    entry = max(P, o)
    stop = P + STOP_K * F
    if h >= stop:
        return entry - (max(stop, o) + TICK) - COMM
    for _, o, h, l, c in sb[i + 1:]:
        if o >= stop:
            return entry - (o + TICK) - COMM
        if h >= stop:
            return entry - (stop + TICK) - COMM
    return entry - (sb[-1][4] + TICK) - COMM


# ----------------------------------------------------------------------------- one era


def collect(oos: list[dict], bars_by_day: dict[date, list[tuple]], step: int):
    """Events per level, with drop counts. oos: rows from range_model.walk_forward."""
    events = {k: [] for k in LEVELS}
    drops: Counter = Counter()
    for r in oos:
        d, F = r["d"], math.exp(r["f_model"])
        bars = bars_by_day.get(d)
        if not bars:
            drops["no bars"] += 1
            continue
        if bars[-1][0] < rm.RTH_CLOSE - step:
            drops["no closing bar"] += 1
            continue
        if has_gap(bars, step):
            drops["gap > 0.5%"] += 1
            continue
        for k in LEVELS:
            t = find_touch(bars, k * F)
            if t is None:
                drops[f"{k}F never reached"] += 1
                continue
            if t == "ambiguous":
                drops[f"{k}F ambiguous bar"] += 1
                continue
            if t["minute"] >= LAST_TOUCH:
                drops[f"{k}F reached after 15:30"] += 1
                continue
            ev = {"d": d, "F": F, **t, "R": close_return(bars, t, F)}
            if k == 1.0:
                ev["hit25"] = first_hit(bars, t, F, 0.25)
                ev["hit50"] = first_hit(bars, t, F, 0.5)
                ev["follow"] = follow(bars, t, F)
                ev["fade"] = fade(bars, t, F)
            events[k].append(ev)
    return events, drops


def mean_t(xs: list[float]) -> tuple[float, float]:
    return (sum(xs) / len(xs), rm.newey_west_t(xs, 5)) if len(xs) > 6 else (float("nan"), float("nan"))


def max_drawdown(pnl: list[float]) -> float:
    peak = cum = dd = 0.0
    for x in pnl:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def trade_summary(name: str, evs: list[dict], key: str) -> tuple[list[str], dict]:
    pts = [e[key] for e in evs if e[key] is not None]
    usd = [x * PT_USD for x in pts]
    m, t = mean_t(pts)
    years = (evs[-1]["d"] - evs[0]["d"]).days / 365.25 if evs else 1
    trim = sorted(usd)[:len(usd) - math.ceil(0.01 * len(usd))]
    s = {"n": len(pts), "mean_usd": m * PT_USD, "t": t, "without_best_1pct": sum(trim),
         "total": sum(usd)}
    lines = [f"    {name:7} trades {len(pts):4d}  net ${m * PT_USD:+7.2f}/trade  t {t:+.2f}  "
             f"wins {sum(x > 0 for x in usd) / len(usd):.0%}  total ${sum(usd):+,.0f} "
             f"(${sum(usd) / years:+,.0f}/yr)  worst ${min(usd):+,.0f}  max drawdown ${max_drawdown(usd):,.0f}  "
             f"without best 1%: ${sum(trim):+,.0f}"]
    return lines, s


def report_era(name: str, n_days: int, events: dict, drops: Counter) -> tuple[list[str], dict]:
    out = []
    p = out.append
    ev = events[1.0]
    R = [e["R"] for e in ev]
    m, t = mean_t(R)
    p(f"=== Era {name}: {n_days} out-of-sample days")
    p(f"  dropped / not used: {dict(sorted(drops.items()))}")
    p(f"  touches of the forecast used: {len(ev)} ({len(ev) / n_days:.0%} of days); "
      f"up {sum(e['side'] == 1 for e in ev)}, down {sum(e['side'] == -1 for e in ev)}")
    p("")
    p(f"  PRIMARY  R = side*(close - level)/F:  mean {m:+.4f} F   Newey-West t {t:+.2f}")
    p(f"           median {sorted(R)[len(R) // 2]:+.4f} F,  closed beyond the level (continuation) {sum(x > 0 for x in R) / len(R):.0%}"
      f",  mean in points {sum(e['R'] * e['F'] for e in ev) / len(ev):+.1f}")
    for y in sorted({e["d"].year for e in ev}):
        ry = [e["R"] for e in ev if e["d"].year == y]
        my, ty = mean_t(ry)
        p(f"    {y}: n {len(ry):3d}  mean R {my:+.4f}  t {ty:+.2f}")
    for lo, hi, lab in ((570, 720, "before 12:00"), (720, 840, "12:00-14:00"), (840, LAST_TOUCH, "14:00-15:30")):
        rb = [e["R"] for e in ev if lo <= e["minute"] < hi]
        if rb:
            mb, tb = mean_t(rb)
            p(f"    touched {lab:12}: n {len(rb):3d}  mean R {mb:+.4f}  t {tb:+.2f}")
    p("")
    p("  SECONDARY (no verdict)")
    for key, k in (("hit25", 0.25), ("hit50", 0.5)):
        c = Counter(e[key] for e in ev)
        cont, rev, amb, nei = c[1], c[-1], c[None], c[0]
        res = cont + rev
        ind = [1.0 if e[key] == 1 else 0.0 for e in ev if e[key] in (1, -1)]
        tt = rm.newey_west_t([x - 0.5 for x in ind], 5) if len(ind) > 6 else float("nan")
        p(f"    first to +-{k}F: continuation first {cont}, reversal first {rev}, neither by close {nei}, ambiguous {amb}")
        p(f"      continuation share {cont / res:.1%} (t vs 50%: {tt:+.2f});  bounds with ambiguous "
          f"{cont / (res + amb):.1%} - {(cont + amb) / (res + amb):.1%}")
    for k in LEVELS:
        rk = [e["R"] for e in events[k]]
        mk, tk = mean_t(rk)
        p(f"    level {k:.1f}F: n {len(rk):4d}  mean R {mk:+.4f}  t {tk:+.2f}")
    p("")
    p("  TRADES, one MNQ, net of $1 commission and 1-tick slippage")
    fl, fs = trade_summary("follow", ev, "follow")
    fd, ds = trade_summary("fade", ev, "fade")
    out += fl + fd
    p("")
    return out, {"mean": m, "t": t, "n": len(ev), "follow": fs, "fade": ds}


def main() -> str:
    print("loading bars and walk-forward forecasts...")
    eras = rm.load_eras()
    a_oos = rm.walk_forward(eras["A (NT 2021-2026)"][0])
    b_oos = rm.walk_forward(eras["B (LSE 2016-2020)"][0])
    a_ev, a_dr = collect(a_oos, nt_rth(), 1)
    b_ev, b_dr = collect(b_oos, lse_rth(), 5)
    la, sa = report_era("A (NT 1-minute, 2022-2026)", len(a_oos), a_ev, a_dr)
    lb, sb = report_era("B (LSE 5-minute, 2017-2020)", len(b_oos), b_ev, b_dr)
    out = la + lb
    if sa["mean"] > 0 and sa["t"] >= 2.0 and sb["mean"] > 0:
        verdict, rule = "CONTINUES", "follow"
    elif sa["mean"] < 0 and sa["t"] <= -2.0 and sb["mean"] < 0:
        verdict, rule = "REVERSES", "fade"
    else:
        verdict, rule = "NEITHER", None
    out.append("=== Pre-registered verdict")
    out.append(f"  era A mean R {sa['mean']:+.4f} (t {sa['t']:+.2f}), era B mean R {sb['mean']:+.4f} (t {sb['t']:+.2f})")
    out.append(f"  VERDICT: {verdict}")
    if rule:
        fa, fb = sa[rule], sb[rule]
        ok = fa["mean_usd"] > 0 and fb["mean_usd"] > 0 and fa["t"] >= 2.0 and fa["without_best_1pct"] > 0
        out.append(f"  {rule}: era A ${fa['mean_usd']:+.2f}/trade t {fa['t']:+.2f}, era B ${fb['mean_usd']:+.2f}/trade,"
                   f" era A without best 1% ${fa['without_best_1pct']:+,.0f}  ->  {'TRADEABLE' if ok else 'NOT TRADEABLE'}")
    else:
        out.append("  no direction, so neither rule is tradeable by the pre-registered rule")
    text = "\n".join(out)
    (HERE / "results_touch.txt").write_text(text + "\n", encoding="utf-8")
    return text


if __name__ == "__main__":
    print(main())
