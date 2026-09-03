from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app


def test_local_login_session_protects_situation_snapshot(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        demo_password="local-test-password",
    )

    with TestClient(app) as client:
        unauthenticated = client.get("/api/situation/snapshot")
        login = client.post(
            "/api/auth/login",
            json={
                "username": "situation-viewer",
                "password": "local-test-password",
            },
        )
        authenticated = client.get("/api/situation/snapshot")
        logout = client.post("/api/auth/logout")
        after_logout = client.get("/api/situation/snapshot")

    assert unauthenticated.status_code == 401
    assert login.status_code == 200
    assert login.json() == {
        "username": "situation-viewer",
        "role": "situation_viewer",
        "role_label": "态势查看者",
        "capabilities": ["situation:read", "query:read"],
    }
    set_cookie = login.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert authenticated.status_code == 200
    assert logout.status_code == 204
    assert after_logout.status_code == 401


def test_roles_and_identity_activity_are_enforced_and_audited(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'roles.db'}",
        demo_password="local-test-password",
    )
    expected_roles = {
        "situation-viewer": (
            "situation_viewer",
            ["situation:read", "query:read"],
        ),
        "clue-reviewer": (
            "clue_reviewer",
            ["situation:read", "clue:review"],
        ),
        "spatial-admin": (
            "spatial_admin",
            ["situation:read", "spatial:manage"],
        ),
    }

    with TestClient(app) as client:
        for username, (role, capabilities) in expected_roles.items():
            login = client.post(
                "/api/auth/login",
                json={"username": username, "password": "local-test-password"},
            )
            assert login.status_code == 200
            session = client.get("/api/auth/session")
            assert session.json()["role"] == role
            assert session.json()["capabilities"] == capabilities
            assert client.get("/api/audit/events").status_code == 200
            another_actor = next(actor for actor in expected_roles if actor != username)
            assert (
                client.get(
                    "/api/audit/events", params={"actor": another_actor}
                ).status_code
                == 403
            )
            client.post("/api/auth/logout")

        failed_login = client.post(
            "/api/auth/login",
            json={"username": "situation-viewer", "password": "wrong-password"},
        )
        assert failed_login.status_code == 401
        client.post(
            "/api/auth/login",
            json={
                "username": "situation-viewer",
                "password": "local-test-password",
            },
        )
        own_audit = client.get("/api/audit/events")
        forbidden_audit = client.get(
            "/api/audit/events", params={"actor": "spatial-admin"}
        )
        audit_after_denial = client.get("/api/audit/events")
        mutation_responses = [
            client.request(
                method,
                "/api/audit/events",
                json={"action": "erase_login_failure"},
            )
            for method in ("POST", "PUT", "PATCH", "DELETE")
        ]

    assert own_audit.status_code == 200
    assert [event["action"] for event in own_audit.json()["events"]][-2:] == [
        "login_failed",
        "login_succeeded",
    ]
    assert forbidden_audit.status_code == 403
    assert audit_after_denial.json()["events"][-1]["action"] == "access_denied"
    assert all(response.status_code == 405 for response in mutation_responses)
