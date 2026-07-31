-- Read-only examples for the application PostgreSQL analytics schema.

SELECT
    activity_date,
    source_system,
    asset_external_id,
    event_type,
    event_count,
    gross_amount
FROM analytics.daily_asset_activity
ORDER BY activity_date, source_system, asset_external_id, event_type;

SELECT
    source_system,
    event_type,
    count(*) AS event_count,
    sum(abs(amount)) AS gross_amount
FROM analytics.canonical_event_flows
GROUP BY source_system, event_type
ORDER BY event_count DESC, source_system, event_type;
