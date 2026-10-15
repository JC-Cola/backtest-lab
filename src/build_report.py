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


def main():
    d = slim(json.loads((ROOT / "results" / "results.json").read_text()))
    blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "report" / "template.html").read_text().replace("/*DATA*/", blob)
    out = ROOT / "report" / "index.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
