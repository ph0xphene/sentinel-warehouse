import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine
from sentinel.models import (
    AttackCategory,
    AttackPattern,
    AttackSubcategory,
    IncidentCase,
)
from sentinel.security.features import (
    CATEGORICAL_FEATURES,
    EXTRACTION_VERSION,
    FEATURE_NAMES,
    NUMERIC_FEATURES,
    FeatureExtractionSummary,
    extract_case_features,
)

DATASET_VERSION = "1.0.0"
SCHEMA_VERSION = "2"


class DatasetQualityError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatasetValidationReport:
    cases_checked: int
    issues: tuple[str, ...]
    extractions: dict[uuid.UUID, FeatureExtractionSummary]

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class DatasetExportSummary:
    output: Path
    rows: int
    columns: int
    dataset_version: str
    extraction_version: str
    schema_version: str
    generated_at: str


DATASET_SCHEMA = pa.schema(
    [
        pa.field("case_id", pa.string(), nullable=False),
        pa.field("case_name", pa.string(), nullable=False),
        pa.field("protocol", pa.string(), nullable=False),
        pa.field("chain", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("severity", pa.string(), nullable=False),
        pa.field("confidence_level", pa.string(), nullable=False),
        pa.field("affected_contracts", pa.string(), nullable=False),
        pa.field("attacker_addresses", pa.string(), nullable=False),
        pa.field("reference_transactions", pa.string(), nullable=False),
        pa.field("external_references", pa.string(), nullable=False),
        pa.field("attack_pattern_id", pa.string(), nullable=False),
        pa.field("attack_pattern_name", pa.string(), nullable=False),
        pa.field("attack_pattern_category", pa.string(), nullable=False),
        pa.field("attack_pattern_description", pa.string(), nullable=False),
        pa.field("attack_category_id", pa.string(), nullable=False),
        pa.field("attack_category_name", pa.string(), nullable=False),
        pa.field("attack_category_description", pa.string(), nullable=False),
        pa.field("attack_subcategory_id", pa.string(), nullable=False),
        pa.field("attack_subcategory_name", pa.string(), nullable=False),
        pa.field("attack_subcategory_description", pa.string(), nullable=False),
        pa.field("expected_status", pa.string(), nullable=False),
        pa.field("replay_matched", pa.bool_(), nullable=False),
        *[pa.field(name, pa.float64(), nullable=False) for name in NUMERIC_FEATURES],
        *[pa.field(name, pa.string(), nullable=False) for name in CATEGORICAL_FEATURES],
    ]
)


def _missing_provenance(case: IncidentCase) -> tuple[str, ...]:
    missing: list[str] = []
    scalar_fields = {
        "protocol": case.protocol,
        "chain": case.chain,
        "confidence_level": case.confidence_level,
        "description": case.description,
    }
    for field, value in scalar_fields.items():
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
    collection_fields = {
        "affected_contracts": case.affected_contracts,
        "reference_transactions": case.reference_transactions,
        "external_references": case.external_references,
    }
    for field, values in collection_fields.items():
        if not isinstance(values, list) or not values:
            missing.append(field)
    replay_fixture = case.replay_definition.get("fixture")
    if not isinstance(replay_fixture, dict) or not replay_fixture.get("source_name"):
        missing.append("replay.fixture.source_name")
    return tuple(missing)


def validate_incident_dataset(
    engine: Engine | None = None,
) -> DatasetValidationReport:
    """Run label, provenance, replay, and two-pass determinism checks."""
    engine = engine or create_database_engine()
    with Session(engine) as session:
        rows = session.execute(
            select(
                IncidentCase,
                AttackPattern,
                AttackSubcategory,
                AttackCategory,
            )
            .outerjoin(
                AttackPattern,
                IncidentCase.attack_pattern_id == AttackPattern.pattern_id,
            )
            .outerjoin(
                AttackSubcategory,
                AttackPattern.subcategory_id == AttackSubcategory.subcategory_id,
            )
            .outerjoin(
                AttackCategory,
                AttackSubcategory.category_id == AttackCategory.category_id,
            )
            .order_by(IncidentCase.case_id)
        ).all()

    issues: list[str] = []
    extractions: dict[uuid.UUID, FeatureExtractionSummary] = {}
    for case, pattern, subcategory, category in rows:
        prefix = f"case {case.case_id}"
        if pattern is None:
            issues.append(f"{prefix}: missing attack-pattern label")
        if subcategory is None or category is None:
            issues.append(f"{prefix}: missing taxonomy category or subcategory")
        missing = _missing_provenance(case)
        if missing:
            issues.append(f"{prefix}: missing provenance {', '.join(missing)}")
        if pattern is None or subcategory is None or category is None:
            continue
        try:
            first = extract_case_features(case.case_id, engine)
            second = extract_case_features(case.case_id, engine)
        except (RuntimeError, ValueError) as error:
            issues.append(f"{prefix}: feature extraction failed: {error}")
            continue
        if first.features != second.features:
            issues.append(f"{prefix}: feature extraction is non-deterministic")
        names = {feature.name for feature in second.features}
        if names != set(FEATURE_NAMES):
            issues.append(f"{prefix}: incomplete feature set")
        if not second.replay_matched:
            issues.append(f"{prefix}: replay does not match expected outcome")
        extractions[case.case_id] = second

    return DatasetValidationReport(
        cases_checked=len(rows),
        issues=tuple(issues),
        extractions=extractions,
    )


def _encoded_list(values: list[str] | None) -> str:
    return json.dumps(sorted(values or []), separators=(",", ":"))


def export_incident_dataset(
    output: Path,
    engine: Engine | None = None,
) -> DatasetExportSummary:
    """Validate and export one versioned, wide Parquet row per incident case."""
    engine = engine or create_database_engine()
    validation = validate_incident_dataset(engine)
    if not validation.valid:
        raise DatasetQualityError("Dataset validation failed: " + "; ".join(validation.issues))

    with Session(engine) as session:
        rows = session.execute(
            select(
                IncidentCase,
                AttackPattern,
                AttackSubcategory,
                AttackCategory,
            )
            .join(
                AttackPattern,
                IncidentCase.attack_pattern_id == AttackPattern.pattern_id,
            )
            .join(
                AttackSubcategory,
                AttackPattern.subcategory_id == AttackSubcategory.subcategory_id,
            )
            .join(
                AttackCategory,
                AttackSubcategory.category_id == AttackCategory.category_id,
            )
            .order_by(IncidentCase.case_id)
        ).all()
        dataset_rows: list[dict[str, object]] = []
        for case, pattern, subcategory, category in rows:
            feature_values = {
                feature.name: feature.value
                for feature in validation.extractions[case.case_id].features
            }
            replay_expected = case.replay_definition.get("expected", {})
            expected_status = (
                str(replay_expected.get("status"))
                if isinstance(replay_expected, dict)
                else "unknown"
            )
            dataset_rows.append(
                {
                    "case_id": str(case.case_id),
                    "case_name": case.name,
                    "protocol": case.protocol,
                    "chain": str(case.chain),
                    "category": case.category,
                    "severity": case.severity,
                    "confidence_level": str(case.confidence_level),
                    "affected_contracts": _encoded_list(case.affected_contracts),
                    "attacker_addresses": _encoded_list(case.attacker_addresses),
                    "reference_transactions": _encoded_list(case.reference_transactions),
                    "external_references": _encoded_list(case.external_references),
                    "attack_pattern_id": str(pattern.pattern_id),
                    "attack_pattern_name": pattern.name,
                    "attack_pattern_category": pattern.category,
                    "attack_pattern_description": pattern.description,
                    "attack_category_id": str(category.category_id),
                    "attack_category_name": category.name,
                    "attack_category_description": category.description,
                    "attack_subcategory_id": str(subcategory.subcategory_id),
                    "attack_subcategory_name": subcategory.name,
                    "attack_subcategory_description": subcategory.description,
                    "expected_status": expected_status,
                    "replay_matched": validation.extractions[case.case_id].replay_matched,
                    **{name: float(feature_values[name]) for name in NUMERIC_FEATURES},
                    **{name: str(feature_values[name]) for name in CATEGORICAL_FEATURES},
                }
            )

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    schema = DATASET_SCHEMA.with_metadata(
        {
            b"dataset": b"sentinel_security_incidents",
            b"dataset_version": DATASET_VERSION.encode(),
            b"extraction_version": EXTRACTION_VERSION.encode(),
            b"schema_version": SCHEMA_VERSION.encode(),
            b"generated_at": generated_at.encode(),
        }
    )
    table = pa.Table.from_pylist(dataset_rows, schema=schema)
    resolved = output.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        resolved,
        compression="zstd",
        use_dictionary=True,
        write_statistics=True,
    )
    return DatasetExportSummary(
        output=resolved,
        rows=table.num_rows,
        columns=table.num_columns,
        dataset_version=DATASET_VERSION,
        extraction_version=EXTRACTION_VERSION,
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
    )
