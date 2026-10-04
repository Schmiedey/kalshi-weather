"""Static HTML dashboard of the paper-trading ledger (no JavaScript, no dependencies).

The GitHub workflow regenerates it every hour and publishes it with GitHub Pages.
"""
from __future__ import annotations

import html
import math
from collections import defaultdict
from datetime import datetime, timezone

from .longshot import VARIANTS

# Backtest reference (180 days, 7 cities, after fees): cents per contract and 90% CI.
BACKTEST = {"longshot_noon": (0.48, 0.03, 0.94), "longshot_3pm": (0.83, 0.37, 1.27),
            "longshot_5pm": (1.09, 0.70, 1.47), "longshot_7pm": (1.02, 0.63, 1.38),
            "longshot_9pm": (0.83, 0.37, 1.27), "longshot_11pm": (0.55, 0.07, 1.01)}
TRADES_NEEDED = 1500          # rough count before a ~0.5c edge is distinguishable from zero
SLOTS = 8                     # categorical palette slots, assigned in fixed order

CSS = """
:root{color-scheme:light;--surface-0:#f5f4f1;--surface-1:#fcfcfb;--border:#e4e3df;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#77766f;--grid:#ebeae6;--zero:#b9b8b1;
--good:#0a7f0a;--bad:#c43b3a;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#4a3aa7;--s8:#e34948}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface-0:#121211;
--surface-1:#1a1a19;--border:#2e2e2c;--text-primary:#ffffff;--text-secondary:#c3c2b7;--text-muted:#94938a;
--grid:#2a2a28;--zero:#55544f;--good:#3fbf3f;--bad:#f07777;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--surface-0:#121211;--surface-1:#1a1a19;--border:#2e2e2c;
--text-primary:#ffffff;--text-secondary:#c3c2b7;--text-muted:#94938a;--grid:#2a2a28;--zero:#55544f;
--good:#3fbf3f;--bad:#f07777;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;
--s6:#008300;--s7:#9085e9;--s8:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}
.sub{color:var(--text-secondary);margin:0}.muted{color:var(--text-muted);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.tile,.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .k{color:var(--text-secondary);font-size:13px}.tile .v{font-size:26px;font-weight:600;margin-top:2px;
font-variant-numeric:tabular-nums}
.card{padding:16px;overflow-x:auto}
svg{display:block;width:100%;height:auto}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0 0 10px;padding:0;list-style:none;font-size:13px;
color:var(--text-secondary)}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
th{color:var(--text-secondary);font-weight:500;font-size:13px}
th:first-child,td:first-child{text-align:left}
.pos{color:var(--good)}.neg{color:var(--bad)}
.bar{background:var(--grid);border-radius:4px;height:8px;min-width:80px;overflow:hidden}
.bar>span{display:block;height:100%;background:var(--text-muted);border-radius:4px}
.note{color:var(--text-secondary);font-size:14px;max-width:760px}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _money(x: float) -> str:
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def _signed(x: float, fmt: str = "{:.2f}") -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + fmt.format(abs(x))


def _cls(x: float) -> str:
    return "pos" if x > 0 else "neg" if x < 0 else ""


def variant_stats(rows: list[dict]) -> dict[str, dict]:
    """Per strategy: counts, P&L and edge vs market (P&L minus the no-edge baseline)."""
    out = {}
    by = defaultdict(list)
    for r in rows:
        if r["status"] in ("open", "settled"):       # resting/expired maker orders never traded
            by[r["strategy"]].append(r)
    for name, rs in by.items():
        settled = [r for r in rs if r["status"] == "settled"]
        contracts = sum(r["contracts"] for r in settled)
        pnl = sum(r["pnl"] for r in settled)
        baseline = sum((r["prob"] - r["price"]) * r["contracts"] - r["fee"] for r in settled)
        cost = sum(r["price"] * r["contracts"] + r["fee"] for r in settled)
        out[name] = {
            "trades": len(rs), "open": len(rs) - len(settled), "settled": len(settled),
            "losses": sum(1 for r in settled if r["side"] != r["result"]),
            "pnl": pnl, "edge": pnl - baseline, "cost": cost,
            "cents": 100 * pnl / contracts if contracts else None,
        }
    return out


def pending_stats(rows: list[dict]) -> dict[str, dict]:
    """Open (unsettled) trades per strategy: money at risk and profit if every one wins.

    Nothing here is profit yet. Each trade wins only if its bracket misses.
    """
    out = {}
    for r in rows:
        if r["status"] != "open":
            continue
        o = out.setdefault(r["strategy"], {"trades": 0, "at_risk": 0.0, "if_all_win": 0.0})
        o["trades"] += 1
        o["at_risk"] += r["price"] * r["contracts"] + r["fee"]
        o["if_all_win"] += (1 - r["price"]) * r["contracts"] - r["fee"]
    return out


def _pending_section(pend: dict[str, dict]) -> str:
    if not pend:
        return ""
    h = ["""<h2>Open bets (not profit yet)</h2><div class="card"><table><thead><tr><th>Strategy</th>
<th>Open trades</th><th>At risk</th><th>Profit if all win</th></tr></thead><tbody>"""]
    for n in sorted(pend):
        o = pend[n]
        h.append(f'<tr><td>{_esc(n)}</td><td>{o["trades"]}</td><td>${o["at_risk"]:,.2f}</td>'
                 f'<td class="pos">+${o["if_all_win"]:,.2f}</td></tr>')
    t = {k: sum(o[k] for o in pend.values()) for k in ("trades", "at_risk", "if_all_win")}
    h.append(f'<tr><td><b>Total</b></td><td>{t["trades"]}</td><td>${t["at_risk"]:,.2f}</td>'
             f'<td class="pos">+${t["if_all_win"]:,.2f}</td></tr></tbody></table>'
             '<p class="muted">These bets have not settled. Each wins only if its bracket misses; a single miss '
             'costs about 97¢ per contract, roughly ten times what a win earns.</p></div>')
    return "".join(h)


def _chart(rows: list[dict], names: list[str], color: dict[str, str]) -> str:
    """Cumulative P&L per variant by settlement time, as inline SVG."""
    series = {}
    for n in names:
        pts, total = [], 0.0
        for r in sorted((r for r in rows if r["strategy"] == n and r["status"] == "settled"),
                        key=lambda r: r["settled_at"]):
            total += r["pnl"]
            pts.append((datetime.fromisoformat(r["settled_at"]).timestamp(), total, r))
        if pts:
            series[n] = pts
    if not series:
        return ('<p class="muted" style="margin:40px 0;text-align:center">No settled trades yet. '
                'Trades settle the morning after the event day, so the chart starts filling in '
                'about a day after the first trades.</p>')
    W, H, L, R, T, B = 920, 320, 64, 16, 12, 34
    xs = [p[0] for s in series.values() for p in s]
    ys = [p[1] for s in series.values() for p in s] + [0.0]
    x0, x1 = min(xs), max(xs)
    if x1 == x0:
        x0, x1 = x0 - 43200, x1 + 43200
    y0, y1 = min(ys), max(ys)
    raw = max((y1 - y0) / 4, 0.25)                         # round tick step: 1, 2 or 5 x 10^k
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    y0, y1 = math.floor(y0 / step) * step, math.ceil(y1 / step) * step
    ticks = [y0 + i * step for i in range(int(round((y1 - y0) / step)) + 1)]
    sx = lambda x: L + (x - x0) / (x1 - x0) * (W - L - R)
    sy = lambda y: T + (y1 - y) / (y1 - y0) * (H - T - B)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Cumulative paper P&L by variant">']
    for v in ticks:                                         # recessive horizontal grid
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" '
                   f'stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{L - 8}" y="{sy(v) + 4:.1f}" text-anchor="end" font-size="12" '
                   f'fill="var(--text-muted)">{_esc(_money(v))}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" '
               f'stroke="var(--zero)" stroke-width="1"/>')
    for i in range(5):                                      # date ticks
        t = x0 + (x1 - x0) * i / 4
        label = datetime.fromtimestamp(t, timezone.utc).strftime("%b %d")
        anchor = "start" if i == 0 else "end" if i == 4 else "middle"
        out.append(f'<text x="{sx(t):.1f}" y="{H - 10}" text-anchor="{anchor}" font-size="12" '
                   f'fill="var(--text-muted)">{label}</text>')
    for n, pts in series.items():
        d = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y, _) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{color[n]}" stroke-width="2" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y, r in pts:                                 # hover targets with native tooltips
            tip = (f"{n} · {r['settled_at'][:10]} · {r['ticker']} {r['side'].upper()} → {r['result']}: "
                   f"{_money(r['pnl'])} (total {_money(y)})")
            out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="7" fill="transparent">'
                       f'<title>{_esc(tip)}</title></circle>')
        x, y, _ = pts[-1]
        out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" fill="{color[n]}" '
                   f'stroke="var(--surface-1)" stroke-width="2"/>')
    out.append("</svg>")
    return "".join(out)


STRATEGIES = list(VARIANTS) + ["maker_5pm", "poly_longshot"]
BACKTEST["maker_5pm"] = (2.15, 1.37, 2.80)
BACKTEST["poly_longshot"] = (0.57, 0.16, 0.91)      # assumed 0.5c slippage; live asks ~0.3c worse


def _brain_section(brain: list[dict]) -> str:
    if not brain:
        return ""
    h = ["""<h2>Brain</h2><div class="card"><table><thead><tr><th>Strategy</th><th>Status</th>
<th>Settled</th><th>Edge estimate ¢</th><th>P(edge &gt; 0)</th><th>P(best)</th><th>Next size</th>
</tr></thead><tbody>"""]
    for b in brain:
        h.append(f'<tr><td>{_esc(b["strategy"])}</td><td>{_esc(b["status"])}</td><td>{b["settled"]}</td>'
                 f'<td>{_signed(b["post_c"])} ± {b["post_sd_c"]:.2f}</td><td>{b["p_pos"]:.0%}</td>'
                 f'<td>{b["p_best"]:.0%}</td><td>{b["size"]}</td></tr>')
    h.append('</tbody></table><p class="muted">Skeptical estimate: starts at 0 ± 1¢ and moves only as '
             'settled trades come in. A strategy is stopped automatically if P(edge &gt; 0) falls below 10% '
             'after 200 trades. P(best) shifts size toward the likely-best strategy.</p></div>')
    return "".join(h)


def _etf_section(etf: list[dict]) -> str:
    if not etf:
        return ""
    import json as _json
    last = etf[-1]
    held = {k: v for k, v in _json.loads(etf[-1]["weights"] or "{}").items() if v}
    h = [f"""<h2>ETF trend portfolio (paper)</h2><div class="card"><p class="note">Holds each of SPY, EFA,
IEF, GLD, DBC only while above its 10-month average. Backtest 2008–2026: 5.3% a year with an 11%
worst drop (buy-and-hold: 6.3%, 31%). Paper NAV <b>${last['nav']:,.2f}</b> as of {last['month']};
holding {_esc(', '.join(held) or 'cash')}.</p><table><thead><tr><th>Month</th><th>NAV</th></tr></thead><tbody>"""]
    for r in etf[-12:][::-1]:
        h.append(f'<tr><td>{_esc(r["month"])}</td><td>${r["nav"]:,.2f}</td></tr>')
    h.append("</tbody></table></div>")
    return "".join(h)


def _lab_section(lab: list[dict]) -> str:
    if not lab:
        return ""
    passed = sum(1 for e in lab if e.get("passed"))
    lab = [e | {"passed": e.get("passed", False)} for e in lab]
    h = [f"""<h2>Lab</h2><div class="card"><p class="note">{len(lab)} strategy tests on record, {passed}
passed. Every test is kept, so a pass is read against how many ideas were tried.</p><table><thead><tr>
<th>#</th><th>Test</th><th>Period</th><th>Verdict</th></tr></thead><tbody>"""]
    for e in lab[::-1][:15]:
        v = "pending" if e.get("passed") is None else "pass" if e["passed"] else "fail"
        h.append(f'<tr><td>{e["id"]}</td><td>{_esc(e["name"])}</td><td>{_esc(e.get("period", ""))}</td>'
                 f'<td class="{ {"pass": "pos", "fail": "neg"}.get(v, "")}">{v}</td></tr>')
    h.append("</tbody></table></div>")
    return "".join(h)


# Every idea tested so far, in plain words: (idea, verdict, detail). Updated as lab results come in.
RESEARCH = [
    ("Weather forecast model (GFS, ECMWF, ICON)", "rejected",
     "Kalshi prices were more accurate than the forecasts (Brier 0.106 vs 0.129)."),
    ("Longshot: sell 1–4¢ brackets the day before (7 big cities)", "paper trading",
     "+0.5 to +1.1¢ per contract after fees over 180 days; positive in both halves and every city."),
    ("Maker longshot: post an order 1¢ better, wait for a fill", "paper trading",
     "+2.15¢ per contract in development data; final exam +1.0¢ on 65 trades, too few to be sure."),
    ("Longshot on the 41 other Kalshi temperature markets", "rejected",
     "Lost 0.6–0.7¢ per contract (11,575 trades). Those markets are thin: many 1–4¢ bids have no "
     "real seller behind them (asks of 65–95¢)."),
    ("Same, only where the ask is 10¢ or less (longshot41_5pm)", "forward test",
     "Idea came from the failure above, so it is judged only on paper trades from now on."),
    ("Polymarket longshot", "forward test",
     "Backtest +0.57¢ (CI +0.16 to +0.91) with an assumed cost; real asks are ~0.3¢ worse, so the "
     "edge may be near zero. Paper-trading at real asks (poly_longshot)."),
    ("Machine learning on market prices (calibration, boosting)", "rejected",
     "All 48 markets: slightly better Brier than the market (0.1041 vs 0.1056) but no profit after "
     "costs (CI includes zero, first half negative)."),
    ("Forecast/market blend, favorites, bracket arbitrage", "rejected", "No edge after fees."),
    ("Day-of trading with live station observations", "rejected",
     "Brackets already ruled out by the weather never had buyers to sell to."),
    ("Kalshi vs Polymarket arbitrage", "not possible",
     "The two sites settle on different weather stations, so price gaps are not free money."),
    ("ETF trend (hold only what is above its 10-month average)", "paper trading",
     "2008–2026: 5.3% a year with an 11% worst drop, vs 6.3% and 31% for buy-and-hold."),
]


def maker_stats(rows: list[dict], strategy: str = "maker_5pm") -> dict:
    m = [r for r in rows if r["strategy"] == strategy]
    filled = sum(1 for r in m if r["status"] in ("open", "settled"))
    expired = sum(1 for r in m if r["status"] == "expired")
    return {"resting": sum(1 for r in m if r["status"] == "resting"), "filled": filled,
            "expired": expired, "fill_rate": filled / (filled + expired) if filled + expired else None}


def collector_health(root: str, now: datetime | None = None) -> dict | None:
    """Hourly snapshots stored under `root`/archive, and a look inside the newest one."""
    import glob
    import gzip
    import json
    import os
    files = sorted(glob.glob(os.path.join(root, "archive", "*", "*.jsonl.gz")))
    if not files:
        return None

    def ts(p: str) -> datetime:
        day = os.path.basename(os.path.dirname(p))
        return datetime.strptime(day + os.path.basename(p)[:4], "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)

    with gzip.open(files[-1], "rt") as f:
        rows = [json.loads(line) for line in f]
    last = ts(files[-1])
    return {"snapshots": len(files), "days": len({os.path.dirname(p) for p in files}),
            "first": ts(files[0]), "last": last,
            "age_h": ((now or datetime.now(timezone.utc)) - last).total_seconds() / 3600,
            "markets": len(rows),
            "series": len({(r.get("event_ticker") or "").split("-")[0] for r in rows} - {""}),
            "with_bid": sum(1 for r in rows if float(r.get("yes_bid_dollars") or 0) > 0)}


def _maker_section(m: dict) -> str:
    rate = "—" if m["fill_rate"] is None else f'{m["fill_rate"]:.0%}'
    return f"""<h2>Maker orders (maker_5pm)</h2><div class="card"><table><thead><tr><th>Waiting for a fill</th>
<th>Filled</th><th>Expired unfilled</th><th>Fill rate</th></tr></thead><tbody><tr><td>{m["resting"]}</td>
<td>{m["filled"]}</td><td>{m["expired"]}</td><td>{rate}</td></tr></tbody></table>
<p class="muted">Instead of paying the asking price, this strategy posts its own order 1¢ better and waits.
An order only counts as filled if a real trade on Kalshi reaches its price before the market closes;
unfilled orders expire and cost nothing.</p></div>"""


def _lip_section(lip: list | None) -> str:
    if not lip:
        return ""
    body = "".join(
        f'<tr><td>{r["strategy"]}</td><td>{r["resting"]}</td><td>${r["capital"]:,.0f}</td>'
        f'<td>${r["rewards_24h"]:,.2f}</td><td>${r["rewards"]:,.2f}</td><td>{r["fills"]}</td>'
        f'<td class="{"pos" if r["fill_pnl"] >= 0 else "neg"}">${r["fill_pnl"]:+,.2f}</td></tr>' for r in lip)
    return f"""<h2>Liquidity rewards (paper)</h2><div class="card"><table><thead><tr><th>Strategy</th>
<th>Resting orders</th><th>Cash tied up</th><th>Rewards, last 24h</th><th>Rewards, total</th><th>Fills</th>
<th>Settled fill P&amp;L</th></tr></thead><tbody>{body}</tbody></table>
<p class="muted">Kalshi pays a reward pool to resting orders, but only while both sides of a market's book
are deep enough. The thin side is usually a near-certain loser nobody bids for; <b>lip_1c</b> rests a
1¢ bid there (about its fair value, at most $10 at risk per market). Rewards are estimated hourly from the
live book under Kalshi's published scoring; fills come from real trades. Paper only: it cannot prove
Kalshi would pay, and Kalshi can change the rules or revoke rewards at any time.</p></div>"""


def _collector_section(c: dict | None, data_url: str = "") -> str:
    if not c:
        return ""
    fresh = "pos" if c["age_h"] < 3 else "neg"
    link = f' · <a href="{_esc(data_url)}">raw data</a>' if data_url else ""
    return f"""<h2>Data collector</h2><div class="card"><table><thead><tr><th>Snapshots</th><th>Days</th>
<th>Last snapshot (UTC)</th><th>Markets in last</th><th>With a YES bid</th><th>Series</th></tr></thead><tbody>
<tr><td>{c["snapshots"]:,}</td><td>{c["days"]}</td><td class="{fresh}">{c["last"].strftime("%Y-%m-%d %H:%M")}
({c["age_h"]:.1f}h ago)</td><td>{c["markets"]}</td><td>{c["with_bid"]}</td><td>{c["series"]}</td></tr>
</tbody></table><p class="muted">Every hour, prices for every open Kalshi daily temperature market are saved,
since {c["first"].strftime("%Y-%m-%d")}. This builds a private history that future tests can use{link}.</p></div>"""


def _research_section(research: list[tuple[str, str, str]]) -> str:
    cls = {"paper trading": "pos", "forward test": "pos", "rejected": "neg", "not possible": "neg"}
    h = ["""<h2>What has been tested</h2><div class="card"><table><thead><tr><th>Idea</th><th>Verdict</th>
<th style="text-align:left">Evidence</th></tr></thead><tbody>"""]
    for idea, verdict, detail in research:
        h.append(f'<tr><td>{_esc(idea)}</td><td class="{cls.get(verdict, "")}">{_esc(verdict)}</td>'
                 f'<td style="text-align:left;white-space:normal">{_esc(detail)}</td></tr>')
    h.append("</tbody></table></div>")
    return "".join(h)


def render(rows: list[dict], now: datetime | None = None, repo_url: str = "",
           brain: list[dict] | None = None, etf: list[dict] | None = None,
           lab: list[dict] | None = None, collector: dict | None = None,
           research: list | None = None, lip: list | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    stats = variant_stats(rows)
    names = STRATEGIES + sorted(n for n in stats if n not in STRATEGIES)
    color = {n: f"var(--s{i % SLOTS + 1})" for i, n in enumerate(names)}
    long_rows = [r for r in rows if r["strategy"] in STRATEGIES and r["status"] in ("open", "settled")]
    settled = [r for r in long_rows if r["status"] == "settled"]
    first = min((r["opened_at"] for r in rows), default=None)
    days = (now - datetime.fromisoformat(first)).days + 1 if first else 0
    best = max((n for n in STRATEGIES if stats.get(n, {}).get("settled")),
               key=lambda n: stats[n]["edge"], default=None)

    tiles = [("Paper trades", f"{len(long_rows):,}"), ("Settled", f"{len(settled):,}"),
             ("Days running", f"{days}"),
             ("Leading variant", best.replace("longshot_", "") if best else "—")]
    h = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kalshi Weather Paper Trading</title><style>{CSS}</style></head><body><main>
<h1>Kalshi weather · paper trading</h1>
<p class="sub">A self-running research and paper-trading system for Kalshi daily temperature markets.
Main strategy: bet against brackets priced at 1–4¢ the day before. Fake money, real prices.</p>
<p class="muted">Updated {now.strftime('%Y-%m-%d %H:%M UTC')} · 10 contracts per trade</p>
<div class="tiles">"""]
    for k, v in tiles:
        h.append(f'<div class="tile"><div class="k">{_esc(k)}</div><div class="v">{_esc(v)}</div></div>')
    h.append('</div><h2>Cumulative P&amp;L by variant</h2><div class="card"><ul class="legend">')
    for n in STRATEGIES:
        h.append(f'<li><span class="sw" style="background:{color[n]}"></span>{_esc(n.replace("longshot_", ""))}</li>')
    h.append(f'</ul>{_chart(rows, STRATEGIES, color)}</div>')

    h.append("""<h2>Variants</h2><div class="card"><table><thead><tr><th>Variant</th><th>Trades</th>
<th>Open</th><th>Settled</th><th>Losses</th><th>P&amp;L</th><th>Edge vs market</th><th>¢ / contract</th>
<th>Backtest ¢</th><th>Progress to 1,500</th></tr></thead><tbody>""")
    for n in names:
        s = stats.get(n, {"trades": 0, "open": 0, "settled": 0, "losses": 0, "pnl": 0.0,
                          "edge": 0.0, "cents": None})
        bt = BACKTEST.get(n)
        pct = min(100, 100 * s["settled"] / TRADES_NEEDED)
        cents = "—" if s["cents"] is None else f'<span class="{_cls(s["cents"])}">{_signed(s["cents"])}</span>'
        h.append(f'<tr><td><span class="sw" style="background:{color[n]}"></span>{_esc(n)}</td>'
                 f'<td>{s["trades"]}</td><td>{s["open"]}</td><td>{s["settled"]}</td><td>{s["losses"]}</td>'
                 f'<td class="{_cls(s["pnl"])}">{_money(s["pnl"])}</td>'
                 f'<td class="{_cls(s["edge"])}">{_money(s["edge"])}</td><td>{cents}</td>'
                 f'<td>{_signed(bt[0]) if bt else "—"}</td>'
                 f'<td><div class="bar" title="{s["settled"]} of {TRADES_NEEDED}">'
                 f'<span style="width:{pct:.1f}%"></span></div></td></tr>')
    h.append("</tbody></table></div>")

    h.append(_pending_section(pending_stats(rows)))
    h.append(_brain_section(brain or []))
    h.append(_maker_section(maker_stats(rows)))
    h.append(_lip_section(lip))
    recent = sorted((r for r in rows if r["status"] != "expired"), key=lambda r: r["opened_at"], reverse=True)[:25]
    h.append("""<h2>Recent trades</h2><div class="card"><table><thead><tr><th>Opened (UTC)</th>
<th>Variant</th><th>City</th><th>Bracket</th><th>Side</th><th>Price</th><th>Qty</th><th>Status</th>
<th>P&amp;L</th></tr></thead><tbody>""")
    if not recent:
        h.append('<tr><td colspan="9" class="muted">No trades yet.</td></tr>')
    for r in recent:
        pnl = "" if r["pnl"] is None else f'<span class="{_cls(r["pnl"])}">{_money(r["pnl"])}</span>'
        status = r["status"] if r["status"] in ("open", "resting") else "won" if r["side"] == r["result"] else "lost"
        h.append(f'<tr><td>{_esc(r["opened_at"][:16].replace("T", " "))}</td><td>{_esc(r["strategy"])}</td>'
                 f'<td>{_esc(r["city"].upper())}</td><td>{_esc(r["ticker"])}</td><td>{_esc(r["side"].upper())}</td>'
                 f'<td>${r["price"]:.2f}</td><td>{r["contracts"]}</td><td>{status}</td><td>{pnl}</td></tr>')
    h.append("</tbody></table></div>")

    h.append(_etf_section(etf or []))
    h.append(_research_section(RESEARCH if research is None else research))
    h.append(_lab_section(lab or []))
    h.append(_collector_section(collector, repo_url + "/tree/data" if repo_url else ""))
    h.append(f"""<h2>How to read this</h2><div class="note">
<p><b>Edge vs market</b> is P&amp;L minus what the market prices implied (the fees you would pay
with zero edge). Above zero means the strategy beats the market; it's the number that matters.</p>
<p>Each win earns 1–4¢ per contract and each loss costs about 97¢, so results jump around.
A real edge of about half a cent only becomes clear after roughly {TRADES_NEEDED:,} settled trades
per variant. Before that, a good or bad week is mostly luck.</p>
<p>The variants trade mostly the same brackets at different hours, so they move together.
They show the best time of day to trade; real money would use one of them.</p>
{f'<p class="muted"><a href="{_esc(repo_url)}">Source code and ledger</a></p>' if repo_url else ''}
</div></main></body></html>""")
    return "".join(h)


def _lip_rows(db) -> list:
    from .lip import paper_summary
    rows = paper_summary(db)
    return rows if any(r["resting"] or r["rewards"] or r["fills"] for r in rows) else []


def build(ledger_path: str, out_path: str, repo_url: str = "", store: str | None = None) -> int:
    import os
    from .brain import Brain, p_best
    from .lab import load
    from .ledger import Ledger
    led = Ledger(ledger_path)
    rows = [dict(r) for r in led.db.execute("SELECT * FROM trades")]
    br = Brain(led)
    names = STRATEGIES + sorted({r["strategy"] for r in rows} - set(STRATEGIES))
    reviews = {n: br.review(n) for n in names}
    pb = p_best({n: e for n, (st, e, _) in reviews.items() if st != "killed"})
    brain = [{"strategy": n, "status": st, "settled": e.trades, "post_c": 100 * e.post_mean,
              "post_sd_c": 100 * e.post_sd, "p_pos": e.p_positive, "p_best": pb.get(n, 0.0),
              "size": br.sized(n, 0.97, STRATEGIES)} for n, (st, e, _) in reviews.items()]
    try:
        etf = [dict(r) for r in led.db.execute("SELECT * FROM etf_nav ORDER BY month")]
    except Exception:
        etf = []
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(render(rows, repo_url=repo_url, brain=brain, etf=etf, lab=load(),
                       collector=collector_health(store) if store else None,
                       lip=_lip_rows(led.db)))
    return len(rows)
