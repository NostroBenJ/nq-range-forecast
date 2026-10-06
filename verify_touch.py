"""Checks for touch_test.py, run before the real result. Stdlib only.  py -3.14 verify_touch.py"""
from __future__ import annotations

import math
import random
import sys

import range_model as rm
import touch_test as tt

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def mirror(bars):
    return [(m, -o, -l, -h, -c) for m, o, h, l, c in bars]


# Hand-built day, F = 20: low 99 at the open, the touch comes in bar 2 at 119 (= 99 + 20).
B0 = (570, 100.0, 101.0, 99.0, 100.0)
B1 = (571, 100.0, 116.0, 100.0, 116.0)
TOUCH = (572, 116.0, 119.5, 115.0, 119.0)
UP3 = (573, 119.0, 124.5, 118.0, 124.0)        # reaches U = 124
DN3 = (573, 119.0, 120.0, 113.5, 114.0)        # reaches D = 114
BOTH3 = (573, 119.0, 125.0, 113.0, 120.0)
F = 20.0

# 1. the touch
t = tt.find_touch([B0, B1, TOUCH], F)
check("up-touch found in the right bar at low + F", t == {"i": 2, "minute": 572, "side": 1, "level": 119.0}, str(t))
t2 = tt.find_touch([B0, B1, TOUCH], 20.1)
check("up level rounds UP to the tick (99 + 20.1 -> 119.25)", t2["level"] == 119.25, str(t2))
dn = tt.find_touch(mirror([B0, B1, TOUCH]), F)
check("mirrored day gives a down-touch at high - F", dn["side"] == -1 and dn["level"] == -119.0, str(dn))
dn2 = tt.find_touch(mirror([B0, B1, TOUCH]), 20.1)
check("down level rounds DOWN to the tick (-99 - 20.1 -> -119.25)", dn2["level"] == -119.25, str(dn2))
outside = (572, 116.0, 120.0, 98.0, 110.0)     # new high AND new low in the bar that reaches F
check("a bar with a new high and a new low is ambiguous", tt.find_touch([B0, B1, outside], F) == "ambiguous")
check("no touch when the range never reaches F", tt.find_touch([B0, B1, TOUCH], 50.0) is None)
check("a first bar that alone spans F is ambiguous", tt.find_touch([B0, B1], 1.0) == "ambiguous")
later = tt.find_touch([B0, B1, TOUCH, (573, 119.0, 500.0, 1.0, 250.0)], F)
check("bars after the touch do not move it", later == t)

# 2. the primary statistic
check("R: close 124 after an up-touch at 119 is +5/20", tt.close_return([B0, B1, TOUCH, UP3], t, F) == 0.25)
check("R: mirrored day gives the same +0.25 (sign follows the side)",
      tt.close_return(mirror([B0, B1, TOUCH, UP3]), dn, F) == 0.25)

# 3. first to hit +-0.25F (U = 124, D = 114)
check("continuation first in a later bar", tt.first_hit([B0, B1, TOUCH, UP3], t, F, 0.25) == 1)
check("reversal first in a later bar", tt.first_hit([B0, B1, TOUCH, DN3], t, F, 0.25) == -1)
check("both in one later bar is ambiguous", tt.first_hit([B0, B1, TOUCH, BOTH3], t, F, 0.25) is None)
check("neither by the close", tt.first_hit([B0, B1, TOUCH], t, F, 0.25) == 0)
check("continuation inside the touch bar", tt.first_hit([B0, B1, (572, 116.0, 124.25, 115.0, 124.0)], t, F, 0.25) == 1)
check("touch bar closing at or below D is a reversal",
      tt.first_hit([B0, B1, (572, 116.0, 119.5, 113.75, 113.75)], t, F, 0.25) == -1)
check("touch bar low below D but close above is ambiguous (the low may be before the touch)",
      tt.first_hit([B0, B1, (572, 116.0, 119.5, 113.0, 119.0)], t, F, 0.25) is None)
check("mirrored day: continuation first", tt.first_hit(mirror([B0, B1, TOUCH, UP3]), dn, F, 0.25) == 1)

# 4. trade fills (entry 119.25 for follow; stop 114 / 124)
check("follow to the close: 124 - 0.25 - 119.25 - 0.5 = +4.0", tt.follow([B0, B1, TOUCH, UP3], t, F) == 4.0)
check("follow stopped later: 113.75 - 119.25 - 0.5 = -6.0", tt.follow([B0, B1, TOUCH, DN3], t, F) == -6.0)
check("follow: touch-bar low at the stop counts as stopped (worst case)",
      tt.follow([B0, B1, (572, 116.0, 119.5, 113.0, 119.0)], t, F) == -6.0)
check("fade: no fill when price only touches the level", tt.fade([B0, B1, (572, 116.0, 119.0, 115.0, 118.5)],
                                                                  tt.find_touch([B0, B1, (572, 116.0, 119.0, 115.0, 118.5)], F), F) is None)
check("fade stopped later: 119 - 124.25 - 0.5 = -5.75", tt.fade([B0, B1, TOUCH, UP3], t, F) == -5.75)
check("fade to the close: 119 - 114.25 - 0.5 = +4.25", tt.fade([B0, B1, TOUCH, DN3], t, F) == 4.25)
check("mirrored fade gives the same P&L", tt.fade(mirror([B0, B1, TOUCH, DN3]), dn, F) == 4.25)

# 5. data guards
check("a 0.5% gap between adjacent bars is flagged", tt.has_gap([B0, (571, 101.0, 101.0, 100.0, 100.0)], 1))
check("normal bars are not flagged", not tt.has_gap([B0, B1], 1))


# 6. null and planted effects on simulated days: a random walk in one-tick steps, so a resting order
#    at the level is reached exactly (no overshoot) and the true answer is "no lean".
def sim_day(rng: random.Random, steps_per_min: int = 7) -> list[tuple]:
    p = 20000.0
    bars = []
    for m in range(rm.RTH_OPEN, rm.RTH_CLOSE):
        o = h = l = p
        for _ in range(steps_per_min):
            p += tt.TICK if rng.random() < 0.5 else -tt.TICK
            h, l = max(h, p), min(l, p)
        bars.append((m, o, h, l, p))
    return bars


def drift_after(bars, touch, per_min):
    i, s = touch["i"], touch["side"]
    out = list(bars[:i + 1])
    for j, (m, o, h, l, c) in enumerate(bars[i + 1:], start=1):
        dd = s * per_min * j
        out.append((m, o + dd, h + dd, l + dd, c + dd))
    return out


print("\nsimulating 3,000 driftless days...")
rng = random.Random(11)
R0, H0, Rp, Rn, same = [], [], [], [], True
for _ in range(3000):
    bars = sim_day(rng)
    Fd = 21.0 * math.exp(rng.gauss(0, 0.3))
    t0 = tt.find_touch(bars, Fd)
    if not isinstance(t0, dict) or t0["minute"] >= tt.LAST_TOUCH:
        continue
    R0.append(tt.close_return(bars, t0, Fd))
    h = tt.first_hit(bars, t0, Fd, 0.25)
    if h in (1, -1):
        H0.append(1.0 if h == 1 else 0.0)
    up = drift_after(bars, t0, 0.02)
    dn_ = drift_after(bars, t0, -0.02)
    same &= tt.find_touch(up, Fd) == t0 and tt.find_touch(dn_, Fd) == t0
    Rp.append(tt.close_return(up, t0, Fd))
    Rn.append(tt.close_return(dn_, t0, Fd))
m0, t0s = sum(R0) / len(R0), rm.newey_west_t(R0, 5)
check("null: mean R is indistinguishable from 0", abs(t0s) < 2.5, f"n {len(R0)}, mean {m0:+.4f}, t {t0s:+.2f}")
share = sum(H0) / len(H0)
check("null: continuation-first share near 50%", 0.46 <= share <= 0.54, f"{share:.1%} of {len(H0)}")
tp, tn = rm.newey_west_t(Rp, 5), rm.newey_west_t(Rn, 5)
check("planted continuation drift is found as positive", tp > 3, f"mean {sum(Rp) / len(Rp):+.4f}, t {tp:+.2f}")
check("planted reversal drift is found as negative", tn < -3, f"mean {sum(Rn) / len(Rn):+.4f}, t {tn:+.2f}")
check("planting drift after the touch never moves the touch", same)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
