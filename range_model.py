"""NQ daily range forecast. Stdlib only. See PREREGISTRATION.md.

Forecasts ln(regular-session range) at 09:30 from things known before the open, walk-forward by
year, against the plain 22-day mean range. Run:  py -3.14 range_model.py
"""
from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
NT_1M = Path(r"C:\Users\jontr\dev\ftm-port\data\nq_1m_eth_nt.csv")
LSE_5M = Path(r"C:\Users\jontr\Downloads\quantdesk\nq_f_5m_lse.csv")
VIX_CSV = Path(r"C:\Users\jontr\Downloads\intraday_batch\data\vix_fred.csv")

RTH_OPEN, RTH_CLOSE, ON_START = 570, 960, 1080      # 09:30, 16:00, 18:00 in minutes of the ET day
FEATURES = ["lr1", "lr5", "lr22", "lon", "lvix", "fomc", "mon", "fri"]
LOOKBACK = 22

# Scheduled FOMC statement days, copied from intraday_batch\batch.py (federalreserve.gov, 2026-10-01).
FOMC = {date.fromisoformat(d) for d in """
2016-01-27 2016-03-16 2016-04-27 2016-06-15 2016-07-27 2016-09-21 2016-11-02 2016-12-14
2017-02-01 2017-03-15 2017-05-03 2017-06-14 2017-07-26 2017-09-20 2017-11-01 2017-12-13
2018-01-31 2018-03-21 2018-05-02 2018-06-13 2018-08-01 2018-09-26 2018-11-08 2018-12-19
2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11
2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16
""".split()}


# ----------------------------------------------------------------------------- bars -> daily stats


def nt_bars(path: Path = NT_1M):
    """(date, minute-of-day at bar OPEN, high, low). NinjaTrader stamps 1-minute bars at their close, ET."""
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            s = r[0]
            d = date(int(s[:4]), int(s[5:7]), int(s[8:10]))
            m = int(s[11:13]) * 60 + int(s[14:16]) - 1
            if m < 0:                                   # the 00:00 stamp is the 23:59 bar of the day before
                d, m = d - timedelta(days=1), m + 1440
            yield d, m, float(r[2]), float(r[3])


def lse_bars(path: Path = LSE_5M):
    """(date, minute-of-day at bar open, high, low). The LSE file is stamped at the bar's open in UTC."""
    memo: dict[str, tuple[date, int]] = {}
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            s = r[0]
            hit = memo.get(s[:13])
            if hit is None:
                u = datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), tzinfo=timezone.utc)
                e = u.astimezone(NY)
                hit = memo[s[:13]] = (e.date(), e.hour * 60)
            d, base = hit
            yield d, base + int(s[14:16]), float(r[2]), float(r[3])


def daily_stats(bars) -> dict[date, dict]:
    """Per trading day d: the regular session [09:30, 16:00) on d, and the overnight session
    [18:00 on d-1, 09:30 on d). Only bars that OPEN before 09:30 on d ever touch d's overnight."""
    out: dict[date, dict] = {}

    def get(d):
        s = out.get(d)
        if s is None:
            s = out[d] = {"hi": -math.inf, "lo": math.inf, "n": 0, "on_hi": -math.inf, "on_lo": math.inf, "on_n": 0}
        return s

    for d, m, h, l in bars:
        if RTH_OPEN <= m < RTH_CLOSE:
            s = get(d)
            s["hi"], s["lo"], s["n"] = max(s["hi"], h), min(s["lo"], l), s["n"] + 1
        else:
            on_day = d + timedelta(days=1) if m >= ON_START else d if m < RTH_OPEN else None
            if on_day is not None:
                s = get(on_day)
                s["on_hi"], s["on_lo"], s["on_n"] = max(s["on_hi"], h), min(s["on_lo"], l), s["on_n"] + 1
    return out


# ----------------------------------------------------------------------------- features


def load_vix(path: Path = VIX_CSV) -> list[tuple[date, float]]:
    out = []
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for d, v in rd:
            try:
                out.append((date.fromisoformat(d), float(v)))
            except ValueError:
                pass                                        # FRED writes "." on holidays
    return out


def vix_before(vix: list[tuple[date, float]]):
    """Function d -> latest VIX close dated strictly before d."""
    ds = [d for d, _ in vix]
    import bisect

    def f(d: date):
        k = bisect.bisect_left(ds, d) - 1
        return vix[k][1] if k >= 0 else None
    return f


def roll_window_days(first_year: int, last_year: int) -> set[date]:
    """Thursday eight days before each quarterly expiry through expiry Friday (ta_setups\\data.py)."""
    out = set()
    for y in range(first_year, last_year + 1):
        for mo in (3, 6, 9, 12):
            d = date(y, mo, 1)
            d += timedelta(days=(4 - d.weekday()) % 7)
            ex = d + timedelta(days=14)
            for k in range(9):
                out.add(ex - timedelta(days=k))
    return out


def build_rows(stats: dict[date, dict], start: date, end: date, min_rth: int, vix_f,
               excluded: set[date] = frozenset()) -> tuple[list[dict], dict]:
    """Kept sessions in [start, end], in order, each with its target y and its features.
    Returns (rows, counts of dropped days by reason)."""
    dropped = {"incomplete": 0, "excluded": 0, "warmup": 0, "no_overnight": 0, "no_vix": 0}
    kept: list[tuple[date, float]] = []                    # (day, range) of every complete session
    rows = []
    for d in sorted(stats):
        if not (start <= d <= end) or d.weekday() >= 5:
            continue
        s = stats[d]
        if d in excluded:
            dropped["excluded"] += 1
            continue
        if s["n"] < min_rth:
            dropped["incomplete"] += 1
            continue
        rng = s["hi"] - s["lo"]
        prev = [r for _, r in kept[-LOOKBACK:]]
        kept.append((d, rng))
        if len(prev) < LOOKBACK:
            dropped["warmup"] += 1
            continue
        if s["on_n"] == 0 or s["on_hi"] - s["on_lo"] <= 0:
            dropped["no_overnight"] += 1
            continue
        v = vix_f(d)
        if v is None:
            dropped["no_vix"] += 1
            continue
        rows.append({
            "d": d, "range": rng, "y": math.log(rng),
            "lr1": math.log(prev[-1]),
            "lr5": math.log(sum(prev[-5:]) / 5),
            "lr22": math.log(sum(prev) / LOOKBACK),
            "lon": math.log(s["on_hi"] - s["on_lo"]),
            "lvix": math.log(v),
            "fomc": 1.0 if d in FOMC else 0.0,
            "mon": 1.0 if d.weekday() == 0 else 0.0,
            "fri": 1.0 if d.weekday() == 4 else 0.0,
        })
    return rows, dropped


# ----------------------------------------------------------------------------- statistics


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-14:
            raise ValueError("singular system")
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def ols(xs: list[list[float]], ys: list[float]) -> list[float]:
    """Coefficients [const, b1..bk] by the normal equations."""
    rows = [[1.0] + x for x in xs]
    k = len(rows[0])
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(k)]
    return solve(xtx, xty)


def predict(beta: list[float], x: list[float]) -> float:
    return beta[0] + sum(b * v for b, v in zip(beta[1:], x))


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def newey_west_t(xs: list[float], lags: int) -> float:
    """t-statistic of the mean of xs with a Bartlett-kernel HAC variance."""
    n = len(xs)
    mu = sum(xs) / n
    e = [x - mu for x in xs]
    var = sum(v * v for v in e) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        var += 2 * w * sum(e[i] * e[i - L] for i in range(L, n)) / n
    return mu / math.sqrt(var / n)


# ----------------------------------------------------------------------------- walk-forward


def walk_forward(rows: list[dict]) -> list[dict]:
    """For each year after the first: fit on every earlier row, forecast the year."""
    years = sorted({r["d"].year for r in rows})
    out = []
    for y in years[1:]:
        train = [r for r in rows if r["d"].year < y]
        test = [r for r in rows if r["d"].year == y]
        beta = ols([[r[f] for f in FEATURES] for r in train], [r["y"] for r in train])
        res = [r["y"] - predict(beta, [r[f] for f in FEATURES]) for r in train]
        q10, q90 = quantile(res, 0.10), quantile(res, 0.90)
        for r in test:
            f = predict(beta, [r[k] for k in FEATURES])
            out.append({**r, "f_model": f, "f_base": r["lr22"], "lo80": f + q10, "hi80": f + q90,
                        "beta": beta})
    return out


def score(oos: list[dict]) -> dict:
    ys = [r["y"] for r in oos]
    my = sum(ys) / len(ys)
    sst = sum((y - my) ** 2 for y in ys)
    se_m = [(r["y"] - r["f_model"]) ** 2 for r in oos]
    se_b = [(r["y"] - r["f_base"]) ** 2 for r in oos]
    pct = lambda key: sum(abs(r["range"] / math.exp(r[key]) - 1) for r in oos) / len(oos)
    return {
        "n": len(oos),
        "mse_model": sum(se_m) / len(oos), "mse_base": sum(se_b) / len(oos),
        "r2_model": 1 - sum(se_m) / sst, "r2_base": 1 - sum(se_b) / sst,
        "mape_model": pct("f_model"), "mape_base": pct("f_base"),
        "nw_t": newey_west_t([b - m for b, m in zip(se_b, se_m)], 5),
        "coverage": sum(r["lo80"] <= r["y"] <= r["hi80"] for r in oos) / len(oos),
    }


# ----------------------------------------------------------------------------- main


def load_eras():
    vf = vix_before(load_vix())
    nt = daily_stats(nt_bars())
    a_rows, a_drop = build_rows(nt, date(2021, 1, 4), date(2026, 9, 25), 380, vf)
    lse = daily_stats(lse_bars())
    b_rows, b_drop = build_rows(lse, date(2016, 7, 1), date(2020, 12, 31), 75, vf,
                                roll_window_days(2016, 2020))
    return {"A (NT 2021-2026)": (a_rows, a_drop), "B (LSE 2016-2020)": (b_rows, b_drop)}


def report(eras) -> str:
    out = []
    p = out.append
    verdicts = {}
    for name, (rows, drop) in eras.items():
        oos = walk_forward(rows)
        s = score(oos)
        verdicts[name] = s
        p(f"=== Era {name}: {len(rows)} kept sessions, dropped {drop}")
        p(f"  out-of-sample days {s['n']} ({oos[0]['d']} .. {oos[-1]['d']})")
        p(f"  R2 of ln range       model {s['r2_model']:+.3f}   baseline (22d mean) {s['r2_base']:+.3f}")
        p(f"  MSE of ln range      model {s['mse_model']:.4f}   baseline {s['mse_base']:.4f}")
        p(f"  mean abs error       model {s['mape_model']:.1%} of the range   baseline {s['mape_base']:.1%}")
        p(f"  Newey-West t (base SE - model SE, 5 lags): {s['nw_t']:+.2f}")
        p(f"  80% band coverage    {s['coverage']:.1%}")
        for y in sorted({r['d'].year for r in oos}):
            yr = [r for r in oos if r["d"].year == y]
            cy = sum(r["lo80"] <= r["y"] <= r["hi80"] for r in yr) / len(yr)
            sy = score(yr)
            p(f"    {y}: n {len(yr):3d}  R2 model {sy['r2_model']:+.3f} base {sy['r2_base']:+.3f}"
              f"  abs err {sy['mape_model']:.1%} vs {sy['mape_base']:.1%}  coverage {cy:.1%}")
        beta = oos[-1]["beta"]
        p("  last fit coefficients: const {:+.3f}  ".format(beta[0])
          + "  ".join(f"{f} {b:+.3f}" for f, b in zip(FEATURES, beta[1:])))
        last = [r for r in oos if r["d"].year == oos[-1]["d"].year]
        widths = [math.exp(r["hi80"]) - math.exp(r["lo80"]) for r in last]
        mids = [math.exp(r["f_model"]) for r in last]
        p(f"  {oos[-1]['d'].year}: median forecast {quantile(mids, .5):.0f} pts, median 80% band width"
          f" {quantile(widths, .5):.0f} pts, median actual {quantile([r['range'] for r in last], .5):.0f} pts")
        p("  last 5 days: " + "; ".join(
            f"{r['d']} fc {math.exp(r['f_model']):.0f} [{math.exp(r['lo80']):.0f}-{math.exp(r['hi80']):.0f}]"
            f" act {r['range']:.0f}" for r in oos[-5:]))
        p("")
    a, b = verdicts["A (NT 2021-2026)"], verdicts["B (LSE 2016-2020)"]
    g1 = a["mse_model"] < a["mse_base"] and b["mse_model"] < b["mse_base"]
    g2 = a["nw_t"] >= 2
    g3 = all(0.75 <= v["coverage"] <= 0.85 for v in (a, b))
    p("=== Pre-registered gates")
    p(f"  1. beats baseline in both eras: {'PASS' if g1 else 'FAIL'}")
    p(f"  2. era A Newey-West t >= 2:     {'PASS' if g2 else 'FAIL'} (t {a['nw_t']:+.2f})")
    p(f"  3. coverage 75-85% both eras:   {'PASS' if g3 else 'FAIL'} ({a['coverage']:.1%}, {b['coverage']:.1%})")
    p(f"  VERDICT: {'PASS' if g1 and g2 and g3 else 'FAIL'}")
    return "\n".join(out)


if __name__ == "__main__":
    text = report(load_eras())
    print(text)
    (HERE / "results.txt").write_text(text + "\n", encoding="utf-8")
