// ═════════════════════════════════════════════════════════════════════════════════════════════════════
//  NQ RANGE FORECAST for Quantower  (Downloads\nq_range\quantower)
//  Forecasts the size of today's 09:30-16:00 NQ range before the open, and draws where the day would reach
//  that size from its low or from its high. Same model and same drawing as NQ_Range_Forecast.pine.
//
//  What the research behind it says (2026-10-04):
//   · Size is forecastable. Out of sample the model explained 41% of the variation in the daily range on
//     2022-2026 against 25% for the plain 22-day average (Newey-West t +5.40), and 64% vs 57% on 2017-2020.
//     Its 80% band caught 78.6% / 78.7% of days. (RESULT.md)
//   · Direction is not. On days the range reached the forecast, price went a quarter-forecast further first
//     on 248 days and came back a quarter first on 251. Following or fading the level both made about $0 a
//     trade at one MNQ. (RESULT_TOUCH.md) The levels mark SIZE, not entries.
//
//  Everything that decides a number is in Core\RangeCore.cs (the model, the daily ranges, the walk over the
//  chart's bars), which Harness runs on the NinjaTrader data: compare_core.py and compare_walk.py check it
//  against the tested Python. This file is only the Quantower part: 1-minute history, yesterday's VIX
//  close, the lines and the table.
//
//  Data:
//   · 1-minute history of the chart's symbol (Symbol.GetHistory: blocks until loaded, then keeps updating
//     live). A session counts when every half-hour of 09:30-16:00 traded (RangeCore's half-hour rule), so a
//     contract's thin back-month weeks before its roll still count.
//   · VIX: the latest daily close dated before the session, downloaded from Yahoo (FRED if Yahoo fails).
//     Rithmic has no CBOE index data. "VIX close override" replaces it for today's and later sessions.
//   · The forecast is fixed on the first regular-session bar; before 09:30 the table shows a provisional
//     number that moves with the overnight range.
// ═════════════════════════════════════════════════════════════════════════════════════════════════════
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using TradingPlatform.BusinessLayer;

namespace NqRangeForecast
{
    public sealed class NqRangeForecastIndicator : Indicator
    {
        // ─── inputs ──────────────────────────────────────────────────────────────────────────────────
        [InputParameter("Forecast levels (session low + forecast, session high - forecast)", 10)]
        public bool ShowLevels = true;

        [InputParameter("80% band edges", 20)]
        public bool ShowBand = true;

        [InputParameter("Keep a level after price reaches it (faded)", 30)]
        public bool KeepAfter = true;

        [InputParameter("Show table", 40)]
        public bool ShowTable = true;

        [InputParameter("Table position", 45, variants: new object[] { "Top right", 0, "Bottom right", 1, "Top left", 2, "Bottom left", 3 })]
        public int TablePosition = 0;

        [InputParameter("Recent sessions in the table", 50, 0, 10, 1, 0)]
        public int RecentSessions = 5;

        [InputParameter("VIX close override (0 = download yesterday's close)", 60, 0, 200, 0.01, 2)]
        public double VixOverride = 0.0;

        [InputParameter("Days of 1-minute history to load", 70, 35, 120, 1, 0)]
        public int HistoryDays = 50;

        // ─── lines ───────────────────────────────────────────────────────────────────────────────────
        private const int LUp = 0, LDn = 1, LTopUp = 2, LTopDn = 3, LBotUp = 4, LBotDn = 5, LKept = 6, NLines = 7;
        private static readonly Color Blue = Color.FromArgb(0x2a, 0x78, 0xd6);     // black_scholes._INK slot 1
        private static readonly Color Gray = Color.FromArgb(0x89, 0x87, 0x81);

        public NqRangeForecastIndicator()
            : base()
        {
            Name = "NQ Range Forecast";
            Description = "Pre-open forecast of the size of NQ's 09:30-16:00 range, and where the day would reach it. "
                + "The levels mark size, not entries: tested, price continues or reverses there 50/50.";
            SeparateWindow = false;
            AddLineSeries("Forecast level above (low + forecast)", Blue, 2, LineStyle.Solid);
            AddLineSeries("Forecast level below (high - forecast)", Blue, 2, LineStyle.Solid);
            AddLineSeries("Band top above", Gray, 1, LineStyle.Dot);
            AddLineSeries("Band top below", Gray, 1, LineStyle.Dot);
            AddLineSeries("Band bottom above", Color.FromArgb(150, Gray), 1, LineStyle.Dot);
            AddLineSeries("Band bottom below", Color.FromArgb(150, Gray), 1, LineStyle.Dot);
            AddLineSeries("Level after it was reached", Color.FromArgb(110, Blue), 2, LineStyle.Solid);
        }

        public override string ShortName => "NQ range forecast";

        // ─── state: every field below is read and written under `sync` ───────────────────────────────
        // Quantower calls (Time, High, SetValue, SetLineBreak, history reads) and downloads never happen while
        // `sync` is held: OnPaintChart takes it on the UI thread, so holding it across either could stall the
        // chart.
        private readonly object sync = new object();
        private TimeZoneInfo ny;
        private HistoricalData minutes;
        private string historyError;
        private string historyNote = "";

        // Some feeds keep little 1-minute history (dxFeed: about 8 days), so coarser bars are tried in turn.
        // Harness "periods" checks that 1-, 5- and 30-minute bars give identical inputs (1,460 / 1,460 sessions).
        private static readonly (Period Period, string Name)[] HistoryPeriods =
            { (Period.MIN1, "1-minute"), (Period.MIN5, "5-minute"), (Period.MIN30, "30-minute") };
        private DailyTracker tracker;
        private SessionWalker walker;
        private List<(DateTime Date, double Close)> vixCloses = new List<(DateTime, double)>();
        private DateTime vixFetchedUtc, vixTriedUtc;
        private string vixStatus = "not downloaded yet";
        private volatile bool vixFetching;

        // provisional forecast for the next session
        private DateTime pSession;
        private ForecastInfo pInfo = new ForecastInfo { Why = "loading" };
        private DateTime pComputedUtc;

        // ─── life cycle ──────────────────────────────────────────────────────────────────────────────
        protected override void OnInit()
        {
            ny = FindZone("Eastern Standard Time", "America/New_York");
            lock (sync)
            {
                walker = new SessionWalker(ForecastFor) { ShowLevels = ShowLevels, ShowBand = ShowBand, KeepAfter = KeepAfter };
            }
            // The finest bars that hold 22 complete sessions before the next session; else the deepest found.
            DateTime target = TargetSession(ToEt(DateTime.UtcNow));
            DateTime from = DateTime.UtcNow.AddDays(-HistoryDays);
            HistoricalData best = null;
            DailyTracker bestTracker = null;
            int bestN = -1;
            string err = null, note = "", tried = "";
            foreach (var (period, name) in HistoryPeriods)
            {
                HistoricalData hd;
                try
                {
                    hd = Symbol?.GetHistory(period, Symbol.HistoryType, from);
                }
                catch (Exception e)
                {
                    err = e.Message;
                    continue;
                }
                if (hd == null)
                {
                    err = "no symbol";
                    break;
                }
                DailyTracker t = BuildTracker(hd, out string readErr);
                int n = t == null ? -1 : t.CompleteBefore(target);
                tried += (tried.Length > 0 ? ", " : "") + name + " " + Math.Max(n, 0);
                if (n > bestN)
                {
                    DisposeQuietly(best);
                    best = hd;
                    bestTracker = t;
                    bestN = n;
                    note = name + " history";
                    err = readErr;
                }
                else
                {
                    DisposeQuietly(hd);
                }
                if (bestN >= DailyTracker.Lookback)
                    break;
            }
            lock (sync)
            {
                minutes = best;
                tracker = bestTracker;
                historyError = best == null ? (err ?? "no history") : err;
                historyNote = best == null ? "" : note + " (complete sessions found: " + tried + ")";
            }
            FetchVix();
            ComputeProvisional();
        }

        private static void DisposeQuietly(HistoricalData hd)
        {
            try
            {
                hd?.Dispose();
            }
            catch
            {
            }
        }

        protected override void OnClear()
        {
            HistoricalData hd;
            lock (sync)
            {
                hd = minutes;
                minutes = null;
            }
            try
            {
                hd?.Dispose();
            }
            catch
            {
            }
        }

        // ─── the walk ────────────────────────────────────────────────────────────────────────────────
        protected override void OnUpdate(UpdateArgs args)
        {
            if (ny == null)
                return;
            DateTime t = Time();
            double h = High(), l = Low(), c = Close();
            DateTime et = ToEt(t);
            bool historical = args.Reason == UpdateReason.HistoricalBar;

            if (!historical)
            {
                bool willStart;
                lock (sync)
                    willStart = walker != null && walker.WouldStart(t, et);
                if (willStart)
                {
                    RebuildTracker();                                   // take the overnight that just finished
                    if (!VixFresh(ToEt(DateTime.UtcNow).Date))
                        FetchVix();
                }
            }

            (bool BreakLines, bool Plot, LineValues Lines) step;
            lock (sync)
            {
                if (walker == null)
                    return;
                step = walker.Update(t, et, h, l, c);
            }

            if (step.BreakLines)
            {
                for (int i = 0; i < NLines; i++)
                    SetLineBreak(0, i);                                 // sessions never join
            }
            if (step.Plot)
            {
                SetValue(step.Lines.Up, LUp);
                SetValue(step.Lines.Dn, LDn);
                SetValue(step.Lines.TopUp, LTopUp);
                SetValue(step.Lines.TopDn, LTopDn);
                SetValue(step.Lines.BotUp, LBotUp);
                SetValue(step.Lines.BotDn, LBotDn);
                SetValue(step.Lines.Kept, LKept);
            }
            else if (!historical)
            {
                MaybeProvisional();
            }
        }

        /// <summary>The walker's forecast source. Called under the lock; no Quantower calls, no downloads.</summary>
        private ForecastInfo ForecastFor(DateTime sess)
        {
            var info = new ForecastInfo();
            if (tracker == null)
            {
                info.Why = historyError != null ? "1-minute history did not load: " + historyError : "loading history";
                return info;
            }
            if (!tracker.TryGetInputs(sess, out SessionInputs x, out string why))
            {
                info.Why = historyError != null ? "1-minute history did not load: " + historyError : why;
                return info;
            }
            info.Inputs = x;
            if (!TryVix(sess, out double v, out string from))
            {
                info.Why = "no VIX close before " + sess.ToString("MM-dd", CultureInfo.InvariantCulture) + ": " + vixStatus
                    + ". Set the VIX close override.";
                return info;
            }
            var f = Model.Forecast(x, v);
            info.Fc = f.Forecast;
            info.Lo = f.Low80;
            info.Hi = f.High80;
            info.Vix = v;
            info.VixFrom = from;
            return info;
        }

        // ─── data ────────────────────────────────────────────────────────────────────────────────────
        private void RebuildTracker()
        {
            HistoricalData hd;
            lock (sync)
                hd = minutes;
            if (hd == null)
                return;
            DailyTracker t = BuildTracker(hd, out string err);
            lock (sync)
            {
                if (t != null)
                    tracker = t;                                        // else keep the previous one
                if (err != null)
                    historyError = err;
            }
        }

        /// <summary>A tracker over every bar of `hd` (half-hour rule), or null with the error.</summary>
        private DailyTracker BuildTracker(HistoricalData hd, out string error)
        {
            error = null;
            var t = new DailyTracker(380, halfHourRule: true);
            try
            {
                int n = hd.Count;
                for (int i = 0; i < n; i++)
                {
                    if (hd[i, SeekOriginHistory.Begin] is HistoryItemBar b)
                        t.Add(ToEt(b.TimeLeft), b.High, b.Low);
                }
                return t;
            }
            catch (Exception e)
            {
                error = "reading history: " + e.Message;
                return null;
            }
        }

        private void MaybeProvisional()
        {
            DateTime now = DateTime.UtcNow;
            lock (sync)
            {
                if (now - pComputedUtc < TimeSpan.FromSeconds(30))
                    return;
                pComputedUtc = now;
            }
            RebuildTracker();
            DateTime target = TargetSession(ToEt(now));
            bool stale;
            lock (sync)
                stale = now - vixTriedUtc > TimeSpan.FromMinutes(10);
            if (!VixFresh(target) && stale && !vixFetching)
            {
                vixFetching = true;
                Task.Run(() =>
                {
                    try
                    {
                        FetchVix();
                        ComputeProvisional();
                    }
                    finally
                    {
                        vixFetching = false;
                    }
                });
            }
            ComputeProvisional();
        }

        private void ComputeProvisional()
        {
            DateTime target = TargetSession(ToEt(DateTime.UtcNow));
            lock (sync)
            {
                pSession = target;
                pInfo = ForecastFor(target);
                pComputedUtc = DateTime.UtcNow;
            }
        }

        /// <summary>Latest VIX close dated before the session; the override for today's and later sessions.
        /// Called under the lock.</summary>
        private bool TryVix(DateTime sess, out double v, out string from)
        {
            DateTime todayEt = ToEt(DateTime.UtcNow).Date;
            if (VixOverride > 0 && sess >= todayEt)
            {
                v = VixOverride;
                from = "override";
                return true;
            }
            if (Vix.TryCloseBefore(vixCloses, sess, out v, out DateTime d))
            {
                from = d.ToString("ddd MM-dd", CultureInfo.InvariantCulture);
                return true;
            }
            from = null;
            return false;
        }

        /// <summary>True when the closes were downloaded after 16:30 New York on the weekday before the session,
        /// so the close the session needs is in them.</summary>
        private bool VixFresh(DateTime sess)
        {
            DateTime prev = sess.Date.AddDays(-1);
            while (prev.DayOfWeek == DayOfWeek.Saturday || prev.DayOfWeek == DayOfWeek.Sunday)
                prev = prev.AddDays(-1);
            DateTime fetched;
            lock (sync)
                fetched = vixFetchedUtc;
            return fetched != default && ToEt(fetched) >= prev.AddHours(16.5);
        }

        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };

        /// <summary>Downloads daily VIX closes: Yahoo first, FRED if Yahoo fails. Blocks up to ~16 s; never
        /// called under the lock.</summary>
        private void FetchVix()
        {
            List<(DateTime, double)> list = null;
            string source = null, error = null;
            try
            {
                list = FetchYahoo();
                source = "Yahoo";
            }
            catch (Exception e)
            {
                error = "Yahoo: " + e.Message;
            }
            if (list == null || list.Count == 0)
            {
                try
                {
                    list = FetchFred();
                    source = "FRED";
                }
                catch (Exception e)
                {
                    error = (error == null ? "" : error + "; ") + "FRED: " + e.Message;
                }
            }
            lock (sync)
            {
                vixTriedUtc = DateTime.UtcNow;
                if (list != null && list.Count > 0)
                {
                    vixCloses = list;
                    vixFetchedUtc = DateTime.UtcNow;
                    vixStatus = "from " + source;
                }
                else
                {
                    vixStatus = "download failed (" + (error ?? "no data") + ")";
                }
            }
        }

        private List<(DateTime, double)> FetchYahoo()
        {
            var req = new HttpRequestMessage(HttpMethod.Get,
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=6mo");
            req.Headers.TryAddWithoutValidation("User-Agent", "Mozilla/5.0");      // Yahoo wants a browser UA
            using HttpResponseMessage resp = Http.Send(req);
            resp.EnsureSuccessStatusCode();
            using Stream s = resp.Content.ReadAsStream();
            using JsonDocument doc = JsonDocument.Parse(s);
            JsonElement res = doc.RootElement.GetProperty("chart").GetProperty("result")[0];
            JsonElement ts = res.GetProperty("timestamp");
            JsonElement closes = res.GetProperty("indicators").GetProperty("quote")[0].GetProperty("close");
            var byDate = new SortedDictionary<DateTime, double>();
            for (int i = 0; i < ts.GetArrayLength() && i < closes.GetArrayLength(); i++)
            {
                if (closes[i].ValueKind != JsonValueKind.Number)
                    continue;
                DateTime d = ToEt(DateTimeOffset.FromUnixTimeSeconds(ts[i].GetInt64()).UtcDateTime).Date;
                byDate[d] = closes[i].GetDouble();
            }
            var list = new List<(DateTime, double)>();
            foreach (var kv in byDate)
                list.Add((kv.Key, kv.Value));
            return list;
        }

        private static List<(DateTime, double)> FetchFred()
        {
            string text = Http.GetStringAsync("https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS")
                .GetAwaiter().GetResult();
            var list = new List<(DateTime, double)>();
            foreach (string line in text.Split('\n'))
            {
                string[] f = line.Trim().Split(',');
                if (f.Length < 2 || !DateTime.TryParseExact(f[0], "yyyy-MM-dd", CultureInfo.InvariantCulture,
                        DateTimeStyles.None, out DateTime d))
                    continue;
                if (double.TryParse(f[1], NumberStyles.Float, CultureInfo.InvariantCulture, out double v))
                    list.Add((d, v));                                    // FRED writes "." on holidays
            }
            return list;
        }

        // ─── drawing ─────────────────────────────────────────────────────────────────────────────────
        private static readonly Font TableFont = new Font("Segoe UI", 8.5f);
        private static readonly Font MarkFont = new Font("Segoe UI", 7.5f);
        private static readonly Brush Back = new SolidBrush(Color.FromArgb(225, 22, 24, 30));
        private static readonly Brush LabelBrush = new SolidBrush(Color.FromArgb(150, 155, 165));
        private static readonly Brush ValueBrush = new SolidBrush(Color.FromArgb(232, 234, 238));
        private static readonly Brush WarnBrush = new SolidBrush(Color.FromArgb(0xeb, 0x68, 0x34));
        private static readonly Brush MarkBrush = new SolidBrush(Color.FromArgb(215, 0x2a, 0x78, 0xd6));
        private static readonly Pen Frame = new Pen(Color.FromArgb(70, 120, 125, 135));

        private sealed class Row
        {
            public string[] Cells;      // four cells, or null for a wide row
            public string Wide;
            public Brush Brush;
        }

        public override void OnPaintChart(PaintChartEventArgs args)
        {
            base.OnPaintChart(args);
            if (CurrentChart?.MainWindow == null || ny == null)
                return;
            Graphics g = args.Graphics;
            Rectangle rect = args.Rectangle;
            string chartProblem = ChartProblem();                   // Quantower reads, before taking the lock
            string symbol = Symbol?.Name ?? "";
            List<Row> rows;
            List<TouchMark> marks;
            lock (sync)
            {
                if (walker == null)
                    return;
                rows = ShowTable ? TableRows(chartProblem, symbol) : null;
                marks = new List<TouchMark>(walker.Marks);
            }

            var conv = CurrentChart.MainWindow.CoordinatesConverter;
            foreach (TouchMark mk in marks)
            {
                float x = (float)conv.GetChartX(mk.BarKey);
                float y = (float)conv.GetChartY(mk.Price);
                if (x < rect.Left || x > rect.Right || y < rect.Top || y > rect.Bottom)
                    continue;
                string text = mk.Side == 1 ? "forecast size reached ↑" : mk.Side == -1 ? "forecast size reached ↓" : "forecast size reached";
                SizeF sz = g.MeasureString(text, MarkFont);
                float ty = mk.Side == -1 ? y + 6 : y - sz.Height - 6;
                g.FillRectangle(MarkBrush, x - 3, ty - 1, sz.Width + 6, sz.Height + 2);
                g.DrawString(text, MarkFont, ValueBrush, x, ty);
            }

            if (rows == null || rows.Count == 0)
                return;
            const float pad = 6f, gap = 10f;
            float[] w = new float[4];
            float wideMax = 0f, lineH = 0f;
            foreach (Row r in rows)
            {
                if (r.Cells != null)
                {
                    for (int i = 0; i < 4; i++)
                    {
                        SizeF s = g.MeasureString(r.Cells[i] ?? "", TableFont);
                        w[i] = Math.Max(w[i], s.Width);
                        lineH = Math.Max(lineH, s.Height);
                    }
                }
                else
                {
                    SizeF s = g.MeasureString(r.Wide, TableFont);
                    wideMax = Math.Max(wideMax, s.Width);
                    lineH = Math.Max(lineH, s.Height);
                }
            }
            float tableW = Math.Max(w[0] + w[1] + w[2] + w[3] + 3 * gap, wideMax) + 2 * pad;
            float tableH = rows.Count * lineH + 2 * pad;
            bool right = TablePosition == 0 || TablePosition == 1, bottom = TablePosition == 1 || TablePosition == 3;
            float left = right ? rect.Right - tableW - 70f : rect.Left + 10f;      // clear of the price scale
            float top = bottom ? rect.Bottom - tableH - 30f : rect.Top + 60f;     // clear of the legend lines
            g.FillRectangle(Back, left, top, tableW, tableH);
            g.DrawRectangle(Frame, left, top, tableW, tableH);
            float yy = top + pad;
            foreach (Row r in rows)
            {
                if (r.Cells == null)
                {
                    g.DrawString(r.Wide, TableFont, r.Brush ?? LabelBrush, left + pad, yy);
                }
                else
                {
                    float xx = left + pad;
                    for (int i = 0; i < 4; i++)
                    {
                        g.DrawString(r.Cells[i] ?? "", TableFont, i % 2 == 0 ? LabelBrush : (r.Brush ?? ValueBrush), xx, yy);
                        xx += w[i] + gap;
                    }
                }
                yy += lineH;
            }
        }

        private static Row Cells(string a, string b, string c, string d, Brush brush = null) =>
            new Row { Cells = new[] { a, b, c, d }, Brush = brush };

        private static Row Wide(string s, Brush brush = null) => new Row { Wide = s, Brush = brush };

        private static string N(double x) => double.IsNaN(x) ? "-" : Math.Round(x).ToString("0", CultureInfo.InvariantCulture);

        private static string Pct(double x) => double.IsNaN(x) ? "-" : (x >= 0 ? "+" : "") + Math.Round(x * 100).ToString("0", CultureInfo.InvariantCulture) + "%";

        private static string Fomc(DateTime d) =>
            Calendar.IsFomc(d) ? "YES (about +" + Math.Round(Model.FomcLift * 100).ToString("0", CultureInfo.InvariantCulture) + "%)" : "no";

        /// <summary>Called under the lock; no Quantower calls.</summary>
        private List<Row> TableRows(string chartProblem, string sym)
        {
            var rows = new List<Row>();
            var inv = CultureInfo.InvariantCulture;
            DateTime nowEt = ToEt(DateTime.UtcNow);
            int nm = nowEt.Hour * 60 + nowEt.Minute;
            bool weekday = nowEt.DayOfWeek != DayOfWeek.Saturday && nowEt.DayOfWeek != DayOfWeek.Sunday;
            List<SessionRecord> recs = walker.Records;
            SessionRecord last = recs.Count > 0 ? recs[recs.Count - 1] : null;
            bool today = last != null && last.Session == nowEt.Date && weekday
                && nm >= DailyTracker.RthOpen && nm < DailyTracker.OnStart;

            if (today)
            {
                ForecastInfo f = walker.Info;
                rows.Add(Wide("NQ range forecast  ·  " + last.Session.ToString("ddd MM-dd", inv) + (last.Ended ? "  (session closed)" : ""), ValueBrush));
                if (!f.Ok)
                {
                    rows.Add(Wide("No forecast: " + f.Why, WarnBrush));
                }
                else
                {
                    double rng = last.Ended ? last.Actual : walker.CurHigh - walker.CurLow;
                    rows.Add(Cells("Forecast", N(f.Fc) + " pts", "80% band", N(f.Lo) + " - " + N(f.Hi)));
                    rows.Add(Cells(last.Ended ? "Actual range" : "Range so far", N(rng) + " pts", "of forecast",
                        double.IsNaN(rng) ? "-" : Math.Round(rng / f.Fc * 100).ToString("0", inv) + "%"));
                    rows.Add(Cells("22-day avg", N(f.Inputs.M22) + " pts", "forecast vs avg", Pct(f.Fc / f.Inputs.M22 - 1)));
                    rows.Add(Cells("Overnight", N(f.Inputs.OvernightRange) + " pts", "VIX", f.Vix.ToString("0.0", inv) + " (" + f.VixFrom + ")"));
                    rows.Add(Cells("FOMC day", Fomc(last.Session), "", ""));
                }
            }
            else
            {
                rows.Add(Wide("NQ range forecast  ·  " + pSession.ToString("ddd MM-dd", inv) + "  (provisional)", ValueBrush));
                if (!pInfo.Ok)
                {
                    rows.Add(Wide("No forecast yet: " + pInfo.Why, WarnBrush));
                }
                else
                {
                    rows.Add(Cells("Forecast", N(pInfo.Fc) + " pts", "80% band", N(pInfo.Lo) + " - " + N(pInfo.Hi)));
                    rows.Add(Cells("22-day avg", N(pInfo.Inputs.M22) + " pts", "forecast vs avg", Pct(pInfo.Fc / pInfo.Inputs.M22 - 1)));
                    rows.Add(Cells("Overnight so far", N(pInfo.Inputs.OvernightRange) + " pts", "VIX", pInfo.Vix.ToString("0.0", inv) + " (" + pInfo.VixFrom + ")"));
                    rows.Add(Cells("FOMC day", Fomc(pSession), "through", pInfo.Inputs.OvernightLastBar.ToString("HH:mm", inv) + " ET"));
                }
                rows.Add(Wide("Fixed at 09:30 New York; the levels draw from then."));
            }
            rows.Add(Wide("Levels mark size, not entries: tested 50/50."));
            if (walker.TallyDays > 0)
                rows.Add(Wide("This chart: reached on " + walker.TallyReached + " of " + walker.TallyDays + " days; closed beyond "
                    + walker.TallyBeyond + ", back inside " + (walker.TallyReached - walker.TallyBeyond) + "  (5-yr test: 51 / 49%)"));

            if (chartProblem != null)
                rows.Add(Wide(chartProblem, WarnBrush));
            if (!sym.ToUpperInvariant().Contains("NQ"))
                rows.Add(Wide("Fitted on NQ; untested on " + sym + ".", WarnBrush));
            if (VixOverride > 0)
                rows.Add(Wide("VIX override " + VixOverride.ToString("0.00", inv) + " in use for today and later.", WarnBrush));
            if (historyNote.Length > 0)
                rows.Add(Wide("Data: " + historyNote));

            if (RecentSessions > 0 && recs.Count > 0)
            {
                rows.Add(Cells("Session", "Forecast", "80% band", "Actual", LabelBrush));
                int shown = 0;
                for (int i = recs.Count - 1; i >= 0 && shown < RecentSessions; i--, shown++)
                {
                    SessionRecord r = recs[i];
                    string act = !r.Ended ? N(walker.CurHigh - walker.CurLow) + " live"
                        : !r.Complete ? "half day"
                        : N(r.Actual) + (double.IsNaN(r.Fc) ? "" : r.Actual >= r.Lo && r.Actual <= r.Hi ? "  in" : "  OUT");
                    rows.Add(Cells(r.Session.ToString("ddd MM-dd", inv), N(r.Fc), N(r.Lo) + " - " + N(r.Hi), act));
                }
            }
            return rows;
        }

        private string ChartProblem()
        {
            if (HistoricalData?.Aggregation is HistoryAggregationTime agg)
                return agg.Period.Duration > TimeSpan.FromMinutes(30) ? "Use a chart of 30 minutes or less." : null;
            return "Use a time chart of 1 to 30 minutes: the levels follow time bars.";
        }

        // ─── time ────────────────────────────────────────────────────────────────────────────────────
        /// <summary>The session a forecast is for: today's until 16:00 on a weekday, else the next weekday's
        /// (forecast_today.target_day).</summary>
        private static DateTime TargetSession(DateTime nowEt)
        {
            DateTime d = nowEt.Date;
            bool weekday = d.DayOfWeek != DayOfWeek.Saturday && d.DayOfWeek != DayOfWeek.Sunday;
            if (weekday && nowEt.Hour * 60 + nowEt.Minute < DailyTracker.RthClose)
                return d;
            d = d.AddDays(1);
            while (d.DayOfWeek == DayOfWeek.Saturday || d.DayOfWeek == DayOfWeek.Sunday)
                d = d.AddDays(1);
            return d;
        }

        private DateTime ToEt(DateTime time)
        {
            DateTime utc = time.Kind == DateTimeKind.Utc ? time
                : time.Kind == DateTimeKind.Local ? time.ToUniversalTime()
                : DateTime.SpecifyKind(time, DateTimeKind.Utc);              // Quantower's times are UTC
            return TimeZoneInfo.ConvertTimeFromUtc(utc, ny);
        }

        private static TimeZoneInfo FindZone(string windowsId, string ianaId)
        {
            try
            {
                return TimeZoneInfo.FindSystemTimeZoneById(windowsId);
            }
            catch (TimeZoneNotFoundException)
            {
                return TimeZoneInfo.FindSystemTimeZoneById(ianaId);
            }
        }
    }
}
