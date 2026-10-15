"""Strategy registry: id, metadata, function and parameter grid."""
from __future__ import annotations

import itertools

import strategies_meanrev as mr
import strategies_trend as tr


def grid(filt=None, **axes):
    keys = list(axes)
    out = [dict(zip(keys, v)) for v in itertools.product(*axes.values())]
    return [p for p in out if filt is None or filt(p)]


_MA = grid(fast=[10, 20, 50], slow=[100, 150, 200])


def _s(id_, name, family, source, desc, fn, g):
    return {"id": id_, "name": name, "family": family, "source": source,
            "description": desc, "fn": fn, "grid": g}


STRATEGIES = [
    _s("buy_hold", "Buy & Hold", "benchmark", "Benchmark", "Always fully long.", tr.buy_hold, [{}]),
    _s("sma_cross", "SMA Crossover", "trend", "Golden/death cross (classic technical analysis)",
       "Long when SMA(fast) > SMA(slow), else flat.", tr.sma_cross, _MA),
    _s("trend_filter", "Trend Filter (close > SMA)", "trend", "Faber (2007) tactical asset allocation",
       "Long when close is above its n-day SMA.", tr.trend_filter, grid(n=[50, 100, 150, 200, 250])),
    _s("tsmom", "Time-Series Momentum", "trend", "Moskowitz, Ooi, Pedersen (2012)",
       "Sign of the L-day return skipping the last 21 days; long/flat or long/short.", tr.tsmom,
       grid(lookback=[63, 126, 252], mode=["long_flat", "long_short"])),
    _s("donchian", "Donchian Breakout (Turtle)", "breakout", "Turtle Traders (Dennis/Eckhardt)",
       "Enter on close above prior N-day high, exit on close below prior M-day low.", tr.donchian,
       grid(lambda p: p["m"] < p["n"], n=[20, 55, 100], m=[10, 20, 50])),
    _s("macd", "MACD Signal Cross", "trend", "Gerald Appel (1979)",
       "Long when the MACD line is above its signal line.", tr.macd,
       [{"fast": f, "slow": s, "signal": g}
                                       for f, s, g in [(12, 26, 9), (8, 21, 5), (19, 39, 9)]]),
    _s("rsi2_connors", "RSI(2) Pullback", "mean_reversion", "Connors & Alvarez, Short Term Trading Strategies That Work",
       "Long when RSI(2) < x and close > SMA200; exit when close > SMA5.", mr.rsi2_connors, grid(x=[5, 10, 15])),
    _s("rsi14_reel", "RSI(14) Oversold (Reel Rule)", "mean_reversion", "Instagram reel rule",
       "Enter RSI(14)<30 (optionally with close>SMA200); exit RSI(14)>60 or stop loss on close.", mr.rsi14_reel,
       grid(stop=[0.05, 0.10], sma_filter=[True, False])),
    _s("bollinger_revert", "Bollinger Band Reversion", "mean_reversion", "John Bollinger bands",
       "Long when close < SMA20 - k*std; exit when close > SMA20.", mr.bollinger_revert, grid(k=[1.5, 2.0, 2.5])),
    _s("ibs", "Internal Bar Strength", "mean_reversion", "IBS reversal (Connors/Alvarez era; popular quant blogs)",
       "Long when (close-low)/(high-low) < a; exit when > b.", mr.ibs, grid(a=[0.1, 0.2, 0.3], b=[0.5, 0.7, 0.8])),
    _s("turn_of_month", "Turn of the Month", "seasonal", "Ariel (1987); Lakonishok & Smidt (1988)",
       "Long the last N and first M trading days of each month.", mr.turn_of_month,
       grid(n=[1, 2, 3, 4], m=[1, 2, 3, 4])),
    _s("vol_target_trend", "Vol-Targeted Trend", "trend", "Moreira & Muir (2017) volatility-managed portfolios",
       "SMA200 trend filter scaled by target vol / 20d realized vol, capped at 1.", tr.vol_target_trend,
       grid(target=[0.10, 0.15, 0.20])),
    _s("darvas_box", "Darvas Box Breakout", "breakout", "Nicolas Darvas, How I Made $2,000,000 in the Stock Market",
       "Enter on close above a confirmed box top, exit on close below the box bottom.", tr.darvas_box,
       grid(hold=[3, 4, 5])),
    _s("nday_low_dip", "N-Day Low Dip Buy", "mean_reversion", "Connors-style short-term pullback",
       "Long when close is the N-day closing low and above SMA200; exit after H days or close > SMA5.",
       mr.nday_low_dip, grid(n=[5, 10], h=[3, 5, 10])),
    _s("dual_ma_long_short", "Dual MA Long/Short", "trend", "Classic MA crossover, short side",
       "sma_cross that goes short (-1) instead of flat.", tr.dual_ma_long_short, _MA),
]
