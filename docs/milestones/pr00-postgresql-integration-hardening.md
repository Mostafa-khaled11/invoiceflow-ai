# PR-00 — PostgreSQL Integration Test Hardening

## Scope

PR-00 adds CI verification for PostgreSQL-backed persistence and Alembic
migrations. It does not change application behavior, business rules, release
history, or the v0.1.0 release contents.

## CI strategy

The dedicated `postgres-integration` job starts PostgreSQL 17 and creates two
disposable databases:

- `invoiceflow_migrations` receives `alembic upgrade head`.
- `invoiceflow_tests` runs the full test suite with `TEST_DATABASE_URL`.

The migration database is inspected by
`backend/tests/test_migration_smoke.py` before the test suite starts. The smoke
test checks migration-created tables, invoice uniqueness constraints, workflow
event constraints, and the Alembic head revision without calling
`Base.metadata.create_all()`.

The full-suite JUnit report is checked for skipped tests. Any skip fails the
job. The explicitly opt-in Groq smoke test is excluded because PR-00 never
makes provider calls; all PostgreSQL-backed tests must execute against the
configured test database.

## Skip policy

The ordinary local suite intentionally skips 13 PostgreSQL-marked tests plus
one PostgreSQL-only workflow-persistence test when `TEST_DATABASE_URL` is
absent. It also skips the opt-in Groq smoke test unless explicit live-test
settings are provided. Those local skips are not evidence of PostgreSQL
verification.

In CI, `TEST_DATABASE_URL` is always configured, the Groq smoke test is
explicitly excluded, and the JUnit report must contain zero skipped tests.

## Local verification status

Static workflow validation, Ruff, Pyright, and the ordinary test suite pass
locally. After adding the migration smoke test, the local suite reports 269
passed and 15 skipped; the additional skip is the new PostgreSQL smoke test
without `TEST_DATABASE_URL`. A disposable PostgreSQL run could not be
completed because Docker Desktop was unavailable; hosted CI is therefore
required to verify the new database job.
