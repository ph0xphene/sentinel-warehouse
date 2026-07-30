import json
import uuid
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine
from sentinel.models import Incident, IncidentEvidence, IncidentOrigin
from sentinel.reporting import generate_incident_report


def list_incidents(
    engine: Engine | None = None,
    *,
    origin: IncidentOrigin | None = None,
) -> int:
    engine = engine or create_database_engine()
    with Session(engine) as session:
        statement = select(Incident)
        if origin is not None:
            statement = statement.where(Incident.origin == origin)
        incidents = tuple(
            session.scalars(statement.order_by(Incident.detected_at.desc(), Incident.incident_id))
        )

    if not incidents:
        print("No incidents found.")
        return 0
    print(
        "INCIDENT_ID                           ORIGIN   STATUS         SEVERITY  PROTOCOL      TYPE"
    )
    for incident in incidents:
        print(
            f"{incident.incident_id}  {incident.origin.value:<7}  "
            f"{incident.status.value:<13}  "
            f"{incident.severity:<8}  {(incident.protocol_name or '-'):<12}  "
            f"{incident.incident_type}"
        )
    return 0


def show_incident(incident_id: uuid.UUID, engine: Engine | None = None) -> int:
    engine = engine or create_database_engine()
    with Session(engine) as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            print(f"Incident {incident_id} was not found.")
            return 1
        evidence = tuple(
            session.scalars(
                select(IncidentEvidence)
                .where(IncidentEvidence.incident_id == incident_id)
                .order_by(IncidentEvidence.created_at, IncidentEvidence.evidence_id)
            )
        )
        values = {
            "incident_id": str(incident.incident_id),
            "incident_type": incident.incident_type,
            "protocol_name": incident.protocol_name,
            "origin": incident.origin.value,
            "case_id": str(incident.case_id) if incident.case_id is not None else None,
            "severity": incident.severity,
            "status": incident.status.value,
            "detected_at": incident.detected_at.isoformat(),
            "batch_id": str(incident.batch_id),
            "summary": incident.summary,
            "evidence": [
                {
                    "affected_entity": item.affected_entity,
                    "evidence_type": item.evidence_type,
                    "payload": item.payload,
                }
                for item in evidence
            ],
        }
    print(json.dumps(values, indent=2, sort_keys=True))
    return 0


def report_incident(
    incident_id: uuid.UUID,
    output: Path | None = None,
    engine: Engine | None = None,
) -> int:
    try:
        summary = generate_incident_report(incident_id, output, engine)
    except ValueError as error:
        print(str(error))
        return 1
    print(f"Generated: {summary.output}")
    print(f"Incident:  {summary.subject_id}")
    print(f"Origin:    {summary.origin.value}")
    print(f"Status:    {summary.status}")
    print(f"Events:    {summary.events}")
    print(f"Evidence:  {summary.evidence_records}")
    return 0
