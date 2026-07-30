import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine
from sentinel.models import (
    Account,
    Asset,
    AttackFlow,
    FinancialEvent,
    Incident,
    IncidentCase,
    IncidentEvidence,
    IncidentOrigin,
    IngestionBatch,
    InvariantResult,
)
from sentinel.reporting.charts import build_state_deltas, render_balance_delta_chart
from sentinel.reporting.graph import render_relationship_graph
from sentinel.reporting.templates import (
    EvidenceView,
    InvariantView,
    ReportContent,
    render_report_html,
)
from sentinel.reporting.timeline import build_timeline, timeline_from_fixture
from sentinel.security import CanonicalEvent
from sentinel.security.cases import replay_incident_case


@dataclass(frozen=True)
class ReportGenerationSummary:
    output: Path
    report_kind: str
    subject_id: uuid.UUID
    subject_name: str
    origin: IncidentOrigin
    status: str
    events: int
    invariants: int
    evidence_records: int


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "investigation"


def _case_by_selector(session: Session, selector: str | uuid.UUID) -> IncidentCase:
    try:
        case_id = selector if isinstance(selector, uuid.UUID) else uuid.UUID(str(selector))
    except ValueError:
        case_id = None
    if case_id is not None:
        case = session.get(IncidentCase, case_id)
        if case is None:
            raise ValueError(f"Incident case {case_id} was not found")
        return case

    normalized = str(selector).casefold()
    slug = _slug(str(selector))
    cases = tuple(
        case
        for case in session.scalars(
            select(IncidentCase).order_by(IncidentCase.name, IncidentCase.case_id)
        )
        if case.name.casefold() == normalized
        or _slug(case.name) == slug
        or _slug(case.name).startswith(f"{slug}-")
    )
    if not cases:
        raise ValueError(f"Incident case {selector!s} was not found")
    if len(cases) > 1:
        raise ValueError(f"Incident case selector {selector!s} is ambiguous")
    return cases[0]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _invariant_view(result: InvariantResult) -> InvariantView:
    status = {
        "passed": "PASS",
        "failed": "FAIL",
        "insufficient_evidence": "INSUFFICIENT_EVIDENCE",
    }.get(result.execution_result, result.execution_result.upper())
    affected = tuple(dict(record) for record in result.affected_records)
    first = affected[0] if affected else {}
    if first.get("reason") is not None:
        reason = str(first["reason"])
    elif first.get("missing_or_invalid") is not None:
        values = first["missing_or_invalid"]
        reason = f"Missing or invalid fields: {values}"
    elif status == "PASS":
        reason = "The evaluated records satisfied this invariant."
    elif status == "INSUFFICIENT_EVIDENCE":
        reason = "The available range does not contain enough state to prove the invariant."
    else:
        reason = "One or more affected records violated the invariant."
    return InvariantView(
        name=result.name,
        status=status,
        description=result.description,
        reason=reason,
        affected_records=affected,
        protocol_name=result.protocol_name,
    )


def _evidence_view(value: IncidentEvidence) -> EvidenceView:
    return EvidenceView(
        affected_entity=value.affected_entity,
        evidence_type=value.evidence_type,
        origin=value.origin.value,
        payload=dict(value.payload),
    )


def _executive_summary(
    *,
    status: str,
    failed_invariants: Sequence[str],
    insufficient_invariants: Sequence[str],
    origin: str,
) -> str:
    if failed_invariants:
        names = ", ".join(name.replace("_", " ") for name in failed_invariants)
        return (
            f"The reconstructed financial state violated {names} after the ordered event "
            f"sequence. Sentinel preserved {origin.lower()} evidence and generated an "
            "auditable, reproducible incident."
        )
    if insufficient_invariants:
        names = ", ".join(name.replace("_", " ") for name in insufficient_invariants)
        return (
            f"The pipeline completed with status {status}, but {names} could not be proven "
            "from the available state. The report preserves this uncertainty explicitly."
        )
    return (
        f"The ordered event sequence completed with status {status} and no invariant "
        "violations. The reconstructed state and supporting evidence remain reproducible."
    )


def _flow_descriptions(
    case: IncidentCase,
    flows: Sequence[AttackFlow],
) -> dict[str, str]:
    links = _mapping(case.replay_definition.get("flow_event_external_ids"))
    descriptions: dict[str, str] = {}
    for flow in flows:
        external_id = links.get(str(flow.step_number))
        if external_id is not None:
            descriptions[str(external_id)] = f"{flow.action}: {flow.description}"
    return descriptions


def _canonical_database_events(
    session: Session,
    batch_id: uuid.UUID,
) -> tuple[CanonicalEvent, ...]:
    events = tuple(
        session.scalars(
            select(FinancialEvent).where(
                FinancialEvent.batch_id == batch_id,
                FinancialEvent.canonical.is_(True),
            )
        )
    )
    account_ids = {
        value
        for event in events
        for value in (event.account_from_id, event.account_to_id)
        if value is not None
    }
    asset_ids = {event.asset_id for event in events if event.asset_id is not None}
    accounts = (
        {
            account.account_id: account.external_id
            for account in session.scalars(
                select(Account).where(Account.account_id.in_(account_ids))
            )
        }
        if account_ids
        else {}
    )
    assets = (
        {
            asset.asset_id: asset.external_id
            for asset in session.scalars(select(Asset).where(Asset.asset_id.in_(asset_ids)))
        }
        if asset_ids
        else {}
    )
    return tuple(
        CanonicalEvent(
            external_id=event.external_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            asset_external_id=assets.get(event.asset_id),
            account_from_external_id=accounts.get(event.account_from_id),
            account_to_external_id=accounts.get(event.account_to_id),
            amount=event.amount,
            metadata=dict(event.event_metadata),
            chain_id=event.chain_id,
            block_number=event.block_number,
            block_hash=event.block_hash,
            transaction_index=event.transaction_index,
            log_index=event.log_index,
            checker_authorized=event.checker_authorized,
        )
        for event in events
    )


def _write_report(
    content: ReportContent,
    output: Path,
) -> ReportGenerationSummary:
    incident_types = tuple(
        sorted(
            {str(value.payload.get("invariant", value.evidence_type)) for value in content.evidence}
        )
    )
    graph_svg = render_relationship_graph(content.timeline, incident_types)
    balance_chart_svg = render_balance_delta_chart(content.state_deltas)
    html = render_report_html(
        content,
        graph_svg=graph_svg,
        balance_chart_svg=balance_chart_svg,
    )
    resolved = output.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(html, encoding="utf-8")
    return ReportGenerationSummary(
        output=resolved,
        report_kind=content.report_kind,
        subject_id=content.subject_id,
        subject_name=content.subject_name,
        origin=IncidentOrigin(content.origin),
        status=content.status,
        events=len(content.timeline),
        invariants=len(content.invariants),
        evidence_records=len(content.evidence),
    )


def generate_case_report(
    selector: str | uuid.UUID,
    output: Path | None = None,
    engine: Engine | None = None,
    *,
    generated_at: datetime | None = None,
) -> ReportGenerationSummary:
    """Replay a case and generate one self-contained investigation report."""
    engine = engine or create_database_engine()
    with Session(engine) as session:
        selected = _case_by_selector(session, selector)
        case_id = selected.case_id
        case_name = selected.name

    replay = replay_incident_case(case_id, engine)
    with Session(engine) as session:
        case = session.get(IncidentCase, case_id)
        batch = session.get(IngestionBatch, replay.pipeline.batch_id)
        if case is None or batch is None:
            raise RuntimeError("Case replay data disappeared before report generation")
        flows = tuple(
            session.scalars(
                select(AttackFlow)
                .where(AttackFlow.case_id == case_id)
                .order_by(AttackFlow.step_number, AttackFlow.flow_id)
            )
        )
        invariant_models = tuple(
            session.scalars(
                select(InvariantResult)
                .where(
                    InvariantResult.batch_id == batch.batch_id,
                    InvariantResult.attempt_number == batch.attempt_count,
                    InvariantResult.origin == IncidentOrigin.REPLAY,
                    InvariantResult.case_id == case_id,
                )
                .order_by(InvariantResult.name, InvariantResult.invariant_id)
            )
        )
        incidents = tuple(
            session.scalars(
                select(Incident)
                .where(
                    Incident.batch_id == batch.batch_id,
                    Incident.origin == IncidentOrigin.REPLAY,
                    Incident.case_id == case_id,
                )
                .order_by(Incident.incident_type, Incident.incident_id)
            )
        )
        incident_ids = tuple(incident.incident_id for incident in incidents)
        evidence_models = (
            tuple(
                session.scalars(
                    select(IncidentEvidence)
                    .where(IncidentEvidence.incident_id.in_(incident_ids))
                    .order_by(
                        IncidentEvidence.created_at,
                        IncidentEvidence.evidence_id,
                    )
                )
            )
            if incident_ids
            else ()
        )
        fixture = _mapping(case.replay_definition.get("fixture"))
        timeline = timeline_from_fixture(
            fixture,
            flow_descriptions=_flow_descriptions(case, flows),
        )
        invariants = tuple(_invariant_view(result) for result in invariant_models)
        evidence = tuple(_evidence_view(value) for value in evidence_models)
        failed = tuple(value.name for value in invariants if value.status == "FAIL")
        insufficient = tuple(
            value.name for value in invariants if value.status == "INSUFFICIENT_EVIDENCE"
        )
        content = ReportContent(
            report_kind="CASE",
            subject_id=case.case_id,
            subject_name=case.name,
            origin=batch.origin.value,
            status=batch.status.value.upper(),
            protocol=case.protocol,
            chain=case.chain,
            generated_at=generated_at or datetime.now(UTC),
            executive_summary=_executive_summary(
                status=batch.status.value.upper(),
                failed_invariants=failed,
                insufficient_invariants=insufficient,
                origin=batch.origin.value,
            ),
            case_id=case.case_id,
            incident_id=incident_ids[0] if len(incident_ids) == 1 else None,
            timeline=timeline,
            state_deltas=build_state_deltas(fixture),
            invariants=invariants,
            evidence=evidence,
            references=tuple(case.external_references or ()),
        )

    target = output or Path("reports") / f"{_slug(case_name)}.html"
    return _write_report(content, target)


def generate_incident_report(
    incident_id: uuid.UUID,
    output: Path | None = None,
    engine: Engine | None = None,
    *,
    generated_at: datetime | None = None,
) -> ReportGenerationSummary:
    """Generate a self-contained report from an existing incident and its evidence."""
    engine = engine or create_database_engine()
    with Session(engine) as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} was not found")
        batch = session.get(IngestionBatch, incident.batch_id)
        if batch is None:
            raise RuntimeError(f"Incident batch {incident.batch_id} was not found")
        case = session.get(IncidentCase, incident.case_id) if incident.case_id else None
        fixture = _mapping(case.replay_definition.get("fixture")) if case is not None else {}
        invariant_models = tuple(
            session.scalars(
                select(InvariantResult)
                .where(
                    InvariantResult.batch_id == batch.batch_id,
                    InvariantResult.attempt_number == batch.attempt_count,
                    InvariantResult.origin == incident.origin,
                )
                .order_by(InvariantResult.name, InvariantResult.invariant_id)
            )
        )
        evidence_models = tuple(
            session.scalars(
                select(IncidentEvidence)
                .where(IncidentEvidence.incident_id == incident.incident_id)
                .order_by(IncidentEvidence.created_at, IncidentEvidence.evidence_id)
            )
        )
        timeline = (
            timeline_from_fixture(fixture)
            if fixture
            else build_timeline(_canonical_database_events(session, batch.batch_id))
        )
        invariants = tuple(_invariant_view(result) for result in invariant_models)
        failed = tuple(value.name for value in invariants if value.status == "FAIL")
        insufficient = tuple(
            value.name for value in invariants if value.status == "INSUFFICIENT_EVIDENCE"
        )
        content = ReportContent(
            report_kind="INCIDENT",
            subject_id=incident.incident_id,
            subject_name=f"{incident.incident_type.replace('_', ' ').title()} Incident",
            origin=incident.origin.value,
            status=incident.status.value,
            protocol=incident.protocol_name or (case.protocol if case else "generic"),
            chain=case.chain if case else None,
            generated_at=generated_at or datetime.now(UTC),
            executive_summary=_executive_summary(
                status=incident.status.value,
                failed_invariants=failed or (incident.incident_type,),
                insufficient_invariants=insufficient,
                origin=incident.origin.value,
            ),
            case_id=incident.case_id,
            incident_id=incident.incident_id,
            timeline=timeline,
            state_deltas=build_state_deltas(fixture),
            invariants=invariants,
            evidence=tuple(_evidence_view(value) for value in evidence_models),
            references=tuple(case.external_references or ()) if case else (),
        )

    target = output or Path("reports") / f"incident-{incident.incident_id}.html"
    return _write_report(content, target)
