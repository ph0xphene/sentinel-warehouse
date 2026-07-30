import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.models import Incident, IncidentEvidence, IncidentStatus
from sentinel.security.invariants import InvariantOutcome


def _affected_entity(record: dict[str, object], batch_id: uuid.UUID) -> str:
    for field in ("external_id", "account_external_id", "asset_external_id"):
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return f"batch:{batch_id}"


def record_invariant_incidents(
    session: Session,
    batch_id: uuid.UUID,
    attempt_number: int,
    outcomes: tuple[InvariantOutcome, ...],
) -> None:
    """Create or reopen incidents and append evidence for failed invariants."""
    now = datetime.now(UTC)
    for outcome in outcomes:
        if outcome.passed:
            continue
        incident = session.scalar(
            select(Incident).where(
                Incident.batch_id == batch_id,
                Incident.incident_type == outcome.name,
            )
        )
        if incident is None:
            incident = Incident(
                incident_id=uuid.uuid4(),
                incident_type=outcome.name,
                protocol_name=outcome.protocol_name,
                severity=outcome.severity,
                status=IncidentStatus.OPEN,
                detected_at=now,
                batch_id=batch_id,
                summary=(
                    f"{outcome.description} Affected records: {len(outcome.affected_records)}."
                ),
            )
            session.add(incident)
            session.flush()
        elif incident.status is IncidentStatus.RESOLVED:
            incident.status = IncidentStatus.OPEN
        if incident.protocol_name is None:
            incident.protocol_name = outcome.protocol_name

        session.add_all(
            IncidentEvidence(
                evidence_id=uuid.uuid4(),
                incident_id=incident.incident_id,
                affected_entity=_affected_entity(record, batch_id),
                evidence_type="invariant_violation",
                payload={
                    **record,
                    "invariant": outcome.name,
                    "severity": outcome.severity,
                    "attempt_number": attempt_number,
                },
            )
            for record in outcome.affected_records
        )


def resolve_batch_incidents(session: Session, batch_id: uuid.UUID) -> int:
    """Resolve active incidents after the same logical batch succeeds."""
    incidents = session.scalars(
        select(Incident).where(
            Incident.batch_id == batch_id,
            Incident.status.in_((IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)),
        )
    )
    resolved = 0
    for incident in incidents:
        incident.status = IncidentStatus.RESOLVED
        resolved += 1
    return resolved
