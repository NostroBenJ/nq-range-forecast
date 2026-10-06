# NQ daily range forecast: result

Run once on 2026-10-04 22:16 EDT after the files were frozen (`FROZEN.txt`). Full output: `results.txt`.
`verify.py --data`: 15/15 checks pass.

## Verdict: PASS, all three pre-registered gates

| | Era A, NT 2022-2026 | Era B, LSE 2017-2020 |
|---|---|---|
| Out-of-sample days | 1,175 | 878 |
| R² of ln range, model | 0.41 | 0.64 |
| R² of ln range, 22-day mean | 0.25 | 0.57 |
| Newey-West t, model beats baseline | +5.40 | +3.79 |
| 80% band coverage | 78.6% | 78.7% |

The model beat the baseline in every out-of-sample year on R², in both eras.

## What drives it (last fit, era A)

- Overnight range (`lon` +0.27): a wide night means a wide day.
- The last 5 days' ranges (`lr5` +0.36) matter far more than yesterday alone (`lr1` +0.04).
- VIX (`lvix` +0.18).
- FOMC day (+0.37 in logs): the range runs about 44% bigger than an ordinary day with the same inputs.
- Monday is about 8% smaller.

## Limits, stated plainly

- **It forecasts size, not direction.** It says nothing about whether the high or the low comes first.
- **It is blunt.** The typical miss is still about 30% of the range, barely better than the baseline on
  that measure (31.5% vs 32.8%). The gain shows up in the big misses. The 2026 median 80% band is
  323 points wide against a 370-point median day.
- In 2017 the mean absolute error was worse than the baseline's (45.1% vs 37.6%).
- CPI, NFP and other scheduled releases are not in it yet. Only FOMC is.

## Use

At 09:30 you need the overnight high and low, the last 5 and 22 regular-session ranges, yesterday's
VIX close, and whether it is an FOMC day. Read the forecast as "what kind of day is this likely to be"
for stop size, targets, position size, and knowing when the day's range is mostly used up.

## Daily calculator (added 2026-10-04, after the verdict)

```
py -3.14 forecast_today.py                          run after 09:40 ET for the final number
py -3.14 forecast_today.py --overnight HIGH LOW     Mondays, when the Sunday open set the overnight high or low
py -3.14 forecast_today.py --grade                  forecast vs actual for every logged session
```

- Model refit on every era-A session (1,403, 2021-02 .. 2026-09-25) into `model.json`. FOMC lift in the
  final fit is about 41%.
- Data is Yahoo NQ=F 5-minute bars plus the named contracts Yahoo still serves (NQZ26.CME ...), and
  ^VIX. Each run is appended to `forecast_log.csv` so the live record can be graded.
- `verify_forecast.py` (all pass): on the 44 sessions Yahoo and the NinjaTrader file share, regular
  ranges agree to 0.5 pt, Tuesday-Friday overnight ranges to 3.75 pt, and Yahoo-built forecasts are
  within 0.22% of the NinjaTrader-built ones.
- Two Yahoo defects found by that check and handled:
  1. NQ=F flips between contracts around a roll, once inside a single bar (2026-09-14 11:30, a 384-pt
     bar). Such sessions are rebuilt from the front contract by the training data's roll rule.
  2. Every Yahoo series starts Sunday evening at 18:10, not 18:00. On 5 of 8 Mondays the overnight
     range came out 11-41 pts short. Mondays print a warning; pass `--overnight` from your chart.

## TradingView indicator (added 2026-10-04)

`NQ_Range_Forecast.pine` (Pine v6) draws the same forecast on the chart: the forecast and 80% band
in a table, a provisional number before 09:30, the level where the day would reach its forecast size
from its low and from its high, and the last sessions' forecast against the actual range.

- History comes from a 30-minute request on the extended session (`f_ctx`), so any chart of 30
  minutes or less works and 22 sessions are always there; the forecast is fixed on the first 09:30
  bar. VIX is the latest daily close dated before the session, chosen so it never reads a close
  that has not happened.
- Checks: TradingView's compiler, 0 errors and 0 warnings (`cisd_v31\pine_check.py`).
  `pine_port_check.py` replays the indicator's logic on the NinjaTrader data for all 1,403 sessions:
  overnight, VIX and calendar inputs identical on 100%, forecast identical on 97.6% (within 1% on
  99.5%). The rest trace to two sessions where the NinjaTrader file is missing minutes but has every
  half-hour (2021-08-25, 2026-07-24), which the indicator keeps and the model dropped.
- Checked on TradingView itself (a live 1-minute NQ chart, 2026-10-04 23:05 ET): "sessions
  22 / 22", and the Recent sessions rows read 343, 337, 301, 337, 325 against the Python calculator's
  342, 337, 300, 336, 320 (`tv_compare_python.txt`). Tuesday-Friday agree within a point; Monday is
  5 higher because TradingView has the Sunday 18:00-18:10 bars that Yahoo lacks. All five actuals
  were inside the band.

## Quantower indicator (added 2026-10-04)

`quantower\` holds the same indicator for Quantower 1.146.18, deployed to
`C:\Quantower\Settings\Scripts\Indicators\NqRangeForecast\`.

- `Core\RangeCore.cs`: everything that decides a number (the model, the daily ranges, the walk over
  the chart's bars, the tally), with no Quantower types. `Indicator\NqRangeForecastIndicator.cs` is the
  Quantower part only: 1-minute history from `Symbol.GetHistory`, yesterday's VIX close downloaded from
  Yahoo (FRED as fallback; Rithmic has no CBOE data; a manual override setting), lines and table.
- A session counts when every half-hour of 09:30-16:00 traded, as in the TradingView version, because a
  single contract's thin back-month weeks before its roll would fail the model's 380-minute rule.
- Checks (`Harness` runs the core on the NinjaTrader file):
  - `compare_core.py`, model's minute rule: all 1,403 sessions identical to the Python (largest input
    difference 0, forecast 1.8e-15). Half-hour rule: 97.6% within 0.1%, the same as `pine_port_check.py`.
  - `compare_walk.py`: the walk finds the same touch as `touch_test.find_touch` on 1,456 / 1,456
    sessions (bar, side, level), the same day's range, and the same tally (612 reached, 320 beyond).
    Feeding every bar as three live updates gives the same lines and records as whole bars.
  - Built against Quantower's DLL: 0 errors, 0 warnings. Quantower's own `AssemblyLoader.LoadTypes`
    finds and constructs it (7 lines, 7 settings, updates on tick). The shipped VIX download code,
    called by reflection, returned Yahoo's close for Fri 10-02 (15.31) and FRED's history.
  - Not yet run inside Quantower on live data.

## Descriptive, not a test: what happens once a day reaches its forecast

Out-of-sample days, from ranges alone (`touch_descriptive.txt`):

| | era A 2022-2026 | era B 2017-2020 |
|---|---|---|
| days that reached the forecast at some point | 53% | 52% |
| of those, went on to 1.10x the forecast | 78% | 84% |
| of those, went on to 1.25x | 55% | 61% |
| of those, went on to 1.50x | 29% | 38% |

The forecast size is not a wall. But going a little further is also what a coin flip does, so this
table says nothing about direction. The proper symmetric test is in `RESULT_TOUCH.md`: at the level,
price continues or reverses 50/50, and neither following nor fading it makes money.
