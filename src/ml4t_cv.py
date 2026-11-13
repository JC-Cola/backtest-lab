"""Purged / embargoed cross-validation over trading days (Lopez de Prado; ML4T's PurgedKFold / MultipleTimeSeriesCV idea).

Rows carry a day position `pos` (0, 1, 2, ... in calendar order of the pooled sessions). The sessions are cut into
contiguous blocks; each block is a test fold; the training rows are all rows farther than `purge` sessions from the block
(on BOTH sides: before it, because features look back up to 14 sessions, and after it, the embargo). Labels of this study
end on the same day they start, so purging by distance is conservative.
"""
from __future__ import annotations

import numpy as np


def purged_block_splits(pos, n_blocks=5, purge=5):
    """[(train_idx, test_idx)] over the rows. `pos`: int session position of every row."""
    pos = np.asarray(pos)
    days = np.unique(pos)
    blocks = np.array_split(days, n_blocks)
    out = []
    for b in blocks:
        if len(b) == 0:
            continue
        lo, hi = b.min(), b.max()
        test = (pos >= lo) & (pos <= hi)
        train = (pos < lo - purge) | (pos > hi + purge)
        out.append((np.flatnonzero(train), np.flatnonzero(test)))
    return out


def oof_predict(X, y, pos, fit_predict, n_blocks=5, purge=5):
    """Out-of-fold predictions: `fit_predict(X_train, y_train, X_test) -> scores`. Every row is predicted exactly once."""
    X, y = np.asarray(X), np.asarray(y)
    out = np.full(len(y), np.nan)
    for tr, te in purged_block_splits(pos, n_blocks, purge):
        out[te] = fit_predict(X[tr], y[tr], X[te])
    return out
