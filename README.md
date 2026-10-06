# nq-range-forecast

How big will today's Nasdaq-100 futures (NQ) session be? This is a pre-registered forecast of the
regular-session high-low range (09:30-16:00 ET), made at the open from information known before it,
and tested walk-forward against the plain 22-day average range. It forecasts **size, not direction**.

## Result: passed all three pre-registered gates

| | Era A, 2022-2026 (1-minute) | Era B, 2017-2020 (5-minute, separate data) |
|---|---|---|
| Out-of-sample days | 1,175 | 878 |
| R² of ln range, model | **0.41** | **0.64** |
| R² of ln range, 22-day mean | 0.25 | 0.57 |
| Newey-West t, model beats baseline | **+5.40** | **+3.79** |
| 80% band coverage | 78.6% | 78.7% |

The model beat the baseline on R² in every out-of-sample year, in both eras. It is an OLS regression
on ln(range) with eight inputs: yesterday's range, the 5- and 22-day mean ranges, the overnight range
(18:00 to 09:30), the prior VIX close, and FOMC, Monday and Friday flags. It is refit walk-forward by
year.

What drives it: a wide overnight session means a wide day; the last five days matter far more than
yesterday alone; FOMC days run about 44% bigger than ordinary days with the same inputs.

**Limits.** The typical miss is still about 30% of the range, barely better than the baseline on that
measure (31.5% vs 32.8%); the gain is in the big misses. In 2017 its mean absolute error was worse than
the baseline's. CPI, NFP and other releases are not in it yet. Full write-up: [`RESULT.md`](RESULT.md).

## Is the forecast level a trade? No.

A second pre-registered test ([`RESULT_TOUCH.md`](RESULT_TOUCH.md)) asked whether price reverses or
continues once the day's range reaches the forecast. On 579 touches with 1-minute bars it went
248 continuation-first against 251 reversal-first. Following the level made +$1.13 a trade (t +0.14) and
fading it lost $1.62 (t −0.19), both with drawdowns of $4,700 or more at one micro contract. The
forecast is for stop distance, targets and position size, not for entries.

## How it was kept honest

- **Pre-registered and frozen.** [`PREREGISTRATION.md`](PREREGISTRATION.md) fixed the model, the
  baseline and the pass gates before the single run, and [`FROZEN.txt`](FROZEN.txt) holds the SHA-256 of
  the plan and the code at that moment. Those files are published unchanged, so
  `sha256sum -c FROZEN.txt FROZEN_TOUCH.txt` still passes here.
- **Checked code.** `verify.py` (15 checks on the data and the model), `verify_touch.py` (32 checks,
  including a driftless simulation that must show no edge and planted drift that must be found both
  ways), `verify_forecast.py` (the live Yahoo pipeline against the research data).
- **Three implementations, checked against each other.** The Python model, a TradingView indicator
  (Pine v6) and a Quantower indicator (C#). `pine_port_check.py`: the Pine logic gives the identical
  forecast on 97.6% of 1,403 sessions and is within 1% on 99.5% (the rest are two sessions with missing
  minutes). The C# core matches Python on 1,403 / 1,403 sessions (largest difference 1.8e-15), and its
  intraday walk finds the same touch on 1,456 / 1,456.
- **Data defects found, not assumed away.** Yahoo's continuous `NQ=F` switches contracts around a
  roll, once inside a single 5-minute bar (a 384-point fake bar on 2026-09-14), and every Yahoo
  session starts Sunday at 18:10 rather than 18:00. Both are detected and handled in
  `forecast_today.py`.

## Files

| file | what it is |
|---|---|
| `range_model.py` | The model, the walk-forward test and the gates (frozen). |
| `touch_test.py` | The reverse-or-continue test at the forecast level (frozen). |
| `verify*.py` | The check suites. |
| `forecast_today.py` | Daily calculator: Yahoo 5-minute bars + `model.json`, logs to `forecast_log.csv`, `--grade` scores the log. |
| `NQ_Range_Forecast.pine` | TradingView indicator: forecast, 80% band and the levels on the chart. |
| `quantower/` | Quantower indicator: a platform-free `Core/RangeCore.cs`, the indicator, and a harness that runs the core on the research data. |
| `results*.txt`, `*_check.txt` | Output of every run. |

## Running it

Python 3.12+ (developed on 3.14), standard library only.

```
python forecast_today.py            # after 09:40 ET; needs only internet access for Yahoo and VIX
python forecast_today.py --grade    # forecast vs actual for every logged session
```

The research scripts need data that is not included: a NinjaTrader 1-minute NQ export (era A), a
licensed 5-minute NQ series (era B) and FRED's daily VIX. Their paths are the three constants at the
top of `range_model.py`, which is left exactly as frozen so the hashes still check; point them at your
own copies to re-run.

Research code, not trading advice.
