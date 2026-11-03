//+------------------------------------------------------------------+
//| BacktestLab.mq5                                                  |
//| Daily, long-only strategies from the backtest lab, for MT5       |
//| demo-account paper trading and the MT5 Strategy Tester.          |
//|                                                                  |
//| Same timing as the lab's engine: the decision uses only the last |
//| COMPLETED daily bar (shift 1) and is executed on the first tick  |
//| of the new daily bar, i.e. at (about) the next open.             |
//+------------------------------------------------------------------+
#property copyright   "backtest-lab"
#property version     "1.00"
#property description "IBS, Williams %R, trend filter, vol-targeted trend and January barometer (long-only, daily)."

#include <Trade\Trade.mqh>

enum ENUM_LAB_STRATEGY
  {
   LAB_IBS = 0,            // Internal Bar Strength dip buy
   LAB_WILLIAMS_R = 1,     // Williams %R dip buy
   LAB_TREND = 2,          // Trend filter: close above its average
   LAB_VOL_TREND = 3,      // Vol-targeted trend
   LAB_JAN_BAROMETER = 4   // January barometer
  };

input ENUM_LAB_STRATEGY InpStrategy       = LAB_IBS;  // Strategy
input double            InpEquityFraction = 1.0;      // Share of equity at a full position (1.0 = no leverage)
input double            InpIbsBuy         = 0.1;      // IBS: buy when IBS is below
input double            InpIbsSell        = 0.8;      // IBS: sell when IBS is above
input int               InpWrDays         = 5;        // Williams %R: look-back days
input bool              InpWrTrendOnly    = true;     // Williams %R: only buy above the 200-day average
input int               InpTrendDays      = 150;      // Trend filter: length of the average (days)
input double            InpVolTarget      = 0.15;     // Vol-targeted trend: yearly volatility target
input bool              InpJanFirst5      = true;     // January barometer: judge on the first 5 days (false = whole January)
input long              InpMagic          = 20261103; // Magic number (keeps this EA's trades apart)
input bool              InpWriteLog       = true;     // Write each daily decision to MQL5\Files

CTrade   trade;
datetime g_lastBar = 0;

//--- price helpers on the daily chart (shift 1 = last completed day)
double C(const int s) { return iClose(_Symbol, PERIOD_D1, s); }
double H(const int s) { return iHigh(_Symbol, PERIOD_D1, s); }
double L(const int s) { return iLow(_Symbol, PERIOD_D1, s); }

bool EnoughBars(const int need) { return Bars(_Symbol, PERIOD_D1) > need + 2; }

double Sma(const int n, const int shift)
  {
   double sum = 0.0;
   for(int i = shift; i < shift + n; i++)
     {
      double c = C(i);
      if(c <= 0.0) return 0.0;
      sum += c;
     }
   return sum / n;
  }

//--- 20-day realised volatility of daily returns, annualised over 252 days (sample std, like the lab)
double RealisedVol(const int n, const int shift)
  {
   double r[];
   ArrayResize(r, n);
   double mean = 0.0;
   for(int i = 0; i < n; i++)
     {
      double a = C(shift + i), b = C(shift + i + 1);
      if(a <= 0.0 || b <= 0.0) return 0.0;
      r[i] = a / b - 1.0;
      mean += r[i];
     }
   mean /= n;
   double ss = 0.0;
   for(int i = 0; i < n; i++) ss += (r[i] - mean) * (r[i] - mean);
   return MathSqrt(ss / (n - 1)) * MathSqrt(252.0);
  }

//--- this EA's open long volume on this symbol (works on netting and hedging accounts)
double CurrentVolume()
  {
   double v = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) v += PositionGetDouble(POSITION_VOLUME);
     }
   return v;
  }

void CloseAll()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      trade.PositionClose(ticket);
     }
  }

//--- volume worth `fraction` of equity at the current price, rounded down to the volume step
double VolumeFor(const double fraction)
  {
   double price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(price <= 0.0 || tickValue <= 0.0 || tickSize <= 0.0 || step <= 0.0) return 0.0;
   double perLot = price * tickValue / tickSize;          // account-currency value of 1.0 lot
   double vol = MathFloor(AccountInfoDouble(ACCOUNT_EQUITY) * fraction / perLot / step) * step;
   if(vol < vmin) vol = (fraction > 0.0 ? vmin : 0.0);
   if(vol > vmax) vol = vmax;
   return NormalizeDouble(vol, 8);
  }

//--- January barometer: target for the session that is starting now (bar 0)
double JanuaryTarget(string &why)
  {
   MqlDateTime now;
   TimeToStruct(iTime(_Symbol, PERIOD_D1, 0), now);
   if(now.mon == 1) { why = "January: always invested"; return 1.0; }
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int got = CopyRates(_Symbol, PERIOD_D1, 1, 300, rates);   // completed bars only, newest first
   if(got < 30) { why = "not enough history"; return 0.0; }
   double lastDec = 0.0, ref = 0.0;
   int janCount = 0;
   // walk from oldest to newest so the first January bars come first
   for(int i = got - 1; i >= 0; i--)
     {
      MqlDateTime d;
      TimeToStruct(rates[i].time, d);
      if(d.year == now.year - 1 && d.mon == 12) lastDec = rates[i].close;
      if(d.year == now.year && d.mon == 1)
        {
         janCount++;
         if(InpJanFirst5 && janCount == 5) ref = rates[i].close;
         if(!InpJanFirst5) ref = rates[i].close;               // ends on the last January bar
        }
     }
   if(lastDec <= 0.0 || ref <= 0.0) { why = "January data missing"; return 0.0; }
   double ret = ref / lastDec - 1.0;
   why = StringFormat("%s return %.2f%%", (InpJanFirst5 ? "first-5-day" : "January"), ret * 100.0);
   return (ret > 0.0 ? 1.0 : 0.0);
  }

//--- decide the target (0 = cash, 1 = full position, or a fraction for vol targeting)
double Target(const bool holding, string &why)
  {
   switch(InpStrategy)
     {
      case LAB_IBS:
        {
         double rng = H(1) - L(1);
         double ibs = (rng > 0.0 ? (C(1) - L(1)) / rng : 0.5);
         why = StringFormat("IBS %.2f", ibs);
         if(holding) return (ibs > InpIbsSell ? 0.0 : 1.0);
         return (ibs < InpIbsBuy ? 1.0 : 0.0);
        }
      case LAB_WILLIAMS_R:
        {
         if(!EnoughBars(MathMax(InpWrDays, 200))) { why = "not enough history"; return holding ? 1.0 : 0.0; }
         double hh = H(1), ll = L(1);
         for(int i = 2; i <= InpWrDays; i++) { hh = MathMax(hh, H(i)); ll = MathMin(ll, L(i)); }
         double wr = (hh > ll ? -100.0 * (hh - C(1)) / (hh - ll) : -50.0);
         bool trendOk = !InpWrTrendOnly || C(1) > Sma(200, 1);
         why = StringFormat("%%R %.1f, above 200d avg %s", wr, (C(1) > Sma(200, 1) ? "yes" : "no"));
         if(holding) return (wr > -20.0 ? 0.0 : 1.0);
         return (wr < -90.0 && trendOk ? 1.0 : 0.0);
        }
      case LAB_TREND:
        {
         if(!EnoughBars(InpTrendDays)) { why = "not enough history"; return 0.0; }
         double avg = Sma(InpTrendDays, 1);
         why = StringFormat("close %.2f vs %d-day avg %.2f", C(1), InpTrendDays, avg);
         return (C(1) > avg ? 1.0 : 0.0);
        }
      case LAB_VOL_TREND:
        {
         if(!EnoughBars(200)) { why = "not enough history"; return 0.0; }
         double avg = Sma(200, 1), rv = RealisedVol(20, 1);
         double w = (rv > 0.0 ? MathMin(1.0, InpVolTarget / rv) : 0.0);
         why = StringFormat("above 200d avg %s, 20d vol %.1f%%, weight %.2f", (C(1) > avg ? "yes" : "no"), rv * 100.0, w);
         return (C(1) > avg ? w : 0.0);
        }
      case LAB_JAN_BAROMETER:
         return JanuaryTarget(why);
     }
   why = "unknown strategy";
   return 0.0;
  }

void LogDecision(const double target, const double vol, const string why)
  {
   if(!InpWriteLog) return;
   string name = StringFormat("backtestlab_%s_%d.csv", _Symbol, (int)InpStrategy);
   int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ, ',');
   if(h == INVALID_HANDLE) return;
   if(FileSize(h) == 0) FileWrite(h, "decided_at", "completed_bar", "strategy", "target", "volume", "close", "reason");
   FileSeek(h, 0, SEEK_END);
   FileWrite(h, TimeToString(TimeCurrent()), TimeToString(iTime(_Symbol, PERIOD_D1, 1), TIME_DATE),
             EnumToString(InpStrategy), DoubleToString(target, 3), DoubleToString(vol, 2), DoubleToString(C(1), 5), why);
   FileClose(h);
  }

int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
      Print("WARNING: this is a REAL account. The backtest lab found no strategy that beats just holding. Use a demo account.");
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   datetime bar = iTime(_Symbol, PERIOD_D1, 0);
   if(bar == 0 || bar == g_lastBar) return;           // act once, on the first tick of each new day
   g_lastBar = bar;

   double have = CurrentVolume();
   string why = "";
   double target = Target(have > 0.0, why);
   double want = (target > 0.0 ? VolumeFor(target * InpEquityFraction) : 0.0);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(want <= 0.0 && have > 0.0)
      CloseAll();
   else if(want > 0.0 && have <= 0.0)
      trade.Buy(want, _Symbol, 0.0, 0.0, 0.0, "BacktestLab entry");
   else if(want > 0.0 && InpStrategy == LAB_VOL_TREND && MathAbs(want - have) >= step && MathAbs(want - have) / want > 0.10)
     {
      // resize only for the vol-targeted strategy, and only when the change is above 10%
      if(want > have) trade.Buy(NormalizeDouble(want - have, 8), _Symbol, 0.0, 0.0, 0.0, "BacktestLab resize");
      else { CloseAll(); trade.Buy(want, _Symbol, 0.0, 0.0, 0.0, "BacktestLab resize"); }
     }
   PrintFormat("BacktestLab %s %s: %s -> target %.2f (holding %.2f lots, want %.2f)",
               _Symbol, EnumToString(InpStrategy), why, target, have, want);
   LogDecision(target, want, why);
  }
//+------------------------------------------------------------------+
