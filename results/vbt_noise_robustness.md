# Noise-band robustness (vectorbt), tuning years only

Descriptive check of how sensitive the live noise-band strategy is to its parameters. **Nothing here selects or changes a parameter.**
Window: 2016-02-18 to 2020-12-31 (1228 sessions, tuning years only; no 2021+ data is loaded).
Book: 50% SPY + 50% QQQ, 1 bp per side. Metric: annualised Sharpe of daily returns. Grid: lookback [5, 10, 14, 20, 25, 30] x check [15, 30, 60, 90, 120] min
x band multiplier [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4] x VWAP filter on/off = 420 cells.

## Where the live setting sits
Live (lookback 14, check 60, band 1.0, VWAP on): Sharpe **0.74** (SPY 0.32, QQQ 0.98),
CAGR 4.6%, max drawdown -6.5%. Rank 49 of 420 (percentile 88%).
Its 7 one-step neighbours average 0.60 (range 0.33 to 0.86);
live minus neighbour mean = +0.14. VWAP filter off (all else live): 0.64.

## Shape of the surface
Whole grid: median 0.47, 10th to 90th percentile 0.21 to 0.75, min -0.11, max 1.00;
99% of cells have a positive Sharpe. A cell correlates 0.94 with the mean of its
neighbours (near 1 = smooth plateau, near 0 = noisy/spiky). The best cell in the grid ({'lookback': 14, 'check': 60, 'band': 1.3, 'vwap': True}) scores 1.00 against a neighbour mean of
0.76 (gap +0.24); it is shown only to judge spike vs plateau and is not adopted.

Marginal mean Sharpe by axis: {"lookback": {"5": 0.44819665738964976, "10": 0.3629489350072574, "14": 0.4162879246363316, "20": 0.5243962924019386, "25": 0.5370410272850556, "30": 0.5487894483996656}, "check": {"15": 0.30488960083516575, "30": 0.5712063790561239, "60": 0.7254793507868385, "90": 0.3497872428891432, "120": 0.4133543306993108}, "band": {"0.8": 0.36672782551452415, "0.9": 0.3773081754072037, "1.0": 0.45186422973203155, "1.1": 0.4866720383681338, "1.2": 0.5021148683904876, "1.3": 0.5671915905639779, "1.4": 0.5587249379968565}, "vwap": {"True": 0.4860261983039167, "False": 0.4598605634027162}}

**Lookback x check (band 1.0, VWAP on); live in bold**

| lookback \ check min | 15 | 30 | 60 | 90 | 120 |
|---|---|---|---|---|---|
| 5 | 0.21 | 0.61 | 0.67 | 0.37 | 0.53 |
| 10 | -0.09 | 0.35 | 0.54 | 0.23 | 0.17 |
| 14 | 0.24 | 0.51 | **0.74** | 0.34 | 0.35 |
| 20 | 0.22 | 0.62 | 0.73 | 0.41 | 0.64 |
| 25 | 0.46 | 0.64 | 0.86 | 0.43 | 0.57 |
| 30 | 0.43 | 0.56 | 0.82 | 0.42 | 0.45 |


**Band multiplier x check (lookback 14, VWAP on)**

| band \ check min | 15 | 30 | 60 | 90 | 120 |
|---|---|---|---|---|---|
| 0.8 | 0.12 | 0.33 | 0.46 | 0.28 | 0.20 |
| 0.9 | 0.16 | 0.48 | 0.62 | 0.27 | 0.25 |
| 1.0 | 0.24 | 0.51 | **0.74** | 0.34 | 0.35 |
| 1.1 | 0.19 | 0.60 | 0.86 | 0.33 | 0.47 |
| 1.2 | 0.17 | 0.44 | 0.81 | 0.43 | 0.42 |
| 1.3 | 0.33 | 0.49 | 1.00 | 0.38 | 0.68 |
| 1.4 | 0.33 | 0.51 | 0.82 | 0.28 | 0.52 |


**Lookback x band (check 60, VWAP on)**

| lookback \ band | 0.8 | 0.9 | 1.0 | 1.1 | 1.2 | 1.3 | 1.4 |
|---|---|---|---|---|---|---|---|
| 5 | 0.45 | 0.57 | 0.67 | 0.64 | 0.67 | 0.63 | 0.75 |
| 10 | 0.70 | 0.63 | 0.54 | 0.62 | 0.74 | 0.89 | 0.93 |
| 14 | 0.46 | 0.62 | **0.74** | 0.86 | 0.81 | 1.00 | 0.82 |
| 20 | 0.64 | 0.59 | 0.73 | 0.82 | 0.84 | 0.97 | 0.88 |
| 25 | 0.65 | 0.70 | 0.86 | 0.75 | 0.76 | 0.93 | 0.88 |
| 30 | 0.64 | 0.72 | 0.82 | 0.86 | 0.82 | 0.85 | 0.76 |


**Lookback x check (band 1.0, VWAP off)**

| lookback \ check min | 15 | 30 | 60 | 90 | 120 |
|---|---|---|---|---|---|
| 5 | 0.21 | 0.68 | 0.67 | 0.41 | 0.45 |
| 10 | -0.11 | 0.35 | 0.46 | 0.26 | 0.06 |
| 14 | 0.21 | 0.49 | **0.64** | 0.29 | 0.21 |
| 20 | 0.21 | 0.65 | 0.74 | 0.39 | 0.55 |
| 25 | 0.41 | 0.63 | 0.83 | 0.39 | 0.46 |
| 30 | 0.39 | 0.57 | 0.82 | 0.38 | 0.39 |



## Speed
vectorbt, all 420 configs for one symbol in one Portfolio: {'SPY': 23.23, 'QQQ': 9.16} s per symbol.
Existing engine: 2.818 s per config, so about 2367.4 s for the same
420 x 2 runs (it cannot vary band or VWAP, so this is extrapolated). Vectorised results match the engine on the lookback x check plane:
{"SPY": {"max_abs_daily_diff_vs_engine_compounded": 3.0051700899980105e-06, "configs": 30, "engine_seconds_per_config": 2.400774833302906}, "QQQ": {"max_abs_daily_diff_vs_engine_compounded": 3.1262902028261763e-06, "configs": 30, "engine_seconds_per_config": 3.235778718003227}}.

Caveat: Sharpe over five years of one book is a noisy number (about +/-0.5 standard error); read the surface for shape, not for small differences.
