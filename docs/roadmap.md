# InvoiceFlow AI — Post-release Roadmap

The strategic direction is:

**InvoiceFlow AI → Intelligent Invoice Data Platform**

The order is deliberate: build trustworthy data foundations before adding
predictive behavior.

## 9A — Data Engineering Foundation

- Add PostgreSQL analytics schemas with staging, clean, and analytics layers.
- Build reproducible ETL/ELT pipelines, incremental models, backfills, and
  idempotent loads.
- Introduce dbt, data contracts, and automated data-quality checks.
- Use DuckDB/Parquet only where a measured local analytical workload justifies
  it.

## 9B — Analytics and BI

- Model invoice-processing KPIs, review rate, processing time, and exception
  outcomes.
- Add supplier and workflow analytics marts.
- Expose dashboards from governed analytical models rather than operational
  tables directly.

## 9C — AI extraction evaluation

- Correct and version ground-truth datasets.
- Track field-level extraction metrics, error categories, and model/prompt
  experiments.
- Keep evaluator comparisons separate from deterministic production validation.

## 9D — Duplicate invoice detection

- Extend exact and rule-based matching with fuzzy candidates and feature
  engineering.
- Rank likely duplicates for human verification; do not auto-label fraud.

## 9E — Anomaly detection

- Establish statistical baselines and explainable outlier signals.
- Evaluate Isolation Forest or other methods only against suitable historical
  data.
- Route anomalies as review alerts, not confirmed fraud findings.

## 9F — MLOps and data observability

- Add experiment/model and dataset version tracking.
- Measure drift, lineage, extraction quality, and pipeline freshness.
- Consider MLflow or comparable tooling when the experimental surface warrants
  it.

## 10 — Predictive intelligence

- Start with review-resolution-time prediction when enough labeled history
  exists.
- Defer payment-delay prediction until historical payment outcome data is
  available and governed.

Kafka, Spark, Kubernetes, and other infrastructure are not roadmap commitments.
Introduce them only when future scale, reliability, or data-volume requirements
justify them.
