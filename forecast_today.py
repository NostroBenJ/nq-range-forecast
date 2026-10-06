"""Morning NQ range forecast. Stdlib only.

    py -3.14 forecast_today.py                     the next session's forecast (today's, once it has begun)
    py -3.14 forecast_today.py --overnight 30210 30005    use your chart's overnight high and low (Mondays)
    py -3.14 forecast_today.py --date 2026-09-30   any session in the last ~60 days (not logged)
    py -3.14 forecast_today.py --grade             score every logged forecast against the range that happened
    py -3.14 forecast_today.py --refit             refit model.json on all NinjaTrader data (about a minute)

Data: Yahoo Finance NQ=F 5-minute bars (last 60 days, about 10 minutes delayed), the named quarterly
contracts Yahoo still serves (NQZ26.CME ...) to catch NQ=F switching contracts mid-session, and ^VIX
daily closes. Model: the OLS that passed PREREGISTRATION.md (RESULT.md), refit on every era-A session
2021-02 .. 2026-09-25 and saved in model.json. The forecast is the size of the 09:30-16:00 range, not
its direction, and it only uses bars that open before 09:30.

Two Yahoo defects this works around (verify_forecast.py measures both against NinjaTrader):
  * NQ=F can flip between the old and new contract around a roll, even inside a 5-minute bar
    (2026-09-14 11:30: a 384-point bar). A session where it mixes contracts is rebuilt from the named
    contract if that one is complete, and otherwise dropped, as the model drops incomplete sessions.
  * Every Yahoo series starts the Sunday evening session at 18:10, not 18:00. If the first ten
    minutes set the overnight high or low, Monday's overnight range comes out short: read the
    overnight high and low off your chart and pass --overnight.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import range_model as rm

HERE = Path(__file__).resolve().parent
MODEL_JSON = HERE / "model.json"
LOG_CSV = HERE / "forecast_log.csv"
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={iv}&range={rg}"

MIN_RTH_BARS = 75             # of 78 five-minute bars: the era-B completeness rule
MIN_ON_BARS = 170             # of 186 (184 on a Monday, see the docstring)
JUMP = 0.005                  # 0.5% between two contracts' prices, or across two adjacent bars
SAME = 0.0015                 # within 0.15%: the same contract
MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}

# FOMC statement days after range_model's frozen list (federalreserve.gov fomccalendars.htm, 2026-10-04).
EXTRA_FOMC = {date.fromisoformat(d) for d in """
2026-10-28 2026-12-09
2027-01-27 2027-03-17 2027-04-28 2027-06-09 2027-07-28 2027-09-15 2027-10-27 2027-12-08
""".split()}
FOMC = rm.FOMC | EXTRA_FOMC


# ----------------------------------------------------------------------------- model


def fit_final() -> dict:
    """OLS on every era-A session, with the 10th/90th percentile residuals for the 80% band."""
    vf = rm.vix_before(rm.load_vix())
    rows, _ = rm.build_rows(rm.daily_stats(rm.nt_bars()), date(2021, 1, 4), date(2026, 9, 25), 380, vf)
    beta = rm.ols([[r[f] for f in rm.FEATURES] for r in rows], [r["y"] for r in rows])
    res = [r["y"] - rm.predict(beta, [r[f] for f in rm.FEATURES]) for r in rows]
    model = {
        "features": rm.FEATURES, "beta": beta,
        "q10": rm.quantile(res, 0.10), "q90": rm.quantile(res, 0.90),
        "rmse": math.sqrt(sum(e * e for e in res) / len(res)),
        "n": len(rows), "fit_from": str(rows[0]["d"]), "fit_to": str(rows[-1]["d"]),
        "fitted": datetime.now(rm.NY).strftime("%Y-%m-%d %H:%M ET"),
    }
    MODEL_JSON.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return model


def load_model() -> dict:
    if not MODEL_JSON.exists():
        print("model.json missing: fitting on the NinjaTrader data (about a minute)...")
        return fit_final()
    return json.loads(MODEL_JSON.read_text(encoding="utf-8"))


def forecast(model: dict, feats: dict) -> tuple[float, float, float]:
    """(forecast, low edge, high edge) of the 80% band, in points."""
    f = rm.predict(model["beta"], [feats[k] for k in model["features"]])
    return math.exp(f), math.exp(f + model["q10"]), math.exp(f + model["q90"])


# ----------------------------------------------------------------------------- contracts


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def front_contract(d: date) -> tuple[int, int]:
    """(year, month) of the quarterly contract that is front for session d. It hands over to the next
    one on the Friday a week before expiry: NinjaTrader's series, which the model was fit on, is on
    September for the 2026-09-10 session and on December from 2026-09-11."""
    y, m = d.year, 3 * ((d.month + 2) // 3)
    while d >= third_friday(y, m) - timedelta(days=7):
        y, m = (y + 1, 3) if m == 12 else (y, m + 3)
    return y, m


def next_contract(ym: tuple[int, int]) -> tuple[int, int]:
    y, m = ym
    return (y + 1, 3) if m == 12 else (y, m + 3)


def symbol(ym: tuple[int, int]) -> str:
    return f"NQ{MONTH_CODE[ym[1]]}{ym[0] % 100:02d}.CME"


# ----------------------------------------------------------------------------- data


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})   # Yahoo wants a browser UA
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def yahoo_bars(raw: dict) -> list[tuple[datetime, float, float, float, float]]:
    """(bar open in New York time, o, h, l, c), time order, rows with nulls dropped."""
    res = raw["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for ts, o, h, l, c in zip(res.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"]):
        if None in (o, h, l, c):
            continue
        out.append((datetime.fromtimestamp(ts, timezone.utc).astimezone(rm.NY), o, h, l, c))
    return out


def yahoo_vix(raw: dict) -> list[tuple[date, float]]:
    res = raw["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(ts, timezone.utc).astimezone(rm.NY).date(), c)
            for ts, c in zip(res["timestamp"], closes) if c is not None]


def fetch(today: date) -> tuple[list, dict[str, dict], list[tuple[date, float]]]:
    """NQ=F bars, {symbol: {bar time: bar}} for every named contract Yahoo still serves that was or
    will be front inside the 60-day window, and VIX closes."""
    nqf = yahoo_bars(_get_json(CHART.format(sym="NQ%3DF", iv="5m", rg="60d")))
    wanted = {front_contract(nqf[0][0].date()), front_contract(today)}
    wanted.add(next_contract(front_contract(today)))
    refs = {}
    for ym in sorted(wanted):
        try:
            bars = yahoo_bars(_get_json(CHART.format(sym=symbol(ym), iv="5m", rg="60d")))
        except urllib.error.HTTPError:
            continue                                    # expired contracts are gone (404)
        if bars:
            refs[symbol(ym)] = {b[0]: b for b in bars}
    vix = yahoo_vix(_get_json(CHART.format(sym="%5EVIX", iv="1d", rg="3mo")))
    return nqf, refs, vix


def to_minutes(bars):
    """(date, minute-of-day, high, low), the shape range_model.daily_stats reads."""
    for t, _, h, l, _ in bars:
        yield t.date(), t.hour * 60 + t.minute, h, l


def in_overnight(t: datetime, d: date) -> bool:
    """Bar opening at t belongs to session d's overnight [18:00 the day before, 09:30): daily_stats' rule."""
    m = t.hour * 60 + t.minute
    return (t.date() == d and m < rm.RTH_OPEN) or (t.date() == d - timedelta(days=1) and m >= rm.ON_START)


def in_rth(t: datetime, d: date) -> bool:
    return t.date() == d and rm.RTH_OPEN <= t.hour * 60 + t.minute < rm.RTH_CLOSE


def mixed(window: list[tuple], refs: dict[str, dict]) -> str | None:
    """Why this window of NQ=F bars mixes two contracts, or None. Three signs:
      * two adjacent bars five minutes apart gap by 0.5% (inside a session the next open sits a tick
        or two from the last close; a gap that size is a switch at the bar boundary);
      * against a named contract, some bars sit on it (within 0.15%) and others a spread away (0.5%+);
      * a bar that opens or closes on the named contract is 0.5% wider than that contract's bar at
        the same time (a switch inside the bar). Only checked when the bar is partly on that
        contract, so a thin back month's narrow bars never flag a normal one."""
    for a, b in zip(window, window[1:]):
        if b[0] - a[0] == timedelta(minutes=5) and abs(b[1] - a[4]) > JUMP * a[4]:
            return f"{b[0]:%m-%d %H:%M} gap {b[1] - a[4]:+.0f}"
    for sym, ref in refs.items():
        near = far = 0
        for t, o, h, l, c in window:
            r = ref.get(t)
            if r is None:
                continue
            dc = abs(c - r[4]) / r[4]
            near += dc < SAME
            far += dc > JUMP
            partly_on = dc < SAME or abs(o - r[1]) / r[1] < SAME
            if partly_on and (h - l) - (r[2] - r[3]) > JUMP * r[4]:
                return f"{t:%m-%d %H:%M} bar {(h - l) - (r[2] - r[3]):.0f} wider than {sym}"
        if near and far:
            return f"switches between contracts ({far} bars a spread away from {sym})"
    return None


def off_front(window: list[tuple], ref: dict) -> bool:
    """True when every NQ=F bar that can be compared sits a spread away from the front contract:
    NQ=F is still (or already) on another contract, as on 2026-09-11's overnight."""
    near = far = 0
    for t, _, _, _, c in window:
        r = ref.get(t)
        if r is not None:
            dc = abs(c - r[4]) / r[4]
            near += dc < SAME
            far += dc > JUMP
    return far > 0 and near == 0


def clean_stats(nqf: list[tuple], refs: dict[str, dict]) -> tuple[dict, list[tuple[date, str]]]:
    """range_model.daily_stats over NQ=F, made to follow the training data's roll rule: a window where
    NQ=F mixes contracts, or sits wholly on a contract other than front_contract(), is rebuilt from
    the front contract if Yahoo serves it complete, else from the most complete one, else dropped.
    Returns (stats, notes)."""
    stats = rm.daily_stats(to_minutes(nqf))
    rth, on = defaultdict(list), defaultdict(list)
    for b in nqf:
        m = b[0].hour * 60 + b[0].minute
        if rm.RTH_OPEN <= m < rm.RTH_CLOSE:
            rth[b[0].date()].append(b)
        elif m >= rm.ON_START:
            on[b[0].date() + timedelta(days=1)].append(b)
        elif m < rm.RTH_OPEN:
            on[b[0].date()].append(b)
    notes = []
    for k, s in stats.items():
        for kind, window, member, need in (("regular session", rth[k], in_rth, MIN_RTH_BARS),
                                           ("overnight", on[k], in_overnight, MIN_ON_BARS)):
            if not window:
                continue
            front = symbol(front_contract(k))
            why = mixed(window, refs)
            if why is None and front in refs and off_front(window, refs[front]):
                why = f"is on another contract than the front {front}"
            if why is None:
                continue
            cands = [(sym, [b for t, b in ref.items() if member(t, k)]) for sym, ref in refs.items()]
            full = [c for c in cands if len(c[1]) >= need]
            best = (next((c for c in full if c[0] == front), None)
                    or max(cands, key=lambda x: len(x[1]), default=(None, [])))
            hi_k, lo_k, n_k = ("hi", "lo", "n") if kind == "regular session" else ("on_hi", "on_lo", "on_n")
            if len(best[1]) >= need:
                s[hi_k], s[lo_k], s[n_k] = max(b[2] for b in best[1]), min(b[3] for b in best[1]), len(best[1])
                notes.append((k, f"{kind} rebuilt from {best[0]}: NQ=F {why}"))
            else:
                s[n_k] = 0
                notes.append((k, f"{kind} dropped: NQ=F {why}"))
    return stats, notes


# ----------------------------------------------------------------------------- features


def target_day(now: datetime) -> date:
    """The session a forecast is for: today's until 16:00 on a weekday, else the next weekday's."""
    d = now.date()
    if d.weekday() < 5 and now.hour * 60 + now.minute < rm.RTH_CLOSE:
        return d
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def features_for(stats: dict, d: date, vix: list[tuple[date, float]]) -> tuple[dict, dict]:
    """The eight model inputs for session d, from bars that open before 09:30 on d only.
    Returns (features, context for the printout). Raises ValueError when an input is missing."""
    prior = [(k, stats[k]["hi"] - stats[k]["lo"]) for k in sorted(stats)
             if k < d and k.weekday() < 5 and stats[k]["n"] >= MIN_RTH_BARS]
    if len(prior) < rm.LOOKBACK:
        raise ValueError(f"only {len(prior)} complete sessions before {d}; need {rm.LOOKBACK}")
    prior = prior[-rm.LOOKBACK:]
    s = stats.get(d)
    if s is None or s["on_n"] == 0:
        raise ValueError(f"no usable overnight bars for {d} (the session opens 18:00 ET the evening "
                         "before); pass --overnight HIGH LOW from your chart")
    v = rm.vix_before(vix)(d)
    if v is None:
        raise ValueError(f"no VIX close before {d}")
    rngs = [r for _, r in prior]
    feats = {
        "lr1": math.log(rngs[-1]), "lr5": math.log(sum(rngs[-5:]) / 5),
        "lr22": math.log(sum(rngs) / rm.LOOKBACK), "lon": math.log(s["on_hi"] - s["on_lo"]),
        "lvix": math.log(v), "fomc": 1.0 if d in FOMC else 0.0,
        "mon": 1.0 if d.weekday() == 0 else 0.0, "fri": 1.0 if d.weekday() == 4 else 0.0,
    }
    vix_day = max(k for k, _ in vix if k < d)
    ctx = {"prior": prior, "on_hi": s["on_hi"], "on_lo": s["on_lo"], "on_n": s["on_n"],
           "vix": v, "vix_day": vix_day}
    return feats, ctx


# ----------------------------------------------------------------------------- printout


def run(d: date | None, overnight: tuple[float, float] | None, log: bool) -> int:
    model = load_model()
    now = datetime.now(rm.NY)
    d = d or target_day(now)
    nqf, refs, vix = fetch(now.date())
    stats, notes = clean_stats(nqf, refs)
    if overnight:
        hi_, lo_ = max(overnight), min(overnight)
        s = stats.setdefault(d, {"hi": -math.inf, "lo": math.inf, "n": 0, "on_hi": 0, "on_lo": 0, "on_n": 0})
        s["on_hi"], s["on_lo"], s["on_n"] = hi_, lo_, max(s["on_n"], 1)
    try:
        feats, ctx = features_for(stats, d, vix)
    except ValueError as e:
        print(f"Cannot forecast {d}: {e}")
        return 1
    fc, lo, hi = forecast(model, feats)

    on_bars = [b for b in nqf if in_overnight(b[0], d)]
    last_on = max(b[0] for b in on_bars) if on_bars else None
    on_done = overnight is not None or (last_on is not None and last_on.date() == d
                                        and last_on.hour * 60 + last_on.minute >= rm.RTH_OPEN - 5)
    avg22 = math.exp(feats["lr22"])
    rel = fc / avg22 - 1
    mood = ("a quieter day than recently" if rel < -0.10 else
            "a bigger day than recently" if rel > 0.10 else "about a normal day for recent weeks")
    fomc_lift = math.exp(model["beta"][1 + model["features"].index("fomc")]) - 1

    p = print
    p(f"\nNQ range forecast for {d:%A %Y-%m-%d}")
    p(f"  run {now:%a %Y-%m-%d %H:%M} ET, Yahoo data about 10 minutes delayed")
    p("")
    p(f"  Forecast 09:30-16:00 range   {fc:5.0f} pts")
    p(f"  80% band                     {lo:5.0f} - {hi:.0f} pts")
    p(f"  vs last 22 sessions' average ({avg22:.0f} pts): {rel:+.0%}, {mood}")
    p("")
    p("  Inputs, all from before 09:30")
    on_rng = ctx["on_hi"] - ctx["on_lo"]
    if overnight:
        on_note = "from your chart"
    elif on_done:
        on_note = f"complete, high {ctx['on_hi']:,.2f} low {ctx['on_lo']:,.2f}"
    else:
        on_note = f"PARTIAL: bars through {last_on:%a %H:%M} only; rerun after 09:40 ET"
    p(f"    overnight range 18:00-09:30  {on_rng:5.0f} pts   {on_note}")
    p(f"    previous session             {math.exp(feats['lr1']):5.0f} pts   ({ctx['prior'][-1][0]:%a %m-%d})")
    p(f"    last 5 sessions average      {math.exp(feats['lr5']):5.0f} pts")
    p(f"    last 22 sessions average     {avg22:5.0f} pts")
    p(f"    VIX close                    {ctx['vix']:5.1f}       ({ctx['vix_day']:%a %m-%d})")
    p(f"    FOMC statement day           {f'YES (about {fomc_lift:.0%} bigger)' if feats['fomc'] else 'no'}")
    p(f"    day of week                  {d:%A}")
    p("  Not in the model: CPI, jobs report and other scheduled releases. Check the calendar.")
    if d.weekday() == 0 and not overnight:
        p(f"  MONDAY: Yahoo has no bars for Sunday 18:00-18:10. If your chart's overnight high is above"
          f" {ctx['on_hi']:,.2f}\n  or its low below {ctx['on_lo']:,.2f}, rerun with"
          f" --overnight HIGH LOW.")
    first = ctx["prior"][0][0]
    for k, msg in notes:
        if first <= k <= d:
            p(f"  data: {k} {msg}")

    s = stats.get(d)
    if s and s["n"] > 0:
        rng = s["hi"] - s["lo"]
        last_rth = max(b[0] for b in nqf if in_rth(b[0], d))
        p("")
        p(f"  So far today (09:30 - {last_rth + timedelta(minutes=5):%H:%M}): high {s['hi']:,.2f}  low {s['lo']:,.2f}"
          f"  range {rng:.0f} pts = {rng / fc:.0%} of the forecast")
        finished = d < now.date() or now.hour * 60 + now.minute >= rm.RTH_CLOSE + 10
        if not finished:
            if rng >= hi:
                p("    The day is already past the top of the 80% band: a big day.")
            elif rng >= fc:
                p("    The day has already reached its forecast size.")
            else:
                p("    Where the day would reach its forecast size:")
                p(f"      if the low holds:  high near {s['lo'] + fc:,.0f}   (band {s['lo'] + lo:,.0f} - {s['lo'] + hi:,.0f})")
                p(f"      if the high holds: low near  {s['hi'] - fc:,.0f}   (band {s['hi'] - hi:,.0f} - {s['hi'] - lo:,.0f})")
            p("    Tested (RESULT_TOUCH.md): at these levels price continues or reverses 50/50. Sizes, not entries.")
        elif s["n"] >= MIN_RTH_BARS:
            inside = "inside" if lo <= rng <= hi else "OUTSIDE"
            p(f"    Final: actual {rng:.0f} pts vs forecast {fc:.0f} ({rng / fc - 1:+.0%}), {inside} the band")
    p("")

    if log:
        new = not LOG_CSV.exists()
        with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["target", "run_et", "forecast", "lo80", "hi80", "overnight_complete",
                            "overnight_from_chart", "avg22", *model["features"]])
            w.writerow([d, now.strftime("%Y-%m-%d %H:%M"), f"{fc:.1f}", f"{lo:.1f}", f"{hi:.1f}",
                        int(on_done), int(overnight is not None), f"{avg22:.1f}",
                        *(f"{feats[k]:.6f}" for k in model["features"])])
    return 0


def grade() -> int:
    """Every logged session that has finished: forecast against the range that happened."""
    if not LOG_CSV.exists():
        print("No forecasts logged yet.")
        return 1
    latest: dict[str, dict] = {}
    with open(LOG_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            latest[row["target"]] = row                 # the last run for each session wins
    today = datetime.now(rm.NY).date()
    nqf, refs, _ = fetch(today)
    stats, _ = clean_stats(nqf, refs)
    done = []
    for k, row in sorted(latest.items()):
        d = date.fromisoformat(k)
        s = stats.get(d)
        if d >= today or s is None or s["n"] < MIN_RTH_BARS:
            continue
        act = s["hi"] - s["lo"]
        fc, lo, hi = float(row["forecast"]), float(row["lo80"]), float(row["hi80"])
        done.append((d, fc, lo, hi, act, float(row["avg22"]), row["overnight_complete"] == "1"))
    if not done:
        print("No finished sessions to grade yet.")
        return 1
    print(f"\n{'session':10}  {'forecast':>8}  {'band':>11}  {'actual':>6}  {'error':>6}  in band")
    for d, fc, lo, hi, act, _, full in done:
        print(f"{d}  {fc:8.0f}  {lo:5.0f}-{hi:<5.0f}  {act:6.0f}  {act / fc - 1:+6.0%}  "
              f"{'yes' if lo <= act <= hi else 'NO'}{'' if full else '  (partial overnight)'}")
    n = len(done)
    cov = sum(lo <= act <= hi for _, _, lo, hi, act, _, _ in done) / n
    err_m = sum(abs(act / fc - 1) for _, fc, _, _, act, _, _ in done) / n
    err_b = sum(abs(act / b - 1) for _, _, _, _, act, b, _ in done) / n
    print(f"\n{n} sessions: band coverage {cov:.0%} (target 80%), mean abs error {err_m:.0%} "
          f"vs {err_b:.0%} for the 22-day average")
    print("A few weeks of sessions say little; the backtest had 1,175.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, help="session to forecast (YYYY-MM-DD); not logged")
    ap.add_argument("--overnight", nargs=2, type=float, metavar=("HIGH", "LOW"),
                    help="overnight high and low from your own chart")
    ap.add_argument("--grade", action="store_true", help="score logged forecasts")
    ap.add_argument("--refit", action="store_true", help="refit model.json on the NinjaTrader data")
    ap.add_argument("--no-log", action="store_true", help="do not append to forecast_log.csv")
    a = ap.parse_args()
    if a.refit:
        m = fit_final()
        print(f"refit on {m['n']} sessions {m['fit_from']} .. {m['fit_to']}")
        sys.exit(0)
    if a.grade:
        sys.exit(grade())
    sys.exit(run(a.date, tuple(a.overnight) if a.overnight else None,
                 log=not a.no_log and a.date is None))
