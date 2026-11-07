"""Smoke-test the schema created by Alembic without recreating it from models."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.integration

_EXPECTED_TABLES = {
    "alembic_version",
    "invoices",
    "invoice_text_extractions",
    "invoice_structured_extractions",
    "invoice_validations",
    "invoice_validation_findings",
    "invoice_workflows",
    "workflow_events",
    "review_cases",
    "user_accounts",
    "oidc_identities",
    "roles",
    "user_role_assignments",
    "user_sessions",
}


def test_alembic_head_creates_expected_postgresql_schema() -> None:
    """Verify migration-created objects on the database upgraded by CI."""

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        database_inspector = inspect(engine)
        assert _EXPECTED_TABLES.issubset(set(database_inspector.get_table_names()))

        invoice_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints("invoices")
        }
        assert {("sha256",), ("storage_key",)} <= invoice_unique_constraints

        workflow_event_checks = {
            check["name"]
            for check in database_inspector.get_check_constraints("workflow_events")
        }
        assert "ck_workflow_events_event_type" in workflow_event_checks

        identity_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints(
                "oidc_identities"
            )
        }
        assert {("issuer", "subject")} <= identity_unique_constraints

        session_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints("user_sessions")
        }
        assert {("token_hash",)} <= session_unique_constraints

        role_assignment_indexes = {
            index["name"]
            for index in database_inspector.get_indexes("user_role_assignments")
        }
        assert "uq_user_role_assignments_active" in role_assignment_indexes

        session_foreign_keys = database_inspector.get_foreign_keys("user_sessions")
        assert any(
            foreign_key["referred_table"] == "user_accounts"
            for foreign_key in session_foreign_keys
        )

        with engine.connect() as connection:
            role_names = {
                row[0]
                for row in connection.execute(
                    text("select name from roles order by name")
                )
            }
            assert role_names == {"Admin", "Approver", "Operator", "Reviewer"}

            revision = connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
        assert revision == "e1b2c3d4e5f6"
    finally:
        engine.dispose()
