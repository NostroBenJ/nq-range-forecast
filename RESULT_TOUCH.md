# Reverse or continue at the forecast level: result

Run once on 2026-10-04 22:47 EDT after the files were frozen (`FROZEN_TOUCH.txt`). Full output:
`results_touch.txt`. `verify_touch.py`: 32/32 checks pass, including a driftless simulation (no
built-in lean: four more seeds gave mean R of -0.023, +0.022, -0.012, -0.012) and planted drift found
both ways.

## Verdict: NEITHER. At the forecast level price is a coin flip

| | Era A, NT 1-min 2022-2026 | Era B, LSE 5-min 2017-2020 |
|---|---|---|
| Days the forecast was reached (before 15:30) | 579 (49%) | 411 (47%) |
| Mean move from the level to the close | +0.027 F (+4.8 pts), t +1.22 | +0.054 F (+3.0 pts), t +1.75 |
| Closed beyond the level | 51% | 53% |
| First to +-0.25F: continuation first | 49.7% | 53.1% (bounds 41-64%, 5-min bars) |
| First to +-0.5F: continuation first | 50.3% | 57.5% (t +2.45, secondary) |

- Both eras lean slightly toward continuation, but the main era is not significant (t 1.22 against a
  pre-registered bar of 2.0) and the years disagree: 2023 t +2.66, 2024 t -0.73, 2026 t -0.96.
- In era A, with 1-minute bars, the clean symmetric test is 248 continuation-first against 251
  reversal-first. That is as close to a coin flip as data gets.
- **The level is not special.** The same statistic at 0.8F and 1.2F is about the same small positive
  number (+0.047 and +0.026 in era A). The faint lean is ordinary intraday momentum on big days, not
  anything the forecast level does.

## Trades at one MNQ (net of $1 commission and 1-tick slippage)

| | Follow (stop entry at the level) | Fade (limit at the level) |
|---|---|---|
| Era A | +$1.13/trade, t +0.14, max drawdown $7,120 | -$1.62/trade, t -0.19, max drawdown $4,767 |
| Era B | -$1.12/trade, t -0.28 | -$0.44/trade, t -0.10 |

Both are about zero before you count the drawdowns, and both drawdowns would end a $2,000 trailing
account. Neither is tradeable.

## What this corrects

The descriptive table in `RESULT.md` (78-84% of days that reach the forecast go on to 1.1x) was first
read as "a day that reaches its forecast usually keeps going, so fading it means standing
in front of a trend day". That was wrong. Going a little further is what a coin flip does too: from a
new extreme with hours left, price usually wanders 10% further, and is just as likely to wander 10%
back first. The symmetric test above is the right measure, and it says 50/50.

## Use

The forecast is for sizing, stops, targets and knowing what kind of day it is. It is not an entry
level, for a fade or a breakout.
