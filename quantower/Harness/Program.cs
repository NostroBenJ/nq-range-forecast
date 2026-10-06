// Harness: the indicator's core on the NinjaTrader 1-minute file, every session's inputs and forecast to CSV.
//   dotnet run -c Release -- <nq_1m_eth_nt.csv> <vix_fred.csv> <out.csv> [halfhour]
// Without "halfhour" the core uses the model's 380-minute rule (must match the Python exactly); with it, the
// half-hour rule the Quantower indicator uses.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using NqRangeForecast;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length > 3 && args[3] == "walk")
            return Walk(args[0], args[1], args[2]);
        if (args.Length > 3 && args[3] == "periods")
            return Periods(args[0]);
        var inv = CultureInfo.InvariantCulture;
        var tracker = new DailyTracker(380, halfHourRule: args.Length > 3 && args[3] == "halfhour");
        int bars = 0;
        using (var r = new StreamReader(args[0]))
        {
            r.ReadLine();
            string line;
            while ((line = r.ReadLine()) != null)
            {
                string[] f = line.Split(',');
                // NinjaTrader stamps 1-minute bars at their CLOSE on the New York clock: open = stamp - 1 minute.
                DateTime close = DateTime.ParseExact(f[0], "yyyy-MM-dd HH:mm:ss", inv);
                tracker.Add(close.AddMinutes(-1), double.Parse(f[2], inv), double.Parse(f[3], inv));
                bars++;
            }
        }

        var vix = new List<(DateTime, double)>();
        foreach (string line in File.ReadLines(args[1]))
        {
            string[] f = line.Split(',');
            if (f.Length < 2 || !DateTime.TryParseExact(f[0], "yyyy-MM-dd", inv, DateTimeStyles.None, out DateTime d))
                continue;
            if (double.TryParse(f[1], NumberStyles.Float, inv, out double v))
                vix.Add((d, v));                              // FRED writes "." on holidays
        }

        int rows = 0;
        using (var w = new StreamWriter(args[2]))
        {
            w.WriteLine("d,lr1,lr5,lr22,lon,lvix,fomc,mon,fri,forecast,lo80,hi80");
            for (DateTime d = new DateTime(2021, 1, 4); d <= new DateTime(2026, 9, 25); d = d.AddDays(1))
            {
                if (d.DayOfWeek == DayOfWeek.Saturday || d.DayOfWeek == DayOfWeek.Sunday)
                    continue;
                if (!tracker.TryGetInputs(d, out SessionInputs x, out _))
                    continue;
                if (!Vix.TryCloseBefore(vix, d, out double v, out _))
                    continue;
                double[] feats = Model.Features(x, v);
                var (fc, lo, hi) = Model.Forecast(x, v);
                w.Write(d.ToString("yyyy-MM-dd", inv));
                foreach (double z in feats)
                    w.Write("," + z.ToString("R", inv));
                w.WriteLine("," + fc.ToString("R", inv) + "," + lo.ToString("R", inv) + "," + hi.ToString("R", inv));
                rows++;
            }
        }
        Console.WriteLine($"{bars} bars read, {tracker.Count} days, {rows} sessions written to {args[2]}");
        return 0;
    }

    private static List<(DateTime Et, double O, double H, double L, double C)> ReadBars(string path)
    {
        var inv = CultureInfo.InvariantCulture;
        var list = new List<(DateTime, double, double, double, double)>();
        using var r = new StreamReader(path);
        r.ReadLine();
        string line;
        while ((line = r.ReadLine()) != null)
        {
            string[] f = line.Split(',');
            DateTime close = DateTime.ParseExact(f[0], "yyyy-MM-dd HH:mm:ss", inv);
            list.Add((close.AddMinutes(-1), double.Parse(f[1], inv), double.Parse(f[2], inv),
                double.Parse(f[3], inv), double.Parse(f[4], inv)));
        }
        return list;
    }

    private static List<(DateTime, double)> ReadVix(string path)
    {
        var inv = CultureInfo.InvariantCulture;
        var vix = new List<(DateTime, double)>();
        foreach (string line in File.ReadLines(path))
        {
            string[] f = line.Split(',');
            if (f.Length < 2 || !DateTime.TryParseExact(f[0], "yyyy-MM-dd", inv, DateTimeStyles.None, out DateTime d))
                continue;
            if (double.TryParse(f[1], NumberStyles.Float, inv, out double v))
                vix.Add((d, v));
        }
        return vix;
    }

    /// <summary>The indicator falls back to 5- or 30-minute history when a feed has too little 1-minute history.
    /// With the half-hour rule the inputs must not depend on the bar size: aggregates the 1-minute bars to 5 and
    /// 30 minutes on the clock grid and compares every session's inputs.</summary>
    private static int Periods(string barsPath)
    {
        var bars = ReadBars(barsPath);
        var trackers = new Dictionary<int, DailyTracker>();
        foreach (int p in new[] { 1, 5, 30 })
        {
            var t = new DailyTracker(380, halfHourRule: true);
            DateTime curKey = default;
            double hi = 0, lo = 0;
            bool have = false;
            foreach (var b in bars)
            {
                DateTime key = b.Et.AddMinutes(-(b.Et.Minute % p));
                if (have && key != curKey)
                {
                    t.Add(curKey, hi, lo);
                    have = false;
                }
                if (!have)
                {
                    curKey = key;
                    hi = b.H;
                    lo = b.L;
                    have = true;
                }
                else
                {
                    hi = Math.Max(hi, b.H);
                    lo = Math.Min(lo, b.L);
                }
            }
            if (have)
                t.Add(curKey, hi, lo);
            trackers[p] = t;
        }
        int sessions = 0, differ = 0;
        for (DateTime d = new DateTime(2021, 2, 1); d <= new DateTime(2026, 9, 25); d = d.AddDays(1))
        {
            if (d.DayOfWeek == DayOfWeek.Saturday || d.DayOfWeek == DayOfWeek.Sunday)
                continue;
            bool ok1 = trackers[1].TryGetInputs(d, out SessionInputs a, out _);
            foreach (int p in new[] { 5, 30 })
            {
                bool okp = trackers[p].TryGetInputs(d, out SessionInputs b, out _);
                bool same = ok1 == okp && (!ok1 || (a.R1 == b.R1 && a.M5 == b.M5 && a.M22 == b.M22
                    && a.OvernightRange == b.OvernightRange && a.PreviousSession == b.PreviousSession));
                if (!same)
                {
                    differ++;
                    if (differ <= 5)
                        Console.WriteLine($"  differs: {d:yyyy-MM-dd} at {p} minutes");
                }
            }
            if (ok1)
                sessions++;
        }
        Console.WriteLine($"{sessions} sessions with inputs; 5- and 30-minute bars differ from 1-minute on {differ}");
        Console.WriteLine(differ == 0 ? "SAME INPUTS AT 1, 5 AND 30 MINUTES" : "INPUTS DEPEND ON BAR SIZE");
        return differ == 0 ? 0 : 1;
    }

    private static bool Same(double a, double b) => (double.IsNaN(a) && double.IsNaN(b)) || Math.Abs(a - b) < 1e-9;

    private static bool Same(LineValues a, LineValues b) =>
        Same(a.Up, b.Up) && Same(a.Dn, b.Dn) && Same(a.TopUp, b.TopUp) && Same(a.TopDn, b.TopDn)
        && Same(a.BotUp, b.BotUp) && Same(a.BotDn, b.BotDn) && Same(a.Kept, b.Kept);

    /// <summary>The indicator's walk (half-hour rule, final model, FRED VIX) on the NinjaTrader bars as a 1-minute
    /// chart, once with whole bars and once with each bar arriving as three live updates; writes every session.</summary>
    private static int Walk(string barsPath, string vixPath, string outPath)
    {
        var inv = CultureInfo.InvariantCulture;
        var bars = ReadBars(barsPath);
        var vix = ReadVix(vixPath);
        var tracker = new DailyTracker(380, halfHourRule: true);
        foreach (var b in bars)
            tracker.Add(b.Et, b.H, b.L);
        ForecastInfo Source(DateTime sess)
        {
            var info = new ForecastInfo();
            if (!tracker.TryGetInputs(sess, out SessionInputs x, out string why))
            {
                info.Why = why;
                return info;
            }
            if (!Vix.TryCloseBefore(vix, sess, out double v, out _))
            {
                info.Why = "no vix";
                return info;
            }
            var f = Model.Forecast(x, v);
            info.Fc = f.Forecast;
            info.Lo = f.Low80;
            info.Hi = f.High80;
            info.Inputs = x;
            info.Vix = v;
            return info;
        }

        var whole = new SessionWalker(Source) { MaxRecords = int.MaxValue, MaxMarks = int.MaxValue };
        var ticks = new SessionWalker(Source) { MaxRecords = int.MaxValue, MaxMarks = int.MaxValue };
        int barDiffs = 0, breakDiffs = 0;
        var diffSessions = new SortedSet<DateTime>();
        foreach (var b in bars)
        {
            var a = whole.Update(b.Et, b.Et, b.H, b.L, b.C);
            // three live updates: the open, halfway to the bar's high and low, then the final bar
            var t1 = ticks.Update(b.Et, b.Et, b.O, b.O, b.O);
            ticks.Update(b.Et, b.Et, b.O + (b.H - b.O) / 2, b.O - (b.O - b.L) / 2, b.C);
            var t3 = ticks.Update(b.Et, b.Et, b.H, b.L, b.C);
            if (a.BreakLines != t1.BreakLines)
                breakDiffs++;
            if (a.Plot != t3.Plot || (a.Plot && !Same(a.Lines, t3.Lines)))
            {
                barDiffs++;
                diffSessions.Add(whole.Session);
            }
        }

        int ambiguousWhole = 0;
        var ambiguous = new HashSet<DateTime>();
        foreach (SessionRecord r in whole.Records)
        {
            if (r.Reached && r.ReachedSide == 0)
            {
                ambiguousWhole++;
                ambiguous.Add(r.Session);
            }
        }
        int recordDiffs = 0, recordDiffsOutsideAmbiguous = 0;
        for (int i = 0; i < whole.Records.Count; i++)
        {
            SessionRecord p = whole.Records[i], q = ticks.Records[i];
            bool same = p.Session == q.Session && p.Reached == q.Reached && p.ReachedSide == q.ReachedSide
                && p.ReachedMinute == q.ReachedMinute && Same(p.Level, q.Level) && Same(p.Actual, q.Actual)
                && p.Complete == q.Complete && Same(p.LastClose, q.LastClose);
            if (!same)
            {
                recordDiffs++;
                if (!ambiguous.Contains(p.Session))
                    recordDiffsOutsideAmbiguous++;
            }
        }
        int linesOutside = 0;
        foreach (DateTime s in diffSessions)
            if (!ambiguous.Contains(s))
                linesOutside++;

        using (var w = new StreamWriter(outPath))
        {
            w.WriteLine("session,fc,lo,hi,reached,side,minute,level,last_close,ended,complete,actual");
            foreach (SessionRecord r in whole.Records)
            {
                w.WriteLine(string.Join(",", r.Session.ToString("yyyy-MM-dd", inv), r.Fc.ToString("R", inv),
                    r.Lo.ToString("R", inv), r.Hi.ToString("R", inv), r.Reached ? 1 : 0, r.ReachedSide, r.ReachedMinute,
                    r.Level.ToString("R", inv), r.LastClose.ToString("R", inv), r.Ended ? 1 : 0, r.Complete ? 1 : 0,
                    r.Actual.ToString("R", inv)));
            }
        }
        Console.WriteLine($"{bars.Count} bars, {whole.Records.Count} sessions walked, written to {outPath}");
        Console.WriteLine($"tally: {whole.TallyReached} of {whole.TallyDays} complete forecast days reached before 15:30, "
            + $"{whole.TallyBeyond} closed beyond the level");
        Console.WriteLine($"whole bars vs live updates: line breaks differ on {breakDiffs} bars; final line values differ on "
            + $"{barDiffs} bars in {diffSessions.Count} sessions ({linesOutside} of them not an ambiguous touch); "
            + $"session records differ on {recordDiffs} ({recordDiffsOutsideAmbiguous} not an ambiguous touch); "
            + $"ambiguous touches (new high and new low in the same bar): {ambiguousWhole}");
        bool ok = breakDiffs == 0 && linesOutside == 0 && recordDiffsOutsideAmbiguous == 0;
        Console.WriteLine(ok ? "LIVE UPDATES MATCH WHOLE BARS" : "LIVE UPDATES DIFFER");
        return ok ? 0 : 1;
    }
}
