"""Phase 7 (T15) — post-flight HTML report.

Reads a Sentinel SQLite event log after a session and produces a single,
self-contained HTML report suitable for handing to judges or stakeholders:
flight duration, detection counts by class, an activity timeline, and a full
event table. No external assets — all CSS + SVG charts are inlined.

    python -m sentinel.report                       # latest session in events.db
    python -m sentinel.report --db events.db --out report.html
    python -m sentinel.report --session 20260710_1030
"""

from __future__ import annotations

import argparse
import html
import sqlite3
import time
from dataclasses import dataclass

from .logging_config import configure, get_logger

_log = get_logger("sentinel.report")


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    total: int
    first_ts: float
    last_ts: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


def list_sessions(db_path: str) -> list[SessionInfo]:
    """All sessions in the DB, most recent first."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT session_id, COUNT(*), MIN(ts), MAX(ts) "
            "FROM detections GROUP BY session_id ORDER BY MIN(ts) DESC"
        ).fetchall()
    finally:
        conn.close()
    return [SessionInfo(r[0], int(r[1]), float(r[2]), float(r[3])) for r in rows]


def load_events(db_path: str, session_id: str) -> list[dict]:
    """All detection rows for a session, oldest first."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ts, cls_name, confidence, x1, y1, x2, y2 "
            "FROM detections WHERE session_id = ? ORDER BY ts ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _counts_by_class(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        counts[e["cls_name"]] = counts.get(e["cls_name"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def _bar_chart_svg(counts: dict[str, int], width: int = 320) -> str:
    if not counts:
        return "<p class='muted'>No detections.</p>"
    top = max(counts.values())
    rows = []
    y = 0
    row_h = 26
    for name, n in counts.items():
        bar_w = int((width - 120) * n / top) if top else 0
        rows.append(
            f'<g transform="translate(0,{y})">'
            f'<text x="0" y="16" class="lbl">{html.escape(name)}</text>'
            f'<rect x="80" y="4" width="{bar_w}" height="16" rx="3" class="bar"/>'
            f'<text x="{88 + bar_w}" y="16" class="val">{n}</text>'
            f"</g>"
        )
        y += row_h
    return (f'<svg viewBox="0 0 {width} {y}" width="100%" '
            f'height="{y}" role="img">{"".join(rows)}</svg>')


def _timeline_svg(events: list[dict], first: float, last: float,
                  width: int = 640, height: int = 90) -> str:
    if not events or last <= first:
        return "<p class='muted'>Not enough data for a timeline.</p>"
    span = last - first
    buckets = 60
    bins = [0] * buckets
    for e in events:
        idx = min(buckets - 1, int((e["ts"] - first) / span * buckets))
        bins[idx] += 1
    peak = max(bins) or 1
    bar_w = width / buckets
    bars = []
    for i, c in enumerate(bins):
        bh = int((height - 20) * c / peak)
        bars.append(
            f'<rect x="{i * bar_w:.1f}" y="{height - 20 - bh}" '
            f'width="{bar_w - 1:.1f}" height="{bh}" class="tl"/>'
        )
    axis = (f'<text x="0" y="{height - 4}" class="ax">'
            f'{time.strftime("%H:%M:%S", time.localtime(first))}</text>'
            f'<text x="{width}" y="{height - 4}" text-anchor="end" class="ax">'
            f'{time.strftime("%H:%M:%S", time.localtime(last))}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img">{"".join(bars)}{axis}</svg>')


def _event_rows(events: list[dict], limit: int = 500) -> str:
    rows = []
    for e in events[:limit]:
        t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        rows.append(
            f"<tr><td>{t}</td><td>{html.escape(e['cls_name'])}</td>"
            f"<td>{e['confidence'] * 100:.0f}%</td>"
            f"<td>{e['x1']},{e['y1']},{e['x2']},{e['y2']}</td></tr>"
        )
    extra = ("" if len(events) <= limit
             else f"<tr><td colspan='4' class='muted'>"
                  f"… {len(events) - limit} more</td></tr>")
    return "".join(rows) + extra


def build_report_html(info: SessionInfo, events: list[dict]) -> str:
    counts = _counts_by_class(events)
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    metrics = [
        ("Duration", _fmt_duration(info.duration_s)),
        ("Total detections", str(info.total)),
        ("Classes seen", str(len(counts))),
        ("Session", html.escape(info.session_id)),
    ]
    cards = "".join(
        f'<div class="card"><div class="n">{v}</div><div class="k">{k}</div></div>'
        for k, v in metrics
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Sentinel Report · {html.escape(info.session_id)}</title>
<style>
  :root {{ --bg:#0b0f14; --panel:#131a22; --line:#223040; --text:#e6edf3;
          --muted:#7d8da0; --accent:#22d3ee; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; padding:28px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }} h1 .b {{ color:var(--accent); }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.6px;
        color:var(--muted); margin:26px 0 10px; }}
  .sub {{ color:var(--muted); font-size:12px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-top:18px; }}
  @media (max-width:680px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }}
  .card .n {{ font-size:22px; font-weight:700; color:var(--accent); word-break:break-all; }}
  .card .k {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--muted); font-weight:600; text-transform:uppercase; font-size:11px; }}
  .muted {{ color:var(--muted); }}
  .bar {{ fill:var(--accent); }} .tl {{ fill:var(--accent); opacity:.85; }}
  .lbl {{ fill:var(--text); font-size:12px; }} .val {{ fill:var(--muted); font-size:12px; }}
  .ax {{ fill:var(--muted); font-size:11px; }}
  .scroll {{ max-height:420px; overflow:auto; }}
</style></head>
<body>
  <h1><span class="b">SENTINEL</span> · Post-Flight Report</h1>
  <div class="sub">Generated {generated}</div>
  <div class="cards">{cards}</div>

  <h2>Activity timeline</h2>
  <div class="panel">{_timeline_svg(events, info.first_ts, info.last_ts)}</div>

  <h2>Detections by class</h2>
  <div class="panel">{_bar_chart_svg(counts)}</div>

  <h2>Event log</h2>
  <div class="panel scroll"><table>
    <thead><tr><th>Time</th><th>Class</th><th>Conf</th><th>BBox</th></tr></thead>
    <tbody>{_event_rows(events)}</tbody>
  </table></div>
</body></html>
"""


def generate(db_path: str, out_path: str,
             session_id: str | None = None) -> str:
    """Write an HTML report for a session; returns the output path."""
    sessions = list_sessions(db_path)
    if not sessions:
        raise ValueError(f"No sessions found in {db_path}.")
    if session_id is None:
        info = sessions[0]  # most recent
    else:
        matches = [s for s in sessions if s.session_id == session_id]
        if not matches:
            raise ValueError(f"Session '{session_id}' not found in {db_path}.")
        info = matches[0]

    events = load_events(db_path, info.session_id)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(build_report_html(info, events))
    _log.info("Report written: %s (session %s, %d events, %s)",
              out_path, info.session_id, info.total,
              _fmt_duration(info.duration_s))
    return out_path


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sentinel.report", description="Generate a post-flight HTML report.")
    p.add_argument("--db", default="events.db", help="SQLite event log path.")
    p.add_argument("--out", default="report.html", help="Output HTML path.")
    p.add_argument("--session", default=None,
                   help="Session id (default: most recent).")
    p.add_argument("--list", action="store_true",
                   help="List sessions and exit.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    configure("INFO")
    if args.list:
        for s in list_sessions(args.db):
            print(f"{s.session_id}  {s.total:5d} events  "
                  f"{_fmt_duration(s.duration_s)}")
        return 0
    try:
        generate(args.db, args.out, args.session)
    except (ValueError, sqlite3.Error) as exc:
        _log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
