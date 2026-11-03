# Paper trading the lab's strategies in MetaTrader 5

`BacktestLab.mq5` is one Expert Advisor (EA) that runs any of five of the lab's strategies on a
**demo account**. The lab found **no strategy that beats simply holding the market**, so use a demo
(practice) account. The EA prints a warning if it is attached to a real-money account.

| Strategy in the EA | Lab name | Suggested MT5 symbol | Lab settings (★) |
|---|---|---|---|
| Internal Bar Strength | MR-IBS | US500 / SPX500 (S&P 500 CFD) | buy IBS < 0.1, sell > 0.8 |
| Williams %R | MR-WR | USTEC / NAS100 (Nasdaq 100 CFD) | 5 days, only above 200-day average |
| Trend filter | TS-TREND | BTCUSD | 150-day average |
| Vol-targeted trend | TS-VOL | US500 | 15% target |
| January barometer | CAL-JAN | USTEC / NAS100 | first 5 days |

The calendar strategy (CAL-MIX, gold) and the agent's ideas are not in the EA. They need NYSE holiday and Fed-meeting calendars, and the lab's feature table. Those keep paper trading in the lab itself.

## Set it up (about 10 minutes)

1. **Get a demo account.** Open MT5, then File → Open an Account. Pick any broker and choose a demo account.
2. **Install the EA.** In MT5: File → Open Data Folder → `MQL5` → `Experts`. Copy `BacktestLab.mq5` there.
3. **Compile it.** Open the file in MetaEditor (press F4 in MT5) and press Compile (F7). It should finish with
   0 errors. I could not compile it here, so if MetaEditor reports an error, paste it back to me.
4. **Attach it.** Open a **daily (D1)** chart of the symbol, then drag *BacktestLab* from the Navigator onto it.
   Pick the strategy and settings in the Inputs tab, and switch on **Algo Trading** in the toolbar.
   Use one chart per strategy, each with its own *Magic number*.
5. **Leave MT5 running.** The EA acts once a day, on the first price of each new daily bar. If the terminal is
   closed, it does nothing that day.

Every decision is written to `MQL5\Files\backtestlab_<symbol>_<strategy>.csv`, with the reason (for example `IBS 0.07`).
Send that file back to compare against the lab's own paper log.

## Backtest it in MT5 too

View → Strategy Tester. Choose *BacktestLab*, the symbol, timeframe **D1**, and modelling **"Open prices only"**.
The EA only acts on new daily bars, so open prices only is exact and fast. Then choose a date range. This
is a genuinely new test, because the broker's data differs from what the lab used.

## Why MT5 results will differ from the lab

- **Different bars.** A broker's daily bar follows its server clock (often GMT+2) and includes overnight CFD
  trading, so IBS and %R values will not match the SPY/QQQ ETF bars the lab used.
- **Overnight financing.** CFD brokers charge a *swap* for every night a position stays open. The lab's ETF
  backtests had no such cost, so trend strategies that hold for weeks will look worse in MT5.
- **Spreads and lot sizes.** Costs are the broker's spread, not the lab's 3–10 bps. Position size is rounded to
  the broker's lot step (`Share of equity at a full position`, default 1.0 = no leverage).
- **No tuning in MT5.** Leave the ★ settings. Optimising them in the Strategy Tester is the data-mining the
  lab warns about.

---

# Alternative: Alpaca paper account (viewable in TradingView)

`src/alpaca_paper.py` mirrors the lab's daily paper decisions into an **Alpaca paper account**. The
daily cycle (`src/daily.py`) runs it automatically when `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are set.

- Each Strategy Lab strategy gets an equal share of the account. Shares on the same symbol are added together.
- Stock orders are market-on-open (fill at the next open, like the backtest). Bitcoin orders go straight in.
- `paper=True` is hard-coded, so it cannot reach a live account.
- `python src/alpaca_paper.py --dry-run` shows the orders it would place, without keys.
- Every order is logged to `agent_memory/alpaca_orders.csv`.

**To watch it in TradingView:** open TradingView's Trading Panel, choose Alpaca, sign in, and pick the paper account.
