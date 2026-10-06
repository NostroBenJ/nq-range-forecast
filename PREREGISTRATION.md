# NQ daily range forecast: pre-registration

Written 2026-10-04, before any result was computed. Not to be edited after the first run; any change
after that is recorded below the line as a deviation.

## Question

Before 09:30 New York time, can we forecast the size of today's regular-session NQ range
(09:30-16:00 high minus low) better than the plain "average of the last 22 days" that anyone can
compute? This forecasts how big the day will be, not which way it goes.

## Data

- **Era A (main):** NinjaTrader NQ 1-minute, back-adjusted, `dev\ftm-port\data\nq_1m_eth_nt.csv`,
  sessions 2021-01-04 .. 2026-09-25. Back-adjustment adds a constant, so ranges in points are exact.
- **Era B (replication):** LSE NQ 5-minute, 2016-07-01 .. 2020-12-31. Every date from the Thursday
  eight days before a quarterly expiry through expiry Friday is dropped (the file flips between
  contracts there; `ta_setups\data.py` `roll_window_days`).
- **VIX:** FRED VIXCLS close, `intraday_batch\data\vix_fred.csv`. Day d uses the latest close dated
  before d.
- **FOMC statement days:** the list in `intraday_batch\batch.py`.

A session is kept if it is a weekday with a complete regular session (at least 380 of 390 minutes on
era A, 75 of 78 bars on era B) and at least 22 earlier kept sessions in the same era. Half days fail
the completeness rule and are dropped.

## Target and features

Target: `y = ln(range)` of the regular session.

Features, all known at 09:30 (bars that open before 09:30 only):

| name  | definition |
|-------|------------|
| lr1   | ln range of the previous kept session |
| lr5   | ln mean range of the previous 5 kept sessions |
| lr22  | ln mean range of the previous 22 kept sessions |
| lon   | ln overnight range: high - low of bars opening in [18:00 the day before, 09:30) |
| lvix  | ln VIX, latest close before d |
| fomc  | 1 on a scheduled FOMC statement day |
| mon   | 1 on Monday |
| fri   | 1 on Friday |

## Models

- **Baseline:** forecast `y = lr22` (the 22-day mean range, no fitting).
- **Model:** ordinary least squares of `y` on all eight features plus a constant.

## Out-of-sample protocol

Walk-forward by calendar year, within each era: for test year Y, fit on every kept session of that era
dated before 1 Jan Y; forecast every session in Y. The first year of each era is training only.
Out-of-sample sets: era A 2022-2026 (through 25 Sep), era B 2017-2020.

The 80% band: the 10th and 90th percentiles of the training residuals are added to the forecast.

## Pass rule (all must hold, decided now)

1. **Beats the baseline in both eras:** out-of-sample mean squared error of `y` is lower for the model
   than for the baseline, in era A and in era B.
2. **Not by chance:** on era A, the daily loss difference (baseline squared error minus model squared
   error) has a Newey-West t (5 lags) of at least 2.
3. **Honest band:** out-of-sample 80%-band coverage is between 75% and 85% in both eras.

Reported regardless of the verdict: out-of-sample R² of `y` for each model, mean absolute error in
percent of the range, band width in points for 2026, coverage by year, and the fitted coefficients.

## Checks before the real run (`verify.py`)

- OLS recovers known coefficients on synthetic data to 1e-9.
- Range, overnight range and session completeness on hand-built bars.
- No look-ahead: changing every bar at or after 09:30 on day d leaves day d's features unchanged.
- Era A sessions have 390 regular-session minutes on a normal day; era B has 78 bars.
- Band coverage on synthetic i.i.d. normal residuals is close to 80%.
- Newey-West t matches the plain t when lags = 0.

---

## Deviations

(none yet)
