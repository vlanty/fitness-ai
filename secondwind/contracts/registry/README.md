# Registries

What every metric, method, unit, sport, source and calculation module means. Data rows only
carry codes from these files; the meaning lives here. Change them by pull request; tests check
that every code is defined and every reference resolves. Loaded into BigQuery on deploy.

Rules:
- A code never changes meaning. New meaning = new code; old code gets `status: deprecated`.
- Metric codes are `<domain>.<name>`; vendor-specific scores are `vendor_score.<vendor>.<name>`;
  fields a source plugin cannot map yet are written as `x.<source>.<path>` (not listed here).
- Units are UCUM codes; each metric has one canonical unit, plugins convert on ingest.
