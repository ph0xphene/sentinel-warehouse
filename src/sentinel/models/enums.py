import enum


class AnalysisStatus(enum.StrEnum):
    """How completely an ingestion batch was understood by security analysis."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class IncidentOrigin(enum.StrEnum):
    """Namespace separating operational, replay, and deterministic fixture findings."""

    LIVE = "LIVE"
    REPLAY = "REPLAY"
    FIXTURE = "FIXTURE"
