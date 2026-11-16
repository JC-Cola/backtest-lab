"""qlib data: incremental merge with dividend rescale, qlib binary layout, normalisation. No qlib/lightgbm needed."""
import numpy as np
import pandas as pd

import qlib_data as D
import qlib_model as M


def frame(dates, close, vol=1000):
    c = np.array(close, dtype=float)
    return pd.DataFrame({"open": c * 0.99, "high": c * 1.01, "low": c * 0.98, "close": c, "volume": vol}, index=pd.to_datetime(dates))


def test_merge_rescales_history_after_a_dividend():
    old = frame(["2026-01-02", "2026-01-05", "2026-01-06"], [100, 101, 102])
    new = frame(["2026-01-05", "2026-01-06", "2026-01-07"], [101 * 0.99, 102 * 0.99, 103.0])   # ex-dividend: history x 0.99
    m = D.merge(old, new)
    assert list(m.index.strftime("%m-%d")) == ["01-02", "01-05", "01-06", "01-07"]
    assert m["close"].round(4).tolist() == [99.0, 99.99, 100.98, 103.0]
    assert np.allclose(m["close"].pct_change().dropna().iloc[:1], 101 / 100 - 1)


def test_merge_without_change_keeps_old_and_appends():
    old = frame(["2026-01-02", "2026-01-05"], [100, 101])
    new = frame(["2026-01-05", "2026-01-06"], [101, 102])
    m = D.merge(old, new)
    assert m["close"].tolist() == [100, 101, 102] and D.merge(None, new).equals(new)


def test_write_bin_layout_matches_qlib(tmp_path):
    cal = pd.bdate_range("2024-01-01", periods=420)
    csv = tmp_path / "csv"
    csv.mkdir()
    frame(cal, np.linspace(100, 130, 420)).to_csv(csv / "SPY.csv")
    late = frame(cal[100:], np.linspace(10, 20, 320))
    late.iloc[10, 3] = np.nan                                                  # a missing day inside the range
    late.dropna(subset=["close"]).to_csv(csv / "BRK.B.csv")
    frame(cal[:50], np.ones(50)).to_csv(csv / "SHORT.csv")                    # < MIN_ROWS: skipped
    info = D.write_bin(csv, tmp_path / "bin")
    assert info["symbols"] == 1 and info["skipped"] == ["SHORT"] and info["last"] == cal[-1].strftime("%Y-%m-%d")
    assert (tmp_path / "bin" / "calendars" / "day.txt").read_text().split("\n")[0] == "2024-01-01"
    inst = (tmp_path / "bin" / "instruments" / "sp500.txt").read_text().strip().split("\n")
    assert inst == ["BRK.B\t%s\t%s" % (cal[100].strftime("%Y-%m-%d"), cal[-1].strftime("%Y-%m-%d"))]
    assert "SPY" in (tmp_path / "bin" / "instruments" / "all.txt").read_text() and "SPY" not in "".join(inst)
    b = np.fromfile(tmp_path / "bin" / "features" / "brk.b" / "close.day.bin", dtype="<f")
    assert int(b[0]) == 100 and len(b) == 1 + 320 and np.isclose(b[1], 10.0, atol=1e-4)
    f = np.fromfile(tmp_path / "bin" / "features" / "brk.b" / "factor.day.bin", dtype="<f")
    assert (f[1:] == 1.0).all()


def test_robust_norm_clips_and_fills():
    X = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0], [100.0, 5.0], [np.nan, 5.0]], dtype="float32")
    n = M.Norm.fit(X)
    Z = n.transform(X)
    assert Z.max() <= 3.0 and Z.min() >= -3.0 and not np.isnan(Z).any()
    assert Z[3, 0] == 3.0 and Z[4, 0] == 0.0 and (Z[:, 1] == 0).all()           # constant column -> 0


def test_rank_norm_is_per_day_centred():
    idx = pd.MultiIndex.from_product([pd.to_datetime(["2026-01-05", "2026-01-06"]), list("abcd")])
    y = pd.Series([1, 2, 3, 4, 10, 30, 20, 40], index=idx, dtype=float)
    r = M.rank_norm(y)
    assert np.isclose(r.groupby(level=0).mean(), 0.0, atol=0.5).all()
    assert r.loc[("2026-01-06", "b")] > r.loc[("2026-01-06", "c")] - 10 and r.iloc[3] > r.iloc[0] and r.iloc[5] > r.iloc[6]
