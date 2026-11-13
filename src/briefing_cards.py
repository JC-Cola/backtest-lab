"""Picture cards (PNG, 1080x1350, phone portrait) and captions for the Telegram briefings.

    render(data) -> PNG bytes       caption(data) -> 2-3 line HTML caption (<= 1024 characters)

`data` is the plain dict from briefing.morning_data() / evening_data(). White background, one accent blue, green for
gains and red for losses, big numbers, few words. Everything is drawn on one pixel-coordinate canvas (x right, y down)
plus small matplotlib axes for the fan charts. Raises on bad data: the caller falls back to the text message.
"""
from __future__ import annotations

import html
import io
import textwrap
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402
import matplotlib.dates as mdates   # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle   # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator   # noqa: E402

from news_gappers import fmt_vol   # noqa: E402
from report import NY   # noqa: E402

W, H, DPI = 1080, 1350, 150
BLUE, GREEN, RED, AMBER = "#2563EB", "#16A34A", "#DC2626", "#D97706"
INK, GRAY, LINE, SOFT = "#111827", "#6B7280", "#E5E7EB", "#F3F4F6"
LABEL = {"intraday": "Noise band", "qlib": "qlib", "ta": "TradingAgents"}
MX = 30                      # side margin (px)
PILLS_MAX = 8                # pills / chips shown per row group before "+k more"
GAUGE_BAR = 0.95             # score a research idea needs
OPEN_MIN, CLOSE_MIN = 570, 960   # 09:30 and 16:00 in minutes


# ---------- small formatting helpers ----------
def usd(x, sign=False):
    return f"{'+' if sign and x > 0 else ''}{'-' if x < 0 else ''}${abs(x):,.2f}"


def pct(x, d=2):
    return f"{x * 100:+.{d}f}%"


def tone(x):
    return GREEN if x >= 0 else RED


def day_label(day):
    return f"{day.day} {day:%b}"


def qty(x):
    return f"{x:,.4f}".rstrip("0").rstrip(".")


def cut(s, n):
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


# ---------- canvas ----------
class Card:
    def __init__(self, h=H):
        self.h = h
        self.fig = plt.figure(figsize=(W / DPI, h / DPI), dpi=DPI, facecolor="white")
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W); self.ax.set_ylim(h, 0); self.ax.axis("off")
        self.r = self.fig.canvas.get_renderer()

    def text(self, x, y, s, size=13, color=INK, weight="normal", ha="left", va="center"):
        return self.ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va)

    def width(self, s, size, weight="normal"):
        t = self.text(0, 0, s, size, weight=weight)
        w = t.get_window_extent(self.r).width
        t.remove()
        return w

    def box(self, x, y, w, h, fc=SOFT, ec="none", r=14, lw=1.0):
        self.ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw))

    def title(self, y, s):
        self.text(MX, y, s, 17, INK, "bold")

    def pills(self, x, y, items, maxw=W - 2 * MX, h=42, gap=10, size=13):
        """Flow rounded pills left to right, wrapping at maxw. items: lists of (text, color, weight) parts.
        Returns the y below the last row."""
        cx, cy = x, y
        for parts in items:
            ws = [self.width(t, size, wt) for t, _, wt in parts]
            pw = sum(ws) + 8 * (len(parts) - 1) + 28
            if cx > x and cx + pw > x + maxw:
                cx, cy = x, cy + h + gap
            self.box(cx, cy, pw, h, SOFT, r=h / 2)
            tx = cx + 14
            for (t, c, wt), w in zip(parts, ws):
                self.text(tx, cy + h / 2, t, size, c, wt)
                tx += w + 8
            cx += pw + gap
        return cy + h

    def axes(self, x, y, w, h):
        return self.fig.add_axes([x / W, 1 - (y + h) / self.h, w / W, h / self.h])

    def png(self):
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=DPI, facecolor="white")
        plt.close(self.fig)
        return buf.getvalue()


# ---------- shared pieces ----------
def tiles(c, d, y, chg_label, key):
    """One tile per account (Noise band, plus qlib / TradingAgents when present), side by side: value, change (coloured),
    since start. A lone Noise band tile is full width."""
    names = list(d["accounts"])
    n = len(names)
    w = (W - 2 * MX - 20 * (n - 1)) / n
    big, mid = (32, 16) if n < 3 else (20, 13)
    for i, name in enumerate(names):
        a, x = d["accounts"][name], MX + i * (w + 20)
        c.box(x, y, w, 220, "#F8FAFC", LINE)
        c.text(x + 24, y + 36, LABEL[name], 16, BLUE, "bold")
        if a["equity"] is None:
            c.text(x + 24, y + 105, "no numbers", 22, GRAY)
            continue
        c.text(x + 24, y + 105, usd(a["equity"]), big, INK, "bold")
        chg = a[key]
        if chg is None:
            c.text(x + 24, y + 158, f"{chg_label} n/a", 15, GRAY)
        else:
            c.text(x + 24, y + 158, f"{usd(chg[0], True)}  {pct(chg[1])}", mid, tone(chg[0]), "bold")
        c.text(x + 24, y + 196, f"since start {pct(a['since'])}", 12, GRAY)


def header(c, d, word):
    c.text(MX, 45, word, 22, INK, "bold")
    c.text(W - MX, 45, day_label(d["day"]), 17, GRAY, ha="right")


# ---------- evening ----------
def fan_chart(c, x, y, w, h, a):
    ax = c.axes(x + 78, y, w - 90, h - 44)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(LINE)
    ax.tick_params(colors=GRAY, labelsize=12, length=0)
    ax.axhline(0, color=LINE, lw=1, ls=(0, (3, 3)))
    ys = list(a["path"]) + [0.0]
    f = a["fan"]
    if f:
        ax.fill_between(f["days"], f["lo"], f["hi"], color=BLUE, alpha=0.16, lw=0)
        ax.plot(f["days"], f["med"], color=BLUE, lw=1.2)
        ys += f["lo"] + f["hi"]
    if a["path"]:
        xs, vals = list(range(len(a["path"]) + 1)), [0.0] + list(a["path"])
        ax.plot(xs, vals, color=INK, lw=3, solid_capstyle="round")
        ax.scatter([xs[-1]], [vals[-1]], s=90, color=tone(vals[-1]), zorder=5, edgecolor="white", linewidth=1.5)
    pad = (max(ys) - min(ys)) * 0.12 or 0.005
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_xlim(0, max(f["days"][-1] if f else 0, len(a["path"]), 5))
    ax.yaxis.set_major_locator(MaxNLocator(3))
    ax.xaxis.set_major_locator(MaxNLocator(4, integer=True))
    span = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 100
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v * 100:+.{0 if span > 6 else 1}f}%"))


def verdict_text(a):
    n, v = a["days"], a["verdict"]
    return {"early": (f"too early to tell ({n} day{'' if n == 1 else 's'})", GRAY), "in line": ("in line with the backtest", GREEN),
            "above normal": ("above the backtest range", GREEN), "below normal": ("below the backtest range", RED)
            }.get(v, ("backtest unavailable", GRAY))


def evening_backtest(c, d, y):
    c.title(y, "Matches the backtest?")
    c.text(MX, y + 32, "shaded: backtest 5th-95th percentile · dark line: live, by trading day", 12, GRAY)
    a, x = d["accounts"]["intraday"], MX
    c.text(x + 4, y + 78, LABEL["intraday"], 15, BLUE, "bold")
    if a["equity"] is None:
        c.text(x + 4, y + 190, "no live numbers", 14, GRAY)
        return y + 450
    fan_chart(c, x, y + 105, W - 2 * MX, 250, a)
    s, col = verdict_text(a)
    c.text(x + 4, y + 385, s, 14, col, "bold")
    return y + 450


def timeline(c, trades, trips, y):
    """09:30-16:00 axis with a buy (blue up) / sell (dark down) marker per fill and the P/L of each closed round trip."""
    x0, x1 = 150, W - 50
    px = lambda t: x0 + (min(max(t.astimezone(NY).hour * 60 + t.astimezone(NY).minute, OPEN_MIN), CLOSE_MIN) - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN) * (x1 - x0)
    for hh, mm in ((9, 30), (11, 0), (13, 0), (15, 0), (16, 0)):
        x = x0 + ((hh * 60 + mm) - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN) * (x1 - x0)
        c.text(x, y, f"{hh}:{mm:02d}", 12, GRAY, ha="center")
    syms = sorted({t["symbol"] for t in trades}, key=lambda s: -sum(t["symbol"] == s for t in trades))[:3]
    ry = y + 50
    for s in syms:
        c.ax.plot([x0, x1], [ry + 6, ry + 6], color=LINE, lw=1.5, zorder=1)
        c.text(MX, ry + 6, s, 14, INK, "bold")
        for t in (t for t in trades if t["symbol"] == s):
            buy = t["side"] == "buy"
            c.ax.scatter([px(t["t"])], [ry + 6], marker="^" if buy else "v", s=150, color=BLUE if buy else INK, zorder=3)
        for sym, pl, t in trips:
            if sym == s and t:
                x = px(t)
                c.text(min(x, x1 - 50), ry - 20, usd(pl, True), 13, tone(pl), "bold", ha="center")
        ry += 64
    return ry - 10


def evening_trades(c, d, y):
    c.title(y, "Today's trades")
    it = d["accounts"]["intraday"]
    if not it["trades"]:
        c.text(MX, y + 50, "no trades today", 16, GRAY)
        return y + 90
    y += 42
    c.text(MX, y, f"{LABEL['intraday']}   ▲ buy  ▼ sell", 13, GRAY)
    return timeline(c, it["trades"], it["trips"], y + 34) + 38


def evening_research(c, d, y):
    c.title(y, "Research today")
    r = d["research"]
    top = max(10, r["intraday"]["tested"])
    c.text(285, y + 50, "ideas tested", 13, GRAY)
    c.text(655, y + 50, f"best score (needs {GAUGE_BAR})", 12, GRAY)
    ry = y + 115
    for label, key in (("Intraday agent", "intraday"),):
        s = r[key]
        c.text(MX, ry, label, 14, INK, "bold")
        c.box(285, ry - 10, 280, 20, SOFT, r=10)
        if s["tested"]:
            c.box(285, ry - 10, max(20, 280 * s["tested"] / top), 20, BLUE, r=10)
            c.text(575, ry, str(s["tested"]), 16, INK, "bold")
        else:
            c.text(575, ry, "none", 14, GRAY)
        gx, gw = 655, 370
        c.box(gx, ry - 10, gw, 20, SOFT, r=10)
        if s["best"] is not None:
            frac = min(max(s["best"], 0) / 1.0, 1.0)
            c.box(gx, ry - 10, max(20, gw * frac), 20, GREEN if s["best"] >= GAUGE_BAR else BLUE, r=10)
            c.text(gx, ry + 28, f"{s['best']:.2f}", 13, INK, "bold")
        elif s["tested"]:
            c.text(gx, ry + 28, "no score", 13, GRAY)
        c.ax.plot([gx + gw * GAUGE_BAR] * 2, [ry - 16, ry + 16], color=INK, lw=2)
        ry += 100
    wc = d["waiting_candidates"]
    if wc:
        label = f"{len(wc)} candidate{'' if len(wc) == 1 else 's'} awaiting approval"
        c.box(MX, ry - 6, c.width(label, 14, "bold") + 36, 44, "#FEF3C7", r=22)
        c.text(MX + 18, ry + 16, label, 14, AMBER, "bold")
        ry += 50
    return ry


def evening(c, d):
    header(c, d, "Evening")
    tiles(c, d, 80, "today", "today")
    y = evening_backtest(c, d, 350)
    y = evening_trades(c, d, y + 10)
    return evening_research(c, d, y + 25)


# ---------- morning ----------
def risk_bar(c, y, label, used, limit, text):
    """Bar filled by how much of the limit is used; green below 50%, amber below 80%, red beyond."""
    frac = min(max(used / limit, 0.0), 1.0)
    col = GREEN if frac < 0.5 else AMBER if frac < 0.8 else RED
    c.text(MX, y, label, 13, GRAY)
    c.box(MX, y + 26, 700, 26, SOFT, r=13)
    c.box(MX, y + 26, max(26, 700 * frac), 26, col, r=13)
    c.text(MX + 725, y + 39, text, 15, col, "bold")
    return y + 86


def morning_risk(c, d, y):
    c.title(y, "Risk")
    y += 56
    for name, a in d["accounts"].items():
        if a["kill"] is None:   # shadow systems have no agreed limits
            continue
        if a["equity"] is None:
            c.text(MX, y, f"{LABEL[name]}: no numbers", 14, GRAY)
            y += 40
            continue
        y = risk_bar(c, y, f"{LABEL[name]} · drawdown from peak (stops at -{a['kill'] * 100:.0f}%)", -a["dd"], a["kill"],
                     f"{a['dd'] * 100:.1f}% of -{a['kill'] * 100:.0f}%")
        if a["day_limit"] and a["last"]:
            f = a["last"][1]
            y = risk_bar(c, y, f"{LABEL[name]} · last day (limit -{a['day_limit'] * 100:.0f}%)", -f, a["day_limit"],
                         f"{f * 100:+.1f}% of -{a['day_limit'] * 100:.0f}%")
    return y


def morning_flags(c, d, y):
    flags = d["flags"]
    if not flags:
        c.ax.scatter([MX + 8], [y + 4], s=90, color=GREEN)
        c.text(MX + 30, y + 4, "All clear", 15, GREEN, "bold")
        return y + 40
    for f in flags[:3]:
        lines = textwrap.wrap(f, 50)[:2]
        if len(textwrap.wrap(f, 50)) > 2:
            lines[1] = cut(lines[1] + " …", 50)
        c.ax.scatter([MX + 8], [y + 4], s=90, color=RED)
        for k, ln in enumerate(lines):
            c.text(MX + 30, y + 4 + 30 * k, ln, 14, RED, "bold")
        y += 30 * len(lines) + 20
    if len(flags) > 3:
        c.text(MX + 30, y + 4, f"+{len(flags) - 3} more", 14, GRAY)
        y += 40
    return y


NEWS_WRAP = 55   # characters per headline line at 14 pt


def morning_news(c, d, y):
    """Compact headlines (tag chip + up to 2 lines each, no URLs). Draws nothing when there are none."""
    items = d.get("headlines") or []
    if not items:
        return y
    c.title(y, "Headlines")
    y += 44
    for it in items[:5]:
        lines = textwrap.wrap(it["headline"], NEWS_WRAP)
        if len(lines) > 2:
            lines = [lines[0], cut(" ".join(lines[1:]), NEWS_WRAP)]
        tag = cut(it["tag"].replace("USD", ""), 5)
        c.box(MX, y - 12, 74, 26, SOFT, r=13)
        c.text(MX + 37, y + 1, tag, 11, BLUE, "bold", ha="center")
        for k, ln in enumerate(lines):
            c.text(MX + 92, y + 1 + 31 * k, ln, 14, INK)
        y += 31 * len(lines) + 14
    return y


def morning_gappers(c, d, y):
    """Top pre-market gappers with news (symbol chip, gap, volume, one headline line). Draws nothing when there are none."""
    items = d.get("gappers") or []
    if not items:
        return y
    c.title(y, "Gappers with news")
    y += 44
    for g in items[:5]:
        c.box(MX, y - 12, 74, 26, SOFT, r=13)
        c.text(MX + 37, y + 1, cut(g["symbol"], 5), 11, BLUE, "bold", ha="center")
        c.text(MX + 92, y + 1, f"{g['gap'] * 100:+.1f}%  ·  pre-market vol {fmt_vol(g['pm_vol'])}", 14, tone(g["gap"]), "bold")
        c.text(MX + 92, y + 32, cut(g["headline"], NEWS_WRAP + 5), 14, INK)
        y += 31 * 2 + 14
    return y


def morning(c, d):
    header(c, d, "Morning · before the open")
    tiles(c, d, 80, "last session", "last")
    y = 350
    c.title(y, "Open positions")
    y += 50
    for name, a in d["accounts"].items():
        c.text(MX, y + 4, LABEL[name], 13, GRAY)
        pos = a["positions"][:PILLS_MAX]
        if not pos:
            c.text(MX + 180, y + 4, "none", 14, GRAY)
            y += 56
            continue
        items = [[(p["symbol"], INK, "bold"), (qty(p["qty"]), GRAY, "normal"), (usd(p["pl"], True), tone(p["pl"]), "bold")] for p in pos]
        if len(a["positions"]) > PILLS_MAX:
            items.append([(f"+{len(a['positions']) - PILLS_MAX} more", GRAY, "normal")])
        y = c.pills(MX, y + 28, items) + 34
    y = morning_risk(c, d, y + 40) + 20
    y = morning_flags(c, d, y)
    y = morning_gappers(c, d, y + 14)
    return morning_news(c, d, y + 14)


def render(d):
    """PNG bytes. 1080x1350, taller only when the content does not fit (many positions, trades or warnings)."""
    draw = morning if d["kind"] == "morning" else evening
    end = draw(Card(3000), d)
    plt.close("all")
    c = Card(max(H, int(end) + 40))
    draw(c, d)
    return c.png()


# ---------- balance history (TradingView-style area charts) ----------
NO_HIST = "No balance history yet"


def series(pts):
    """[(New York datetime, equity)] oldest first from [[unix ts, equity], ...]; zero/None values dropped."""
    out = [(datetime.fromtimestamp(t, NY), float(v)) for t, v in (pts or []) if v]
    return sorted(out)


def area_chart(c, x, y, w, h, pts, base, xfmt, step_hours=None):
    """Line + soft fill (green when the last point is at/above `base`, else red), dashed line at `base`, $ labels on
    the right, time labels below. pts: series(); needs >= 2 points (the caller draws the empty message)."""
    ax = c.axes(x, y, w, h)
    xs, ys = [mdates.date2num(t) for t, _ in pts], [v for _, v in pts]
    col = tone(ys[-1] - base)
    lo, hi = min(ys + [base]), max(ys + [base])
    pad = (hi - lo) * 0.12 or max(abs(base) * 0.002, 1.0)
    ax.set_ylim(lo - pad, hi + pad)
    span = xs[-1] - xs[0]
    ax.set_xlim(xs[0] - span * 0.025, xs[-1] + span * 0.01)
    ax.fill_between(xs, ys, lo - pad, color=col, alpha=0.12, lw=0)
    ax.plot(xs, ys, color=col, lw=2.6, solid_capstyle="round")
    ax.axhline(base, color=GRAY, lw=1.1, ls=(0, (4, 4)))
    ax.scatter([xs[-1]], [ys[-1]], s=60, color=col, zorder=5, edgecolor="white", linewidth=1.2, clip_on=False)
    for sp in ("top", "left", "right"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(LINE)
    ax.grid(axis="y", color=SOFT, lw=1)
    ax.set_axisbelow(True)
    ax.yaxis.tick_right()
    ax.tick_params(colors=GRAY, labelsize=11, length=0)
    ax.yaxis.set_major_locator(MaxNLocator(4 if h > 150 else 2))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    if step_hours:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=step_hours, tz=NY))
    else:
        ax.xaxis.set_major_locator(MaxNLocator(3))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: xfmt(mdates.num2date(v, NY))))
    return col


def hour_label(t):
    return f"{t.hour % 12 or 12}{'AM' if t.hour < 12 else 'PM'}"


def balance_panel(c, name, a, y):
    """One account: name, big balance, today's change, the 1D area chart and the 'since start' strip."""
    c.text(MX, y + 24, LABEL[name], 17, BLUE, "bold")
    if a["equity"] is None:
        c.text(MX, y + 80, "no numbers", 22, GRAY)
        return
    c.text(MX, y + 72, usd(a["equity"]), 34, INK, "bold")
    if a["today"]:
        c.text(W - MX, y + 62, f"{usd(a['today'][0], True)}  {pct(a['today'][1])}", 18, tone(a["today"][0]), "bold", ha="right")
        c.text(W - MX, y + 90, "today", 12, GRAY, ha="right")
    cw = W - 2 * MX - 100   # the $ labels sit in the 100 px to the right of the axes
    day, allp = series(a["h1d"]), series(a["hall"])
    if len(day) >= 2:
        area_chart(c, MX, y + 112, cw, 205, day, a["today_base"] or day[0][1], hour_label, 4)
    else:
        c.box(MX, y + 112, cw + 100, 205, "#F8FAFC", LINE)
        c.text(W / 2, y + 215, NO_HIST, 16, GRAY, ha="center")
    c.text(MX, y + 374, "Since start", 13, GRAY, "bold")
    if len(allp) >= 2:
        base = allp[0][1]
        chg = allp[-1][1] - base
        c.text(W - MX, y + 374, f"{usd(chg, True)}  {pct(chg / base)}", 13, tone(chg), "bold", ha="right")
        area_chart(c, MX, y + 394, cw, 66, allp, base, lambda t: f"{t.day} {t:%b}")
    else:
        c.box(MX, y + 394, cw + 100, 66, "#F8FAFC", LINE)
        c.text(W / 2, y + 427, NO_HIST, 14, GRAY, ha="center")


def render_balance(d):
    """PNG, 1080 wide: Noise band panel, then qlib / TradingAgents panels when present. d from briefing.balance_data()."""
    names = list(d["accounts"])
    c = Card(85 + 495 * len(names) + 5)
    header(c, d, "Balance history")
    for i, name in enumerate(names):
        if i:
            c.ax.plot([MX, W - MX], [85 + i * 495 - 23, 85 + i * 495 - 23], color=LINE, lw=1.5)
        balance_panel(c, name, d["accounts"][name], 85 + i * 495)
    return c.png()


def balance_caption(d):
    return f"Balance history · {day_label(d['day'])}"


# ---------- captions ----------
def change_part(a, key):
    return "n/a" if a["equity"] is None or a[key] is None else pct(a[key][1], 1)


def caption(d):
    e = html.escape
    acc = d["accounts"]
    if d["kind"] == "morning":
        head = f"<b>Morning {day_label(d['day'])}</b> · " + " · ".join(f"{LABEL[n]} {change_part(acc[n], 'last')}" for n in acc) + " (last session)"
        f = d["flags"]
        return "\n".join([head, "All clear before the open" if not f else f"{len(f)} warning{'' if len(f) == 1 else 's'} - see the card"])
    head = f"<b>Evening {day_label(d['day'])}</b> · " + " · ".join(f"{LABEL[n]} {change_part(acc[n], 'today')}" for n in acc)
    vs = {n: acc[n]["verdict"] for n in acc if acc[n]["verdict"]}
    if vs and all(v == "early" for v in vs.values()):
        line2 = f"Too early to compare with the backtest ({max(acc[n]['days'] for n in vs)} day{'' if max(acc[n]['days'] for n in vs) == 1 else 's'})"
    elif vs and all(v == "in line" for v in vs.values()):
        line2 = "In line with the backtest"
    else:
        name = {"early": "too early", "in line": "in line", "above normal": "above range", "below normal": "below range", "unavailable": "no backtest"}
        line2 = "Backtest: " + ", ".join(f"{LABEL[n]} {name[v]}" for n, v in vs.items()) if vs else "Backtest: no live numbers"
    r = d["research"]
    n_tr = sum(len(acc[n]["trades"]) for n in acc)
    line3 = f"{n_tr} trade{'' if n_tr == 1 else 's'} · {r['intraday']['tested']} ideas tested"
    if d["waiting_candidates"]:
        line3 += " · candidate awaiting approval"
    lines = [head, e(line2), e(line3)]
    if d.get("reconcile_line"):
        lines.append(e(d["reconcile_line"]))
    if d.get("shadow_line"):
        lines.append(e(d["shadow_line"])[:600])
    return "\n".join(lines)
