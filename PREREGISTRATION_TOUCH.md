# Reverse or continue at the forecast level: pre-registration

Written 2026-10-04, before any of the statistics below were computed. Not to be edited after the
first run; changes after that go below the line as deviations.

## Question

When the day's regular-session range first reaches its pre-open forecast size, does price then
**continue** in the direction of that move, **reverse**, or do **neither** reliably?

## What has already been seen (so is not blind)

`RESULT.md`, "Descriptive, not a test": of out-of-sample days that reached the forecast, 78-84% went
on to 1.10x and 55-61% to 1.25x. That bears on the barrier statistic below, which is therefore
reported but carries no verdict. The primary statistic (return to the close) has not been looked at.

## Events

- **Forecast F:** the walk-forward out-of-sample forecast from `range_model.walk_forward` (fit only on
  earlier years). Era A: NinjaTrader 1-minute bars, 2022-01-03 .. 2026-09-25. Era B: LSE 5-minute bars,
  2017-2020, roll windows already excluded.
- **Touch:** scanning regular-session bars from 09:30, the first bar in which the running range
  (high so far minus low so far) reaches F.
  - Up-touch if that bar made a new high: the level is `P = low_before + F`, rounded up to the tick.
  - Down-touch if it made a new low: `P = high_before - F`, rounded down to the tick.
  - A bar that makes both a new high and a new low is ambiguous: the day is dropped and counted.
- Touches in bars opening at or after 15:30 are excluded (too little day left to trade).
- A day with a gap of more than 0.5% between two adjacent regular-session bars is dropped (data error).
- **Side** = +1 for an up-touch, -1 for a down-touch. All results are signed so that + means
  continuation.

## Primary statistic (the verdict)

`R = side * (session close - P) / F`: the move from the level to the 16:00 close, in units of the
day's forecast. Under a driftless market E[R] = 0. One event per day; mean of R with a Newey-West
t-statistic (5 lags) over the events in date order.

| Verdict | Rule (all parts) |
|---|---|
| **CONTINUES** | era A mean R > 0 with t >= 2.0, and era B mean R > 0 |
| **REVERSES** | era A mean R < 0 with t <= -2.0, and era B mean R < 0 |
| **NEITHER** | anything else |

## Secondary, reported, no verdict

1. **First to hit:** from P, does price reach `P + 0.25F` (continuation) before `P - 0.25F`
   (reversal), with the close as the deadline? Share of continuation-first among days where one was
   hit, with ambiguous bars (both barriers inside one bar, or a reversal barrier that the touch bar's
   low may have reached before the touch) excluded and also shown as bounds. Same at +-0.5F.
2. **Is it the forecast level, or any level?** The primary statistic recomputed with the level at
   0.8F and at 1.2F. If the forecast level is special, 1.0F should stand apart from both.
3. By year, and by touch time (before 12:00, 12:00-14:00, 14:00-15:30).

## Trading translation, one MNQ ($2/pt)

Costs: $1.00 round-trip commission (0.50 pt); 1 tick slippage on stop entries, stop exits and the
16:00 exit; limit entries need price to trade 1 tick through the level.

- **Follow:** stop order at P in the touch direction; protective stop 0.25F back; otherwise out at
  16:00. If the touch bar's low could have reached the stop, it counts as stopped (worst case).
- **Fade:** limit order at P against the touch; protective stop 0.25F beyond; otherwise out at 16:00.

Called **tradeable** only if the rule in the verdict's direction is net positive in both eras, has
era A net t >= 2.0, and stays positive without its best 1% of days. A NEITHER verdict makes both rules
untradeable by definition.

## Checks before the real run (`verify_touch.py`)

- Touch side, level and tick rounding on hand-built bars; ambiguous outside bars dropped; no touch
  when the range never reaches F.
- No look-ahead: changing bars after the touch bar leaves the touch time, side and level unchanged.
- First-to-hit on hand-built paths, including the ambiguous cases.
- Trade fills: stop entry slippage, limit trade-through, worst-case stop in the touch bar.
- Null: on driftless simulated 1-minute paths the mean of R is near 0 and continuation-first is near
  50%, so the measurement itself has no built-in lean.
- Planted effect: adding drift after the touch is found with the right sign.

---

## Deviations

(none yet)
