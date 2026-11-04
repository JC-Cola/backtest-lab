"""Builds report/lab.html (the Strategy Lab dashboard) from the engine, results.json,
confirmation.json and the agent's memory. Every number on the page is net of costs."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

import data
from agent import platform as AP
from agent import spec as AS
from agent.memory import Memory
from engine import extract_trades, returns_np
from run import CONFIG
from strategies import STRATEGIES, positions

ROOT = Path(__file__).resolve().parent.parent
BY_ID = {s["id"]: s for s in STRATEGIES}

# The book: (code, strategy id, market) plus the plain-language panels for each.
BOOK = [
    {"code": "MR-IBS", "id": "ibs", "market": "SPY",
     "signal": ["ibs_t = (C_t - L_t) / (H_t - L_t)      // where the close sits in the day's range",
                "", "ENTRY  ibs_t < a                     // closed near the low",
                "EXIT   ibs_t > b                     // closed near the high",
                "TRADE  decide at close t, fill at open t+1"],
     "edge": "Index ETFs that close near the day's low tend to bounce the next day. Short-term liquidity "
             "providers are paid to absorb end-of-day selling.",
     "kills": ["Earns less than just holding: in the market only about a third of the time.",
               "Since it was published (2014) it only works on big US index ETFs, not abroad.",
               "Each round trip pays about 6 bps in costs, a real share of a ~40 bp average edge."]},
    {"code": "MR-WR", "id": "williams_r", "market": "QQQ",
     "signal": ["wr_t = -100 * (HH_n - C_t) / (HH_n - LL_n)   // Williams %R over n days",
                "", "ENTRY  wr_t < -90  [and C_t > SMA200]", "EXIT   wr_t > -20",
                "TRADE  decide at close t, fill at open t+1"],
     "edge": "Same idea as IBS with a longer lookback: buy short, sharp dips in a market that drifts up.",
     "kills": ["Did not hold up on fresh data: only 4 of 13 untouched markets were convincing.",
               "Overlaps heavily with IBS, so it is not a second, independent edge.",
               "Sits out the strongest up-trends, so it trails buy-and-hold in bull markets."]},
    {"code": "CAL-JAN", "id": "qp_january_barometer", "market": "QQQ",
     "signal": ["jan5 = C(5th session of Jan) / C(last session of Dec) - 1",
                "", "HOLD   every January", "HOLD   Feb-Dec only if jan5 > 0 (rule 'first5')",
                "       or full-January return > 0 (rule 'january')"],
     "edge": "Folklore from the Stock Trader's Almanac: a weak start to the year has preceded several bear markets.",
     "kills": ["One decision a year. Most of the edge is 2000, 2001 and 2008, about three lucky calls.",
               "Januaries move together worldwide, so more markets do not add independent evidence.",
               "Often sits out rising years (1991, 1995, 2016, 2024 in the fresh-data test)."]},
    {"code": "CAL-MIX", "id": "calendar_composite", "market": "GLD",
     "signal": ["tom   = last session + first 3 sessions of the month", "hol   = session before an NYSE holiday",
                "fomc  = day before + day of a scheduled Fed statement", "",
                "HOLD   any chosen parts, optionally only Nov-Apr"],
     "edge": "Cash flows cluster around month ends, holidays and Fed days, and some of that pressure shows up in prices.",
     "kills": ["Best single result in the whole search (p = 0.0015) but it failed on the lockbox markets.",
               "Gold has no obvious reason to follow equity cash-flow calendars.",
               "In the market only a few days a month: small, lumpy returns."]},
    {"code": "TS-TREND", "id": "trend_filter", "market": "BTC-USD",
     "signal": ["ENTRY  C_t > SMA(n)", "EXIT   C_t < SMA(n)", "",
                "TRADE  decide at close t, fill at open t+1", "COST   10 bps per side (crypto)"],
     "edge": "Trends in crypto persist because new money arrives in waves and leverage forces late buyers and sellers.",
     "kills": ["Bitcoin is in the test only because it survived. That is hindsight.",
               "The ETH lockbox moves with BTC, so it is weak confirmation.",
               "Whipsaws in sideways years cost 10 bps each way."]},
    {"code": "TS-VOL", "id": "vol_target_trend", "market": "SPY",
     "signal": ["rv_t  = stdev(daily returns, 20d) * sqrt(252)", "w_t   = min(1, target / rv_t)", "",
                "HOLD   w_t of the portfolio while C_t > SMA200", "FLAT   otherwise"],
     "edge": "Volatility clusters, so cutting size when it rises should lower drawdowns for a similar return.",
     "kills": ["No better than random timing on the test years (p = 0.77).",
               "Halves exposure in the sharp rebounds that follow volatility spikes.",
               "Lower risk, but much lower return than just holding SPY."]},
]

ABOUT = {
    "MR-IBS": ("Mean reversion", "Daily, 1-3 day holds", "IBS reversal (Connors/Alvarez; Pagonidis 2014)"),
    "MR-WR": ("Mean reversion", "Daily, 1-5 day holds", "Larry Williams; TradingView built-in"),
    "CAL-JAN": ("Calendar", "Yearly decision", "Quantpedia: January barometer"),
    "CAL-MIX": ("Calendar", "A few days a month", "Ariel 1987/1990; Lucca & Moench 2015"),
    "TS-TREND": ("Trend", "Daily, weeks-long holds", "Faber (2007) trend filter"),
    "TS-VOL": ("Trend", "Daily", "Moreira & Muir (2017)"),
    "AGENT": ("Found by the agent", "Daily", "Self-improving agent, best idea so far"),
}


PLAIN = {'MR-IBS': ("Buys the S&P 500 fund (SPY) on days it closes near the bottom of that day's price range, and sells once it closes near the top.", {'a': ('Buy when it closes in the…', 'Lower waits for deeper dips, so it trades less often.', {0.1: "bottom 10% of the day's range", 0.2: "bottom 20% of the day's range", 0.3: "bottom 30% of the day's range"}), 'b': ('Sell when it closes above…', 'Higher holds on longer, waiting for a stronger bounce.', {0.5: "the middle of the day's range", 0.7: "70% of the day's range", 0.8: "80% of the day's range"})}), 'MR-WR': ('Buys the Nasdaq 100 fund (QQQ) right after a short, sharp drop, and sells once it bounces back.', {'n': ('How far back to look', 'Shorter reacts to smaller, quicker dips.', {2: 'last 2 days', 5: 'last 5 days', 10: 'last 10 days'}), 'trend': ('Only buy in an uptrend?', 'Yes skips dips that happen during a falling market.', {True: 'yes, only when above its 200-day average', False: 'no, buy any dip'})}), 'CAL-JAN': ('Always holds the Nasdaq 100 fund (QQQ) in January, then stays in for the rest of the year only if January started well.', {'rule': ('What counts as a good January', 'Decides whether to stay invested from February to December.', {'first5': 'the first 5 trading days were up', 'january': 'the whole month was up'})}), 'CAL-MIX': ('Holds gold (GLD) only on a few calendar days: around month-ends, before holidays and around Federal Reserve meetings. Otherwise it sits in cash.', {'parts': ('Which days to hold', 'More kinds of days means more time in the market.', {('tom', 'fomc'): 'month-ends and Fed days', ('tom', 'holiday'): 'month-ends and pre-holiday days', ('holiday', 'fomc'): 'pre-holiday days and Fed days', ('tom', 'holiday', 'fomc'): 'all three'}), 'halloween_gate': ('Only November to April?', 'Yes skips the summer months, which history says are weaker.', {True: 'yes, skip May to October', False: 'no, all year'})}), 'TS-TREND': ('Holds Bitcoin while its price is above its average price of recent months, and sells when it drops below.', {'n': ('Length of the average', 'Longer reacts more slowly but gives fewer false alarms.', {50: '50 days', 100: '100 days', 150: '150 days', 200: '200 days', 250: '250 days'})}), 'TS-VOL': ('Holds the S&P 500 fund (SPY) while it is in an uptrend, and holds less of it when the market gets jumpy.', {'target': ('How much risk to allow', 'Higher means it holds more SPY most of the time.', {0.1: 'low (10% swings a year)', 0.15: 'medium (15% a year)', 0.2: 'high (20% a year)'})})}


def _plain_params(code, combos):
    """Plain-language label, help and value names for each setting, keyed like the page's axes."""
    if code not in PLAIN:
        return {}
    out = {}
    for k, (label, help_, vals) in PLAIN[code][1].items():
        out[k] = {"label": label, "help": help_,
                  "values": {_js(list(v) if isinstance(v, tuple) else v): t for v, t in vals.items()}}
    return out


# Daily strategies kept in the lab: the ones behind the three open Alpaca positions (SPY, QQQ, BTC).
LAB_DAILY = {"MR-IBS", "TS-VOL", "MR-WR", "CAL-JAN", "TS-TREND"}
SHOW_AGENT = False

INTRA = {
    "id_overnight": ("ID-NIGHT", "Buys the fund at the close and sells at the next morning's open, so it only owns the night.",
        ["ENTRY  buy at the session close", "EXIT   sell at the next session's open", "COST   1 bp per side"],
        "Studies find most of the stock market's long-run gain arrived overnight, while it was closed.",
        ["Pays costs every single day: 2 bp a night adds up to about 5% a year.", "Holds through weekends, which FTMO's Standard account forbids on the funded stage.", "The overnight edge has shrunk in recent years."], {}),
    "id_intraday_only": ("ID-DAY", "Buys the fund at the open and sells at the close, every trading day.",
        ["ENTRY  buy at the session open", "EXIT   sell at the session close", "COST   1 bp per side"],
        "The contrast to the overnight hold: it shows how little of the market's gain comes during trading hours.",
        ["Historically earns close to nothing before costs.", "Pays costs every day.", "Mainly a benchmark, not a strategy."], {}),
    "id_intraday_momentum": ("ID-MOM", "Looks at the move from yesterday's close to 10:00 (or 10:30). If it was up, buys for the last part of the day; if down, sells short.",
        ["r     = price(10:00 or 10:30) / yesterday's close - 1", "ENTRY  last 30 or 60 minutes: long if r > 0, short if r < 0", "EXIT   at the close"],
        "Gao, Han, Li & Zhou (2018) found the first half hour predicts the last half hour, as late traders and hedgers repeat the morning's direction.",
        ["The effect was found on 1993-2013 data and has weakened since publication.", "Only about one trade a day, so costs are a large share of a small edge.", "Works mainly on big-news days; most days it is noise."],
        {"sig_min": ("Signal measured until", "Later gives a better read on the day but leaves less time to trade.", {30: "10:00 (first 30 minutes)", 60: "10:30 (first hour)"}),
         "hold": ("Trade the last", "Longer holds catch more of the move but more noise too.", {30: "30 minutes", 60: "60 minutes"})}),
    "id_orb": ("ID-ORB", "Waits for the first 5 to 30 minutes of the day, then trades in that range's direction, with a stop at the other end of the range.",
        ["range  = first N minutes (high, low, direction)", "ENTRY  next bar, in the range's direction", "STOP   other end of the range", "EXIT   10x the risk (10R) or the close"],
        "Zarattini, Barbon & Aziz (2023): strong opens often keep going, and a tight stop keeps losers small.",
        ["Wins only about 1 trade in 4, so long losing streaks are normal.", "An independent replication found the profit vanishes after costs.", "The paper had no out-of-sample test."],
        {"or_min": ("Opening range length", "Shorter reacts faster but is noisier.", {5: "first 5 minutes", 15: "first 15 minutes", 30: "first 30 minutes"}),
         "target": ("Take profit", "10R takes big wins early; none holds to the close.", {10.0: "at 10x the risk (10R)", 0.0: "none, hold to the close"})}),
    "id_vwap_trend": ("ID-VWAP", "Long while the price is above the day's volume-weighted average price (VWAP), short while below, flipping every time it crosses.",
        ["vwap   = cumulative (price x volume) / cumulative volume, from 09:30", "SIDE   long if close > vwap, short if close < vwap", "CHECK  every 1, 5 or 15 minutes; flat at the close"],
        "Zarattini & Aziz (2023): big institutions benchmark against VWAP, so moves away from it can persist.",
        ["Flips many times a day, so costs pile up fast.", "Choppy days are death by a thousand small losses.", "The paper's results used leveraged QQQ and no slippage."],
        {"bar": ("Check the price every", "Less often means fewer flips and lower costs.", {1: "1 minute", 5: "5 minutes", 15: "15 minutes"})}),
    "id_noise_breakout": ("ID-NOISE", "Only trades when the price leaves its usual daily range, measured from the last 10 or 14 days, and is on the right side of VWAP.",
        ["band   = open x (1 +/- average move at this time over the last N days)", "LONG   if price > max(upper band, vwap)", "SHORT  if price < min(lower band, vwap)", "CHECK  every 30 or 60 minutes; flat at the close"],
        "Zarattini, Aziz & Barbon (2024): moves beyond the normal noise of the day signal real buying or selling pressure.",
        ["Many days it never trades; when it does, it can be stopped out at the next check.", "The paper reports results with leverage up to 4x.", "Weak after double costs in this test."],
        {"lookback": ("Normal range measured over", "Longer gives a steadier band.", {10: "last 10 days", 14: "last 14 days"}),
         "check": ("Check the price every", "Less often means fewer trades.", {30: "30 minutes", 60: "60 minutes"})}),
    "id_gap_fade": ("ID-GAP", "When the market opens far from yesterday's close, bets it moves back towards that close.",
        ["gap    = open / yesterday's close - 1", "ENTRY  09:31, against the gap, if |gap| > threshold", "EXIT   when the gap fills, or at the close"],
        "Big gaps are often overreactions to overnight news that partly reverse once regular trading starts.",
        ["Big gaps are rare, so there are few trades.", "When a gap keeps running it can lose all day.", "Trader folklore, not a peer-reviewed result."],
        {"gap": ("Minimum gap", "Bigger gaps are rarer but more stretched.", {0.0025: "0.25%", 0.005: "0.5%", 0.01: "1%"}),
         "exit_": ("Take profit", "'When it fills' banks the win early.", {"fill": "when the gap fills", "close": "hold to the close"})}),
    "id_halfhour_periodicity": ("ID-HALF", "Each half hour of the day, bets the same way that half hour went on average over recent days.",
        ["slot   = one of the 13 half hours of the session", "SIGNAL = average return of this slot over the last N days", "SIDE   long if > 0 (short if < 0, or flat in long-only)"],
        "Heston, Korajczyk & Sadka (2010) found the same half hour tends to repeat its performance on later days.",
        ["The paper found it unprofitable after trading costs.", "It was a cross-section of stocks; one index fund is a weaker test.", "Trades up to 13 times a day."],
        {"lookback": ("Average over", "Longer is steadier but slower to adapt.", {10: "last 10 days", 20: "last 20 days", 40: "last 40 days"}),
         "mode": ("Direction", "Long-only never goes short.", {"ls": "long and short", "long": "long only"})}),
    # study 3 (INTRADAY_PLAN_3.md)
    "id_vol_breakout": ("ID-WILL", "Sets bands at today's open plus and minus part of yesterday's high-low range, and trades the first break of either band until the close.",
        ["range  = yesterday's high - yesterday's low", "bands  = today's open +/- k x range", "ENTRY  first close beyond a band, in that direction", "EXIT   at the close"],
        "Larry Williams (1999): a move bigger than a slice of yesterday's range shows today's real direction.",
        ["Built for 1980s-90s futures; markets have changed since.", "Late breaks leave little time to profit.", "One trade a day, so a small edge is eaten by costs."],
        {"k": ("Band width", "Wider bands trade less often, on bigger moves.", {0.25: "0.25 x yesterday's range", 0.5: "0.5 x", 0.75: "0.75 x"}),
         "mode": ("Direction", "Long-only never goes short.", {"ls": "long and short", "long": "long only"})}),
    "id_prev_day_breakout": ("ID-PDB", "Buys when the price breaks above yesterday's high, sells short when it breaks below yesterday's low, and holds to the close.",
        ["levels = yesterday's high and low", "ENTRY  first close beyond a level", "STOP   optional: back through the level", "EXIT   at the close"],
        "Yesterday's high and low are levels many traders watch, so breaking them can bring in more buyers or sellers.",
        ["Many breaks fail and snap back.", "Gap days trigger at the open, often at the worst price.", "Practitioner lore, no peer-reviewed evidence."],
        {"start": ("Start checking at", "Waiting skips the noisy first half hour.", {0: "09:30", 30: "10:00"}),
         "stop": ("Stop", "A stop cuts failed breaks short.", {"none": "none, hold to the close", "level": "back through the broken level"})}),
    "id_vwap_reversion": ("ID-VREV", "When the price is stretched far from the day's VWAP, bets it comes back to it.",
        ["dev    = price / vwap - 1, checked 10:00-15:30", "ENTRY  against the move if |dev| > threshold", "EXIT   when the price crosses VWAP, or at the close"],
        "The mirror image of VWAP Trend: big intraday stretches are often liquidity shocks that fade.",
        ["Trend days run away from VWAP and never come back.", "Practitioner idea, not a published result.", "The opposite rule (VWAP Trend) is also on this page; both cannot be right."],
        {"thr": ("Minimum stretch", "Bigger stretches are rarer but more extreme.", {0.003: "0.3% from VWAP", 0.005: "0.5%", 0.008: "0.8%"}),
         "exit_": ("Take profit", "'At VWAP' banks the win early.", {"vwap": "when it reaches VWAP", "close": "hold to the close"})}),
    "id_bollinger_5m": ("ID-BB", "On 5-minute bars, buys closes below the lower Bollinger Band and sells closes above the upper one, until the price is back at the average.",
        ["mid    = average of the last n 5-minute closes", "bands  = mid +/- k standard deviations", "ENTRY  fade a close outside a band", "EXIT   close crosses mid, or at the close"],
        "Bollinger (2001): prices outside the bands are statistically stretched and tend to revert.",
        ["Bollinger himself warned bands are not buy and sell signals on their own.", "Strong trends ride the band for hours.", "Many small trades, so costs matter."],
        {"n": ("Average over", "More bars make steadier bands.", {20: "20 bars (100 minutes)", 40: "40 bars (200 minutes)"}),
         "k": ("Band width", "Wider bands trade only bigger stretches.", {2.0: "2 standard deviations", 2.5: "2.5 standard deviations"})}),
    "id_donchian": ("ID-DON", "Goes long when the price beats the high of the last 30 to 120 minutes and short when it breaks the low, always in the market after the first break.",
        ["hh, ll = high and low of the previous N minutes", "LONG   close > hh;  SHORT  close < ll", "EXIT   the opposite break, or at the close"],
        "The Turtle traders' channel breakout, moved from daily to minute bars: ride whichever way the price escapes.",
        ["Built for long trends on daily bars; intraday trends are short.", "Choppy days whipsaw it back and forth.", "Always exposed after the first break."],
        {"N": ("Channel length", "Longer channels break less often.", {30: "30 minutes", 60: "60 minutes", 120: "120 minutes"})}),
    "id_late_momentum": ("ID-LATE", "If the market is up on the day by 15:00 or 15:30 it buys into the close; if down, it sells short.",
        ["r     = price(15:00 or 15:30) / reference - 1", "REF   yesterday's close or today's open", "ENTRY  long if r > 0, short if r < 0", "EXIT   at the close"],
        "Baltussen, Da, Lammers & Martens (2021): option dealers' hedging pushes late-day prices in the day's direction.",
        ["The hedging flow depends on dealer positioning, which changes.", "One short trade a day, so costs are a big share.", "Overlaps with Market Intraday Momentum."],
        {"ref": ("Measure from", "Yesterday's close includes the overnight gap.", {"close": "yesterday's close", "open": "today's open"}),
         "sig_end": ("Signal until", "Later reads more of the day but leaves less to trade.", {330: "15:00 (hold 60 minutes)", 360: "15:30 (hold 30 minutes)"})}),
    "id_fomc_drift": ("ID-FOMC", "On the eight Fed announcement days a year, buys in the morning and sells at 14:00, just before the statement.",
        ["DAYS   FOMC announcement days only", "ENTRY  09:30 or 10:00, long", "EXIT   14:00"],
        "Lucca & Moench (2015): stocks rose before Fed announcements from 1994 to 2011, a large share of the equity premium.",
        ["Only about 8 trades a year, so results are very noisy.", "Later studies found the drift faded after it was published.", "The coin-flip test here only checks the timing within those days."],
        {"entry": ("Buy at", "Later skips the opening noise.", {0: "09:30", 30: "10:00"})}),
    # study 4 (INTRADAY_PLAN_4.md)
    "id_gap_go": ("ID-GAPGO", "When the market opens with a big gap and the first minutes keep going that way, trades with the gap until the close.",
        ["gap    = open / yesterday's close - 1", "CONFIRM first N minutes close in the gap's direction", "ENTRY  with the gap, after N minutes", "STOP   yesterday's close (gap filled)"],
        "Big gaps on news can keep running as slower traders react; the opposite bet to Gap Fade.",
        ["Big gaps are rare, so there are few trades.", "Gap Fade, its exact opposite, is also on this page.", "Practitioner lore, no peer-reviewed evidence."],
        {"gap": ("Minimum gap", "Bigger gaps are rarer.", {0.0025: "0.25%", 0.005: "0.5%", 0.01: "1%"}),
         "confirm": ("Confirm over the first", "Longer confirmation is surer but enters later.", {5: "5 minutes", 15: "15 minutes"})}),
    "id_pivot_revert": ("ID-PIVOT", "Sells the first touch of yesterday's pivot resistance and buys the first touch of support, betting the level holds.",
        ["P      = (yesterday's high + low + close) / 3", "R1/S1  = 2P - low / 2P - high;  R2/S2 = P +/- range", "ENTRY  fade the first touch", "STOP   next level out;  EXIT  at P or the close"],
        "Floor traders have watched these levels for decades, so orders cluster there.",
        ["The levels are arithmetic, not real supply and demand.", "Strong trend days slice straight through them.", "Practitioner lore, no peer-reviewed evidence."],
        {"level": ("Fade the", "Level 2 is further out and touched less often.", {1: "first level (R1/S1)", 2: "second level (R2/S2)"}),
         "target": ("Take profit", "The pivot banks wins early.", {"pivot": "at the pivot P", "close": "hold to the close"})}),
    "id_first_hour_reversal": ("ID-FHR", "If the first hour of trading moves a lot, bets the rest of the day goes the other way.",
        ["r     = price(10:30) / open - 1", "ENTRY  against r at 10:30, if |r| > threshold", "EXIT   after 2 hours, or at the close"],
        "Morning moves are often overreactions to overnight news that partly unwind by the afternoon.",
        ["Market Intraday Momentum finds the opposite: the morning predicts the close.", "Trend days punish it all afternoon.", "Practitioner idea, not a published result."],
        {"thr": ("Minimum first-hour move", "Bigger moves are rarer.", {0.0025: "0.25%", 0.005: "0.5%", 0.01: "1%"}),
         "hold": ("Hold for", "Shorter holds take less risk.", {120: "2 hours", 0: "until the close"})}),
    "id_time_of_day": ("ID-TOD", "Owns the market during only one fixed part of every trading day, and nothing else.",
        ["WINDOW one fixed part of the session", "ENTRY  long at its start, every day", "EXIT   at its end"],
        "Studies of intraday seasonality find some parts of the day (often the open and the last half hour) earn more than others.",
        ["Pays costs every single day.", "Picking the best window after seeing the data is hindsight.", "Any pattern this simple tends to get traded away."],
        {"window": ("Hold during", "Each is a different part of the day.", {"open30": "09:30-10:00", "morning": "10:00-12:00", "midday": "12:00-14:00",
                                                                             "afternoon": "14:00-15:30", "last30": "last 30 minutes", "last60": "last 60 minutes"})}),
    "id_volume_spike_fade": ("ID-VSPK", "When one minute has a huge burst of volume and a sharp move, bets the price snaps back over the next 15 to 30 minutes.",
        ["spike  = volume > k x average of the last 30 minutes", "AND    the minute moved more than 0.1%", "ENTRY  against that move, next minute", "EXIT   after 15 or 30 minutes"],
        "Big market orders push prices past fair value for a moment; liquidity providers then pull them back.",
        ["Spikes on real news do not snap back.", "Short holds make costs a big share of the edge.", "Practitioner idea, not a published result."],
        {"k": ("Volume spike of", "Bigger spikes are rarer.", {5.0: "5x normal", 10.0: "10x normal"}),
         "hold": ("Hold for", "Longer gives the snap-back more time.", {15: "15 minutes", 30: "30 minutes"})}),
    "id_crabel_stretch": ("ID-CRAB", "Trades the first move away from the open that is bigger than the market's usual opening 'stretch'.",
        ["stretch = 10-day average of min(high - open, open - low)", "bands   = open +/- m x stretch", "ENTRY  first close beyond a band", "EXIT   at the close"],
        "Toby Crabel (1990): the open usually sits near one end of the day's range, so a move past the normal stretch shows the day's direction.",
        ["Close cousin of Williams's Volatility Breakout.", "Built on 1980s futures data.", "One trade a day, so costs eat a small edge."],
        {"mult": ("Band width", "Wider bands trade less often.", {1.0: "1 x stretch", 1.5: "1.5 x stretch", 2.0: "2 x stretch"}),
         "mode": ("Direction", "Long-only never goes short.", {"ls": "long and short", "long": "long only"})}),
    "id_lunch_breakout": ("ID-LUNCH", "Waits for the quiet lunchtime range, then trades the first afternoon break out of it until the close.",
        ["range  = high and low of the midday window", "ENTRY  first close beyond the range", "STOP   optional: the range's other end", "EXIT   at the close"],
        "Lunchtime is quiet; when afternoon traders return, a break of that range can show the day's last move.",
        ["Many afternoon breaks fail and snap back.", "Little time left in the day to profit.", "Practitioner idea, not a published result."],
        {"win": ("Midday window", "Each is a different lunch range.", {"1130": "11:30-13:00", "1200": "12:00-13:30"}),
         "stop": ("Stop", "A stop cuts failed breaks short.", {"none": "none, hold to the close", "range": "the range's other end"})}),
}


def build_intraday(book, markets):
    """Add the intraday studies' strategies (results/intraday.json and intraday3.json) to the book, with every
    grid setting recomputed so the page's sliders work, plus each strategy's FTMO 2-Step challenge table."""
    import intraday as I
    import intraday3
    import intraday4
    by_id = {s["id"]: s for s in I.STRATEGIES + intraday3.STRATEGIES + intraday4.STRATEGIES}
    days = {}
    for fname in ("intraday.json", "intraday3.json", "intraday4.json"):
        path = ROOT / "results" / fname
        if path.exists():
            _add_intraday(book, markets, json.loads(path.read_text()), by_id, days)


def _add_intraday(book, markets, res, by_id, days):
    import intraday as I
    pool = sum(1 for r in res["results"] if r.get("coin_flip"))
    for row in res["results"]:
        sym, st = row["asset"], by_id[row["strategy"]]
        if sym not in days:
            days[sym] = I.Days(sym)
            D = days[sym]
            markets[f"{sym}-1m"] = {"dates": [str(d.date()) for d in D.dates],
                                    "bench": r5(np.r_[0.0, D.close[1:] / D.close[:-1] - 1]),
                                    "oos_start": I.OOS_START, "is_end": I.IS_END, "ann": 252, "cost_bps": 1}
        D = days[sym]
        combos = []
        for p in st["grid"]:
            t = st["fn"](D, **p)
            r = np.zeros(D.n)
            np.add.at(r, t["day"], t["ret"])
            traded = np.zeros(D.n, int)
            traded[t["day"]] = 100
            # trades packed as [day deltas, returns in 1e-5 units]; the page unpacks them into [[day, ret], ...]
            days_ = t["day"].astype(int)
            combos.append({"params": p, "r": r5(r), "tr": [np.diff(days_, prepend=0).tolist(), np.round(t["ret"] * 1e5).astype(int).tolist()],
                           "pos": traded.tolist()})
        chosen = next(i for i, c in enumerate(combos) if _js(c["params"]) == _js(row["params"]))
        code, plain, signal, edge, kills, pp = INTRA[st["id"]]
        cf, lb = row.get("coin_flip"), row.get("lockbox")
        if cf is None:
            verdict = "NOTEST"
        elif row.get("candidate") and lb and lb.get("confirmed"):
            verdict = "PASS"
        elif cf["p_value"] < 0.05:
            verdict = "WEAK"
        else:
            verdict = "FAIL"
        oos_years = (D.n - int(D.dates.searchsorted(np.datetime64(I.OOS_START)))) / 252
        per_year = row["oos"]["trades"] / oos_years if oos_years > 0 else 0.0
        beat = 1 - cf["p_value"] if cf else 0.0
        lock = bool(lb and lb.get("confirmed"))
        cand = bool(row.get("candidate"))
        book.append({"code": f"{code}", "id": f"{st['id']}_{sym}", "name": st["name"], "market": f"{sym}-1m",
                     "desc": st["plain"], "family": "Intraday", "freq": "Minute bars, flat every night" if st["id"] != "id_overnight" else "Holds overnight only",
                     "source": st["source"], "signal": signal, "edge": edge, "kills": kills, "plain": plain,
                     "pparams": {k: {"label": a, "help": h, "values": {_js(v): txt for v, txt in vals.items()}} for k, (a, h, vals) in pp.items()},
                     "axes": {k: sorted({_js(c["params"][k]) for c in combos}, key=_axis_key) for k in st["grid"][0]},
                     "combos": combos, "chosen": chosen, "ftmo": row.get("ftmo"),
                     "verdict": {"verdict": verdict, "p": cf["p_value"] if cf else None, "q": cf.get("q_value") if cf else None,
                                 "lockbox": lock if cand else None, "pool": pool, "params": row["params"],
                                 "cost2x": row["oos_cost2x_sharpe"]},
                     "evidence": {"fresh": lock, "lockbox": cand, "beat": round(beat, 4), "per_year": round(per_year, 1),
                                  "few": bool(per_year < 5), "tested": cf is not None,
                                  "score": (2 if lock else 0) + (1 if cand else 0) - (1 if per_year < 5 else 0) - (0 if cf else 2)}})


def _js(v):
    """Same text as the browser's JSON.stringify, so settings match on the page."""
    return json.dumps(list(v) if isinstance(v, tuple) else v, separators=(",", ":"))


def _axis_key(x):
    v = json.loads(x)
    return (0, float(v), "") if isinstance(v, (int, float)) and not isinstance(v, bool) else (1, 0.0, x)


def r5(a):
    return [None if not math.isfinite(x) else round(float(x), 5) for x in a]


def verdict_for(sid, market, results, conf):
    row = next((r for r in results if r["strategy"] == sid and r["asset"] == market), None)
    if row is None:
        return None
    cf, lb = row.get("coin_flip") or {}, row.get("lockbox") or {}
    v = {"verdict": row["verdict"], "p": cf.get("p_value"), "q": cf.get("q_value"),
         "lockbox": lb.get("confirmed"), "lockbox_p": lb.get("fisher_p"), "params": row["params"]}
    c = (conf or {}).get("candidates", {}).get(sid)
    if c:
        v["fresh"] = {"confirmed": c["confirmed"], "fisher_p": c["fisher_p"],
                      "hits": c["markets_p_below_0.10"], "n": c["n_markets"]}
    return v


def build_market(ticker, ctx_raw, cache):
    if ticker not in cache:
        cfg = CONFIG[ticker]
        df = data.load(ticker).loc[cfg["is_start"]:]
        cache[ticker] = (df, data.align_ctx(ctx_raw, df.index), cfg)
    return cache[ticker]


def series(df, pos, cost):
    o, c = df.Open.values, df.Close.values
    r = returns_np(o, c, pos, cost)
    tr = extract_trades(o, c, pos, cost)
    return {"r": r5(r), "trades": [[int(e), round(float(x), 5)] for e, x in zip(tr["entry"], tr["ret"])],
            "pos": [int(round(p * 100)) for p in pos]}


def rank_book(book, cache):
    """Order the book by strength of evidence (rule shown on the page):
    +2 held up on fresh data, +1 held up on lockbox markets, -1 if fewer than 5 decisions a year
    (a record built on a handful of calls); ties broken by the share of random traders beaten."""
    for b in book:
        if "evidence" in b:  # intraday entries carry their own evidence
            continue
        v = b["verdict"]
        df, _, cfg = cache[b["market"]]
        oos = df.index.searchsorted(np.datetime64(cfg["oos_start"]))
        years = (len(df) - oos) / cfg["ann"]
        n_tr = sum(1 for e, _ in b["combos"][b["chosen"]]["trades"] if e >= oos)
        per_year = float(n_tr / years) if years > 0 else 0.0
        fresh = bool((v.get("fresh") or {}).get("confirmed"))
        lock = bool(v.get("lockbox"))
        beat = 1 - v["p"] if v.get("p") is not None else 0.0
        few = bool(per_year < 5)
        score = (2 if fresh else 0) + (1 if lock else 0) - (1 if few and b["code"] != "AGENT" else 0)
        if b["code"] == "AGENT":
            score = -9  # no held-out evidence at all yet
        b["evidence"] = {"fresh": fresh, "lockbox": lock, "beat": round(beat, 4), "per_year": round(per_year, 1),
                         "few": few, "score": score, "tested": v.get("p") is not None}
    book.sort(key=lambda b: (-b["evidence"]["score"], -b["evidence"]["beat"]))
    for i, b in enumerate(book, 1):
        b["rank"] = i


def main():
    results = json.loads((ROOT / "results" / "results.json").read_text())["results"]
    cp = ROOT / "results" / "confirmation.json"
    conf = json.loads(cp.read_text()) if cp.exists() else None
    ctx_raw = data.load_context()
    cache, markets, book = {}, {}, []
    for b in BOOK:
        if b["code"] not in LAB_DAILY:
            continue
        st = BY_ID[b["id"]]
        df, ctx, cfg = build_market(b["market"], ctx_raw, cache)
        v = verdict_for(b["id"], b["market"], results, conf)
        combos = []
        for p in st["grid"]:
            pos = np.clip(np.nan_to_num(positions(st, df, ctx, p).values.astype(float)), 0, 1)
            combos.append({"params": p} | series(df, pos, cfg["cost_bps"]))
        chosen = next(i for i, c in enumerate(combos) if json.loads(json.dumps(c["params"])) == v["params"])
        fam, freq, src = ABOUT[b["code"]]
        book.append({"code": b["code"], "id": b["id"], "name": st["name"], "market": b["market"],
                     "desc": st["description"], "family": fam, "freq": freq, "source": src,
                     "signal": b["signal"], "edge": b["edge"], "kills": b["kills"],
                     "plain": PLAIN[b["code"]][0], "pparams": _plain_params(b["code"], combos),
                     "axes": {k: sorted({_js(c["params"][k]) for c in combos}, key=_axis_key)
                              for k in st["grid"][0]},
                     "combos": combos, "chosen": chosen, "verdict": v})
    # the self-improving agent's current best idea
    mem = Memory()
    top = mem.top(1) if SHOW_AGENT else []
    if top:
        t = top[0]
        s = t["spec"]
        m = AP.Market(s["market"], ctx_raw)
        df, ctx, cfg = build_market(s["market"], ctx_raw, cache)
        pos = AS.positions(s, m.table, m.th)
        assert len(pos) == len(df)
        fam, freq, src = ABOUT["AGENT"]
        book.append({"code": "AGENT", "id": "agent_" + t["hash"], "name": "Agent's best idea", "market": s["market"],
                     "desc": AS.describe(s), "family": fam, "freq": freq, "source": src,
                     "signal": ["ENTRY  " + " AND ".join(f"{c['feature']} {c['op']} p{c['q']}" for c in s["entry"])]
                     + ([f"FILTER {s['filter']['feature']} {s['filter']['op']} p{s['filter']['q']}"] if s.get("filter") else [])
                     + ([f"EXIT   after {s['exit']['n']} sessions"] if s["exit"]["type"] == "hold" else
                        [f"EXIT   {s['exit']['cond']['feature']} {s['exit']['cond']['op']} p{s['exit']['cond']['q']}"
                         f" or after {s['exit']['max_hold']} sessions"])
                     + ["", "pN = N-th percentile of that feature on the tuning years"],
                     "plain": "An idea the self-learning agent came up with on its own: " + AS.describe(s).split(": ", 1)[1] + ".",
                     "pparams": {},
                     "edge": f"Found by the agent after {len(mem.trials)} trials. Its score on the tuning years, "
                             f"deflated for every trial run, is {mem.current_reward(t):.2f} (0.95 is needed before a held-out test).",
                     "kills": ["Chosen as the best of hundreds of tries: some of its score is luck.",
                               "Has not earned a held-out test yet, so its test-year numbers below are for viewing only.",
                               "Three or more conditions: each one is another chance to fit noise."],
                     "axes": {}, "combos": [{"params": {}} | series(df, pos, cfg["cost_bps"])], "chosen": 0,
                     "verdict": {"verdict": "AGENT", "reward": round(mem.current_reward(t), 3),
                                 "trials": len(mem.trials)}})
    build_intraday(book, markets)
    rank_book(book, cache)
    for ticker, (df, _, cfg) in cache.items():
        o, c = df.Open.values, df.Close.values
        markets[ticker] = {"dates": [d.strftime("%Y-%m-%d") for d in df.index],
                           "bench": r5(returns_np(o, c, np.ones(len(c)), cfg["cost_bps"])),
                           "oos_start": cfg["oos_start"], "is_end": cfg["is_end"], "ann": cfg["ann"],
                           "cost_bps": cfg["cost_bps"]}
    hv = mem.holdout_verdicts()
    research = {"trials": len(mem.trials), "scored": len(mem.scored()),
                "rejected": sum(1 for t in mem.trials if t.get("status") == "rejected"),
                "holdout_shots": len(hv), "holdout_pass": sum(v["pass"] for v in hv),
                "top": [{"desc": AS.describe(t["spec"]), "now": round(mem.current_reward(t), 3),
                         "found": t["reward"], "folds": t["fold_sharpes"]} for t in mem.top(8)],
                "hunt_tests": sum(1 for r in results if r.get("coin_flip")),
                "hunt_strategies": len({r["strategy"] for r in results}) - 1}
    pp = ROOT / "agent_memory" / "paper_report.json"
    paper = json.loads(pp.read_text()) if pp.exists() else {"strategies": {}}
    ap = ROOT / "agent_memory" / "alpaca_account.json"
    alpaca = json.loads(ap.read_text()) if ap.exists() else None
    ip = ROOT / "results" / "intraday.json"
    portfolios = json.loads(ip.read_text()).get("portfolios", []) if ip.exists() else []
    blob = json.dumps({"portfolios": portfolios, "book": book, "markets": markets, "research": research, "paper": paper, "alpaca": alpaca,
                       "generated": json.loads((ROOT / "results" / "results.json").read_text())["generated"]},
                      separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "report" / "lab_template.html").read_text().replace("/*LAB*/", blob)
    out = ROOT / "report" / "lab.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
