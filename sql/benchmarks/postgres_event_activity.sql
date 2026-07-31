EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS)
SELECT
    occurred_at::date AS activity_date,
    source_system,
    asset_id,
    event_type,
    count(*) AS event_count,
    sum(abs(amount)) AS gross_amount
FROM core.financial_events
WHERE canonical
GROUP BY occurred_at::date, source_system, asset_id, event_type
ORDER BY activity_date, source_system, asset_id, event_type;
