from pathlib import Path

from sqlalchemy import Engine

from sentinel.security.dataset import (
    DatasetQualityError,
    export_incident_dataset,
    validate_incident_dataset,
)


def export_dataset(output: Path, engine: Engine | None = None) -> int:
    try:
        summary = export_incident_dataset(output, engine)
    except DatasetQualityError as error:
        print(f"Dataset export failed: {error}")
        return 1
    print(f"Dataset:  {summary.output}")
    print(f"Rows:     {summary.rows}")
    print(f"Columns:  {summary.columns}")
    print(f"Dataset version:    {summary.dataset_version}")
    print(f"Extraction version: {summary.extraction_version}")
    print(f"Schema version:     {summary.schema_version}")
    print(f"Generated at:       {summary.generated_at}")
    return 0


def validate_dataset(engine: Engine | None = None) -> int:
    report = validate_incident_dataset(engine)
    print(f"Cases checked: {report.cases_checked}")
    print(f"Valid:         {report.valid}")
    for issue in report.issues:
        print(f"Issue:         {issue}")
    return 0 if report.valid else 1
