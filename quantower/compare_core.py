"""Does the Quantower indicator's C# core reproduce the tested Python exactly? Stdlib only.

Reads Harness's CSV (RangeCore.cs run on the NinjaTrader file) and compares every session's eight inputs
and forecast with range_model.build_rows and forecast_today.forecast(model.json).
    py -3.14 compare_core.py harness_out.csv
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import forecast_today as ft  # noqa: E402
import range_model as rm  # noqa: E402


def main(path: str) -> int:
    model = json.loads((HERE.parent / "model.json").read_text(encoding="utf-8"))
    rows, _ = rm.build_rows(rm.daily_stats(rm.nt_bars()), date(2021, 1, 4), date(2026, 9, 25), 380,
                            rm.vix_before(rm.load_vix()))
    py = {r["d"]: r for r in rows}
    cs = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            cs[date.fromisoformat(row["d"])] = {k: float(v) for k, v in row.items() if k != "d"}
    both = sorted(set(py) & set(cs))
    worst_feat = 0.0
    worst_fc = (0.0, None)
    for d in both:
        for k in rm.FEATURES:
            worst_feat = max(worst_feat, abs(py[d][k] - cs[d][k]))
        fp, lo, hi = ft.forecast(model, py[d])
        for a, b in ((fp, cs[d]["forecast"]), (lo, cs[d]["lo80"]), (hi, cs[d]["hi80"])):
            rel = abs(a / b - 1)
            if rel > worst_fc[0]:
                worst_fc = (rel, d)
    only_py = sorted(set(py) - set(cs))
    print(f"python rows {len(py)}, C# rows {len(cs)}, compared {len(both)}")
    print(f"python rows the C# core did not produce: {len(only_py)} {only_py[:5]}")
    print(f"largest input difference: {worst_feat:.2e}")
    print(f"largest relative forecast/band difference: {worst_fc[0]:.2e} on {worst_fc[1]}")
    if halfhour:
        # The half-hour rule keeps sessions the model's minute rule dropped (NinjaTrader minutes missing,
        # every half-hour traded), so a few lookbacks differ. pine_port_check.py measured the same rule.
        diffs = [abs(cs[d]["forecast"] / ft.forecast(model, py[d])[0] - 1) for d in both]
        n = len(diffs)
        print(f"half-hour rule: forecast within 0.1% on {sum(x < 0.001 for x in diffs) / n:.1%},"
              f" within 1% on {sum(x < 0.01 for x in diffs) / n:.1%}, max {max(diffs):.2%}")
        return 0
    ok = len(both) == len(py) and worst_feat < 1e-9 and worst_fc[0] < 1e-9
    print("IDENTICAL" if ok else "DIFFERENT")
    return 0 if ok else 1


if __name__ == "__main__":
    halfhour = len(sys.argv) > 2 and sys.argv[2] == "halfhour"
    sys.exit(main(sys.argv[1]))
