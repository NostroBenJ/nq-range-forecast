// The NQ range forecast's arithmetic, with no Quantower types, so the same file is compiled into the
// Quantower indicator and into the check harness that runs it on the NinjaTrader data.
// It mirrors the Python that was tested (Downloads\nq_range): range_model.daily_stats, build_rows'
// kept-session rule, and the final model in model.json.
using System;
using System.Collections.Generic;

namespace NqRangeForecast
{
    /// <summary>The model that passed PREREGISTRATION.md, refit on every NinjaTrader session
    /// 2021-02 .. 2026-09-25 (model.json, 2026-10-04).</summary>
    public static class Model
    {
        public const double B0 = 0.7476858583414273;
        public const double BLr1 = 0.04139622668684042;
        public const double BLr5 = 0.36715751400330815;
        public const double BLr22 = 0.12483805626794996;
        public const double BLon = 0.26695766370307267;
        public const double BLvix = 0.14789676438374263;
        public const double BFomc = 0.3404870779832627;
        public const double BMon = -0.08346899054455817;
        public const double BFri = -0.008118208380775436;
        public const double Q10 = -0.4401180155790621;
        public const double Q90 = 0.4600218341208407;

        /// <summary>The eight inputs in model.json's order: lr1, lr5, lr22, lon, lvix, fomc, mon, fri.</summary>
        public static double[] Features(SessionInputs x, double vix)
        {
            return new[]
            {
                Math.Log(x.R1), Math.Log(x.M5), Math.Log(x.M22), Math.Log(x.OvernightRange), Math.Log(vix),
                Calendar.IsFomc(x.Session) ? 1.0 : 0.0,
                x.Session.DayOfWeek == DayOfWeek.Monday ? 1.0 : 0.0,
                x.Session.DayOfWeek == DayOfWeek.Friday ? 1.0 : 0.0,
            };
        }

        /// <summary>(forecast, 80% band low, 80% band high) in points.</summary>
        public static (double Forecast, double Low80, double High80) Forecast(SessionInputs x, double vix)
        {
            double[] f = Features(x, vix);
            double[] b = { BLr1, BLr5, BLr22, BLon, BLvix, BFomc, BMon, BFri };
            double s = 0.0;                                  // summed as range_model.predict does
            for (int i = 0; i < f.Length; i++)
                s += b[i] * f[i];
            double lf = B0 + s;
            return (Math.Exp(lf), Math.Exp(lf + Q10), Math.Exp(lf + Q90));
        }

        /// <summary>exp(FOMC coefficient) - 1: how much bigger an FOMC day runs, other inputs equal.</summary>
        public static double FomcLift => Math.Exp(BFomc) - 1.0;
    }

    /// <summary>Everything the model needs from prices for one session, all from bars that open before 09:30.</summary>
    public sealed class SessionInputs
    {
        public DateTime Session;          // the session's New York date
        public DateTime PreviousSession;  // the date R1 comes from
        public double R1;                 // range of the previous complete session
        public double M5;                 // mean range of the last 5 complete sessions
        public double M22;                // mean range of the last 22 complete sessions
        public double OvernightHigh;
        public double OvernightLow;
        public double OvernightRange;     // bars opening in [18:00 the day before, 09:30)
        public int OvernightBars;
        public DateTime OvernightLastBar; // New York open time of the latest overnight bar seen
    }

    /// <summary>range_model.daily_stats plus build_rows' kept-session rule, for bars stamped at their OPEN on
    /// the New York clock. A session counts (is "kept") when it is a weekday and complete:
    ///   * minute rule (the model's): at least minRthBars regular-session bars, 380 of 390 for 1-minute bars;
    ///   * half-hour rule (the indicators'): every half-hour of 09:30-16:00 has at least one bar. A single
    ///     contract's history before its roll is thin back-month trading with empty minutes, which the minute
    ///     rule would throw out for weeks after every roll. Half days fail both rules.</summary>
    public sealed class DailyTracker
    {
        public const int RthOpen = 570;     // 09:30
        public const int RthClose = 960;    // 16:00
        public const int OnStart = 1080;    // 18:00
        public const int Lookback = 22;
        private const int AllHalfHours = (1 << 13) - 1;

        private sealed class Day
        {
            public double Hi = double.NegativeInfinity, Lo = double.PositiveInfinity;
            public int N;
            public int HalfHours;           // bit k: a bar opened in the k-th half-hour after 09:30
            public double OnHi = double.NegativeInfinity, OnLo = double.PositiveInfinity;
            public int OnN;
            public DateTime OnLast;
        }

        private readonly SortedDictionary<DateTime, Day> days = new SortedDictionary<DateTime, Day>();
        private readonly int minRthBars;
        private readonly bool halfHourRule;
        private List<(DateTime Date, double Range)> kept;

        public DailyTracker(int minRthBars = 380, bool halfHourRule = false)
        {
            this.minRthBars = minRthBars;
            this.halfHourRule = halfHourRule;
        }

        public int Count => days.Count;

        private Day Get(DateTime date)
        {
            if (!days.TryGetValue(date, out Day d))
                days[date] = d = new Day();
            return d;
        }

        /// <summary>One bar by its open time on the New York clock (any order; repeats are the caller's to avoid).</summary>
        public void Add(DateTime etOpen, double high, double low)
        {
            int m = etOpen.Hour * 60 + etOpen.Minute;
            DateTime date = etOpen.Date;
            kept = null;
            if (m >= RthOpen && m < RthClose)
            {
                Day d = Get(date);
                if (high > d.Hi) d.Hi = high;
                if (low < d.Lo) d.Lo = low;
                d.N++;
                d.HalfHours |= 1 << ((m - RthOpen) / 30);
                return;
            }
            DateTime onDay;
            if (m >= OnStart) onDay = date.AddDays(1);
            else if (m < RthOpen) onDay = date;
            else return;                                     // 16:00-18:00 belongs to nobody
            Day o = Get(onDay);
            if (high > o.OnHi) o.OnHi = high;
            if (low < o.OnLo) o.OnLo = low;
            o.OnN++;
            if (etOpen > o.OnLast) o.OnLast = etOpen;
        }

        private List<(DateTime Date, double Range)> Kept()
        {
            if (kept != null)
                return kept;
            kept = new List<(DateTime, double)>();
            foreach (var kv in days)
            {
                DateTime k = kv.Key;
                if (k.DayOfWeek == DayOfWeek.Saturday || k.DayOfWeek == DayOfWeek.Sunday)
                    continue;
                bool complete = halfHourRule ? kv.Value.HalfHours == AllHalfHours : kv.Value.N >= minRthBars;
                if (complete)
                    kept.Add((k, kv.Value.Hi - kv.Value.Lo));
            }
            return kept;
        }

        /// <summary>How many complete sessions come before `session` (the model needs 22).</summary>
        public int CompleteBefore(DateTime session)
        {
            List<(DateTime Date, double Range)> k = Kept();
            int n = 0;
            while (n < k.Count && k[n].Date < session.Date)
                n++;
            return n;
        }

        /// <summary>The model's price inputs for session date, or false with the reason.</summary>
        public bool TryGetInputs(DateTime session, out SessionInputs inputs, out string why)
        {
            inputs = null;
            session = session.Date;
            List<(DateTime Date, double Range)> k = Kept();
            int end = k.Count;                               // first kept session on or after `session`
            while (end > 0 && k[end - 1].Date >= session)
                end--;
            if (end < Lookback)
            {
                why = "only " + end + " complete sessions before " + session.ToString("MM-dd") + " (need 22)";
                return false;
            }
            if (!days.TryGetValue(session, out Day d) || d.OnN == 0 || !(d.OnHi - d.OnLo > 0))
            {
                why = "no overnight bars yet for " + session.ToString("MM-dd");
                return false;
            }
            double s5 = 0.0, s22 = 0.0;
            for (int i = end - 5; i < end; i++)
                s5 += k[i].Range;
            for (int i = end - Lookback; i < end; i++)
                s22 += k[i].Range;
            inputs = new SessionInputs
            {
                Session = session,
                PreviousSession = k[end - 1].Date,
                R1 = k[end - 1].Range,
                M5 = s5 / 5,
                M22 = s22 / Lookback,
                OvernightHigh = d.OnHi,
                OvernightLow = d.OnLo,
                OvernightRange = d.OnHi - d.OnLo,
                OvernightBars = d.OnN,
                OvernightLastBar = d.OnLast,
            };
            why = null;
            return true;
        }
    }

    /// <summary>VIX: the latest daily close dated BEFORE the session (range_model.vix_before).</summary>
    public static class Vix
    {
        public static bool TryCloseBefore(IReadOnlyList<(DateTime Date, double Close)> closes, DateTime session,
            out double close, out DateTime date)
        {
            close = double.NaN;
            date = default;
            for (int i = closes.Count - 1; i >= 0; i--)
            {
                if (closes[i].Date < session.Date)
                {
                    close = closes[i].Close;
                    date = closes[i].Date;
                    return close > 0;
                }
            }
            return false;
        }
    }

    public static class Calendar
    {
        /// <summary>Scheduled FOMC statement days: range_model.FOMC (2016-2026, federalreserve.gov, fetched
        /// 2026-10-01) plus forecast_today.EXTRA_FOMC (fetched 2026-10-04).</summary>
        private static readonly HashSet<DateTime> Fomc = Parse(
            "2016-01-27 2016-03-16 2016-04-27 2016-06-15 2016-07-27 2016-09-21 2016-11-02 2016-12-14 " +
            "2017-02-01 2017-03-15 2017-05-03 2017-06-14 2017-07-26 2017-09-20 2017-11-01 2017-12-13 " +
            "2018-01-31 2018-03-21 2018-05-02 2018-06-13 2018-08-01 2018-09-26 2018-11-08 2018-12-19 " +
            "2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11 " +
            "2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16 " +
            "2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15 " +
            "2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14 " +
            "2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13 " +
            "2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18 " +
            "2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10 " +
            "2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16 2026-10-28 2026-12-09 " +
            "2027-01-27 2027-03-17 2027-04-28 2027-06-09 2027-07-28 2027-09-15 2027-10-27 2027-12-08");

        private static HashSet<DateTime> Parse(string s)
        {
            var set = new HashSet<DateTime>();
            foreach (string t in s.Split(' ', StringSplitOptions.RemoveEmptyEntries))
                set.Add(DateTime.ParseExact(t, "yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture));
            return set;
        }

        public static bool IsFomc(DateTime date) => Fomc.Contains(date.Date);
    }

    /// <summary>One session's forecast as the walker needs it, plus what the table shows.</summary>
    public sealed class ForecastInfo
    {
        public double Fc = double.NaN, Lo = double.NaN, Hi = double.NaN;
        public SessionInputs Inputs;
        public double Vix = double.NaN;
        public string VixFrom;
        public string Why;                       // why there is no forecast
        public bool Ok => !double.IsNaN(Fc);
    }

    public sealed class SessionRecord
    {
        public DateTime Session;
        public double Fc = double.NaN, Lo = double.NaN, Hi = double.NaN;
        public double Actual = double.NaN;       // regular-session range, once the session has ended complete
        public bool Ended, Complete;
        public int ReachedSide;                  // +1 reached with a new high, -1 with a new low, 0 not / both at once
        public bool Reached;
        public int ReachedMinute = -1;           // New York minute of the bar that reached it
        public double Level = double.NaN;        // the level as it stood when price reached it
        public double LastClose = double.NaN;    // the last regular-session bar's close
    }

    public sealed class TouchMark
    {
        public DateTime BarKey;
        public double Price;
        public int Side;
    }

    /// <summary>The seven line values for one bar; NaN draws nothing.</summary>
    public struct LineValues
    {
        public double Up, Dn, TopUp, TopDn, BotUp, BotDn, Kept;
    }

    /// <summary>The walk over a chart's bars: sessions, the levels, the moment a day reaches its forecast, the
    /// tally. Platform-free so the harness can run it on the NinjaTrader bars and compare it with
    /// touch_test.py. Update may be called several times for the same bar (live ticks); the last call for a bar
    /// carries its final high, low and close.</summary>
    public sealed class SessionWalker
    {
        private readonly Func<DateTime, ForecastInfo> source;
        public bool ShowLevels = true, ShowBand = true, KeepAfter = true;

        private DateTime barKey;
        private bool haveBar;
        private DateTime barEt;
        private double barHigh, barLow, barClose;
        private bool barRth;
        private DateTime barSession;

        private double hiClosed, loClosed;                 // session extremes over the bars before the current one
        private bool done, doneTop, doneBot;               // reached, as of now
        private bool doneBefore, doneTopBefore, doneBotBefore;   // as of the bars before the current one

        public bool InSession { get; private set; }
        public DateTime Session { get; private set; }
        public ForecastInfo Info { get; private set; } = new ForecastInfo();
        public double CurHigh { get; private set; } = double.NaN;
        public double CurLow { get; private set; } = double.NaN;
        public readonly List<SessionRecord> Records = new List<SessionRecord>();
        public readonly List<TouchMark> Marks = new List<TouchMark>();
        public int TallyDays, TallyReached, TallyBeyond;
        public int MaxRecords = 10, MaxMarks = 60;

        public SessionWalker(Func<DateTime, ForecastInfo> source)
        {
            this.source = source;
        }

        private SessionRecord Current => Records.Count > 0 ? Records[Records.Count - 1] : null;

        private static void Classify(DateTime et, out int m, out bool rth, out DateTime sess)
        {
            m = et.Hour * 60 + et.Minute;
            bool weekday = et.DayOfWeek != DayOfWeek.Saturday && et.DayOfWeek != DayOfWeek.Sunday;
            rth = weekday && m >= DailyTracker.RthOpen && m < DailyTracker.RthClose;
            sess = m >= DailyTracker.OnStart ? et.Date.AddDays(1) : et.Date;
        }

        /// <summary>True when an update for this bar would start a regular session (so the caller can refresh its
        /// data first, outside any lock).</summary>
        public bool WouldStart(DateTime key, DateTime et)
        {
            Classify(et, out _, out bool rth, out DateTime sess);
            return rth && (!haveBar || key != barKey) && (!barRth || sess != barSession || !InSession);
        }

        /// <summary>One update for the bar `key` that opened at `et` (New York clock). Returns whether the lines
        /// must break before this bar (a new session) and, inside a session, the bar's line values.</summary>
        public (bool BreakLines, bool Plot, LineValues Lines) Update(DateTime key, DateTime et, double high, double low, double close)
        {
            Classify(et, out int m, out bool rth, out DateTime sess);
            bool breakLines = false;
            if (!haveBar || key != barKey)
            {
                if (haveBar && barRth && InSession)
                {
                    hiClosed = Math.Max(hiClosed, barHigh);
                    loClosed = Math.Min(loClosed, barLow);
                    Current.LastClose = barClose;
                    lastRthMinute = barEt.Hour * 60 + barEt.Minute;
                }
                doneBefore = done;
                doneTopBefore = doneTop;
                doneBotBefore = doneBot;
                bool starts = rth && (!barRth || sess != barSession || !InSession);
                if (InSession && (!rth || starts))
                    EndSession();
                if (starts)
                {
                    StartSession(sess);
                    breakLines = true;
                }
                haveBar = true;
                barKey = key;
                barEt = et;
                barRth = rth;
                barSession = sess;
            }
            barHigh = high;
            barLow = low;
            barClose = close;
            if (!(InSession && rth))
                return (breakLines, false, default);
            Track(m);
            return (breakLines, Info.Ok, Info.Ok ? Lines() : default);
        }

        private int lastRthMinute;

        private void StartSession(DateTime sess)
        {
            InSession = true;
            Session = sess;
            hiClosed = double.NegativeInfinity;
            loClosed = double.PositiveInfinity;
            done = doneTop = doneBot = false;
            doneBefore = doneTopBefore = doneBotBefore = false;
            lastRthMinute = -1;
            Info = source(sess) ?? new ForecastInfo { Why = "no forecast source" };
            Records.Add(new SessionRecord { Session = sess, Fc = Info.Fc, Lo = Info.Lo, Hi = Info.Hi });
            if (Records.Count > MaxRecords)
                Records.RemoveAt(0);
        }

        private void EndSession()
        {
            SessionRecord r = Current;
            bool complete = lastRthMinute >= 930;            // half days end at 13:00
            r.Ended = true;
            r.Complete = complete;
            r.Actual = complete ? hiClosed - loClosed : double.NaN;
            if (complete && Info.Ok)
            {
                TallyDays++;
                if (r.Reached && r.ReachedSide != 0 && r.ReachedMinute < 930)   // the test's window: before 15:30
                {
                    TallyReached++;
                    if (r.ReachedSide * (r.LastClose - r.Level) > 0)
                        TallyBeyond++;
                }
            }
            InSession = false;
        }

        private void Track(int m)
        {
            CurHigh = Math.Max(hiClosed, barHigh);
            CurLow = Math.Min(loClosed, barLow);
            if (!Info.Ok)
                return;
            double rng = CurHigh - CurLow;
            if (!done && rng >= Info.Fc)
            {
                done = true;
                bool newHigh = barHigh > hiClosed;
                bool newLow = barLow < loClosed;
                int side = newHigh && !newLow ? 1 : newLow && !newHigh ? -1 : 0;
                SessionRecord r = Current;
                r.Reached = true;
                r.ReachedSide = side;
                r.ReachedMinute = m;
                r.Level = side == 1 ? CurLow + Info.Fc : side == -1 ? CurHigh - Info.Fc : double.NaN;
                Marks.Add(new TouchMark { BarKey = barKey, Price = side == -1 ? barLow : barHigh, Side = side });
                if (Marks.Count > MaxMarks)
                    Marks.RemoveAt(0);
            }
            if (!doneTop && rng >= Info.Hi)
                doneTop = true;
            if (!doneBot && rng >= Info.Lo)
                doneBot = true;
        }

        private LineValues Lines()
        {
            double fc = Info.Fc, lo = Info.Lo, hi = Info.Hi, nan = double.NaN;
            double kept = Current.Level;
            return new LineValues
            {
                Up = ShowLevels && !doneBefore ? CurLow + fc : nan,
                Dn = ShowLevels && !doneBefore ? CurHigh - fc : nan,
                TopUp = ShowBand && !doneTopBefore ? CurLow + hi : nan,
                TopDn = ShowBand && !doneTopBefore ? CurHigh - hi : nan,
                BotUp = ShowBand && !doneBotBefore ? CurLow + lo : nan,
                BotDn = ShowBand && !doneBotBefore ? CurHigh - lo : nan,
                Kept = ShowLevels && KeepAfter && done && !double.IsNaN(kept) ? kept : nan,
            };
        }
    }
}
