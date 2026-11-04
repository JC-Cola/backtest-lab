"""Intraday proposers: the free evolution proposer and the Claude one (via the local Claude Code CLI)."""
from __future__ import annotations

from agent.proposer import ClaudeProposer, EvolutionProposer
from iagent import spec as S
from iagent.features import DECISIONS

MEANING = {
    "gap": "today's open vs yesterday's close", "ret_open": "return since today's open",
    "ret_prev_close": "return since yesterday's close", "ret_30": "return over the last 30 minutes",
    "vwap_dist": "distance of the price from today's volume-weighted average price",
    "range_pos": "where the price sits in today's range so far (0 = low, 1 = high)",
    "noise_z": "return since the open divided by its typical size over the last 14 days",
    "vol_ratio": "volume so far today vs the 14-day average at this time",
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
for these markets: {markets} (SPY and QQQ, minute bars). Each spec makes at most one trade a day.
Each spec is scored as excess return over random timing, in 3 time folds of the search years, deflated by
every trial ever run, so only propose ideas you have a real reason to expect to be robust.
Do not repeat anything already tried.

Grammar (return each spec as a JSON string; "time" is minutes after the 09:30 open):
{S.__doc__}
Decision times: {times}.
Features (all known at the decision time):
{feats}
"q" is a percentile 1-99 of that feature at that decision time over 2016-2020.

What the lab has learned so far:
{lessons}

Most recent trials:
{rec}
"""
