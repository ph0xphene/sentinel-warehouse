-- Replace the dataset directory with the experiment being analyzed.
-- Run from the repository root:
-- duckdb < sql/duckdb/research_activity.sql

SELECT
    event_date,
    asset,
    count(*) AS event_count,
    sum(amount) AS transferred_volume,
    count(DISTINCT account_from) AS sending_accounts,
    count(DISTINCT account_to) AS receiving_accounts
FROM read_parquet(
    'data/research/generated/synthetic-seed-7-rows-10000/events/*.parquet'
)
GROUP BY event_date, asset
ORDER BY event_date, asset;
