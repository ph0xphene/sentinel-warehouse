from collections.abc import Sequence
from html import escape

from sentinel.reporting.timeline import TimelineItem


def _short(value: str, limit: int = 28) -> str:
    return value if len(value) <= limit else f"{value[:12]}…{value[-10:]}"


def render_relationship_graph(
    events: Sequence[TimelineItem],
    incident_types: Sequence[str],
) -> str:
    """Render a deterministic entity/asset/incident relationship graph as inline SVG."""
    sources = sorted({event.account_from or "EXTERNAL_SOURCE" for event in events})
    destinations = sorted({event.account_to or "EXTERNAL_SINK" for event in events})
    assets = sorted({event.asset for event in events if event.asset})
    incidents = sorted(set(incident_types))
    if not sources and not destinations and not assets and not incidents:
        return '<p class="empty">No relationship data was available.</p>'

    columns = (
        ("Sources", sources, 30, "#dbeafe", "#1d4ed8"),
        ("Assets", assets, 330, "#ccfbf1", "#0f766e"),
        ("Destinations", destinations, 630, "#e0e7ff", "#4338ca"),
        ("Findings", incidents, 930, "#fee2e2", "#b91c1c"),
    )
    row_height = 76
    max_rows = max((len(values) for _, values, _, _, _ in columns), default=1)
    height = 64 + row_height * max(max_rows, 1)
    width = 1200
    node_width = 240
    node_height = 42
    positions: dict[tuple[str, str], tuple[int, int]] = {}
    parts = [
        f'<svg class="relationship-graph" viewBox="0 0 {width} {height}" '
        'role="img" aria-label="Event relationship graph">',
        "<defs>",
        '<marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" '
        'orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#64748b"/></marker>',
        "</defs>",
    ]
    for heading, values, x, fill, stroke in columns:
        parts.append(f'<text x="{x}" y="22" class="svg-heading">{escape(heading)}</text>')
        for index, value in enumerate(values):
            y = 38 + index * row_height
            positions[(heading, value)] = (x, y)
            parts.extend(
                (
                    f'<rect x="{x}" y="{y}" width="{node_width}" height="{node_height}" '
                    f'rx="8" fill="{fill}" stroke="{stroke}"/>',
                    f'<text x="{x + 12}" y="{y + 26}" class="svg-node">'
                    f"{escape(_short(value))}</text>",
                )
            )

    def edge(
        start: tuple[int, int],
        end: tuple[int, int],
        label: str,
        *,
        dashed: bool = False,
    ) -> None:
        x1, y1 = start[0] + node_width, start[1] + node_height // 2
        x2, y2 = end[0], end[1] + node_height // 2
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        parts.append(
            f'<path d="M{x1},{y1} C{x1 + 45},{y1} {x2 - 45},{y2} {x2},{y2}" '
            f'fill="none" stroke="#64748b" stroke-width="1.5" marker-end="url(#arrow)"'
            f"{dash}/>"
        )
        parts.append(
            f'<text x="{(x1 + x2) // 2}" y="{(y1 + y2) // 2 - 6}" '
            f'text-anchor="middle" class="svg-edge">{escape(_short(label, 36))}</text>'
        )

    for event in events:
        source = event.account_from or "EXTERNAL_SOURCE"
        destination = event.account_to or "EXTERNAL_SINK"
        source_position = positions.get(("Sources", source))
        destination_position = positions.get(("Destinations", destination))
        asset_position = positions.get(("Assets", event.asset or ""))
        amount = f" {event.amount}" if event.amount is not None else ""
        if source_position and asset_position:
            edge(source_position, asset_position, f"{event.event_type}{amount}")
        if asset_position and destination_position:
            edge(asset_position, destination_position, "to")
        elif source_position and destination_position:
            edge(source_position, destination_position, f"{event.event_type}{amount}")

    finding_positions = [
        positions[("Findings", value)] for value in incidents if ("Findings", value) in positions
    ]
    destination_positions = [
        positions[("Destinations", value)]
        for value in destinations
        if ("Destinations", value) in positions
    ]
    for index, finding_position in enumerate(finding_positions):
        if destination_positions:
            edge(
                destination_positions[index % len(destination_positions)],
                finding_position,
                "supports finding",
                dashed=True,
            )

    parts.append("</svg>")
    return "".join(parts)
