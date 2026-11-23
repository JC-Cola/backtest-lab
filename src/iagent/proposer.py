"""Intraday proposers: the free evolution proposer and the Claude one (via the local Claude Code CLI)."""
from __future__ import annotations

from agent.proposer import ClaudeProposer, EvolutionProposer, WebResearchProposer
from iagent import spec as S
from iagent.features import DECISIONS

MEANING = {
    "gap": "today's open vs yesterday's close", "ret_open": "return since today's open",
    "ret_prev_close": "return since yesterday's close", "ret_30": "return over the last 30 minutes",
    "vwap_dist": "distance of the price from today's volume-weighted average price",
    "range_pos": "where the price sits in today's range so far (0 = low, 1 = high)",
    "noise_z": "return since the open divided by its typical size over the last 14 days",
    "vol_ratio": "volume so far today vs the 14-day average at this time",
    # day-trader scanner setup families (all use only bars before the decision time and daily closes before today)
    "orb5": "opening-range breakout: how far the price is beyond the first-5-minute range (> 0 above its high, < 0 below its low, 0 inside)",
    "orb15": "opening-range breakout on the first 15 minutes (same convention; the scanner's default range)",
    "orb30": "opening-range breakout on the first 30 minutes (same convention)",
    "run_10d": "bull-flag pole: return over the 10 sessions before the last 5 sessions",
    "range_5d": "bull-flag tightness: high-low spread of the last 5 daily closes relative to the last close (small = consolidating)",
    "consol_30": "intraday consolidation: high-low range of the last 30 minutes relative to the price (small = tight)",
    "hi20_dist": "price vs the highest daily close of the last 20 sessions (> 0 = a new 20-day closing high, the scanner's breakout_20d)",
    "rsi_14d": "Wilder RSI(14) of the daily closes up to yesterday (< 30 = oversold, the scanner's oversold / oversold bounce)",
    "z_20d": "price vs the 20-day mean of daily closes, in standard deviations (negative = oversold)",
    "gap_fill": "share of today's opening gap already filled (1 = back at yesterday's close; needs a gap over 0.1%)",
    "rvol_30": "relative-volume spike: volume of the last 30 minutes vs the average of the same 30 minutes over the last 14 days",
    "prev_day_ret": "yesterday's return", "prev_day_ibs": "where yesterday closed in its range (0 = low, 1 = high)",
    "rv_5d": "volatility of daily returns over the last 5 days",
}


class IEvolutionProposer(EvolutionProposer):
    S = S


class IClaudeProposer(ClaudeProposer):
    S = S

    def _prompt(self, k, markets):
        lessons, rec = self._lessons_and_recent()
        times = ", ".join(f"{S.clock(m)} (m={m})" for m in DECISIONS)
        feats = "\n".join(f"- {f}: {d}" for f, d in MEANING.items())
        return f"""You are the research agent of a trading backtest lab. Propose {k} NEW intraday strategy specs
for these markets: {markets} (SPY and QQQ on minute bars; the other names are the most-traded liquid US stocks/ETFs on 5-minute
bars; you may only use the markets listed, the lab keeps other names untouched for confirmation). Each spec trades ONE name and
makes at most one trade a day; there are no cross-sectional specs.
Idea families (from the day-trader scanner): opening-range breakout (orb5/orb15/orb30 > p90 long, or < p10 short), bull flag /
consolidation breakout (run_10d high with range_5d or consol_30 small, then hi20_dist or an orb feature turning positive), oversold
bounce (rsi_14d or z_20d low, then ret_30 / ret_open turning up), gap-and-go (gap high with ret_open high) versus gap fade (gap
high, gap_fill rising, short), relative-volume spike (rvol_30 or vol_ratio high with a direction feature). Thresholds are per name,
so one rule may behave differently on different names: prefer ideas with a reason that should hold across stocks.
Each spec is scored as excess return over random timing, in 3 time folds of the search years, deflated by
every trial ever run on every name, and ranked by robustness (all 3 folds positive, also at 2x costs, with at least 150 trades), so
only propose ideas you have a real reason to expect to be robust.
Do not repeat anything already tried.

Grammar (return each spec as a JSON string; "time" is minutes after the 09:30 open):
{S.__doc__}
Decision times: {times}.
Risk options: "stop_bp" (basis points from the entry price) can be null, 10, 25, 50, 100 or 200. "target_r" is an
optional take-profit placed at target_r x the stop distance (1.5, 2, 3 or 4, e.g. stop_bp 50 with target_r 3 targets
+150 bp); it needs a stop_bp, and a bar that touches both the stop and the target is treated as a stop-out.
Omit "target_r" (or use null) for no target. Hold exits take n of 5, 15, 30, 60, 120 or 240 minutes.
Features (all known at the decision time):
{feats}
"q" is a percentile 1-99 of that feature at that decision time over 2016-2020.

What the lab has learned so far:
{lessons}

Most recent trials:
{rec}
"""


class IWebResearchProposer(WebResearchProposer, IClaudeProposer):
    FOCUS = ("intraday rules on SPY, QQQ and the most-traded liquid US stocks (at most one trade a day per name, decided at a fixed time "
             "after the open): opening-range breakouts, flags, oversold bounces, gap-and-go/fade, relative-volume spikes")
