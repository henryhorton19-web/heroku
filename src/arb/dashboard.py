"""The dashboard: one self-contained HTML file.

**No server, no build step, no second runtime.** The roadmap's source matrix proposed
Next.js, Prisma and MongoDB for what is a read-only view over a local SQLite file;
that is three runtimes to maintain for a page one person looks at. This renders a
single file you open in a browser. If an interactive UI ever earns its maintenance,
this is the thing it replaces, and the queries it is built on stay put.

**The design brief is provenance.** ROADMAP §5: a margin computed from provisional
fees and a margin computed from settlement data must not look identical on screen. So
colour here carries exactly one meaning and no decoration — **teal is measured, amber
is assumed** — and every figure drawn from a placeholder is marked at the point of
reading rather than disclaimed in a footnote nobody scrolls to.

That is also why the placeholder register is a full section rather than an appendix.
For a tool whose entire philosophy is refusing to present an estimate as a
measurement, burying its own list of open assumptions would be a lie told by layout.

Figures are set in a monospace face throughout. Not a stylistic tic: money in a ledger
wants tabular alignment, and a column of right-aligned figures that do not line up is
harder to read than one that does.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, NamedTuple

from arb.books.ledger import AGEING_DAYS
from arb.money import pence_to_decimal
from arb.provenance import PlaceholderStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from arb.books.ledger import CapitalPosition, RealisedTrade
    from arb.books.verticals import Vertical
    from arb.provenance import PlaceholderState

__all__ = ["DashboardData", "render_dashboard"]


class DashboardData(NamedTuple):
    """Everything the page shows. Gathered by the caller so rendering stays pure."""

    generated_at: datetime
    capital: CapitalPosition
    trades: Sequence[RealisedTrade]
    placeholders: Sequence[PlaceholderState]
    verticals: Sequence[Vertical]
    synthetic_trades: int
    """Seeded rows included in the figures. Shown prominently when non-zero: a
    dashboard demonstrating itself with generated data must say so."""


_CSS = """
:root {
  --ink: #16202b;
  --ink-soft: #5b6b7a;
  --ground: #eceff1;
  --card: #ffffff;
  --rule: #d3dbe0;
  --measured: #0f766e;
  --assumed: #b45309;
  --assumed-wash: #fdf6ec;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem;
  background: var(--ground); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 .2rem; letter-spacing: -.01em; }
.sub { color: var(--ink-soft); font-size: .82rem; margin: 0 0 1.75rem; }
section { background: var(--card); border: 1px solid var(--rule); border-radius: 3px;
  padding: 1.1rem 1.25rem; margin-bottom: 1rem; }
h2 { font-size: .7rem; text-transform: uppercase; letter-spacing: .11em;
  color: var(--ink-soft); margin: 0 0 .9rem; font-weight: 600; }
.figs { display: flex; flex-wrap: wrap; gap: 2.25rem; }
.fig { min-width: 8rem; }
.fig .n { font: 600 1.5rem/1.1 ui-monospace, "SF Mono", Menlo, monospace;
  font-variant-numeric: tabular-nums; display: block; }
.fig .l { font-size: .72rem; color: var(--ink-soft); letter-spacing: .03em; }
.measured { color: var(--measured); }
.assumed { color: var(--assumed); border-bottom: 2px dotted var(--assumed); }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th { text-align: left; font-size: .68rem; text-transform: uppercase;
  letter-spacing: .08em; color: var(--ink-soft); font-weight: 600;
  padding: 0 .6rem .45rem 0; border-bottom: 1px solid var(--rule); }
td { padding: .45rem .6rem .45rem 0; border-bottom: 1px solid var(--ground);
  font-variant-numeric: tabular-nums; }
td.n { font-family: ui-monospace, "SF Mono", Menlo, monospace; text-align: right;
  padding-right: 1.2rem; }
th.n { text-align: right; padding-right: 1.2rem; }
.tag { display: inline-block; font-size: .64rem; letter-spacing: .07em;
  text-transform: uppercase; padding: .12rem .4rem; border-radius: 2px;
  font-weight: 600; }
.tag.open { background: var(--assumed-wash); color: var(--assumed); }
.tag.closed { background: #e6f4f1; color: var(--measured); }
.tag.unknown { background: var(--ground); color: var(--ink-soft); }
.legend { display: flex; gap: 1.5rem; flex-wrap: wrap; font-size: .74rem;
  color: var(--ink-soft); margin-bottom: 1.75rem; }
.legend b { font-weight: 600; }
.banner { background: var(--assumed-wash); border: 1px solid #f0d9b5;
  border-left: 3px solid var(--assumed); padding: .7rem .9rem; border-radius: 3px;
  font-size: .82rem; margin-bottom: 1rem; }
.note { color: var(--ink-soft); font-size: .78rem; margin: .85rem 0 0; }
.empty { color: var(--ink-soft); font-size: .85rem; }
@media (max-width: 34rem) { .figs { gap: 1.25rem; } .fig .n { font-size: 1.2rem; } }
"""


def _money(pence: int) -> str:
    return f"£{pence_to_decimal(pence)}"


def _fig(label: str, value: str, *, measured: bool | None = None) -> str:
    """One headline figure. `measured=None` means the question does not apply."""
    cls = "" if measured is None else (" measured" if measured else " assumed")
    return (
        f'<div class="fig"><span class="n{cls}">{escape(value)}</span>'
        f'<span class="l">{escape(label)}</span></div>'
    )


def _capital_section(data: DashboardData) -> str:
    capital = data.capital
    figs = [
        _fig("capital deployed", _money(capital.deployed_pence)),
        _fig("returned by sales", _money(capital.recycled_pence)),
    ]
    if capital.aged_count:
        figs.append(
            _fig(
                f"ageing over {AGEING_DAYS} days ({capital.aged_count})",
                _money(capital.aged_pence),
            )
        )
    return f'<section><h2>Capital</h2><div class="figs">{"".join(figs)}</div></section>'


def _realised_section(data: DashboardData) -> str:
    settled = [t for t in data.trades if t.settled]
    estimated = [t for t in data.trades if not t.settled]
    if not data.trades:
        return (
            "<section><h2>Realised</h2>"
            '<p class="empty">No completed sales yet. Record one with '
            "<code>arb decide --outcome bought</code>.</p></section>"
        )
    settled_net = sum(t.net_pence for t in settled)
    estimated_net = sum(t.net_pence for t in estimated)
    figs = [
        _fig(f"settled ({len(settled)} trades)", _money(settled_net), measured=True),
        _fig(f"estimated ({len(estimated)} trades)", _money(estimated_net), measured=False),
    ]
    return (
        f'<section><h2>Realised margin</h2><div class="figs">{"".join(figs)}</div>'
        '<p class="note">These are shown apart and never added. One is measured '
        "against settlement data, the other is computed from fee rates nobody has "
        "checked; a single total would be neither.</p></section>"
    )


def _stock_section(data: DashboardData) -> str:
    rows = [
        f"<tr><td>{escape(state.value.replace('_', ' '))}</td>"
        f'<td class="n">{count}</td><td class="n">{_money(cost)}</td></tr>'
        for state, count, cost in data.capital.by_state
        if count
    ]
    if not rows:
        return '<section><h2>Stock</h2><p class="empty">No stock recorded.</p></section>'
    return (
        "<section><h2>Stock by lifecycle state</h2><table><thead><tr>"
        '<th>State</th><th class="n">Items</th><th class="n">Cost basis</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
    )


def _verticals_section(data: DashboardData) -> str:
    if not data.verticals:
        return (
            '<section><h2>Verticals</h2><p class="empty">Not enough listings seen yet. '
            "These build up from <code>arb scan</code>; nothing extra is collected."
            "</p></section>"
        )
    rows = []
    for vertical in data.verticals:
        median = _money(vertical.median_net_pence) if vertical.median_net_pence is not None else "—"
        contest = f"{vertical.avg_contest:.1f}" if vertical.avg_contest is not None else "—"
        rows.append(
            f"<tr><td>{escape(vertical.category_id)}</td>"
            f"<td>{escape(vertical.country or '—')}</td>"
            f'<td class="n">{vertical.listings}</td>'
            f'<td class="n">{median}</td><td class="n">{contest}</td></tr>'
        )
    return (
        "<section><h2>Verticals</h2><table><thead><tr><th>Category</th><th>Country</th>"
        '<th class="n">Listings</th><th class="n">Median net</th>'
        '<th class="n">Avg watchers</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<p class="note">Read margin against watchers, not on its own. A niche with '
        "high margin and many watchers is one you lose races in.</p></section>"
    )


def _provenance_section(data: DashboardData) -> str:
    rows = []
    for entry in data.placeholders:
        status = entry.status.value
        rows.append(
            f"<tr><td>{escape(entry.placeholder.id)}</td>"
            f"<td>{escape(entry.placeholder.gap)}</td>"
            f'<td><span class="tag {escape(status)}">{escape(status)}</span></td>'
            f"<td>{escape(entry.evidence)}</td></tr>"
        )
    open_count = sum(1 for e in data.placeholders if e.status is PlaceholderStatus.OPEN)
    return (
        f"<section><h2>Assumptions still open ({open_count} of {len(data.placeholders)})</h2>"
        "<table><thead><tr><th></th><th>Gap</th><th>Status</th><th>Evidence</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        '<p class="note">Every figure above inherits from these. Closing one means '
        "re-scoring what it influenced, not just changing the number.</p></section>"
    )


def render_dashboard(data: DashboardData) -> str:
    """Render the whole page. Pure: data in, HTML out, no queries and no clock read."""
    banner = ""
    if data.synthetic_trades:
        banner = (
            f'<div class="banner"><b>{data.synthetic_trades} of these trades are '
            "generated, not traded.</b> They exist so this page has shape before the "
            "first real sale, and they are excluded from the assumptions register "
            "below — seeding cannot close a placeholder.</div>"
        )
    stamp = data.generated_at.isoformat(timespec="minutes")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>arb — books</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>Books</h1>
<p class="sub">Generated {escape(stamp)} · a local view over arb.db</p>
<div class="legend">
  <span><b class="measured">Teal</b> — measured against real settlement data</span>
  <span><b class="assumed">Amber</b> — computed from an assumption nobody has checked</span>
</div>
{banner}
{_capital_section(data)}
{_realised_section(data)}
{_stock_section(data)}
{_verticals_section(data)}
{_provenance_section(data)}
</div></body></html>
"""
