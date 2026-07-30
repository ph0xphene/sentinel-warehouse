import json
import uuid

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine
from sentinel.models import Incident, IncidentEvidence


def list_incidents(engine: Engine | None = None) -> int:
    engine = engine or create_database_engine()
    with Session(engine) as session:
        incidents = tuple(
            session.scalars(
                select(Incident).order_by(Incident.detected_at.desc(), Incident.incident_id)
            )
        )

    if not incidents:
        print("No incidents found.")
        return 0
    print("INCIDENT_ID                           STATUS         SEVERITY  PROTOCOL      TYPE")
    for incident in incidents:
        print(
            f"{incident.incident_id}  {incident.status.value:<13}  "
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
