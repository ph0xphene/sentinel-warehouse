import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from uuid import UUID

from sentinel.reporting.charts import StateDelta
from sentinel.reporting.timeline import TimelineItem


@dataclass(frozen=True)
class InvariantView:
    name: str
    status: str
    description: str
    reason: str
    affected_records: tuple[dict[str, object], ...]
    protocol_name: str | None = None


@dataclass(frozen=True)
class EvidenceView:
    affected_entity: str
    evidence_type: str
    origin: str
    payload: dict[str, object]


@dataclass(frozen=True)
class ReportContent:
    report_kind: str
    subject_id: UUID
    subject_name: str
    origin: str
    status: str
    protocol: str
    chain: str | None
    generated_at: datetime
    executive_summary: str
    case_id: UUID | None
    incident_id: UUID | None
    timeline: tuple[TimelineItem, ...]
    state_deltas: tuple[StateDelta, ...]
    invariants: tuple[InvariantView, ...]
    evidence: tuple[EvidenceView, ...]
    references: tuple[str, ...]


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _format_decimal(value: object) -> str:
    rendered = str(value)
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _badge(value: str, kind: str = "") -> str:
    class_name = f"badge {kind}".strip()
    return f'<span class="{class_name}">{escape(value)}</span>'


def _metadata(content: ReportContent) -> str:
    values = (
        ("Report", content.report_kind),
        ("Origin", content.origin),
        ("Status", content.status),
        ("Protocol", content.protocol),
        ("Chain", content.chain or "not specified"),
        ("Generated", _format_datetime(content.generated_at)),
    )
    return "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>" for label, value in values
    )


def _timeline(
    items: Sequence[TimelineItem],
    failed_invariants: Sequence[str],
) -> str:
    rows: list[str] = []
    for index, item in enumerate(items, start=1):
        movement = " → ".join(
            value for value in (item.account_from, item.account_to) if value is not None
        )
        amount = (
            f"{_format_decimal(item.amount)} {item.asset or 'unknown asset'}"
            if item.amount is not None
            else item.asset or ""
        )
        details = " · ".join(value for value in (movement, amount) if value)
        description = f'<p class="muted">{escape(item.description)}</p>' if item.description else ""
        transaction_hash = (
            f'<div class="hash">Transaction: {escape(item.transaction_hash)}</div>'
            if item.transaction_hash
            else ""
        )
        rows.append(
            f'<li data-order="{index}"><div class="timeline-marker"></div>'
            f'<div class="timeline-card"><div class="timeline-coordinate">'
            f"{escape(item.coordinate)}</div><h3>{escape(item.event_type)}</h3>"
            f'<p class="event-id">{escape(item.external_id)}</p>'
            f"<p>{escape(details)}</p>{description}{transaction_hash}</div></li>"
        )
    for offset, invariant in enumerate(failed_invariants, start=len(rows) + 1):
        rows.append(
            f'<li data-order="{offset}" class="violation">'
            '<div class="timeline-marker">×</div><div class="timeline-card">'
            f'<div class="timeline-coordinate">Invariant evaluation</div>'
            f"<h3>{escape(invariant)} violated</h3>"
            "<p>The reconstructed state produced a blocking security finding.</p>"
            "</div></li>"
        )
    if not rows:
        return '<p class="empty">No related events were available.</p>'
    return f'<ol class="timeline">{"".join(rows)}</ol>'


def _state_table(deltas: Sequence[StateDelta]) -> str:
    if not deltas:
        return '<p class="empty">No before/after balance snapshots were available.</p>'
    rows = []
    for value in deltas:
        delta = value.delta
        sign = "+" if delta > 0 else ""
        delta_class = "negative" if delta < 0 else "positive" if delta > 0 else "neutral"
        rows.append(
            "<tr>"
            f"<td>{escape(value.entity)}</td>"
            f"<td>{escape(value.asset)}</td>"
            f"<td>{escape(_format_decimal(value.before))}</td>"
            f"<td>{escape(_format_decimal(value.after))}</td>"
            f'<td class="{delta_class}">{escape(sign + _format_decimal(delta))}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Entity</th><th>Asset</th>'
        f"<th>Before</th><th>After</th><th>Delta</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _invariants(values: Sequence[InvariantView]) -> str:
    if not values:
        return '<p class="empty">No invariant results were available.</p>'
    cards = []
    for value in values:
        status_class = value.status.lower().replace("_", "-")
        affected = ""
        if value.affected_records:
            affected_json = json.dumps(
                value.affected_records,
                indent=2,
                sort_keys=True,
                default=str,
            )
            affected = (
                "<details><summary>Affected records "
                f"({len(value.affected_records)})</summary><pre>"
                f"{escape(affected_json)}"
                "</pre></details>"
            )
        protocol = (
            f'<span class="protocol-label">{escape(value.protocol_name)}</span>'
            if value.protocol_name
            else ""
        )
        cards.append(
            '<article class="invariant-card">'
            f"<div>{_badge(value.status, status_class)}{protocol}</div>"
            f"<h3>{escape(value.name)}</h3><p>{escape(value.description)}</p>"
            f'<p class="reason"><strong>Reason:</strong> {escape(value.reason)}</p>'
            f"{affected}</article>"
        )
    return f'<div class="invariant-grid">{"".join(cards)}</div>'


def _evidence(values: Sequence[EvidenceView], content: ReportContent) -> str:
    identity = (
        f'<dl class="identity"><div><dt>Case ID</dt><dd>'
        f"{escape(str(content.case_id)) if content.case_id else '—'}</dd></div>"
        f"<div><dt>Incident ID</dt><dd>"
        f"{escape(str(content.incident_id)) if content.incident_id else '—'}</dd></div></dl>"
    )
    if not values:
        return f'{identity}<p class="empty">No incident evidence records were created.</p>'
    cards = []
    for value in values:
        cards.append(
            '<article class="evidence-card">'
            f"<div>{_badge(value.origin, 'origin')} "
            f"{_badge(value.evidence_type)}</div>"
            f"<h3>{escape(value.affected_entity)}</h3>"
            f"<pre>{escape(json.dumps(value.payload, indent=2, sort_keys=True, default=str))}"
            "</pre></article>"
        )
    return f'{identity}<div class="evidence-grid">{"".join(cards)}</div>'


def _references(values: Sequence[str]) -> str:
    if not values:
        return ""
    items = "".join(f"<li><code>{escape(value)}</code></li>" for value in values)
    return f"<h3>External references</h3><ul>{items}</ul>"


STYLE = """
:root { color-scheme: light; --ink:#172033; --muted:#64748b; --line:#dbe3ee;
  --panel:#ffffff; --canvas:#f5f7fb; --accent:#1d4ed8; }
* { box-sizing:border-box; }
body { margin:0; background:var(--canvas); color:var(--ink);
  font:15px/1.55 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
main { max-width:1120px; margin:0 auto; padding:42px 24px 72px; }
header { padding:34px; color:white; border-radius:18px;
  background:linear-gradient(125deg,#172554,#1d4ed8 62%,#0f766e);
  box-shadow:0 18px 42px #1e3a5f24; }
header .eyebrow { margin:0 0 8px; text-transform:uppercase; letter-spacing:.15em;
  font-size:12px; opacity:.76; }
h1 { margin:0; font-size:34px; line-height:1.15; } h2 { margin:0 0 20px; font-size:23px; }
h3 { margin:10px 0 6px; } p { margin:7px 0; }
.metadata { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px;
  margin-top:28px; }
dl,dt,dd { margin:0; } .metadata div { border-top:1px solid #ffffff42; padding-top:9px; }
dt { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; }
header dt { color:#bfdbfe; } dd { margin-top:3px; font-weight:650; overflow-wrap:anywhere; }
section { margin-top:24px; padding:28px; background:var(--panel); border:1px solid var(--line);
  border-radius:16px; box-shadow:0 8px 30px #3341550b; }
.summary { font-size:18px; max-width:850px; }
.badge { display:inline-block; margin-right:6px; padding:4px 9px; border-radius:999px;
  background:#e2e8f0; font-size:11px; font-weight:800; letter-spacing:.05em; }
.pass,.passed { color:#065f46; background:#d1fae5; }
.fail,.failed { color:#991b1b; background:#fee2e2; }
.insufficient-evidence { color:#92400e; background:#fef3c7; }
.origin { color:#1e3a8a; background:#dbeafe; }
.timeline { list-style:none; padding:0; margin:0; position:relative; }
.timeline:before { content:""; position:absolute; left:15px; top:12px; bottom:12px;
  width:2px; background:var(--line); }
.timeline li { position:relative; display:grid; grid-template-columns:32px 1fr; gap:15px;
  margin-bottom:15px; }
.timeline-marker { z-index:1; width:31px; height:31px; display:grid; place-items:center;
  border-radius:50%; background:#dbeafe; border:3px solid white; color:#1d4ed8; font-weight:900; }
.violation .timeline-marker { color:#b91c1c; background:#fee2e2; }
.timeline-card,.invariant-card,.evidence-card { border:1px solid var(--line); border-radius:12px;
  padding:16px 18px; background:#fff; }
.timeline-coordinate,.event-id,.muted,.hash,.empty { color:var(--muted); }
.timeline-coordinate { font-size:12px; text-transform:uppercase; letter-spacing:.07em;
  font-weight:750; }
.event-id,.hash { font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;
  overflow-wrap:anywhere; }
.table-wrap { overflow-x:auto; } table { width:100%; border-collapse:collapse; }
th,td { padding:11px 12px; text-align:left; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
.positive { color:#047857; font-weight:750; }.negative { color:#b91c1c; font-weight:750; }
.neutral { color:var(--muted); }
.invariant-grid,.evidence-grid { display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
.protocol-label { color:var(--muted); font-size:12px; }.reason { padding-top:8px; }
details { margin-top:12px; } summary { cursor:pointer; color:var(--accent); }
pre { max-height:320px; overflow:auto; padding:14px; border-radius:8px; background:#0f172a;
  color:#e2e8f0; font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;
  white-space:pre-wrap; }
.identity { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px;
  margin-bottom:18px; }
.identity div { padding:12px; border:1px solid var(--line); border-radius:9px; }
.balance-chart,.relationship-graph { display:block; width:100%; height:auto; min-height:180px; }
.svg-label,.svg-value,.svg-node,.svg-edge,.svg-muted,.svg-heading {
  font-family:ui-sans-serif,system-ui,sans-serif; fill:#172033; }
.svg-label,.svg-node { font-size:12px; }.svg-value,.svg-edge,.svg-muted { font-size:10px; }
.svg-heading { font-size:13px; font-weight:800; text-transform:uppercase; }
code { overflow-wrap:anywhere; }
footer { padding:22px 4px; color:var(--muted); font-size:12px; text-align:center; }
@media (max-width:760px) {
  .metadata,.invariant-grid,.evidence-grid,.identity { grid-template-columns:1fr; }
  main { padding:20px 12px 42px; } header,section { padding:22px; } }
@media print { body { background:white; } main { max-width:none; padding:0; }
  section,header { box-shadow:none; break-inside:avoid; } }
"""


def render_report_html(
    content: ReportContent,
    *,
    graph_svg: str,
    balance_chart_svg: str,
) -> str:
    failed = tuple(value.name for value in content.invariants if value.status == "FAIL")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
        f"<title>Sentinel Investigation Report — {escape(content.subject_name)}</title>"
        f"<style>{STYLE}</style></head><body><main>"
        '<header><p class="eyebrow">Sentinel Investigation Report</p>'
        f"<h1>{escape(content.subject_name)}</h1>"
        f'<dl class="metadata">{_metadata(content)}</dl></header>'
        "<section><h2>Executive summary</h2>"
        f'<p class="summary">{escape(content.executive_summary)}</p></section>'
        '<section id="timeline"><h2>Event timeline</h2>'
        f"{_timeline(content.timeline, failed)}</section>"
        '<section id="state"><h2>State transition</h2>'
        f"{_state_table(content.state_deltas)}{balance_chart_svg}</section>"
        '<section id="graph"><h2>Event relationship graph</h2>'
        f"{graph_svg}</section>"
        '<section id="invariants"><h2>Invariant results</h2>'
        f"{_invariants(content.invariants)}</section>"
        '<section id="evidence"><h2>Evidence</h2>'
        f"{_evidence(content.evidence, content)}{_references(content.references)}</section>"
        "<footer>Generated by Sentinel Warehouse · static offline investigation artifact</footer>"
        "</main></body></html>"
    )
