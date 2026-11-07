"""Focused PR-01C authorization boundary tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.main import create_app
from app.models.identity import (
    ApplicationRole,
    Role,
    UserAccountStatus,
    UserRoleAssignment,
    UserSession,
)
from app.models.invoice_workflow import ReviewResolution, WorkflowEvent
from app.services.authorization import (
    ROLE_PERMISSIONS,
    AuthorizationContext,
    Permission,
)
from app.services.identity_persistence import (
    assign_role,
    create_session,
    create_user_account,
    hash_session_token,
)
from app.services.invoice_workflow import ReviewResolutionError, resolve_invoice_review
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session


@contextmanager
def _role_client(
    settings: Settings,
    engine: Engine,
    role_name: ApplicationRole,
) -> Iterator[tuple[TestClient, object]]:
    raw_token = f"test-{uuid4()}"
    now = datetime.now(UTC)
    with Session(engine) as session:
        account = create_user_account(
            session,
            email=f"{role_name.value.lower()}@example.test",
            status=UserAccountStatus.ACTIVE,
        )
        role = session.execute(
            select(Role).where(Role.name == role_name.value)
        ).scalar_one_or_none()
        if role is None:
            role = Role(name=role_name.value)
            session.add(role)
            session.flush()
        assign_role(session, user_id=account.id, role=role_name)
        create_session(
            session,
            user_id=account.id,
            token_hash=hash_session_token(raw_token),
            created_at=now,
            last_activity_at=now,
            idle_expires_at=now + timedelta(hours=1),
            absolute_expires_at=now + timedelta(hours=8),
        )
        session.commit()
        account_id = account.id

    with TestClient(create_app(settings)) as client:
        client.cookies.set(settings.auth_session_cookie_name, raw_token)
        yield client, account_id


def test_anonymous_sensitive_routes_require_authentication(
    anonymous_client: TestClient,
) -> None:
    invoice_id = uuid4()

    assert anonymous_client.get(f"/api/v1/invoices/{invoice_id}").status_code == 401
    assert (
        anonymous_client.post(f"/api/v1/invoices/{invoice_id}/validate").status_code
        == 401
    )
    assert anonymous_client.get("/api/v1/review-cases").status_code == 401


def test_role_matrix_has_no_implicit_approval_authority() -> None:
    assert Permission.APPROVAL_DECIDE not in ROLE_PERMISSIONS[ApplicationRole.ADMIN]
    assert Permission.APPROVAL_DECIDE not in ROLE_PERMISSIONS[ApplicationRole.REVIEWER]
    assert ROLE_PERMISSIONS[ApplicationRole.APPROVER] == frozenset()


def test_every_sensitive_registered_route_has_a_permission_dependency() -> None:
    app = create_app(Settings(database_url="sqlite+pysqlite:///:memory:"))
    expected_permissions = {
        "/api/v1/invoices/upload",
        "/api/v1/invoices/{invoice_id}",
        "/api/v1/invoices/{invoice_id}/extract-text",
        "/api/v1/invoices/{invoice_id}/extract-structured",
        "/api/v1/invoices/{invoice_id}/validate",
        "/api/v1/invoices/{invoice_id}/validation",
        "/api/v1/invoices/{invoice_id}/workflow/route",
        "/api/v1/invoices/{invoice_id}/workflow",
        "/api/v1/invoices/{invoice_id}/workflow/review",
        "/api/v1/review-cases",
        "/api/v1/review-cases/{review_case_id}",
    }

    def walk(router: object, prefix: str = ""):
        if hasattr(router, "original_router"):
            context = getattr(router, "include_context", None)
            prefix += getattr(context, "prefix", "") if context else ""
            router = router.original_router
        for route in getattr(router, "routes", []):
            if hasattr(route, "original_router") or hasattr(route, "routes"):
                yield from walk(route, prefix + getattr(route, "prefix", ""))
            elif hasattr(route, "dependant"):
                yield prefix + route.path, route

    routes = dict(walk(app))
    assert expected_permissions <= routes.keys()
    assert set(routes) == {
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/callback",
        "/api/v1/auth/session",
        "/api/v1/auth/logout",
        *expected_permissions,
    }
    for path in expected_permissions:
        route = routes[path]
        dependencies = {
            getattr(dependency.call, "required_permission", None)
            for dependency in route.dependant.dependencies
        }
        assert any(permission is not None for permission in dependencies), path


def test_review_service_rejects_an_unprivileged_principal_before_mutation(
    engine: Engine,
) -> None:
    principal = AuthorizationContext(
        principal_type="human",
        account_id=uuid4(),
        account=None,
        roles=frozenset({ApplicationRole.OPERATOR.value}),
        permissions=frozenset(),
    )
    with Session(engine) as session:
        with pytest.raises(ReviewResolutionError) as error:
            resolve_invoice_review(
                session,
                uuid4(),
                ReviewResolution.REJECT,
                str(uuid4()),
                principal=principal,
            )
    assert error.value.error_code == "authorization_required"


def test_operator_cannot_route_or_resolve_workflow(
    app_settings: Settings,
    engine: Engine,
) -> None:
    with _role_client(app_settings, engine, ApplicationRole.OPERATOR) as (
        client,
        _account_id,
    ):
        invoice_id = uuid4()
        assert (
            client.post(f"/api/v1/invoices/{invoice_id}/workflow/route").status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/invoices/{invoice_id}/workflow/review",
                json={
                    "decision": "REJECT",
                    "resolution_request_id": str(uuid4()),
                },
            ).status_code
            == 403
        )


def test_revoked_role_and_disabled_account_are_rechecked(
    authenticated_client: TestClient,
    engine: Engine,
) -> None:
    with Session(engine) as session:
        account = session.execute(
            select(Role, UserRoleAssignment).join(
                UserRoleAssignment, UserRoleAssignment.role_id == Role.id
            )
        ).first()
        assert account is not None
        _role, assignment = account
        assignment_id = assignment.id
        user = assignment.user
        user.status = UserAccountStatus.DISABLED.value
        session.commit()

    assert authenticated_client.get("/api/v1/review-cases").status_code == 401

    with Session(engine) as session:
        refreshed_assignment = session.get(UserRoleAssignment, assignment_id)
        assert refreshed_assignment is not None
        refreshed_assignment.user.status = UserAccountStatus.ACTIVE.value
        refreshed_assignment.revoked_at = datetime.now(UTC)
        session.commit()

    assert authenticated_client.get("/api/v1/review-cases").status_code == 403


def test_expired_session_cannot_access_protected_routes(
    authenticated_client: TestClient,
    engine: Engine,
) -> None:
    with Session(engine) as session:
        session_record = session.execute(select(UserSession)).scalar_one()
        now = datetime.now(UTC)
        session_record.created_at = now - timedelta(hours=2)
        session_record.idle_expires_at = now - timedelta(minutes=1)
        session.commit()

    assert authenticated_client.get("/api/v1/review-cases").status_code == 401


def test_cross_user_review_replay_conflicts_without_new_event(
    authenticated_client: TestClient,
    app_settings: Settings,
    engine: Engine,
) -> None:
    from tests.test_invoice_workflow_api import _seed_review_workflow

    invoice_id = _seed_review_workflow(authenticated_client)
    request_id = str(uuid4())
    url = f"/api/v1/invoices/{invoice_id}/workflow/review"
    first = authenticated_client.post(
        url,
        json={
            "decision": "REJECT",
            "resolution_request_id": request_id,
        },
    )
    with _role_client(app_settings, engine, ApplicationRole.REVIEWER) as (
        other_client,
        _other_account_id,
    ):
        replay = other_client.post(
            url,
            json={
                "decision": "REJECT",
                "resolution_request_id": request_id,
            },
        )

    assert first.status_code == 200
    assert replay.status_code == 409
    with Session(engine) as session:
        events = (
            session.execute(
                select(WorkflowEvent).order_by(WorkflowEvent.sequence_number)
            )
            .scalars()
            .all()
        )
        assert len(events) == 2


def test_n8n_service_token_is_scoped_to_read_only_review_queue(
    app_settings: Settings,
) -> None:
    raw_token = "n8n-test-service-token"
    app_settings.n8n_service_token_hash = sha256(raw_token.encode()).hexdigest()
    with TestClient(create_app(app_settings)) as client:
        headers = {"Authorization": f"Bearer {raw_token}"}
        assert client.get("/api/v1/review-cases", headers=headers).status_code == 200
        assert (
            client.get(f"/api/v1/review-cases/{uuid4()}", headers=headers).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v1/review-cases", headers={"Authorization": "Bearer bad"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                f"/api/v1/invoices/{uuid4()}/workflow/review",
                headers=headers,
                json={
                    "decision": "REJECT",
                    "resolution_request_id": str(uuid4()),
                },
            ).status_code
            == 403
        )
    replacement_token = "rotated-n8n-test-service-token"
    app_settings.n8n_service_token_hash = sha256(replacement_token.encode()).hexdigest()
    with TestClient(create_app(app_settings)) as rotated_client:
        assert (
            rotated_client.get(
                "/api/v1/review-cases",
                headers={"Authorization": f"Bearer {raw_token}"},
            ).status_code
            == 401
        )
        assert (
            rotated_client.get(
                "/api/v1/review-cases",
                headers={"Authorization": f"Bearer {replacement_token}"},
            ).status_code
            == 200
        )
