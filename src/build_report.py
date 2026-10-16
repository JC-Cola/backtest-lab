"""Builds report/index.html from results/results.json and report/template.html."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def slim(d: dict) -> dict:
    """Drop duplicated buy-and-hold curves and round floats so the page stays small."""
    bh = {}
    for r in d["results"]:
        e = r.pop("equity")
        r["eq"] = [None if v is None else round(v, 3) for v in e["strategy"]]
        bh.setdefault(r["asset"], {"dates": e["dates"], "bh": [round(v, 3) for v in e["buy_hold"]]})
        if r.get("coin_flip"):
            r["coin_flip"]["hist"]["edges"] = [round(x, 3) for x in r["coin_flip"]["hist"]["edges"]]
    d["bh_curves"] = bh
    return d


# Plain-language caveats from post-hoc checks run AFTER the pre-registered confirmation
# (they cannot change its verdict; see the commit history for the order of events).
CONFIRM_NOTES = {
    "ibs": "Holds up even on the 7 markets whose opening prices are real trades (some index data "
           "reuses yesterday's close as the open, which would flatter a dip-buyer). But it is only in "
           "the market about a third of the time and usually earned less than just holding: a real "
           "timing edge, not a better investment on its own. Since 2014, after IBS became widely "
           "published, it still beat random timing on SPY, QQQ and DIA but not on any of the 10 "
           "international indexes, and it earned less than holding in 13 of 14 markets.",
    "qp_january_barometer": "Only one decision a year, and Januaries move together worldwide. Most of "
           "its edge comes from three global bear markets (2000, 2001, 2008); in other years it often sat "
           "out rising markets. That is about three lucky calls, so treat it as unproven.",
    "williams_r": "Strong combined score, but too few markets were individually convincing.",
}


def main():
    d = slim(json.loads((ROOT / "results" / "results.json").read_text()))
    cpath = ROOT / "results" / "confirmation.json"
    if cpath.exists():
        d["confirmation"] = json.loads(cpath.read_text()) | {"notes": CONFIRM_NOTES}
    blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "report" / "template.html").read_text().replace("/*DATA*/", blob)
    out = ROOT / "report" / "index.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
