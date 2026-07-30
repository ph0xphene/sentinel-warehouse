from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any


@dataclass(frozen=True)
class StateDelta:
    entity: str
    asset: str
    before: Decimal
    after: Decimal

    @property
    def delta(self) -> Decimal:
        return self.after - self.before


def build_state_deltas(fixture: Mapping[str, Any]) -> tuple[StateDelta, ...]:
    values: list[StateDelta] = []
    for balance in fixture.get("balances", []):
        if not isinstance(balance, Mapping):
            continue
        try:
            before = Decimal(str(balance.get("opening_amount", 0)))
            after = Decimal(str(balance["amount"]))
        except (InvalidOperation, KeyError, ValueError):
            continue
        values.append(
            StateDelta(
                entity=str(balance.get("account_external_id", "UNKNOWN")),
                asset=str(balance.get("asset_external_id", "UNKNOWN")),
                before=before,
                after=after,
            )
        )
    return tuple(sorted(values, key=lambda value: (value.entity, value.asset)))


def _number(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def render_balance_delta_chart(deltas: Sequence[StateDelta]) -> str:
    """Render an inline, dependency-free SVG centered around a zero-delta axis."""
    if not deltas:
        return '<p class="empty">No balance snapshots were available.</p>'

    width = 920
    label_width = 260
    chart_width = 500
    row_height = 44
    height = 42 + row_height * len(deltas)
    center = label_width + chart_width // 2
    maximum = max((abs(value.delta) for value in deltas), default=Decimal(0))
    maximum = maximum or Decimal(1)
    rows: list[str] = [
        f'<svg class="balance-chart" viewBox="0 0 {width} {height}" '
        'role="img" aria-label="Balance delta chart">',
        f'<line x1="{center}" y1="18" x2="{center}" y2="{height - 14}" '
        'stroke="#64748b" stroke-width="1"/>',
        f'<text x="{center}" y="14" text-anchor="middle" class="svg-muted">zero</text>',
    ]
    half = Decimal(chart_width // 2 - 24)
    for index, value in enumerate(deltas):
        y = 34 + index * row_height
        delta = value.delta
        bar_width = int((abs(delta) / maximum) * half)
        x = center if delta >= 0 else center - bar_width
        color = "#dc2626" if delta < 0 else "#0f766e"
        sign = "+" if delta > 0 else ""
        label = escape(f"{value.entity} · {value.asset}")
        rows.extend(
            (
                f'<text x="8" y="{y + 16}" class="svg-label">{label}</text>',
                f'<rect x="{x}" y="{y}" width="{max(bar_width, 1)}" height="22" '
                f'rx="4" fill="{color}"/>',
                f'<text x="{center + (bar_width + 8 if delta >= 0 else -bar_width - 8)}" '
                f'y="{y + 16}" text-anchor="{"start" if delta >= 0 else "end"}" '
                f'class="svg-value">{escape(sign + _number(delta))}</text>',
            )
        )
    rows.append("</svg>")
    return "".join(rows)
